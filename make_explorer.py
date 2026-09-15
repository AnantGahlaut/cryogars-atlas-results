#!/usr/bin/env python3
"""
make_explorer.py -- build a complete browser for ONE site's HDF5 file.

Where make_atlas.py produces a curated overview across all sites, this exports
everything in a single file: every group, every dataset, every attribute, and
the match table, with any array renderable in 3D on the site's own terrain.

    python make_explorer.py --site mores_creek --open
    python make_explorer.py --site grand_mesa --stride 12

Every array shares one grid, so the DEM supplies the shape and whichever
dataset you select supplies the colour. That is what makes browsing 250-odd
arrays meaningful rather than a list of thumbnails.

Two resolutions are exported, because the two costs are wildly different. The
terrain mesh is ONE array, so it is cheap to ship at high detail and it is what
makes the scene look like ground rather than a blanket. The colour layers
number in the hundreds, so they dominate file size and are shipped coarse; the
viewer lifts each onto the mesh when you select it. LiDAR layers are the
exception -- there are only a handful and they are the ones worth zooming into,
so they get the fine grid too.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import warnings
import webbrowser
from datetime import datetime
from pathlib import Path
from build_provenance import capture_sources, file_identity, new_record, read_lineage

_EXPORT_SOURCES = capture_sources(__file__)

warnings.filterwarnings("ignore")

#: Vertices to aim for in the terrain mesh. Grids across the archive differ by
#: nearly 7x in cell count, so a fixed stride that suits Mores Creek would ask
#: Banner Summit for a 1.7M-vertex mesh and tens of megabytes of index buffer.
#: The stride is chosen per site to land near this budget instead.
TARGET_VERTICES = 500_000

#: Never go finer than this even on a small site: past it the mesh stops
#: gaining visible detail and only costs download.
MIN_TERRAIN_STRIDE = 4

#: Colour layers are shipped this many times coarser than the mesh. There is
#: one terrain array and there can be hundreds of colour layers, so the mesh is
#: cheap to refine and the colour layers are what govern file size. Sampling a
#: coarser colour layer onto a fine mesh is nearly invisible in practice.
DATA_STRIDE_FACTOR = 4

#: Arrays under this prefix are exported at the fine stride: there are only a
#: few of them and they carry the science people actually zoom into.
FINE_PREFIX = "science/LIDAR/"

DEM_PATH = "science/LIDAR/DEM/grids/elevation"
ASPECT_PATH = "science/LIDAR/DERIVED/aspect"
# Numerical cancellation of mean unit vectors, not a terrain-dispersion cutoff.
ASPECT_RESULTANT_TOLERANCE = 1e-12

#: Colour ramp per array kind, keyed by the dataset's leaf name.
CMAP_BY_LEAF = {
    "elevation": "terrain", "hgt": "terrain",
    "snow_depth": "blues",
    "veg_height": "greens",
    "cor": "viridis",
    "unw": "diverging",
    "amp1": "magma", "amp2": "magma", "amp": "magma",
}

#: Fallback readout units when the source has no unit attribute. Blank means
#: dimensionless or unspecified (including uncalibrated radar amplitude).
UNIT_BY_LEAF = {
    "elevation": "m", "hgt": "m", "snow_depth": "m", "veg_height": "m",
    "unw": "rad", "cor": "", "amp1": "", "amp2": "",
    "slope": "°", "aspect": "°",
    "incidence_angle_flat": "°", "local_incidence_angle": "°",
}

#: Human-readable names. The leaf names are JPL/NSIDC shorthand and mean
#: nothing to someone opening the file for the first time.
LABEL_BY_LEAF = {
    "elevation": "Elevation", "snow_depth": "Snow depth",
    "veg_height": "Vegetation height", "cor": "Coherence",
    "unw": "Unwrapped phase", "hgt": "InSAR height",
    "int": "Interferogram", "amp1": "Amplitude, pass 1",
    "amp2": "Amplitude, pass 2", "amp": "Amplitude",
}

#: Quantities whose distribution is heavily skewed. Radar amplitude and
#: interferogram magnitude are log-normal: a linear min-to-max stretch spends
#: the whole colour ramp on a few bright outliers and renders the scene black.
#: These are stretched between percentiles instead.
STRETCH_PERCENTILE = {"amp1", "amp2", "amp", "magnitude"}

POL_RE = re.compile(r"^(HH|HV|VH|VV)$")
PAIR_RE = re.compile(r"(?:(.+)_)?(\d{8})_(\d{8})$")
DATE_RE = re.compile(r"(?:(.+)_)?(\d{8})$")


def pick_terrain_stride(gh: int, gw: int) -> int:
    """Mesh stride that lands this site's grid near TARGET_VERTICES."""
    return max(MIN_TERRAIN_STRIDE,
               math.ceil(math.sqrt(gh * gw / TARGET_VERTICES)))


