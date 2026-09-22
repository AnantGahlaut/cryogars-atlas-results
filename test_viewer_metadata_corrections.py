"""Metadata-only SNEX-004 export corrections, using tiny synthetic archives."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np

import make_explorer as E
from explorer_addon import PAYLOAD, compact_metadata
from viewer_metadata_corrections import (
    apply_viewer_metadata_corrections, SOURCE_FILENAME, DEM_PATH,
    OLD_SITE, NEW_SITE, OLD_SOURCE, NEW_SOURCE, HISTORY,
)


def fixture():
    ident = {"reference_filename": SOURCE_FILENAME, "site_note": OLD_SITE}
    return {"site": "grand_mesa", "identification": ident,
            "tree": [{"path": "identification", "attrs": dict(ident)},
                     {"path": DEM_PATH, "attrs": {"source_note": OLD_SOURCE}}],
            "arrays": {"sentinel": {"b64": "unchanged packed bytes"}},
            "terrain": {"b64": "unchanged terrain"},
            "build_provenance": {"artifact_id": "original numeric export"}}


class MetadataCorrectionTests(unittest.TestCase):
    def test_exact_approved_notes_history_and_numeric_payload_are_preserved(self):
        payload = fixture()
        before = copy.deepcopy(payload)
        corrections = apply_viewer_metadata_corrections(payload)
        self.assertEqual(len(corrections), 3)
        for attrs, field, old, new in [
                (payload["identification"], "site_note", OLD_SITE, NEW_SITE),
                (payload["tree"][0]["attrs"], "site_note", OLD_SITE, NEW_SITE),
                (payload["tree"][1]["attrs"], "source_note", OLD_SOURCE, NEW_SOURCE)]:
            self.assertEqual(attrs[field], new)
            self.assertEqual(attrs[field + "_previous"], old)
            self.assertEqual(attrs[field + "_correction"], HISTORY)
        for key in ("arrays", "terrain", "build_provenance"):
            self.assertEqual(payload[key], before[key])
        self.assertTrue(all(c["issue"] == "SNEX-004" and c["provider_section"] == "2.3"
                            for c in corrections))
        once = copy.deepcopy(payload)
        self.assertEqual(apply_viewer_metadata_corrections(payload), [])
        self.assertEqual(payload, once)

    def test_other_sites_and_reference_files_are_not_relabelled(self):
        for field, value in (("site", "banner_summit"), ("reference_filename", "other.tif")):
            payload = fixture()
            (payload if field == "site" else payload["identification"])[field] = value
            before = copy.deepcopy(payload)
            self.assertEqual(apply_viewer_metadata_corrections(payload), [])
            self.assertEqual(payload, before)

    def test_unknown_wording_missing_targets_or_conflicting_history_are_atomic_errors(self):
        for change in ("wording", "missing", "duplicate", "history"):
            with self.subTest(change=change):
                payload = fixture()
                if change == "wording":
                    payload["tree"][1]["attrs"]["source_note"] = "unreviewed source"
                elif change == "missing":
                    payload["tree"].pop()
                elif change == "duplicate":
                    payload["tree"].append(copy.deepcopy(payload["tree"][1]))
                else:
                    payload["tree"][1]["attrs"]["source_note_previous"] = "different history"
                before = copy.deepcopy(payload)
                with self.assertRaises(ValueError):
                    apply_viewer_metadata_corrections(payload)
                self.assertEqual(payload, before)

    def test_new_archive_correct_text_does_not_acquire_invented_previous_notes(self):
        payload = fixture()
        payload["identification"]["site_note"] = NEW_SITE
        payload["tree"][0]["attrs"]["site_note"] = NEW_SITE
        payload["tree"][1]["attrs"]["source_note"] = NEW_SOURCE
        before = copy.deepcopy(payload)
        self.assertEqual(apply_viewer_metadata_corrections(payload), [])
        self.assertEqual(payload, before)

    def test_fresh_export_applies_correction_and_hashes_helper_without_archive_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "grand_mesa.enriched.h5"
            with h5py.File(source, "w") as archive:
                archive.create_group("identification").attrs.update(
                    common_grid_shape=(2, 2), common_grid_transform=(3, 0, 0, 0, -3, 6),
                    common_grid_resolution_m=3, site_name="Grand Mesa",
                    reference_filename=SOURCE_FILENAME, site_note=OLD_SITE)
                archive.create_dataset(DEM_PATH, data=np.full((2, 2), 2000., "float32")).attrs["source_note"] = OLD_SOURCE
            before = source.read_bytes()
            args = SimpleNamespace(terrain_stride=1, stride=1, viewer_dir=root / "viewer",
                                   template=Path(E.__file__).with_name("explorer_template.html"))
            code, _ = E.build_site("grand_mesa", source, args, ["grand_mesa"])
            self.assertEqual(code, 0)
            payload = json.loads(PAYLOAD.search((args.viewer_dir / "grand_mesa_explorer.html").read_text(encoding="utf-8"))[1])
            self.assertEqual(source.read_bytes(), before)
        self.assertEqual(payload["identification"]["site_note"], NEW_SITE)
        self.assertEqual(compact_metadata(payload)["layers"][DEM_PATH]["attrs"]["source_note"], NEW_SOURCE)
        self.assertEqual(payload["arrays"][DEM_PATH]["lo"], 2000.)
        record = payload["build_provenance"]
        self.assertEqual(len(record["parameters"]["metadata_corrections"]), 3)
        helper = Path(E.__file__).with_name("viewer_metadata_corrections.py").resolve()
        self.assertEqual(record["sources"][str(helper)]["sha256"], hashlib.sha256(helper.read_bytes()).hexdigest())
        self.assertEqual(record["source_status"], "unchanged_since_capture")


if __name__ == "__main__":
    unittest.main()
