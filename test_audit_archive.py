"""SNEX-026 audit checks on tiny, temporary HDF5 archives only."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import h5py
import numpy as np

import audit_archive as A

SHAPE = (2, 3)
TRANSFORM = [3., 0., 600000., 0., -3., 4940000.]
DEM = "science/LIDAR/DEM/grids/elevation"
SLOPE = "science/LIDAR/DERIVED/slope"
MASK = "science/UAVSAR/20200212_20200219/LINE/HH/coherence_mask"


class TestAuditArchive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def archive(self, enriched=False):
        path = self.directory / ("fixture.enriched.h5" if enriched else "fixture.h5")
        with h5py.File(path, "w") as h5:
            attrs = {key: "fixture" for key in A.REQUIRED_IDENT_ATTRS}
            attrs.update(common_grid_shape=SHAPE, common_grid_transform=TRANSFORM,
                         common_grid_resolution_m=3., common_crs_epsg=32611,
                         common_crs_wkt="fixture-wkt", site_name="Fixture")
            if enriched:
                attrs["enrichment_version"] = "3.3.0"
            h5.create_group("identification").attrs.update(attrs)
            h5.create_group("science")
            h5.create_group("matches")
        return path

    def array(self, path, name, values, **extra):
        with h5py.File(path, "a") as h5:
            ds = h5.create_dataset(name, data=np.asarray(values).reshape(SHAPE))
            attrs = dict(description="Synthetic test raster", crs_wkt="fixture-wkt",
                         transform=TRANSFORM, shape=SHAPE, nodata="NaN",
                         dtype=str(ds.dtype), resolution_m=3.,
                         resampling_method="none", source_dataset="fixture")
            attrs.update(extra)
            ds.attrs.update(attrs)

    def audit(self, path, inventory=None):
        before = path.read_bytes()
        audit = A.Audit()
        result = A.audit_site(path, audit, inventory)
        self.assertEqual(path.read_bytes(), before, "Audit must remain read-only")
        return result, audit

    def test_missing_or_malformed_grid_identification_fails_without_crashing(self):
        cases = [("common_grid_resolution_m", None),
                 ("site_name", None),
                 ("common_grid_transform", [3., 0.]),
                 ("common_grid_transform", [3., 0., np.nan, 0., -3., 1.]),
                 ("common_grid_resolution_m", "unknown"),
                 ("common_grid_resolution_m", np.inf),
                 ("common_grid_shape", [2.5, 3.]),
                 ("common_grid_shape", 3),
                 ("common_grid_shape", [0, 3]),
                 ("common_crs_epsg", np.nan)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                path = self.archive()
                self.array(path, DEM, np.ones(SHAPE, "float32"))
                with h5py.File(path, "a") as h5:
                    del h5["identification"].attrs[key]
                    if value is not None:
                        h5["identification"].attrs[key] = value
                result, audit = self.audit(path)
                self.assertTrue(audit.fail)
                self.assertTrue(result.get("unreadable"), result)
                self.assertNotIn("shape", result)

    def test_mask_nodata_is_excluded_and_zero_is_valid(self):
        path = self.archive(True)
        self.array(path, MASK, np.array([0, 1, 255, 255, 1, 0], dtype="uint8"),
                   nodata=255, nodata_value=255, valid_pixel_count=4, valid_fraction=4/6)
        result, audit = self.audit(path)
        self.assertEqual(audit.fail, [])
        self.assertEqual(result["stats"][MASK.removeprefix("science/")],
                         dict(valid=4, frac=4/6, min=0., max=1., median=.5))

    def test_numeric_float_nodata_and_complex_nonfinite_are_excluded(self):
        path = self.archive()
        self.array(path, DEM, np.array([1, -9999, 0, np.nan, np.inf, 2], "float32"),
                   nodata=-9999)
        self.array(path, "science/UAVSAR/INTERFEROMETRY_GRD/flight/HH/int",
                   np.array([1+2j, complex(1, np.inf), complex(np.nan, 0), 0j, 3j, 4j]))
        result, audit = self.audit(path)
        self.assertEqual(audit.fail, [])
        self.assertEqual(result["stats"][DEM.removeprefix("science/")]["valid"], 3)
        self.assertEqual(result["stats"]["UAVSAR/INTERFEROMETRY_GRD/flight/HH/int"]["valid"], 4)

    def test_mask_rejects_nonbinary_valid_values(self):
        path = self.archive(True)
        self.array(path, MASK, np.array([0, 1, 2, 255, 1, 0], "uint8"), nodata=255)
        _, audit = self.audit(path)
        self.assertTrue(any("coherence_mask" in msg and "0/1" in msg for msg in audit.fail), audit.fail)

    def test_recorded_validity_must_match_actual_stored_values(self):
        path = self.archive(True)
        self.array(path, MASK, np.array([0, 1, 255, 255, 1, 0], "uint8"),
                   nodata=255, valid_pixel_count=6, valid_fraction=1.)
        _, audit = self.audit(path)
        self.assertTrue(any("valid_pixel_count" in msg for msg in audit.fail), audit.fail)
        self.assertTrue(any("valid_fraction" in msg for msg in audit.fail), audit.fail)

    def test_enriched_inventory_counts_base_lidar_and_keeps_radar_units_distinct(self):
        path = self.archive(True)
        self.array(path, DEM, np.ones(SHAPE, "float32"))
        self.array(path, SLOPE, np.ones(SHAPE, "float32"))
        self.array(path, MASK, np.zeros(SHAPE, "uint8"), nodata=255)
        inventory = {"sites": {"fixture": {"lidar": [{}], "uavsar_needed": [{}, {}, {}]}}}
        result, audit = self.audit(path, inventory)
        self.assertEqual(audit.fail, [])
        self.assertEqual(result["site"], "fixture")
        self.assertEqual(result["archive_kind"], "enriched")
        self.assertEqual(result["lidar_arrays"], 1)
        self.assertEqual(result["lidar_derived_arrays"], 1)
        self.assertEqual(result["uavsar_group_basis"], "merged_acquisitions")
        self.assertEqual(result["uavsar_inventory_products"], 3)
        self.assertNotIn("uavsar_expected", result)
        inventory["sites"]["fixture"]["lidar"].append({})
        _, audit = self.audit(path, inventory)
        self.assertTrue(any("inventory expects 2" in msg for msg in audit.fail), audit.fail)

    def test_enriched_area_is_diagnostic_after_cleaning(self):
        path = self.archive(True)
        self.array(path, DEM, np.array([1, 1, 1, np.nan, np.nan, np.nan], "float32"))
        self.array(path, SLOPE, np.ones(SHAPE, "float32"))
        with patch.dict(A.README_AREA_KM2, {"fixture": 6*9/1e6}):
            result, audit = self.audit(path)
        self.assertEqual(audit.fail, [])
        self.assertEqual(result["area_layer"], DEM.removeprefix("science/"))
        self.assertEqual(result["area_err_pct"], -50.)
        self.assertEqual(result["area_check_scope"], "diagnostic_cleaned_base_lidar")
        self.assertTrue(any("cleaned" in msg for msg in audit.note), audit.note)

    def test_missing_derived_attrs_do_not_inherit_from_identification(self):
        path = self.archive(True)
        self.array(path, SLOPE, np.ones(SHAPE, "float32"))
        with h5py.File(path, "a") as h5:
            for name in ("crs_wkt", "transform", "resolution_m", "resampling_method", "source_dataset"):
                del h5[SLOPE].attrs[name]
        _, audit = self.audit(path)
        self.assertEqual(len(audit.fail), 5)
        self.assertTrue(all("missing attribute" in msg for msg in audit.fail))

    def test_known_empty_input_is_reported_without_hiding_the_empty_source(self):
        path = self.archive(True)
        empty_attrs = dict(valid_pixel_count=0, valid_fraction=0.,
                           value_stats_stage="stored_array", value_stats_status="no_valid_values")
        self.array(path, DEM, np.full(SHAPE, np.nan, "float32"), **empty_attrs)
        self.array(path, SLOPE, np.full(SHAPE, np.nan, "float32"), **empty_attrs,
                   empty_reason="no_valid_input_cells", derived_from=DEM,
                   derived_from_archive="self")
        result, audit = self.audit(path)
        self.assertEqual(len(audit.fail), 1, audit.fail)
        self.assertIn("LIDAR/DEM/grids/elevation is entirely nodata", audit.fail[0])
        self.assertTrue(any("DERIVED/slope" in msg and "no_valid_input_cells" in msg for msg in audit.warn), audit.warn)
        stat = result["stats"][SLOPE.removeprefix("science/")]
        self.assertEqual(stat["valid"], 0)
        self.assertIsNone(stat["min"])
        self.assertEqual(stat["empty_reason"], "no_valid_input_cells")

    def test_unexplained_or_inconsistent_empty_layer_remains_failure(self):
        changes = [{}, {"empty_reason": "unknown"},
                   {"empty_reason": "no_valid_input_cells", "derived_from": DEM},
                   {"empty_reason": "no_valid_input_cells", "derived_from": "science/missing"}]
        for index, extra in enumerate(changes):
            with self.subTest(case=index):
                path = self.archive(True)
                self.array(path, DEM, np.ones(SHAPE, "float32"), valid_pixel_count=6)
                self.array(path, SLOPE, np.full(SHAPE, np.nan, "float32"),
                           valid_pixel_count=0, valid_fraction=0.,
                           value_stats_stage="stored_array", value_stats_status="no_valid_values",
                           derived_from_archive="self", **extra)
                _, audit = self.audit(path)
                self.assertTrue(any("DERIVED/slope is entirely nodata" in msg for msg in audit.fail), audit.fail)

    def test_empty_input_explanation_cannot_form_a_circular_dependency(self):
        path = self.archive(True)
        attrs = dict(valid_pixel_count=0, valid_fraction=0., value_stats_stage="stored_array",
                     value_stats_status="no_valid_values", empty_reason="no_valid_input_cells",
                     derived_from_archive="self")
        self.array(path, DEM, np.full(SHAPE, np.nan, "float32"), derived_from=SLOPE, **attrs)
        self.array(path, SLOPE, np.full(SHAPE, np.nan, "float32"), derived_from=DEM, **attrs)
        _, audit = self.audit(path)
        self.assertEqual(len(audit.fail), 2, audit.fail)

    def test_dataset_grid_metadata_matches_the_entire_common_transform_and_resolution(self):
        path = self.archive(True)
        self.array(path, SLOPE, np.ones(SHAPE, "float32"),
                   transform=TRANSFORM[:2], resolution_m=24.)
        _, audit = self.audit(path)
        self.assertTrue(any("transform differs from grid" in msg for msg in audit.fail), audit.fail)
        self.assertTrue(any("resolution_m differs from grid" in msg for msg in audit.fail), audit.fail)

    def test_empty_input_explanation_cannot_use_an_external_archive(self):
        external = self.directory / "external.h5"
        with h5py.File(external, "w") as other:
            source = other.create_dataset(DEM, data=np.full(SHAPE, np.nan, "float32"))
            source.attrs["valid_pixel_count"] = 0
        path = self.archive(True)
        with h5py.File(path, "a") as h5:
            h5.require_group("science/LIDAR/DEM/grids")["elevation"] = h5py.ExternalLink(str(external), DEM)
        self.array(path, SLOPE, np.full(SHAPE, np.nan, "float32"), valid_pixel_count=0,
                   valid_fraction=0., value_stats_stage="stored_array", value_stats_status="no_valid_values",
                   empty_reason="no_valid_input_cells", derived_from=DEM, derived_from_archive="self")
        _, audit = self.audit(path)
        self.assertTrue(any("DERIVED/slope is entirely nodata" in msg for msg in audit.fail), audit.fail)

    def test_base_area_tolerance_remains_enforced(self):
        path = self.archive()
        self.array(path, DEM, np.array([1, 1, 1, np.nan, np.nan, np.nan], "float32"))
        with patch.dict(A.README_AREA_KM2, {"fixture": 6*9/1e6}):
            _, audit = self.audit(path)
        self.assertTrue(any("README" in msg and "-50.00%" in msg for msg in audit.fail), audit.fail)

    def test_cli_audits_both_layouts_without_reapplying_base_area_tolerance(self):
        raw = self.archive()
        self.array(raw, DEM, np.ones(SHAPE, "float32"))
        enriched = self.archive(True)
        self.array(enriched, DEM, np.array([1, 1, 1, np.nan, np.nan, np.nan], "float32"))
        report = self.directory / "report.json"
        with patch.dict(A.README_AREA_KM2, {"fixture": 6*9/1e6}), redirect_stdout(io.StringIO()) as output:
            code = A.main(["--out-dir", str(self.directory), "--inventory", str(self.directory / "absent.json"),
                           "--json", str(report)])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn("fixture.enriched", output.getvalue())
        self.assertEqual(json.loads(report.read_text())["fail"], [])

    def test_all_255_mask_has_zero_valid_cells_and_fails_without_explanation(self):
        path = self.archive(True)
        self.array(path, MASK, np.full(SHAPE, 255, "uint8"), nodata=255, nodata_value=255)
        result, audit = self.audit(path)
        self.assertEqual(result["stats"][MASK.removeprefix("science/")]["valid"], 0)
        self.assertTrue(any("coherence_mask is entirely nodata" in msg for msg in audit.fail), audit.fail)


if __name__ == "__main__":
    unittest.main()
