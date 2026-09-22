"""SNEX-007 integration checks using temporary archives and synthetic inputs.

These tests change the dependency stage, not the scientific formulas: the
current aspect convention and incidence geometry remain covered separately by
test_scientific_methods.py. No production archive or network access is used.
"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import h5py
import numpy as np
from pyproj import CRS

import enrich_hdf5 as E


SHAPE = (40, 40)
RESOLUTION = 3.0
TRANSFORM = [3.0, 0.0, 600000.0, 0.0, -3.0, 4940000.0]
EPSG = 6340
DEM = "science/LIDAR/DEM/grids/elevation"
VEGETATION = [f"science/LIDAR/VH/{date}/veg_height"
              for date in ("20200212", "20200219")]
FLIGHT = "FIXTURE_CUTFROMLOWMAN05208_20200212_20200219"
RADAR_SOURCE = f"science/UAVSAR/INTERFEROMETRY_GRD/{FLIGHT}"
RADAR_OUTPUT = "science/UAVSAR/20200212_20200219/LOWMAN05208"
GEOMETRY = f"{RADAR_OUTPUT}/GEOMETRY"
ANNOTATION = {
    "peg_latitude_deg": 44.0,
    "peg_longitude_deg": -115.0,
    "peg_heading_deg": 15.0,
    "platform_altitude_m": 12000.0,
    "radar_look_direction": "Left",
}
INPUT_COUNTS = {
    "cleaning_sentinels_removed": 3,
    "cleaning_out_of_range_removed": 5,
    "cleaning_spikes_removed": 7,
    "cleaning_gaps_interpolated": 11,
}


def identify(h5):
    h5.require_group("identification").attrs.update({
        "common_grid_resolution_m": RESOLUTION,
        "common_crs_epsg": EPSG,
        "common_crs_wkt": CRS.from_epsg(EPSG).to_wkt(),
        "common_grid_transform": TRANSFORM,
        "common_grid_shape": SHAPE,
        "chunk_edge_px": 16,
    })


def source_array(h5, path, data):
    ds = h5.create_dataset(path, data=data)
    ds.attrs.update(INPUT_COUNTS)
    ds.attrs["source_filename"] = "synthetic-input.tif"
    return ds


def direct_canopy_fraction(vegetation):
    """Independent finite-cell count ratio in the actual 11-cell window."""
    height, width = vegetation.shape
    result = np.full(vegetation.shape, np.nan, dtype="float32")
    for row in range(height):
        for col in range(width):
            window = vegetation[max(0, row - 5):min(height, row + 6),
                                max(0, col - 5):min(width, col + 6)]
            finite = window[np.isfinite(window)]
            if finite.size:
                result[row, col] = np.count_nonzero(finite >= 2.0) / finite.size
    return result


def incidence_from(dem):
    return E.local_incidence(
        dem, TRANSFORM, EPSG,
        ANNOTATION["peg_latitude_deg"], ANNOTATION["peg_longitude_deg"],
        ANNOTATION["peg_heading_deg"], ANNOTATION["platform_altitude_m"],
        ANNOTATION["radar_look_direction"])


class TestCleanedDerivativeInputs(unittest.TestCase):
    def test_copied_auxiliary_preserves_original_producer(self):
        path = 'science/LIDAR/DEM/grids/quality_flags'
        with h5py.File(self.source, 'r') as source, h5py.File(self.output, 'r') as output:
            np.testing.assert_array_equal(output[path][...], source[path][...])
            self.assertEqual(dict(output[path].attrs), dict(source[path].attrs))

    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.directory = Path(temp.name)
        cls.source = cls.directory / "source.h5"
        cls.output = cls.directory / "source.enriched.h5"

        rows, cols = np.indices(SHAPE, dtype="float32")
        cls.source_dem = np.full(SHAPE, np.nan, dtype="float32")
        cls.source_dem[2:26, 2:26] = (2000.0 + rows * 0.75 + cols * 0.5)[2:26, 2:26]
        cls.source_dem[12, 12] = 15000.0
        cls.source_dem[35, 35] = 1800.0
        cls.expected_dem = cls.source_dem.copy()
        cls.expected_dem[12, 12] = np.nan  # current elevation range screen
        cls.expected_dem[35, 35] = np.nan  # separate component below 100 cells

        cls.source_vegetation = {}
        cls.expected_vegetation = {}
        for index, path in enumerate(VEGETATION):
            vegetation = np.full(SHAPE, np.nan, dtype="float32")
            vegetation[2:26, 2:26] = 0.0
            if index == 0:
                vegetation[6:20, 6:20] = 3.0
            else:
                vegetation[12:26, 2:26] = 4.0
            vegetation[10, 10] = 130.0
            vegetation[14, 14] = np.nan
            vegetation[35, 35] = 3.0
            cls.source_vegetation[path] = vegetation
            expected = vegetation.copy()
            expected[10, 10] = np.nan
            expected[35, 35] = np.nan
            cls.expected_vegetation[path] = expected

        cls.source_cor = np.full(SHAPE, 0.8, dtype="float32")
        cls.source_cor[0, 0] = 0.0
        cls.source_cor[1, 1] = 1.3
        cls.expected_cor = cls.source_cor.copy()
        cls.expected_cor[0, 0] = np.nan
        cls.expected_cor[1, 1] = np.nan
        cls.projection_dem = np.full(SHAPE, 2300.0, dtype="float32")
        cls.projection_dem[3, 3] = -10000.0

        with h5py.File(cls.source, "w") as h5:
            identify(h5)
            source_array(h5, DEM, cls.source_dem)
            auxiliary = h5.create_dataset('science/LIDAR/DEM/grids/quality_flags', data=[0, 1, 2])
            auxiliary.attrs['build_provenance_json'] = json.dumps({
                'artifact_id': 'original-flags', 'stage': 'provider-quality-flags'})
            auxiliary.attrs['artifact_id'] = 'original-flags'
            for path, vegetation in cls.source_vegetation.items():
                source_array(h5, path, vegetation)
            source_array(h5, f"{RADAR_SOURCE}/HH/cor", cls.source_cor)
            source_array(h5, f"{RADAR_SOURCE}/GEOMETRY/hgt", cls.projection_dem)
            h5[RADAR_SOURCE].attrs["source_url"] = "https://fixture.invalid/product.zip"

        cls.original_bytes = cls.source.read_bytes()
        # Patch only annotation retrieval/parsing. The incidence spy executes
        # the real geometry function with pyproj's installed local CRS data.
        with patch.object(E, "cached_annotation", return_value={}), \
                patch.object(E, "annotation_scalars", return_value=ANNOTATION), \
                patch.object(E, "local_incidence", wraps=E.local_incidence) as incidence:
            cls.result, cls.stats = E.enrich(
                cls.source, cls.output, cls.directory / "cache", session=object())
            cls.incidence_calls = incidence.call_args_list

    def test_input_archive_is_byte_identical_and_cleaning_results_are_preserved(self):
        self.assertEqual(self.result, 0)
        self.assertEqual(self.source.read_bytes(), self.original_bytes)
        expected_arrays = {DEM: self.expected_dem, **self.expected_vegetation}
        with h5py.File(self.output, "r") as h5:
            for path, expected in expected_arrays.items():
                with self.subTest(path=path):
                    ds = h5[path]
                    np.testing.assert_array_equal(ds[...], expected)
                    self.assertEqual(ds.attrs["out_of_range_set_to_nodata"], 1)
                    self.assertEqual(ds.attrs["speckle_components_removed"], 1)
                    self.assertEqual(ds.attrs["speckle_cells_removed"], 1)
                    for key, count in INPUT_COUNTS.items():
                        self.assertEqual(ds.attrs[key], count)
                    history = json.loads(ds.attrs["processing_history"])
                    for key, count in INPUT_COUNTS.items():
                        self.assertEqual(history[-1]["input_processing_metadata"][key], count)
            np.testing.assert_array_equal(h5[f"{RADAR_OUTPUT}/HH/cor"][...], self.expected_cor)
        self.assertEqual(self.stats["out_of_range"], 4)
        self.assertEqual(self.stats["speckle_cells"], 3)

    def test_slope_and_aspect_match_the_stored_clean_dem(self):
        input_slope, input_aspect = E.slope_aspect(self.source_dem, RESOLUTION)
        with h5py.File(self.output, "r") as h5:
            expected = E.slope_aspect(h5[DEM][...], RESOLUTION)
            for name, cleaned_result, input_result in zip(
                    ("slope", "aspect"), expected, (input_slope, input_aspect)):
                with self.subTest(layer=name):
                    stored = h5[f"{E.DERIVED_GROUP}/{name}"][...]
                    np.testing.assert_array_equal(stored, cleaned_result)
                    # Both the formerly valid outlier and its Horn neighbours
                    # must lose derivatives once cleaning removes that cell.
                    self.assertTrue(np.isnan(stored[11:14, 11:14]).all())
                    self.assertTrue(np.isfinite(input_result[11:14, 11:14]).all())

    def test_every_canopy_date_uses_its_stored_clean_vegetation(self):
        with h5py.File(self.output, "r") as h5:
            for path in VEGETATION:
                with self.subTest(path=path):
                    date = path.split("/")[-2]
                    stored = h5[f"{E.DERIVED_GROUP}/forest_cover_fraction_{date}"][...]
                    expected = direct_canopy_fraction(h5[path][...])
                    np.testing.assert_array_equal(stored, expected)
                    self.assertFalse(np.allclose(
                        expected, direct_canopy_fraction(self.source_vegetation[path]),
                        equal_nan=True))
                    self.assertTrue(np.isnan(h5[path][14, 14]))
                    self.assertTrue(np.isfinite(stored[14, 14]))
                    self.assertTrue(np.isnan(stored[35, 35]))
                    self.assertTrue(np.isnan(stored[39, 0]))

    def test_incidence_receives_the_stored_clean_dem_and_uses_its_mask(self):
        self.assertEqual(len(self.incidence_calls), 1)
        with h5py.File(self.output, "r") as h5:
            cleaned_dem = h5[DEM][...]
            np.testing.assert_array_equal(self.incidence_calls[0].args[0], cleaned_dem)
            expected = incidence_from(cleaned_dem)
            original = incidence_from(self.source_dem)
            for name, expected_array, original_array in zip(
                    ("local_incidence_angle", "incidence_angle_flat"), expected, original):
                with self.subTest(layer=name):
                    stored = h5[f"{GEOMETRY}/{name}"][...]
                    np.testing.assert_array_equal(stored, expected_array)
                    np.testing.assert_array_equal(np.isfinite(stored), np.isfinite(cleaned_dem))
                    self.assertTrue(np.isnan(stored[12, 12]))
                    self.assertTrue(np.isfinite(original_array[12, 12]))

    def test_coherence_mask_keeps_its_existing_cleaned_radar_dependency(self):
        expected = np.ones(SHAPE, dtype="uint8")
        expected[0, 0] = 255
        expected[1, 1] = 255
        with h5py.File(self.output, "r") as h5:
            np.testing.assert_array_equal(h5[f"{RADAR_OUTPUT}/HH/coherence_mask"][...], expected)

    def test_projection_dem_comparison_uses_the_stored_clean_dem(self):
        with h5py.File(self.output, "r") as h5:
            cleaned_dem = h5[DEM][...]
            overlap = np.isfinite(cleaned_dem) & (self.projection_dem > -100.0)
            differences = self.projection_dem[overlap] - cleaned_dem[overlap]
            attrs = h5[RADAR_OUTPUT].attrs
            self.assertEqual(attrs["projection_dem_comparison_status"], "computed")
            self.assertEqual(attrs["projection_dem_compared_cells"], 574)
            self.assertEqual(attrs["projection_dem_reference_dataset"], DEM)
            self.assertEqual(attrs["projection_dem_reference_archive"], "self")
            self.assertEqual(attrs["projection_dem_reference_stage"], "enriched_base_after_cleaning")
            self.assertAlmostEqual(attrs["projection_dem_median_offset_m"], float(np.median(differences)))
            self.assertAlmostEqual(attrs["projection_dem_mean_abs_offset_m"], float(np.mean(np.abs(differences))))
            self.assertAlmostEqual(attrs["projection_dem_rmse_m"], float(np.sqrt(np.mean(differences ** 2))))

    def test_derived_layers_record_their_self_contained_input_stage(self):
        paths = {
            f"{E.DERIVED_GROUP}/slope": DEM,
            f"{E.DERIVED_GROUP}/aspect": DEM,
            f"{GEOMETRY}/local_incidence_angle": DEM,
            f"{GEOMETRY}/incidence_angle_flat": DEM,
            **{f"{E.DERIVED_GROUP}/forest_cover_fraction_{p.split('/')[-2]}": p
               for p in VEGETATION},
        }
        with h5py.File(self.output, "r") as h5:
            self.assertEqual(h5["identification"].attrs["enrichment_version"], "3.3.0")
            for path, dependency in paths.items():
                with self.subTest(path=path):
                    attrs = h5[path].attrs
                    self.assertEqual(attrs["derived_from"], dependency)
                    self.assertIn(dependency, h5)
                    self.assertEqual(attrs["derived_from_archive"], "self")
                    self.assertEqual(attrs["derived_from_stage"], "enriched_base_after_cleaning")
                    self.assertEqual(attrs["derivation_version"], "3.0" if "/GEOMETRY/" in path else
                                     "2.0" if path.endswith('/aspect') else "1.0")
                    if "/GEOMETRY/" in path:
                        self.assertEqual(attrs["look_side_mask_method"], "projected_peg_track_half_plane_v2")
                        self.assertEqual(attrs["heading_conversion_method"], "wgs84_geodesic_tangent_100m")
                        self.assertTrue(np.isfinite(attrs["track_heading_grid_deg"]))
                        self.assertEqual(attrs["radar_look_direction"], "Left")
                        self.assertIn("not a radar-swath", attrs["look_side_mask_note"])
                    if path.endswith('/aspect'):
                        self.assertEqual(attrs['aspect_convention'], 'downhill_clockwise_from_grid_north')

    def test_every_derived_family_has_local_grid_and_truthful_provenance(self):
        paths = {
            f"{E.DERIVED_GROUP}/slope": DEM,
            f"{E.DERIVED_GROUP}/aspect": DEM,
            f"{GEOMETRY}/local_incidence_angle": DEM,
            f"{GEOMETRY}/incidence_angle_flat": DEM,
            f"{RADAR_OUTPUT}/HH/coherence_mask": f"{RADAR_OUTPUT}/HH/cor",
            **{f"{E.DERIVED_GROUP}/forest_cover_fraction_{p.split('/')[-2]}": p
               for p in VEGETATION},
        }
        with h5py.File(self.output, "r") as h5:
            for path, dependency in paths.items():
                with self.subTest(path=path):
                    attrs = h5[path].attrs
                    self.assertIn("crs_wkt", attrs)
                    self.assertEqual(attrs["crs_wkt"], CRS.from_epsg(EPSG).to_wkt())
                    np.testing.assert_array_equal(attrs["transform"], TRANSFORM)
                    self.assertEqual(attrs["resolution_m"], RESOLUTION)
                    self.assertEqual(attrs["source_dataset"], "CryoGARS derived product")
                    self.assertEqual(attrs["resampling_method"], "none")
                    self.assertIn("derived", attrs["resampling_note"])
                    self.assertEqual(attrs["derived_from"], dependency)
                    self.assertEqual(attrs["derived_from_archive"], "self")
                    self.assertEqual(attrs["derived_from_stage"], "enriched_base_after_cleaning")
                    self.assertIn("method", attrs)
            mask = h5[f"{RADAR_OUTPUT}/HH/coherence_mask"]
            self.assertEqual(mask.attrs.get("method"), "coherence_threshold_inclusive")
            self.assertEqual(mask.attrs["coherence_threshold"], 0.3)
            self.assertEqual(mask.dtype, np.dtype("uint8"))
            expected = np.where(np.isfinite(self.expected_cor),
                                self.expected_cor >= 0.3, 255).astype("uint8")
            np.testing.assert_array_equal(mask[...], expected)

    def test_new_enrichment_lineage_distinguishes_unknown_parent_history(self):
        with h5py.File(self.output, 'r') as h5:
            record = json.loads(h5['identification'].attrs['build_provenance_json'])
            self.assertEqual(record['stage'], 'enrichment')
            self.assertEqual(record['source_status'], 'unchanged_since_capture')
            self.assertEqual(record['parent']['lineage']['status'], 'unknown_not_recorded')
            self.assertEqual(record['parent']['file']['identity_method'], 'filename_size_mtime_not_content_hash')
            self.assertEqual(record['parameters']['with_insitu'], False)
            self.assertEqual(record['parameters']['grid']['epsg'], EPSG)
            self.assertIn('pyproj', record['dependencies'])
            self.assertTrue(any(p.endswith('enrich_hdf5.py') for p in record['sources']))
            self.assertEqual(record['inputs'][0]['scalars'], ANNOTATION)
            for path in (DEM, f'{GEOMETRY}/local_incidence_angle', f'{E.DERIVED_GROUP}/aspect'):
                link = json.loads(h5[path].attrs['build_provenance_json'])
                self.assertEqual(link['artifact_id'], record['artifact_id'])
                self.assertEqual(link['record_path'], '/identification')
                self.assertEqual(link['dataset'], '/'+path)


class TestEntirelyRemovedDem(unittest.TestCase):
    def test_range_removed_dem_produces_empty_terrain_and_incidence(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            source, output = directory / "empty.h5", directory / "empty.enriched.h5"
            with h5py.File(source, "w") as h5:
                identify(h5)
                source_array(h5, DEM, np.full(SHAPE, 15000.0, dtype="float32"))
                source_array(h5, f"{RADAR_SOURCE}/HH/cor", np.ones(SHAPE, dtype="float32"))
                source_array(h5, f"{RADAR_SOURCE}/GEOMETRY/hgt",
                             np.full(SHAPE, 2300.0, dtype="float32"))
                h5[RADAR_SOURCE].attrs["source_url"] = "https://fixture.invalid/product.zip"
            original_bytes = source.read_bytes()
            with patch.object(E, "cached_annotation", return_value={}), \
                    patch.object(E, "annotation_scalars", return_value=ANNOTATION), \
                    warnings.catch_warnings():
                # Existing incidence/logging math may warn about an empty
                # slice; the contract here is safe, correctly empty outputs.
                warnings.filterwarnings("ignore", category=RuntimeWarning,
                                        message=".*(empty slice|All-NaN slice).*")
                result, stats = E.enrich(source, output, directory / "cache", session=object())
            self.assertEqual(result, 0)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual(stats["out_of_range"], np.prod(SHAPE))
            with h5py.File(output, "r") as h5:
                self.assertEqual(h5[DEM].attrs["out_of_range_set_to_nodata"], np.prod(SHAPE))
                comparison = h5[RADAR_OUTPUT].attrs
                self.assertEqual(comparison["projection_dem_comparison_status"], "no_valid_overlap")
                self.assertEqual(comparison["projection_dem_compared_cells"], 0)
                self.assertNotIn("projection_dem_median_offset_m", comparison)
                for path in (DEM, f"{E.DERIVED_GROUP}/slope", f"{E.DERIVED_GROUP}/aspect",
                             f"{GEOMETRY}/local_incidence_angle", f"{GEOMETRY}/incidence_angle_flat"):
                    with self.subTest(path=path):
                        ds = h5[path]
                        self.assertTrue(np.isnan(ds[...]).all())
                        self.assertEqual(ds.attrs["valid_pixel_count"], 0)
                        for key in ("value_min", "value_max", "value_median", "value_p1", "value_p99"):
                            self.assertNotIn(key, ds.attrs)


if __name__ == "__main__":
    unittest.main()
