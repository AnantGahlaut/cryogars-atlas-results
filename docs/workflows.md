# Workflow guide

Run commands from the project directory. Paths below are placeholders: choose
archive and scratch directories with sufficient space, outside cloud-synced
source folders. Keep explicit `--out-dir` arguments so a command does not read
or write an unintended local `out` directory.

## Environment

| Task | Requirements |
| --- | --- |
| Browse a prepared export | Modern browser; WebGL for the 3D explorer |
| Rebuild only the entrance | Python standard library |
| Generate explorers from HDF5 | Python, NumPy, h5py |
| Build and enrich archives | Scientific/geospatial environment and source-data access |
| Refresh explorer interfaces and run JavaScript checks | Node.js; version 22.21.0 checked locally |

A candidate scientific environment is provided in
[`environment.yml`](../environment.yml):

```bash
conda env create -f environment.yml
conda activate snowex-atlas
```

The versions listed in that file match the installed scientific packages used
for this documentation pass. **A fresh installation from the file has not yet
been tested.** The successful offline run used an existing Windows Conda
environment with Python 3.11.15. Activate the environment before using Rasterio
so GDAL's runtime data paths are configured.

The full builder uses NumPy, h5py, Rasterio, SciPy, Shapely, pyproj, earthaccess,
asf_search, requests, and affine. NumPy and h5py suffice for explorer extraction.
Node.js is installed separately. Tests use Python's standard-library `unittest`;
pytest is not required.

## Browse or regenerate exports

Open `viewer/index.html` when you already have a generated bundle. For an
existing archive, build one site first:

```bash
python make_explorer.py --site mores_creek --out-dir "path/to/archive" --viewer-dir viewer
```

To generate all available sites and an entrance:

```bash
python make_explorer.py --all --out-dir "path/to/archive" --viewer-dir viewer
```

`--all` currently discovers site names only from base `<site>.h5` filenames.
If the directory contains enriched files only, use `--site <key>` for each site
and then run `python make_index.py --viewer-dir viewer`. Enriched files are
preferred; `--prefer-plain` selects base archives. Explicit
`--stride` and `--terrain-stride` options change display sampling. These commands
read full scientific arrays and can be costly, especially at Grand Mesa. Back
up existing generated pages before replacing them with a full extraction run.

## Acquire and build data

Discovery contacts NASA CMR and ASF. Downloads require a NASA Earthdata account
and any applicable provider authorization. Keep authentication in the user
account's credential configuration, outside the repository; never put actual
credentials in commands, notebooks, or release files.

1. Discover and write a site inventory:

   ```bash
   python build_hdf5.py --mode preflight --sites mores_creek --inventory "path/to/inventory.json"
   ```

2. Check configured authentication:

   ```bash
   python build_hdf5.py --mode check-auth
   ```

3. Download, align, and write the base archive:

   ```bash
   python build_hdf5.py --mode build --sites mores_creek --inventory "path/to/inventory.json" --out-dir "path/to/archive" --work-dir "path/to/scratch"
   ```

`--mode build` is essential: the script defaults to `preflight`. Normal build
reruns preserve completed arrays unless `--overwrite` is specified. Source
catalogues can change; preserve the actual inventory used for a reproducible
run. Do not infer a fixed download volume from historical estimates.

## Enrich safely

Enrichment reads `<site>.h5` and writes `<site>.enriched.h5`. The current code
removes an existing enriched destination before rebuilding it; it does not
perform an atomic replacement. Preserve a verified copy of finished outputs,
or use a separate candidate suffix:

```bash
python enrich_hdf5.py --site mores_creek --out-dir "path/to/archive" --suffix .candidate
```

After reviewing that candidate and retaining a backup, the standard command is:

```bash
python enrich_hdf5.py --site mores_creek --out-dir "path/to/archive"
```

Use `--all` in place of `--site mores_creek` to process all base site files.
Enrichment normally tries to fetch radar annotations through ASF. `--no-network`
skips annotation-derived radar geometry entirely, even when annotations are
cached, because the current geometry branch requires a session. In-situ observations are omitted by
default; `--with-insitu` opts in. Explorer extraction has a separate flag of the
same name.

Candidate files are not automatically selected by the explorer or the
`verify_enriched.py` command. The standard verified/viewed filename remains
`<site>.enriched.h5`. Do not rename a candidate over a finished file casually.

## Verify an archive and its transfer

Wait until all writers are finished before opening their archives:

```bash
python build_hdf5.py --mode verify --sites mores_creek --out-dir "path/to/archive"
python verify_enriched.py --site mores_creek --out-dir "path/to/archive"
python manifest.py --out-dir "path/to/archive"
python manifest.py --out-dir "path/to/archive" --verify
```

The first command checks base archive invariants. The independent enrichment
checker reads values and currently expects enrichment version 3.x. Manifest
creation writes `MANIFEST.json` and `MANIFEST.sha256`; verification checks the
existing manifest. Full scans and hashes can take considerable time.

Default manifests include per-file SHA-256 and per-dataset summary statistics.
Summary statistics alone cannot localize every possible corruption. The current
verifier fails missing expected files, extra unlisted files, name collisions and
incomplete records, then checks file sizes and hashes. On systems with
`sha256sum`, `sha256sum -c MANIFEST.sha256` also checks every listed file after
transfer, but does not detect extra files.

Optional `--deep` uses the versioned dataset fingerprint format: framed type/shape
metadata and ordered values, with explicit string encoding/lengths. Unsupported
compound/reference types fail rather than hashing object pointers. Keep the
matching fingerprint version when comparing manifests. File hashes establish
byte identity; neither file nor dataset hashes establish scientific accuracy.

