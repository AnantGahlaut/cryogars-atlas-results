#!/usr/bin/env python3
"""
build_hdf5.py -- SnowEx LiDAR + UAVSAR HDF5 co-storage pipeline.

Builds one HDF5 file per site containing NASA SnowEx LiDAR products (DEM, snow
depth, vegetation height) alongside matching NASA/JPL UAVSAR L-band products,
all resampled onto a single common grid derived from that site's LiDAR DEM.

Single file by design: the verifier imports the exact same grid-derivation code
the builder uses, so the two cannot drift out of sync.

Modes
-----
    preflight   discover granules, match LiDAR <-> UAVSAR by footprint + date,
                write an inventory JSON. No downloads, no auth required.
    build       download, reproject, clip, resample, write HDF5. Needs auth.
    verify      reopen written files and check grid/attribute invariants.

Usage
-----
    python build_hdf5.py --mode preflight
    python build_hdf5.py --mode preflight --sites banner_summit --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from processing_metadata import record_processing_history, statistics_attrs
from processing_metadata import _SOURCE_SNAPSHOT as _PROCESSING_SOURCES
from typing import Any, Sequence
from build_provenance import capture_sources, file_identity, json_value, new_record, read_lineage, store_lineage

_BUILD_SOURCES = {**capture_sources(__file__), **_PROCESSING_SOURCES}

log = logging.getLogger("build_hdf5")

# =====================================================================
# SECTION 1 -- CONFIGURATION
#
# Everything here is a resolved decision from the project README. Values
# marked "expected" are preflight assertions, not inputs: the pipeline
# reads the real value from the data and warns if reality disagrees.
# =====================================================================

VERSION = "0.1.0"

#: Match window between a LiDAR acquisition date and a UAVSAR acquisition date.
MATCH_WINDOW_DAYS = 5

#: Buffer beyond the site footprint when clipping UAVSAR (~167 px at 3 m).
CLIP_BUFFER_M = 500.0

#: Common grid resolution. LiDAR native; UAVSAR is upsampled onto it.
TARGET_RES_M = 3.0

#: UAVSAR true ground resolution before resampling, at ~44 N. Recorded in
#: metadata so the 3 m grid is never mistaken for a native 3 m measurement.
UAVSAR_NATIVE_RES_M = (4.45, 6.18)  # (east-west, north-south)

#: ASF processing levels we keep. Everything else (KMZ, METADATA, the
#: polarimetric-only levels) is not part of this archive.
UAVSAR_LEVELS = ("INTERFEROMETRY_GRD", "AMPLITUDE_GRD", "DEM_TIFF")

#: Polarizations that a UAVSAR archive *may* contain. Which ones are actually
#: present varies per acquisition and is discovered from the archive, never
#: assumed -- measured at Grand Mesa, two of five interferograms carry the full
#: quad-pol set (16 arrays, as the README describes) while the other three carry
#: HH alone (4 arrays). ASF's `polarization` metadata field reports "HH" for all
#: of them and cannot be trusted to tell them apart; only the member filenames
#: inside the archive can. See `parse_grd_member`.
POLARIZATIONS = ("HH", "HV", "VH", "VV")

#: Sub-products inside one INTERFEROMETRY_GRD archive, per polarization.
INSAR_SUBPRODUCTS = ("int", "unw", "cor", "hgt")

#: Sub-products taken from an AMPLITUDE_GRD archive. It also ships a `hgt.grd`
#: that is the same file as the interferometry archive's, so it is not read
#: twice; see UAVSAR_DUPLICATE_SUBPRODUCTS.
AMPLITUDE_SUBPRODUCTS = ("amp1", "amp2")

#: Members present in more than one archive for the same scene. Whichever
#: archive is processed first supplies them.
UAVSAR_DUPLICATE_SUBPRODUCTS = {"AMPLITUDE_GRD": ("hgt",)}

#: Resampling method per array kind. `.int` is complex wrapped phase: bilinear
#: would average across the 2*pi wrap boundary and produce meaningless values.
RESAMPLING = {
    "int": "nearest",
    "unw": "bilinear",
    "cor": "bilinear",
    "hgt": "bilinear",
    "amp": "bilinear",
    "dem_tiff": "bilinear",
    "lidar": "bilinear",
}

# --- LiDAR source datasets -------------------------------------------------

QSI_DEM = "SNEX20_QSI_DEM_3m"
QSI_SD = "SNEX20_QSI_SD_3m"
QSI_VH = "SNEX20_QSI_VH_3m"
HRSI_CO = "SNEX_HRSI_SD_DEM_CO"
GM_LIDAR = "SNEX20_GM_Lidar"
ORNL_IDAHO = "LiDAR_Veg_Ht_Idaho_1532"

#: Airborne Snow Observatory 3 m snow depth. This is how the SnowEx 2017
#: Grand Mesa surveys are published -- they are not in the QSI collection,
#: which is why a footprint search returned 2017 UAVSAR for Grand Mesa but
#: nothing ever matched it: there was no 2017 lidar date to pair against.
ASO_SD = "ASO_3M_SD"

#: Two Mores Creek Summit lidar series that sit outside the QSI collection and
#: are therefore invisible to a site-code search. Between them they cover
#: 2022-2026 at 98% and 100% overlap with the mores_creek grid respectively.
#:
#: There is no UAVSAR to pair them with -- the SnowEx airborne campaign ended
#: in 2021, confirmed by both a footprint search and a flight-line search that
#: return zero products after that year. They are carried as snow-depth truth
#: for whatever L-band comes next, NISAR included, not as trainable scenes now.
#: Grand Mesa SWE and snow density, derived from lidar snow depth combined
#: with two independent GPR surveys. Density is the only measured input to the
#: permittivity term in the phase-to-depth inversion anywhere in this archive;
#: every other site would have to assume a value.
GM_SWE_SD = "SNEX20_GM_SWE_SD"

#: GPR transects carrying relative permittivity directly, rather than a density
#: a user has to convert. Grand Mesa's dates fall inside the same fortnight as
#: the 2017 ASO snow-depth surveys; Cameron Pass spans two winters.
GM_PERM = "SNEX17_SD_Perm"
COCP_PERM = "SNEX20_COCP_SPD"

#: Snow pits. Each visit records two density profiles and a measured depth, so
#: these are the closest thing to ground truth for the density term. They are
#: campaign-wide files -- one CSV covers every site -- and are filtered to each
#: site on ingest.
TS_PITS_20 = "SNEX20_TS_SP"
TS_PITS_21 = "SNEX21_TS_SP"

#: Published pit column -> stored name, keyed on a NORMALISED form of the
#: header: lowercased with every non-alphanumeric character removed. The two
#: campaigns publish "Density A Mean (kg/m^3)" and "SWE (mm)", and an earlier
#: version of this map guessed "Density A mean (kg/m3)" and "SWE mean (mm)".
#: Those near-misses matched nothing and silently dropped density entirely --
#: the single most valuable column here. Normalising makes the match immune to
#: capitalisation, spacing and the caret in the units.
PIT_COLUMNS = {
    "pitid": "pit_id",
    "location": "location",
    "site": "site_name",
    "utmzone": "utm_zone",
    "latitudedeg": "latitude",
    "longitudedeg": "longitude",
    "densityameankgm3": "density_a_kg_m3",
    "densitybmeankgm3": "density_b_kg_m3",
    "densitymeankgm3": "density_kg_m3",
    "sweamm": "swe_a_mm",
    "swebmm": "swe_b_mm",
    "swemm": "swe_mm",
    "hscm": "depth_cm",
    "flag": "flag",
}


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())

#: Pit density and SWE columns use -9999 where no profile was collected.
PIT_FILL = -9999.0

SNEX_MCS = "SNEX_MCS_Lidar"
NIVAL_MCS = "NIVAL_MCS_Lidar"


@dataclass(frozen=True)
class SourceSpec:
    """One LiDAR product stream feeding a site.

    Exactly one of `site_code` (QSI naming convention) or `filename_contains`
    (everything else) selects granules from `short_name`.
    """

    product: str  # "DEM" | "SD" | "VH"
    short_name: str
    site_code: str | None = None
    filename_contains: str | None = None
    #: Native resolution of the source in metres, before resampling to 3 m.
    native_res_m: float = 3.0
    note: str = ""

    def __post_init__(self) -> None:
        if (self.site_code is None) == (self.filename_contains is None):
            raise ValueError(
                f"{self.short_name}/{self.product}: set exactly one of "
                "site_code or filename_contains"
            )


@dataclass(frozen=True)
class PointSpec:
    """One in-situ point dataset feeding a site.

    These are CSV transects, not rasters: ground-penetrating radar surveys
    carrying two-way travel time alongside a co-located lidar snow depth, from
    which snow density, SWE and relative permittivity are derived. They cannot
    become grid layers -- a transect covers a line, not an area -- so they are
    stored as tables and carry the row and column of the grid cell each point
    falls in, which is what lets a user join them to the rasters.

    Permittivity is the reason these are worth carrying at all: it is the term
    the InSAR phase-to-depth inversion needs and the one quantity every other
    site in this archive has to assume.
    """

    key: str                 # group name under insitu/
    short_name: str
    filename_contains: str
    #: "gpr" is a plain CSV of transect points. "pit_summary" is a NSIDC
    #: summary table: dozens of comment lines, then the header, then a row of
    #: bare column numbers that is not data and has to be dropped.
    layout: str = "gpr"
    note: str = ""


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    state: str
    #: EPSG we expect the reference raster to carry. Preflight assertion only --
    #: the real CRS is always read from the reference raster itself.
    expected_epsg: int
    sources: tuple[SourceSpec, ...]
    #: Which product supplies the common grid. README decision 2: the LiDAR DEM,
    #: except where no DEM exists yet.
    reference_product: str = "DEM"
    #: In-situ point observations. Optional; most sites have none.
    points: tuple[PointSpec, ...] = ()
    note: str = ""

    def source(self, product: str) -> SourceSpec | None:
        for s in self.sources:
            if s.product == product:
                return s
        return None


#: Both campaigns publish one campaign-wide summary; the ingest keeps only the
#: pits that land inside a given site, so the same pair serves every site.
PIT_SOURCES = (
    PointSpec(key="snow_pits_2020", short_name=TS_PITS_20,
              filename_contains="Summary_SWE", layout="pit_summary",
              note="SnowEx20 time-series snow pits, Oct 2019 to May 2020."),
    PointSpec(key="snow_pits_2021", short_name=TS_PITS_21,
              filename_contains="Summary_SWE", layout="pit_summary",
              note="SnowEx21 time-series snow pits, Nov 2020 to May 2021."),
)


def _qsi_site(key: str, name: str, state: str, epsg: int, code: str) -> Site:
    """The six sites fully covered by the standard QSI 3 m product suite."""
    return Site(
        key=key,
        name=name,
        state=state,
        expected_epsg=epsg,
        sources=(
            SourceSpec("DEM", QSI_DEM, site_code=code),
            SourceSpec("SD", QSI_SD, site_code=code),
            SourceSpec("VH", QSI_VH, site_code=code),
        ),
        points=PIT_SOURCES,
    )


SITES: dict[str, Site] = {
    s.key: s
    for s in [
        _qsi_site("banner_summit", "Banner Summit", "ID", 6340, "USIDBS"),
        Site(
            key="mores_creek",
            name="Mores Creek",
            state="ID",
            expected_epsg=6340,
            sources=(
                SourceSpec("DEM", QSI_DEM, site_code="USIDMC"),
                SourceSpec("SD", QSI_SD, site_code="USIDMC"),
                SourceSpec("VH", QSI_VH, site_code="USIDMC"),
                # NOT INGESTED, deliberately. Two further snow-depth series
                # cover this exact ground and are discovered by the search
                # above if enabled:
                #
                #   SNEX_MCS_Lidar   2023-02-09, 2025-01-29, 2025-05-01
                #   NIVAL_MCS_Lidar  2025-12-13, 2026-02-07, 2026-02-22,
                #                    2026-03-03, 2026-04-04
                #
                # They overlap the grid at 98% and 100% and are real snow
                # depth, but no UAVSAR exists for any of those dates: the
                # airborne campaign ended in 2021, confirmed independently by
                # a footprint search and a flight-line search. They would add
                # ~30 GB and zero trainable scenes. Re-enable them when a
                # spaceborne L-band mission covers Idaho.
            ),
            points=PIT_SOURCES,
            note="Snow depth from the QSI campaign (2020-2021). Further "
                 "surveys exist for 2022-2026 over the same ground but have "
                 "no matching radar; see the comment in sources.",
        ),
        _qsi_site("dry_creek", "Dry Creek", "ID", 6340, "USIDDC"),
        _qsi_site("fraser", "Fraser", "CO", 6342, "USCOFR"),
        Site(
            key="cameron_pass",
            name="Cameron Pass",
            state="CO",
            expected_epsg=6342,
            sources=(
                SourceSpec("DEM", QSI_DEM, site_code="USCOCP"),
                SourceSpec("SD", QSI_SD, site_code="USCOCP"),
                SourceSpec("VH", QSI_VH, site_code="USCOCP"),
            ),
            points=PIT_SOURCES + (
                PointSpec(
                    key="gpr_permittivity_2019_2021",
                    short_name=COCP_PERM,
                    filename_contains="SNEX20_COCP_SPD",
                    note="Cameron Pass GPR transects, Dec 2019 to May 2021, "
                         "spanning the 2021 lidar survey and the 2021 radar. "
                         "The second site in this archive with measured "
                         "permittivity, which is what makes it possible to "
                         "ask whether a density model transfers between sites.",
                ),
            ),
        ),
        _qsi_site("little_cottonwood", "Little Cottonwood Canyon", "UT", 6341, "USUTLC"),
        Site(
            key="grand_mesa",
            name="Grand Mesa",
            state="CO",
            # Measured from the HRSI DTM itself: EPSG:32612, WGS 84 / UTM
            # zone 12N. Grand Mesa straddles the zone 12/13 boundary at -108
            # deg and the product sits in zone 12, on WGS84 rather than the
            # NAD83 the QSI sites use.
            expected_epsg=32612,
            sources=(
                SourceSpec(
                    "DEM", HRSI_CO, filename_contains="GM_DTM_1m",
                    native_res_m=1.0,
                    note="LiDAR-derived snow-off reference DTM from the HRSI collection. "
                         "Resampled 1 m -> 3 m.",
                ),
                SourceSpec("SD", GM_LIDAR, filename_contains="SNEX20_GM_Lidar_SD_"),
                SourceSpec("VH", QSI_VH, site_code="USCOGM"),
                # SnowEx 2017. Five ASO surveys spanning February 2017, already
                # at 3 m. Two of them bracket a three-day interferogram at
                # one-day and same-day separation, which is the tightest
                # snow-depth-change constraint anywhere in the archive.
                # 1 m rasters over a 15.8 km2 IOP plot -- about 6% of this
                # site. Sparse by area, but the only measured density and SWE
                # in the archive, so worth carrying with honest coverage.
                SourceSpec(
                    "SWE", GM_SWE_SD, filename_contains="_SWE_20200201",
                    native_res_m=1.0,
                    note="Lidar + GPR derived SWE, SnowEx20 Grand Mesa IOP. "
                         "Covers an intensive study plot, not the whole site.",
                ),
                SourceSpec(
                    "DENSITY", GM_SWE_SD, filename_contains="_SnowDensity_20200201",
                    native_res_m=1.0,
                    note="Lidar + GPR derived bulk snow density, SnowEx20 "
                         "Grand Mesa IOP. Feeds the permittivity term that "
                         "every other site has to assume.",
                ),
                SourceSpec(
                    "SD", ASO_SD, filename_contains="ASO_3M_SD_USCOGM",
                    note="NASA/JPL Airborne Snow Observatory, SnowEx 2017 "
                         "campaign. Same site and grid as the 2020 products; "
                         "a different campaign three years earlier.",
                ),
            ),
            points=PIT_SOURCES + (
                PointSpec(
                    key="gpr_permittivity_2017",
                    short_name=GM_PERM,
                    filename_contains="SNEX17_SD_Perm",
                    note="Grand Mesa GPR transects, 8-25 Feb 2017 -- the same "
                         "fortnight as the five ASO snow-depth surveys and the "
                         "three-day interferogram, so depth, radar, geometry "
                         "and permittivity all come from one campaign.",
                ),
            ),
            note="DEM is the LiDAR-derived snow-off reference DTM from the HRSI "
                 "collection. Carries two "
                 "campaigns: SnowEx 2017 (ASO) and 2020. They cover the same "
                 "ground, so they must never be split across train and test.",
        ),
        Site(
            key="reynolds_creek",
            name="Reynolds Creek",
            state="ID",
            expected_epsg=6340,
            sources=(
                SourceSpec(
                    "DEM", ORNL_IDAHO, filename_contains="RCEW_DEM_1m",
                    native_res_m=1.0,
                    note="2014 lidar DEM from the ORNL DAAC Idaho vegetation-height "
                         "collection, six years before the 2020 snow survey. "
                         "Terrain is stable; recorded in identification/.",
                ),
                SourceSpec("VH", QSI_VH, site_code="USIDRC"),
            ),
            reference_product="DEM",
            note="No snow-depth product. DEM is from a different campaign and year "
                 "than the vegetation-height survey.",
        ),
    ]
}

#: Human-readable descriptions attached to every array in the HDF5 file.
DESCRIPTIONS = {
    "DEM": "Bare-earth ground surface elevation, metres above the vertical datum.",
    "SD": "Snow depth, metres, from differencing snow-on and snow-off lidar surfaces.",
    "VH": "Vegetation canopy height above ground, metres.",
    "SWE": "Snow water equivalent, millimetres of water. Derived from lidar "
           "snow depth and GPR two-way travel time, not modelled.",
    "DENSITY": "Bulk snow density, kg/m3. Derived from lidar snow depth and "
               "GPR two-way travel time. This is the measured input to the "
               "permittivity term in the InSAR phase-to-depth inversion, "
               "which is otherwise assumed.",
    "int": "Raw interferogram, complex64. Wrapped interferometric phase; the "
           "argument is phase in radians modulo 2*pi.",
    "unw": "Unwrapped interferometric phase, radians, float32. The usable "
           "surface-change signal between the two acquisition dates.",
    "cor": "Interferometric coherence, 0-1, float32. Per-pixel reliability of "
           "the phase measurement.",
    "hgt": "Ground elevation in the DEM used to project the radar imagery, "
           "metres, float32. This is the projection DEM, not elevation change.",
    "amp": "Radar backscatter amplitude, float32, linear amplitude units.",
    "amp1": "Radar backscatter amplitude of the first (reference) pass, "
            "float32, linear amplitude units.",
    "amp2": "Radar backscatter amplitude of the second (repeat) pass, "
            "float32, linear amplitude units.",
    "dem_tiff": "Projection DEM elevation used during UAVSAR processing, metres. "
                "A separate archive product from science/LIDAR/DEM.",
}


# Provider product definitions for the direct products reviewed in SNEX-018.
# These declare metres only; a height unit does not establish a vertical datum.
LIDAR_QUANTITY_ATTRS = {
    "DEM": {"quantity": "ground_surface_elevation", "units": "m"},
    "SD": {"quantity": "snow_depth", "units": "m"},
    "VH": {"quantity": "vegetation_height", "units": "m"},
}


def uavsar_quantity_attrs(subproduct: str, annotation: dict | None = None) -> dict:
    """Describe imported values without converting or certifying calibration.

    JPL's repeat-pass format and example annotation define these conventions.
    Preserve the consumed annotation declaration, and make an unfamiliar one
    explicitly unknown instead of silently assigning the default unit.
    """
    kind = "amp" if subproduct in ("amp1", "amp2") else subproduct
    kind = "hgt" if kind == "dem_tiff" else kind
    quantity, units, field, expected = {
        "amp": ("radar_backscatter_amplitude", "linear amplitude",
                "amplitude units", "linear amplitude"),
        "int": ("complex_interferogram", "linear power", "interferogram units",
                "linear power and phase in radians"),
        "unw": ("unwrapped_interferometric_phase", "rad",
                "unwrapped phase units", "radians"),
        "cor": ("interferometric_coherence", "1", "correlation units",
                "scalar between 0 and 1"),
        "hgt": ("projection_dem_elevation", "m", "dem units", "meters"),
    }[kind]
    raw = str((annotation or {}).get(field, {}).get("value", "")).strip()
    supported = not raw or " ".join(raw.lower().split()) == expected
    attrs = {
        "quantity": quantity,
        "units": units if supported else "unknown",
        "units_status": ("source_annotation" if raw else "provider_documentation_default")
                        if supported else "unrecognized_source_declaration",
        "source_units": raw,
        "source_units_annotation_key": field,
        "units_reference": "https://uavsar.jpl.nasa.gov/science/documents/rpi-format.html",
        "units_annotation_reference":
            "https://uavsar.jpl.nasa.gov/science/documents/example_rpi.ann.txt",
        "units_note": "Provider product units; no local unit conversion applied.",
    }
    if kind in ("amp", "int"):
        attrs.update(
            radiometric_reference="not_established",
            units_note=(f"UAVSAR product-native {units} convention. "
                        "No SI power unit or sigma0/beta0/gamma0 normalization is asserted. "
                        "The local importer performs no radiometric calibration or unit conversion."))
    if kind == "int":
        attrs.update(magnitude_quantity="interferogram_magnitude",
                     magnitude_units=units if supported else "unknown",
                     phase_quantity="wrapped_interferometric_phase",
                     phase_units="rad" if supported else "unknown",
                     complex_representation="real and imaginary components; phase is the complex argument")
    if kind == "cor":
        attrs["units_note"] = "Dimensionless correlation magnitude, with nominal range 0 to 1."
    if kind == "hgt":
        attrs["units_note"] = "Metres of projection DEM elevation; units alone do not establish the vertical datum."
    if not supported:
        attrs["units_note"] = ("Unrecognized source unit declaration retained in source_units. "
                               "Values were not converted; establish the convention before quantitative use.")
        attrs["description"] = (f"Imported UAVSAR {subproduct}. Source units require review; "
                                "see source_units and units_status.")
    return attrs


# =====================================================================
# SECTION 2 -- SMALL UTILITIES
# =====================================================================


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # earthaccess is chatty about DataGranule.size deprecation; harmless.
    # asf_search logs every subquery and its full options dict at INFO, which
    # buries our own output. Both stay visible at WARNING and above.
    for noisy in ("earthaccess", "asf_search", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def retry(fn, *, attempts: int = 5, base_delay: float = 2.0, what: str = "request",
          fatal: tuple[type[BaseException], ...] = ()):
    """Call `fn`, retrying with exponential backoff.

    ASF/CloudFront intermittently returns 503; CMR occasionally times out.

    Exceptions listed in `fatal` are re-raised immediately. Some failures are
    not transient and retrying them is actively harmful -- repeating a rejected
    password can get an Earthdata account rate-limited or locked.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except fatal:
            raise
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, we retry all
            last = exc
            if i == attempts - 1:
                break
            delay = base_delay * (2 ** i)
            log.warning("%s failed (%s: %s); retry %d/%d in %.0fs",
                        what, type(exc).__name__, exc, i + 1, attempts - 1, delay)
            time.sleep(delay)
    raise RuntimeError(f"{what} failed after {attempts} attempts") from last


