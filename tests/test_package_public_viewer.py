import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "scripts" / "package_public_viewer.py"
spec = importlib.util.spec_from_file_location("package_public_viewer", MODULE)
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class PublicViewerTests(unittest.TestCase):
    def test_redaction_preserves_source_hashes_and_pre_redaction_identity(self):
        record = {"schema": "snowex-build-lineage-v1", "recipe_sha256": "recipe-id",
                  "sources": {r"C:\Users\person\project\export.py": {"sha256": "source-id"}},
                  "dependencies": {"executable": "/home/person/venv/bin/python"},
                  "inputs": [{"path": r"D:\input\site.h5"}]}
        public = pack.redact_lineage(record, r"C:\Users\person\project")
        self.assertEqual(public["sources"], {"repository/export.py": {"sha256": "source-id"}})
        self.assertEqual(public["dependencies"]["executable"], "[local]/python")
        self.assertEqual(public["inputs"][0]["path"], "[local]/site.h5")
        self.assertNotIn("recipe_sha256", public)
        self.assertEqual(public["pre_redaction_recipe_sha256"], "recipe-id")
        self.assertEqual(public["pre_redaction_record_sha256"], pack.digest(pack.canonical(record)))
        self.assertEqual(public["recipe_status"], "redacted_recipe_not_checksum_verifiable")
        self.assertEqual(pack.redact_lineage(public, "elsewhere"), public)

    def test_escaped_windows_paths_and_entire_scientific_payload_unchanged(self):
        science = {"terrain": {"data": "AAAA/w=="}, "layers": [{"data": "AC+F/fff", "min": 0}],
                   "tree": [{"path": "/science/LIDAR", "attrs": {"url": "https://provider/data"}}]}
        original = {**science, "build_provenance": {"schema": "snowex-build-lineage-v1",
                    "recipe_sha256": "original", "dependencies": {"executable": r"C:\Users\person\python.exe"}}}
        raw = ('<script id="payload" type="application/json">' + json.dumps(original)
               + '</script><script>window.example = "unchanged";</script>').encode()
        public, blocks = pack.public_page(raw, "project")
        payload = json.loads(pack.SCRIPT.search(public.decode())[2])
        self.assertEqual({k: v for k, v in payload.items() if k != "build_provenance"}, science)
        self.assertEqual(blocks, ["payload"])
        self.assertNotIn(b"Users", public)
        self.assertIn(b'<script>window.example = "unchanged";</script>', public)

    def test_nested_parent_redaction_invalidates_ancestor_recipe(self):
        record = {"schema": "snowex-build-lineage-v1", "recipe_sha256": "outer",
                  "parent": {"schema": "snowex-build-lineage-v1", "recipe_sha256": "inner",
                             "path": "/home/person/data.tif"}}
        result = pack.redact_lineage(record, "project")
        self.assertEqual(result["pre_redaction_recipe_sha256"], "outer")
        self.assertEqual(result["parent"]["pre_redaction_recipe_sha256"], "inner")

    def test_unexpected_home_path_outside_lineage_stops_publication(self):
        raw = b'<script id="payload" type="application/json">{"notes":"/home/person/private/data.tif"}</script>'
        with self.assertRaisesRegex(ValueError, "Unhandled local path"):
            pack.public_page(raw, "project")

    def test_path_collisions_are_not_silently_merged(self):
        with self.assertRaisesRegex(ValueError, "collision"):
            pack.redact_lineage({r"D:\a\same.py": 1, r"E:\b\same.py": 2}, "project")

    def test_links_must_be_bundled_or_explicit_external(self):
        self.assertEqual(pack.check_links(b'<a href="index.html">Home</a><img src="data:x">'), [])
        with self.assertRaisesRegex(ValueError, "Unbundled"):
            pack.check_links(b'<script src="missing.js"></script>')

    def test_full_allowlist_and_sources_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "viewer"
            source.mkdir()
            raw = b'<a href="index.html">Home</a><script>const x=1;</script>'
            for name in pack.PAGES:
                (source / name).write_bytes(raw)
            result = pack.package(source, root / "bundle", root)
            self.assertEqual(len(result["pages"]), 9)
            self.assertEqual(sorted(p.name for p in (root / "bundle").iterdir()), sorted(pack.PAGES))
            self.assertTrue(all((source / name).read_bytes() == raw for name in pack.PAGES))
            with self.assertRaisesRegex(ValueError, "empty"):
                pack.package(source, root / "bundle", root)


if __name__ == "__main__":
    unittest.main()
