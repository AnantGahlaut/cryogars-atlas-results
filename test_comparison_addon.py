"""Comparison composition contracts: exact base/payload preservation, no archive IO."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from comparison_addon import append_comparison, remove_comparison, rollout, MARKER, BRIDGE_START


def fixture():
    raw=json.dumps({'site':'example','arrays':{}},indent=2)
    return '<script id="payload" type="application/json">'+raw+'</script>\r\n<script>\r\n(function(){\r\n(function spin(){draw();requestAnimationFrame(spin);})();\r\n})();\r\n</script>\r\n<!-- original notes -->'


class ComparisonTests(unittest.TestCase):
    def test_exact_original_restored_by_removing_only_additions(self):
        source=fixture();after=append_comparison(source)
        self.assertEqual(remove_comparison(after),source)
        self.assertEqual(after.count(MARKER),1)
        self.assertEqual(after.count(BRIDGE_START),1)
        self.assertIn('id="nxc-panel"',after)
        self.assertNotIn('<script src=',after)

    def test_repeat_is_idempotent(self):
        first=append_comparison(fixture())
        self.assertEqual(append_comparison(first),first)

    def test_on_demand_viewer_preserves_boot_and_round_trips(self):
        source=fixture().replace('(function spin(){draw();requestAnimationFrame(spin);})();',
                                 'draw(); // Start on-demand rendering.')
        after=append_comparison(source)
        self.assertEqual(remove_comparison(after),source)
        self.assertEqual(append_comparison(after),after)

    def test_mixed_legacy_and_on_demand_hooks_refuses_to_guess(self):
        with self.assertRaises(ValueError):
            append_comparison(fixture()+'\ndraw(); // Start on-demand rendering.')

    def test_actual_scripts_parse_in_composed_page(self):
        from comparison_addon import validate
        with tempfile.TemporaryDirectory() as directory:
            candidate=Path(directory)/'page.html'
            candidate.write_text(append_comparison(fixture()),encoding='utf-8')
            validate(candidate)

    def test_missing_or_duplicate_hook_refuses_to_guess(self):
        with self.assertRaises(ValueError):append_comparison('<script>unrelated</script>')
        with self.assertRaises(ValueError):append_comparison(fixture()+fixture())

    def test_validator_failure_leaves_original_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            viewer=Path(directory)/'viewer';viewer.mkdir()
            p=viewer/'example_explorer.html';p.write_bytes(fixture().encode())
            with patch('comparison_addon.validate',side_effect=ValueError('invalid candidate')):
                with self.assertRaises(ValueError):rollout(viewer,expected_count=1)
            self.assertEqual(p.read_bytes(),fixture().encode())

    def test_rollout_backups_and_exact_original_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            viewer=Path(directory)/'viewer';viewer.mkdir()
            p=viewer/'example_explorer.html';p.write_bytes(fixture().encode())
            index=viewer/'index.html';index.write_bytes(b'keep index')
            with patch('comparison_addon.validate'):
                backup=rollout(viewer,expected_count=1)
            self.assertEqual((backup/p.name).read_bytes(),fixture().encode())
            self.assertEqual(remove_comparison(p.read_bytes().decode()),fixture())
            self.assertEqual(index.read_bytes(),b'keep index')


if __name__=='__main__':unittest.main()