def parse_iso_date(value: str | None) -> date | None:
    """Parse a CMR/ASF timestamp to a date.

    `properties.get('startTime')` can be present but None, so callers must not
    rely on dict-default behaviour.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", text)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_yyyymmdd(value: str) -> date | None:
    """Parse a fixed-width `YYYYMMDD` token.

    The width check is not redundant: `strptime` happily reads "2020021" as
    2020-02-01, so a truncated filename field would silently produce a plausible
    but wrong acquisition date.
    """
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


# =====================================================================
# SECTION 3 -- CMR DISCOVERY (no authentication required)
# =====================================================================


def footprint_from_umm(umm: dict):
    """Extract an EPSG:4326 footprint from a CMR UMM-G record.

    CMR returns either `GPolygons` or `BoundingRectangles` depending on the
    provider. Handling only one path silently drops granules from matching.
    """
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    geom = (
        umm.get("SpatialExtent", {})
        .get("HorizontalSpatialDomain", {})
        .get("Geometry", {})
    )
    parts: list[Any] = []

    for gp in geom.get("GPolygons", []) or []:
        pts = (gp.get("Boundary", {}) or {}).get("Points", []) or []
        coords = [(p["Longitude"], p["Latitude"]) for p in pts]
        if len(coords) >= 3:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                parts.append(poly)

    for br in geom.get("BoundingRectangles", []) or []:
        try:
            parts.append(
                box(
                    float(br["WestBoundingCoordinate"]),
                    float(br["SouthBoundingCoordinate"]),
                    float(br["EastBoundingCoordinate"]),
                    float(br["NorthBoundingCoordinate"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    if not parts:
        return None
    return parts[0] if len(parts) == 1 else unary_union(parts)


def parse_qsi_filename(filename: str) -> dict[str, Any] | None:
    """Parse `SNEX20_QSI_<PROD>_3M_<SITECODE>_<YYYYMMDD>_<YYYYMMDD>.tif`.

    Index 3 is the resolution token `3M` and index 4 is the geo code. An
    off-by-one here silently collapses every site into one bucket, so the
    resolution token is asserted rather than assumed.
    """
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) < 7 or parts[1] != "QSI":
        return None
    if parts[3].upper() != "3M":
        log.warning("unexpected resolution token %r in %s", parts[3], filename)
        return None
    d0, d1 = parse_yyyymmdd(parts[5]), parse_yyyymmdd(parts[6])
    if d0 is None:
        return None
    return {
        "product": parts[2].upper(),
        "site_code": parts[4].upper(),
        "date_begin": d0,
        "date_end": d1 or d0,
    }


@dataclass
class LidarGranule:
    site_key: str
    product: str            # DEM | SD | VH
    short_name: str
    filename: str
    url: str
    date_begin: date
    date_end: date
    footprint_wkt: str
    native_res_m: float
    note: str = ""

    @property
    def date_key(self) -> str:
        """HDF5 subgroup key. The first date of the acquisition range."""
        return self.date_begin.strftime("%Y%m%d")

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["date_begin"] = self.date_begin.isoformat()
        d["date_end"] = self.date_end.isoformat()
        d["date_key"] = self.date_key
        return d


def _search_cmr(short_name: str, cache: dict[str, list]) -> list:
    if short_name not in cache:
        import earthaccess

        log.debug("CMR search: %s", short_name)
        cache[short_name] = retry(
            lambda: earthaccess.search_data(short_name=short_name, count=2000),
            what=f"CMR search {short_name}",
        )
        log.debug("  %s -> %d granules", short_name, len(cache[short_name]))
    return cache[short_name]


def _granule_filename(granule) -> str:
    links = granule.data_links()
    if not links:
        return ""
    return links[0].rsplit("/", 1)[-1]


def _granule_url(granule) -> str:
    links = granule.data_links()
    return links[0] if links else ""


def discover_lidar(site: Site, cache: dict[str, list]) -> list[LidarGranule]:
    """Find every LiDAR granule belonging to one site."""
    found: list[LidarGranule] = []

    for spec in site.sources:
        for g in _search_cmr(spec.short_name, cache):
            filename = _granule_filename(g)
            if not filename:
                continue

            if spec.site_code is not None:
                parsed = parse_qsi_filename(filename)
                if parsed is None:
                    continue
                if parsed["site_code"] != spec.site_code:
                    continue
                if parsed["product"] != spec.product:
                    continue
                d0, d1 = parsed["date_begin"], parsed["date_end"]
            else:
                if spec.filename_contains not in filename:
                    continue
                tmp = g["umm"].get("TemporalExtent", {})
                rng = tmp.get("RangeDateTime", {})
                d0 = parse_iso_date(rng.get("BeginningDateTime")) or parse_iso_date(
                    tmp.get("SingleDateTime")
                )
                d1 = parse_iso_date(rng.get("EndingDateTime")) or d0
                if d0 is None:
                    log.warning("no usable date for %s; skipping", filename)
                    continue

            fp = footprint_from_umm(g["umm"])
            if fp is None or fp.is_empty:
                log.warning("no footprint for %s; skipping", filename)
                continue

            found.append(
                LidarGranule(
                    site_key=site.key,
                    product=spec.product,
                    short_name=spec.short_name,
                    filename=filename,
                    url=_granule_url(g),
                    date_begin=d0,
                    date_end=d1,
                    footprint_wkt=fp.wkt,
                    native_res_m=spec.native_res_m,
                    note=spec.note,
                )
            )

    found.sort(key=lambda x: (x.product, x.date_begin, x.filename))
    return found


# =====================================================================
# SECTION 4 -- UAVSAR SEARCH AND GEOMETRIC MATCHING
#
# Matching is geometric, never by name. The documented campaign name for
# the Idaho sites, "Lowman, ID", returns zero results -- the registered
# ASF name is "Lowman, CO" (upstream mislabel). Name-based search
# silently missed three of eight sites.
# =====================================================================


@dataclass
class UavsarProduct:
    scene_name: str
    file_id: str
    level: str
    url: str
    filename: str
    bytes_: int
    #: ASF-published MD5 of the archive, used to catch a corrupt download that
    #: happens to have the right length.
    md5sum: str
    date_ref: date | None
    date_sec: date | None
    footprint_wkt: str

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["date_ref"] = self.date_ref.isoformat() if self.date_ref else None
        d["date_sec"] = self.date_sec.isoformat() if self.date_sec else None
        return d

    @property
    def dates(self) -> list[date]:
        return [d for d in (self.date_ref, self.date_sec) if d is not None]


def site_search_polygon(granules: Sequence[LidarGranule]):
    """Convex hull of a site's LiDAR footprints, wound counter-clockwise.

    The hull deliberately over-selects from ASF; each candidate is then tested
    against the precise per-granule footprint locally.
    """
    from shapely import wkt as shapely_wkt
    from shapely.geometry.polygon import orient
    from shapely.ops import unary_union

    union = unary_union([shapely_wkt.loads(g.footprint_wkt) for g in granules])
    return orient(union.convex_hull, sign=1.0)


def search_uavsar(search_poly) -> list[UavsarProduct]:
    """All UAVSAR products of the levels we keep, intersecting `search_poly`."""
    import asf_search as asf
    from shapely.geometry import shape

    results = retry(
        lambda: asf.geo_search(intersectsWith=search_poly.wkt, platform="UAVSAR"),
        what="ASF geo_search",
    )

    products: list[UavsarProduct] = []
    for r in results:
        p = r.properties
        level = p.get("processingLevel")
        if level not in UAVSAR_LEVELS:
            continue
        try:
            geom = shape(r.geometry)
        except Exception:  # noqa: BLE001
            log.warning("unparseable ASF geometry for %s", p.get("fileID"))
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)

        products.append(
            UavsarProduct(
                scene_name=p.get("sceneName") or "",
                file_id=p.get("fileID") or "",
                level=level,
                url=p.get("url") or "",
                filename=p.get("fileName") or "",
                bytes_=int(p.get("bytes") or 0),
                md5sum=str(p.get("md5sum") or ""),
                # startTime/stopTime are the two interferogram acquisition dates.
                date_ref=parse_iso_date(p.get("startTime") or ""),
                date_sec=parse_iso_date(p.get("stopTime") or ""),
                footprint_wkt=geom.wkt,
            )
        )

    products.sort(key=lambda x: (x.scene_name, x.level))
    return products


def gap_days(lidar: LidarGranule, product: UavsarProduct) -> int | None:
    """Smallest whole-day separation between the LiDAR and UAVSAR acquisitions.

    The LiDAR acquisition can span days; so can the interferogram pair. The gap
    is the closest approach between the two intervals.
    """
    if not product.dates:
        return None
    best: int | None = None
    for ld in (lidar.date_begin, lidar.date_end):
        for ud in product.dates:
            d = abs((ld - ud).days)
            best = d if best is None else min(best, d)
    return best


@dataclass
class Match:
    site_key: str
    lidar_product_type: str
    lidar_filename: str
    lidar_date: str
    uavsar_scene: str
    uavsar_level: str
    uavsar_dates: str
    gap_days: int
    verdict: str
    group_name: str


def flight_token(scene_name: str) -> str:
    """`UA_lowman_05208_21019-019_...` -> `LOWMAN05208`.

    The campaign name alone is not unique: Grand Mesa is overflown by lines
    `grmesa_09305` and `grmesa_27416`, and on a single-date PolSAR pass both
    produce the same date pair, so a campaign-only token collides and one
    acquisition silently overwrites the other. The line number disambiguates
    them while keeping the name readable. JPL's full product id is kept as an
    attribute regardless.
    """
    parts = scene_name.split("_")
    if len(parts) < 2 or not parts[1]:
        return "UNKNOWN"
    campaign = re.sub(r"[^A-Z0-9]", "", parts[1].upper())
    line = re.sub(r"[^A-Z0-9]", "", parts[2].upper()) if len(parts) > 2 else ""
    return f"{campaign}{line}" if line else campaign


def scene_version(scene_name: str) -> int:
    """Trailing processing version of a UAVSAR scene name, e.g. `..._01` -> 1.

    JPL reprocesses scenes and publishes the result alongside the original:
    `UA_grmesa_07805_17002-000_17016-000_0016d_s01_L090_01` and `..._02` are
    the same two passes, processed twice. They are not two acquisitions, so
    only the newest is ingested; keeping both would store the same ground twice
    under names that differ only by a digit.
    """
    m = re.search(r"_(\d+)$", scene_name.strip())
    return int(m.group(1)) if m else 0


def newest_versions(products: list) -> list:
    """Drop superseded reprocessings, keeping the highest version of each.

    Keyed on everything except the version suffix, so it only ever collapses
    genuine reprocessings of one scene and never two distinct acquisitions.
    """
    best: dict[tuple[str, str], object] = {}
    for p in products:
        stem = re.sub(r"_\d+$", "", p.scene_name.strip())
        key = (p.level, stem)
        cur = best.get(key)
        if cur is None or scene_version(p.scene_name) > scene_version(cur.scene_name):
            best[key] = p
    dropped = len(products) - len(best)
    if dropped:
        log.info("    %d superseded reprocessing(s) skipped in favour of a "
                 "newer version", dropped)
    return sorted(best.values(), key=lambda p: p.scene_name)


def group_name_for(site: Site, product: UavsarProduct) -> str:
    """e.g. BANNERSUMMIT_CUTFROMLOWMAN_20200211_20200218."""
    site_token = re.sub(r"[^A-Z0-9]", "", site.name.upper())
    dates = "_".join(d.strftime("%Y%m%d") for d in product.dates) or "UNDATED"
    return f"{site_token}_CUTFROM{flight_token(product.scene_name)}_{dates}"


def match_site(
    site: Site, granules: Sequence[LidarGranule], products: Sequence[UavsarProduct]
) -> tuple[list[Match], list[UavsarProduct]]:
    """Pair each snow-season LiDAR granule with intersecting UAVSAR flights.

    Returns the match rows and the deduplicated list of UAVSAR products this
    site actually needs.
    """
    from shapely import wkt as shapely_wkt

    matches: list[Match] = []
    needed: dict[str, UavsarProduct] = {}

    for g in granules:
        if g.product == "DEM":
            # The DEM is the snow-off survey; UAVSAR coverage is winter/spring.
            # No match is the correct outcome, not a gap.
            matches.append(
                Match(
                    site_key=site.key,
                    lidar_product_type="DEM",
                    lidar_filename=g.filename,
                    lidar_date=g.date_key,
                    uavsar_scene="",
                    uavsar_level="",
                    uavsar_dates="",
                    gap_days=-1,
                    verdict="snow_off_no_match_expected",
                    group_name="",
                )
            )
            continue

        gfp = shapely_wkt.loads(g.footprint_wkt)
        for p in products:
            gap = gap_days(g, p)
            if gap is None or gap > MATCH_WINDOW_DAYS:
                continue
            if not shapely_wkt.loads(p.footprint_wkt).intersects(gfp):
                continue
            matches.append(
                Match(
                    site_key=site.key,
                    lidar_product_type=g.product,
                    lidar_filename=g.filename,
                    lidar_date=g.date_key,
                    uavsar_scene=p.scene_name,
                    uavsar_level=p.level,
                    uavsar_dates="_".join(d.strftime("%Y%m%d") for d in p.dates),
                    gap_days=gap,
                    verdict="match",
                    group_name=group_name_for(site, p),
                )
            )
            needed[p.file_id] = p

    matches.sort(key=lambda m: (m.lidar_product_type, m.lidar_date, m.uavsar_scene))
    products = sorted(needed.values(), key=lambda p: (p.scene_name, p.level))

    # Two distinct acquisitions must never share an HDF5 group name, or one
    # silently overwrites the other. This actually happened: the flight token
    # used to omit the line number, so grmesa_09305 and grmesa_27416 collided.
    products = newest_versions(products)

    claimed: dict[tuple[str, str], str] = {}
    for p in products:
        key = (p.level, group_name_for(site, p))
        if key in claimed and claimed[key] != p.scene_name:
            log.error(
                "%s: group name %s is claimed by two different scenes (%s and "
                "%s). One would overwrite the other.",
                site.key, key[1], claimed[key], p.scene_name,
            )
        claimed[key] = p.scene_name

    return matches, products


# =====================================================================
# SECTION 5 -- THE COMMON GRID
#
# Every array in a site's file sits on one identical pixel grid. CRS,
# pixel size and pixel phase come from the site's reference LiDAR DEM;
# the extent comes from GRID_EXTENT_RULE, because the source footprints
# genuinely disagree with one another:
#
#   * snow depth is always inside the DEM footprint, but the 2021
#     vegetation-height granules overhang it by 23-31% at Banner Summit,
#     Fraser and Little Cottonwood;
#   * at Grand Mesa and Reynolds Creek the reference DEM is far larger
#     than the science data (9.9x and 3.4x by area), so the DEM extent
#     would produce a mostly empty grid.
#
# "dem_intersect_science" keeps every snow-depth pixel, trims the VH
# overhang, and bounds the two oversized DEMs to where science exists.
# =====================================================================

#: How the grid extent is chosen. See the discussion above.
#:   dem_intersect_science  reference DEM footprint AND science footprints
#:   dem                    reference DEM footprint alone (literal README)
#:   science                union of SD/VH footprints alone
GRID_EXTENT_RULE = "dem_intersect_science"

#: Whether CLIP_BUFFER_M is baked into the grid itself. False means the buffer
#: is used only while reprojecting -- source data is read from a wider window so
#: resampling kernels never run off the edge -- and then trimmed away, so no
#: array in the finished file carries an empty border. True keeps the ring,
#: giving a CNN radar context beyond the LiDAR coverage at the cost of a band
#: where every LiDAR array is nodata.
GRID_INCLUDES_BUFFER = False


@dataclass(frozen=True)
class CommonGrid:
    """The single pixel grid shared by every array in one site's file."""

    crs_wkt: str
    epsg: int | None
    #: Affine coefficients (a, b, c, d, e, f) as rasterio orders them.
    transform: tuple[float, float, float, float, float, float]
    width: int
    height: int
    res_m: float

    @property
    def affine(self):
        from rasterio.transform import Affine

        return Affine(*self.transform)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        a = self.affine
        left, top = a.c, a.f
        return (left, top + a.e * self.height, left + a.a * self.width, top)

    def to_attrs(self) -> dict[str, Any]:
        return {
            "common_crs_wkt": self.crs_wkt,
            "common_crs_epsg": self.epsg if self.epsg is not None else -1,
            "common_grid_transform": list(self.transform),
            "common_grid_shape": [self.height, self.width],
            "common_grid_bounds": list(self.bounds),
            "common_grid_resolution_m": self.res_m,
        }


