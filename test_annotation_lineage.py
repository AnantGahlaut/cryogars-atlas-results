"""Consumed cached annotation bytes have an explicit digest, without network I/O."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_hdf5 as B
import enrich_hdf5 as E


class AnnotationLineageTests(unittest.TestCase):
    def test_digest_and_scientific_fields(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            raw = cache / 'fixture.zip.ann'
            raw.write_text('Radar Look Direction (&) = Left\nPeg Heading (deg) = 52.0\n')
            ann = E.cached_annotation(None, 'https://fixture.invalid/fixture.zip', cache)
            self.assertEqual(ann['_snowex_annotation_source']['sha256'], hashlib.sha256(raw.read_bytes()).hexdigest())
            self.assertEqual(E.annotation_scalars(ann), {'radar_look_direction': 'Left', 'peg_heading_deg': 52.})

    def test_offline_cache_miss_raises_without_fetching(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(E, 'fetch_annotation') as fetch:
                with self.assertRaisesRegex(RuntimeError, 'offline'):
                    E.cached_annotation(None, 'https://fixture.invalid/missing.zip', Path(td))
            fetch.assert_not_called()

    def test_cache_change_during_parse_is_not_given_a_false_digest(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            raw = cache / 'fixture.zip.ann'
            raw.write_text('Radar Look Direction (&) = Left\n')
            def change(path):
                path.write_text('Radar Look Direction (&) = Right\n')
                return {}
            with patch.object(B, 'read_annotation', side_effect=change):
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    E.cached_annotation(None, 'https://fixture.invalid/fixture.zip', cache)


if __name__ == '__main__':
    unittest.main()
