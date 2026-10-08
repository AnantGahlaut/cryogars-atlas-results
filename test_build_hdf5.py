#!/usr/bin/env python3
"""
Offline unit tests for build_hdf5.py.

stdlib unittest only, so this runs on Borah with nothing but the conda env.
No network, no authentication, no downloads.

    python test_build_hdf5.py
    python -m unittest test_build_hdf5 -v
"""

from __future__ import annotations

import json
import unittest
from datetime import date

import build_hdf5 as B


# ---------------------------------------------------------------------
# Filename parsing -- the off-by-one here silently collapses every site
# into one bucket, so it gets the most coverage.
# ---------------------------------------------------------------------


class TestParseQsiFilename(unittest.TestCase):
    def test_vh_two_distinct_dates(self):
        got = B.parse_qsi_filename("SNEX20_QSI_VH_3M_USIDBS_20200218_20200219.tif")
        self.assertEqual(got["product"], "VH")
        self.assertEqual(got["site_code"], "USIDBS")
        self.assertEqual(got["date_begin"], date(2020, 2, 18))
        self.assertEqual(got["date_end"], date(2020, 2, 19))

    def test_sd_same_day_range(self):
        got = B.parse_qsi_filename("SNEX20_QSI_SD_3M_USIDMC_20210315_20210315.tif")
        self.assertEqual(got["product"], "SD")
        self.assertEqual(got["site_code"], "USIDMC")
        self.assertEqual(got["date_begin"], got["date_end"])

    def test_dem_uses_same_field_positions(self):
        got = B.parse_qsi_filename("SNEX20_QSI_DEM_3M_USCOCP_20210918_20210918.tif")
        self.assertEqual(got["product"], "DEM")
        self.assertEqual(got["site_code"], "USCOCP")

    def test_every_real_site_code_is_recovered(self):
        codes = ["USIDBS", "USIDMC", "USIDDC", "USCOFR",
                 "USCOCP", "USUTLC", "USCOGM", "USIDRC"]
        for c in codes:
            name = f"SNEX20_QSI_VH_3M_{c}_20200218_20200219.tif"
            self.assertEqual(B.parse_qsi_filename(name)["site_code"], c, name)

    def test_rejects_non_qsi_products(self):
        for name in ("SNEX20_GM_Lidar_SD_20200201_20200202_v01.0.tif",
                     "SNEX_HRSI_SD_DEM_CO_GM_DTM_1m_V01.0.tif",
                     "RCEW_DEM_1m.tif",
                     "hollister000001.laz"):
            self.assertIsNone(B.parse_qsi_filename(name), name)

    def test_rejects_unexpected_resolution_token(self):
        # If the token at index 3 is ever not "3M" the field layout has moved
        # and site codes would be read from the wrong position.
        self.assertIsNone(
            B.parse_qsi_filename("SNEX20_QSI_VH_1M_USIDBS_20200218_20200219.tif")
        )

    def test_rejects_truncated_name(self):
        self.assertIsNone(B.parse_qsi_filename("SNEX20_QSI_VH_3M_USIDBS.tif"))


# ---------------------------------------------------------------------
# CMR footprint extraction -- both geometry encodings must work.
# ---------------------------------------------------------------------


def _umm(geometry: dict) -> dict:
    return {"SpatialExtent": {"HorizontalSpatialDomain": {"Geometry": geometry}}}


class TestFootprintFromUmm(unittest.TestCase):
    def test_gpolygon(self):
        umm = _umm({"GPolygons": [{"Boundary": {"Points": [
            {"Longitude": -115.281, "Latitude": 44.1953},
            {"Longitude": -115.092, "Latitude": 44.1923},
            {"Longitude": -115.087, "Latitude": 44.3411},
            {"Longitude": -115.277, "Latitude": 44.3442},
            {"Longitude": -115.281, "Latitude": 44.1953},
        ]}}]})
        fp = B.footprint_from_umm(umm)
        self.assertIsNotNone(fp)
        self.assertTrue(fp.is_valid)
        self.assertGreater(fp.area, 0)

    def test_bounding_rectangle(self):
        umm = _umm({"BoundingRectangles": [{
            "WestBoundingCoordinate": -116.865,
            "EastBoundingCoordinate": -116.683,
            "NorthBoundingCoordinate": 43.3163,
            "SouthBoundingCoordinate": 43.057,
        }]})
        fp = B.footprint_from_umm(umm)
        self.assertIsNotNone(fp)
        west, south, east, north = fp.bounds
        self.assertAlmostEqual(west, -116.865)
        self.assertAlmostEqual(north, 43.3163)

    def test_rcew_dem_contains_reynolds_creek_vh(self):
        # The 2014 ORNL DEM must cover the 2020 vegetation-height survey,
        # otherwise it cannot serve as Reynolds Creek's reference grid.
        dem = B.footprint_from_umm(_umm({"BoundingRectangles": [{
            "WestBoundingCoordinate": -116.865,
            "EastBoundingCoordinate": -116.683,
            "NorthBoundingCoordinate": 43.3163,
            "SouthBoundingCoordinate": 43.057,
        }]}))
        vh = B.footprint_from_umm(_umm({"GPolygons": [{"Boundary": {"Points": [
            {"Longitude": -116.843, "Latitude": 43.0559},
            {"Longitude": -116.709, "Latitude": 43.0557},
            {"Longitude": -116.709, "Latitude": 43.1587},
            {"Longitude": -116.843, "Latitude": 43.159},
            {"Longitude": -116.843, "Latitude": 43.0559},
        ]}}]}))
        # Measured: the 2014 DEM covers 98.8% of the 2020 VH footprint. The VH
        # survey reaches ~145 m further south than the DEM (43.0557 vs 43.0570).
        # Coverage is not total, so the shortfall is asserted explicitly rather
        # than rounded away -- if it ever grows, this test says so.
        coverage = vh.intersection(dem).area / vh.area
        self.assertGreater(coverage, 0.98)
        self.assertLess(coverage, 1.0)

    def test_missing_geometry_returns_none(self):
        self.assertIsNone(B.footprint_from_umm(_umm({})))
        self.assertIsNone(B.footprint_from_umm({}))

    def test_malformed_rectangle_is_skipped_not_fatal(self):
        umm = _umm({"BoundingRectangles": [{"WestBoundingCoordinate": -116.0}]})
        self.assertIsNone(B.footprint_from_umm(umm))

    def test_degenerate_polygon_is_skipped(self):
        umm = _umm({"GPolygons": [{"Boundary": {"Points": [
            {"Longitude": -115.0, "Latitude": 44.0},
            {"Longitude": -115.0, "Latitude": 44.0},
        ]}}]})
        self.assertIsNone(B.footprint_from_umm(umm))


# ---------------------------------------------------------------------
# Date handling
# ---------------------------------------------------------------------


class TestDates(unittest.TestCase):
    def test_iso_with_z(self):
        self.assertEqual(B.parse_iso_date("2021-03-16T18:09:32Z"), date(2021, 3, 16))

    def test_iso_date_only(self):
        self.assertEqual(B.parse_iso_date("2020-02-18"), date(2020, 2, 18))

    def test_none_and_empty_are_none(self):
        # ASF's startTime can be present but None.
        self.assertIsNone(B.parse_iso_date(None))
        self.assertIsNone(B.parse_iso_date(""))

    def test_garbage_is_none(self):
        self.assertIsNone(B.parse_iso_date("not a date"))

    def test_yyyymmdd(self):
        self.assertEqual(B.parse_yyyymmdd("20200218"), date(2020, 2, 18))
        self.assertIsNone(B.parse_yyyymmdd("2020021"))
        self.assertIsNone(B.parse_yyyymmdd("20201345"))


# ---------------------------------------------------------------------
# Matching arithmetic
# ---------------------------------------------------------------------


def _granule(product="SD", d0=(2020, 2, 18), d1=None, wkt=None) -> B.LidarGranule:
    b = date(*d0)
    e = date(*d1) if d1 else b
    return B.LidarGranule(
        site_key="banner_summit", product=product, short_name="X",
        filename="f.tif", url="", date_begin=b, date_end=e,
        footprint_wkt=wkt or "POLYGON((-115.3 44.2,-115.1 44.2,"
                             "-115.1 44.34,-115.3 44.34,-115.3 44.2))",
        native_res_m=3.0,
    )


def _product(ref=(2020, 2, 11), sec=(2020, 2, 18), wkt=None,
             scene="UA_lowman_05208_20030-007_20034-000_0007d_s01_L090_01",
             level="INTERFEROMETRY_GRD") -> B.UavsarProduct:
    return B.UavsarProduct(
        scene_name=scene, file_id=scene + "-" + level, level=level,
        url="", filename="", bytes_=0, md5sum="",
        date_ref=date(*ref) if ref else None,
        date_sec=date(*sec) if sec else None,
        footprint_wkt=wkt or "POLYGON((-116.0 43.9,-114.9 43.9,"
                             "-114.9 44.5,-116.0 44.5,-116.0 43.9))",
    )


class TestGapDays(unittest.TestCase):
    def test_exact_hit_on_secondary_date(self):
        self.assertEqual(B.gap_days(_granule(), _product()), 0)

    def test_closest_approach_between_two_ranges(self):
        # LiDAR 2020-02-18..19, UAVSAR 2020-02-11 and 2020-02-25 -> 6 days.
        g = _granule(d0=(2020, 2, 18), d1=(2020, 2, 19))
        p = _product(ref=(2020, 2, 11), sec=(2020, 2, 25))
        self.assertEqual(B.gap_days(g, p), 6)

    def test_direction_does_not_matter(self):
        g = _granule(d0=(2020, 2, 18))
        self.assertEqual(B.gap_days(g, _product(ref=(2020, 2, 13), sec=None)), 5)
        self.assertEqual(B.gap_days(g, _product(ref=(2020, 2, 23), sec=None)), 5)

    def test_undated_product_returns_none(self):
        self.assertIsNone(B.gap_days(_granule(), _product(ref=None, sec=None)))


class TestMatchSite(unittest.TestCase):
    def setUp(self):
        self.site = B.SITES["banner_summit"]

    def test_dem_is_recorded_as_expected_non_match(self):
        rows, needed = B.match_site(self.site, [_granule("DEM")], [_product()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].verdict, "snow_off_no_match_expected")
        self.assertEqual(rows[0].uavsar_scene, "")
        self.assertEqual(needed, [])

    def test_inside_window_and_overlapping_matches(self):
        rows, needed = B.match_site(self.site, [_granule("SD")], [_product()])
        self.assertEqual([r.verdict for r in rows], ["match"])
        self.assertEqual(len(needed), 1)

    def test_window_boundary_is_inclusive_at_five_days(self):
        g = _granule("SD", d0=(2020, 2, 18))
        rows, _ = B.match_site(self.site, [g],
                               [_product(ref=(2020, 2, 23), sec=None)])
        self.assertEqual(len(rows), 1)

    def test_six_days_is_excluded(self):
        g = _granule("SD", d0=(2020, 2, 18))
        rows, needed = B.match_site(self.site, [g],
                                    [_product(ref=(2020, 2, 24), sec=None)])
        self.assertEqual(rows, [])
        self.assertEqual(needed, [])

    def test_date_ok_but_no_spatial_overlap_is_excluded(self):
        far = ("POLYGON((-100.0 40.0,-99.0 40.0,-99.0 41.0,"
               "-100.0 41.0,-100.0 40.0))")
        rows, needed = B.match_site(self.site, [_granule("SD")],
                                    [_product(wkt=far)])
        self.assertEqual(rows, [])
        self.assertEqual(needed, [])

    def test_shared_flight_is_needed_only_once(self):
        # Two LiDAR granules matching the same flight must not queue two
        # downloads of the same 5 GB product.
        p = _product()
        rows, needed = B.match_site(
            self.site, [_granule("SD"), _granule("VH")], [p, p]
        )
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(needed), 1)

    def test_distinct_levels_of_one_flight_are_separate_downloads(self):
        a = _product(level="INTERFEROMETRY_GRD")
        b = _product(level="AMPLITUDE_GRD")
        _, needed = B.match_site(self.site, [_granule("SD")], [a, b])
        self.assertEqual(len(needed), 2)