def snap_bounds(
    bounds: tuple[float, float, float, float],
    origin_x: float,
    origin_y: float,
    res: float,
) -> tuple[float, float, float, float]:
    """Expand `bounds` outward to the nearest pixel edges of a grid.

    The grid is defined by an origin and a pixel size; snapping outward
    guarantees the result contains the input and that every site's arrays land
    on exactly the same pixel centres as the reference raster.
    """
    import math

    left, bottom, right, top = bounds
    if res <= 0:
        raise ValueError(f"resolution must be positive, got {res}")
    if not (left < right and bottom < top):
        raise ValueError(f"degenerate bounds: {bounds}")

    snapped_left = origin_x + math.floor((left - origin_x) / res) * res
    snapped_right = origin_x + math.ceil((right - origin_x) / res) * res
    snapped_top = origin_y - math.floor((origin_y - top) / res) * res
    snapped_bottom = origin_y - math.ceil((origin_y - bottom) / res) * res
    return (snapped_left, snapped_bottom, snapped_right, snapped_top)


def derive_common_grid(
    ref_crs, ref_transform, target_bounds: tuple[float, float, float, float],
    res: float = TARGET_RES_M,
) -> CommonGrid:
    """Build the common grid from a reference raster and a target extent.

    `target_bounds` must already be expressed in `ref_crs`.
    """
    from rasterio.crs import CRS
    from rasterio.transform import Affine

    crs = CRS.from_user_input(ref_crs)
    if ref_transform.e >= 0:
        raise ValueError(
            "reference raster is not north-up (transform.e >= 0); the grid "
            "derivation assumes a north-up, axis-aligned reference"
        )
    if ref_transform.b or ref_transform.d:
        raise ValueError("reference raster transform is rotated; not supported")

    left, bottom, right, top = snap_bounds(
        target_bounds, ref_transform.c, ref_transform.f, res
    )
    width = int(round((right - left) / res))
    height = int(round((top - bottom) / res))
    if width <= 0 or height <= 0:
        raise ValueError(f"empty grid from bounds {target_bounds}")

    return CommonGrid(
        crs_wkt=crs.to_wkt(),
        epsg=crs.to_epsg(),
        transform=tuple(Affine(res, 0.0, left, 0.0, -res, top))[:6],
        width=width,
        height=height,
        res_m=res,
    )


def grid_target_bounds(
    dem_bounds: tuple[float, float, float, float] | None,
    science_bounds: tuple[float, float, float, float] | None,
    rule: str = GRID_EXTENT_RULE,
    buffer_m: float = 0.0,
) -> tuple[float, float, float, float]:
    """Combine the reference and science extents according to `rule`.

    Both inputs must be in the same CRS. Returns the extent before snapping.
    """
    if rule == "dem":
        chosen = dem_bounds
    elif rule == "science":
        chosen = science_bounds
    elif rule == "dem_intersect_science":
        if dem_bounds is None:
            chosen = science_bounds
        elif science_bounds is None:
            chosen = dem_bounds
        else:
            chosen = (
                max(dem_bounds[0], science_bounds[0]),
                max(dem_bounds[1], science_bounds[1]),
                min(dem_bounds[2], science_bounds[2]),
                min(dem_bounds[3], science_bounds[3]),
            )
            if chosen[0] >= chosen[2] or chosen[1] >= chosen[3]:
                raise ValueError(
                    "reference DEM and science footprints do not overlap; "
                    f"dem={dem_bounds} science={science_bounds}"
                )
    else:
        raise ValueError(f"unknown grid extent rule {rule!r}")

    if chosen is None:
        raise ValueError("no extent available: both dem and science bounds are None")
    if buffer_m:
        chosen = (chosen[0] - buffer_m, chosen[1] - buffer_m,
                  chosen[2] + buffer_m, chosen[3] + buffer_m)
    return chosen


def transform_bounds_to(src_crs, dst_crs, bounds, densify: int = 21):
    """Reproject a bounding box, densifying the edges.

    Corner-only transformation understates the extent whenever the projection
    curves the edges, which silently clips data along the top and bottom of a
    UTM tile.
    """
    from rasterio.warp import transform_bounds

    return transform_bounds(src_crs, dst_crs, *bounds, densify_pts=densify)


# =====================================================================
# SECTION 6 -- RASTER READING AND RESAMPLING
# =====================================================================


def _resampling(method: str):
    from rasterio.enums import Resampling

    try:
        return getattr(Resampling, method)
    except AttributeError as exc:
        raise ValueError(f"unknown resampling method {method!r}") from exc


def nodata_for(dtype) -> Any:
    """Fill value for a given dtype.

    NaN for anything floating or complex, so unmeasured pixels can never be
    confused with a real value of zero. Integer arrays have no such sentinel,
    so they are promoted to float32 on read instead.
    """
    import numpy as np

    kind = np.dtype(dtype).kind
    if kind == "f":
        return np.nan
    if kind == "c":
        return complex(np.nan, np.nan)
    raise TypeError(f"no NaN sentinel for dtype {dtype}; promote to float first")


def reproject_array(
    src_array,
    src_crs,
    src_transform,
    grid: CommonGrid,
    method: str,
    src_nodata: Any = None,
):
    """Reproject one array onto the common grid.

    Complex input is warped as separate real and imaginary planes. With nearest
    neighbour that is exactly equivalent to warping the complex array -- the
    resampler only ever selects a source pixel, it never combines two -- and it
    avoids depending on GDAL's complex-warp support. Any method other than
    nearest is rejected for complex input, because averaging wrapped phase
    across the 2*pi boundary produces meaningless values.
    """
    import numpy as np
    from rasterio.warp import reproject

    src = np.asarray(src_array)

    if np.iscomplexobj(src):
        if method != "nearest":
            raise ValueError(
                f"refusing to resample complex wrapped phase with {method!r}; "
                "only nearest neighbour preserves the 2*pi wrap boundary"
            )
        real = reproject_array(src.real.astype("float32"), src_crs, src_transform,
                               grid, method, src_nodata)
        imag = reproject_array(src.imag.astype("float32"), src_crs, src_transform,
                               grid, method, src_nodata)
        return (real + 1j * imag).astype("complex64")

    if src.dtype.kind != "f":
        src = src.astype("float32")

    dst = np.full(grid.shape, np.nan, dtype=src.dtype)
    reproject(
        source=src,
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=grid.affine,
        dst_crs=grid.crs_wkt,
        dst_nodata=np.nan,
        resampling=_resampling(method),
    )
    return dst


def source_window(
    grid: CommonGrid, src_crs, src_transform, src_shape: tuple[int, int],
    margin_px: int = 8,
):
    """The source-raster window covering the common grid, plus a margin.

    Reading only this window turns a whole-scene read into a small windowed one:
    a UAVSAR scene is ~16000 x 25000 px while a site needs roughly 2% of it.
    The margin keeps resampling kernels away from the window edge.

    Returns `(row_off, col_off, n_rows, n_cols)`, clamped to the raster, or
    None when the grid does not overlap the source at all.
    """
    import math

    src_bounds = transform_bounds_to(grid.crs_wkt, src_crs, grid.bounds)
    left, bottom, right, top = src_bounds

    inv = ~src_transform
    cols_rows = [inv * (x, y) for x, y in
                 ((left, top), (right, top), (left, bottom), (right, bottom))]
    cols = [c for c, _ in cols_rows]
    rows = [r for _, r in cols_rows]

    col_off = int(math.floor(min(cols))) - margin_px
    row_off = int(math.floor(min(rows))) - margin_px
    col_end = int(math.ceil(max(cols))) + margin_px
    row_end = int(math.ceil(max(rows))) + margin_px

    height, width = src_shape
    col_off, row_off = max(0, col_off), max(0, row_off)
    col_end, row_end = min(width, col_end), min(height, row_end)
    if col_end <= col_off or row_end <= row_off:
        return None
    return (row_off, col_off, row_end - row_off, col_end - col_off)


#: Fill sentinel used by the SnowEx QSI GeoTIFFs. The DEM declares it; the
#: snow-depth and vegetation-height products do not declare anything at all.
FLOAT32_MIN_SENTINEL = -3.4028234663852886e38


def normalise_fill(data, declared_nodata):
    """Convert every flavour of "no data" in a source array into NaN.

    Measured on the real products: the QSI DEM declares -3.4028235e38, while
    the QSI snow-depth and vegetation-height rasters declare **no nodata at
    all** yet are full of NaN. Trusting the declaration alone would let the
    fill value through as if it were a real measurement -- a snow depth of
    -3.4e38, or worse, a bilinear average of it and a genuine value.

    Grand Mesa's snow depth is the opposite case: it declares 0.0, which is
    also a physically meaningful depth. The declaration is honoured, because
    that is the publisher's stated encoding, but it means true bare ground is
    not distinguishable from outside-the-survey there.

    Returns `(float array with NaN fill, np.nan)`.
    """
    import numpy as np

    array = np.asarray(data)
    if array.dtype.kind not in "fc":
        array = array.astype("float32")
    else:
        array = array.astype(array.dtype, copy=True)

    if declared_nodata is not None and not (
        isinstance(declared_nodata, float) and np.isnan(declared_nodata)
    ):
        array[array == declared_nodata] = np.nan

    # Undeclared sentinels. NaN is already NaN; the float32 minimum is not.
    array[array == FLOAT32_MIN_SENTINEL] = np.nan
    return array, np.nan


def read_windowed(src, grid: CommonGrid, band: int = 1, margin_px: int = 8):
    """Read only the part of an open rasterio dataset the grid needs.

    Returns `(array, transform, nodata)`, or None when there is no overlap.
    The array comes back with every fill flavour already turned into NaN.
    """
    from rasterio.windows import Window

    win = source_window(grid, src.crs, src.transform, (src.height, src.width),
                        margin_px)
    if win is None:
        return None
    row_off, col_off, n_rows, n_cols = win
    window = Window(col_off, row_off, n_cols, n_rows)
    data = src.read(band, window=window)
    data, nodata = normalise_fill(data, src.nodata)
    return data, src.window_transform(window), nodata


# --- UAVSAR ground-projected binary (.grd) ---------------------------------
#
# A .grd is a headerless row-major binary array; its georeferencing lives in
# the companion .ann text file. Reading it directly, rather than converting the
# whole scene to GeoTIFF first, avoids writing ~25 GB of intermediates per
# acquisition for data that is 98% outside the site.


def read_annotation(path: Path) -> dict[str, Any]:
    """Parse a UAVSAR `.ann` file.

    Lines look like `DEM Original Pixel spacing (arcsec) = 1 ; comment`, read
    as `key (units) = value`. Keys are lowercased; numeric values are cast.
    """
    data: dict[str, Any] = {}
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        head = line.split(";", 1)[0]
        if "=" not in head:
            continue
        name, _, value = head.partition("=")
        key = name.split("(")[0].strip().lower()
        if not key:
            continue
        units = None
        if "(" in name and ")" in name:
            units = name[name.index("(") + 1:name.rindex(")")].strip() or None
        value = value.strip()
        cast: Any = value
        stripped = value.strip("-")
        if stripped.replace(".", "", 1).isdigit():
            cast = float(value) if "." in value else int(value)
        data[key] = {"value": cast, "units": units}
    return data


#: Which annotation key prefix describes which `.grd` sub-product. `.int` is
#: the phase product and is described by `grd_phs`; the rest share `grd`.
ANN_PREFIX = {"int": "grd_phs", "unw": "grd", "cor": "grd", "hgt": "grd",
              "amp": "grd", "amp1": "grd", "amp2": "grd"}

#: On-disk dtype of each `.grd` sub-product.
GRD_DTYPE = {"int": "complex64", "unw": "float32", "cor": "float32",
             "hgt": "float32", "amp": "float32", "amp1": "float32",
             "amp2": "float32"}


@dataclass(frozen=True)
class GrdLayout:
    """Geometry of a ground-projected UAVSAR binary, read from its `.ann`."""

    rows: int
    cols: int
    lat0: float      # centre latitude of the first row
    lon0: float      # centre longitude of the first column
    dlat: float      # latitude step, negative (north-up)
    dlon: float      # longitude step, positive
    dtype: str

    @property
    def transform(self):
        """Affine transform in EPSG:4326.

        `.ann` addresses are pixel centres; a rasterio transform describes the
        upper-left corner, so both origins shift by half a pixel.
        """
        from rasterio.transform import Affine

        return Affine(self.dlon, 0.0, self.lon0 - self.dlon / 2.0,
                      0.0, self.dlat, self.lat0 - self.dlat / 2.0)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    @property
    def itemsize(self) -> int:
        import numpy as np

        return np.dtype(self.dtype).itemsize


def grd_layout(ann: dict[str, Any], subproduct: str) -> GrdLayout:
    """Pull one sub-product's geometry out of a parsed annotation."""
    prefix = ANN_PREFIX.get(subproduct)
    if prefix is None:
        raise ValueError(f"unknown UAVSAR sub-product {subproduct!r}")

    def need(field: str) -> Any:
        key = f"{prefix}.{field}"
        if key not in ann:
            raise KeyError(f"annotation is missing {key!r}")
        return ann[key]["value"]

    return GrdLayout(
        rows=int(need("set_rows")),
        cols=int(need("set_cols")),
        lat0=float(need("row_addr")),
        lon0=float(need("col_addr")),
        dlat=float(need("row_mult")),
        dlon=float(need("col_mult")),
        dtype=GRD_DTYPE[subproduct],
    )


def reconcile_grd_shape(member_bytes: int, layout: GrdLayout
                        ) -> tuple[GrdLayout | None, str | None]:
    """Reconcile an annotation whose dimensions disagree with the binary.

    Measured on scene `lowman_23205_20007-003_20011-003`: the annotation says
    16045 x 24939 while the file is exactly 16046 x 24941 float32. The
    annotation under-reports both dimensions.

    The binary is treated as authoritative because its length is a hard fact
    while the annotation is a description of it, and because the georeferencing
    keys -- `row_addr`, `col_addr`, `row_mult`, `col_mult` -- describe the first
    pixel and the step, not the extent. They stay correct however many rows and
    columns there turn out to be; the grid simply reaches slightly further.

    A correction is only accepted when some shape within a few rows and columns
    of the annotation divides the file length exactly. That makes it a
    reconciliation, not a guess: reading the annotated shape out of a file with
    a different row length would shear every row progressively out of
    alignment, producing plausible-looking nonsense.

    Returns `(layout, note)`; `(None, reason)` when nothing sensible fits.
    """
    from dataclasses import replace

    expected = layout.rows * layout.cols * layout.itemsize
    if member_bytes == expected:
        return layout, None
    if member_bytes % layout.itemsize:
        return None, (f"{member_bytes} bytes is not a whole number of "
                      f"{layout.dtype} samples")

    samples = member_bytes // layout.itemsize
    best = None
    for rows in range(max(1, layout.rows - 4), layout.rows + 5):
        if samples % rows:
            continue
        cols = samples // rows
        if abs(cols - layout.cols) > 8:
            continue
        drift = abs(rows - layout.rows) + abs(cols - layout.cols)
        if best is None or drift < best[0]:
            best = (drift, rows, cols)
    if best is None:
        return None, (f"no shape near {layout.rows}x{layout.cols} divides "
                      f"{member_bytes} bytes exactly")
    _, rows, cols = best
    return (replace(layout, rows=rows, cols=cols),
            f"annotation says {layout.rows}x{layout.cols}, file is {rows}x{cols}")


def read_grd_window(path: Path, layout: GrdLayout, grid: CommonGrid,
                    margin_px: int = 8):
    """Memory-map a `.grd` and read only the block the common grid needs.

    Returns `(array, transform)` in EPSG:4326, or None when there is no overlap.
    """
    import numpy as np

    expected = layout.rows * layout.cols * layout.itemsize
    actual = Path(path).stat().st_size
    if actual != expected:
        raise ValueError(
            f"{Path(path).name}: expected {expected} bytes for "
            f"{layout.rows}x{layout.cols} {layout.dtype}, found {actual}. "
            "The annotation and the binary disagree."
        )

    win = source_window(grid, "EPSG:4326", layout.transform, layout.shape,
                        margin_px)
    if win is None:
        return None
    row_off, col_off, n_rows, n_cols = win

    mm = np.memmap(path, dtype=layout.dtype, mode="r", shape=layout.shape)
    block = np.array(mm[row_off:row_off + n_rows, col_off:col_off + n_cols])
    del mm

    from rasterio.windows import Window, transform as window_transform

    win_tf = window_transform(Window(col_off, row_off, n_cols, n_rows),
                              layout.transform)
    return block, win_tf


def parse_grd_member(name: str) -> tuple[str, str] | None:
    """`..._s01_L090HH_01.int.grd` -> `("HH", "int")`.

    Returns None for anything that is not a ground-projected binary.
    """
    stem = Path(name).name
    if not stem.endswith(".grd"):
        return None
    parts = stem.split(".")
    if len(parts) < 3:
        return None
    subproduct = parts[-2].lower()
    m = re.search(r"_L090([A-Z]{2})_", stem)
    if not m:
        return None
    return m.group(1), subproduct


def read_grd_window_streaming(fileobj, layout: GrdLayout, win):
    """Read one window from a `.grd` by streaming, never storing the whole array.

    The `.grd` members inside a UAVSAR zip are deflate-compressed, so they
    cannot be memory-mapped in place and random access means decompressing from
    the start regardless. Rather than extract a whole array to disk -- 1.6 GB
    for a single sub-product of a large scene, to read about 2% of it -- the
    stream is walked row by row and only the wanted rows are kept.
    """
    import numpy as np

    row_off, col_off, n_rows, n_cols = win
    itemsize = layout.itemsize
    row_bytes = layout.cols * itemsize
    out = np.empty((n_rows, n_cols), dtype=layout.dtype)

    def consume(n_bytes: int) -> None:
        """Advance the stream without keeping the data."""
        remaining = n_bytes
        while remaining > 0:
            chunk = fileobj.read(min(remaining, 8 << 20))
            if not chunk:
                raise ValueError("unexpected end of .grd while skipping")
            remaining -= len(chunk)

    consume(row_off * row_bytes)

    for i in range(n_rows):
        buf = bytearray(row_bytes)
        view = memoryview(buf)
        filled = 0
        while filled < row_bytes:
            chunk = fileobj.read(row_bytes - filled)
            if not chunk:
                raise ValueError(
                    f"unexpected end of .grd at row {row_off + i} of "
                    f"{layout.rows}"
                )
            view[filled:filled + len(chunk)] = chunk
            filled += len(chunk)
        row = np.frombuffer(bytes(buf), dtype=layout.dtype, count=layout.cols)
        out[i] = row[col_off:col_off + n_cols]

    return out


def read_grd_from_zip(zip_path: Path, member: str, layout: GrdLayout,
                      grid: CommonGrid, margin_px: int = 8):
    """Read the part of a zipped `.grd` that the common grid needs.

    Returns `(array, transform)` in EPSG:4326, or None when there is no overlap.
    """
    import zipfile

    win = source_window(grid, "EPSG:4326", layout.transform, layout.shape,
                        margin_px)
    if win is None:
        return None
    row_off, col_off, n_rows, n_cols = win

    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo(member)
        fixed, note = reconcile_grd_shape(info.file_size, layout)
        if fixed is None:
            raise ValueError(f"{Path(member).name}: {note}")
        if note:
            log.warning("    %s: %s -- trusting the file",
                        Path(member).name, note)
            layout = fixed
            # The window was computed from the annotated shape; redo it against
            # the corrected one so the crop lands where it should.
            win = source_window(grid, "EPSG:4326", layout.transform,
                                layout.shape, margin_px)
            if win is None:
                return None
            row_off, col_off, n_rows, n_cols = win
        with zf.open(member) as fh:
            block = read_grd_window_streaming(fh, layout, win)

    from rasterio.windows import Window, transform as window_transform

    win_tf = window_transform(Window(col_off, row_off, n_cols, n_rows),
                              layout.transform)
    return block, win_tf


