# Repository organization

Reviewed 2026-09-22. This is a proposed layout, not a completed file migration.
The current pipeline and viewer paths remain in place.

## What needs organizing

Before this documentation and publication update, the repository root contained
74 files: 22 Python tests, 14 log/error files, three `.bak` snapshots, and the
application code, templates, launchers and documentation. Git tracked 87 files
in total, including 38 at the root. Much of the visible clutter is local working
material already excluded from Git; it is not all published source.

Documentation navigation is being added in `docs/README.md`. New publication
work uses `scripts/package_public_viewer.py` and tests under `tests/`, establishing
the intended separation without moving the existing pipeline.

## Proposed layout

```text
README.md                  project introduction and primary links
CHANGELOG.md               release history
environment.yml            Python environment
pipeline/                  acquisition, enrichment, audit and export modules
web/                       viewer templates, notes and comparison application
tests/                     Python and JavaScript checks and small fixtures
scripts/                   publication and other operational entry points
docs/                      design, science, workflows and review records
assets/                    logos, screenshots and vendored browser libraries

out/                       local HDF5 archives — ignored
viewer/                    generated local explorers — ignored
work/, work2/, ann_cache/   local processing scratch/cache — ignored
tmp/                       temporary candidates, checks and logs — ignored
backups/                   local rollback copies — ignored
ASO_*/                     incoming local source packages — ignored
```

`output/` currently holds review/PDF artifacts and is **not** covered by the
directory rules that ignore `out/`. Classify those artifacts before choosing
whether to retain selected reports in `docs/` or exclude local generated output.
Do not stage the whole directory by default.

## Order of work

1. **Improve navigation and separate inactive local clutter.** Keep the root
   README focused on starting points. Move closed logs into `tmp/logs/` and old
   snapshots into `backups/` only after checking that no running task uses them.
   `run_build_task.bat` still appends to root `build_task.log`; moving a live log
   would not update that launcher. No archive or incoming data moves are needed.
2. **Move existing tests as a small, separate change.** Update test discovery,
   sibling test imports and resource paths together. In particular,
   `test_explorer_addon.py` finds `assets/` beside itself,
   `test_scientific_methods.py` reads `enrich_hdf5.py` beside itself, and
   `test_refresh_explorers.py` imports a fixture from `test_make_index.py`.
3. **Move pipeline and web source together with their path updates.** Establish
   explicit package imports and one consistent repository/resource location.
   Update launchers, documented commands and the affected checks in the same
   change. Keep the public website publication separate from this migration.

Several production paths depend on the current layout. `make_index.py` locates
its template, logo and default viewer directory beside the script.
`refresh_explorers.py`, `explorer_addon.py` and `comparison_addon.py` resolve
templates, notes, comparison files and validation scripts relative to the root.
The Windows launchers run from their own directory. Moving these files without
updating those assumptions breaks builds even when the scientific code is
unchanged.

The name `ui_preview/` is also misleading: it contains both development checks
and the **production** `logo_notes_addon.html` fragment. Move that fragment into
`web/`, checks into `tests/`, and generated previews into an ignored location
when doing the coordinated migration. Preserve comparison-module relative
imports when splitting the existing `viewer_compare/` folder.

## Checks for a layout change

- Run Python discovery and the affected JavaScript checks from the repository
  root, using the newly documented commands.
- Render a tiny synthetic archive and exercise the viewer/index generators from
  a different working directory to catch accidental current-directory reliance.
- Verify that generated scientific payloads and executable viewer code remain
  unchanged for a paths-only move; source paths/hashes in provenance may change
  and must accurately describe the new layout.
- Check launcher paths and documentation links. A folder cleanup does not
  require regenerating the real HDF5 archives or making another large backup.

Keep new source and tests in the reviewed change set. Recently added files such
as `viewer_metadata_corrections.py` are required at runtime even if they have not
yet been added to Git; a source bundle based only on the current tracked-file
list can omit necessary code.