def domain_of(path: str) -> str:
    """Which branch of the archive a path belongs to. Drives colour in the UI."""
    if path.startswith("science/LIDAR"):
        return "lidar"
    # Raw archives carry product type in the group prefix. Enriched archives
    # merge amplitude and interferometry beneath acquisition/line groups, so
    # their product type has to be recovered from the dataset leaf instead.
    if path.startswith("science/UAVSAR/INTERFEROMETRY"):
        return "insar"
    if path.startswith("science/UAVSAR/AMPLITUDE"):
        return "amp"
    if path.startswith("science/UAVSAR/DEM"):
        return "udem"
    if path.startswith("science/UAVSAR/"):
        leaf = path.rsplit("/", 1)[-1].split(" ", 1)[0]
        if leaf in {"amp", "amp1", "amp2"}:
            return "amp"
        if (leaf in {"int", "unw", "cor", "hgt", "coherence_mask",
                     "local_incidence_angle", "incidence_angle_flat"}
                or "/GEOMETRY/" in path):
            return "insar"
    if path.startswith("matches"):
        return "match"
    return "meta"


def describe(path: str, leaf: str, suffix: str = "") -> dict:
    """Qualify a quantity using its enriched or base acquisition structure."""
    parts = path.split("/")
    # Only radar paths carry a polarisation. The LiDAR branch uses VH as the
    # group name for vegetation height, which is not the VH cross-pol channel,
    # and labelling it as one would be a plain misstatement of what the array is.
    radar = parts[:2] == ["science", "UAVSAR"]
    pol = next((p for p in parts if POL_RE.fullmatch(p)), "") if radar else ""
    date = line = ""
    # Enriched: UAVSAR/date_pair/flight_line/...; base: product/line_date_pair/...
    # Match complete group components, never dates embedded in a dataset name.
    for i, part in enumerate(parts[:-1]):
        pair = PAIR_RE.fullmatch(part)
        single = DATE_RE.fullmatch(part) if not pair else None
        if not (pair or single):
            continue
        prefix, a = (pair or single).group(1, 2)
        if prefix and not radar:
            continue
        b = pair.group(3) if pair else a
        date = f"{a[:4]}-{a[4:6]}-{a[6:8]}"
        if a != b:
            date += f" → {b[:4]}-{b[4:6]}-{b[6:8]}"
        if radar:
            line = prefix or (parts[i + 1] if i == 2 and i + 2 < len(parts) else "")
        break

    name = LABEL_BY_LEAF.get(leaf, leaf)
    if suffix:
        name = f"{name} {suffix}"
    bits = [name] + [x for x in (date, line, pol) if x]
    # `short` is what a tab can fit; `label` is the fully qualified name the
    # legend and the info card carry.
    return {"label": "  ·  ".join(bits), "short": name, "pol": pol, "date": date,
            "leaf": leaf + (f" {suffix}" if suffix else "")}


def display_unit(leaf: str, attrs) -> str:
    """Use declared units without changing values or copying convention prose."""
    unit = str(attr_to_json(attrs.get("units", attrs.get("unit", ""))) or "").strip()
    if not unit:
        return UNIT_BY_LEAF.get(leaf, "")
    normalized = unit.lower()
    if normalized in {"°", "deg"} or re.match(r"degrees?\b", normalized):
        return "°"
    if normalized in {"rad", "radian", "radians"}:
        return "rad"
    if (normalized in {"1", "unitless", "dimensionless", "fraction", "fraction, 0-1"}
            or (leaf == "coherence_mask" and normalized == "1 = usable, 0 = decorrelated, 255 = nodata")):
        return ""
    return unit