# =====================================================================
# SECTION 6b -- ARTIFACT REMOVAL AND GAP FILLING
#
# The published products carry three distinct problems that need three
# different answers, and conflating them damages the data:
#
#   1. Undeclared fill sentinels. The Idaho UAVSAR DEM_TIFFs contain
#      exactly -10000 with no nodata tag; Grand Mesa's equivalent has
#      nothing below -500 and agrees with its InSAR hgt to 0.01 m, so
#      this is fill, not terrain. Removed unconditionally.
#
#   2. Gross outliers. Measured at Banner Summit: 334 snow-depth cells
#      below -5 m out of 18.5 million (0.002%), and deviations up to
#      31.6 m from their own neighbours. Instrument and processing
#      artifacts. Removed.
#
#   3. Small negative snow depths, which are NOT errors. Depth is the
#      difference of two lidar surfaces each carrying ~0.1 m vertical
#      uncertainty, so where the true depth is near zero the difference
#      scatters symmetrically about it: 74,000 Banner Summit cells fall
#      between -0.25 m and 0, 82% of all negatives there. Discarding
#      them biases every low-snow area upward and teaches a model that
#      bare ground carries snow. They are kept.
# =====================================================================

#: Fill values that appear in the source products without being declared.
FILL_SENTINELS = (-3.4028234663852886e38, -10000.0, -9999.0)

#: Heuristic range per array kind. The -1 m snow-depth lower bound allows
#: small negatives in this builder stage; later enrichment treats them
#: differently. Passing this screen does not establish measurement validity.
PLAUSIBLE_RANGE = {
    "DEM": (-500.0, 9000.0),
    "SD": (-1.0, 20.0),
    "VH": (-1.0, 120.0),
    # SWE in millimetres of water; 5 m of water would be an extraordinary
    # snowpack and is far above anything these campaigns measured.
    "SWE": (0.0, 5000.0),
    # Snow density in kg/m3. Fresh snow runs near 50, deep firn near 600;
    # anything at or beyond the density of water is not snow.
    "DENSITY": (0.0, 1000.0),
    "dem_tiff": (-500.0, 9000.0),
    "hgt": (-500.0, 9000.0),
    "cor": (0.0, 1.0),
    "unw": (-1e4, 1e4),
}

#: Maximum deviation from the median of four finite cardinal neighbours
#: before the interior cell is treated as a spike; this is a heuristic screen.
SPIKE_TOLERANCE = {
    "DEM": 25.0, "dem_tiff": 25.0, "hgt": 25.0,
    "SD": 10.0, "VH": 30.0, "cor": 0.9,
}

#: Maximum number of neighbour-based gap-filling passes (legacy variable
#: name retained). The routine does not measure or bound connected hole area.
MAX_GAP_PX = 2


def clean_array(data, kind: str, fill_gaps: bool = True):
    """Strip artifacts from one array and close pinhole gaps.

    Returns `(array, stats)`. Scalar removals become NaN; optional neighbour
    interpolation can subsequently fill missing cells, including removed ones.
    These heuristic screens do not establish whether a value is an artifact.
    Complex arrays bypass this routine. No boundary clipping occurs here.
    """
    import numpy as np

    a = np.array(data, dtype="float64" if data.dtype.kind != "c" else data.dtype)
    stats = {"sentinels": 0, "out_of_range": 0, "spikes": 0,
             "gaps_filled": 0, "removed_total": 0}
    if np.iscomplexobj(a):
        return data, stats          # wrapped phase has no meaningful range

    before = np.isfinite(a)

    for sentinel in FILL_SENTINELS:
        hit = a == sentinel
        if hit.any():
            stats["sentinels"] += int(hit.sum())
            a[hit] = np.nan

    lo_hi = PLAUSIBLE_RANGE.get(kind)
    if lo_hi:
        lo, hi = lo_hi
        bad = np.isfinite(a) & ((a < lo) | (a > hi))
        stats["out_of_range"] = int(bad.sum())
        a[bad] = np.nan

    tol = SPIKE_TOLERANCE.get(kind)
    if tol and a.shape[0] > 2 and a.shape[1] > 2:
        centre = a[1:-1, 1:-1]
        neigh = np.stack([a[:-2, 1:-1], a[2:, 1:-1], a[1:-1, :-2], a[1:-1, 2:]])
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(centre) & np.all(np.isfinite(neigh), axis=0)
            # Median, not mean: with the mean, a single spike drags its four
            # neighbours past the tolerance too and one bad cell costs five.
            # The median of four neighbours ignores one contaminated value.
            dev = np.abs(centre - np.median(neigh, axis=0))
            spike = ok & (dev > tol)
        stats["spikes"] = int(spike.sum())
        if spike.any():
            inner = a[1:-1, 1:-1]
            inner[spike] = np.nan
            a[1:-1, 1:-1] = inner

    if fill_gaps and MAX_GAP_PX > 0 and a.shape[0] > 2 and a.shape[1] > 2:
        # Each pass fills only holes with at least three valid neighbours, so
        # a pinhole closes and the interior of a large hole never does.
        for _ in range(MAX_GAP_PX):
            hole = ~np.isfinite(a)
            if not hole.any():
                break
            padded = np.pad(a, 1, constant_values=np.nan)
            stack = np.stack([padded[:-2, 1:-1], padded[2:, 1:-1],
                              padded[1:-1, :-2], padded[1:-1, 2:]])
            valid = np.isfinite(stack)
            count = valid.sum(axis=0)
            with np.errstate(invalid="ignore"):
                mean = np.where(count > 0,
                                np.nansum(np.where(valid, stack, 0.0), axis=0)
                                / np.maximum(count, 1), np.nan)
            fillable = hole & (count >= 3) & np.isfinite(mean)
            if not fillable.any():
                break
            stats["gaps_filled"] += int(fillable.sum())
            a[fillable] = mean[fillable]

    stats["removed_total"] = int(before.sum() - np.isfinite(a).sum()
                                 + stats["gaps_filled"])
    return a.astype("float32"), stats


# =====================================================================
# SECTION 7 -- HDF5 SCHEMA AND WRITING
#
# Structure follows NISAR HDF5 conventions:
#
#   identification/          file-level metadata, no science data
#   science/LIDAR/DEM/grids/elevation
#   science/LIDAR/SD/<YYYYMMDD>/snow_depth
#   science/LIDAR/VH/<YYYYMMDD>/veg_height
#   science/UAVSAR/INTERFEROMETRY_GRD/<group>/<POL>/{int,unw,cor,hgt}
#   science/UAVSAR/AMPLITUDE_GRD/<group>/<POL>
#   science/UAVSAR/DEM_TIFF/<group>/elevation
#   matches/                 flat table, equal-length arrays
# =====================================================================

#: Target chunk edge in pixels; override with --chunk-edge.
#:
#: Measured on a real UAVSAR array (3575 x 7622 float32, 100% valid), reading
#: 120 random 256x256 patches with h5py's stock 1 MB chunk cache:
#:
#:     chunks      stored    patches    single pixels
#:     128x128     84.0 MB     0.25 s        0.06 s
#:     256x256     83.7 MB     0.51 s        0.25 s
#:     512x512     83.5 MB     1.10 s        1.00 s
#:     1024x1024   83.6 MB     3.20 s        5.65 s
#:
#: 128 is 3-17x faster than the 512 this used to be, for 0.6% more storage,
#: because a 512x512 float32 chunk is 1.05 MB -- just over h5py's default 1 MB
#: cache, so every read evicts it. Raising the reader's cache to 64 MB helps
#: the large chunks but does not catch up: 512 tuned is still 2x slower than
#: 128 untuned. Small chunks perform well for readers who configure nothing,
#: which matters for a shared archive.
#:
#: Caveat: measured on local SSD. A parallel filesystem (Lustre, NFS) charges
#: more per operation and may favour larger chunks; revisit if Borah's shared
#: storage behaves differently.
CHUNK_EDGE = 128

COMPRESSION = "gzip"
COMPRESSION_LEVEL = 4

#: Array name for each LiDAR product, per the schema.
LIDAR_ARRAY_NAME = {"DEM": "elevation", "SD": "snow_depth",
                    "VH": "veg_height", "SWE": "swe",
                    "DENSITY": "snow_density"}


def lidar_group_path(product: str, date_key: str | None = None) -> str:
    """Group holding one LiDAR array.

    The DEM sits under `grids/` where the dated products carry a date key --
    this asymmetry is the README's schema as written, kept verbatim so the
    builder and verifier agree with the documented layout.
    """
    if product == "DEM":
        return "science/LIDAR/DEM/grids"
    if not date_key:
        raise ValueError(f"{product} requires a date key")
    return f"science/LIDAR/{product}/{date_key}"


def uavsar_group_path(level: str, group_name: str) -> str:
    return f"science/UAVSAR/{level}/{group_name}"


def choose_chunks(shape: tuple[int, int], edge: int = CHUNK_EDGE
                  ) -> tuple[int, int]:
    """Chunk shape for an array, never larger than the array itself.

    h5py rejects a chunk bigger than the dataset, which is easy to hit on the
    small sites.
    """
    height, width = shape
    if height <= 0 or width <= 0:
        raise ValueError(f"cannot chunk an empty array of shape {shape}")
    return (min(edge, height), min(edge, width))


def _attr_value(value: Any) -> Any:
    """Coerce a Python value into something h5py can store.

    h5py cannot store None, and silently stores str as fixed-width ASCII unless
    told otherwise, which mangles any non-ASCII text in a description.
    """
    import numpy as np

    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        if not value:
            return np.zeros(0, dtype="float64")
        if all(isinstance(v, str) for v in value):
            return [str(v) for v in value]
        return np.asarray(value)
    return value


def set_attrs(obj, attrs: dict[str, Any]) -> None:
    for key, value in attrs.items():
        obj.attrs[key] = _attr_value(value)


def array_exists(group, name: str, grid: CommonGrid,
                 content_key: str | None) -> bool:
    """Whether `name` is already present and matches what we would write.

    Deleting an HDF5 dataset does not return its bytes to the filesystem, so a
    delete-and-rewrite loop grows the file on every run. Skipping arrays that
    are already correct keeps a re-run free and makes an interrupted 175 GB job
    resumable rather than restartable.
    """
    if name not in group:
        return False
    existing = group[name]
    if tuple(existing.shape) != tuple(grid.shape):
        return False
    if content_key is None:
        return True
    return str(existing.attrs.get("content_key", "")) == content_key


def cleaning_attrs(stats: dict, kind: str, *, fill_gaps: bool = True,
                   is_complex: bool = False,
                   stage: str = "builder_cleaning") -> dict[str, Any]:
    """Record this pass's settings and event counts, never cumulative totals."""
    names = {"sentinels": "cleaning_sentinels_removed",
             "out_of_range": "cleaning_out_of_range_removed",
             "spikes": "cleaning_spikes_removed",
             "gaps_filled": "cleaning_gaps_interpolated",
             "removed_total": "cleaning_removal_events"}
    attrs = {name: int(stats[key]) for key, name in names.items() if key in stats}
    attrs.update({
        "cleaning_stage": stage,
        "cleaning_input_stage": ("common_grid_after_resampling"
                                 if stage == "builder_cleaning" else "stored_input_array"),
        "cleaning_record_scope": "this_pass_event_counts; not cumulative or pixel lineage",
        "cleaning_kind": kind,
        "cleaning_status": "bypassed_complex" if is_complex else "applied_scalar",
        "cleaning_fill_gaps_enabled": fill_gaps,
        "cleaning_gap_max_passes": MAX_GAP_PX,
        "cleaning_gap_min_finite_cardinal_neighbors": 3,
        "cleaning_sentinel_values": list(FILL_SENTINELS),
        "cleaning_range_configured": kind in PLAUSIBLE_RANGE,
        "cleaning_spike_configured": kind in SPIKE_TOLERANCE,
        "cleaning_note": (
            "Complex input bypassed all builder cleaning steps. Recorded zero "
            "events describe this bypass; scalar settings were not applied."
            if is_complex else
            "This pass applies configured sentinel and range screens, then a "
            "spike screen where configured, using the median of four finite "
            "cardinal neighbours on interior cells. Screens are heuristics, "
            "not proof of an invalid measurement. If gap filling is enabled "
            "and both dimensions exceed two, up to the recorded number of "
            "passes replace missing cells with the mean of at least three "
            "finite cardinal neighbours. Hole area is not measured. Removed "
            "cells may be refilled. Counters record events in this pass, not "
            "the locations or present-day number of measured cells. No boundary "
            "clipping is performed in this builder step."
        )})
    if kind in PLAUSIBLE_RANGE:
        attrs["cleaning_plausible_range"] = list(PLAUSIBLE_RANGE[kind])
    if kind in SPIKE_TOLERANCE:
        attrs["cleaning_spike_tolerance"] = SPIKE_TOLERANCE[kind]
    return attrs


def write_array(
    group,
    name: str,
    data,
    *,
    description: str,
    resampling_method: str,
    source: str,
    grid: CommonGrid,
    extra: dict[str, Any] | None = None,
    content_key: str | None = None,
    overwrite: bool = False,
):
    """Write one array plus the attributes every array in this archive carries.

    An array already present and matching `content_key` is left untouched
    unless `overwrite` is set. See `array_exists` for why.
    """
    import numpy as np

    array = np.asarray(data)
    if array.shape != grid.shape:
        raise ValueError(
            f"{group.name}/{name}: array shape {array.shape} does not match the "
            f"common grid {grid.shape}"
        )

    if not overwrite and array_exists(group, name, grid, content_key):
        log.debug("    %s/%s already written; skipped", group.name, name)
        return group[name]

    if name in group:
        del group[name]

    dataset = group.create_dataset(
        name,
        data=array,
        chunks=choose_chunks(array.shape),
        compression=COMPRESSION,
        compression_opts=COMPRESSION_LEVEL,
        shuffle=True,
    )

    finite_mask = np.isfinite(array.real)
    finite = int(finite_mask.sum())

    stats = statistics_attrs(array, finite_mask, percentiles=True)

    set_attrs(dataset, {
        "description": description,
        "crs_wkt": grid.crs_wkt,
        "crs_epsg": grid.epsg if grid.epsg is not None else -1,
        "transform": list(grid.transform),
        "shape": [array.shape[0], array.shape[1]],
        "nodata": "NaN",
        "dtype": array.dtype.name,
        "resolution_m": grid.res_m,
        "resampling_method": resampling_method,
        "source_dataset": source,
        "valid_pixel_count": finite,
        "valid_fraction": finite / array.size if array.size else 0.0,
        "content_key": content_key or "",
    })
    if extra:
        set_attrs(dataset, {k: v for k, v in extra.items() if not k.startswith("value_")})
    set_attrs(dataset, stats)
    if extra and "cleaning_stage" in extra:
        record_processing_history(dataset.attrs, extra["cleaning_stage"])
    source_attrs = {"source_dataset": source, "content_key": content_key or "",
                    **{k: v for k, v in (extra or {}).items()
                       if k.startswith(("source_", "acquisition_", "annotation_"))}}
    ancestor = group
    while ancestor.name != '/':
        for key, value in ancestor.attrs.items():
            if key.startswith(("source_", "asf_", "original_product_", "acquisition_", "annotation_")):
                source_attrs.setdefault(key, json_value(value))
        ancestor = ancestor.parent
    store_lineage(dataset.attrs, new_record('builder_array_write', _BUILD_SOURCES,
        {'dataset_path': dataset.name, 'grid': grid.to_attrs(),
         'resampling_method': resampling_method, 'overwrite': overwrite,
         'compression': COMPRESSION, 'compression_level': COMPRESSION_LEVEL,
         'chunks': list(dataset.chunks), 'shuffle': True,
         'processing_attributes': {k: v for k, v in (extra or {}).items() if k.startswith('cleaning_')},
         'input_identity_note': 'Source identifiers are not content hashes unless explicitly recorded.'},
        inputs=[source_attrs]))
    return dataset


def write_identification(h5, site: Site, grid: CommonGrid,
                         extra: dict[str, Any] | None = None,
                         overwrite: bool = False):
    """File-level metadata. No science data lives here.

    Rewritten only when something actually changed. Beyond the file-growth
    problem, this keeps `created_date` meaning what it says: the moment the
    file was first written, not the last time a resumed run touched it.
    """
    import hashlib

    group = h5.require_group("identification")
    attrs: dict[str, Any] = {
        "site_name": site.name,
        "site_key": site.key,
        "state": site.state,
        "product_version": VERSION,
        "reference_product": site.reference_product,
        "grid_extent_rule": GRID_EXTENT_RULE,
        "grid_includes_clip_buffer": bool(GRID_INCLUDES_BUFFER),
        "clip_buffer_m": CLIP_BUFFER_M,
        "chunk_edge_px": CHUNK_EDGE,
        "uavsar_native_resolution_m": list(UAVSAR_NATIVE_RES_M),
        "resampling_note": (
            "UAVSAR arrays are resampled onto the 3 m LiDAR grid. This adds no "
            "information: their true ground resolution remains about "
            f"{UAVSAR_NATIVE_RES_M[0]} m east-west by {UAVSAR_NATIVE_RES_M[1]} m "
            "north-south. Wrapped-phase (.int) arrays use nearest-neighbour "
            "resampling; everything else uses bilinear."
        ),
        "site_note": site.note,
    }
    attrs.update(grid.to_attrs())
    if extra:
        attrs.update(extra)

    digest = hashlib.sha256(
        json.dumps({k: str(v) for k, v in sorted(attrs.items())}).encode("utf-8")
    ).hexdigest()[:16]
    if not overwrite and str(group.attrs.get("content_key", "")) == digest:
        return group

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    existing_created = str(group.attrs.get("created_date", "")) or now
    attrs["created_date"] = existing_created
    if existing_created != now:
        attrs["updated_date"] = now
    attrs["content_key"] = digest

    parent = read_lineage(group.attrs)
    set_attrs(group, attrs)
    store_lineage(group.attrs, new_record('builder_identification_write', _BUILD_SOURCES,
        {'metadata': attrs, 'science_lineage': 'per_dataset; skipped historical arrays remain unknown',
         'match_window_days': MATCH_WINDOW_DAYS, 'resampling': RESAMPLING,
         'fill_sentinels': FILL_SENTINELS, 'plausible_ranges': PLAUSIBLE_RANGE,
         'spike_tolerances': SPIKE_TOLERANCE, 'gap_fill_passes': MAX_GAP_PX},
        inputs=[{'reference_filename': attrs.get('reference_filename', ''),
                 'site': site.key}], parent=parent))
    return group


#: Columns as published, mapped to the names used here. The two collections
#: share a schema exactly, so one mapping serves both.
POINT_COLUMNS = {
    "Date[mmddyy]": ("date", "str"),
    "Time[HHMMSS]": ("time", "str"),
    "Longitude[DD]": ("longitude", "f8"),
    "Latitude[DD]": ("latitude", "f8"),
    "ElevationWGS84[mae]": ("elevation_m", "f8"),
    "Easting[m]": ("source_easting_m", "f8"),
    "Northing[m]": ("source_northing_m", "f8"),
    "UTM_Zone": ("source_utm_zone", "f8"),
    "TWT[ns]": ("two_way_travel_time_ns", "f8"),
    "Depth[cm]": ("depth_cm", "f8"),
    "SWE[mm]": ("swe_mm", "f8"),
    "Density[kg m-3]": ("density_kg_m3", "f8"),
    "Permittivity[unitless]": ("permittivity", "f8"),
}

