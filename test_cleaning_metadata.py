"""SNEX-005 regressions on tiny temporary HDF5 files; no production inputs."""

import json
from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from pyproj import CRS

import build_hdf5 as B
import enrich_hdf5 as E


SHAPE = (40, 40)
DEM = "science/LIDAR/DEM/grids/elevation"
SNOW = "science/LIDAR/SD/20200219/snow_depth"
EMPTY_SNOW = "science/LIDAR/SD/20200220/snow_depth"
FLIGHT = "FIXTURE_CUTFROMLOWMAN05208_20200212_20200219"
RADAR_SOURCE = f"science/UAVSAR/INTERFEROMETRY_GRD/{FLIGHT}"
RADAR_OUTPUT = "science/UAVSAR/20200212_20200219/LOWMAN05208"
BUILDER_COUNTS = {
    "cleaning_sentinels_removed": 7,
    "cleaning_out_of_range_removed": 11,
    "cleaning_spikes_removed": 13,
    "cleaning_gaps_interpolated": 17,
}
BUILDER_EVENT = {
    "stage": "builder_cleaning",
    "input_processing_metadata": {},
    "output_processing_metadata": BUILDER_COUNTS,
}
VALUE_STATS = ("value_min", "value_max", "value_median", "value_p1", "value_p99")


def identify(h5):
    ident = h5.require_group("identification")
    ident.attrs.update({
        "common_grid_resolution_m": 3.0,
        "common_crs_epsg": 6340,
        "common_crs_wkt": CRS.from_epsg(6340).to_wkt(),
        "common_grid_transform": [3.0, 0.0, 600000.0, 0.0, -3.0, 4940000.0],
        "common_grid_shape": SHAPE,
        "chunk_edge_px": 16,
    })


def source_array(h5, path, data):
    ds = h5.create_dataset(path, data=data)
    ds.attrs.update(BUILDER_COUNTS)
    ds.attrs["processing_history"] = json.dumps([BUILDER_EVENT])
    ds.attrs["source_filename"] = "fixture-source.tif"
    ds.attrs["cleaning_note"] = "Legacy builder cleaning record."
    ds.attrs["value_stats_note"] = "No clipping or filtering has been applied."
    for key in VALUE_STATS:
        ds.attrs[key] = -12345.0
    return ds