class TestNaming(unittest.TestCase):
    def test_flight_token_includes_the_line_number(self):
        # Campaign alone is not unique: grmesa_09305 and grmesa_27416 both fly
        # Grand Mesa and would otherwise produce the same group name.
        self.assertEqual(
            B.flight_token("UA_lowman_05208_21019-019_21021-007_0006d_s01_L090_01"),
            "LOWMAN05208",
        )

    def test_flight_token_separates_two_lines_of_one_campaign(self):
        a = B.flight_token("UA_grmesa_09305_20003_029_200201_L090_CX_01")
        b = B.flight_token("UA_grmesa_27416_20003_028_200201_L090_CX_01")
        self.assertNotEqual(a, b)
        self.assertEqual((a, b), ("GRMESA09305", "GRMESA27416"))

    def test_flight_token_on_junk(self):
        self.assertEqual(B.flight_token("nounderscores"), "UNKNOWN")
        self.assertEqual(B.flight_token(""), "UNKNOWN")

    def test_group_name_matches_readme_convention(self):
        name = B.group_name_for(B.SITES["banner_summit"], _product())
        self.assertEqual(name,
                         "BANNERSUMMIT_CUTFROMLOWMAN05208_20200211_20200218")

    def test_two_lines_of_one_campaign_get_distinct_group_names(self):
        site = B.SITES["grand_mesa"]
        a = _product(scene="UA_grmesa_09305_20003_029_200201_L090_CX_01",
                     ref=(2020, 2, 1), sec=(2020, 2, 1))
        b = _product(scene="UA_grmesa_27416_20003_028_200201_L090_CX_01",
                     ref=(2020, 2, 1), sec=(2020, 2, 1))
        self.assertNotEqual(B.group_name_for(site, a), B.group_name_for(site, b))

    def test_group_name_strips_spaces_from_multiword_sites(self):
        name = B.group_name_for(B.SITES["little_cottonwood"], _product())
        self.assertTrue(
            name.startswith("LITTLECOTTONWOODCANYON_CUTFROMLOWMAN05208_"), name)

    def test_group_names_are_hdf5_path_safe(self):
        for site in B.SITES.values():
            name = B.group_name_for(site, _product())
            self.assertNotIn("/", name)
            self.assertNotIn(" ", name)


# ---------------------------------------------------------------------
# Configuration invariants
# ---------------------------------------------------------------------


class TestSiteConfig(unittest.TestCase):
    def test_eight_sites(self):
        self.assertEqual(len(B.SITES), 8)

    def test_keys_are_self_consistent(self):
        for key, site in B.SITES.items():
            self.assertEqual(key, site.key)

    def test_every_site_has_a_reference_product_source(self):
        for site in B.SITES.values():
            self.assertIsNotNone(
                site.source(site.reference_product),
                f"{site.key} references {site.reference_product} but has no "
                "matching source spec",
            )

    def test_every_site_has_vegetation_height(self):
        # VH is the only product present at all eight sites.
        for site in B.SITES.values():
            self.assertIsNotNone(site.source("VH"), site.key)

    def test_no_two_sites_share_a_qsi_site_code(self):
        # Within a site the same code repeats across DEM/SD/VH by design; across
        # sites a collision would merge two sites into one bucket.
        owners: dict[str, str] = {}
        for site in B.SITES.values():
            for code in {s.site_code for s in site.sources if s.site_code}:
                self.assertNotIn(code, owners,
                                 f"{code} claimed by both {owners.get(code)} "
                                 f"and {site.key}")
                owners[code] = site.key
        self.assertEqual(len(owners), 8)

    def test_each_site_uses_one_qsi_site_code(self):
        for site in B.SITES.values():
            codes = {s.site_code for s in site.sources if s.site_code}
            self.assertLessEqual(len(codes), 1, site.key)

    def test_source_spec_rejects_ambiguous_selector(self):
        with self.assertRaises(ValueError):
            B.SourceSpec("SD", "X", site_code="USIDBS", filename_contains="x")
        with self.assertRaises(ValueError):
            B.SourceSpec("SD", "X")

    def test_reynolds_creek_has_no_snow_depth(self):
        self.assertIsNone(B.SITES["reynolds_creek"].source("SD"))

    def test_grand_mesa_and_reynolds_use_one_metre_sources(self):
        for key in ("grand_mesa", "reynolds_creek"):
            self.assertEqual(B.SITES[key].source("DEM").native_res_m, 1.0)

    def test_interferogram_uses_nearest_neighbour(self):
        # Bilinear across the 2*pi wrap boundary produces meaningless values.
        self.assertEqual(B.RESAMPLING["int"], "nearest")
        for k in ("unw", "cor", "hgt", "amp", "lidar"):
            self.assertEqual(B.RESAMPLING[k], "bilinear")

    def test_every_array_kind_has_a_description(self):
        for kind in list(B.INSAR_SUBPRODUCTS) + ["amp", "dem_tiff",
                                                 "DEM", "SD", "VH"]:
            self.assertIn(kind, B.DESCRIPTIONS)
            self.assertGreater(len(B.DESCRIPTIONS[kind]), 20)


class TestRetry(unittest.TestCase):
    def test_returns_first_success(self):
        self.assertEqual(B.retry(lambda: 42, what="t"), 42)

    def test_recovers_after_transient_failures(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("503")
            return "ok"

        self.assertEqual(
            B.retry(flaky, attempts=5, base_delay=0.0, what="t"), "ok"
        )
        self.assertEqual(state["n"], 3)

    def test_gives_up_and_reports_the_last_error(self):
        def always():
            raise ValueError("nope")

        with self.assertRaises(RuntimeError):
            B.retry(always, attempts=2, base_delay=0.0, what="t")


# ---------------------------------------------------------------------
# The common grid
# ---------------------------------------------------------------------


class TestSnapBounds(unittest.TestCase):
    def test_expands_outward_to_pixel_edges(self):
        self.assertEqual(
            B.snap_bounds((10.4, 20.1, 31.9, 44.2), 0.0, 100.0, 3.0),
            (9.0, 19.0, 33.0, 46.0),
        )

    def test_already_aligned_bounds_are_unchanged(self):
        self.assertEqual(
            B.snap_bounds((9.0, 19.0, 33.0, 46.0), 0.0, 100.0, 1.0),
            (9.0, 19.0, 33.0, 46.0),
        )

    def test_result_always_contains_the_input(self):
        for bounds in [(10.4, 20.1, 31.9, 44.2), (-7.3, -2.2, 5.1, 9.9),
                       (600010.5, 4890010.5, 600100.5, 4890100.5)]:
            left, bottom, right, top = B.snap_bounds(bounds, 0.0, 0.0, 3.0)
            self.assertLessEqual(left, bounds[0])
            self.assertLessEqual(bottom, bounds[1])
            self.assertGreaterEqual(right, bounds[2])
            self.assertGreaterEqual(top, bounds[3])

    def test_result_lands_on_the_origin_lattice(self):
        left, bottom, right, top = B.snap_bounds(
            (600010.4, 4890010.1, 600100.9, 4890100.2), 600000.0, 4900000.0, 3.0
        )
        for value, origin in ((left, 600000.0), (right, 600000.0),
                              (top, 4900000.0), (bottom, 4900000.0)):
            self.assertAlmostEqual((value - origin) % 3.0, 0.0, places=6)

    def test_rejects_degenerate_and_negative_inputs(self):
        with self.assertRaises(ValueError):
            B.snap_bounds((10.0, 10.0, 10.0, 20.0), 0.0, 0.0, 3.0)
        with self.assertRaises(ValueError):
            B.snap_bounds((10.0, 10.0, 20.0, 20.0), 0.0, 0.0, 0.0)


class TestDeriveCommonGrid(unittest.TestCase):
    def setUp(self):
        from rasterio.transform import Affine

        # A plausible NAD83 / UTM 11N reference DEM origin at 3 m.
        self.ref = Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4900000.0)

    def test_shape_and_transform(self):
        g = B.derive_common_grid(
            "EPSG:6340", self.ref, (600010.0, 4890010.0, 600100.0, 4890100.0)
        )
        self.assertEqual(g.shape, (30, 31))
        self.assertEqual(g.epsg, 6340)
        self.assertEqual(g.res_m, 3.0)
        self.assertAlmostEqual(g.transform[0], 3.0)
        self.assertAlmostEqual(g.transform[4], -3.0)

    def test_bounds_round_trip_through_the_transform(self):
        g = B.derive_common_grid(
            "EPSG:6340", self.ref, (600010.0, 4890010.0, 600100.0, 4890100.0)
        )
        left, bottom, right, top = g.bounds
        self.assertAlmostEqual(right - left, g.width * g.res_m)
        self.assertAlmostEqual(top - bottom, g.height * g.res_m)

    def test_grid_stays_aligned_with_the_reference_pixels(self):
        # Every array in the file must land on the DEM's pixel centres.
        g = B.derive_common_grid(
            "EPSG:6340", self.ref, (600011.7, 4890013.2, 600097.4, 4890099.1)
        )
        self.assertAlmostEqual((g.bounds[0] - 600000.0) % 3.0, 0.0, places=6)
        self.assertAlmostEqual((4900000.0 - g.bounds[3]) % 3.0, 0.0, places=6)

    def test_one_metre_reference_still_yields_a_three_metre_grid(self):
        from rasterio.transform import Affine

        ref_1m = Affine(1.0, 0.0, 600000.0, 0.0, -1.0, 4900000.0)
        g = B.derive_common_grid(
            "EPSG:6340", ref_1m, (600010.0, 4890010.0, 600100.0, 4890100.0)
        )
        self.assertEqual(g.res_m, 3.0)
        self.assertAlmostEqual(g.transform[0], 3.0)

    def test_rejects_south_up_reference(self):
        from rasterio.transform import Affine

        south_up = Affine(3.0, 0.0, 600000.0, 0.0, 3.0, 4890000.0)
        with self.assertRaises(ValueError):
            B.derive_common_grid("EPSG:6340", south_up,
                                 (600010.0, 4890010.0, 600100.0, 4890100.0))

    def test_rejects_rotated_reference(self):
        from rasterio.transform import Affine

        rotated = Affine(3.0, 0.4, 600000.0, 0.4, -3.0, 4900000.0)
        with self.assertRaises(ValueError):
            B.derive_common_grid("EPSG:6340", rotated,
                                 (600010.0, 4890010.0, 600100.0, 4890100.0))

    def test_attrs_carry_everything_the_schema_needs(self):
        g = B.derive_common_grid(
            "EPSG:6340", self.ref, (600010.0, 4890010.0, 600100.0, 4890100.0)
        )
        attrs = g.to_attrs()
        for key in ("common_crs_wkt", "common_crs_epsg", "common_grid_transform",
                    "common_grid_shape", "common_grid_resolution_m"):
            self.assertIn(key, attrs)
        self.assertEqual(attrs["common_grid_shape"], [30, 31])


class TestGridTargetBounds(unittest.TestCase):
    DEM = (0.0, 0.0, 100.0, 100.0)
    SCI = (50.0, 50.0, 200.0, 200.0)

    def test_dem_rule(self):
        self.assertEqual(B.grid_target_bounds(self.DEM, self.SCI, "dem"), self.DEM)

    def test_science_rule(self):
        self.assertEqual(B.grid_target_bounds(self.DEM, self.SCI, "science"),
                         self.SCI)

    def test_intersection_rule(self):
        self.assertEqual(
            B.grid_target_bounds(self.DEM, self.SCI, "dem_intersect_science"),
            (50.0, 50.0, 100.0, 100.0),
        )

    def test_intersection_bounds_an_oversized_dem(self):
        # Grand Mesa: the DEM dwarfs the science data, so the rule must pick
        # the science extent, not the DEM's.
        huge_dem = (0.0, 0.0, 1000.0, 1000.0)
        small_sci = (400.0, 400.0, 500.0, 500.0)
        self.assertEqual(
            B.grid_target_bounds(huge_dem, small_sci, "dem_intersect_science"),
            small_sci,
        )

    def test_intersection_trims_a_science_overhang(self):
        # Banner Summit 2021 VH: science overhangs the DEM, so the rule must
        # pick the DEM extent and drop the overhang.
        dem = (0.0, 0.0, 100.0, 100.0)
        overhang = (-50.0, -50.0, 150.0, 150.0)
        self.assertEqual(
            B.grid_target_bounds(dem, overhang, "dem_intersect_science"), dem
        )

    def test_disjoint_footprints_raise_rather_than_produce_an_empty_grid(self):
        with self.assertRaises(ValueError):
            B.grid_target_bounds((0.0, 0.0, 10.0, 10.0), (20.0, 20.0, 30.0, 30.0),
                                 "dem_intersect_science")

    def test_missing_dem_falls_back_to_science(self):
        self.assertEqual(
            B.grid_target_bounds(None, self.SCI, "dem_intersect_science"), self.SCI
        )

    def test_buffer_expands_every_side(self):
        got = B.grid_target_bounds(self.DEM, self.SCI, "dem", buffer_m=500.0)
        self.assertEqual(got, (-500.0, -500.0, 600.0, 600.0))

    def test_unknown_rule_raises(self):
        with self.assertRaises(ValueError):
            B.grid_target_bounds(self.DEM, self.SCI, "whatever")


