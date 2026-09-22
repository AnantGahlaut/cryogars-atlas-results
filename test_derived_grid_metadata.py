"""SNEX-026 metadata writes use tiny temporary arrays; no archive repair."""
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from pyproj import CRS

import enrich_hdf5 as E


class DerivedGridMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h5 = h5py.File(Path(self.temp.name) / "tiny.h5", "w")
        self.addCleanup(self.h5.close)
        self.ident = self.h5.require_group("identification").attrs
        self.ident.update(common_crs_wkt=CRS.from_epsg(6340).to_wkt(),
                          common_grid_transform=[3, 0, 600000, 0, -3, 4940000],
                          common_grid_resolution_m=3,
                          common_grid_shape=[2, 2])
        self.group = self.h5.require_group("science/derived")
        self.input = E.write_grid(self.group, "input", np.ones((2, 2), "float32"), 2, {})
        self.attrs = {"derived_from": self.input.name, "method": "fixture_method"}

    def test_missing_or_inconsistent_grid_and_input_are_rejected_before_write(self):
        cases = ("missing_crs", "wrong_shape", "missing_input")
        for case in cases:
            with self.subTest(case=case):
                old_wkt = self.ident["common_crs_wkt"]
                attrs = dict(self.attrs)
                if case == "missing_crs":
                    del self.ident["common_crs_wkt"]
                elif case == "wrong_shape":
                    self.ident["common_grid_shape"] = [3, 2]
                else:
                    attrs["derived_from"] = "/science/missing"
                try:
                    with self.assertRaises((ValueError, KeyError)):
                        E.write_grid(self.group, case, np.zeros((2, 2), "float32"), 2, attrs)
                    self.assertNotIn(case, self.group)
                finally:
                    self.ident["common_crs_wkt"] = old_wkt
                    self.ident["common_grid_shape"] = [2, 2]

    def test_local_derivation_does_not_inherit_provider_resampling_or_identity(self):
        attrs = dict(self.attrs, resampling_method="bilinear", source_dataset="NASA input")
        result = E.write_grid(self.group, "derived", np.zeros((2, 2), "float32"), 2, attrs)
        self.assertEqual(result.attrs["resampling_method"], "none")
        self.assertEqual(result.attrs["source_dataset"], "CryoGARS derived product")
        self.assertEqual(result.attrs["derived_from"], self.input.name)
        self.assertEqual(result.attrs["method"], "fixture_method")

    def test_external_link_cannot_claim_a_same_archive_derivation(self):
        other = Path(self.temp.name) / "other.h5"
        with h5py.File(other, "w") as handle:
            handle.create_dataset("input", data=np.ones((2, 2), "float32"))
        self.h5["science/external"] = h5py.ExternalLink(str(other), "/input")
        attrs = dict(self.attrs, derived_from="/science/external")
        with self.assertRaisesRegex(ValueError, "same.archive"):
            E.write_grid(self.group, "external_result", np.zeros((2, 2), "float32"), 2, attrs)
        self.assertNotIn("external_result", self.group)

    def test_only_verified_empty_inputs_supply_an_empty_reason(self):
        empty = np.full((2, 2), np.nan, "float32")
        unexplained = E.write_grid(self.group, "unexplained", empty, 2, self.attrs)
        self.assertNotIn("empty_reason", unexplained.attrs)
        source = E.write_grid(self.group, "empty_input", empty, 2, {})
        attrs = dict(self.attrs, derived_from=source.name)
        result = E.write_grid(self.group, "explained", empty, 2, attrs)
        self.assertEqual(result.attrs.get("empty_reason"), "no_valid_input_cells")
        self.assertEqual(result.attrs["valid_pixel_count"], 0)
        self.assertEqual(result.attrs["valid_fraction"], 0)
        self.assertEqual(result.attrs["value_stats_stage"], "stored_array")
        self.assertEqual(result.attrs["value_stats_status"], "no_valid_values")


if __name__ == "__main__":
    unittest.main()