class TestBuilderCleaningMetadata(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.h5 = h5py.File(Path(temp.name) / "builder.h5", "w")
        self.addCleanup(self.h5.close)
        self.grid = B.CommonGrid(
            crs_wkt="fixture", epsg=6340,
            transform=(3.0, 0.0, 600000.0, 0.0, -3.0, 4940000.0),
            width=5, height=5, res_m=3.0)

    def write(self, data, attrs):
        return B.write_array(
            self.h5.require_group("science/fixture"), "values", data,
            description="Metadata fixture", resampling_method="nearest",
            source="fixture", grid=self.grid, extra=attrs)

    def test_complex_bypass_is_recorded_without_scalar_statistics(self):
        data = np.full((5, 5), -10000.0 + 2.0j, dtype="complex64")
        data[2, 2] = complex(np.nan, np.nan)
        cleaned, counts = B.clean_array(data, "int")
        ds = self.write(cleaned, B.cleaning_attrs(counts, "int", is_complex=True))
        np.testing.assert_array_equal(ds[...], data)
        self.assertEqual(ds.dtype, data.dtype)
        self.assertEqual(ds.attrs["cleaning_status"], "bypassed_complex")
        self.assertEqual(ds.attrs["cleaning_sentinels_removed"], 0)
        self.assertEqual(ds.attrs["cleaning_gaps_interpolated"], 0)
        for key in VALUE_STATS:
            self.assertNotIn(key, ds.attrs)
        event = json.loads(ds.attrs["processing_history"])[-1]
        self.assertEqual(event["stage"], "builder_cleaning")
        self.assertEqual(event["output_processing_metadata"]["cleaning_status"],
                         "bypassed_complex")

    def test_disabled_gap_filling_records_option_and_preserves_hole(self):
        data = np.full((5, 5), 100.0, dtype="float32")
        data[2, 2] = np.nan
        cleaned, counts = B.clean_array(data, "DEM", fill_gaps=False)
        ds = self.write(cleaned, B.cleaning_attrs(counts, "DEM", fill_gaps=False))
        np.testing.assert_array_equal(ds[...], data)
        self.assertFalse(ds.attrs["cleaning_fill_gaps_enabled"])
        self.assertEqual(ds.attrs["cleaning_gaps_interpolated"], 0)
        self.assertEqual(ds.attrs["valid_pixel_count"], 24)
        event = json.loads(ds.attrs["processing_history"])[-1]
        self.assertFalse(event["output_processing_metadata"]["cleaning_fill_gaps_enabled"])

    def test_unknown_counts_are_not_invented_as_zero(self):
        attrs = B.cleaning_attrs({"sentinels": 2}, "unknown_kind", fill_gaps=False)
        ds = self.write(np.ones((5, 5), dtype="float32"), attrs)
        self.assertEqual(ds.attrs["cleaning_sentinels_removed"], 2)
        self.assertFalse(ds.attrs["cleaning_range_configured"])
        self.assertFalse(ds.attrs["cleaning_spike_configured"])
        output = json.loads(ds.attrs["processing_history"])[-1]["output_processing_metadata"]
        for key in ("cleaning_out_of_range_removed", "cleaning_spikes_removed",
                    "cleaning_gaps_interpolated", "cleaning_removal_events"):
            self.assertNotIn(key, ds.attrs)
            self.assertNotIn(key, output)


class TestEnrichmentCleaningMetadata(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.source = self.directory / "fixture.h5"
        self.output = self.directory / "fixture.enriched.h5"

        snow = np.full(SHAPE, np.nan, dtype="float32")
        snow[5:25, 5:25] = 1.0
        snow[10, 10:13] = [-0.1, -0.3, 16.0]
        snow[35, 35] = 1.0
        self.expected_snow = np.full(SHAPE, np.nan, dtype="float32")
        self.expected_snow[5:25, 5:25] = 1.0
        self.expected_snow[10, 10:13] = [0.0, np.nan, np.nan]

        self.expected_radar = {}
        radar = {
            "amp1": np.ones(SHAPE, dtype="float32"),
            "amp2": np.full(SHAPE, 2.0, dtype="float32"),
            "cor": np.full(SHAPE, 0.5, dtype="float32"),
            "int": np.full(SHAPE, 3.0 + 4.0j, dtype="complex64"),
            "unw": np.full(SHAPE, 5.0, dtype="float32"),
        }
        for name in ("amp1", "amp2", "cor"):
            radar[name][0, :] = 0.0
        radar["cor"][1, 1] = 0.0
        radar["cor"][3, 3] = 1.5
        radar["unw"][2, 2] = 0.0
        radar["unw"][4, 4] = -3.0
        for name, data in radar.items():
            expected = data.copy()
            missing = complex(np.nan, np.nan) if name == "int" else np.nan
            expected[0, :] = missing
            expected[1, 1] = missing
            if name == "cor":
                expected[3, 3] = np.nan
            if name == "unw":
                expected[2, 2] = np.nan
            self.expected_radar[name] = expected

        with h5py.File(self.source, "w") as h5:
            identify(h5)
            source_array(h5, DEM, np.full(SHAPE, 2000.0, dtype="float32"))
            source_array(h5, SNOW, snow)
            source_array(h5, EMPTY_SNOW, np.full(SHAPE, np.nan, dtype="float32"))
            for name, data in radar.items():
                ds = source_array(h5, f"{RADAR_SOURCE}/HH/{name}", data)
                ds.attrs["out_of_range_set_to_nodata"] = 99
            # Without an amplitude/coherence probe this polarization receives
            # no swath mask, so its phase zeros must stay exactly as supplied.
            ds = source_array(h5, f"{RADAR_SOURCE}/HV/unw",
                              np.zeros(SHAPE, dtype="float32"))
            ds.attrs["out_of_range_set_to_nodata"] = 99
            ds.attrs["unw_zero_fill_masked"] = 88
            ds.attrs["swath_mask_group"] = "/obsolete/input/mask"
            ds.attrs["range_lower_bound"] = 0.0
            ds.attrs["range_upper_bound"] = 5.0
            ds.attrs["plausible_range"] = [0.0, 5.0]
        self.original_bytes = self.source.read_bytes()
        result, self.stats = E.enrich(self.source, self.output,
                                     self.directory / "cache", session=None)
        self.assertEqual(result, 0)

    def test_enrichment_preserves_existing_numeric_operations_and_source(self):
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        with h5py.File(self.output, "r") as h5:
            np.testing.assert_array_equal(h5[SNOW][...], self.expected_snow)
            np.testing.assert_array_equal(h5[DEM][...],
                                          np.full(SHAPE, 2000.0, dtype="float32"))
            self.assertTrue(np.isnan(h5[EMPTY_SNOW][...]).all())
            for name, expected in self.expected_radar.items():
                with self.subTest(layer=name):
                    ds = h5[f"{RADAR_OUTPUT}/HH/{name}"]
                    np.testing.assert_array_equal(ds[...], expected)
                    self.assertEqual(ds.dtype, expected.dtype)
            np.testing.assert_array_equal(h5[f"{RADAR_OUTPUT}/HV/unw"][...],
                                          np.zeros(SHAPE, dtype="float32"))
            mask = np.ones(SHAPE, dtype="uint8")
            mask[0, :] = 255
            mask[1, 1] = mask[3, 3] = 255
            np.testing.assert_array_equal(h5[f"{RADAR_OUTPUT}/HH/coherence_mask"][...],
                                          mask)
            snow = h5[SNOW].attrs
            self.assertEqual(snow["negatives_clipped_to_zero"], 1)
            self.assertEqual(snow["negatives_set_to_nodata"], 1)
            self.assertEqual(snow["out_of_range_set_to_nodata"], 1)
            self.assertEqual(snow["speckle_cells_removed"], 1)
            self.assertEqual(h5[f"{RADAR_OUTPUT}/HH"].attrs["swath_fill_cells_masked"], 41)
            self.assertEqual(h5[f"{RADAR_OUTPUT}/HH"].attrs["unw_zero_fill_masked"], 1)

    def test_only_an_all_zero_fill_unwrapped_phase_records_an_empty_reason(self):
        with h5py.File(self.output, "r") as h5:
            # One masked zero among valid phase, and unmasked zeros, stay unexplained.
            self.assertNotIn("empty_reason", h5[f"{RADAR_OUTPUT}/HH/unw"].attrs)
            self.assertNotIn("empty_reason", h5[f"{RADAR_OUTPUT}/HV/unw"].attrs)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, output = directory / "zero.h5", directory / "zero.enriched.h5"
            with h5py.File(source, "w") as h5:
                identify(h5)
                source_array(h5, DEM, np.full(SHAPE, 2000.0, dtype="float32"))
                for name in ("amp1", "amp2", "cor"):
                    source_array(h5, f"{RADAR_SOURCE}/HH/{name}",
                                 np.full(SHAPE, 0.5, dtype="float32"))
                source_array(h5, f"{RADAR_SOURCE}/HH/unw", np.zeros(SHAPE, dtype="float32"))
            result, _ = E.enrich(source, output, directory / "cache", session=None)
            self.assertEqual(result, 0)
            with h5py.File(output, "r") as h5:
                attrs = h5[f"{RADAR_OUTPUT}/HH/unw"].attrs
                self.assertTrue(np.isnan(h5[f"{RADAR_OUTPUT}/HH/unw"][...]).all())
                self.assertEqual(attrs["empty_reason"], "unwrapper_zero_fill")
                self.assertEqual(attrs["unw_zero_fill_masked"], SHAPE[0] * SHAPE[1])
                self.assertEqual(attrs["valid_pixel_count"], 0)

    def test_enriched_statistics_describe_only_stored_arrays(self):
        with h5py.File(self.output, "r") as h5:
            paths = [DEM, SNOW, EMPTY_SNOW]
            paths += [f"{RADAR_OUTPUT}/HH/{name}" for name in self.expected_radar]
            for path in paths:
                with self.subTest(path=path):
                    attrs = h5[path].attrs
                    self.assertNotIn("value_p1", attrs)
                    self.assertNotIn("value_p99", attrs)
                    self.assertNotIn("no clipping or filtering",
                                     str(attrs.get("value_stats_note", "")).lower())
            for path in (EMPTY_SNOW, f"{RADAR_OUTPUT}/HH/int"):
                with self.subTest(no_scalar_statistics=path):
                    for key in VALUE_STATS:
                        self.assertNotIn(key, h5[path].attrs)
            self.assertEqual(h5[SNOW].attrs["value_min"], 0.0)
            self.assertEqual(h5[SNOW].attrs["value_max"], 1.0)
            self.assertEqual(h5[SNOW].attrs["value_median"], 1.0)
            self.assertEqual(h5[EMPTY_SNOW].attrs["valid_pixel_count"], 0)

    def test_enrichment_appends_stage_history_without_losing_builder_counts(self):
        with h5py.File(self.output, "r") as h5:
            for path, stage in ((SNOW, "enrichment_lidar"),
                                (f"{RADAR_OUTPUT}/HH/cor", "enrichment_radar")):
                with self.subTest(path=path):
                    attrs = h5[path].attrs
                    for key, expected in BUILDER_COUNTS.items():
                        self.assertEqual(attrs[key], expected)
                    self.assertEqual(attrs["source_filename"], "fixture-source.tif")
                    history = json.loads(attrs["processing_history"])
                    self.assertEqual(history[:-1], [BUILDER_EVENT])
                    event = history[-1]
                    self.assertEqual(event["stage"], stage)
                    self.assertEqual(event["input_processing_metadata"]
                                          ["cleaning_sentinels_removed"], 7)
                    self.assertEqual(event["output_processing_metadata"]
                                          ["out_of_range_set_to_nodata"], 1)

    def test_radar_records_current_zero_counts_and_skipped_range_screening(self):
        with h5py.File(self.output, "r") as h5:
            for pol, leaf, count, status in (
                    ("HH", "cor", 1, "applied"),
                    ("HH", "amp1", 0, "applied"),
                    ("HH", "int", 0, "skipped_no_bounds"),
                    ("HH", "unw", 0, "skipped_no_bounds"),
                    ("HV", "unw", 0, "skipped_no_bounds")):
                with self.subTest(polarization=pol, layer=leaf):
                    attrs = h5[f"{RADAR_OUTPUT}/{pol}/{leaf}"].attrs
                    self.assertEqual(attrs["out_of_range_set_to_nodata"], count)
                    self.assertEqual(attrs.get("range_screening_status"), status)

    def test_skipped_steps_keep_prior_settings_and_counts_only_in_history(self):
        with h5py.File(self.output, "r") as h5:
            attrs = h5[f"{RADAR_OUTPUT}/HV/unw"].attrs
            self.assertEqual(attrs["swath_mask_status"], "skipped_no_mask_inputs")
            self.assertEqual(attrs["unw_zero_mask_status"], "skipped_swath_mask_unavailable")
            previous = json.loads(attrs["processing_history"])[-1]["input_processing_metadata"]
            for key, value in (("unw_zero_fill_masked", 88),
                               ("swath_mask_group", "/obsolete/input/mask"),
                               ("range_lower_bound", 0.0),
                               ("range_upper_bound", 5.0),
                               ("plausible_range", [0.0, 5.0])):
                with self.subTest(attribute=key):
                    self.assertNotIn(key, attrs)
                    self.assertEqual(previous[key], value)


class TestArchiveRecleaningMetadata(unittest.TestCase):
    def test_repeated_cleaning_preserves_history_groups_and_refreshes_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "fixture.h5"
            data = np.ones(SHAPE, dtype="float32")
            data[0, :3] = [99.0, -10000.0, np.nan]
            expected = np.ones(SHAPE, dtype="float32")
            expected[0, :3] = np.nan
            with h5py.File(path, "w") as h5:
                identify(h5)
                source_array(h5, SNOW, data)
                source_array(h5, EMPTY_SNOW,
                             np.full(SHAPE, np.nan, dtype="float32"))
                h5["science"].attrs["provenance_note"] = "preserve group provenance"
                h5["science/LIDAR"].attrs["swath_fill_cells_masked"] = 23
                h5["science/LIDAR"].attrs["processing_history"] = json.dumps([BUILDER_EVENT])

            first_history = None
            for pass_number in (1, 2):
                self.assertEqual(B.run_clean(["fixture"], directory, fill_gaps=False), 0)
                with h5py.File(path, "r") as h5:
                    ds = h5[SNOW]
                    np.testing.assert_array_equal(ds[...], expected)
                    self.assertEqual(ds.dtype, np.dtype("float32"))
                    with self.subTest(pass_number=pass_number, check="statistics"):
                        for key in VALUE_STATS:
                            self.assertEqual(ds.attrs[key], 1.0)
                        self.assertEqual(ds.attrs["valid_pixel_count"], 1597)
                        for key in VALUE_STATS:
                            self.assertNotIn(key, h5[EMPTY_SNOW].attrs)
                    with self.subTest(pass_number=pass_number, check="group provenance"):
                        self.assertEqual(h5["science"].attrs.get("provenance_note"),
                                         "preserve group provenance")
                        self.assertEqual(h5["science/LIDAR"].attrs.get("swath_fill_cells_masked"), 23)
                        self.assertEqual(json.loads(h5["science/LIDAR"].attrs
                                                     .get("processing_history", "[]")),
                                         [BUILDER_EVENT])
                    with self.subTest(pass_number=pass_number, check="history"):
                        history = json.loads(ds.attrs["processing_history"])
                        self.assertEqual(len(history), pass_number + 1)
                        self.assertEqual(history[0], BUILDER_EVENT)
                        event = history[-1]
                        self.assertEqual(event["stage"], "archive_recleaning")
                        prior = event["input_processing_metadata"]
                        self.assertEqual(prior["cleaning_sentinels_removed"],
                                         7 if pass_number == 1 else 1)
                        output = event["output_processing_metadata"]
                        for key in ("cleaning_sentinels_removed",
                                    "cleaning_out_of_range_removed"):
                            self.assertEqual(output[key], 1 if pass_number == 1 else 0)
                        self.assertEqual(output["cleaning_gaps_interpolated"], 0)
                        if first_history is not None:
                            self.assertEqual(history[:-1], first_history)
                        first_history = history


if __name__ == "__main__":
    unittest.main(verbosity=2)