POINT_UNITS = {
    "depth_cm": "cm", "swe_mm": "mm", "density_kg_m3": "kg m-3",
    "two_way_travel_time_ns": "ns", "elevation_m": "m",
    "permittivity": "dimensionless, relative",
}


def read_pit_summary(raw: bytes):
    """Parse an NSIDC pit summary CSV.

    The file opens with a block of comment lines and an attribute glossary
    whose lines also contain commas, so the header cannot be found by position.
    It is located by content instead: the first non-comment line carrying
    `PitID`. The line after it is a row of bare column numbers, which pandas
    would otherwise read as an observation.
    """
    import io as _io

    import pandas as pd

    lines = raw.decode("utf-8", "replace").splitlines()
    try:
        h = next(i for i, x in enumerate(lines)
                 if not x.startswith("#") and "PitID" in x and x.count(",") > 6)
    except StopIteration:
        raise ValueError("no header row containing PitID")
    body = [lines[h]] + [x for x in lines[h + 1:] if not x.startswith("#")]
    df = pd.read_csv(_io.StringIO("\n".join(body)))
    return df[~df.iloc[:, 0].astype(str).str.fullmatch(r"\d+")].reset_index(drop=True)


def ingest_points(h5, site: Site, spec: PointSpec, grid: "CommonGrid",
                  session, overwrite: bool = False) -> int:
    """Fetch one in-situ CSV, clip it to the site, and write it as a table.

    Returns the number of points written. Points outside the site grid are
    dropped: these surveys span whole campaigns and a Cameron Pass transect
    has no business inside the Grand Mesa file.
    """
    import io as _io

    import numpy as np
    import pandas as pd
    from pyproj import Transformer

    group_path = f"insitu/{spec.key}"
    if not overwrite and group_path in h5 and h5[group_path].attrs.get("complete"):
        log.info("    %s already present; skipped", spec.key)
        return int(h5[group_path].attrs.get("point_count", 0))

    gran = _search_cmr(spec.short_name, {})
    url = None
    for g in gran:
        fn = _granule_filename(g)
        if spec.filename_contains in fn:
            url = _granule_url(g)
            break
    if url is None:
        log.warning("    %s: no granule matching %r", spec.short_name,
                    spec.filename_contains)
        return 0

    log.info("    fetching %s", url.rsplit("/", 1)[-1])
    r = session.get(url, timeout=300)
    r.raise_for_status()

    if spec.layout == "pit_summary":
        df = read_pit_summary(r.content)
        TEXT = {"pit_id", "location", "site_name", "flag"}
        colmap = {}
        for actual in df.columns:
            stored = PIT_COLUMNS.get(_norm_col(actual))
            if stored:
                colmap[actual] = (stored, "str" if stored in TEXT else "f8")
        datecol = next((c for c in df.columns if "Date" in c), None)
        if datecol:
            colmap[datecol] = ("datetime_local", "str")
        got = {v[0] for v in colmap.values()}
        missing = sorted(set(PIT_COLUMNS.values()) - got)
        if missing:
            log.warning("    %s: pit columns not found: %s", spec.key, missing)
        lonkey = next(c for c in df.columns if c.lower().startswith("lon"))
        latkey = next(c for c in df.columns if c.lower().startswith("lat"))
    else:
        df = pd.read_csv(_io.BytesIO(r.content))
        colmap = dict(POINT_COLUMNS)
        lonkey, latkey = "Longitude[DD]", "Latitude[DD]"
        missing = [c for c in POINT_COLUMNS if c not in df.columns]
        if missing:
            log.warning("    %s: unexpected columns, missing %s",
                        spec.key, missing[:3])

    # Points are published in WGS84 lon/lat; the grid is in the site CRS.
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    lon = pd.to_numeric(df[lonkey], errors="coerce").to_numpy(dtype="f8")
    lat = pd.to_numeric(df[latkey], errors="coerce").to_numpy(dtype="f8")
    finite = np.isfinite(lon) & np.isfinite(lat)
    x = np.full(lon.shape, np.nan)
    y = np.full(lat.shape, np.nan)
    x[finite], y[finite] = tf.transform(lon[finite], lat[finite])

    # CommonGrid.transform is the raw six-tuple, not an Affine object.
    a, _, c, _, e, f = grid.transform
    col = np.floor((x - c) / a).astype("int64")
    row = np.floor((y - f) / e).astype("int64")
    inside = (finite & (row >= 0) & (row < grid.height)
              & (col >= 0) & (col < grid.width))
    n_in, n_all = int(inside.sum()), len(df)
    if not n_in:
        log.warning("    %s: no points fall inside this site", spec.key)
        return 0

    grp = h5.require_group(group_path)
    for k in list(grp.keys()):
        del grp[k]

    for src_col, (name, kind) in colmap.items():
        if src_col not in df.columns:
            continue
        vals = df[src_col].to_numpy()[inside]
        if kind == "str":
            data = np.array([str(v) for v in vals], dtype=h5py_vlen_str())
        else:
            data = pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy("f8")
            # -9999 marks "profile not collected". Left as a number it would
            # quietly poison any mean a user takes.
            data = np.where(data == PIT_FILL, np.nan, data)
        ds = grp.create_dataset(name, data=data, compression="gzip",
                                compression_opts=4)
        if name in POINT_UNITS:
            ds.attrs["units"] = POINT_UNITS[name]

    grp.create_dataset("grid_row", data=row[inside], compression="gzip")
    grp.create_dataset("grid_col", data=col[inside], compression="gzip")
    grp["grid_row"].attrs["description"] = (
        "Row of the common grid this point falls in, so the observation can be "
        "compared directly with any raster in this file.")
    grp["grid_col"].attrs["description"] = grp["grid_row"].attrs["description"]

    set_attrs(grp, {
        "description":
            "Ground-penetrating radar transect with co-located lidar snow "
            "depth. Two-way travel time and depth are measured; SWE, density "
            "and relative permittivity are derived from them by the data "
            "producer. Point observations along survey lines, NOT a grid.",
        "source_dataset": spec.short_name,
        "source_filename": url.rsplit("/", 1)[-1],
        "source_url": url,
        "point_count": n_in,
        "points_outside_site_dropped": n_all - n_in,
        "note": spec.note,
        "usage_note":
            "grid_row and grid_col index the common grid. Permittivity is the "
            "term the InSAR phase-to-depth inversion requires; every site "
            "without one of these tables has to assume it.",
        "complete": True,
    })
    store_lineage(grp.attrs, new_record('builder_point_table', _BUILD_SOURCES,
        {'site': site.key, 'grid': grid.to_attrs(), 'layout': spec.layout,
         'column_mapping': colmap, 'fill_value': PIT_FILL, 'overwrite': overwrite},
        inputs=[{'source_url': url, 'source_dataset': spec.short_name,
                 'sha256': hashlib.sha256(r.content).hexdigest(),
                 'identity_method': 'sha256_of_consumed_csv_bytes'}]))
    for dataset in grp.values():
        dataset.attrs['provenance_artifact_id'] = grp.attrs['artifact_id']
    log.info("    %s: %d point(s) inside the site, %d dropped",
             spec.key, n_in, n_all - n_in)
    return n_in


def h5py_vlen_str():
    import h5py
    return h5py.special_dtype(vlen=str)


def write_matches(h5, matches: Sequence[Match], overwrite: bool = False):
    """The flat match table: equal-length arrays, one row per pair.

    Rewritten only when the content actually changes, for the same
    space-reclamation reason as `array_exists`.
    """
    import hashlib

    import numpy as np

    digest = hashlib.sha256(
        json.dumps([asdict(m) for m in matches], sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    group = h5.require_group("matches")
    if not overwrite and str(group.attrs.get("content_key", "")) == digest:
        return group
    for key in list(group):
        del group[key]

    columns = {
        "lidar_product_type": [m.lidar_product_type for m in matches],
        "lidar_filename": [m.lidar_filename for m in matches],
        "lidar_date": [m.lidar_date for m in matches],
        "uavsar_flight_id": [m.uavsar_scene for m in matches],
        "uavsar_level": [m.uavsar_level for m in matches],
        "uavsar_dates": [m.uavsar_dates for m in matches],
        "group_name": [m.group_name for m in matches],
        "verdict": [m.verdict for m in matches],
    }
    string_dtype = _h5_string_dtype()
    for name, values in columns.items():
        group.create_dataset(name, data=np.array(values, dtype=object),
                             dtype=string_dtype)
    group.create_dataset("gap_days",
                         data=np.array([m.gap_days for m in matches], dtype="int32"))

    set_attrs(group, {
        "description": (
            "One row per LiDAR granule / UAVSAR flight pair considered. "
            "verdict='match' means the pair is within the match window and the "
            "footprints intersect. verdict='snow_off_no_match_expected' marks "
            "the snow-off DEM survey, which correctly has no radar counterpart."
        ),
        "match_window_days": MATCH_WINDOW_DAYS,
        "row_count": len(matches),
        "content_key": digest,
    })
    store_lineage(group.attrs, new_record('builder_match_table', _BUILD_SOURCES,
        {'match_window_days': MATCH_WINDOW_DAYS, 'overwrite': overwrite},
        inputs=[{'match_rows': [asdict(m) for m in matches]}]))
    for dataset in group.values():
        dataset.attrs['provenance_artifact_id'] = group.attrs['artifact_id']
    return group


def _h5_string_dtype():
    import h5py

    return h5py.string_dtype(encoding="utf-8")


# =====================================================================
# SECTION 8 -- VERIFY
# =====================================================================


#: Attributes every science array must carry for the archive to be self-describing.
REQUIRED_ARRAY_ATTRS = (
    "description", "crs_wkt", "transform", "shape", "nodata", "dtype",
    "resolution_m", "resampling_method", "source_dataset",
)

REQUIRED_IDENTIFICATION_ATTRS = (
    "site_name", "common_crs_wkt", "common_crs_epsg", "common_grid_transform",
    "common_grid_shape", "uavsar_native_resolution_m", "resampling_note",
    "product_version", "created_date",
)


def verify_file(path: Path, strict_empty: bool = True) -> list[str]:
    """Reopen one site file and check every invariant. Returns problems found."""
    import h5py
    import numpy as np

    problems: list[str] = []

    with h5py.File(path, "r") as h5:
        if "identification" not in h5:
            return [f"{path.name}: no identification group"]
        ident = h5["identification"]

        for key in REQUIRED_IDENTIFICATION_ATTRS:
            if key not in ident.attrs:
                problems.append(f"identification is missing attribute {key!r}")

        if "common_grid_shape" not in ident.attrs:
            return problems + [f"{path.name}: cannot verify arrays without a grid"]

        expected_shape = tuple(int(v) for v in ident.attrs["common_grid_shape"])
        expected_transform = tuple(
            float(v) for v in ident.attrs["common_grid_transform"]
        )
        expected_crs = str(ident.attrs["common_crs_wkt"])

        if "science" not in h5:
            problems.append("no science group")
            return problems

        n_arrays = 0

        def check(name: str, obj) -> None:
            """Inspect one dataset. Any unexpected failure becomes a problem
            report rather than an exception, so one damaged array cannot hide
            the state of every array after it."""
            nonlocal n_arrays
            if not isinstance(obj, h5py.Dataset):
                return
            n_arrays += 1
            full = f"science/{name}"
            try:
                _check_dataset(full, obj)
            except Exception as exc:  # noqa: BLE001 -- report, never abort
                problems.append(f"{full}: could not be verified "
                                f"({type(exc).__name__}: {exc})")

        def _check_dataset(full: str, obj) -> None:
            if obj.shape != expected_shape:
                problems.append(
                    f"{full}: shape {obj.shape} does not match the common grid "
                    f"{expected_shape}"
                )
            for key in REQUIRED_ARRAY_ATTRS:
                if key not in obj.attrs:
                    problems.append(f"{full}: missing attribute {key!r}")

            if "transform" in obj.attrs:
                # Never trust the stored value: a corrupt attribute must be
                # reported, not raised. The verifier's job is to survive bad
                # files and describe them.
                try:
                    got = tuple(float(v) for v in obj.attrs["transform"])
                except (TypeError, ValueError):
                    problems.append(
                        f"{full}: transform attribute is not six numbers "
                        f"({obj.attrs['transform']!r})"
                    )
                else:
                    if len(got) != len(expected_transform) or not all(
                        abs(a - b) < 1e-6
                        for a, b in zip(got, expected_transform)
                    ):
                        problems.append(
                            f"{full}: transform {got} does not match the "
                            "common grid"
                        )
            if "crs_wkt" in obj.attrs and str(obj.attrs["crs_wkt"]) != expected_crs:
                problems.append(f"{full}: CRS differs from the common grid")
            if "description" in obj.attrs and not str(obj.attrs["description"]).strip():
                problems.append(f"{full}: description is empty")

            if strict_empty:
                sample = obj[...]
                if np.iscomplexobj(sample):
                    finite = np.isfinite(sample.real) & np.isfinite(sample.imag)
                else:
                    finite = np.isfinite(sample)
                if not finite.any():
                    problems.append(f"{full}: every pixel is nodata")

        h5["science"].visititems(check)

        if n_arrays == 0:
            problems.append("science group contains no arrays")

        # An acquisition group that holds no arrays is a hollow shell: the
        # group and its attributes were written, then every array failed. The
        # per-array checks above cannot see this, because there are no arrays
        # to check -- the file passes vacuously while a whole acquisition is
        # silently absent.
        uav = h5.get("science/UAVSAR")
        if uav is not None:
            for level in uav:
                for name in uav[level]:
                    node = uav[level][name]
                    count = [0]
                    node.visititems(
                        lambda _n, o: count.__setitem__(0, count[0] + 1)
                        if isinstance(o, h5py.Dataset) else None)
                    if count[0] == 0:
                        problems.append(
                            f"science/UAVSAR/{level}/{name}: group contains no "
                            "arrays (ingest failed part-way)")
                    # `ingest_complete` is deliberately NOT asserted here. It is
                    # a resume marker, not a property of the data: groups written
                    # before that marker existed are perfectly valid, and
                    # treating its absence as corruption produces false alarms
                    # on a sound archive.

        if "matches" in h5:
            lengths = {k: h5["matches"][k].shape[0] for k in h5["matches"]}
            if len(set(lengths.values())) > 1:
                problems.append(f"matches columns have unequal lengths: {lengths}")

    return problems


def run_verify(paths: Sequence[Path], strict_empty: bool = True) -> int:
    total = 0
    for path in paths:
        if not path.exists():
            log.error("%s: missing", path)
            total += 1
            continue
        problems = verify_file(path, strict_empty=strict_empty)
        if problems:
            log.error("%s: %d problem(s)", path.name, len(problems))
            for p in problems:
                log.error("    %s", p)
            total += len(problems)
        else:
            log.info("%s: OK", path.name)
    if total:
        log.error("verification failed with %d problem(s)", total)
    else:
        log.info("all files verified")
    return total


# =====================================================================
# SECTION 9 -- AUTHENTICATION AND DATA ACCESS
# =====================================================================

EDL_HOST = "urs.earthdata.nasa.gov"


def netrc_path() -> Path | None:
    """Locate the netrc file, whatever the platform calls it.

    POSIX uses `~/.netrc`; Windows conventionally uses `~/_netrc`. The stdlib
    `netrc.netrc()` does NOT know this -- with no argument it looks only for
    `~/.netrc` and raises FileNotFoundError on Windows even when `_netrc` is
    sitting right there. Both names are checked here, in both places, so the
    same code path works on a laptop and on Borah.
    """
    import os

    home = Path(os.path.expanduser("~"))
    candidates = [home / ".netrc", home / "_netrc"]
    override = os.environ.get("NETRC")
    if override:
        candidates.insert(0, Path(override))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def earthdata_credentials() -> tuple[str, str]:
    """Earthdata Login credentials, from the environment or from netrc.

    Never hardcoded, never logged.
    """
    import os

    user = os.environ.get("EARTHDATA_USERNAME")
    password = os.environ.get("EARTHDATA_PASSWORD")
    if user and password:
        log.debug("using Earthdata credentials from the environment")
        return user, password

    import netrc

    path = netrc_path()
    if path is None:
        raise RuntimeError(
            "No Earthdata credentials found. Set EARTHDATA_USERNAME and "
            f"EARTHDATA_PASSWORD, or create a netrc entry for {EDL_HOST} "
            "(~/.netrc on Linux, ~/_netrc on Windows)."
        )

    try:
        entry = netrc.netrc(str(path)).authenticators(EDL_HOST)
    except Exception as exc:  # noqa: BLE001 -- netrc raises several types
        raise RuntimeError(
            f"could not read {path}: {type(exc).__name__}: {exc}"
        ) from exc

    if not entry:
        raise RuntimeError(f"{path} has no entry for {EDL_HOST}")
    login, _, password = entry
    if not login or not password:
        raise RuntimeError(f"the {EDL_HOST} entry in {path} is incomplete")
    log.debug("using Earthdata credentials from %s", path)
    return login, password


def asf_session():
    """An authenticated ASF session for downloads.

    Metadata search needs no authentication; only downloads do.
    """
    import asf_search as asf

    user, password = earthdata_credentials()
    return asf.ASFSession().auth_with_creds(user, password)


def earthaccess_login():
    """Authenticate to Earthdata Login.

    Deliberately not retried when the credentials themselves are rejected:
    repeating a wrong password neither helps nor is harmless, since Earthdata
    rate-limits and can lock an account after repeated failures. Only genuine
    transport failures are worth a second attempt.
    """
    import earthaccess

    def attempt():
        try:
            return earthaccess.login(strategy="netrc")
        except Exception as exc:  # noqa: BLE001
            if "invalid_credentials" in str(exc) or "Invalid user" in str(exc):
                raise PermissionError(
                    "Earthdata rejected the credentials (invalid_credentials). "
                    "The password is wrong or expired."
                ) from exc
            raise

    try:
        return retry(attempt, attempts=3, what="Earthdata login",
                     fatal=(PermissionError,))
    except RuntimeError as exc:
        cause = exc.__cause__
        if isinstance(cause, PermissionError):
            raise cause from None
        raise


def run_check_auth() -> int:
    """Report whether credentials work, without ever printing them.

    Checks the three things independently, because they fail for different
    reasons: Earthdata Login itself, the ASF session used for UAVSAR
    downloads, and an actual authenticated pixel read from the DAAC.
    """
    import os
    import stat

    ok = True

    log.info("=" * 62)
    log.info("1. credential source")
    if os.environ.get("EARTHDATA_USERNAME") and os.environ.get("EARTHDATA_PASSWORD"):
        log.info("   EARTHDATA_USERNAME / EARTHDATA_PASSWORD are set")
    path = netrc_path()
    if path is None:
        log.info("   no netrc file found at ~/.netrc or ~/_netrc")
    else:
        log.info("   netrc: %s", path)
        if os.name != "nt":
            mode = path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                log.error("   permissions are %o -- earthaccess rejects a "
                          "group/world-readable netrc. Run: chmod 600 %s",
                          stat.S_IMODE(mode), path)
                ok = False
            else:
                log.info("   permissions look right (0%o)", stat.S_IMODE(mode))
    try:
        user, _ = earthdata_credentials()
        log.info("   username: %s", user)
    except RuntimeError as exc:
        log.error("   %s", exc)
        return 1

    log.info("2. Earthdata Login")
    try:
        auth = earthaccess_login()
        log.info("   authenticated: %s", getattr(auth, "authenticated", "?"))
    except Exception as exc:  # noqa: BLE001
        log.error("   FAILED: %s", exc)
        log.error("   The password is wrong or expired. Reset it at")
        log.error("   https://urs.earthdata.nasa.gov and rewrite the netrc entry.")
        return 1

    log.info("3. ASF session (needed for UAVSAR downloads)")
    try:
        asf_session()
        log.info("   session created")
    except Exception as exc:  # noqa: BLE001
        log.error("   FAILED: %s", exc)
        ok = False

    log.info("4. authenticated read of one real LiDAR granule")
    try:
        import earthaccess
        import rasterio

        granules = retry(
            lambda: earthaccess.search_data(short_name=QSI_DEM, count=1),
            attempts=3, what="CMR search",
        )
        if not granules:
            log.warning("   no granule returned; skipped")
        else:
            url = granules[0].data_links()[0]
            handle = earthaccess.open([url])[0]
            with rasterio.open(handle) as src:
                log.info("   %s", url.rsplit("/", 1)[-1])
                log.info("   %d x %d px, %s, nodata=%s",
                         src.width, src.height, src.crs, src.nodata)
                src.read(1, window=((0, 4), (0, 4)))
            log.info("   pixel read succeeded")
    except Exception as exc:  # noqa: BLE001
        log.error("   FAILED: %s: %s", type(exc).__name__, exc)
        log.error("   Login works but data access does not. Usually this means")
        log.error("   a required end-user agreement has not been accepted yet.")
        ok = False

    log.info("=" * 62)
    if ok:
        log.info("all checks passed -- --mode build is ready to run")
        log.info("note: UAVSAR downloads additionally need the ASF end-user")
        log.info("agreement accepted once at https://search.asf.alaska.edu")
    else:
        log.error("some checks failed; see above")
    return 0 if ok else 1


#: Set once GDAL has been pointed at Earthdata with a bearer token.
_GDAL_EARTHDATA_READY = False


def configure_gdal_for_earthdata(auth) -> bool:
    """Let GDAL read protected DAAC files directly over HTTP range requests.

    fsspec, which `earthaccess.open()` uses, times out on the large reference
    DEMs -- Grand Mesa's DTM is 3.97 GB and Reynolds Creek's is 1.38 GB.
    GDAL's own /vsicurl driver opens the same 3.97 GB file in under three
    seconds and reads a 512 x 512 window in well under one, because these are
    tiled COGs and it issues real range requests.
    """
    global _GDAL_EARTHDATA_READY
    import os

    common = {
        # Do not list the whole bucket directory just to open one file.
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "26214400",
    }
    # Preferred: netrc login with a cookie jar, NASA's documented GDAL route.
    # GDAL 3.12 forwards a bearer header through NSIDC's redirect to its
    # CloudFront/S3 storage, which rejects it; the netrc route survives that.
    path = netrc_path()
    if path is not None:
        jar = Path.home() / ".cache" / "snowex" / "gdal_earthdata_cookies.txt"
        jar.parent.mkdir(parents=True, exist_ok=True)
        os.environ.pop("GDAL_HTTP_HEADERS", None)
        os.environ.update({**common, "GDAL_HTTP_NETRC": "YES",
                           "GDAL_HTTP_NETRC_FILE": str(path),
                           "GDAL_HTTP_COOKIEFILE": str(jar),
                           "GDAL_HTTP_COOKIEJAR": str(jar)})
        _GDAL_EARTHDATA_READY = True
        return True

    token = getattr(auth, "token", None)
    access = token.get("access_token") if isinstance(token, dict) else token
    if not access:
        log.warning("no Earthdata bearer token available; falling back to "
                    "streaming reads, which time out on the largest DEMs")
        return False

    if isinstance(token, dict) and token.get("expiration_date"):
        log.debug("Earthdata token expires %s", token["expiration_date"])

    os.environ.update({**common, "GDAL_HTTP_HEADERS": f"Authorization: Bearer {access}"})
    _GDAL_EARTHDATA_READY = True
    return True


def open_lidar_raster(granule: LidarGranule, local_dir: Path | None = None):
    """Open one LiDAR granule for reading.

    With `local_dir`, reads an already-downloaded file -- which is how the
    pipeline is exercised without network access. Otherwise streams from the
    DAAC. `earthaccess.open()` returns a file-like object, not a context
    manager, so it is opened first and handed to rasterio separately; opening a
    raw DAAC URL with rasterio directly gives HTTP 404 because no credentials
    are attached.
    """
    import rasterio

    if local_dir is not None:
        path = Path(local_dir) / granule.filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found in {local_dir}")
        return rasterio.open(path)

    if _GDAL_EARTHDATA_READY and granule.url.startswith("http"):
        return retry(lambda: rasterio.open("/vsicurl/" + granule.url),
                     attempts=3, what=f"open {granule.filename}")

    import earthaccess

    handle = retry(lambda: earthaccess.open([granule.url])[0],
                   what=f"open {granule.filename}")
    return rasterio.open(handle)


# =====================================================================
# SECTION 10 -- BUILD
# =====================================================================


@dataclass
class SitePlan:
    """Everything needed to write one site file, resolved from real rasters."""

    site: Site
    granules: list[LidarGranule]
    matches: list[Match]
    grid: CommonGrid
    reference_filename: str
    reference_epsg: int | None
    crs_note: str
    path: Path


def crs_is_equivalent(a, b, sample_lonlat: tuple[float, float],
                      tolerance_m: float = 0.01) -> bool:
    """Whether two CRSs place the same point in the same spot.

    Compares behaviour rather than metadata: a point is projected through both
    and the results are differenced. This is the only safe basis for relabelling
    a raster, because two CRS descriptions can differ in wording while being
    numerically identical, and can agree in wording while differing in datum.
    """
    from pyproj import CRS, Transformer

    lon, lat = sample_lonlat
    try:
        xa, ya = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(a),
                                      always_xy=True).transform(lon, lat)
        xb, yb = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(b),
                                      always_xy=True).transform(lon, lat)
    except Exception:  # noqa: BLE001 -- an untransformable CRS is not equivalent
        return False
    if not all(map(lambda v: v == v, (xa, ya, xb, yb))):  # NaN check
        return False
    return ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5 <= tolerance_m


