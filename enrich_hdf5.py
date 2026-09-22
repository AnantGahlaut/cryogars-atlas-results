#!/usr/bin/env python3
"""
enrich_hdf5.py -- add the radar geometry and terrain layers the retrieval needs.

A built archive carries phase, coherence and amplitude but not the quantities
that turn phase into snow depth. The Guneriussen/Leinss inversion is

    dz  =  -(dphi * lambda) / (4*pi * (cos(theta_loc) - sqrt(eps - sin^2 theta_loc)))

and of the three terms on the right, the archive stored only dphi. This adds
the rest: the wavelength and viewing geometry harvested from each flight's
annotation, and the local incidence angle computed against the 3 m lidar DEM.

    python enrich_hdf5.py --site mores_creek
    python enrich_hdf5.py --all --out-dir C:/SnowEx/out

The source file is never modified. Every run writes a new `<site>.enriched.h5`
and leaves the original untouched, because HDF5 cannot reclaim space from an
in-place edit and a half-finished enrichment of a file that took days to
download is not a recoverable state.

Rewriting rather than appending also reclaims the duplicated `hgt` arrays: the
InSAR height is byte-identical across all four polarisations, so it is written
once per flight instead of four times.

In-situ observations (snow pits, GPR) are not carried into the enriched copy
in v1. They stay in the raw archives and `--with-insitu` brings them back.

Annotations are fetched with HTTP range requests -- the `.ann` is ~30 KB inside
a ~5 GB zip, so the whole harvest costs a few hundred KB per flight instead of
re-downloading the archive.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import logging
import math
import os
import re
import sys
import warnings
import zipfile
from pathlib import Path
from processing_metadata import processing_snapshot, record_processing_history, statistics_attrs
from build_provenance import capture_sources, file_identity, new_record, read_lineage, store_lineage
from build_hdf5 import _BUILD_SOURCES as _BASE_BUILD_SOURCES

_BUILD_SOURCES = {**capture_sources(__file__), **_BASE_BUILD_SOURCES}

warnings.filterwarnings("ignore")
log = logging.getLogger("enrich")

#: Where derived terrain layers live. They are lidar-derived, so they sit
#: under the lidar branch rather than pretending to be a radar product.
DERIVED_GROUP = "science/LIDAR/DERIVED"

#: Vegetation at least this tall counts as canopy for the cover fraction.
#: Bonnell et al. (2024) report L-band SWE error roughly doubling above a
#: forest cover fraction of 0.5, which is the threshold this exists to test.
CANOPY_HEIGHT_M = 2.0

#: Nominal requested window side. Inclusive centered endpoints give
#: 11 x 11 cells (33 x 33 m) at 3 m grid spacing. Keep the nominal setting
#: distinct from the actual support recorded with the derived fraction.
CANOPY_WINDOW_M = 30.0

#: Coherence below this is conventionally treated as unusable for InSAR
#: inversion. Stored as an attribute so a user can re-threshold knowingly.
COHERENCE_MIN = 0.30

#: Nodata for the uint8 coherence mask. 255 cannot collide with the 0/1 the
#: mask carries, and uint8 has no NaN to fall back on.
MASK_NODATA = 255

#: Keys lifted out of the annotation, mapped to the attribute names used here.
#: Everything the retrieval equation or the geometry needs, and nothing else.
ANN_SCALARS = {
    "center wavelength":                  ("radar_wavelength_cm", float),
    "bandwidth":                          ("radar_bandwidth_mhz", float),
    "radar look direction":               ("radar_look_direction", str),
    "average look angle in near range":   ("look_angle_near_deg", float),
    "average look angle in far range":    ("look_angle_far_deg", float),
    "global average altitude":            ("platform_altitude_m", float),
    "global average squint angle":        ("squint_angle_deg", float),
    "slant range data at near range":     ("slant_range_near_m", float),
    "peg latitude":                       ("peg_latitude_deg", float),
    "peg longitude":                      ("peg_longitude_deg", float),
    "peg heading":                        ("peg_heading_deg", float),
    "start time of acquisition for pass 1": ("acquisition_start_pass1", str),
    "stop time of acquisition for pass 1":  ("acquisition_stop_pass1", str),
    "start time of acquisition for pass 2": ("acquisition_start_pass2", str),
    "stop time of acquisition for pass 2":  ("acquisition_stop_pass2", str),
    "number of looks in range":           ("looks_range", int),
    "number of looks in azimuth":         ("looks_azimuth", int),
    # No numeric baseline is published for these products: UAVSAR flies a
    # repeat tube and removes the residual baseline before delivery, so the
    # topographic phase is already largely compensated in `unw`. Only the
    # method is recorded, and it is kept as provenance.
    "residual baseline estimation method": ("baseline_estimation_method", str),
    "number of baseline iterations":       ("baseline_iterations", int),
}


# --------------------------------------------------------------------------
# remote annotation harvest
# --------------------------------------------------------------------------
class RangeReader(io.RawIOBase):
    """Seekable file-like over an HTTP resource that honours Range requests.

    ASF refuses HEAD on the data pool but answers ranged GETs with 206, so the
    total size is taken from the Content-Range of a one-byte probe.
    """

    def __init__(self, session, url, size):
        self.s, self.url, self.size, self.pos = session, url, size, 0

    def seek(self, off, whence=0):
        self.pos = (off if whence == 0 else
                    self.pos + off if whence == 1 else self.size + off)
        return self.pos

    def tell(self):
        return self.pos

    def seekable(self):
        return True

    def readable(self):
        return True

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b""
        hi = min(self.pos + n, self.size) - 1
        r = self.s.get(self.url, headers={"Range": f"bytes={self.pos}-{hi}"},
                       timeout=180)
        r.raise_for_status()
        self.pos = hi + 1
        return r.content


def fetch_annotation(session, url: str) -> str:
    """Read only the `.ann` member out of a remote zip."""
    probe = session.get(url, headers={"Range": "bytes=0-0"}, stream=True,
                        timeout=120)
    probe.raise_for_status()
    rng = probe.headers.get("Content-Range")
    probe.close()
    if not rng:
        raise RuntimeError(f"{url}: server did not honour Range")
    size = int(rng.split("/")[1])
    zf = zipfile.ZipFile(RangeReader(session, url, size))
    names = [n for n in zf.namelist() if n.endswith(".ann")]
    if not names:
        raise RuntimeError(f"{url}: no .ann member")
    return zf.read(names[0]).decode("utf-8", "replace")


def cached_annotation(session, url: str, cache: Path) -> dict:
    """Parsed annotation, fetched once and kept on disk."""
    import build_hdf5 as B

    cache.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", url.rsplit("/", 1)[-1])
    raw = cache / (key + ".ann")
    if not raw.exists():
        log.info("    fetching annotation (range request)")
        raw.write_text(fetch_annotation(session, url), encoding="utf-8")
    before = raw.read_bytes()
    parsed = B.read_annotation(raw)
    if raw.read_bytes() != before:
        raise RuntimeError("Annotation changed while being read")
    parsed['_snowex_annotation_source'] = {
        'source_url': url, 'cache_filename': raw.name,
        'sha256': hashlib.sha256(before).hexdigest(), 'bytes': len(before),
        'identity_method': 'sha256_of_consumed_cached_annotation'}
    return parsed


def annotation_scalars(ann: dict) -> dict:
    """Pull the retrieval-relevant scalars out of a parsed annotation."""
    out: dict = {}
    for key, (name, cast) in ANN_SCALARS.items():
        if key not in ann:
            continue
        value = ann[key]["value"]
        try:
            out[name] = cast(value) if cast is not str else str(value).strip()
        except (TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------
# terrain derivatives
# --------------------------------------------------------------------------
def slope_aspect(dem, res_m: float):
    """Horn (1981) slope and aspect from a DEM, in degrees.

    Horn's 3x3 operator is used rather than a simple central difference
    because it weights the diagonals, which makes it markedly less noisy on
    lidar-resolution terrain where a single spurious cell would otherwise
    swing the gradient.
    """
    import numpy as np

    p = np.pad(dem, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, _, f = p[1:-1, :-2], p[1:-1, 1:-1], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]

    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res_m)
    # north is row 0, so the sign flips to make +dzdy point north
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * res_m)

    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    # Downhill vector in projected east/north coordinates; clockwise from north.
    aspect = np.degrees(np.arctan2(-dzdx, -dzdy)) % 360.0
    flat = np.hypot(dzdx, dzdy) < 1e-9
    aspect[flat] = np.nan
    bad = ~np.isfinite(dem)
    slope[bad] = np.nan
    aspect[bad] = np.nan
    aspect = aspect.astype("float32")
    aspect[aspect >= 360.0] = 0.0  # rounding at the circular seam
    return slope.astype("float32"), aspect


def surface_normals(dem, res_m: float):
    """Unit surface normals from the DEM, as three arrays (east, north, up)."""
    import numpy as np

    p = np.pad(dem, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, _, f = p[1:-1, :-2], p[1:-1, 1:-1], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res_m)
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8 * res_m)
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(dzdx)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / norm, ny / norm, nz / norm


def box_fraction(mask, valid, win_px: int):
    """Fraction of valid cells inside a square window that satisfy `mask`.

    Summed-area tables, so cost is independent of window size.
    """
    import numpy as np

    def sat(a):
        s = np.cumsum(np.cumsum(a.astype("float64"), axis=0), axis=1)
        return np.pad(s, ((1, 0), (1, 0)))

    r = win_px // 2
    h, w = mask.shape
    rows = np.arange(h)
    cols = np.arange(w)
    r0 = np.clip(rows - r, 0, h)[:, None]
    r1 = np.clip(rows + r + 1, 0, h)[:, None]
    c0 = np.clip(cols - r, 0, w)[None, :]
    c1 = np.clip(cols + r + 1, 0, w)[None, :]

    def window_sum(a):
        s = sat(a)
        return s[r1, c1] - s[r0, c1] - s[r1, c0] + s[r0, c0]

    hits = window_sum(mask & valid)
    seen = window_sum(valid)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(seen > 0, hits / seen, np.nan)
    return out.astype("float32")


def dem_vertical_reference(attrs):
    """Provider-backed descriptions for the identified existing DEM products.

    This records evidence only. A horizontal EPSG code never supplies a missing
    vertical datum, and no vertical coordinate conversion is performed here.
    """
    dataset = str(attrs.get("source_dataset", ""))
    filename = str(attrs.get("source_filename", ""))
    url = str(attrs.get("source_url", ""))
    if dataset == "SNEX20_QSI_DEM_3m" and "/SNEX20_QSI_DEM_3m/1/" in url:
        return {
            "dem_vertical_reference": "NAVD88 orthometric height (GEOID12b)",
            "dem_vertical_reference_status": "confirmed_provider_guide",
            "dem_vertical_reference_source": "https://nsidc.org/sites/default/files/documents/user-guide/multi_snex20_qsi_dem_3m-v001-userguide.pdf"}
    if dataset == "SNEX_HRSI_SD_DEM_CO" and filename == "SNEX_HRSI_SD_DEM_CO_GM_DTM_1m_V01.0.tif":
        return {
            "dem_vertical_reference": "WGS84 ellipsoidal height",
            "dem_vertical_reference_status": "confirmed_provider_linked_author_methods",
            "dem_vertical_reference_source": "https://doi.org/10.1029/2023GL104871"}
    return {"dem_vertical_reference": "Unresolved in exact-product provider metadata",
            "dem_vertical_reference_status": "unresolved"}


def projected_peg_track(peg_lat: float, peg_lon: float, heading_deg: float, epsg: int):
    """Projected peg and local grid bearing of its WGS84 geodesic heading.

    Project endpoints 100 m forward/backward along the geographic heading.
    This accounts for local grid convergence, not curvature along a full pass.
    """
    from pyproj import Geod, Transformer

    if not all(math.isfinite(v) for v in (peg_lat, peg_lon, heading_deg)):
        raise ValueError("Peg coordinates and heading must be finite")
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    px, py = tf.transform(peg_lon, peg_lat)
    geod = Geod(ellps="WGS84")
    lon0, lat0, _ = geod.fwd(peg_lon, peg_lat, heading_deg + 180.0, 100.0)
    lon1, lat1, _ = geod.fwd(peg_lon, peg_lat, heading_deg, 100.0)
    x0, y0 = tf.transform(lon0, lat0)
    x1, y1 = tf.transform(lon1, lat1)
    if not all(math.isfinite(v) for v in (px, py, x0, y0, x1, y1)) or (x0 == x1 and y0 == y1):
        raise ValueError("Could not establish a finite projected peg track")
    return px, py, math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360.0


def local_incidence(dem, transform, epsg: int, peg_lat: float, peg_lon: float,
                    peg_heading_deg: float, altitude_m: float,
                    look_direction: str):
    """Local incidence angle, degrees, between the radar line of sight and the
    terrain normal.

    Approximate UAVSAR with a straight line through the peg point on the peg
    heading converted to the local projected-grid bearing. For
    each ground cell the nearest point on that track is found, the platform is
    placed above it at the reported altitude, and the angle between the
    ground-to-platform vector and the lidar surface normal is taken.

    This uses the 3 m lidar DEM. A finer normal stencil does not by itself
    validate navigation geometry or establish compatible height references.

    Only the declared Left/Right side of that approximate track is retained;
    wrong-side cells and cells within 1e-7 m of the track are missing. This is
    a numerical boundary tolerance, not a beam/swath or terrain-occlusion test.
    Full-track curvature, fixed altitude and vertical-datum limitations remain.

    Returns (local_incidence_deg, flat_incidence_deg). The second ignores
    terrain and is the angle from vertical, kept so the terrain contribution
    can be separated from the pure viewing geometry.
    """
    import numpy as np

    side = look_direction.strip().lower() if isinstance(look_direction, str) else ""
    if side not in ("left", "right"):
        raise ValueError("radar_look_direction must be explicitly Left or Right")

    h, w = dem.shape
    # cell centres in projected coordinates
    a, _, c, _, e, f = (transform[0], transform[1], transform[2],
                        transform[3], transform[4], transform[5])
    xs = c + a * (np.arange(w) + 0.5)
    ys = f + e * (np.arange(h) + 0.5)
    X = np.broadcast_to(xs[None, :], (h, w))
    Y = np.broadcast_to(ys[:, None], (h, w))

    px, py, grid_heading = projected_peg_track(peg_lat, peg_lon, peg_heading_deg, epsg)
    hdg = math.radians(grid_heading)
    dx, dy = math.sin(hdg), math.cos(hdg)

    vx, vy = X - px, Y - py
    along = vx * dx + vy * dy
    # closest point on the track, then the platform directly above it
    cx, cy = px + along * dx, py + along * dy
    ax, ay = cx, cy
    az = float(altitude_m)

    gz = np.where(np.isfinite(dem), dem, np.nan)
    lx, ly, lz = ax - X, ay - Y, az - gz
    ln = np.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / ln, ly / ln, lz / ln

    nx, ny, nz = surface_normals(np.nan_to_num(gz, nan=np.nanmean(gz)),
                                 abs(a))
    cos_loc = lx * nx + ly * ny + lz * nz
    cos_flat = lz

    with np.errstate(invalid="ignore"):
        loc = np.degrees(np.arccos(np.clip(cos_loc, -1.0, 1.0)))
        flat = np.degrees(np.arccos(np.clip(cos_flat, -1.0, 1.0)))
    bad = ~np.isfinite(gz)
    # Positive cross-track distance is right of the existing projected track.
    # Look side restricts eligibility; it does not reverse the sight vector.
    # Reuse these owned displacement arrays to avoid another full float64 grid.
    cross_track = vx
    cross_track *= dy
    vy *= dx
    cross_track -= vy
    bad |= cross_track >= -1e-7 if side == "left" else cross_track <= 1e-7
    loc[bad] = np.nan
    flat[bad] = np.nan
    return loc.astype("float32"), flat.astype("float32")


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
def write_grid(group, name: str, data, chunk: int, attrs: dict):
    """Write one full-grid raster, preserving what the array actually is.

    The dtype is taken from the data rather than forced: casting the complex
    interferogram to float32 would throw away the phase this archive exists to
    carry, and casting the uint8 coherence mask up to float32 would quadruple
    the largest avoidable cost in the file. Integer arrays have no NaN, so
    their nodata is an explicit sentinel supplied by the caller.
    """
    import numpy as np

    # New derived arrays carry their own common-grid metadata. Input-provider
    # identity/resampling belongs to derived_from, not this local calculation.
    derivation_input = None
    if "derived_from" in attrs:
        ident = group.file["identification"].attrs
        if tuple(ident["common_grid_shape"]) != tuple(data.shape):
            raise ValueError("Derived array shape differs from the common grid")
        wkt = ident["common_crs_wkt"]
        transform = np.asarray(ident["common_grid_transform"], dtype="float64")
        resolution = float(ident["common_grid_resolution_m"])
        if (not isinstance(wkt, str) or not wkt.strip() or
                transform.shape != (6,) or not np.isfinite(transform).all() or
                not np.isfinite(resolution) or resolution <= 0 or
                not np.allclose(transform[[0, 1, 3, 4]], [resolution, 0, 0, -resolution])):
            raise ValueError("Derived array requires a valid common-grid CRS and transform")
        source = attrs["derived_from"]
        if not isinstance(source, str) or source not in group.file:
            raise ValueError("Derived array requires an existing same-archive input")
        derivation_input = group.file[source]
        if derivation_input.file.id != group.file.id:
            raise ValueError("Derived array requires an input in the same archive")
        if getattr(derivation_input, "shape", None) != data.shape:
            raise ValueError("Derived input shape differs from the common grid")
        attrs = dict(attrs, crs_wkt=wkt, transform=transform, resolution_m=resolution,
                     source_dataset="CryoGARS derived product", resampling_method="none",
                     resampling_note="Locally derived on the common grid; no additional spatial resampling.",
                     derived_metadata_version="1.0")

    h, w = data.shape
    dt = data.dtype
    kind = dt.kind
    ds = group.create_dataset(
        name, data=data, dtype=dt,
        chunks=(min(chunk, h), min(chunk, w)),
        compression="gzip", compression_opts=4, shuffle=True)

    if kind in "fc":
        finite = np.isfinite(data) if kind == "f" else np.isfinite(data.real)
        nodata = "NaN"
    else:
        sentinel = attrs.get("nodata_value")
        finite = (data != sentinel) if sentinel is not None \
            else np.ones(data.shape, dtype=bool)
        nodata = sentinel if sentinel is not None else "none"
    n = int(finite.sum())
    base = {"dtype": str(dt), "nodata": nodata, "shape": [h, w],
            "valid_pixel_count": n,
            "valid_fraction": float(n) / data.size if data.size else 0.0}
    base.update({k: v for k, v in attrs.items() if not k.startswith("value_")})
    base.update(statistics_attrs(data, finite))
    if (n == 0 and derivation_input is not None and
            derivation_input.attrs.get("valid_pixel_count") == 0 and
            derivation_input.attrs.get("value_stats_stage") == "stored_array" and
            derivation_input.attrs.get("value_stats_status") == "no_valid_values"):
        base["empty_reason"] = "no_valid_input_cells"
    for k, v in base.items():
        ds.attrs[k] = v
    return ds


def inherit_processing(ds, source, extra, stage):
    """Keep provenance; old statistics belong only in the input history."""
    input_processing = processing_snapshot(source.attrs)
    for k, v in source.attrs.items():
        if (k not in ds.attrs
                and (k not in input_processing or k.startswith("cleaning_"))):
            ds.attrs[k] = v
    ds.attrs["cleaning_record_scope"] = "input_archive_last_recorded_pass; not enrichment totals"
    ds.attrs["cleaning_stage"] = source.attrs.get("cleaning_stage", "input_stage_unrecorded")
    ds.attrs["cleaning_note"] = (
        "Inherited cleaning counters/settings, when present, describe the input "
        "archive's last recorded builder cleaning pass. Missing fields are "
        "unrecorded, not zero. Earlier passes and settings may be unknown. "
        "Original input wording is preserved in processing_history. Current "
        "enrichment steps and event counts are recorded separately."
    )
    for k, v in extra.items():
        ds.attrs[k] = v
    record_processing_history(ds.attrs, stage, source.attrs)


def range_metadata(leaf, removed):
    """Describe this enrichment range step, including a skipped step or zero."""
    attrs = {
        "out_of_range_set_to_nodata": removed,
        "range_screening_status": "applied" if leaf in PLAUSIBLE else "skipped_no_bounds",
        "range_note": (
            "This enrichment pass sets finite values outside configured bounds "
            "to nodata. Bounds are heuristic screening thresholds, not proof "
            "that an individual value is an artifact. A skipped step performs "
            "no range removal; its zero counter does not certify validity."
        ),
    }
    if leaf in PLAUSIBLE:
        lo, hi = PLAUSIBLE[leaf]
        attrs["range_lower_bound"] = lo if lo is not None else "unbounded"
        attrs["range_upper_bound"] = hi if hi is not None else "unbounded"
        if lo is not None and hi is not None:
            attrs["plausible_range"] = [lo, hi]
    return attrs


def _datasets(f):
    import h5py
    names = []
    f.visititems(lambda n, o: names.append(n)
                 if isinstance(o, h5py.Dataset) else None)
    return names


#: `MORESCREEK_CUTFROMLOWMAN05208_20210303_20210310` -> line, date1, date2.
FLIGHT_RE = re.compile(r"_CUTFROM(.+?)_(\d{8})_(\d{8})$")


def flight_parts(group_name: str):
    """Split a flight group name into (line, date1, date2), or None."""
    m = FLIGHT_RE.search(group_name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def acquisition_path(group_name: str) -> str | None:
    """Where a flight's arrays live in the restructured tree.

    ASF ships amplitude and interferometry as two separate downloads, so the
    archive grew a top-level split between them. They are the same pair of
    passes -- verified: every site has exactly the same flight set under both,
    with no orphan on either side -- so the split describes how the data was
    packaged, not what it is, and the products are merged here.

    The date pair alone is NOT a unique key. Mores Creek flies three of its
    date pairs on two different lines, so keying on date would silently drop
    one line on top of the other. The line therefore stays as a level below.
    """
    parts = flight_parts(group_name)
    if parts is None:
        return None
    line, d1, d2 = parts
    return f"science/UAVSAR/{d1}_{d2}/{line}"


#: Convention for negative snow depth: finite values between this threshold
#: and zero are clipped to zero; smaller finite values become nodata. This
#: threshold does not establish the cause of individual negative values.
#:
#: Measured over all 71.8 M valid snow-depth cells in the archive: 88.4% of
#: negatives fall in -0.25..0, 9.5% in -1..-0.25, and 2.0% below -1.
SD_NOISE_FLOOR = -0.25


#: Heuristic screening range per layer leaf; values outside become nodata.
#: The screen can remove measurements as well as artifacts. `None` means
#: unbounded; leaves absent from this mapping bypass the range step.
#:
#: snow_depth   the deepest snowpack ever recorded anywhere is about 11.5 m;
#:              15 m is comfortably past any real value at these eight sites,
#:              and Grand Mesa 2017 carries cells up to 19.99 m against a
#:              99th percentile of 2.25 m.
#: elevation    wide enough for Death Valley and the Himalaya, tight enough to
#:              catch -9999 and 1e38 sentinels.
#: veg_height   the tallest known tree is about 116 m.
#: cor          interferometric coherence is a correlation coefficient.
PLAUSIBLE = {
    "snow_depth": (0.0, 15.0),
    "elevation": (-500.0, 9000.0),
    "veg_height": (0.0, 120.0),
    "swe": (0.0, 6000.0),
    "snow_density": (0.0, 1000.0),
    "cor": (0.0, 1.0),
    "amp1": (0.0, None),
    "amp2": (0.0, None),
}

#: Component-size threshold for the LiDAR footprint screen, applied only when
#: more than one component exists. Historical observations on Grand Mesa 20170208:
#: 4,158 components, the largest holding 97.4% of valid cells, and 3,966
#: components under 100 cells holding just 0.14% of the data. The next largest
#: larger patch is 45,817 cells. These counts do not prove what caused a component.
SPECKLE_MIN_CELLS = 100


def apply_range(a, leaf: str):
    """Drop values outside the plausible range for this layer.

    Returns (cleaned, n_removed). Unknown leaves pass through untouched.
    """
    import numpy as np

    if leaf not in PLAUSIBLE:
        return a, 0
    lo, hi = PLAUSIBLE[leaf]
    out = np.array(a, dtype=a.dtype, copy=True)
    fin = np.isfinite(out)
    bad = np.zeros(out.shape, dtype=bool)
    if lo is not None:
        bad |= fin & (out < lo)
    if hi is not None:
        bad |= fin & (out > hi)
    out[bad] = np.nan
    return out, int(bad.sum())


def despeckle(a, min_cells: int = SPECKLE_MIN_CELLS):
    """Remove valid-data islands smaller than `min_cells`.

    This is a heuristic footprint screen, not a determination of what caused
    an isolated return. Four-neighbour components are screened only if there
    is more than one component; a sole small component is retained.
    Returns (cleaned, n_components_removed, n_cells_removed).
    """
    import numpy as np
    from scipy import ndimage

    out = np.array(a, dtype=a.dtype, copy=True)
    valid = np.isfinite(out)
    if not valid.any():
        return out, 0, 0
    lab, n = ndimage.label(valid)
    if n <= 1:
        return out, 0, 0
    sizes = ndimage.sum(valid, lab, range(1, n + 1))
    small = np.flatnonzero(sizes < min_cells) + 1
    if small.size == 0:
        return out, 0, 0
    kill = np.isin(lab, small)
    out[kill] = np.nan
    return out, int(small.size), int(kill.sum())


def swath_validity(arrays: dict):
    """Where a radar acquisition actually measured something.

    UAVSAR ground-range products fill everything outside the flight swath with
    exact 0.0 rather than a nodata value, so 35% of every array in this archive
    is fill masquerading as data -- a backscatter of exactly zero, a coherence
    of exactly zero. Amplitude and coherence agree on where that fill is to
    within 0.06%, so one mask built from both governs every layer of the
    acquisition.

    Unwrapped phase needs the extra term. Its zeros are not only off-swath:
    9.1% of the *valid* swath is exactly 0.0, in 77,079 solid blobs (only two
    isolated cells), at a median coherence of 0.32 against 0.57 elsewhere.
    That is the unwrapper leaving regions it could not resolve at zero, and
    since 0.32 clears the 0.30 coherence threshold, nothing else would catch it.
    """
    import numpy as np

    valid = None
    for k in ("amp1", "amp2", "cor"):
        if k not in arrays:
            continue
        v = arrays[k]
        m = np.isfinite(v) & (v != 0)
        # AND, not OR: a cell counts as measured only where every channel that
        # exists for it recorded a return. Amplitude and coherence disagree on
        # only 60,659 of 285,645,988 cells (0.02%), so the two choices are
        # near-identical in extent -- and where they differ, keeping fill out
        # of a training set matters more than keeping 0.02% more data in.
        valid = m if valid is None else (valid & m)
    return valid


def clean_snow_depth(a):
    """Remove negative snow depth. Returns (cleaned, n_clipped, n_dropped)."""
    import numpy as np

    out = np.array(a, dtype="float32", copy=True)
    fin = np.isfinite(out)
    noise = fin & (out < 0.0) & (out >= SD_NOISE_FLOOR)
    artifact = fin & (out < SD_NOISE_FLOOR)
    out[noise] = 0.0
    out[artifact] = np.nan
    return out, int(noise.sum()), int(artifact.sum())


def projection_dem_stats(uav, lidar):
    """How far JPL's projection surface sits from the lidar ground truth.

    The UAVSAR-side elevation rasters are dropped from the archive -- nothing
    reads them, and 58 of 121 carried unmasked -10000 nodata. What they do
    carry that is not recoverable elsewhere is a bound on geolocation error:
    the radar was ground-projected onto that surface, so its disagreement with
    the lidar DEM sets how far each radar pixel may be displaced. Ground-range
    displacement goes as dh/tan(theta), so this is worth keeping -- but it is a
    handful of numbers, not gigabytes of raster.
    """
    import numpy as np

    # The sentinel cells have to go before any statistic, or the summary
    # describes the fill value rather than the surface.
    m = (np.isfinite(uav) & np.isfinite(lidar) & (uav > -100.0))
    if not m.any():
        return {}
    d = uav[m] - lidar[m]
    return {
        "projection_dem_median_offset_m": float(np.median(d)),
        "projection_dem_mean_abs_offset_m": float(np.mean(np.abs(d))),
        "projection_dem_p99_abs_offset_m": float(np.percentile(np.abs(d), 99)),
        "projection_dem_rmse_m": float(np.sqrt(np.mean(d * d))),
        "projection_dem_compared_cells": int(m.sum()),
        "projection_dem_sentinel_fraction": float(
            (np.isfinite(uav) & (uav <= -100.0)).mean()),
        "projection_dem_note":
            "Summary of the UAVSAR-side elevation model this flight was "
            "ground-projected onto, measured against the lidar DEM. The raster "
            "itself is not stored: nothing reads it and most copies carried "
            "unmasked -10000 nodata. These statistics bound how far a radar "
            "pixel may be displaced, since ground-range error goes as "
            "dh/tan(incidence). Sentinel cells are excluded from the "
            "comparison.",
    }


def enrich(path, out, cache, session=None, with_insitu=False) -> int:
    import h5py
    import numpy as np

    with h5py.File(path, "r") as f:
        ident = f["identification"].attrs
        parent_lineage = {'file': file_identity(path), 'lineage': read_lineage(ident)}
        annotation_inputs = []
        copied_science = set()
        res = float(ident["common_grid_resolution_m"])
        epsg = int(ident["common_crs_epsg"])
        transform = [float(v) for v in ident["common_grid_transform"]]
        chunk = int(ident.get("chunk_edge_px", 128))

        dem_path = "science/LIDAR/DEM/grids/elevation"
        if dem_path not in f:
            log.error("  no %s; nothing to stand a scene on", dem_path)
            return 2, {}

        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()

        stats = {"clipped": 0, "dropped": 0, "dems_removed": 0,
                 "flights": 0, "masks": 0, "annotated": 0,
                 "out_of_range": 0, "speckle_components": 0,
                 "speckle_cells": 0, "swath_fill_cells": 0,
                 "unw_fill_cells": 0}

        with h5py.File(out, "w") as g:
            # ---------- identification, matches: verbatim ----------
            # `insitu` (snow pits, GPR transects) is deliberately NOT carried
            # into v1. The ingestion code in build_hdf5.py is intact and the
            # groups remain in the raw archives, so `--with-insitu` restores
            # them in full whenever the in-situ work is picked back up.
            keep = ["identification", "matches"]
            if with_insitu:
                keep.append("insitu")
            for name in keep:
                if name in f:
                    f.copy(f[name], g, name=name)
            if "insitu" in f and not with_insitu:
                log.info("  insitu present in source, omitted (v1); "
                         "pass --with-insitu to carry it through")

            # ---------- lidar branch ----------
            log.info("  lidar")
            for p in [n for n in _datasets(f) if n.startswith("science/LIDAR/")]:
                leaf = p.rsplit("/", 1)[-1]
                parent = g.require_group(p.rsplit("/", 1)[0])
                if leaf not in ("snow_depth", "elevation", "veg_height",
                                "swe", "snow_density"):
                    f.copy(f[p], parent, name=leaf)
                    copied_science.add(p)
                    continue

                a = f[p][...]
                extra = {"enrichment_stage": "enrichment_lidar",
                         "enrichment_input_stage": "stored_input_archive_array",
                         "enrichment_count_scope": "this_pass_step_events; not cumulative"}

                # 1. negative snow depth, two-tier
                if leaf == "snow_depth":
                    a, nclip, ndrop = clean_snow_depth(a)
                    stats["clipped"] += nclip
                    stats["dropped"] += ndrop
                    extra["negatives_clipped_to_zero"] = nclip
                    extra["negatives_set_to_nodata"] = ndrop
                    extra["negative_noise_floor_m"] = SD_NOISE_FLOOR
                    extra["negative_handling"] = (
                        f"In this enrichment pass, finite snow depths in "
                        f"[{SD_NOISE_FLOOR}, 0) are clipped to 0; finite values "
                        f"below {SD_NOISE_FLOOR} are set to nodata. This is a "
                        f"noise-handling convention, not evidence of the cause "
                        f"of each negative value. Clipped zeros are derived values.")

                # 2. physically impossible values
                a, nrange = apply_range(a, leaf)
                stats["out_of_range"] += nrange
                extra.update(range_metadata(leaf, nrange))

                # 3. speckle: valid islands detached from the survey footprint
                a, ncomp, ncell = despeckle(a)
                stats["speckle_components"] += ncomp
                stats["speckle_cells"] += ncell
                extra["speckle_components_removed"] = ncomp
                extra["speckle_cells_removed"] = ncell
                extra["speckle_min_cells"] = SPECKLE_MIN_CELLS
                extra["speckle_connectivity"] = 4
                extra["speckle_note"] = (
                    f"After negative/range handling, if there is more than one "
                    f"four-neighbour component of finite data, components with "
                    f"fewer than {SPECKLE_MIN_CELLS} cells are set to nodata. "
                    f"A sole component is retained even if smaller. This "
                    f"footprint heuristic does not prove that removed values "
                    f"were noise. Counts describe this pass only.")

                ds = write_grid(parent, leaf, a, chunk, {})
                inherit_processing(ds, f[p], extra, "enrichment_lidar")
                if nrange or ncell or extra.get("negatives_set_to_nodata"):
                    log.info("    %s/%s: %d neg->0, %d neg->nodata, "
                             "%d out-of-range, %d speckle cells in %d islands",
                             p.split("/")[-2], leaf,
                             extra.get("negatives_clipped_to_zero", 0),
                             extra.get("negatives_set_to_nodata", 0),
                             nrange, ncell, ncomp)
            for grp in ("science", "science/LIDAR"):
                if grp in f:
                    for k, v in f[grp].attrs.items():
                        g.require_group(grp).attrs[k] = v

            # ---------- terrain derivatives ----------
            # Use the same cleaned base raster stored in this output archive.
            dem = g[dem_path][...]
            height_reference = dem_vertical_reference(g[dem_path].attrs)
            g[dem_path].attrs.update(height_reference)
            input_lineage = {
                "derived_from_archive": "self",
                "derived_from_stage": "enriched_base_after_cleaning",
                "derivation_version": "1.0",
            }
            dem_lineage = {"derived_from": dem_path, **input_lineage}
            log.info("  slope / aspect")
            slope, aspect = slope_aspect(dem, res)
            d = g.require_group(DERIVED_GROUP)
            d.attrs["description"] = (
                "Layers computed from the cleaned lidar DEM and vegetation "
                "height stored in this output file after enrichment cleaning. "
                "Derived quantities only; no new observations.")
            write_grid(d, "slope", slope, chunk, {
                "units": "degrees",
                "description": "Terrain slope from the lidar DEM, Horn (1981).",
                **dem_lineage, "method": "horn_1981_3x3"})
            write_grid(d, "aspect", aspect, chunk, {
                "units": "degrees",
                "description":
                    "Terrain aspect from the lidar DEM; NaN where flat. This is "
                    "a circular quantity -- encode as sin/cos before modelling.",
                **dem_lineage, "derivation_version": "2.0",
                "method": "horn_1981_3x3_downhill_grid_bearing",
                "aspect_convention": "downhill_clockwise_from_grid_north"})

            win = max(1, int(round(CANOPY_WINDOW_M / res)))
            window_cells = 2 * (win // 2) + 1
            window_effective_m = window_cells * res
            for vp in [n for n in _datasets(g)
                       if n.startswith("science/LIDAR/") and n.endswith("/veg_height")]:
                v = g[vp][...]
                valid = np.isfinite(v)
                fcf = box_fraction(valid & (v >= CANOPY_HEIGHT_M), valid, win)
                date = vp.split("/")[-2]
                write_grid(d, f"forest_cover_fraction_{date}", fcf, chunk, {
                    "units": "fraction, 0-1",
                    "description":
                        f"Fraction of finite cleaned vegetation-height cells "
                        f"at least {CANOPY_HEIGHT_M:.1f} m tall within a centered "
                        f"{window_effective_m:g} x {window_effective_m:g} m "
                        f"window ({window_cells} x {window_cells} cells). "
                        f"Raster edges truncate the window. Missing cells are "
                        f"excluded from the denominator; a missing center may "
                        f"receive a fraction, and no finite cells gives NaN.",
                    "derived_from": vp,
                    **input_lineage,
                    "method": "finite_canopy_fraction_centered_box",
                    "canopy_height_threshold_m": CANOPY_HEIGHT_M,
                    "window_m": CANOPY_WINDOW_M,
                    "window_effective_m": window_effective_m,
                    "window_cells": window_cells,
                    "window_note": (
                        "window_m is the nominal requested side length. "
                        "window_effective_m and window_cells give the actual "
                        "side length in metres and cells, respectively, before "
                        "truncation at raster edges. "
                        "The denominator counts only finite cleaned vegetation "
                        "cells inside that window, including the center if finite."),
                    "proxy_note":
                        "A lidar-derived proxy for forest cover fraction, not "
                        "a land-cover product. Bonnell et al. (2024) report "
                        "L-band SWE error roughly doubling above FCF 0.5; this "
                        "layer exists so that can be tested here."})
                log.info("    canopy fraction %s", date)

            # ---------- radar, restructured ----------
            # One group per acquisition, amplitude and interferometry together.
            acqs: dict[str, dict] = {}
            if "science" in f and "UAVSAR" in f["science"]:
                for level in f["science/UAVSAR"]:
                    if level == "DEM_TIFF":
                        continue          # dropped; summarised as attributes
                    for flight in f[f"science/UAVSAR/{level}"]:
                        dest = acquisition_path(flight)
                        if dest is None:
                            log.warning("  unparsable flight name %r; kept as-is",
                                        flight)
                            continue
                        acqs.setdefault(dest, {"src": [], "name": flight})
                        acqs[dest]["src"].append(f"science/UAVSAR/{level}/{flight}")

            if acqs:
                u = g.require_group("science/UAVSAR")
                u.attrs["description"] = (
                    "Radar grouped by acquisition: one folder per date pair, "
                    "then per flight line. Amplitude and interferometry "
                    "products for the same pair of passes sit together, since "
                    "they describe the same acquisition and were only "
                    "distributed separately.")

            for dest in sorted(acqs):
                src_groups = sorted(acqs[dest]["src"])
                flight = acqs[dest]["name"]
                grp = g.require_group(dest)

                # provenance from every contributing product level
                for sg in src_groups:
                    lvl = sg.split("/")[2]
                    for k, v in f[sg].attrs.items():
                        grp.attrs[f"{lvl.lower()}_{k}" if k in grp.attrs else k] = v
                grp.attrs["original_group_name"] = flight
                line, d1, d2 = flight_parts(flight)
                grp.attrs["flight_line"] = line
                grp.attrs["acquisition_date_pair"] = [d1, d2]

                # arrays, per polarisation, both levels merged
                uav_dem = None
                srcs: dict[str, dict] = collections.defaultdict(dict)
                for sg in src_groups:
                    for pol in f[sg]:
                        node = f[sg][pol]
                        if not isinstance(node, h5py.Group):
                            continue
                        if pol == "GEOMETRY":
                            if "hgt" in node and uav_dem is None:
                                uav_dem = node["hgt"][...]
                            continue
                        srcs[pol].update(
                            {leaf: node[leaf] for leaf in node
                             if leaf != "hgt" and leaf not in srcs[pol]})
                        for leaf in node:
                            if leaf == "hgt":
                                if uav_dem is None:
                                    uav_dem = node[leaf][...]
                                stats["dems_removed"] += 1

                # ---- mask the off-swath fill, then write ----
                # Amplitude and coherence are read first so a single validity
                # mask can govern every layer of the acquisition. Without it,
                # 35% of each array is exact-0.0 fill stored as a measurement.
                for pol, leaves in srcs.items():
                    if not leaves:
                        continue
                    pg = grp.require_group(pol)
                    probe = {k: leaves[k][...] for k in ("amp1", "amp2", "cor")
                             if k in leaves}
                    mask_inputs = list(probe)
                    valid = swath_validity(probe)
                    del probe
                    if valid is not None:
                        nfill = int((~valid).sum())
                        stats["swath_fill_cells"] += nfill
                        pg.attrs["swath_fill_cells_masked"] = nfill
                        pg.attrs["swath_valid_cells"] = int(valid.sum())
                        pg.attrs["swath_count_scope"] = "mask_footprint; includes already_nodata_cells"
                        pg.attrs["swath_mask_inputs"] = mask_inputs
                        pg.attrs["swath_note"] = (
                            "Heuristic mask: a cell must be finite and nonzero "
                            "in every available amp1/amp2/cor input. The mask "
                            "is applied to layers with matching shape. Counts "
                            "describe the mask footprint, including cells "
                            "already nodata, not newly removed finite values "
                            "in each layer. See each layer's application status.")

                    for leaf, dset in sorted(leaves.items()):
                        if leaf in pg:
                            continue
                        a = dset[...]
                        extra = {
                            "enrichment_stage": "enrichment_radar",
                            "enrichment_input_stage": "stored_input_archive_array",
                            "enrichment_count_scope": "this_pass_step_events; not cumulative",
                            "swath_mask_status": ("skipped_no_mask_inputs" if valid is None
                                                  else "skipped_shape_mismatch"),
                            "unw_zero_mask_status": ("skipped_swath_mask_unavailable"
                                                     if leaf == "unw" else "not_applicable"),
                        }
                        if valid is not None and a.shape == valid.shape:
                            extra["swath_mask_status"] = "applied"
                            extra["swath_mask_group"] = pg.name
                            if a.dtype.kind == "c":
                                a = np.where(valid, a,
                                             np.complex64(complex(np.nan,
                                                                  np.nan)))
                            else:
                                a = np.asarray(a, dtype="float32")
                                a[~valid] = np.nan
                            # Unwrapped phase carries a second fill value: the
                            # unwrapper leaves regions it could not resolve at
                            # exactly 0.0, inside the swath, at a coherence
                            # that clears the usability threshold.
                            if leaf == "unw":
                                z = np.isfinite(a) & (a == 0.0)
                                nz = int(z.sum())
                                a[z] = np.nan
                                stats["unw_fill_cells"] += nz
                                pg.attrs["unw_zero_fill_masked"] = nz
                                extra["unw_zero_mask_status"] = "applied"
                                extra["unw_zero_fill_masked"] = nz
                                extra["unw_zero_note"] = (
                                    "Finite exact-zero unwrapped phase cells remaining "
                                    "after the swath mask are set to nodata as a fill "
                                    "heuristic. This may also remove true zero phase; "
                                    "the count does not establish the cause of each zero.")
                        a, nr = apply_range(a, leaf)
                        stats["out_of_range"] += nr
                        extra.update(range_metadata(leaf, nr))
                        ds = write_grid(pg, leaf, a, chunk, {})
                        inherit_processing(ds, dset, extra, "enrichment_radar")

                        if leaf == "cor":
                            # uint8, not float32: this holds three states, and
                            # there are 53 of them per site at 285 M cells.
                            m = np.where(np.isfinite(a),
                                         (a >= COHERENCE_MIN).astype("uint8"),
                                         MASK_NODATA).astype("uint8")
                            write_grid(pg, "coherence_mask", m, chunk, {
                                "units": "1 = usable, 0 = decorrelated, "
                                         "255 = nodata",
                                "description":
                                    f"Coherence at or above {COHERENCE_MIN} is "
                                    "treated as invertible. Stored so every "
                                    "user applies the same threshold rather "
                                    "than choosing one silently.",
                                **input_lineage,
                                "method": "coherence_threshold_inclusive",
                                "coherence_threshold": COHERENCE_MIN,
                                "nodata_value": MASK_NODATA,
                                "derived_from": f"{dest}/{pol}/cor"})
                            stats["masks"] += 1

                # the dropped projection DEM, kept as a diagnostic
                if uav_dem is not None:
                    comparison = projection_dem_stats(uav_dem, dem)
                    for k, v in comparison.items():
                        grp.attrs[k] = v
                    grp.attrs["projection_dem_reference_dataset"] = dem_path
                    grp.attrs["projection_dem_reference_archive"] = "self"
                    grp.attrs["projection_dem_reference_stage"] = input_lineage["derived_from_stage"]
                    grp.attrs["projection_dem_comparison_status"] = (
                        "computed" if comparison else "no_valid_overlap")
                    if not comparison:
                        grp.attrs["projection_dem_compared_cells"] = 0

                # ---- viewing geometry ----
                url = None
                for sg in src_groups:
                    if "source_url" in f[sg].attrs:
                        url = f[sg].attrs["source_url"]
                        break
                if url is None or session is None:
                    continue
                url = url.decode() if isinstance(url, bytes) else str(url)
                try:
                    ann = cached_annotation(session, url, cache)
                except Exception as exc:                       # noqa: BLE001
                    log.warning("  %s: annotation unavailable (%s)", dest, exc)
                    continue
                sc = annotation_scalars(ann)
                annotation_inputs.append({
                    'acquisition': dest, 'source_url': url, 'scalars': sc,
                    'annotation': ann.get('_snowex_annotation_source', {
                        'identity_method': 'annotation_content_digest_not_recorded'})})
                if not sc:
                    grp.attrs["geometry_status"] = "skipped_missing_annotation_fields"
                    grp.attrs["geometry_note"] = (
                        "No geometry annotation scalars; radar_look_direction and peg inputs are required")
                    continue
                stats["annotated"] += 1
                for k, v in sc.items():
                    grp.attrs[k] = v
                grp.attrs["annotation_note"] = (
                    "Viewing geometry and radar parameters read from this "
                    "flight's UAVSAR .ann file.")

                need = ("peg_latitude_deg", "peg_longitude_deg",
                        "peg_heading_deg", "platform_altitude_m", "radar_look_direction")
                missing = [k for k in need if k not in sc]
                if missing:
                    grp.attrs["geometry_status"] = "skipped_missing_annotation_fields"
                    grp.attrs["geometry_note"] = "Missing geometry inputs: " + ", ".join(missing)
                    log.warning("  %s: %s", dest, grp.attrs["geometry_note"])
                    continue
                look = sc["radar_look_direction"]
                if not isinstance(look, str) or look.strip().lower() not in ("left", "right"):
                    grp.attrs["geometry_status"] = "skipped_invalid_look_direction"
                    grp.attrs["geometry_note"] = "radar_look_direction must be explicitly Left or Right"
                    log.warning("  %s: %s", dest, grp.attrs["geometry_note"])
                    continue
                loc, flat = local_incidence(
                    dem, transform, epsg, sc["peg_latitude_deg"],
                    sc["peg_longitude_deg"], sc["peg_heading_deg"],
                    sc["platform_altitude_m"], look)
                geo = grp.require_group("GEOMETRY")
                grp.attrs["geometry_status"] = "computed_approximate_look_side_restricted"
                geometry_lineage = {
                    **dem_lineage,
                    "derivation_version": "3.0",
                    "radar_look_direction": look.strip().title(),
                    "look_side_mask_method": "projected_peg_track_half_plane_v2",
                    "heading_conversion_method": "wgs84_geodesic_tangent_100m",
                    "track_heading_grid_deg": projected_peg_track(
                        sc["peg_latitude_deg"], sc["peg_longitude_deg"], sc["peg_heading_deg"], epsg)[2],
                    **height_reference,
                    "vertical_reference_status": "unverified_no_conversion",
                    "geometry_validation_status": "approximate_not_navigation_validated",
                    "vertical_reference_note": (
                        "DEM reference: " + height_reference["dem_vertical_reference"] + ". "
                        "The aircraft annotation labels its height GPS altitude, without a "
                        "verified reference realization/epoch here. Height compatibility remains "
                        "unverified; no vertical shift or datum conversion is applied. "
                        "These are approximate angles, not validated navigation geometry."),
                    "look_side_mask_note": (
                        "Retains only the declared look side of the approximate projected peg track. "
                        "Opposite-side cells and cells within 1e-7 m of the track are missing "
                        "in both incidence arrays. This is not a radar-swath or terrain-occlusion mask. "
                        "Geographic heading is converted to the local grid bearing. "
                        "Full-track curvature, detailed navigation, squint and vertical-datum reconciliation "
                        "remain unmodelled.")}
                finite_incidence = np.isfinite(loc)
                incidence_valid_count = int(np.count_nonzero(finite_incidence))
                incidence_ge_90_count = int(np.count_nonzero(finite_incidence & (loc >= 90.0)))
                del finite_incidence
                incidence_ge_90_fraction = (incidence_ge_90_count / incidence_valid_count
                                            if incidence_valid_count else float("nan"))
                write_grid(geo, "local_incidence_angle", loc, chunk, {
                    "units": "degrees",
                    "incidence_ge_90_fraction": incidence_ge_90_fraction,
                    "incidence_ge_90_cell_count": incidence_ge_90_count,
                    "incidence_valid_cell_count": incidence_valid_count,
                    "incidence_ge_90_status": ("computed" if incidence_valid_count
                                               else "no_valid_incidence"),
                    "incidence_ge_90_note": (
                        "Fraction of finite local-incidence cells with angle >= 90 degrees. "
                        "The denominator excludes NaN and infinities; no finite cells "
                        "gives NaN, not zero. Computed after the declared look-side restriction "
                        "on the native stored angle grid, "
                        "without a radar-swath restriction. This angular condition is "
                        "not a terrain-occlusion test or a radar-shadow measurement."),
                    "description":
                        "Angle between the radar line of sight and the lidar "
                        "terrain normal -- the theta in the InSAR "
                        "phase-to-depth inversion.",
                    "method": "peg_track_geometry_with_lidar_normals",
                    **geometry_lineage,
                    "derivation_nodata_note": (
                        "Uses the cleaned DEM. Missing DEM cells are replaced "
                        "by its finite-cell mean only for the normal stencils; "
                        "missing center cells are masked in the output. This "
                        "does not fill or change the stored base DEM."),
                    "improvement_note":
                        "Uses normals from the 3 m lidar DEM. Finer spacing alone "
                        "does not validate viewing geometry or height-reference compatibility."})
                write_grid(geo, "incidence_angle_flat", flat, chunk, {
                    "units": "degrees",
                    "description":
                        "Angle from vertical ignoring terrain, so the terrain "
                        "contribution can be separated from viewing geometry.",
                    "method": "peg_track_geometry",
                    **geometry_lineage,
                    "derivation_nodata_note": (
                        "Ignores terrain slope but uses cleaned DEM elevations "
                        "for the line of sight; missing DEM centers are masked.")})
                stats["flights"] += 1
                fraction_label = (f"{100 * incidence_ge_90_fraction:.2f}% of finite cells"
                                  if incidence_valid_count else "unavailable (no finite cells)")
                log.info("  %s  %s  %.1f-%.1f deg  incidence>=90 %s",
                         f"{d1}_{d2}", line,
                         float(np.nanpercentile(loc, 1)),
                         float(np.nanpercentile(loc, 99)), fraction_label)

            gi = g["identification"].attrs
            gi["enrichment_version"] = "3.3.0"
            gi["processing_metadata_version"] = "1.0"
            gi["enrichment_note"] = (
                "Radar is grouped by acquisition (date pair, then flight line) "
                "with amplitude and interferometry together, since they are the "
                "same pair of passes. UAVSAR-side elevation rasters are not "
                "stored -- nothing read them and most carried unmasked nodata -- "
                "and are summarised instead as projection_dem_* attributes on "
                "each acquisition. Adds lidar-derived slope, aspect and "
                "canopy fraction, local incidence angle, and coherence masks. "
                "Source rasters may already have been resampled and cleaned, "
                "including gap interpolation. This enrichment applies the "
                "recorded snow-negative, range, footprint and radar-fill "
                "heuristics where applicable; these do not prove that retained "
                "values are measurements or removed values are artifacts. "
                "Dataset processing_history separates recorded input metadata "
                "from current pass counts and statistics. Missing historical "
                "settings/counts remain unknown. Terrain, canopy and incidence "
                "layers use the cleaned base arrays stored in this output "
                "archive, with explicit derived_from_stage and file scope. "
                "Projection DEM diagnostics also use its cleaned DEM. "
                + ("In-situ groups present in the source are carried through."
                   if with_insitu else "In-situ observations are omitted."))

            record = new_record('enrichment', _BUILD_SOURCES, {
                'scope': 'recomputed science outputs; copied auxiliary science, matches and optional insitu retain original producers',
                'copied_science_paths': sorted(copied_science),
                'enrichment_version': gi['enrichment_version'], 'with_insitu': bool(with_insitu),
                'grid': {'epsg': epsg, 'transform': transform, 'resolution_m': res,
                         'shape': list(dem.shape), 'chunk_edge_px': chunk},
                'cleaning': {'snow_noise_floor_m': SD_NOISE_FLOOR,
                             'plausible_ranges': PLAUSIBLE, 'speckle_min_cells': SPECKLE_MIN_CELLS},
                'canopy_height_m': CANOPY_HEIGHT_M, 'canopy_window_nominal_m': CANOPY_WINDOW_M,
                'coherence_min': COHERENCE_MIN, 'dem_path': dem_path,
                'derivative_input_stage': 'enriched_base_after_cleaning',
                'aspect_convention': 'downhill_clockwise_from_grid_north',
                'geometry_heading_method': 'wgs84_geodesic_tangent_100m',
                'vertical_conversion': 'none; compatibility unverified',
            }, inputs=annotation_inputs, parent=parent_lineage)
            store_lineage(gi, record)
            # A small per-dataset link resolves to the file's full producer
            # record. Preserve any inherited producer record as input history.
            for name in _datasets(g):
                if not name.startswith('science/') or name in copied_science:
                    continue
                ds = g[name]
                store_lineage(ds.attrs, {
                    'schema': 'snowex-producer-reference-v1',
                    'artifact_id': record['artifact_id'], 'record_path': '/identification',
                    'dataset': ds.name, 'input_lineage': read_lineage(ds.attrs)})

    log.info("  %s: %.2f GB -> %.2f GB | %d acq, %d masks, %d DEMs dropped",
             path.stem, path.stat().st_size / 2 ** 30,
             out.stat().st_size / 2 ** 30, stats["flights"], stats["masks"],
             stats["dems_removed"])
    log.info("    cleaned: SD %s clipped / %s nodata | %s out-of-range | "
             "%s speckle cells in %s islands",
             f'{stats["clipped"]:,}', f'{stats["dropped"]:,}',
             f'{stats["out_of_range"]:,}', f'{stats["speckle_cells"]:,}',
             f'{stats["speckle_components"]:,}')
    log.info("    radar fill masked: %s off-swath cells, %s unwrapper zeros",
             f'{stats["swath_fill_cells"]:,}', f'{stats["unw_fill_cells"]:,}')
    return 0, stats


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir", type=Path,
                    default=Path(os.environ.get("SNOWEX_OUT_DIR", here / "out")))
    ap.add_argument("--cache", type=Path, default=here / "ann_cache")
    ap.add_argument("--suffix", default=".enriched")
    ap.add_argument("--no-network", action="store_true",
                    help="skip annotation harvest; terrain layers only")
    ap.add_argument("--with-insitu", action="store_true",
                    help="carry snow pits and GPR transects into the enriched "
                         "copy; omitted by default in v1")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.path.insert(0, str(here))

    if args.all:
        # A site key never contains a dot; everything else in the directory
        # is a variant of one and must not be built as a site of its own.
        sites = sorted(p.stem for p in args.out_dir.glob("*.h5")
                       if "." not in p.stem)
    elif args.site:
        sites = [args.site]
    else:
        ap.error("give --site or --all")

    session = None
    if not args.no_network:
        try:
            import build_hdf5 as B
            session = B.asf_session()
        except Exception as exc:                               # noqa: BLE001
            log.warning("no ASF session (%s); annotations skipped", exc)

    rc = 0
    for s in sites:
        src = args.out_dir / f"{s}.h5"
        if not src.exists():
            log.error("%s: missing", src)
            rc = 2
            continue
        log.info("%s", s)
        code, _ = enrich(src, args.out_dir / f"{s}{args.suffix}.h5",
                         args.cache, session, args.with_insitu)
        rc |= code
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