def block_mean(a, stride):
    import numpy as np

    if stride <= 1:
        return a
    h, w = a.shape
    h2, w2 = (h // stride) * stride, (w // stride) * stride
    return np.nanmean(
        a[:h2, :w2].reshape(h2 // stride, stride, w2 // stride, stride),
        axis=(1, 3))


def block_circular_mean_degrees(a, stride):
    """Equal-finite-pixel mean bearing in [0, 360); undefined means are NaN.

    Match block_mean's edge cropping. Reduce one output row at a time so the
    Grand Mesa raster does not need full-size float64 trigonometric copies.
    Mean unit-vector lengths <= ASPECT_RESULTANT_TOLERANCE are undefined.
    """
    import numpy as np

    if stride <= 1:
        result = np.array(a, dtype=np.float64, copy=True)
        result[~np.isfinite(result)] = np.nan
        np.remainder(result, 360.0, out=result)
    else:
        h, w = a.shape[0] // stride, a.shape[1] // stride
        result = np.full((h, w), np.nan)
        for row in range(h):
            angles = np.array(a[row * stride:(row + 1) * stride, :w * stride],
                              dtype=np.float64, copy=True).reshape(stride, w, stride)
            valid = np.isfinite(angles)
            count = valid.sum(axis=(0, 2))
            angles[~valid] = 0.0
            np.remainder(angles, 360.0, out=angles)
            np.deg2rad(angles, out=angles)
            sine = np.sum(np.sin(angles), axis=(0, 2), where=valid)
            cosine = np.sum(np.cos(angles), axis=(0, 2), where=valid)
            direction = np.degrees(np.arctan2(sine, cosine)) % 360.0
            # Compare sums against count * tolerance to avoid dividing by zero.
            direction[(count == 0) | (np.hypot(sine, cosine) <=
                                      count * ASPECT_RESULTANT_TOLERANCE)] = np.nan
            result[row] = direction
    result[result >= 360.0] = 0.0  # Modulo of a tiny negative can round to 360.
    return result


def declared_nodata_to_nan(dataset, values):
    """Remove a dataset's finite nodata sentinel before display processing.

    Enriched floating-point science layers already use NaN, but categorical
    uint8 products such as ``coherence_mask`` carry 255 as an explicit nodata
    value.  Letting that sentinel participate in block means or range finding
    turns the real 0/1 mask into an almost-black 0/255 colour stretch.
    """
    import numpy as np

    nodata = dataset.attrs.get("nodata", dataset.attrs.get("nodata_value"))
    if nodata is None or np.iscomplexobj(values):
        return values
    try:
        nodata = float(np.asarray(nodata).reshape(-1)[0])
    except (TypeError, ValueError, IndexError):
        return values
    if not np.isfinite(nodata):
        return values
    invalid = values == nodata
    if not invalid.any():
        return values
    clean = values.astype(np.float32, copy=True)
    clean[invalid] = np.nan
    return clean


def quantise(a, bits=8, stretch=False):
    """Pack to uint8/uint16 with 0 reserved for nodata."""
    import numpy as np

    valid = np.isfinite(a)
    if not valid.any():
        return None
    if stretch:
        vals = a[valid]
        lo, hi = (float(np.percentile(vals, 2)), float(np.percentile(vals, 98)))
        if hi <= lo:
            lo, hi = float(vals.min()), float(vals.max())
    else:
        lo, hi = float(np.nanmin(a)), float(np.nanmax(a))
    span = hi - lo if hi > lo else 1.0
    top = (1 << bits) - 1
    q = np.zeros(a.shape, dtype=np.uint16 if bits == 16 else np.uint8)
    scaled = np.clip((a[valid] - lo) / span, 0.0, 1.0)
    q[valid] = 1 + np.round(scaled * (top - 1)).astype(np.int64)
    return {"lo": lo, "hi": hi, "bits": bits, "stretched": bool(stretch),
            "w": int(a.shape[1]), "h": int(a.shape[0]),
            "b64": base64.b64encode(q.tobytes()).decode(),
            "valid": int(valid.sum()), "total": int(a.size)}


def attr_to_json(v):
    """Make an HDF5 attribute JSON-safe without losing what it says."""
    import numpy as np

    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray):
        if v.dtype.kind == "S":
            return [x.decode("utf-8", "replace") for x in v.tolist()]
        return v.tolist()
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return f if f == f and abs(f) != float("inf") else str(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def build_site(site: str, path: Path, args, sites: list) -> tuple:
    """Render one site's explorer. Returns (exit code, summary dict)."""
    import h5py
    import numpy as np
    tree: list[dict] = []
    arrays: dict[str, dict] = {}
    matches: dict[str, list] = {}
    matched: dict[str, list] = {}
    insitu: dict[str, dict] = {}
    surveys: list[dict] = []
    acquisitions: list[dict] = []
    input_file = file_identity(path)

    with h5py.File(path, "r") as f:
        ident = {k: attr_to_json(v) for k, v in f["identification"].attrs.items()}
        parent_lineage = read_lineage(f['identification'].attrs)
        gh, gw = (int(v) for v in f["identification"].attrs["common_grid_shape"])
        a, b, c, d, e, ff = (float(v) for v in
                             f["identification"].attrs["common_grid_transform"])
        res = float(f["identification"].attrs["common_grid_resolution_m"])

        # Chosen per site: one fixed stride cannot serve grids that differ
        # by 7x in cell count without either starving the small sites of
        # detail or handing the browser a mesh it cannot hold.
        fine = args.terrain_stride or pick_terrain_stride(gh, gw)
        coarse = args.stride or fine * DATA_STRIDE_FACTOR

        if DEM_PATH not in f:
            print(f"  no {DEM_PATH}; nothing to stand a scene on",
                  file=sys.stderr)
            return 2, {}

        dem_native = f[DEM_PATH][...]
        # The site's own footprint, at full resolution. Everything reported as
        # "coverage" is measured against this rather than against the stored
        # rectangle, because the rectangle is a bounding box and no flight
        # fills one.
        dem_valid_fraction = float(np.isfinite(dem_native).mean())
        dem = block_mean(dem_native, fine)
        H, W = dem.shape
        terrain = quantise(dem, 16)

        def record(name, obj):
            entry = {"path": name,
                     "attrs": {k: attr_to_json(v) for k, v in obj.attrs.items()}}
            if isinstance(obj, h5py.Dataset):
                entry.update(type="dataset", shape=list(obj.shape),
                             dtype=str(obj.dtype),
                             chunks=list(obj.chunks) if obj.chunks else None,
                             stored=int(obj.id.get_storage_size()))
            else:
                entry["type"] = "group"
            tree.append(entry)

        f.visititems(record)

        # matches table, read whole -- it is a handful of short columns
        if "matches" in f:
            for col in f["matches"]:
                vals = f["matches"][col][...]
                matches[col] = [v.decode("utf-8", "replace")
                                if isinstance(v, bytes) else attr_to_json(v)
                                for v in vals.tolist()]

        # Which lidar dates each radar acquisition was paired with. The match
        # table still keys on the pre-restructure group name, so the bridge is
        # `original_group_name`, recorded on every acquisition for exactly this.
        by_original: dict[str, str] = {}
        if "science" in f and "UAVSAR" in f["science"]:
            for dp in f["science/UAVSAR"]:
                for line in f[f"science/UAVSAR/{dp}"]:
                    grp = f[f"science/UAVSAR/{dp}/{line}"]
                    og = grp.attrs.get("original_group_name")
                    if og is not None:
                        og = og.decode() if isinstance(og, bytes) else str(og)
                        by_original[og] = f"science/UAVSAR/{dp}/{line}"

        # In-situ points. Kept whole rather than downsampled: there are tens to
        # hundreds per site, not millions, and each one is an actual visit
        # somebody made. grid_row/grid_col come from the archive, so the viewer
        # never has to reproject anything.
        # In-situ is out of scope for v1. The groups stay in the raw archives
        # and this block is intact; `--with-insitu` turns it back on.
        if "insitu" in f and getattr(args, "with_insitu", False):
            for key in f["insitu"]:
                g = f["insitu"][key]
                if "grid_row" not in g or "grid_col" not in g:
                    continue
                cols = {}
                for name in g:
                    arr = g[name][...]
                    if arr.dtype.kind in "SO":
                        cols[name] = [x.decode("utf-8", "replace")
                                      if isinstance(x, bytes) else str(x)
                                      for x in arr.tolist()]
                    else:
                        cols[name] = [None if v != v else round(float(v), 4)
                                      for v in arr.astype("float64").tolist()]
                insitu[key] = {
                    "columns": cols,
                    "count": int(len(g["grid_row"])),
                    "attrs": {k: attr_to_json(v) for k, v in g.attrs.items()},
                    "kind": "pit" if "pit_id" in cols else "gpr",
                }

        # Timeline: when each lidar survey happened, and the span each radar
        # pair covers. Acquisitions are intervals, not instants -- the pass1 to
        # pass2 baseline is the thing that decorrelates -- so both ends travel.
        for kind in ("SD", "VH"):
            grp = f.get(f"science/LIDAR/{kind}")
            if not grp:
                continue
            for date in grp:
                leaf = "snow_depth" if kind == "SD" else "veg_height"
                surveys.append({"product": kind, "date": date,
                                "path": f"science/LIDAR/{kind}/{date}/{leaf}"})
        surveys.sort(key=lambda x: (x["date"], x["product"]))

        for dest, orig in sorted((v, k) for k, v in by_original.items()):
            d1, d2 = dest.split("/")[2].split("_")
            acquisitions.append({
                "path": dest, "d1": d1, "d2": d2,
                "line": dest.split("/")[3],
                "baseline": (datetime(int(d2[:4]), int(d2[4:6]), int(d2[6:])) -
                             datetime(int(d1[:4]), int(d1[4:6]), int(d1[6:]))).days,
            })
        acquisitions.sort(key=lambda x: (x["d1"], x["d2"], x["line"]))

        if matches.get("group_name"):
            n_rows = len(matches["group_name"])
            for i in range(n_rows):
                if matches.get("verdict", [""] * n_rows)[i] != "match":
                    continue
                dest = by_original.get(matches["group_name"][i])
                if dest is None:
                    continue
                entry = {
                    "date": matches.get("lidar_date", [""] * n_rows)[i],
                    "gap": matches.get("gap_days", [None] * n_rows)[i],
                    "product": matches.get("lidar_product_type", [""] * n_rows)[i],
                }
                seen = matched.setdefault(dest, [])
                if entry not in seen:
                    seen.append(entry)
            for v in matched.values():
                v.sort(key=lambda e: (e["date"], e["product"]))

        n_done = 0
        derived: list[dict] = []
        for entry in list(tree):
            if entry["type"] != "dataset" or not entry["path"].startswith("science"):
                continue
            name = entry["path"]
            ds = f[name]
            if tuple(ds.shape) != (gh, gw):
                continue
            leaf = name.rsplit("/", 1)[-1]
            dom = domain_of(name)
            st = fine if name.startswith(FINE_PREFIX) else coarse
            raw = declared_nodata_to_nan(ds, ds[...])
            # Counted at native resolution, not on the display grid. A block
            # mean returns a number whenever ANY cell in the block had one, so
            # a downsampled count silently overstates coverage.
            native = {"valid": int(np.isfinite(raw).sum()), "total": int(raw.size)}

            if np.iscomplexobj(raw):
                # Wrapped phase is the science in a complex interferogram, so
                # magnitude and phase are exported as separate viewable layers.
                # Phase is downsampled as the ARGUMENT OF THE COMPLEX MEAN, not
                # the mean of the angles: averaging angles across the -pi/+pi
                # wrap would return 0 for a block straddling the seam and paint
                # a false fringe everywhere the interferogram wraps.
                for suffix, arr, cmap, unit, stretch in (
                        ("|magnitude|", block_mean(np.abs(raw), st),
                         "magma", entry["attrs"].get("magnitude_units", ""), True),
                        ("∠phase", np.angle(block_mean(raw, st)),
                         "cyclic", "rad", False)):
                    packed = quantise(arr, stretch=stretch)
                    if not packed:
                        continue
                    key = f"{name} {suffix}"
                    arrays[key] = {**packed, **native, "display_valid": packed["valid"],
                                   "display_total": packed["total"], "cmap": cmap,
                                   "unit": unit, "source": name, "domain": dom,
                                   "cell_m": res * st,
                                   **describe(name, leaf, suffix)}
                    # A complex dataset yields two viewable quantities. Giving
                    # each its own tree row is the only way to reach both.
                    derived.append({
                        "path": key, "type": "dataset", "derived": True,
                        "shape": entry.get("shape"), "dtype": str(raw.dtype),
                        "attrs": {"derived_from": name,
                                  "quantity": suffix,
                                  "note": "computed from the complex array"}})
            else:
                is_aspect = name == ASPECT_PATH
                display = (block_circular_mean_degrees(raw, st) if is_aspect
                           else block_mean(raw, st))
                q = quantise(display, stretch=leaf in STRETCH_PERCENTILE)
                if q:
                    arrays[name] = {**q, **native, "display_valid": q["valid"],
                                    "display_total": q["total"],
                                    "cmap": CMAP_BY_LEAF.get(leaf, "viridis"),
                                    "unit": display_unit(leaf, entry["attrs"]),
                                    "source": name, "domain": dom,
                                    "cell_m": res * st,
                                    **describe(name, leaf)}
                    if is_aspect:
                        arrays[name]["aggregation"] = {
                            "method": "circular_mean_degrees",
                            "weighting": "equal_finite_pixels",
                            "resultant_tolerance": ASPECT_RESULTANT_TOLERANCE,
                            "undefined": "nodata"}
            n_done += 1
            if n_done % 25 == 0:
                print(f"  {n_done} arrays...")
        tree.extend(derived)
        tree.sort(key=lambda t: t["path"])

    export_lineage = new_record('explorer_data_export', _EXPORT_SOURCES,
        {'site': site, 'terrain_stride': fine, 'data_stride': coarse,
         'with_insitu': bool(getattr(args, 'with_insitu', False)),
         'grid_shape': [gh, gw], 'grid_transform': [a, b, c, d, e, ff], 'resolution_m': res,
         'fine_prefix': FINE_PREFIX, 'terrain_bits': 16, 'data_bits': 8,
         'stretch_percentiles': [2, 98], 'stretch_leaves': sorted(STRETCH_PERCENTILE),
         'aspect_resultant_tolerance': ASPECT_RESULTANT_TOLERANCE,
         'complex_phase_aggregation': 'argument_of_complex_mean',
         'roster': sites,
         'input_status': 'unchanged_size_mtime' if input_file == file_identity(path) else 'changed_during_export'},
        inputs=[{'dataset_paths': sorted({item['source'] for item in arrays.values()}),
                 'dataset_lineage_location': 'payload.tree[].attrs'}],
        parent={'file': input_file, 'lineage': parent_lineage})
    payload = {
        "build_provenance": export_lineage,
        "file": path.name,
        "site": site,
        "sites": sites,
        "generated": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "identification": ident,
        "dem_path": DEM_PATH,
        "grid": {"w": W, "h": H, "cell_m": res * fine,
                 "full": [gh, gw], "res_m": res,
                 "dem_valid_fraction": dem_valid_fraction,
                 "origin": [c, ff], "pixel": [a * fine, e * fine]},
        "terrain": terrain,
        "tree": tree,
        "arrays": arrays,
        "matches": matches,
        "matched": matched,
        "timeline": {"surveys": surveys, "acquisitions": acquisitions},
        "insitu": insitu,
    }

    args.viewer_dir.mkdir(parents=True, exist_ok=True)
    out = args.viewer_dir / f"{site}_explorer.html"
    from refresh_explorers import render_explorer
    html = render_explorer(json.dumps(payload), args.template)
    out.write_text(html, encoding="utf-8")

    n_groups = sum(1 for t in tree if t["type"] == "group")
    n_ds = sum(1 for t in tree if t["type"] == "dataset")
    n_attr = sum(len(t["attrs"]) for t in tree) + len(ident)
    mb = out.stat().st_size / 1024 / 1024
    print(f"  {n_groups} groups, {n_ds} datasets, {n_attr} attributes")
    print(f"  mesh {W}x{H} @ {res*fine:.0f} m ({(W*H)/1e3:.0f}k vertices), "
          f"{len(arrays)} layers @ {res*coarse:.0f} m")
    if insitu:
        print("  in-situ: " + ", ".join(f"{k} x{v['count']}"
                                        for k, v in sorted(insitu.items())))
    print(f"  -> {out.name}  ({mb:.2f} MB)")
    return 0, {
        "key": site, "name": ident.get("site_name", site),
        "state": ident.get("state", ""), "file": out.name,
        "groups": n_groups, "datasets": n_ds, "attrs": n_attr,
        "layers": len(arrays), "mb": round(mb, 2),
        "mesh": f"{W}x{H}", "cell_m": round(res * fine),
        "grid": f"{gh}x{gw}",
    }


def write_index(viewer_dir: Path, sites: list, generated: str) -> Path:
    """Use the same Field Atlas template as the fast, index-only refresh.

    Keep this signature for build callers. Per-site timestamps and statistics
    now come directly from each saved export rather than the build summary.
    """
    from make_index import build_index

    return build_index(viewer_dir, files=[s["file"] for s in sites])


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", help="site key, e.g. mores_creek")
    ap.add_argument("--all", action="store_true",
                    help="build every site found in --out-dir, plus an index")
    ap.add_argument("--prefer-plain", action="store_true",
                    help="use <site>.h5 even when an enriched copy exists")
    ap.add_argument("--with-insitu", action="store_true",
                    help="draw snow pits and GPR points; omitted by default in v1")
    ap.add_argument("--out-dir", type=Path,
                    default=Path(os.environ.get("SNOWEX_OUT_DIR", here / "out")))
    ap.add_argument("--viewer-dir", type=Path, default=here / "viewer")
    ap.add_argument("--template", type=Path,
                    default=here / "explorer_template.html")
    ap.add_argument("--stride", type=int,
                    help="override the colour-layer stride (default: auto)")
    ap.add_argument("--terrain-stride", type=int,
                    help="override the mesh stride (default: auto per site)")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args(argv)
    if not args.site and not args.all:
        ap.error("give --site or --all")
    if not args.template.exists():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 2

    def source_for(key: str):
        """Prefer the enriched copy: same data plus the derived geometry."""
        rich = args.out_dir / f"{key}.enriched.h5"
        plain = args.out_dir / f"{key}.h5"
        if not args.prefer_plain and rich.exists():
            return rich
        return plain if plain.exists() else None

    if args.all:
        # A site key never contains a dot. Everything else in the directory is
        # a variant of one -- <site>.enriched.h5, <site>.prebuild.h5, and
        # whatever suffix gets added next -- and must not be mistaken for a
        # site of its own. Stripping known suffixes by name would silently
        # start building backups again the first time a new one appears.
        keys = sorted({p.stem for p in args.out_dir.glob("*.h5")
                       if "." not in p.stem})
    else:
        keys = [args.site]

    # The site switcher inside each page needs the full roster, names and all,
    # BEFORE the first page is written -- names discovered afterwards would
    # come too late for every page already on disk. Reading one attribute per
    # file is cheap next to rendering them.
    import h5py

    roster = []
    for k in keys:
        src = source_for(k)
        if src is None:
            continue
        try:
            with h5py.File(src, "r") as f:
                name = f["identification"].attrs.get("site_name", k)
            name = name.decode() if isinstance(name, bytes) else str(name)
        except Exception:                                      # noqa: BLE001
            name = k
        roster.append({"key": k, "name": name,
                       "file": f"{k}_explorer.html"})

    built, rc = [], 0
    for k in keys:
        src = source_for(k)
        if src is None:
            print(f"{k}: no .h5 in {args.out_dir}", file=sys.stderr)
            rc = 2
            continue
        print(f"{k}  <- {src.name}")
        code, summary = build_site(k, src, args, roster)
        if code:
            rc |= code
            continue
        built.append(summary)

    # Only a full run knows the whole roster. Writing the index after a
    # single-site build would replace the eight-card landing page with a
    # one-card one, silently orphaning the other seven.
    if built and args.all:
        idx = write_index(args.viewer_dir,
                          sorted(built, key=lambda s: -s["layers"]),
                          datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"))
        print(f"\n{len(built)} explorer(s), "
              f"{sum(s['mb'] for s in built):.1f} MB total")
        print(f"-> {idx}")
    elif built:
        print(f"\n{len(built)} explorer(s) rebuilt; "
              f"index left as-is (run --all to refresh it)")
    if built and args.open:
        target = (args.viewer_dir / f"{args.site}_explorer.html"
                  if args.site else args.viewer_dir / "index.html")
        webbrowser.open(target.resolve().as_uri())
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