def resolve_reference_crs(src_crs, site: Site, sample_lonlat):
    """Pick the CRS to stamp on the archive, and say whether it was assigned.

    Every SnowEx QSI raster ships as `PROJCS["unnamed"]` on an unnamed datum:
    `to_epsg()` returns None, so the archive would record `common_crs_epsg` as
    -1 and no downstream reader could identify the projection. The documented
    EPSG is substituted only when it is provably equivalent to what the raster
    actually declares, so the label is a clarification and never a change.

    Returns `(crs, assigned, note)`.
    """
    from pyproj import CRS

    epsg = None
    try:
        epsg = src_crs.to_epsg()
    except Exception:  # noqa: BLE001
        pass
    if epsg is not None:
        return src_crs, False, f"EPSG:{epsg} read directly from the reference raster."

    expected = site.expected_epsg
    if expected and crs_is_equivalent(src_crs, CRS.from_epsg(expected),
                                      sample_lonlat):
        log.info("  reference raster declares no EPSG; verified identical to "
                 "EPSG:%d and labelled as such", expected)
        return CRS.from_epsg(expected), True, (
            f"The reference raster declares an unnamed projection on an unnamed "
            f"datum and carries no EPSG code. It was verified to place points "
            f"identically to EPSG:{expected} (agreement within 1 cm) and is "
            f"labelled EPSG:{expected} so this archive is self-describing. The "
            f"pixel values and geometry are unchanged."
        )

    log.warning("  reference raster has no EPSG and does not match the expected "
                "EPSG:%s; keeping its own CRS unlabelled", expected)
    return src_crs, False, (
        "The reference raster carries no EPSG code and did not match the "
        "expected projection, so its own CRS description is preserved verbatim."
    )


def derive_site_grid(
    site: Site, granules: Sequence[LidarGranule], local_dir: Path | None = None
) -> tuple[CommonGrid, str, int | None, str]:
    """Build the common grid from the real rasters, not from CMR footprints.

    CMR polygons are approximations; the grid has to match the data exactly, so
    every extent here comes from the raster's own header.
    """
    reference = next((g for g in granules if g.product == site.reference_product),
                     None)
    if reference is None:
        raise RuntimeError(
            f"{site.key}: no {site.reference_product} granule to derive a grid from"
        )

    with open_lidar_raster(reference, local_dir) as src:
        raw_crs = src.crs
        ref_transform = src.transform
        ref_bounds = tuple(src.bounds)
        declared_epsg = src.crs.to_epsg() if src.crs else None
        # A point inside the raster, for the CRS equivalence check.
        centre_x = (ref_bounds[0] + ref_bounds[2]) / 2.0
        centre_y = (ref_bounds[1] + ref_bounds[3]) / 2.0

    sample_lonlat = transform_bounds_to(
        raw_crs, "EPSG:4326", (centre_x, centre_y, centre_x + 1.0, centre_y + 1.0)
    )[:2]

    resolved, assigned, crs_note = resolve_reference_crs(raw_crs, site, sample_lonlat)
    ref_epsg = resolved.to_epsg() if hasattr(resolved, "to_epsg") else declared_epsg
    # Carry the CRS as WKT from here on: rasterio and pyproj both accept it,
    # and `resolved` may be either library's CRS object.
    ref_crs = resolved.to_wkt()

    if declared_epsg is not None and declared_epsg != site.expected_epsg:
        log.warning(
            "%s: reference raster is EPSG:%s, config expected EPSG:%s -- using "
            "the raster", site.key, declared_epsg, site.expected_epsg
        )

    science_bounds: tuple[float, float, float, float] | None = None
    for g in granules:
        if g.product == "DEM":
            continue
        with open_lidar_raster(g, local_dir) as src:
            b = transform_bounds_to(src.crs, ref_crs, tuple(src.bounds))
        science_bounds = b if science_bounds is None else (
            min(science_bounds[0], b[0]), min(science_bounds[1], b[1]),
            max(science_bounds[2], b[2]), max(science_bounds[3], b[3]),
        )

    target = grid_target_bounds(
        ref_bounds, science_bounds, GRID_EXTENT_RULE,
        buffer_m=CLIP_BUFFER_M if GRID_INCLUDES_BUFFER else 0.0,
    )
    grid = derive_common_grid(ref_crs, ref_transform, target)
    log.info("  grid: %d x %d px at %.0f m, EPSG:%s",
             grid.height, grid.width, grid.res_m, grid.epsg)
    return grid, reference.filename, ref_epsg, crs_note


def grid_fingerprint(grid: CommonGrid) -> str:
    """Short stable digest of a grid, so a changed grid forces a rewrite."""
    import hashlib

    payload = f"{grid.crs_wkt}|{grid.transform}|{grid.shape}|{grid.res_m}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def ingest_lidar_granule(h5, granule: LidarGranule, grid: CommonGrid,
                         local_dir: Path | None = None,
                         overwrite: bool = False) -> bool:
    """Read, reproject and write one LiDAR granule. True if data is present."""
    import numpy as np

    group_path = lidar_group_path(
        granule.product, None if granule.product == "DEM" else granule.date_key
    )
    array_name = LIDAR_ARRAY_NAME[granule.product]
    content_key = f"{granule.filename}@{grid_fingerprint(grid)}"

    # Check before opening anything: on a resumed run this avoids a streamed
    # read and a reprojection for every array already on disk.
    if not overwrite and group_path in h5 and array_exists(
        h5[group_path], array_name, grid, content_key
    ):
        log.info("    %-9s %s  already present; skipped",
                 granule.product, granule.date_key)
        return True

    source_identity = (file_identity(local_dir / granule.filename) if local_dir is not None else
                       {'source_url': granule.url, 'identity_method': 'provider_locator_not_content_hash'})
    with open_lidar_raster(granule, local_dir) as src:
        windowed = read_windowed(src, grid)
        if windowed is None:
            log.warning("    %s does not overlap the grid; skipped",
                        granule.filename)
            return False
        data, transform, src_nodata = windowed
        src_crs = src.crs

    out = reproject_array(data, src_crs, transform, grid,
                          RESAMPLING["lidar"], src_nodata=src_nodata)
    out, clean_stats = clean_array(out, granule.product)
    valid = int(np.isfinite(out).sum())
    if valid == 0:
        log.warning("    %s reprojected to all-nodata; skipped", granule.filename)
        return False

    group = h5.require_group(group_path)
    write_array(
        group, array_name, out,
        description=DESCRIPTIONS[granule.product],
        resampling_method=RESAMPLING["lidar"],
        source=granule.short_name,
        grid=grid,
        content_key=content_key,
        overwrite=overwrite,
        extra={
            **LIDAR_QUANTITY_ATTRS.get(granule.product, {}),
            "acquisition_date": granule.date_begin.isoformat(),
            "acquisition_date_end": granule.date_end.isoformat(),
            "source_filename": granule.filename,
            "source_file_identity_json": json.dumps(source_identity, sort_keys=True),
            "source_url": granule.url,
            "source_native_resolution_m": granule.native_res_m,
            "source_note": granule.note,
            **cleaning_attrs(clean_stats, granule.product),
        },
    )
    log.info("    %-9s %s  %.1f%% valid", granule.product, granule.date_key,
             100.0 * valid / out.size)
    return True


def build_site_lidar(plan: SitePlan, local_dir: Path | None = None,
                     overwrite: bool = False, session=None, run_parameters=None) -> int:
    """Write identification, every LiDAR array, and the match table."""
    import h5py

    written = 0
    plan.path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(plan.path, "a") as h5:
        write_identification(
            h5, plan.site, plan.grid,
            overwrite=overwrite,
            extra={
                "reference_filename": plan.reference_filename,
                "builder_run_parameters_json": json.dumps(run_parameters or {
                    'local_dir': str(local_dir) if local_dir is not None else None,
                    'overwrite': overwrite, 'insitu_enabled': session is not None}, sort_keys=True),
                "reference_epsg_from_raster": (
                    plan.reference_epsg if plan.reference_epsg is not None else -1
                ),
                "crs_provenance_note": plan.crs_note,
                "lidar_granule_count": len(plan.granules),
                "citation_note": CITATION_NOTE,
            },
        )
        for granule in plan.granules:
            if ingest_lidar_granule(h5, granule, plan.grid, local_dir,
                                    overwrite):
                written += 1

        # In-situ transects. Only two sites have any, and a failure here must
        # not cost the rasters: these tables are a few megabytes of supporting
        # measurement, not the archive's reason for existing.
        # The caller supplies the session. Creating one here would drag the
        # network into every offline test that builds a synthetic site, which
        # is exactly what happened: the suite went from 7 s to 112 s.
        for spec in plan.site.points if session is not None else ():
            try:
                if ingest_points(h5, plan.site, spec, plan.grid, session,
                                 overwrite):
                    written += 1
            except Exception as exc:                           # noqa: BLE001
                log.warning("    %s: in-situ table skipped (%s: %s)",
                            spec.key, type(exc).__name__, exc)

        flag_pits_on_snow_depth(h5)
        write_matches(h5, plan.matches, overwrite)
    return written


#: A pit this many days from a lidar survey still describes the same snowpack
#: for density purposes. Depth does not survive the gap -- a single storm
#: invalidates it -- so the count is deliberately named for proximity, not
#: for agreement.
PIT_MATCH_DAYS = 3


def flag_pits_on_snow_depth(h5) -> int:
    """Record on each snow-depth array how many pits sit near it in time.

    Written as attributes rather than a derived array: the answer is a handful
    of counts, and putting them on the array means anyone opening the file sees
    immediately whether that date has in-situ support.
    """
    import datetime as _dt

    import numpy as np

    if "insitu" not in h5 or "science/LIDAR/SD" not in h5:
        return 0

    visits = []
    for key in h5["insitu"]:
        grp = h5["insitu"][key]
        if "datetime_local" not in grp:
            continue
        stamps = [x.decode() if isinstance(x, bytes) else str(x)
                  for x in grp["datetime_local"][...]]
        for t in stamps:
            try:
                visits.append(_dt.date.fromisoformat(t[:10]))
            except ValueError:
                continue
    if not visits:
        return 0

    flagged = 0
    for date_key in h5["science/LIDAR/SD"]:
        ds = h5[f"science/LIDAR/SD/{date_key}/snow_depth"]
        try:
            d0 = _dt.date.fromisoformat(str(ds.attrs.get("acquisition_date", "")))
            d1 = _dt.date.fromisoformat(
                str(ds.attrs.get("acquisition_date_end", "")) or d0.isoformat())
        except ValueError:
            continue
        offs = np.array([0 if d0 <= v <= d1 else
                         (v - d0).days if v < d0 else (v - d1).days
                         for v in visits])
        ds.attrs["pits_within_acquisition_window"] = int((offs == 0).sum())
        ds.attrs["pits_within_3_days"] = int((np.abs(offs) <= 3).sum())
        ds.attrs["pits_within_7_days"] = int((np.abs(offs) <= 7).sum())
        ds.attrs["pit_flag_note"] = (
            "Counts of snow pit visits inside this file, by how far they fall "
            "from this survey. Pits are useful here for DENSITY, which changes "
            "over weeks; depth changes with every storm, so a pit several days "
            "away is not a depth check. Pit coordinates and the grid cell each "
            "falls in are under insitu/.")
        flagged += 1
    return flagged


#: NASA user guides mandate citation, and the archive is destined for a shared
#: server where the guides will not travel with it. So it travels inside.
CITATION_NOTE = (
    "This archive contains NASA SnowEx LiDAR products distributed by the NSIDC "
    "DAAC and the ORNL DAAC, and NASA/JPL UAVSAR products distributed by the "
    "ASF DAAC. The SnowEx user guides require that these datasets be cited in "
    "any derived work. Cite each source dataset named in the source_dataset "
    "attribute of the arrays used."
)


def plan_sites(
    site_keys: Sequence[str], inventory: dict[str, Any], out_dir: Path,
    local_dir: Path | None = None,
) -> list[SitePlan]:
    """Turn the preflight inventory into per-site build plans."""
    plans: list[SitePlan] = []
    for key in site_keys:
        entry = inventory["sites"].get(key)
        if entry is None or "error" in entry:
            log.error("%s: not present in the inventory; run preflight first", key)
            continue
        site = SITES[key]
        log.info("=" * 62)
        log.info("%s", site.name)

        granules = [
            LidarGranule(
                site_key=g["site_key"], product=g["product"],
                short_name=g["short_name"], filename=g["filename"], url=g["url"],
                date_begin=date.fromisoformat(g["date_begin"]),
                date_end=date.fromisoformat(g["date_end"]),
                footprint_wkt=g["footprint_wkt"],
                native_res_m=g["native_res_m"], note=g.get("note", ""),
            )
            for g in entry["lidar"]
        ]
        matches = [Match(**m) for m in entry["matches"]]

        try:
            grid, ref_name, ref_epsg, crs_note = derive_site_grid(
                site, granules, local_dir)
        except Exception as exc:  # noqa: BLE001 -- one site must not stop the rest
            log.error("  %s: could not derive a grid (%s: %s); skipping this "
                      "site and continuing", site.key, type(exc).__name__, exc)
            continue
        plans.append(SitePlan(
            site=site, granules=granules, matches=matches, grid=grid,
            reference_filename=ref_name, reference_epsg=ref_epsg,
            crs_note=crs_note, path=out_dir / f"{key}.h5",
        ))
    return plans