# ---------------------------------------------------------------------
# Reprojection onto the common grid
# ---------------------------------------------------------------------


class TestReprojectArray(unittest.TestCase):
    """Synthetic EPSG:4326 source, UTM target, checked by georeferencing."""

    def setUp(self):
        import numpy as np
        from rasterio.transform import Affine
        from rasterio.warp import transform_bounds

        # 0.02 x 0.02 degree source over Banner Summit at ~0.0002 deg/px.
        self.west, self.north = -115.20, 44.30
        self.res = 0.0002
        self.n = 100
        self.src_transform = Affine(self.res, 0.0, self.west,
                                    0.0, -self.res, self.north)
        self.src = np.zeros((self.n, self.n), dtype="float32")
        # A distinctive block in rows/cols 20..40.
        self.src[20:40, 20:40] = 1.0

        src_bounds = (self.west, self.north - self.n * self.res,
                      self.west + self.n * self.res, self.north)
        utm_bounds = transform_bounds("EPSG:4326", "EPSG:6340", *src_bounds,
                                      densify_pts=21)
        self.grid = B.derive_common_grid(
            "EPSG:6340", Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4910000.0),
            utm_bounds,
        )

    def test_output_matches_the_grid_exactly(self):
        out = B.reproject_array(self.src, "EPSG:4326", self.src_transform,
                                self.grid, "bilinear")
        self.assertEqual(out.shape, self.grid.shape)
        self.assertEqual(out.dtype.name, "float32")

    def test_the_block_lands_where_the_projection_says_it_should(self):
        import numpy as np
        from rasterio.warp import transform as warp_transform

        out = B.reproject_array(self.src, "EPSG:4326", self.src_transform,
                                self.grid, "nearest")
        rows, cols = np.where(out > 0.5)
        self.assertGreater(rows.size, 0, "the block vanished during reprojection")

        # Centre of the source block, in lon/lat, then in grid pixel coordinates.
        lon = self.west + 30 * self.res
        lat = self.north - 30 * self.res
        (x,), (y,) = warp_transform("EPSG:4326", "EPSG:6340", [lon], [lat])
        col, row = ~self.grid.affine * (x, y)

        self.assertAlmostEqual(rows.mean(), row, delta=2.0)
        self.assertAlmostEqual(cols.mean(), col, delta=2.0)

    def test_nearest_neighbour_invents_no_new_values(self):
        import numpy as np

        out = B.reproject_array(self.src, "EPSG:4326", self.src_transform,
                                self.grid, "nearest")
        finite = out[np.isfinite(out)]
        self.assertTrue(np.all((finite == 0.0) | (finite == 1.0)))

    def test_area_is_preserved_within_a_few_percent(self):
        import numpy as np

        out = B.reproject_array(self.src, "EPSG:4326", self.src_transform,
                                self.grid, "nearest")
        # 20x20 source px at ~0.0002 deg; compare against the 3 m grid count.
        src_area_m2 = (20 * self.res * 111320 * np.cos(np.radians(44.3))) * \
                      (20 * self.res * 110574)
        out_area_m2 = int((out > 0.5).sum()) * 9.0
        self.assertLess(abs(out_area_m2 - src_area_m2) / src_area_m2, 0.05)

    def test_uncovered_pixels_are_nan_not_zero(self):
        import numpy as np
        from rasterio.transform import Affine

        # A grid shifted far north of the source has no overlap at all.
        empty_grid = B.CommonGrid(
            crs_wkt=self.grid.crs_wkt, epsg=self.grid.epsg,
            transform=tuple(Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 5100000.0))[:6],
            width=50, height=50, res_m=3.0,
        )
        out = B.reproject_array(self.src, "EPSG:4326", self.src_transform,
                                empty_grid, "bilinear")
        self.assertTrue(np.all(np.isnan(out)))

    def test_source_nodata_becomes_nan(self):
        import numpy as np

        src = self.src.copy()
        src[:] = -9999.0
        out = B.reproject_array(src, "EPSG:4326", self.src_transform, self.grid,
                                "nearest", src_nodata=-9999.0)
        self.assertTrue(np.all(np.isnan(out)))

    def test_integer_input_is_promoted_rather_than_rejected(self):
        import numpy as np

        src = (self.src * 10).astype("int16")
        out = B.reproject_array(src, "EPSG:4326", self.src_transform, self.grid,
                                "nearest")
        self.assertEqual(out.dtype.kind, "f")

    def test_complex_nearest_preserves_the_original_values(self):
        import numpy as np

        src = (self.src + 1j * self.src * 2).astype("complex64")
        out = B.reproject_array(src, "EPSG:4326", self.src_transform, self.grid,
                                "nearest")
        self.assertEqual(out.dtype.name, "complex64")
        finite = out[np.isfinite(out.real)]
        # Only the two source values may appear; nothing may be averaged.
        self.assertTrue(np.all((finite == 0) | (finite == (1 + 2j))))

    def test_complex_refuses_bilinear(self):
        import numpy as np

        src = (self.src + 1j * self.src).astype("complex64")
        for method in ("bilinear", "cubic", "average"):
            with self.assertRaises(ValueError, msg=method):
                B.reproject_array(src, "EPSG:4326", self.src_transform,
                                  self.grid, method)

    def test_wrapped_phase_is_not_averaged_across_the_branch_cut(self):
        # Two neighbouring pixels either side of the 2*pi wrap. Nearest must
        # return one of them; an averaging method would return ~0, a value that
        # exists nowhere in the data.
        import numpy as np

        src = np.zeros((self.n, self.n), dtype="complex64")
        src[:, : self.n // 2] = np.exp(1j * (np.pi - 0.01))
        src[:, self.n // 2:] = np.exp(1j * (-np.pi + 0.01))
        out = B.reproject_array(src, "EPSG:4326", self.src_transform, self.grid,
                                "nearest")
        finite = out[np.isfinite(out.real) & (np.abs(out) > 0.5)]
        self.assertGreater(finite.size, 0)
        self.assertTrue(np.all(np.abs(np.abs(finite) - 1.0) < 1e-4),
                        "nearest neighbour must not change the phasor magnitude")


class TestSourceWindow(unittest.TestCase):
    def setUp(self):
        from rasterio.transform import Affine
        from rasterio.warp import transform_bounds

        # A UAVSAR-sized EPSG:4326 scene: 16046 x 24939 px at 5.556e-05 deg,
        # which spans about 1.39 deg of longitude and 0.89 deg of latitude.
        self.src_transform = Affine(5.556e-05, 0.0, -117.0,
                                    0.0, -5.556e-05, 45.0)
        self.src_shape = (16046, 24939)

        # A site-sized grid well inside that scene, so the window is a genuine
        # interior crop rather than an edge case.
        utm = transform_bounds("EPSG:4326", "EPSG:6340",
                               -116.5, 44.4, -116.4, 44.5, densify_pts=21)
        self.grid = B.derive_common_grid(
            "EPSG:6340", Affine(3.0, 0.0, 500000.0, 0.0, -3.0, 4940000.0), utm
        )

    def test_window_is_a_small_fraction_of_a_uavsar_scene(self):
        win = B.source_window(self.grid, "EPSG:4326", self.src_transform,
                              self.src_shape)
        self.assertIsNotNone(win)
        _, _, n_rows, n_cols = win
        fraction = (n_rows * n_cols) / (self.src_shape[0] * self.src_shape[1])
        self.assertLess(fraction, 0.01)

    def test_window_stays_inside_the_raster(self):
        row_off, col_off, n_rows, n_cols = B.source_window(
            self.grid, "EPSG:4326", self.src_transform, self.src_shape
        )
        self.assertGreaterEqual(row_off, 0)
        self.assertGreaterEqual(col_off, 0)
        self.assertLessEqual(row_off + n_rows, self.src_shape[0])
        self.assertLessEqual(col_off + n_cols, self.src_shape[1])

    def test_margin_widens_the_window(self):
        small = B.source_window(self.grid, "EPSG:4326", self.src_transform,
                                self.src_shape, margin_px=0)
        large = B.source_window(self.grid, "EPSG:4326", self.src_transform,
                                self.src_shape, margin_px=32)
        self.assertGreater(large[2], small[2])
        self.assertGreater(large[3], small[3])

    def test_no_overlap_returns_none(self):
        from rasterio.transform import Affine

        far = Affine(5.556e-05, 0.0, 10.0, 0.0, -5.556e-05, 50.0)
        self.assertIsNone(
            B.source_window(self.grid, "EPSG:4326", far, (1000, 1000))
        )


# ---------------------------------------------------------------------
# UAVSAR .ann / .grd handling
# ---------------------------------------------------------------------


ANN_TEXT = """; UAVSAR annotation, trimmed to the fields the pipeline reads
Site Description = Lowman, CO
grd.set_rows (pixels)      = 40 ; ground range rows
grd.set_cols (pixels)      = 60 ; ground range columns
grd.row_addr (deg)         = 44.5 ; centre latitude of the first row
grd.col_addr (deg)         = -115.5 ; centre longitude of the first column
grd.row_mult (deg/pixel)   = -5.556e-05 ; latitude step
grd.col_mult (deg/pixel)   = 5.556e-05 ; longitude step
grd_phs.set_rows (pixels)  = 40
grd_phs.set_cols (pixels)  = 60
grd_phs.row_addr (deg)     = 44.5
grd_phs.col_addr (deg)     = -115.5
grd_phs.row_mult (deg/pixel) = -5.556e-05
grd_phs.col_mult (deg/pixel) = 5.556e-05
DEM Original Pixel spacing (arcsec) = 1
"""


class TestReadAnnotation(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.ann_path = Path(self.tmp.name) / "test.ann"
        self.ann_path.write_text(ANN_TEXT, encoding="utf-8")
        self.ann = B.read_annotation(self.ann_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_keys_are_lowercased_without_units(self):
        self.assertIn("grd.set_rows", self.ann)
        self.assertIn("dem original pixel spacing", self.ann)

    def test_units_are_captured(self):
        self.assertEqual(self.ann["grd.set_rows"]["units"], "pixels")
        self.assertEqual(self.ann["dem original pixel spacing"]["units"], "arcsec")

    def test_integers_stay_integers(self):
        self.assertIsInstance(self.ann["grd.set_rows"]["value"], int)
        self.assertEqual(self.ann["grd.set_rows"]["value"], 40)

    def test_comments_after_a_semicolon_are_stripped(self):
        self.assertEqual(self.ann["site description"]["value"], "Lowman, CO")

    def test_scientific_notation_survives(self):
        # "-5.556e-05" is not `.isdigit()`-friendly; it must not silently become
        # the string "-5.556e-05" and then blow up as a step size later.
        value = self.ann["grd.row_mult"]["value"]
        self.assertAlmostEqual(float(value), -5.556e-05)


class TestGrdLayout(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        p = Path(self.tmp.name) / "t.ann"
        p.write_text(ANN_TEXT, encoding="utf-8")
        self.ann = B.read_annotation(p)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unw_uses_the_grd_prefix(self):
        layout = B.grd_layout(self.ann, "unw")
        self.assertEqual(layout.shape, (40, 60))
        self.assertEqual(layout.dtype, "float32")

    def test_int_uses_the_grd_phs_prefix_and_is_complex(self):
        layout = B.grd_layout(self.ann, "int")
        self.assertEqual(layout.dtype, "complex64")
        self.assertEqual(layout.itemsize, 8)

    def test_transform_shifts_the_centre_address_to_a_corner(self):
        layout = B.grd_layout(self.ann, "unw")
        tf = layout.transform
        # .ann addresses are pixel centres; rasterio wants the upper-left corner.
        self.assertAlmostEqual(tf.c, -115.5 - 5.556e-05 / 2)
        self.assertAlmostEqual(tf.f, 44.5 + 5.556e-05 / 2)

    def test_transform_is_north_up(self):
        self.assertLess(B.grd_layout(self.ann, "unw").transform.e, 0)

    def test_unknown_subproduct_raises(self):
        with self.assertRaises(ValueError):
            B.grd_layout(self.ann, "nonsense")

    def test_missing_annotation_field_names_the_field(self):
        broken = {k: v for k, v in self.ann.items() if k != "grd.set_rows"}
        with self.assertRaises(KeyError) as ctx:
            B.grd_layout(broken, "unw")
        self.assertIn("grd.set_rows", str(ctx.exception))


class TestReadGrdWindow(unittest.TestCase):
    """A synthetic .grd, read windowed, checked against the full array."""

    def setUp(self):
        import tempfile
        import numpy as np
        from pathlib import Path
        from rasterio.transform import Affine

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "t.ann").write_text(ANN_TEXT, encoding="utf-8")
        self.ann = B.read_annotation(self.dir / "t.ann")
        self.layout = B.grd_layout(self.ann, "unw")

        # Unique value per pixel, so a misread window is impossible to miss.
        self.full = np.arange(40 * 60, dtype="float32").reshape(40, 60)
        self.grd = self.dir / "t.unw.grd"
        self.full.tofile(self.grd)

        # A UTM grid covering the middle of the scene.
        lon0, lat0 = -115.5, 44.5
        step = 5.556e-05
        from rasterio.warp import transform_bounds

        sub = (lon0 + 20 * step, lat0 - 30 * step,
               lon0 + 40 * step, lat0 - 10 * step)
        utm = transform_bounds("EPSG:4326", "EPSG:6340", *sub, densify_pts=21)
        self.grid = B.derive_common_grid(
            "EPSG:6340", Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4940000.0), utm
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_window_matches_a_direct_slice_of_the_full_array(self):
        import numpy as np

        got = B.read_grd_window(self.grd, self.layout, self.grid, margin_px=2)
        self.assertIsNotNone(got)
        block, _ = got
        win = B.source_window(self.grid, "EPSG:4326", self.layout.transform,
                              self.layout.shape, margin_px=2)
        row_off, col_off, n_rows, n_cols = win
        expected = self.full[row_off:row_off + n_rows, col_off:col_off + n_cols]
        np.testing.assert_array_equal(block, expected)

    def test_window_is_smaller_than_the_whole_scene(self):
        block, _ = B.read_grd_window(self.grd, self.layout, self.grid,
                                     margin_px=2)
        self.assertLess(block.size, self.full.size)

    def test_window_transform_georeferences_the_block_correctly(self):
        block, tf = B.read_grd_window(self.grd, self.layout, self.grid,
                                      margin_px=2)
        win = B.source_window(self.grid, "EPSG:4326", self.layout.transform,
                              self.layout.shape, margin_px=2)
        row_off, col_off = win[0], win[1]
        expected_x, expected_y = self.layout.transform * (col_off, row_off)
        self.assertAlmostEqual(tf.c, expected_x)
        self.assertAlmostEqual(tf.f, expected_y)

    def test_reprojecting_the_window_yields_the_full_grid(self):
        block, tf = B.read_grd_window(self.grd, self.layout, self.grid,
                                      margin_px=4)
        out = B.reproject_array(block, "EPSG:4326", tf, self.grid, "bilinear")
        self.assertEqual(out.shape, self.grid.shape)

    def test_size_mismatch_is_caught_rather_than_read_as_garbage(self):
        import numpy as np
        from pathlib import Path

        truncated = self.dir / "short.unw.grd"
        np.arange(10, dtype="float32").tofile(truncated)
        with self.assertRaises(ValueError) as ctx:
            B.read_grd_window(truncated, self.layout, self.grid)
        self.assertIn("disagree", str(ctx.exception))

    def test_no_overlap_returns_none(self):
        from rasterio.transform import Affine

        elsewhere = B.CommonGrid(
            crs_wkt=self.grid.crs_wkt, epsg=self.grid.epsg,
            transform=tuple(Affine(3.0, 0.0, 300000.0, 0.0, -3.0, 4000000.0))[:6],
            width=10, height=10, res_m=3.0,
        )
        self.assertIsNone(B.read_grd_window(self.grd, self.layout, elsewhere))

    def test_complex_grd_round_trips(self):
        import numpy as np

        layout = B.grd_layout(self.ann, "int")
        data = (np.arange(40 * 60) + 1j * np.arange(40 * 60)).astype("complex64")
        path = self.dir / "t.int.grd"
        data.reshape(40, 60).tofile(path)
        block, tf = B.read_grd_window(path, layout, self.grid, margin_px=2)
        self.assertEqual(block.dtype.name, "complex64")
        out = B.reproject_array(block, "EPSG:4326", tf, self.grid, "nearest")
        self.assertEqual(out.dtype.name, "complex64")


class TestNodataFor(unittest.TestCase):
    def test_float_and_complex_get_nan(self):
        import numpy as np

        self.assertTrue(np.isnan(B.nodata_for("float32")))
        self.assertTrue(np.isnan(B.nodata_for("complex64").real))

    def test_integers_are_refused(self):
        with self.assertRaises(TypeError):
            B.nodata_for("int16")


# ---------------------------------------------------------------------
# HDF5 writing and verification, end to end on a synthetic site file
# ---------------------------------------------------------------------


def _grid(height=64, width=48):
    from rasterio.transform import Affine

    ref = Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4900000.0)
    return B.derive_common_grid(
        "EPSG:6340", ref,
        (600000.0, 4900000.0 - height * 3.0, 600000.0 + width * 3.0, 4900000.0),
    )


def _match(verdict="match", **kw):
    defaults = dict(
        site_key="banner_summit", lidar_product_type="SD",
        lidar_filename="SNEX20_QSI_SD_3M_USIDBS_20200218_20200219.tif",
        lidar_date="20200218",
        uavsar_scene="UA_lowman_05208_20030-007_20034-000_0007d_s01_L090_01",
        uavsar_level="INTERFEROMETRY_GRD", uavsar_dates="20200211_20200218",
        gap_days=0, verdict=verdict,
        group_name="BANNERSUMMIT_CUTFROMLOWMAN_20200211_20200218",
    )
    defaults.update(kw)
    return B.Match(**defaults)


def _build_sample_file(path, grid, *, all_nodata=False):
    """A minimal but schema-complete site file."""
    import h5py
    import numpy as np

    site = B.SITES["banner_summit"]
    with h5py.File(path, "w") as h5:
        B.write_identification(h5, site, grid)

        def data(seed):
            if all_nodata:
                return np.full(grid.shape, np.nan, dtype="float32")
            rng = np.random.default_rng(seed)
            return rng.random(grid.shape).astype("float32")

        dem_group = h5.require_group(B.lidar_group_path("DEM"))
        B.write_array(dem_group, "elevation", data(1),
                      description=B.DESCRIPTIONS["DEM"],
                      resampling_method="bilinear",
                      source=B.QSI_DEM, grid=grid)

        sd_group = h5.require_group(B.lidar_group_path("SD", "20200218"))
        B.write_array(sd_group, "snow_depth", data(2),
                      description=B.DESCRIPTIONS["SD"],
                      resampling_method="bilinear",
                      source=B.QSI_SD, grid=grid)

        vh_group = h5.require_group(B.lidar_group_path("VH", "20200218"))
        B.write_array(vh_group, "veg_height", data(3),
                      description=B.DESCRIPTIONS["VH"],
                      resampling_method="bilinear",
                      source=B.QSI_VH, grid=grid)

        flight = "BANNERSUMMIT_CUTFROMLOWMAN_20200211_20200218"
        insar = h5.require_group(
            B.uavsar_group_path("INTERFEROMETRY_GRD", flight))
        B.set_attrs(insar, {"original_product_id": "UA_lowman_05208",
                            "acquisition_dates": ["20200211", "20200218"],
                            "clip_buffer_m": B.CLIP_BUFFER_M,
                            "description": "One interferometric acquisition."})
        for i, pol in enumerate(B.POLARIZATIONS):
            pol_group = insar.require_group(pol)
            for j, sub in enumerate(B.INSAR_SUBPRODUCTS):
                if sub == "int":
                    arr = (data(10 + i) + 1j * data(20 + j)).astype("complex64")
                else:
                    arr = data(30 + i * 4 + j)
                B.write_array(pol_group, sub, arr,
                              description=B.DESCRIPTIONS[sub],
                              resampling_method=B.RESAMPLING[sub],
                              source="ASF UAVSAR INTERFEROMETRY_GRD", grid=grid)

        B.write_matches(h5, [_match(), _match(verdict="snow_off_no_match_expected",
                                              lidar_product_type="DEM",
                                              uavsar_scene="", gap_days=-1,
                                              group_name="")])


class TestHdf5Writing(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "banner_summit.h5"
        self.grid = _grid()

    def tearDown(self):
        self.tmp.cleanup()

    def test_choose_chunks_never_exceeds_the_array(self):
        e = B.CHUNK_EDGE
        self.assertEqual(B.choose_chunks((10, 10)), (10, 10))
        self.assertEqual(B.choose_chunks((5000, 4000)), (e, e))
        self.assertEqual(B.choose_chunks((100, 5000)), (100, e))

    def test_chunk_edge_is_explicitly_overridable(self):
        self.assertEqual(B.choose_chunks((5000, 4000), edge=256), (256, 256))
        self.assertEqual(B.choose_chunks((5000, 4000), edge=64), (64, 64))

    def test_default_chunk_fits_the_stock_h5py_cache(self):
        # A chunk larger than h5py's default 1 MB rdcc_nbytes is evicted on
        # every read; that is why 512x512 float32 (1.05 MB) performed badly.
        self.assertLess(B.CHUNK_EDGE * B.CHUNK_EDGE * 4, 1024 * 1024)

    def test_choose_chunks_rejects_an_empty_array(self):
        with self.assertRaises(ValueError):
            B.choose_chunks((0, 10))

    def test_write_array_rejects_a_shape_that_is_not_the_grid(self):
        import h5py
        import numpy as np

        with h5py.File(self.path, "w") as h5:
            group = h5.require_group("science/LIDAR/DEM/grids")
            with self.assertRaises(ValueError) as ctx:
                B.write_array(group, "elevation",
                              np.zeros((5, 5), dtype="float32"),
                              description="x", resampling_method="bilinear",
                              source="y", grid=self.grid)
            self.assertIn("common grid", str(ctx.exception))

    def test_write_array_is_idempotent(self):
        import h5py
        import numpy as np

        for _ in range(2):
            with h5py.File(self.path, "a") as h5:
                group = h5.require_group("science/LIDAR/DEM/grids")
                B.write_array(group, "elevation",
                              np.zeros(self.grid.shape, dtype="float32"),
                              description="x", resampling_method="bilinear",
                              source="y", grid=self.grid)
        with h5py.File(self.path, "r") as h5:
            self.assertEqual(h5["science/LIDAR/DEM/grids/elevation"].shape,
                             self.grid.shape)

    def test_written_arrays_carry_every_required_attribute(self):
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            ds = h5["science/LIDAR/SD/20200218/snow_depth"]
            for key in B.REQUIRED_ARRAY_ATTRS:
                self.assertIn(key, ds.attrs, key)
            self.assertTrue(str(ds.attrs["description"]).strip())

    def test_valid_fraction_is_recorded(self):
        import h5py
        import numpy as np

        with h5py.File(self.path, "w") as h5:
            group = h5.require_group("science/LIDAR/DEM/grids")
            arr = np.zeros(self.grid.shape, dtype="float32")
            arr[: self.grid.height // 2, :] = np.nan
            B.write_array(group, "elevation", arr, description="x",
                          resampling_method="bilinear", source="y",
                          grid=self.grid)
        with h5py.File(self.path, "r") as h5:
            frac = float(h5["science/LIDAR/DEM/grids/elevation"]
                         .attrs["valid_fraction"])
            self.assertAlmostEqual(frac, 0.5, places=2)

    def test_complex_arrays_survive_the_round_trip(self):
        import h5py
        import numpy as np

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            ds = h5["science/UAVSAR/INTERFEROMETRY_GRD/"
                    "BANNERSUMMIT_CUTFROMLOWMAN_20200211_20200218/HH/int"]
            self.assertEqual(ds.dtype.name, "complex64")
            self.assertEqual(str(ds.attrs["resampling_method"]), "nearest")
            self.assertTrue(np.iscomplexobj(ds[...]))

    def test_every_insar_subproduct_is_written(self):
        # Measured at Grand Mesa, three of five interferograms carry HH alone
        # (4 arrays) and two carry full quad-pol (16). Which is which is
        # discovered from the archive, never assumed.
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            base = ("science/UAVSAR/INTERFEROMETRY_GRD/"
                    "BANNERSUMMIT_CUTFROMLOWMAN_20200211_20200218")
            found = [f"{pol}/{sub}" for pol in B.POLARIZATIONS
                     for sub in B.INSAR_SUBPRODUCTS
                     if f"{base}/{pol}/{sub}" in h5]
            # The sample file is built quad-pol, the larger of the two real
            # shapes: 4 polarizations x 4 sub-products.
            self.assertEqual(len(found), 16)

    def test_polarizations_cover_the_full_quad_pol_set(self):
        # Measured: some acquisitions are HH-only, others carry all four.
        # The constant lists what may appear; the ingester discovers what does.
        self.assertEqual(B.POLARIZATIONS, ("HH", "HV", "VH", "VV"))

    def test_identification_carries_the_grid_and_the_resampling_note(self):
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            attrs = h5["identification"].attrs
            for key in B.REQUIRED_IDENTIFICATION_ATTRS:
                self.assertIn(key, attrs, key)
            self.assertEqual(list(attrs["common_grid_shape"]),
                             [self.grid.height, self.grid.width])
            # The note must state that upsampling adds no information.
            self.assertIn("no information", str(attrs["resampling_note"]))

    def test_matches_table_columns_are_equal_length(self):
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            lengths = {k: h5["matches"][k].shape[0] for k in h5["matches"]}
            self.assertEqual(len(set(lengths.values())), 1)
            self.assertEqual(set(lengths.values()), {2})

    def test_matches_strings_read_back_as_text(self):
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            values = [v.decode() if isinstance(v, bytes) else v
                      for v in h5["matches"]["lidar_product_type"][...]]
            # Rows keep the order they were written in, so both products appear.
            self.assertEqual(values, ["SD", "DEM"])

    def test_snow_off_dem_row_is_preserved_in_the_table(self):
        import h5py

        _build_sample_file(self.path, self.grid)
        with h5py.File(self.path, "r") as h5:
            verdicts = [v.decode() if isinstance(v, bytes) else v
                        for v in h5["matches"]["verdict"][...]]
            self.assertIn("snow_off_no_match_expected", verdicts)

    def test_attr_value_coerces_awkward_python_types(self):
        import numpy as np

        self.assertEqual(B._attr_value(None), "")
        self.assertEqual(B._attr_value(["a", "b"]), ["a", "b"])
        self.assertEqual(B._attr_value(date(2020, 2, 18)), "2020-02-18")
        self.assertEqual(len(B._attr_value([])), 0)
        np.testing.assert_array_equal(B._attr_value([1.0, 2.0]),
                                      np.array([1.0, 2.0]))


class TestVerify(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "banner_summit.h5"
        self.grid = _grid()
        _build_sample_file(self.path, self.grid)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_well_formed_file_has_no_problems(self):
        self.assertEqual(B.verify_file(self.path), [])

    def test_catches_an_array_off_the_common_grid(self):
        import h5py
        import numpy as np

        with h5py.File(self.path, "a") as h5:
            group = h5["science/LIDAR/SD/20200218"]
            del group["snow_depth"]
            ds = group.create_dataset("snow_depth",
                                      data=np.zeros((8, 8), dtype="float32"))
            B.set_attrs(ds, {k: "x" for k in B.REQUIRED_ARRAY_ATTRS})
        problems = B.verify_file(self.path)
        self.assertTrue(any("does not match the common grid" in p
                            for p in problems), problems)

    def test_catches_a_missing_attribute(self):
        import h5py

        with h5py.File(self.path, "a") as h5:
            del h5["science/LIDAR/SD/20200218/snow_depth"].attrs["description"]
        problems = B.verify_file(self.path)
        self.assertTrue(any("description" in p for p in problems), problems)

    def test_catches_an_array_that_is_entirely_nodata(self):
        empty = self.path.with_name("empty.h5")
        _build_sample_file(empty, self.grid, all_nodata=True)
        problems = B.verify_file(empty)
        self.assertTrue(any("every pixel is nodata" in p for p in problems),
                        problems)

    def test_all_nodata_is_tolerated_when_the_check_is_relaxed(self):
        empty = self.path.with_name("empty.h5")
        _build_sample_file(empty, self.grid, all_nodata=True)
        self.assertEqual(B.verify_file(empty, strict_empty=False), [])

    def test_catches_a_transform_that_drifted(self):
        import h5py

        with h5py.File(self.path, "a") as h5:
            ds = h5["science/LIDAR/VH/20200218/veg_height"]
            ds.attrs["transform"] = [3.0, 0.0, 999999.0, 0.0, -3.0, 4900000.0]
        problems = B.verify_file(self.path)
        self.assertTrue(any("transform" in p for p in problems), problems)

    def test_catches_a_crs_that_differs_from_the_grid(self):
        import h5py

        with h5py.File(self.path, "a") as h5:
            h5["science/LIDAR/VH/20200218/veg_height"].attrs["crs_wkt"] = "nonsense"
        problems = B.verify_file(self.path)
        self.assertTrue(any("CRS differs" in p for p in problems), problems)

    def test_catches_unequal_match_columns(self):
        import h5py
        import numpy as np

        with h5py.File(self.path, "a") as h5:
            del h5["matches"]["gap_days"]
            h5["matches"].create_dataset("gap_days",
                                         data=np.array([0], dtype="int32"))
        problems = B.verify_file(self.path)
        self.assertTrue(any("unequal lengths" in p for p in problems), problems)

    def test_catches_a_file_with_no_identification(self):
        import h5py

        broken = self.path.with_name("broken.h5")
        with h5py.File(broken, "w") as h5:
            h5.require_group("science")
        problems = B.verify_file(broken)
        self.assertTrue(any("identification" in p for p in problems), problems)

    def test_catches_a_file_with_no_arrays(self):
        import h5py

        broken = self.path.with_name("bare.h5")
        with h5py.File(broken, "w") as h5:
            B.write_identification(h5, B.SITES["banner_summit"], self.grid)
            h5.require_group("science")
        problems = B.verify_file(broken)
        self.assertTrue(any("no arrays" in p for p in problems), problems)

    def test_run_verify_returns_a_nonzero_problem_count_on_failure(self):
        empty = self.path.with_name("empty.h5")
        _build_sample_file(empty, self.grid, all_nodata=True)
        self.assertGreater(B.run_verify([empty]), 0)
        self.assertEqual(B.run_verify([self.path]), 0)

    def test_run_verify_reports_a_missing_file(self):
        self.assertGreater(B.run_verify([self.path.with_name("nope.h5")]), 0)


class TestSchemaPaths(unittest.TestCase):
    def test_dem_lives_under_grids(self):
        self.assertEqual(B.lidar_group_path("DEM"), "science/LIDAR/DEM/grids")

    def test_dated_products_use_the_date_key(self):
        self.assertEqual(B.lidar_group_path("SD", "20200218"),
                         "science/LIDAR/SD/20200218")
        self.assertEqual(B.lidar_group_path("VH", "20210315"),
                         "science/LIDAR/VH/20210315")

    def test_dated_product_without_a_date_raises(self):
        with self.assertRaises(ValueError):
            B.lidar_group_path("SD")

    def test_uavsar_path_matches_the_schema(self):
        self.assertEqual(
            B.uavsar_group_path("INTERFEROMETRY_GRD", "BANNERSUMMIT_X"),
            "science/UAVSAR/INTERFEROMETRY_GRD/BANNERSUMMIT_X",
        )


# ---------------------------------------------------------------------
# End-to-end build, offline
#
# Synthetic GeoTIFFs with real granule filenames and a deliberately
# overhanging 2021 vegetation-height tile, so the extent rule is
# exercised on the same geometry the real data has.
# ---------------------------------------------------------------------


BS = "banner_summit"
DEM_NAME = "SNEX20_QSI_DEM_3M_USIDBS_20210917_20210917.tif"
SD_2020 = "SNEX20_QSI_SD_3M_USIDBS_20200218_20200219.tif"
SD_2021 = "SNEX20_QSI_SD_3M_USIDBS_20210315_20210315.tif"
VH_2020 = "SNEX20_QSI_VH_3M_USIDBS_20200218_20200219.tif"
VH_2021 = "SNEX20_QSI_VH_3M_USIDBS_20210315_20210315.tif"

# A 300 m square at 3 m, so the grid is 100 x 100 and the tests stay fast.
DEM_ORIGIN = (600000.0, 4900000.0)
DEM_SIZE_M = 300.0


def _write_geotiff(path, west, north, width, height, value, res=3.0,
                   nodata=-9999.0, epsg=6340):
    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    transform = Affine(res, 0.0, west, 0.0, -res, north)
    data = np.full((height, width), float(value), dtype="float32")
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", crs=f"EPSG:{epsg}", transform=transform, nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return (west, north - height * res, west + width * res, north)


def _make_synthetic_lidar(directory):
    """Five rasters matching Banner Summit's real granule names."""
    from pathlib import Path

    directory = Path(directory)
    west, north = DEM_ORIGIN
    n = int(DEM_SIZE_M / 3.0)

    bounds = {}
    for name, value in ((DEM_NAME, 2100.0), (SD_2020, 1.2), (SD_2021, 0.9),
                        (VH_2020, 8.0)):
        bounds[name] = _write_geotiff(directory / name, west, north, n, n, value)

    # The 2021 vegetation-height tile overhangs the DEM on every side, exactly
    # as the real granules do at Banner Summit, Fraser and Little Cottonwood.
    bounds[VH_2021] = _write_geotiff(
        directory / VH_2021, west - 150.0, north + 150.0, n + 100, n + 100, 7.5
    )
    return bounds


def _synthetic_inventory():
    from dataclasses import asdict

    def granule(product, filename, d0, d1):
        return {
            "site_key": BS, "product": product, "short_name": f"SNEX20_QSI_{product}_3m",
            "filename": filename, "url": "",
            "date_begin": d0, "date_end": d1,
            "footprint_wkt": "POLYGON((-115.3 44.2,-115.1 44.2,"
                             "-115.1 44.34,-115.3 44.34,-115.3 44.2))",
            "native_res_m": 3.0, "note": "",
        }

    matches = [asdict(_match()),
               asdict(_match(verdict="snow_off_no_match_expected",
                             lidar_product_type="DEM", uavsar_scene="",
                             gap_days=-1, group_name=""))]
    return {
        "version": B.VERSION,
        "sites": {
            BS: {
                "lidar": [
                    granule("DEM", DEM_NAME, "2021-09-17", "2021-09-17"),
                    granule("SD", SD_2020, "2020-02-18", "2020-02-19"),
                    granule("SD", SD_2021, "2021-03-15", "2021-03-15"),
                    granule("VH", VH_2020, "2020-02-18", "2020-02-19"),
                    granule("VH", VH_2021, "2021-03-15", "2021-03-15"),
                ],
                "matches": matches,
            }
        },
    }


class TestBuildEndToEnd(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lidar_dir = self.root / "lidar"
        self.lidar_dir.mkdir()
        self.out_dir = self.root / "out"
        self.bounds = _make_synthetic_lidar(self.lidar_dir)
        self.inventory = _synthetic_inventory()
        self.inventory_path = self.root / "inventory.json"
        self.inventory_path.write_text(json.dumps(self.inventory),
                                       encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self):
        return B.run_build([BS], self.inventory_path, self.out_dir,
                           local_dir=self.lidar_dir)

    def test_build_succeeds_and_the_result_verifies_clean(self):
        self.assertEqual(self._build(), 0)
        path = self.out_dir / f"{BS}.h5"
        self.assertTrue(path.exists())
        self.assertEqual(B.verify_file(path), [])

    def test_grid_is_the_dem_extent_when_vegetation_height_overhangs(self):
        plans = B.plan_sites([BS], self.inventory, self.out_dir, self.lidar_dir)
        grid = plans[0].grid
        # The intersection rule must trim the overhang back to the DEM.
        self.assertEqual(grid.shape, (100, 100))
        left, bottom, right, top = grid.bounds
        self.assertAlmostEqual(left, DEM_ORIGIN[0])
        self.assertAlmostEqual(top, DEM_ORIGIN[1])
        self.assertAlmostEqual(right, DEM_ORIGIN[0] + DEM_SIZE_M)
        self.assertAlmostEqual(bottom, DEM_ORIGIN[1] - DEM_SIZE_M)

    def test_the_science_rule_would_instead_keep_the_overhang(self):
        original = B.GRID_EXTENT_RULE
        B.GRID_EXTENT_RULE = "science"
        try:
            plans = B.plan_sites([BS], self.inventory, self.out_dir,
                                 self.lidar_dir)
            self.assertGreater(plans[0].grid.width, 100)
        finally:
            B.GRID_EXTENT_RULE = original

    def test_every_lidar_array_lands_on_the_grid(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            expected = tuple(int(v) for v in
                             h5["identification"].attrs["common_grid_shape"])
            for path in ("science/LIDAR/DEM/grids/elevation",
                         "science/LIDAR/SD/20200218/snow_depth",
                         "science/LIDAR/SD/20210315/snow_depth",
                         "science/LIDAR/VH/20200218/veg_height",
                         "science/LIDAR/VH/20210315/veg_height"):
                self.assertIn(path, h5, path)
                self.assertEqual(h5[path].shape, expected, path)

    def test_arrays_carry_the_values_they_were_given(self):
        import h5py
        import numpy as np

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            dem = h5["science/LIDAR/DEM/grids/elevation"][...]
            sd = h5["science/LIDAR/SD/20200218/snow_depth"][...]
            self.assertAlmostEqual(float(np.nanmedian(dem)), 2100.0, places=3)
            self.assertAlmostEqual(float(np.nanmedian(sd)), 1.2, places=3)

    def test_coverage_is_complete_across_the_grid(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            for path in ("science/LIDAR/DEM/grids/elevation",
                         "science/LIDAR/VH/20210315/veg_height"):
                frac = float(h5[path].attrs["valid_fraction"])
                self.assertGreater(frac, 0.99, path)

    def test_source_provenance_travels_with_each_array(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            ds = h5["science/LIDAR/SD/20210315/snow_depth"]
            self.assertEqual(str(ds.attrs["source_filename"]), SD_2021)
            self.assertEqual(str(ds.attrs["acquisition_date"]), "2021-03-15")
            self.assertEqual(str(ds.attrs["source_dataset"]), "SNEX20_QSI_SD_3m")

    def test_citation_requirement_travels_inside_the_file(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            note = str(h5["identification"].attrs["citation_note"])
            self.assertIn("cite", note.lower())

    def test_matches_table_is_written(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            self.assertIn("matches", h5)
            self.assertEqual(h5["matches"]["gap_days"].shape[0], 2)

    def test_rebuilding_does_not_grow_the_file(self):
        # HDF5 never returns the bytes of a deleted dataset to the filesystem,
        # so a delete-and-rewrite loop bloats the archive on every run. A
        # re-run must skip arrays that are already correct.
        self.assertEqual(self._build(), 0)
        first = (self.out_dir / f"{BS}.h5").stat().st_size
        self.assertEqual(self._build(), 0)
        second = (self.out_dir / f"{BS}.h5").stat().st_size
        self.assertEqual(second, first)
        self.assertEqual(B.verify_file(self.out_dir / f"{BS}.h5"), [])

    def test_a_resumed_build_fills_in_only_what_is_missing(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "a") as h5:
            del h5["science/LIDAR/SD/20200218/snow_depth"]
        self.assertEqual(self._build(), 0)
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            self.assertIn("science/LIDAR/SD/20200218/snow_depth", h5)
        self.assertEqual(B.verify_file(self.out_dir / f"{BS}.h5"), [])

    def test_a_changed_grid_forces_a_rewrite(self):
        import h5py

        self._build()
        with h5py.File(self.out_dir / f"{BS}.h5", "r") as h5:
            key = str(h5["science/LIDAR/DEM/grids/elevation"].attrs["content_key"])
        self.assertIn(DEM_NAME, key)
        plans = B.plan_sites([BS], self.inventory, self.out_dir, self.lidar_dir)
        self.assertTrue(key.endswith(B.grid_fingerprint(plans[0].grid)))

    def test_overwrite_rewrites_existing_arrays(self):
        self.assertEqual(self._build(), 0)
        self.assertEqual(
            B.run_build([BS], self.inventory_path, self.out_dir,
                        local_dir=self.lidar_dir, overwrite=True),
            0,
        )
        self.assertEqual(B.verify_file(self.out_dir / f"{BS}.h5"), [])

    def test_missing_inventory_is_reported_not_raised(self):
        self.assertEqual(
            B.run_build([BS], self.root / "nope.json", self.out_dir,
                        local_dir=self.lidar_dir),
            2,
        )

    def test_a_missing_source_file_skips_only_that_site(self):
        # A single unreadable site must not abort a run that may be part way
        # through 175 GB of downloads.
        (self.lidar_dir / DEM_NAME).unlink()
        with self.assertLogs("build_hdf5", level="ERROR") as logs:
            plans = B.plan_sites([BS], self.inventory, self.out_dir,
                                 self.lidar_dir)
        self.assertEqual(plans, [])
        self.assertTrue(any("skipping" in m for m in logs.output), logs.output)

    def test_site_without_a_reference_granule_is_skipped_and_logged(self):
        inv = json.loads(json.dumps(self.inventory))
        inv["sites"][BS]["lidar"] = [
            g for g in inv["sites"][BS]["lidar"] if g["product"] != "DEM"
        ]
        with self.assertLogs("build_hdf5", level="ERROR") as logs:
            plans = B.plan_sites([BS], inv, self.out_dir, self.lidar_dir)
        self.assertEqual(plans, [])
        self.assertTrue(any("grid" in m for m in logs.output), logs.output)

    def test_one_failing_site_does_not_stop_the_others(self):
        import shutil

        # Two sites in the inventory; only one has readable rasters.
        inv = json.loads(json.dumps(self.inventory))
        inv["sites"]["mores_creek"] = json.loads(
            json.dumps(inv["sites"][BS]))
        for g in inv["sites"]["mores_creek"]["lidar"]:
            g["site_key"] = "mores_creek"
            g["filename"] = g["filename"].replace("USIDBS", "USIDMC")
        # Deliberately do not create the USIDMC files.
        path = self.root / "two.json"
        path.write_text(json.dumps(inv), encoding="utf-8")

        with self.assertLogs("build_hdf5", level="ERROR"):
            rc = B.run_build([BS, "mores_creek"], path, self.out_dir,
                             local_dir=self.lidar_dir)
        # Banner Summit still got written even though Mores Creek failed.
        self.assertTrue((self.out_dir / f"{BS}.h5").exists())
        self.assertEqual(B.verify_file(self.out_dir / f"{BS}.h5"), [])
        # ...and the run reports that something was skipped.
        self.assertEqual(rc, 1)


NETRC_BODY = "machine urs.earthdata.nasa.gov login someone password secret123\n"


class TestNetrcPath(unittest.TestCase):
    """Windows calls it _netrc, POSIX calls it .netrc, and the stdlib only
    ever looks for .netrc. Both must be found."""

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._expand = mock.patch(
            "os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(self.home), 1),
        )
        self._expand.start()
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._expand.stop()
        self.tmp.cleanup()

    def test_finds_the_posix_name(self):
        (self.home / ".netrc").write_text(NETRC_BODY, encoding="utf-8")
        self.assertEqual(B.netrc_path(), self.home / ".netrc")

    def test_finds_the_windows_name(self):
        (self.home / "_netrc").write_text(NETRC_BODY, encoding="utf-8")
        self.assertEqual(B.netrc_path(), self.home / "_netrc")

    def test_prefers_the_posix_name_when_both_exist(self):
        (self.home / ".netrc").write_text(NETRC_BODY, encoding="utf-8")
        (self.home / "_netrc").write_text(NETRC_BODY, encoding="utf-8")
        self.assertEqual(B.netrc_path(), self.home / ".netrc")

    def test_none_when_neither_exists(self):
        self.assertIsNone(B.netrc_path())

    def test_gdal_uses_netrc_and_cookies_when_a_netrc_exists(self):
        import os
        from unittest import mock
        (self.home / "_netrc").write_text(NETRC_BODY, encoding="utf-8")
        os.environ["GDAL_HTTP_HEADERS"] = "Authorization: Bearer stale"
        auth = mock.Mock(token={"access_token": "abc"})
        with mock.patch.object(B.Path, "home", return_value=self.home):
            self.assertTrue(B.configure_gdal_for_earthdata(auth))
        self.assertEqual(os.environ["GDAL_HTTP_NETRC"], "YES")
        self.assertEqual(os.environ["GDAL_HTTP_NETRC_FILE"], str(self.home / "_netrc"))
        self.assertEqual(os.environ["GDAL_HTTP_COOKIEFILE"], os.environ["GDAL_HTTP_COOKIEJAR"])
        self.assertNotIn("GDAL_HTTP_HEADERS", os.environ)

    def test_gdal_falls_back_to_a_bearer_header_without_netrc(self):
        import os
        from unittest import mock
        auth = mock.Mock(token={"access_token": "abc"})
        self.assertTrue(B.configure_gdal_for_earthdata(auth))
        self.assertEqual(os.environ["GDAL_HTTP_HEADERS"], "Authorization: Bearer abc")
        self.assertNotIn("GDAL_HTTP_NETRC", os.environ)

    def test_netrc_environment_variable_wins(self):
        import os

        custom = self.home / "custom_netrc"
        custom.write_text(NETRC_BODY, encoding="utf-8")
        (self.home / ".netrc").write_text(NETRC_BODY, encoding="utf-8")
        os.environ["NETRC"] = str(custom)
        self.assertEqual(B.netrc_path(), custom)


class TestCredentials(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._expand = mock.patch(
            "os.path.expanduser",
            side_effect=lambda p: p.replace("~", str(self.home), 1),
        )
        self._expand.start()
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._expand.stop()
        self.tmp.cleanup()

    def test_environment_variables_take_priority(self):
        import os

        os.environ["EARTHDATA_USERNAME"] = "u"
        os.environ["EARTHDATA_PASSWORD"] = "p"
        self.assertEqual(B.earthdata_credentials(), ("u", "p"))

    def test_reads_a_windows_underscore_netrc(self):
        # The exact case that was broken: valid credentials on Windows.
        (self.home / "_netrc").write_text(NETRC_BODY, encoding="utf-8")
        self.assertEqual(B.earthdata_credentials(), ("someone", "secret123"))

    def test_reads_a_posix_dot_netrc(self):
        (self.home / ".netrc").write_text(NETRC_BODY, encoding="utf-8")
        self.assertEqual(B.earthdata_credentials(), ("someone", "secret123"))

    def test_missing_file_raises_a_useful_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            B.earthdata_credentials()
        message = str(ctx.exception)
        self.assertIn("EARTHDATA_USERNAME", message)
        self.assertIn("_netrc", message)

    def test_file_without_the_earthdata_host_is_reported(self):
        (self.home / ".netrc").write_text(
            "machine example.com login a password b\n", encoding="utf-8"
        )
        with self.assertRaises(RuntimeError) as ctx:
            B.earthdata_credentials()
        self.assertIn("no entry", str(ctx.exception))

    def test_incomplete_entry_is_reported(self):
        (self.home / ".netrc").write_text(
            "machine urs.earthdata.nasa.gov login someone password\n",
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError):
            B.earthdata_credentials()


class TestCli(unittest.TestCase):
    def test_unknown_site_is_rejected(self):
        with self.assertRaises(SystemExit):
            B.main(["--mode", "preflight", "--sites", "atlantis"])

    def test_help_lists_every_mode(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            B.main(["--help"])
        text = buf.getvalue()
        for mode in ("preflight", "build", "verify", "inspect-uavsar"):
            self.assertIn(mode, text)


# ---------------------------------------------------------------------
# Fill-value handling
#
# Measured on the real products: the QSI DEM declares -3.4028235e38,
# the QSI SD/VH rasters declare nothing yet are full of NaN, and Grand
# Mesa's SD declares 0.0 -- which is also a real snow depth.
# ---------------------------------------------------------------------


class TestNormaliseFill(unittest.TestCase):
    def test_declared_sentinel_becomes_nan(self):
        import numpy as np

        data = np.array([[1.0, -9999.0], [2.0, 3.0]], dtype="float32")
        out, nodata = B.normalise_fill(data, -9999.0)
        self.assertTrue(np.isnan(out[0, 1]))
        self.assertTrue(np.isnan(nodata))
        self.assertEqual(out[0, 0], 1.0)

    def test_undeclared_nan_is_already_nodata(self):
        # QSI snow depth and veg height: nodata is None but NaN is everywhere.
        import numpy as np

        data = np.array([[1.0, np.nan], [2.0, 3.0]], dtype="float32")
        out, nodata = B.normalise_fill(data, None)
        self.assertTrue(np.isnan(out[0, 1]))
        self.assertTrue(np.isnan(nodata))
        self.assertEqual(int(np.isfinite(out).sum()), 3)

    def test_undeclared_float32_minimum_is_caught(self):
        # The exact value the QSI DEM carries; the SD/VH rasters declare no
        # nodata at all, so an undeclared sentinel must still be recognised.
        import numpy as np

        data = np.array([[1.0, B.FLOAT32_MIN_SENTINEL]], dtype="float32")
        out, _ = B.normalise_fill(data, None)
        self.assertTrue(np.isnan(out[0, 1]))

    def test_grand_mesa_zero_nodata_is_honoured(self):
        # 0.0 is a physically real snow depth, but it is the publisher's
        # declared fill for this product, so the declaration wins.
        import numpy as np

        data = np.array([[0.0, 0.9, 1.2]], dtype="float32")
        out, _ = B.normalise_fill(data, 0.0)
        self.assertTrue(np.isnan(out[0, 0]))
        self.assertEqual(out[0, 1], np.float32(0.9))

    def test_zero_is_kept_when_it_is_not_the_declared_fill(self):
        import numpy as np

        data = np.array([[0.0, 0.9]], dtype="float32")
        out, _ = B.normalise_fill(data, -9999.0)
        self.assertEqual(out[0, 0], 0.0)
        self.assertFalse(np.isnan(out[0, 0]))

    def test_integer_input_is_promoted_so_nan_can_be_stored(self):
        import numpy as np

        data = np.array([[1, 2]], dtype="int16")
        out, _ = B.normalise_fill(data, 1)
        self.assertEqual(out.dtype.kind, "f")
        self.assertTrue(np.isnan(out[0, 0]))

    def test_the_input_array_is_not_mutated(self):
        import numpy as np

        data = np.array([[1.0, -9999.0]], dtype="float32")
        B.normalise_fill(data, -9999.0)
        self.assertEqual(data[0, 1], -9999.0)

    def test_declared_nan_nodata_does_not_break_the_comparison(self):
        import numpy as np

        data = np.array([[1.0, np.nan]], dtype="float32")
        out, _ = B.normalise_fill(data, float("nan"))
        self.assertEqual(out[0, 0], 1.0)
        self.assertTrue(np.isnan(out[0, 1]))

    def test_fill_does_not_survive_reprojection_as_a_real_value(self):
        # The failure this guards against: a -3.4e38 fill bilinearly averaged
        # with a genuine measurement, producing a plausible-looking wrong number.
        import numpy as np
        from rasterio.transform import Affine

        n = 60
        src = np.full((n, n), 2.0, dtype="float32")
        src[:, n // 2:] = B.FLOAT32_MIN_SENTINEL
        src_transform = Affine(0.0002, 0.0, -115.2, 0.0, -0.0002, 44.3)

        from rasterio.warp import transform_bounds

        utm = transform_bounds("EPSG:4326", "EPSG:6340",
                               -115.2, 44.3 - n * 0.0002,
                               -115.2 + n * 0.0002, 44.3, densify_pts=21)
        grid = B.derive_common_grid(
            "EPSG:6340", Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4910000.0), utm)

        clean, nodata = B.normalise_fill(src, None)
        out = B.reproject_array(clean, "EPSG:4326", src_transform, grid,
                                "bilinear", src_nodata=nodata)
        finite = out[np.isfinite(out)]
        self.assertGreater(finite.size, 0)
        self.assertTrue(np.all(finite > 1.9),
                        f"fill leaked into the result: min={finite.min()}")


# ---------------------------------------------------------------------
# CRS resolution
#
# Every SnowEx QSI raster ships as PROJCS["unnamed"] on an unnamed
# datum, so to_epsg() returns None and the archive would be unlabelled.
# ---------------------------------------------------------------------


UNNAMED_UTM11N = (
    'PROJCS["unnamed",GEOGCS["Unknown datum based upon the GRS 1980 ellipsoid",'
    'DATUM["Not_specified_based_on_GRS_1980_ellipsoid",'
    'SPHEROID["GRS 1980",6378137,298.257222101004]],PRIMEM["Greenwich",0],'
    'UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",-117],'
    'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],'
    'PARAMETER["false_northing",0],UNIT["metre",1]]'
)

IDAHO_POINT = (-115.19, 44.27)
UTAH_POINT = (-111.67, 40.56)


class TestCrsEquivalence(unittest.TestCase):
    def test_unnamed_utm11n_matches_epsg_6340(self):
        self.assertTrue(
            B.crs_is_equivalent(UNNAMED_UTM11N, "EPSG:6340", IDAHO_POINT)
        )

    def test_unnamed_utm11n_does_not_match_a_different_zone(self):
        # EPSG:6341 is UTM 12N. Same datum, wrong zone -- must not be accepted.
        self.assertFalse(
            B.crs_is_equivalent(UNNAMED_UTM11N, "EPSG:6341", IDAHO_POINT)
        )

    def test_geographic_does_not_match_projected(self):
        self.assertFalse(
            B.crs_is_equivalent(UNNAMED_UTM11N, "EPSG:4326", IDAHO_POINT)
        )

    def test_garbage_crs_is_not_equivalent_to_anything(self):
        self.assertFalse(B.crs_is_equivalent("not a crs", "EPSG:6340",
                                             IDAHO_POINT))

    def test_a_crs_is_equivalent_to_itself(self):
        self.assertTrue(B.crs_is_equivalent("EPSG:6340", "EPSG:6340",
                                            IDAHO_POINT))


class TestResolveReferenceCrs(unittest.TestCase):
    def test_declared_epsg_is_used_unchanged(self):
        import rasterio.crs

        crs = rasterio.crs.CRS.from_epsg(26911)
        got, assigned, note = B.resolve_reference_crs(
            crs, B.SITES["reynolds_creek"], IDAHO_POINT)
        self.assertFalse(assigned)
        self.assertEqual(got.to_epsg(), 26911)
        self.assertIn("read directly", note)

    def test_unnamed_crs_is_labelled_with_the_documented_epsg(self):
        import rasterio.crs

        crs = rasterio.crs.CRS.from_wkt(UNNAMED_UTM11N)
        self.assertIsNone(crs.to_epsg())
        got, assigned, note = B.resolve_reference_crs(
            crs, B.SITES["banner_summit"], IDAHO_POINT)
        self.assertTrue(assigned)
        self.assertEqual(got.to_epsg(), 6340)
        self.assertIn("unchanged", note)

    def test_a_mismatched_unnamed_crs_is_left_alone(self):
        # A Utah site whose raster is really UTM 11N must NOT be relabelled
        # EPSG:6341; the equivalence check has to refuse.
        import rasterio.crs

        crs = rasterio.crs.CRS.from_wkt(UNNAMED_UTM11N)
        got, assigned, note = B.resolve_reference_crs(
            crs, B.SITES["little_cottonwood"], UTAH_POINT)
        self.assertFalse(assigned)
        self.assertIsNone(got.to_epsg())
        self.assertIn("preserved verbatim", note)

    def test_the_label_never_moves_a_point(self):
        import rasterio.crs

        crs = rasterio.crs.CRS.from_wkt(UNNAMED_UTM11N)
        got, assigned, _ = B.resolve_reference_crs(
            crs, B.SITES["banner_summit"], IDAHO_POINT)
        self.assertTrue(assigned)
        self.assertTrue(B.crs_is_equivalent(crs, got, IDAHO_POINT))


class TestValueStatistics(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.h5"
        self.grid = _grid()

    def tearDown(self):
        self.tmp.cleanup()

    def test_extremes_are_recorded_not_clipped(self):
        import h5py
        import numpy as np

        arr = np.full(self.grid.shape, 1.0, dtype="float32")
        arr[0, 0] = -11.82      # a real value from the Dry Creek snow depth
        arr[0, 1] = 13.41
        with h5py.File(self.path, "w") as h5:
            group = h5.require_group("science/LIDAR/SD/20200219")
            B.write_array(group, "snow_depth", arr, description="x",
                          resampling_method="bilinear", source="y",
                          grid=self.grid)
        with h5py.File(self.path, "r") as h5:
            ds = h5["science/LIDAR/SD/20200219/snow_depth"]
            self.assertAlmostEqual(float(ds.attrs["value_min"]), -11.82, places=2)
            self.assertAlmostEqual(float(ds.attrs["value_max"]), 13.41, places=2)
            self.assertEqual(ds.attrs["value_stats_stage"], "stored_array")
            self.assertNotIn("no clipping or filtering", ds.attrs["value_stats_note"])
            # and the data itself is untouched
            self.assertAlmostEqual(float(ds[0, 0]), -11.82, places=2)

    def test_stats_ignore_nodata(self):
        import h5py
        import numpy as np

        arr = np.full(self.grid.shape, 5.0, dtype="float32")
        arr[: self.grid.height // 2, :] = np.nan
        with h5py.File(self.path, "w") as h5:
            group = h5.require_group("science/LIDAR/DEM/grids")
            B.write_array(group, "elevation", arr, description="x",
                          resampling_method="bilinear", source="y",
                          grid=self.grid)
        with h5py.File(self.path, "r") as h5:
            ds = h5["science/LIDAR/DEM/grids/elevation"]
            self.assertAlmostEqual(float(ds.attrs["value_median"]), 5.0)


# ---------------------------------------------------------------------
# Download integrity
#
# asf.download_url returns normally on a truncated transfer, so the
# integrity check has to sit inside the retried block or a short file is
# seen once and given up on. This happened for real: a 162 MB amplitude
# archive arrived as 69 MB.
# ---------------------------------------------------------------------


class TestDownloadIntegrity(unittest.TestCase):
    def setUp(self):
        import hashlib
        import tempfile
        from pathlib import Path
        from unittest import mock

        # The retry backoff is real seconds; the suite is a smoke test on
        # Borah and must stay fast, so the sleeping is stubbed out.
        self._sleep = mock.patch("build_hdf5.time.sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "x.zip"
        self.path.write_bytes(b"hello world")
        self.md5 = hashlib.md5(b"hello world").hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def test_md5_of_a_known_payload(self):
        self.assertEqual(B.file_md5(self.path), self.md5)

    def test_sound_file_reports_no_problem(self):
        self.assertIsNone(B.verify_download(self.path, 11, self.md5))

    def test_missing_file_is_reported(self):
        self.assertIn("missing",
                      B.verify_download(self.dir / "nope.zip", 11, None))

    def test_truncated_file_is_reported(self):
        self.assertIn("expected", B.verify_download(self.path, 999, None))

    def test_right_length_wrong_content_is_caught_by_md5(self):
        # The corruption a size check alone cannot see.
        self.path.write_bytes(b"HELLO WORLD")
        self.assertIsNone(B.verify_download(self.path, 11, None))
        self.assertIn("md5", B.verify_download(self.path, 11, self.md5))

    def test_absent_md5_falls_back_to_size_only(self):
        self.assertIsNone(B.verify_download(self.path, 11, None))

    def test_a_truncated_download_is_retried_until_complete(self):
        from unittest import mock

        state = {"n": 0}
        dest = self.dir / "prod.zip"

        def flaky_download(url, path, filename, session):
            state["n"] += 1
            (self.dir / filename).write_bytes(b"x" * (5 if state["n"] < 3 else 11))

        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        fake_asf.download_url.side_effect = flaky_download
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            got = B.download_uavsar(product, self.dir, session=None)
        self.assertEqual(got, dest)
        self.assertEqual(state["n"], 3)
        self.assertEqual(dest.stat().st_size, 11)

    def test_a_download_that_never_completes_eventually_raises(self):
        from unittest import mock

        dest = self.dir / "prod.zip"

        def always_short(url, path, filename, session):
            (self.dir / filename).write_bytes(b"x" * 5)

        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        fake_asf.download_url.side_effect = always_short
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            with self.assertRaises(RuntimeError):
                B.download_uavsar(product, self.dir, session=None)

    def test_a_complete_existing_download_is_not_refetched(self):
        from unittest import mock

        dest = self.dir / "prod.zip"
        dest.write_bytes(b"x" * 11)
        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            B.download_uavsar(product, self.dir, session=None)
        fake_asf.download_url.assert_not_called()

    def test_a_stale_partial_file_is_discarded_before_refetching(self):
        from unittest import mock

        dest = self.dir / "prod.zip"
        dest.write_bytes(b"x" * 4)          # left over from a killed job
        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}

        def good_download(url, path, filename, session):
            (self.dir / filename).write_bytes(b"x" * 11)

        fake_asf = mock.MagicMock()
        fake_asf.download_url.side_effect = good_download
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            B.download_uavsar(product, self.dir, session=None)
        self.assertEqual(dest.stat().st_size, 11)


    def test_no_part_file_survives_a_successful_download(self):
        from unittest import mock

        def good_download(url, path, filename, session):
            (self.dir / filename).write_bytes(b"x" * 11)

        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        fake_asf.download_url.side_effect = good_download
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            B.download_uavsar(product, self.dir, session=None)
        self.assertTrue((self.dir / "prod.zip").exists())
        self.assertFalse((self.dir / "prod.zip.part").exists())

    def test_a_failed_download_leaves_no_full_looking_archive(self):
        # A killed job must not leave something the next run mistakes for
        # a complete download.
        from unittest import mock

        def always_short(url, path, filename, session):
            (self.dir / filename).write_bytes(b"x" * 5)

        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        fake_asf.download_url.side_effect = always_short
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            with self.assertRaises(RuntimeError):
                B.download_uavsar(product, self.dir, session=None)
        self.assertFalse((self.dir / "prod.zip").exists())
        self.assertFalse((self.dir / "prod.zip.part").exists())


    def test_an_unremovable_stale_file_fails_fast(self):
        # Downloading first and discovering at rename time that the target is
        # locked would transfer the archive four times for nothing.
        from unittest import mock

        dest = self.dir / "prod.zip"
        dest.write_bytes(b"x" * 4)
        product = {"filename": "prod.zip", "url": "http://example/prod.zip",
                   "bytes_": 11, "md5sum": ""}
        fake_asf = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"asf_search": fake_asf}):
            with mock.patch.object(type(dest), "unlink",
                                   side_effect=PermissionError("locked")):
                with self.assertRaises(RuntimeError) as ctx:
                    B.download_uavsar(product, self.dir, session=None)
        self.assertIn("cannot be removed", str(ctx.exception))
        fake_asf.download_url.assert_not_called()


class TestUavsarProductMd5(unittest.TestCase):
    def test_product_record_carries_the_md5(self):
        self.assertTrue(hasattr(_product(), "md5sum"))

    def test_json_round_trip_preserves_the_md5(self):
        p = _product()
        p.md5sum = "abc123"
        self.assertEqual(p.to_json()["md5sum"], "abc123")
        self.assertEqual(B._product_from_json(p.to_json()).md5sum, "abc123")


# ---------------------------------------------------------------------
# Annotation / binary shape reconciliation
#
# Scene lowman_23205_20007-003_20011-003 ships an annotation saying
# 16045 x 24939 while the binary is exactly 16046 x 24941 float32.
# ---------------------------------------------------------------------


def _layout(rows=16045, cols=24939, dtype="float32"):
    return B.GrdLayout(rows=rows, cols=cols, lat0=44.5, lon0=-115.5,
                       dlat=-5.556e-05, dlon=5.556e-05, dtype=dtype)


class TestReconcileGrdShape(unittest.TestCase):
    def test_the_real_mismatch_is_recovered_exactly(self):
        fixed, note = B.reconcile_grd_shape(1600813144, _layout())
        self.assertIsNotNone(fixed)
        self.assertEqual((fixed.rows, fixed.cols), (16046, 24941))
        self.assertEqual(fixed.rows * fixed.cols * 4, 1600813144)
        self.assertIn("16046x24941", note)

    def test_a_matching_size_is_left_alone(self):
        L = _layout()
        fixed, note = B.reconcile_grd_shape(L.rows * L.cols * 4, L)
        self.assertIs(fixed, L)
        self.assertIsNone(note)

    def test_georeferencing_survives_the_correction(self):
        # row_addr/col_addr/row_mult/col_mult describe the first pixel and the
        # step, so they must not move when the extent changes.
        L = _layout()
        fixed, _ = B.reconcile_grd_shape(1600813144, L)
        self.assertEqual(fixed.transform, L.transform)
        self.assertEqual((fixed.lat0, fixed.lon0), (L.lat0, L.lon0))
        self.assertEqual((fixed.dlat, fixed.dlon), (L.dlat, L.dlon))

    def test_shape_is_updated_consistently(self):
        fixed, _ = B.reconcile_grd_shape(1600813144, _layout())
        self.assertEqual(fixed.shape, (16046, 24941))

    def test_a_size_that_is_not_whole_samples_is_refused(self):
        fixed, why = B.reconcile_grd_shape(1600813146, _layout())
        self.assertIsNone(fixed)
        self.assertIn("whole number", why)

    def test_a_wildly_different_size_is_refused_not_guessed(self):
        # A shape must be found near the annotation, or the file is rejected.
        # Silently accepting a distant factorisation would shear the image.
        fixed, why = B.reconcile_grd_shape(4 * 1000 * 1000, _layout())
        self.assertIsNone(fixed)
        self.assertIn("no shape near", why)

    def test_complex_itemsize_is_respected(self):
        # .int is complex64: 8 bytes per sample, not 4.
        L = _layout(dtype="complex64")
        fixed, note = B.reconcile_grd_shape(16046 * 24941 * 8, L)
        self.assertIsNotNone(fixed)
        self.assertEqual((fixed.rows, fixed.cols), (16046, 24941))

    def test_correction_stays_within_a_tight_neighbourhood(self):
        # A shape 40 rows away is not a reconciliation, it is a different file.
        L = _layout()
        far = (L.rows + 40) * L.cols * 4
        fixed, _ = B.reconcile_grd_shape(far, L)
        self.assertIsNone(fixed)

    def test_reconciled_window_still_reads_the_right_block(self):
        # End to end on a synthetic .grd whose annotation understates its size.
        import tempfile
        import zipfile
        from pathlib import Path

        import numpy as np
        from rasterio.transform import Affine
        from rasterio.warp import transform_bounds

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            true_rows, true_cols = 42, 61          # annotation will say 40x60
            data = np.arange(true_rows * true_cols, dtype="float32") \
                     .reshape(true_rows, true_cols)
            raw = tmp / "x.unw.grd"
            data.tofile(raw)
            zp = tmp / "a.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.write(raw, "s_L090HH_01.unw.grd")

            ann = B.read_annotation_text(ANN_TEXT) if hasattr(B, "read_annotation_text") else None
            path = tmp / "t.ann"
            path.write_text(ANN_TEXT, encoding="utf-8")
            layout = B.grd_layout(B.read_annotation(path), "unw")   # 40 x 60
            self.assertEqual(layout.shape, (40, 60))

            lon0, lat0, step = -115.5, 44.5, 5.556e-05
            sub = (lon0 + 5 * step, lat0 - 30 * step,
                   lon0 + 40 * step, lat0 - 5 * step)
            utm = transform_bounds("EPSG:4326", "EPSG:6340", *sub, densify_pts=21)
            grid = B.derive_common_grid(
                "EPSG:6340", Affine(3.0, 0.0, 600000.0, 0.0, -3.0, 4940000.0), utm)

            got = B.read_grd_from_zip(zp, "s_L090HH_01.unw.grd", layout, grid,
                                      margin_px=1)
            self.assertIsNotNone(got)
            block, _tf = got
            # Values must come from the TRUE 61-wide layout, not the annotated 60.
            self.assertTrue(np.all(np.isin(block, data)))
            self.assertGreater(block.size, 0)


# ---------------------------------------------------------------------
# Artifact removal and gap filling
#
# The governing rule: an artifact is removed, a measurement is kept
# exactly as published, and nothing is ever clipped to a boundary.
# ---------------------------------------------------------------------


class TestCleanArray(unittest.TestCase):
    def test_undeclared_sentinel_is_removed(self):
        import numpy as np

        a = np.full((5, 5), 2000.0)
        a[2, 2] = -10000.0
        out, st = B.clean_array(a, "dem_tiff", fill_gaps=False)
        self.assertEqual(st["sentinels"], 1)
        self.assertFalse(np.isfinite(out[2, 2]))

    def test_small_negative_snow_depth_is_preserved(self):
        # These are genuine measurement noise where true depth is near zero.
        # Removing them biases every low-snow area upward.
        import numpy as np

        sd = np.array([[0.5, -0.12, 0.3],
                       [-0.05, 0.9, -0.20],
                       [0.4, 0.2, -0.18]], dtype="float32")
        out, st = B.clean_array(sd, "SD", fill_gaps=False)
        self.assertEqual(st["out_of_range"], 0)
        self.assertEqual(int(np.isfinite(out).sum()), sd.size)
        self.assertEqual(int((out < 0).sum()), 4)

    def test_gross_negative_snow_depth_is_removed(self):
        import numpy as np

        sd = np.full((5, 5), 1.2, dtype="float32")
        sd[2, 2] = -24.7
        out, st = B.clean_array(sd, "SD", fill_gaps=False)
        self.assertEqual(st["out_of_range"], 1)
        self.assertFalse(np.isfinite(out[2, 2]))

    def test_values_are_removed_not_clipped(self):
        # Clipping would invent a value at the boundary and pile up a spike
        # in the histogram there.
        import numpy as np

        sd = np.full((4, 4), 1.0, dtype="float32")
        sd[0, 0] = 99.0
        out, _ = B.clean_array(sd, "SD", fill_gaps=False)
        self.assertFalse(np.isfinite(out[0, 0]))
        self.assertEqual(int((out == 20.0).sum()), 0)

    def test_one_spike_costs_exactly_one_cell(self):
        import numpy as np

        t = np.tile(np.linspace(1000, 1100, 9), (9, 1))
        t[4, 4] += 400
        out, st = B.clean_array(t, "DEM", fill_gaps=False)
        self.assertEqual(st["spikes"], 1)
        self.assertFalse(np.isfinite(out[4, 4]))
        for r, c in ((3, 4), (5, 4), (4, 3), (4, 5)):
            self.assertTrue(np.isfinite(out[r, c]),
                            f"neighbour {(r, c)} was wrongly removed")

    def test_real_terrain_is_left_alone(self):
        import numpy as np

        smooth = np.tile(np.linspace(1000, 1100, 9), (9, 1))
        _, st = B.clean_array(smooth, "DEM", fill_gaps=False)
        self.assertEqual(st["spikes"], 0)
        self.assertEqual(st["out_of_range"], 0)

    def test_a_pinhole_is_filled(self):
        import numpy as np

        g = np.full((9, 9), 100.0)
        g[4, 4] = np.nan
        out, st = B.clean_array(g, "DEM")
        self.assertGreaterEqual(st["gaps_filled"], 1)
        self.assertTrue(np.isfinite(out[4, 4]))
        self.assertAlmostEqual(float(out[4, 4]), 100.0, places=3)

    def test_a_large_hole_is_not_invented(self):
        # Interpolating across a big gap is invention, not interpolation.
        import numpy as np

        g = np.full((11, 11), 100.0)
        g[3:8, 3:8] = np.nan          # 5x5 hole
        out, _ = B.clean_array(g, "DEM")
        self.assertFalse(np.isfinite(out[5, 5]),
                         "the interior of a large hole must stay nodata")

    def test_gap_filling_can_be_switched_off(self):
        import numpy as np

        g = np.full((7, 7), 50.0)
        g[3, 3] = np.nan
        out, st = B.clean_array(g, "DEM", fill_gaps=False)
        self.assertEqual(st["gaps_filled"], 0)
        self.assertFalse(np.isfinite(out[3, 3]))

    def test_complex_wrapped_phase_is_never_altered(self):
        import numpy as np

        c = (np.ones((4, 4)) + 1j * np.ones((4, 4))).astype("complex64")
        out, st = B.clean_array(c, "int")
        self.assertTrue(np.array_equal(out, c))
        self.assertEqual(st["removed_total"], 0)

    def test_stats_always_carry_every_key(self):
        import numpy as np

        for kind, arr in (("SD", np.ones((4, 4), dtype="float32")),
                          ("int", np.ones((4, 4), dtype="complex64"))):
            _, st = B.clean_array(arr, kind)
            for k in ("sentinels", "out_of_range", "spikes", "gaps_filled",
                      "removed_total"):
                self.assertIn(k, st, f"{kind} stats missing {k}")

    def test_coherence_outside_zero_to_one_is_removed(self):
        import numpy as np

        cor = np.full((5, 5), 0.4, dtype="float32")
        cor[1, 1] = 1.8
        _, st = B.clean_array(cor, "cor", fill_gaps=False)
        self.assertEqual(st["out_of_range"], 1)

    def test_an_unknown_kind_is_left_untouched(self):
        # No declared range means no basis for judging a value bad.
        import numpy as np

        a = np.array([[1e6, -1e6], [0.0, 5.0]], dtype="float32")
        _, st = B.clean_array(a, "something_new", fill_gaps=False)
        self.assertEqual(st["out_of_range"], 0)

    def test_the_snow_depth_floor_sits_between_noise_and_artifact(self):
        # -1 m keeps genuine near-zero noise and excludes the -5 m artifacts.
        lo, hi = B.PLAUSIBLE_RANGE["SD"]
        self.assertLess(lo, -0.25)
        self.assertGreater(lo, -5.0)
        self.assertGreaterEqual(hi, 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
