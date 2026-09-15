"""Focused regression tests for the standalone SnowEx viewer generator."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import make_explorer as explorer
from explorer_addon import PAYLOAD, compact_metadata


class _Dataset:
    def __init__(self, attrs):
        self.attrs = attrs


class DeclaredNodataTests(unittest.TestCase):
    def test_uint8_mask_uses_255_only_as_nodata(self):
        raw = np.array([[0, 1, 255], [1, 0, 255]], dtype=np.uint8)

        clean = explorer.declared_nodata_to_nan(
            _Dataset({"nodata": 255, "nodata_value": 255}), raw
        )

        self.assertEqual(clean.dtype, np.float32)
        np.testing.assert_array_equal(clean[:, :2], raw[:, :2])
        self.assertTrue(np.isnan(clean[:, 2]).all())
        self.assertEqual(int(np.isfinite(clean).sum()), 4)

    def test_nan_nodata_needs_no_copy(self):
        raw = np.array([[0.0, np.nan], [1.0, 0.5]], dtype=np.float32)

        clean = explorer.declared_nodata_to_nan(_Dataset({"nodata": np.nan}), raw)

        self.assertIs(clean, raw)

    def test_clean_mask_quantises_to_science_range(self):
        raw = np.array([[0, 1, 255], [1, 0, 255]], dtype=np.uint8)
        clean = explorer.declared_nodata_to_nan(_Dataset({"nodata": 255}), raw)

        packed = explorer.quantise(clean)

        self.assertEqual(packed["lo"], 0.0)
        self.assertEqual(packed["hi"], 1.0)
        self.assertEqual(packed["valid"], 4)


class DisplayMetadataTests(unittest.TestCase):
    def test_full_radar_labels_distinguish_pair_line_pol_and_complex_quantity(self):
        cases = [
            ('science/UAVSAR/20200201_20200202/LOWMAN23205/HH/int', 'int', '∠phase',
             'Interferogram ∠phase  ·  2020-02-01 → 2020-02-02  ·  LOWMAN23205  ·  HH'),
            ('science/UAVSAR/20201231_20210102/LOWMAN05208/VV/amp1', 'amp1', '',
             'Amplitude, pass 1  ·  2020-12-31 → 2021-01-02  ·  LOWMAN05208  ·  VV'),
            ('science/UAVSAR/20200201_20200202/LOWMAN23205/GEOMETRY/incidence_angle_flat',
             'incidence_angle_flat', '',
             'incidence_angle_flat  ·  2020-02-01 → 2020-02-02  ·  LOWMAN23205'),
            ('science/UAVSAR/AMPLITUDE_GRD/BANNER_CUTFROMLOWMAN05208_20210303_20210310/HV/amp2',
             'amp2', '',
             'Amplitude, pass 2  ·  2021-03-03 → 2021-03-10  ·  BANNER_CUTFROMLOWMAN05208  ·  HV'),
            ('science/UAVSAR/DEM_TIFF/LOWMAN23205_20200213_20200213/elevation',
             'elevation', '', 'Elevation  ·  2020-02-13  ·  LOWMAN23205'),
            ('science/UAVSAR/DEM_TIFF/LOWMAN23205_20200213/elevation',
             'elevation', '', 'Elevation  ·  2020-02-13  ·  LOWMAN23205'),
        ]
        for path, leaf, suffix, label in cases:
            with self.subTest(path=path):
                self.assertEqual(explorer.describe(path, leaf, suffix)['label'], label)
        lidar = explorer.describe('science/LIDAR/VH/20200218/veg_height', 'veg_height')
        self.assertEqual(lidar['label'], 'Vegetation height  ·  2020-02-18')
        self.assertEqual(lidar['pol'], '')
        phase = explorer.describe(cases[0][0], 'int', '∠phase')
        magnitude = explorer.describe(cases[0][0], 'int', '|magnitude|')
        self.assertEqual(phase['short'], 'Interferogram ∠phase')
        self.assertNotEqual(phase['label'], magnitude['label'])

    def test_export_serializes_source_units_and_angular_fallbacks_without_changing_values(self):
        import h5py

        cases = {
            'science/LIDAR/DERIVED/aspect': ({'units': 'degrees clockwise from north'}, '°'),
            'science/LIDAR/DERIVED/slope': ({'units': 'degrees'}, '°'),
            'science/UAVSAR/20200201_20200202/line/GEOMETRY/incidence_angle_flat': ({}, '°'),
            'science/UAVSAR/20200201_20200202/line/GEOMETRY/local_incidence_angle':
                ({'units': np.bytes_('radians')}, 'rad'),
            'science/UAVSAR/20200201_20200202/line/HH/amp1': ({}, ''),
            'science/UAVSAR/20200201_20200202/line/HH/amp2': ({'units': 'linear amplitude'}, 'linear amplitude'),
            'science/UAVSAR/20200201_20200202/line/HH/coherence_mask':
                ({'units': '1 = usable, 0 = decorrelated, 255 = nodata'}, ''),
            'science/LIDAR/DERIVED/forest_cover_fraction_20200218': ({'units': 'fraction, 0-1'}, ''),
        }
        complex_cases = {
            'science/UAVSAR/20200201_20200202/line/HH/int':
                ({'units': 'complex components', 'magnitude_units': np.bytes_('linear power'),
                  'phase_units': 'degrees'}, 'linear power'),
            'science/UAVSAR/20200201_20200202/line/VV/int': ({'units': 'complex components'}, ''),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'metadata.enriched.h5'
            with h5py.File(source, 'w') as archive:
                archive.create_group('identification').attrs.update(
                    common_grid_shape=(2, 2), common_grid_transform=(3, 0, 0, 0, -3, 6),
                    common_grid_resolution_m=3, site_name='Metadata')
                archive.create_dataset(explorer.DEM_PATH, data=np.full((2, 2), 2000., dtype='float32'))
                for path, (attrs, _) in cases.items():
                    archive.create_dataset(path, data=np.full((2, 2), 0.5, dtype='float32')).attrs.update(attrs)
                for path, (attrs, _) in complex_cases.items():
                    archive.create_dataset(path, data=np.full((2, 2), 3 + 4j, dtype='complex64')).attrs.update(attrs)
            before = source.read_bytes()
            args = SimpleNamespace(terrain_stride=1, stride=1, viewer_dir=root / 'viewer',
                                   template=Path(explorer.__file__).with_name('explorer_template.html'))
            code, _ = explorer.build_site('metadata', source, args, ['metadata'])
            self.assertEqual(code, 0)
            payload = json.loads(PAYLOAD.search((args.viewer_dir / 'metadata_explorer.html').read_text(encoding='utf-8'))[1])
            self.assertEqual(source.read_bytes(), before)
        compact = compact_metadata(payload)
        nodes = {node['path']: node for node in payload['tree']}
        for path, (attrs, unit) in cases.items():
            with self.subTest(path=path):
                self.assertEqual(payload['arrays'][path]['unit'], unit)
                self.assertEqual(compact['layers'][path]['unit'], unit)
                self.assertEqual(payload['arrays'][path]['lo'], 0.5)
                for key, value in attrs.items():
                    self.assertEqual(nodes[path]['attrs'][key], explorer.attr_to_json(value))

        for path, (attrs, unit) in complex_cases.items():
            with self.subTest(path=path):
                magnitude = payload['arrays'][path + ' |magnitude|']
                phase = payload['arrays'][path + ' ∠phase']
                self.assertEqual(magnitude['unit'], unit)
                self.assertEqual(compact['layers'][path + ' |magnitude|']['unit'], unit)
                self.assertEqual(phase['unit'], 'rad')
                self.assertAlmostEqual(magnitude['lo'], 5.0)
                self.assertAlmostEqual(phase['lo'], 0.927295218, places=6)
                for key, value in attrs.items():
                    self.assertEqual(nodes[path]['attrs'][key], explorer.attr_to_json(value))


class AspectExportTests(unittest.TestCase):
    def test_real_export_averages_aspect_circularly_and_preserves_other_products(self):
        import h5py

        aspect = np.repeat([[359, 1, 90, 270, 20, 40, np.nan, 50],
                            [350, 10, 0, 180, 40, 60, np.nan, np.nan]], 2, axis=0).astype('float32')
        scalar = np.tile([0, 180, 0, 100, 2, 4, np.nan, 8], (4, 1)).astype('float32')
        radar = np.tile([2 + 0j, 1j], (4, 4)).astype('complex64')
        aspect_path = 'science/LIDAR/DERIVED/aspect'
        scalar_path = 'science/LIDAR/SD/20200201/snow_depth'
        radar_path = 'science/UAVSAR/20200201_20200202/line/HH/int'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'example.enriched.h5'
            with h5py.File(source, 'w') as archive:
                archive.create_group('identification').attrs.update(
                    common_grid_shape=(4, 8), common_grid_transform=(3, 0, 0, 0, -3, 12),
                    common_grid_resolution_m=3, site_name='Example')
                archive.create_dataset(explorer.DEM_PATH, data=np.full((4, 8), 2000., dtype='float32'))
                archive.create_dataset(aspect_path, data=aspect)
                archive.create_dataset(scalar_path, data=scalar)
                archive.create_dataset(radar_path, data=radar)
            before = source.read_bytes()
            args = SimpleNamespace(terrain_stride=2, stride=2, viewer_dir=root / 'viewer',
                                   template=Path(explorer.__file__).with_name('explorer_template.html'))
            code, _ = explorer.build_site('example', source, args, ['example'])
            self.assertEqual(code, 0)
            payload = json.loads(PAYLOAD.search((args.viewer_dir / 'example_explorer.html').read_text(encoding='utf-8'))[1])
            self.assertEqual(source.read_bytes(), before)

        def decode(layer):
            values = np.frombuffer(base64.b64decode(layer['b64']), dtype=np.uint8).reshape(layer['h'], layer['w'])
            return np.where(values == 0, np.nan, layer['lo'] + (values.astype(float) - 1) / 254 * (layer['hi'] - layer['lo']))

        arrays = payload['arrays']
        np.testing.assert_allclose(decode(arrays[aspect_path]),
                                   [[0, np.nan, 30, 50], [0, np.nan, 50, np.nan]], atol=0.2)
        np.testing.assert_allclose(decode(arrays[scalar_path]), [[90, 50, 3, 8]] * 2, atol=0.35)
        np.testing.assert_allclose(decode(arrays[radar_path + ' ∠phase']), 0.463647609, atol=1e-6)
        np.testing.assert_allclose(decode(arrays[radar_path + ' |magnitude|']), 1.5, atol=1e-6)
        self.assertEqual(arrays[aspect_path]['valid'], int(np.isfinite(aspect).sum()))
        aggregation = arrays[aspect_path]['aggregation']
        self.assertEqual(aggregation['method'], 'circular_mean_degrees')
        self.assertEqual(compact_metadata(payload)['layers'][aspect_path]['aggregation'], aggregation)
        self.assertNotIn('aggregation', arrays[scalar_path])
        # Stored support and finite exported blocks are different measurements.
        for key, expected in {
            aspect_path: (5, 8),  # circular cancellation leaves one block undefined
            scalar_path: (8, 8),  # averaging ignores the missing native samples
            radar_path + ' ∠phase': (8, 8),
            radar_path + ' |magnitude|': (8, 8),
        }.items():
            with self.subTest(key=key):
                self.assertEqual(arrays[key].get('display_valid'), expected[0])
                self.assertEqual(arrays[key].get('display_total'), expected[1])
        self.assertEqual(arrays[scalar_path]['valid'], 28)
        self.assertEqual(arrays[scalar_path]['total'], 32)



if __name__ == "__main__":
    unittest.main()