def run_build(
    site_keys: Sequence[str], inventory_path: Path, out_dir: Path,
    local_dir: Path | None = None, overwrite: bool = False,
    work_dir: Path | None = None, skip_uavsar: bool = False,
    keep_downloads: bool = False, max_uavsar: int | None = None,
) -> int:
    if not inventory_path.exists():
        log.error("no inventory at %s -- run --mode preflight first", inventory_path)
        return 2
    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw.decode('utf-8'))
    run_parameters = {'site_keys': list(site_keys), 'local_dir': str(local_dir) if local_dir is not None else None,
                      'out_dir': str(out_dir), 'overwrite': overwrite, 'skip_uavsar': skip_uavsar,
                      'keep_downloads': keep_downloads, 'max_uavsar': max_uavsar,
                      'work_dir': str(work_dir or Path('work')),
                      'inventory': {**file_identity(inventory_path),
                                    'sha256': hashlib.sha256(inventory_raw).hexdigest(),
                                    'identity_method': 'sha256_of_consumed_inventory_bytes'}}

    if local_dir is None:
        try:
            _auth = earthaccess_login()
            configure_gdal_for_earthdata(_auth)
            # Reused for the in-situ CSVs, which are plain HTTP rather than
            # GDAL reads and so need the session object itself.
            lidar_session = _auth.get_session()
        except ImportError as exc:
            # Not a credential problem. This machine has several Python
            # installs and only one carries the dependencies, so a wrapper or
            # scheduled task can easily launch the wrong interpreter.
            log.error("A required package is missing: %s", exc)
            log.error("")
            log.error("This interpreter cannot import the pipeline's "
                      "dependencies:")
            log.error("  %s", sys.executable)
            log.error("Activate the conda environment, or run with an "
                      "interpreter that has")
            log.error("earthaccess, h5py, rasterio and numpy installed.")
            return 4
        except Exception as exc:  # noqa: BLE001 -- turn this into advice
            log.error("Earthdata authentication failed: %s", exc)
            log.error("")
            log.error("Metadata search needs no credentials, so --mode preflight")
            log.error("still works. Reading pixels does. To fix:")
            log.error("  1. Confirm the password at https://urs.earthdata.nasa.gov")
            log.error("     (Earthdata passwords expire and must be reset.)")
            log.error("  2. Rewrite the netrc entry for %s, or set", EDL_HOST)
            log.error("     EARTHDATA_USERNAME and EARTHDATA_PASSWORD.")
            log.error("  3. chmod 600 the netrc file; earthaccess rejects a")
            log.error("     world-readable one.")
            log.error("  4. Accept the UAVSAR end-user agreement once in a")
            log.error("     browser at https://search.asf.alaska.edu before any")
            log.error("     UAVSAR download will succeed.")
            return 3

    lidar_session = locals().get("lidar_session")
    plans = plan_sites(site_keys, inventory, out_dir, local_dir)
    if not plans:
        log.error("nothing to build")
        return 2

    failed: list[str] = []
    for plan in plans:
        log.info("-" * 62)
        log.info("writing %s", plan.path)
        try:
            written = build_site_lidar(plan, local_dir, overwrite,
                                       session=lidar_session, run_parameters=run_parameters)
            log.info("  %d LiDAR array(s) written", written)
        except Exception as exc:  # noqa: BLE001 -- keep going, report at the end
            failed.append(plan.site.key)
            log.error("  %s FAILED (%s: %s); continuing with the other sites",
                      plan.site.key, type(exc).__name__, exc)

    skipped = [k for k in site_keys if k not in {p.site.key for p in plans}]
    if skipped:
        log.warning("sites skipped before writing: %s", ", ".join(skipped))
    if failed:
        log.error("sites that failed while writing: %s", ", ".join(failed))

    uavsar_failed = 0
    if skip_uavsar:
        log.info("=" * 62)
        log.info("UAVSAR stage skipped (--skip-uavsar)")
    elif plans:
        uavsar_failed = build_uavsar(
            plans, inventory, work_dir or Path("work"),
            keep_downloads=keep_downloads, overwrite=overwrite,
            limit=max_uavsar,
        )

    return 1 if (failed or skipped or uavsar_failed) else 0


def archive_content_key(product: dict[str, Any], grid: CommonGrid) -> str:
    """Identity of one archive as written onto one grid."""
    return f"{product['file_id']}@{grid_fingerprint(grid)}"


def archive_already_ingested(path: Path, level: str, group_name: str,
                             product: dict[str, Any], grid: CommonGrid) -> bool:
    """Whether this archive is fully written into this site file already.

    Checked before downloading, not after: re-fetching several gigabytes to
    discover the arrays are already present makes a resumed run as expensive as
    a fresh one.
    """
    import h5py

    if not path.exists():
        return False
    try:
        with h5py.File(path, "r") as h5:
            node = h5.get(uavsar_group_path(level, group_name))
            if node is None:
                return False
            return (bool(node.attrs.get("ingest_complete", False))
                    and str(node.attrs.get("ingest_content_key", ""))
                    == archive_content_key(product, grid))
    except OSError:
        return False


def _archive_input_attrs(product, path):
    return {'source_file_identity_json': json.dumps(file_identity(path), sort_keys=True),
            'source_checksum_status': product.get('source_checksum_status', 'not_verified_in_this_ingest'),
            'source_md5_catalog': product.get('source_md5_catalog', product.get('md5sum') or ''),
            'source_md5_verified': product.get('source_md5_verified', ''),
            'source_md5_observed': product.get('source_md5_observed', ''),
            'source_md5_mismatch_accepted': product.get('md5_mismatch_accepted', '')}


def ingest_uavsar_dem_tiff(h5, product: dict[str, Any], group_name: str,
                           grid: CommonGrid, zip_path: Path,
                           overwrite: bool = False) -> int:
    """Write the projection DEM, which ships as a GeoTIFF rather than a `.grd`.
    This terrain model geocodes the radar; it is a separate archive product
    from science/LIDAR/DEM, with its own source metadata."""
    import contextlib
    import tempfile
    import zipfile

    import numpy as np
    import rasterio

    group = h5.require_group(uavsar_group_path("DEM_TIFF", group_name))
    set_attrs(group, {
        **_archive_input_attrs(product, zip_path),
        "original_product_id": product["scene_name"],
        "asf_file_id": product["file_id"],
        "acquisition_dates": [d for d in (product.get("date_ref"),
                                          product.get("date_sec")) if d],
        "source_url": product["url"],
        "description": (
            "Projection DEM used during UAVSAR processing. "
            "A separate archive product from science/LIDAR/DEM."
        ),
    })

    content_key = f"{product['file_id']}@{grid_fingerprint(grid)}"
    if not overwrite and array_exists(group, "elevation", grid, content_key):
        log.info("    DEM_TIFF already present; skipped")
        return 0

    @contextlib.contextmanager
    def geotiff_path():
        """Yield a readable GeoTIFF path, zipped or not.

        The two flavours of DEM_TIFF do not ship the same way: repeat-pass
        products arrive as `..._hgt_grd_tiff.zip`, while the single-pass PolSAR
        products arrive as a bare `..._hgt.tif`. Assuming a zip makes the
        latter fail with BadZipFile.
        """
        if zipfile.is_zipfile(zip_path):
            with zipfile.ZipFile(zip_path) as zf:
                tifs = [n for n in zf.namelist()
                        if n.lower().endswith((".tif", ".tiff"))]
                if not tifs:
                    raise ValueError(
                        f"{zip_path.name}: no GeoTIFF inside "
                        f"(members: {zf.namelist()[:5]})"
                    )
                if len(tifs) > 1:
                    log.info("    %s: %d GeoTIFFs, using %s",
                             zip_path.name, len(tifs), Path(tifs[0]).name)
                with tempfile.TemporaryDirectory() as tmp:
                    yield Path(zf.extract(tifs[0], tmp)), Path(tifs[0]).name
        else:
            yield zip_path, zip_path.name

    with geotiff_path() as (path, member_name):
        with rasterio.open(path) as src:
            windowed = read_windowed(src, grid)
            if windowed is None:
                log.warning("    DEM_TIFF does not overlap the grid; skipped")
                return 0
            data, transform, nodata = windowed
            src_crs = src.crs

    out = reproject_array(data, src_crs, transform, grid,
                          RESAMPLING["dem_tiff"], src_nodata=nodata)
    out, clean_stats = clean_array(out, "dem_tiff")
    if int(np.isfinite(out).sum()) == 0:
        log.warning("    DEM_TIFF reprojected to all-nodata; skipped")
        return 0

    write_array(group, "elevation", out,
                description=DESCRIPTIONS["dem_tiff"],
                resampling_method=RESAMPLING["dem_tiff"],
                source="ASF UAVSAR DEM_TIFF", grid=grid,
                content_key=content_key, overwrite=overwrite,
                extra={"source_member": member_name,
                       **uavsar_quantity_attrs("dem_tiff"),
                       **cleaning_attrs(clean_stats, "dem_tiff")})
    set_attrs(group, {
        "ingest_complete": True,
        "ingest_content_key": archive_content_key(product, grid),
        "array_count": 1,
    })
    log.info("    DEM_TIFF elevation written")
    return 1


def build_uavsar(plans: Sequence[SitePlan], inventory: dict[str, Any],
                 work_dir: Path, keep_downloads: bool = False,
                 overwrite: bool = False, limit: int | None = None) -> int:
    """Download each unique flight once, clip it into every site that needs it.

    README decision 8: one download per unique product, clipped separately per
    overlapping site, and the raw file deleted only after every site that needs
    it has been written. Banner Summit, Dry Creek and Mores Creek share flight
    lines, so this avoids re-fetching multi-gigabyte archives.
    """
    import h5py

    by_site = {p.site.key: p for p in plans}
    needed: dict[str, dict[str, Any]] = {}
    consumers: dict[str, list[str]] = {}
    for key in by_site:
        for product in inventory["sites"][key].get("uavsar_needed", []):
            needed.setdefault(product["file_id"], product)
            consumers.setdefault(product["file_id"], []).append(key)

    order = sorted(needed, key=lambda fid: needed[fid].get("bytes_") or 0)
    if limit is not None:
        order = order[:limit]
        log.warning("--max-uavsar %d: processing only the %d smallest products",
                    limit, len(order))

    log.info("=" * 62)
    if not order:
        log.info("UAVSAR: nothing to fetch for the selected sites")
        return 0

    total_bytes = sum(needed[f].get("bytes_") or 0 for f in order)
    log.info("UAVSAR: %d unique product(s), %.2f GB to download",
             len(order), total_bytes / 1e9)

    # Created only once there is something to download, so a run with no
    # matched products -- and the offline test suite -- never touches the network.
    session = asf_session()
    written = failed = 0

    for i, file_id in enumerate(order, 1):
        product = needed[file_id]
        sites = consumers[file_id]
        log.info("-" * 62)
        log.info("[%d/%d] %s  %s", i, len(order), product["level"],
                 product["scene_name"])
        log.info("    needed by: %s", ", ".join(sites))

        if not overwrite and all(
            archive_already_ingested(
                by_site[k].path, product["level"],
                group_name_for(by_site[k].site, _product_from_json(product)),
                product, by_site[k].grid)
            for k in sites
        ):
            log.info("    already ingested at every site; no download needed")
            continue

        try:
            zip_path = download_uavsar(product, work_dir, session)
        except Exception as exc:  # noqa: BLE001
            log.error("    download FAILED (%s: %s); continuing",
                      type(exc).__name__, exc)
            failed += 1
            continue

        for key in sites:
            plan = by_site[key]
            group_name = group_name_for(plan.site, _product_from_json(product))
            try:
                with h5py.File(plan.path, "a") as h5:
                    if product["level"] == "DEM_TIFF":
                        written += ingest_uavsar_dem_tiff(
                            h5, product, group_name, plan.grid, zip_path,
                            overwrite)
                    else:
                        written += ingest_uavsar_archive(
                            h5, product, group_name, plan.grid, zip_path,
                            overwrite)
            except Exception as exc:  # noqa: BLE001
                log.error("    %s FAILED (%s: %s); continuing",
                          key, type(exc).__name__, exc)
                failed += 1

        if not keep_downloads:
            try:
                zip_path.unlink()
                log.debug("    removed %s", zip_path.name)
            except OSError as exc:
                log.warning("    could not remove %s: %s", zip_path.name, exc)

    log.info("=" * 62)
    log.info("UAVSAR: %d array(s) written, %d failure(s)", written, failed)
    return failed


def _product_from_json(product: dict[str, Any]) -> UavsarProduct:
    """Rebuild a UavsarProduct from its inventory record, for group naming."""
    return UavsarProduct(
        scene_name=product["scene_name"], file_id=product["file_id"],
        level=product["level"], url=product["url"],
        filename=product["filename"], bytes_=int(product.get("bytes_") or 0),
        md5sum=str(product.get("md5sum") or ""),
        date_ref=parse_iso_date(product.get("date_ref")),
        date_sec=parse_iso_date(product.get("date_sec")),
        footprint_wkt=product.get("footprint_wkt", ""),
    )