## Maintain the viewer without rereading HDF5

The eight local explorers were rebuilt and installed on 2026-09-21 from unchanged
archives; see the [rebuild record](reviews/2026-09-21-explorer-rebuild.md). Archive
repair remains deferred. Local maintenance below is separate from publishing the
public website, whose deployed bundle must be verified after upload.

### Entrance only

Edit `index_template.html`, preserve a copy of the existing entrance, then run:

```bash
python make_index.py --viewer-dir viewer
```

This reads existing `*_explorer.html` payloads and writes only `viewer/index.html`.

### Isolated logo and notes

With all eight site pages and the entrance present in `viewer/`, edit
`ui_preview/logo_notes_addon.html`, then generate the Banner Summit preview:

```bash
python ui_preview/build_logo_notes_preview.py
```

Review `ui_preview/banner_summit_logo_notes_preview.html` in a browser before
rollout. This generated page is a local artifact excluded from the source
repository; the rollout code and checker require it as a review baseline.
The preview builder preserves the existing renderer and comparison code.

After review, when rollout is authorized:

```bash
python explorer_addon.py --rollout
```

This command backs up existing pages and replaces the isolated logo and notes
add-on while checking that original application code, styles, comparison code,
and embedded data remain intact. Renderer and comparison-mask control changes
require the shared interface refresh below. Full builds and interface refreshes
include the same notes add-on automatically.

### Shared explorer interface

To install changes to `explorer_template.html` or the comparison and mask controls
in `viewer_compare/`, use the shared interface refresh after review and rollout
authorization:

```bash
python refresh_explorers.py --viewer-dir viewer
```

This backs up existing pages, checks replacement JavaScript with Node.js,
reuses each page's exact embedded payload, and regenerates the entrance.
Changing scientific data or sampling still requires extraction from HDF5.

`product_guide.js` contains additional scientific notes under review. Passing
its standalone checks does not mean those notes are the active side panel.

## Prepare a public viewer bundle

The public website is distributed separately in
[`AnantGahlaut/cryogars-atlas-viewer`](https://github.com/AnantGahlaut/cryogars-atlas-viewer).
After validating local exports, prepare an empty destination:

```bash
python scripts/package_public_viewer.py viewer "path/to/empty-public-bundle" --report "path/to/package-report.json"
python -m unittest discover -s tests -p "test_package_public_viewer.py"
```

This packages only the index and eight site pages. It removes machine-specific
paths from lineage records, marks redacted recipe hashes as pre-redaction
identities, and checks that scientific data and executable HTML stay unchanged.
Seven packaging tests passed on September 22. Keep the full original lineage
locally; the public copy is explicitly redacted.

Update only those nine pages and the distribution README in a clean checkout of
the viewer repository, retaining its `.nojekyll`. Verify the exact committed page
hashes, push normally, then verify HTTP responses and page hashes at the public
URL after Pages deploys. Do not copy the whole working directory into the public
distribution. Publishing prepared display pages does not require an HDF5 rebuild.

## Checks

Run the source suites explicitly from the repository root so discovery does not
enter backup directories:

```bash
python -m unittest test_annotation_lineage test_audit_archive test_build_hdf5 test_build_provenance test_cleaning_metadata test_comparison_addon test_derivative_inputs test_derived_grid_metadata test_explorer_addon test_four_product_metadata test_incidence_look_side test_incidence_summary test_make_explorer test_make_index test_manifest test_mask_incidence_notes_metadata test_product_units test_projected_geometry test_refresh_explorers test_scientific_methods test_vertical_reference_metadata test_viewer_metadata_corrections
```

The September 21 rebuild verification passed **390 tests** in the existing
scientific environment. These use small offline fixtures, not full research-array
validation. The earlier September 8 preparation passed 250 tests; that is
historical evidence rather than the current suite size.

With generated pages present, selected checks are:

```bash
node ui_preview/check_index.js
node ui_preview/check_dem_notes.js
node ui_preview/check_workspace.js
node ui_preview/check_cell_averaging.js
node ui_preview/check_cell_controls.js
node ui_preview/check_preview.js viewer/index.html
```

`check_workspace.js` has been repaired and checks current Info/validity behavior.
The index/page checks require generated artifacts. `check_product_guides.js` tests
the separate guide implementation; it does not prove that content is installed
in the active notes panel. The rebuild separately checked all 1,664 active notes.

`check_grid_placement.js` requires the local evidence file
`docs/product_trace/evidence/viewer_metadata.json`, which is excluded from source
distribution. Preserve that matching snapshot for reproducing its eight-site
checks. Preview/rollout checks using local backup baselines likewise are not
portable source-only checks.

The [rebuild record](reviews/2026-09-21-explorer-rebuild.md) separates array, notes,
page and existing numerical-renderer evidence. That run did not perform a new
visual browser review; cross-browser, keyboard, small-screen and hosted-delivery
acceptance retain their own scope.

## Files required in a source release

Keep the archive builder/enricher, verifiers, explorer generators/templates,
`product_guide.js`, source tests, and public documentation together. The
following paths are runtime dependencies despite their preview-directory name:

- `assets/cryogars-logo.jpg`
- `ui_preview/logo_notes_addon.html`
- `ui_preview/build_logo_notes_preview.py`
- `ui_preview/check_preview.js`
- `ui_preview/check_logo_notes_rollout.js`

Generated viewers, data archives, local launch scripts, internal handoff notes,
logs, caches, backups, and credentials should be selected or excluded
deliberately. See [the release checklist](release-checklist.md).