def file_md5(path: Path, chunk: int = 8 << 20) -> str:
    import hashlib

    digest = hashlib.md5()  # noqa: S324 -- integrity check, not security
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_crc_ok(path: Path) -> tuple[bool, str]:
    """CRC-check every member of a zip. Returns (ok, detail).

    A zip stores a CRC-32 per member, computed by whoever built the archive.
    Checking those verifies the same thing an md5 of the whole file does --
    that the bytes are the bytes the producer wrote -- but per member and
    independently of any catalogue metadata. That independence is the point:
    it is the check to reach for when the published md5 is itself suspect.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.namelist()
            if not members:
                return False, "archive is empty"
            broken = zf.testzip()
            if broken is not None:
                return False, f"CRC failed on member {broken}"
            return True, f"{len(members)} member(s) CRC-verified"
    except zipfile.BadZipFile as exc:
        return False, f"not a valid zip: {exc}"
    except Exception as exc:                          # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def verify_download(dest: Path, expected_bytes: int, expected_md5: str | None
                    ) -> str | None:
    """Why `dest` is unusable, or None if it is sound."""
    if not dest.exists():
        return "file is missing"
    actual = dest.stat().st_size
    if expected_bytes and actual != expected_bytes:
        return f"{actual:,} bytes, expected {expected_bytes:,}"
    if expected_md5:
        got = file_md5(dest)
        if got.lower() != expected_md5.lower():
            return f"md5 {got} does not match {expected_md5}"
    return None


def download_uavsar(product: dict[str, Any], work_dir: Path, session) -> Path:
    """Fetch one UAVSAR archive, reusing an existing complete download.

    The integrity check lives *inside* the retried block on purpose.
    `asf.download_url` returns normally on a truncated transfer -- ASF and
    CloudFront cut connections often enough that this is routine, not
    exceptional -- so a check placed after the retry would see the failure once
    and give up. Checking inside means a short or corrupt file is simply
    downloaded again.
    """
    import asf_search as asf

    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / product["filename"]
    expected = int(product.get("bytes_") or 0)
    expected_md5 = product.get("md5sum") or None

    def record_checksum(mismatch=None):
        product['source_md5_catalog'] = expected_md5 or ''
        product['source_checksum_status'] = ('verified_md5' if expected_md5 else
                                              'size_only_not_content_verified')
        if mismatch:
            product['source_checksum_status'] = 'zip_crc_passed_catalog_md5_mismatch'
            product['source_md5_observed'] = mismatch.split()[1]
            product.pop('source_md5_verified', None)
        else:
            product.pop('source_md5_observed', None)
            product.pop('md5_mismatch_accepted', None)
            if expected_md5:
                product['source_md5_verified'] = expected_md5.lower()
            else:
                product.pop('source_md5_verified', None)

    problem = verify_download(dest, expected, expected_md5)
    if problem is None and dest.exists():
        record_checksum()
        log.info("    already downloaded (%.2f GB)", dest.stat().st_size / 1e9)
        return dest
    if dest.exists():
        log.warning("    discarding existing %s: %s", dest.name, problem)
        # Clear it now rather than at rename time. If the destination cannot be
        # removed -- a crashed job still holding the handle, a stale NFS lock --
        # then downloading first would transfer the whole archive several times
        # only to fail on the final rename.
        try:
            dest.unlink()
        except OSError as exc:
            raise RuntimeError(
                f"{dest.name} is unusable ({problem}) and cannot be removed: "
                f"{exc}. Another process may still hold it open. Delete it and "
                "re-run."
            ) from exc

    log.info("    downloading %.2f GB%s ...", expected / 1e9,
             " (md5 will be checked)" if expected_md5 else "")

    # Download to a scratch name and rename only once the file is verified.
    # A killed job then leaves a `.part` file that is obviously incomplete
    # rather than a full-looking archive, and the final name never has to be
    # unlinked first -- which matters when a previous crashed run still holds
    # a handle on it.
    part_name = product["filename"] + ".part"
    part = work_dir / part_name

    def fetch_and_check() -> Path:
        part.unlink(missing_ok=True)
        asf.download_url(url=product["url"], path=str(work_dir),
                         filename=part_name, session=session)
        bad = verify_download(part, expected, expected_md5)
        accepted_mismatch = None
        if bad and bad.startswith("md5 "):
            # A wrong md5 that reproduces byte-for-byte across independent
            # downloads is not a corrupt transfer -- corruption would differ
            # each time. Measured on
            # fraser_23306_21020-026_21021-004_0006d_s01_L090_01: all three
            # products returned the identical "wrong" digest on four attempts
            # each, at exactly the advertised length. That is stale catalogue
            # metadata, not damaged data.
            #
            # So fall through to the archive's own CRCs, which do not depend on
            # the catalogue at all. If those pass, the bytes are sound and the
            # download is accepted with the discrepancy recorded in provenance
            # rather than discarded.
            ok, detail = zip_crc_ok(part)
            if ok:
                log.warning("    %s: published md5 does not match, but the "
                            "archive is internally consistent (%s) at the "
                            "advertised %d bytes. Accepting; recorded in "
                            "provenance.", product["filename"], detail, expected)
                product["md5_mismatch_accepted"] = bad
                accepted_mismatch = bad
                bad = None
            else:
                log.error("    %s: md5 mismatch AND %s", product["filename"], detail)
        if bad:
            raise RuntimeError(f"{product['filename']}: {bad}")
        record_checksum(accepted_mismatch)
        import os

        os.replace(part, dest)      # atomic on both POSIX and Windows
        return dest

    try:
        return retry(fetch_and_check, attempts=4,
                     what=f"download {product['filename']}")
    finally:
        part.unlink(missing_ok=True)


def ingest_uavsar_archive(h5, product: dict[str, Any], group_name: str,
                          grid: CommonGrid, zip_path: Path,
                          overwrite: bool = False) -> int:
    """Clip one UAVSAR archive onto the common grid and write it.

    Returns the number of arrays written.
    """
    import zipfile

    import numpy as np

    level = product["level"]
    skip = set(UAVSAR_DUPLICATE_SUBPRODUCTS.get(level, ()))

    if not zipfile.is_zipfile(zip_path):
        raise ValueError(
            f"{zip_path.name}: expected a zip archive for {level} but the file "
            "is not one. UAVSAR ships some products as bare files."
        )

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        ann_names = [n for n in names if n.endswith(".ann")]
        if not ann_names:
            raise ValueError(f"{zip_path.name}: no .ann, cannot georeference")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ann_path = Path(zf.extract(ann_names[0], tmp))
            annotation_sha256 = hashlib.sha256(ann_path.read_bytes()).hexdigest()
            ann = read_annotation(ann_path)

        members: list[tuple[str, str, str]] = []
        for name in names:
            parsed = parse_grd_member(name)
            if parsed is None:
                continue
            pol, sub = parsed
            if sub in skip:
                log.debug("    %s: skipping duplicate %s", zip_path.name, sub)
                continue
            if sub not in GRD_DTYPE:
                log.warning("    %s: unrecognised sub-product %r; skipped",
                            zip_path.name, sub)
                continue
            members.append((name, pol, sub))

    if not members:
        log.warning("    %s: no usable .grd members", zip_path.name)
        return 0

    group = h5.require_group(uavsar_group_path(level, group_name))
    set_attrs(group, {
        **_archive_input_attrs(product, zip_path),
        'source_annotation_filename': ann_names[0],
        'source_annotation_sha256': annotation_sha256,
        "original_product_id": product["scene_name"],
        "asf_file_id": product["file_id"],
        "processing_level": level,
        "acquisition_dates": [d for d in (product.get("date_ref"),
                                          product.get("date_sec")) if d],
        "source_url": product["url"],
        "source_bytes": int(product.get("bytes_") or 0),
        "clip_buffer_m": CLIP_BUFFER_M,
        "uavsar_native_resolution_m": list(UAVSAR_NATIVE_RES_M),
        "description": (
            f"One UAVSAR {level} acquisition, clipped to this site and "
            "resampled onto the common grid. The polarizations present vary by "
            "acquisition: some carry the full HH/HV/VH/VV set, others HH alone."
        ),
    })

    written = 0
    expected_arrays = len(members)
    for name, pol, sub in sorted(members, key=lambda m: (m[1], m[2])):
        pol_group = group.require_group(pol)
        content_key = f"{product['file_id']}:{Path(name).name}@{grid_fingerprint(grid)}"
        if not overwrite and array_exists(pol_group, sub, grid, content_key):
            log.info("    %-4s %-4s already present; skipped", pol, sub)
            continue

        layout = grd_layout(ann, sub)
        got = read_grd_from_zip(zip_path, name, layout, grid)
        if got is None:
            log.warning("    %-4s %-4s does not overlap the grid; skipped",
                        pol, sub)
            continue
        block, transform = got

        method = RESAMPLING[sub if sub in RESAMPLING else "amp"]
        out = reproject_array(block, "EPSG:4326", transform, grid, method)
        out, clean_stats = clean_array(out, sub)
        valid = int(np.isfinite(out.real).sum())
        if valid == 0:
            log.warning("    %-4s %-4s reprojected to all-nodata; skipped",
                        pol, sub)
            continue

        write_array(
            pol_group, sub, out,
            description=DESCRIPTIONS.get(sub, f"UAVSAR {sub} array."),
            resampling_method=method,
            source=f"ASF UAVSAR {level}",
            grid=grid,
            content_key=content_key,
            overwrite=overwrite,
            extra={
                **uavsar_quantity_attrs(sub, ann),
                "polarization": pol,
                "source_member": Path(name).name,
                "native_rows": layout.rows,
                "native_cols": layout.cols,
                "native_step_deg": [abs(layout.dlon), abs(layout.dlat)],
                **cleaning_attrs(clean_stats, sub, is_complex=out.dtype.kind == "c"),
            },
        )
        log.info("    %-4s %-4s %.1f%% valid", pol, sub, 100.0 * valid / out.size)
        written += 1

    # Stamp the group only when every member of the archive is accounted for,
    # so a resumed run can skip the download entirely. A partial ingest leaves
    # no stamp and is retried.
    present = sum(1 for _, pol, sub in members
                  if pol in group and sub in group[pol])
    if present == expected_arrays:
        set_attrs(group, {
            "ingest_complete": True,
            "ingest_content_key": archive_content_key(product, grid),
            "array_count": present,
        })

    return written


def run_inspect_uavsar(inventory_path: Path, work_dir: Path,
                       site_key: str | None = None) -> int:
    """Download the single smallest matched UAVSAR product and describe it.

    The contents of a UAVSAR archive -- how many `.grd` files, how they are
    named, which annotation keys they use, whether amplitude ships as one array
    or two -- decide the shape of the ingester. Guessing produces code that
    cannot be trusted, so this reports the facts from one real product first.
    """
    if not inventory_path.exists():
        log.error("no inventory at %s -- run --mode preflight first", inventory_path)
        return 2
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))

    candidates: list[dict[str, Any]] = []
    for key, entry in inventory["sites"].items():
        if site_key and key != site_key:
            continue
        for p in entry.get("uavsar_needed", []):
            if p["level"] == "INTERFEROMETRY_GRD":
                candidates.append(p)
    if not candidates:
        log.error("no INTERFEROMETRY_GRD products in the inventory")
        return 2

    product = min(candidates, key=lambda p: p["bytes_"])
    log.info("smallest matched interferogram: %s", product["scene_name"])
    log.info("  %s", product["url"])
    log.info("  %.2f GB", product["bytes_"] / 1e9)

    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / product["filename"]
    if dest.exists():
        log.info("  already downloaded: %s", dest)
    else:
        import asf_search as asf

        session = asf_session()
        log.info("  downloading to %s ...", work_dir)
        retry(lambda: asf.download_url(url=product["url"], path=str(work_dir),
                                       filename=product["filename"],
                                       session=session),
              attempts=4, what="ASF download")

    describe_uavsar_archive(dest)
    return 0


def describe_uavsar_archive(zip_path: Path) -> None:
    """Print the real structure of a downloaded UAVSAR archive."""
    import zipfile

    log.info("=" * 62)
    log.info("archive: %s (%.2f GB)", zip_path.name, zip_path.stat().st_size / 1e9)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        log.info("  %d entries", len(names))
        by_ext: dict[str, list[str]] = {}
        for n in names:
            by_ext.setdefault(".".join(Path(n).suffixes), []).append(n)
        for ext, items in sorted(by_ext.items()):
            log.info("  %-14s %3d  e.g. %s", ext or "<none>", len(items),
                     Path(items[0]).name)

        ann_names = [n for n in names if n.endswith(".ann")]
        if not ann_names:
            log.warning("  no .ann file: the binaries cannot be georeferenced")
            return
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            ann_path = Path(zf.extract(ann_names[0], tmp))
            ann = read_annotation(ann_path)
        log.info("  annotation %s -> %d keys", Path(ann_names[0]).name, len(ann))
        for prefix in sorted({"grd", "grd_phs"}):
            keys = [k for k in ann if k.startswith(prefix + ".")]
            if keys:
                log.info("    %s.* : %s", prefix, ", ".join(sorted(keys)[:8]))
        for sub in ("int", "unw", "cor", "hgt"):
            try:
                layout = grd_layout(ann, sub)
            except KeyError as exc:
                log.warning("    %s: %s", sub, exc)
                continue
            log.info("    %-4s %5d x %5d  %s  step %.3e x %.3e deg",
                     sub, layout.rows, layout.cols, layout.dtype,
                     layout.dlon, layout.dlat)


# =====================================================================
# SECTION 11 -- PREFLIGHT
# =====================================================================


def run_preflight(site_keys: Sequence[str], out_path: Path) -> dict[str, Any]:
    cmr_cache: dict[str, list] = {}
    inventory: dict[str, Any] = {
        "version": VERSION,
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config": {
            "match_window_days": MATCH_WINDOW_DAYS,
            "clip_buffer_m": CLIP_BUFFER_M,
            "target_res_m": TARGET_RES_M,
            "uavsar_levels": list(UAVSAR_LEVELS),
        },
        "sites": {},
    }

    for key in site_keys:
        site = SITES[key]
        log.info("=" * 62)
        log.info("%s (%s, %s)", site.name, key, site.state)

        granules = discover_lidar(site, cmr_cache)
        if not granules:
            log.error("  no LiDAR granules found -- check site config")
            inventory["sites"][key] = {"error": "no lidar granules"}
            continue

        by_product: dict[str, list[LidarGranule]] = {}
        for g in granules:
            by_product.setdefault(g.product, []).append(g)
        for prod in ("DEM", "SD", "VH"):
            gs = by_product.get(prod, [])
            log.info("  %-3s %d granule(s): %s", prod, len(gs),
                     ", ".join(g.date_key for g in gs) or "-")

        if site.reference_product not in by_product:
            log.error("  reference product %s has no granule -- grid cannot be "
                      "derived", site.reference_product)

        poly = site_search_polygon(granules)
        products = search_uavsar(poly)
        log.info("  UAVSAR products intersecting site: %d", len(products))

        matches, needed = match_site(site, granules, products)
        n_real = sum(1 for m in matches if m.verdict == "match")
        matched_granules = {m.lidar_filename for m in matches if m.verdict == "match"}
        total_bytes = sum(p.bytes_ for p in needed)

        log.info("  matched pairs: %d across %d LiDAR granule(s)",
                 n_real, len(matched_granules))
        log.info("  unique UAVSAR products needed: %d (%.2f GB)",
                 len(needed), total_bytes / 1e9)

        inventory["sites"][key] = {
            "name": site.name,
            "state": site.state,
            "expected_epsg": site.expected_epsg,
            "reference_product": site.reference_product,
            "note": site.note,
            "search_polygon_wkt": poly.wkt,
            "lidar": [g.to_json() for g in granules],
            "uavsar_needed": [p.to_json() for p in needed],
            "matches": [asdict(m) for m in matches],
            "summary": {
                "lidar_granules": len(granules),
                "lidar_by_product": {k: len(v) for k, v in sorted(by_product.items())},
                "uavsar_intersecting": len(products),
                "uavsar_needed": len(needed),
                "matched_pairs": n_real,
                "matched_lidar_granules": len(matched_granules),
                "download_bytes": total_bytes,
            },
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    log.info("=" * 62)
    log.info("inventory written: %s", out_path)
    return inventory


def print_summary(inventory: dict[str, Any]) -> None:
    rows = []
    for key, s in inventory["sites"].items():
        if "error" in s:
            rows.append((s.get("name", key), "-", "-", "-", "-", "-", "ERROR"))
            continue
        c = s["summary"]
        bp = c["lidar_by_product"]
        rows.append((
            s["name"],
            str(bp.get("DEM", 0)),
            str(bp.get("SD", 0)),
            str(bp.get("VH", 0)),
            str(c["matched_lidar_granules"]),
            str(c["uavsar_needed"]),
            f"{c['download_bytes'] / 1e9:.1f}",
        ))

    hdr = ("Site", "DEM", "SD", "VH", "Matched", "UAVSAR", "GB")
    widths = [max(len(hdr[i]), *(len(r[i]) for r in rows)) for i in range(len(hdr))]
    print("\n" + "  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))

    # Deduplicate shared flights across the whole run.
    seen: dict[str, int] = {}
    for s in inventory["sites"].values():
        for p in s.get("uavsar_needed", []):
            seen[p["file_id"]] = p["bytes_"]
    print(f"\nUnique UAVSAR products across all sites: {len(seen)}")
    print(f"Deduplicated download volume: {sum(seen.values()) / 1e9:.2f} GB")


# =====================================================================
# SECTION 10 -- CLI
# =====================================================================


def run_clean(site_keys: Sequence[str], out_dir: Path,
              fill_gaps: bool = True) -> int:
    """Apply artifact removal to an archive that was built before it existed.

    Writes a fresh file and swaps it in on success. Rewriting in place would
    be worse on both counts: HDF5 never reclaims the space of a replaced
    dataset, and a crash part-way would leave the original half-cleaned.
    """
    import os

    import h5py
    import numpy as np

    total_removed = total_filled = 0
    failures = 0

    for key in site_keys:
        path = out_dir / f"{key}.h5"
        if not path.exists():
            log.warning("%s: no such file; skipped", path.name)
            continue
        tmp = path.with_suffix(".h5.cleaning")
        tmp.unlink(missing_ok=True)
        log.info("=" * 62)
        log.info("cleaning %s", path.name)

        removed = filled = touched = 0
        input_identity = file_identity(path)
        try:
            with h5py.File(path, "r") as src, h5py.File(tmp, "w") as dst:
                parent_lineage = read_lineage(src['identification'].attrs)
                clean_parameters = {'fill_gaps': fill_gaps, 'fill_sentinels': FILL_SENTINELS,
                                    'plausible_ranges': PLAUSIBLE_RANGE,
                                    'spike_tolerances': SPIKE_TOLERANCE,
                                    'gap_fill_passes': MAX_GAP_PX,
                                    'compression': COMPRESSION, 'compression_level': COMPRESSION_LEVEL,
                                    'chunk_edge': CHUNK_EDGE,
                                    'passthrough': ['matches'], 'scope': 'science arrays only'}
                for top in ("identification", "matches"):
                    if top in src:
                        src.copy(top, dst)

                grid_shape = tuple(int(v) for v in
                                   src["identification"].attrs["common_grid_shape"])

                def kind_of(name: str) -> str:
                    leaf = name.rsplit("/", 1)[-1]
                    if leaf == "elevation":
                        return "DEM" if name.startswith("LIDAR") else "dem_tiff"
                    if leaf == "snow_depth":
                        return "SD"
                    if leaf == "veg_height":
                        return "VH"
                    return leaf

                def walk(name, obj):
                    nonlocal removed, filled, touched
                    if isinstance(obj, h5py.Group):
                        set_attrs(dst.require_group("science/" + name), dict(obj.attrs))
                        return
                    if not isinstance(obj, h5py.Dataset):
                        return
                    data = obj[...]
                    cleaned, stats = clean_array(data, kind_of(name), fill_gaps)
                    group = dst.require_group("science/" + name.rsplit("/", 1)[0])
                    leaf = name.rsplit("/", 1)[-1]
                    ds = group.create_dataset(
                        leaf, data=cleaned, chunks=choose_chunks(grid_shape),
                        compression=COMPRESSION, compression_opts=COMPRESSION_LEVEL,
                        shuffle=True)
                    for k, v in obj.attrs.items():
                        if not k.startswith(("value_", "cleaning_")):
                            ds.attrs[k] = v
                    set_attrs(ds, cleaning_attrs(
                        stats, kind_of(name), fill_gaps=fill_gaps,
                        is_complex=data.dtype.kind == "c", stage="archive_recleaning"))
                    finite_mask = np.isfinite(cleaned.real)
                    finite = int(finite_mask.sum())
                    set_attrs(ds, {"valid_pixel_count": finite,
                                   "valid_fraction": finite / cleaned.size
                                   if cleaned.size else 0.0})
                    set_attrs(ds, statistics_attrs(cleaned, finite_mask, percentiles=True))
                    record_processing_history(ds.attrs, "archive_recleaning", obj.attrs)
                    store_lineage(ds.attrs, new_record('archive_recleaning_array', _BUILD_SOURCES,
                        {**clean_parameters, 'kind': kind_of(name), 'dataset_path': ds.name,
                         'grid_shape': grid_shape},
                        parent={'file': input_identity, 'dataset_path': obj.name,
                                'lineage': read_lineage(obj.attrs)}))
                    gone = (stats["sentinels"] + stats["out_of_range"]
                            + stats["spikes"])
                    removed += gone
                    filled += stats["gaps_filled"]
                    touched += 1
                    if gone or stats["gaps_filled"]:
                        log.info("    %-52s -%d +%d", name, gone,
                                 stats["gaps_filled"])

                # Recreate the group tree so empty parents survive the copy.
                set_attrs(dst.require_group("science"), dict(src["science"].attrs))
                src["science"].visititems(walk)
                store_lineage(dst['identification'].attrs, new_record('archive_recleaning', _BUILD_SOURCES,
                    clean_parameters, parent={'file': input_identity, 'lineage': parent_lineage}))
        except Exception as exc:  # noqa: BLE001 -- one site must not stop the rest
            log.error("  %s FAILED (%s: %s); original left untouched",
                      key, type(exc).__name__, exc)
            tmp.unlink(missing_ok=True)
            failures += 1
            continue

        os.replace(tmp, path)
        log.info("  %d array(s): %d value(s) removed, %d gap(s) filled",
                 touched, removed, filled)
        total_removed += removed
        total_filled += filled

    log.info("=" * 62)
    log.info("cleaned: %d value(s) removed, %d gap(s) interpolated, %d failure(s)",
             total_removed, total_filled, failures)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    global CHUNK_EDGE

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--mode",
        choices=("preflight", "build", "verify", "inspect-uavsar", "check-auth",
                 "clean"),
        default="preflight",
        help="preflight: discover and match, no auth needed. "
             "build: download, reproject and write. "
             "verify: reopen written files and check invariants. "
             "inspect-uavsar: fetch one product and report its real layout. "
             "check-auth: report whether credentials work, printing none. "
             "clean: apply artifact removal to an already-built archive.",
    )
    ap.add_argument("--sites", nargs="*", default=None,
                    help="site keys to process (default: all)")
    ap.add_argument("--inventory", type=Path, default=Path("inventory.json"))
    # Defaults come from the environment when set. The archive should not live
    # in a cloud-synced folder: OneDrive locks multi-gigabyte files while it
    # uploads them, which surfaces as an HDF5 "unable to lock file" error.
    ap.add_argument("--out-dir", type=Path,
                    default=Path(os.environ.get("SNOWEX_OUT_DIR", "out")),
                    help="where the per-site .h5 files are written "
                         "(default $SNOWEX_OUT_DIR or ./out)")
    ap.add_argument("--work-dir", type=Path,
                    default=Path(os.environ.get("SNOWEX_WORK_DIR", "work")),
                    help="scratch space for downloads "
                         "(default $SNOWEX_WORK_DIR or ./work)")
    ap.add_argument("--local-lidar", type=Path, default=None,
                    help="read LiDAR from this directory instead of the DAAC; "
                         "lets build run with no network and no credentials")
    ap.add_argument("--overwrite", action="store_true",
                    help="rewrite arrays that are already present; without "
                         "it a re-run resumes and leaves finished arrays "
                         "alone, because HDF5 does not reclaim the space of "
                         "a deleted dataset")
    ap.add_argument("--chunk-edge", type=int, default=None,
                    help=f"HDF5 chunk edge in pixels (default {CHUNK_EDGE}). "
                         "Changing this on an existing archive requires a full "
                         "rebuild, since every array must be rewritten.")
    ap.add_argument("--no-fill-gaps", action="store_true",
                    help="clean: remove artifacts but do not interpolate "
                         "small holes")
    ap.add_argument("--skip-uavsar", action="store_true",
                    help="build the LiDAR half only")
    ap.add_argument("--keep-downloads", action="store_true",
                    help="do not delete each UAVSAR archive after clipping")
    ap.add_argument("--max-uavsar", type=int, default=None,
                    help="process only the N smallest UAVSAR products; for "
                         "trying the pipeline without fetching 175 GB")
    ap.add_argument("--allow-empty", action="store_true",
                    help="verify: do not treat an all-nodata array as a problem")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)

    if args.chunk_edge is not None:
        if args.chunk_edge < 1:
            ap.error("--chunk-edge must be positive")
        CHUNK_EDGE = args.chunk_edge
        log.info("chunk edge set to %d px", CHUNK_EDGE)

    keys = args.sites or list(SITES)
    unknown = [k for k in keys if k not in SITES]
    if unknown:
        ap.error(f"unknown site(s): {', '.join(unknown)}. "
                 f"Available: {', '.join(SITES)}")

    if args.mode == "clean":
        return run_clean(keys, args.out_dir, fill_gaps=not args.no_fill_gaps)

    if args.mode == "check-auth":
        return run_check_auth()

    if args.mode == "preflight":
        inv = run_preflight(keys, args.inventory)
        print_summary(inv)
        return 0

    if args.mode == "build":
        return run_build(keys, args.inventory, args.out_dir, args.local_lidar,
                         overwrite=args.overwrite, work_dir=args.work_dir,
                         skip_uavsar=args.skip_uavsar,
                         keep_downloads=args.keep_downloads,
                         max_uavsar=args.max_uavsar)

    if args.mode == "verify":
        paths = [args.out_dir / f"{k}.h5" for k in keys]
        return 1 if run_verify(paths, strict_empty=not args.allow_empty) else 0

    if args.mode == "inspect-uavsar":
        return run_inspect_uavsar(args.inventory, args.work_dir,
                                  keys[0] if args.sites else None)

    ap.error(f"unhandled mode {args.mode!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
