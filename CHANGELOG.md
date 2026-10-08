# Release notes

## v1.0.05 — 2026-10-08

Corrected scientific archive, matching explorers, and a reproducible download.

- **Enrichment 3.3.0 for all eight sites,** regenerated on Borah from the
  unchanged, checksum-verified base archives and swapped in by checksum-checked
  rename. Aspect now points downhill (it was reflected north–south), terrain and
  canopy derivatives use the cleaned inputs, radar geometry keeps only the
  viewed side and uses the projected heading, and every derived dataset carries
  grid and lineage metadata. Grand Mesa incidence coverage falls 19% where tracks
  cross the site. Snow depth, vegetation height, elevation and radar channels are
  unchanged. See the [rebuild record](docs/reviews/2026-10-05-archive-rebuild-3.3.0.md).
- **Explorers re-exported** from the corrected archives: 1,664 layers, 856
  native-block samples and all page checks passing; viewer code unchanged.
- **`get_dataset.py`** rebuilds the archive from NASA into any folder, from a
  frozen inventory (`snowex_inventory.json`) rebuilt from the archive's own
  records after ASF's 2026 UAVSAR reorganisation. Verified end to end on
  Cameron Pass.
- **Builder:** GDAL reads of protected NSIDC files use netrc and a cookie jar;
  GDAL 3.12 rejects the previous bearer-header method after NSIDC's redirect.
- **Enrichment:** offline runs use cached radar annotations; empty unwrapped
  phase caused by the provider's zero fill is recorded and audited.
- **Audit:** verifies that zero-fill explanation; reports Grand Mesa's measured
  SWE/density rasters as a known omission rather than a silent gap.
- **Environment:** `pandas` added after a clean install on Borah. MIT `LICENSE`
  and `CITATION.cff` added. Slurm jobs for the rebuild are in `scripts/borah/`.

## 2026-09-22 — color editor responsiveness

- Color and contrast edits update palette uniforms and legends without reuploading
  unchanged layer data, rebuilding the info card or replacing Cells textures.
- Redraw requests share one browser frame; unchanged/hidden scenes stop drawing.
  The FPS indicator reports idle separately from active rendering.
- Layer selection and coherence-mask cutoff changes retain full data updates.


## 2026-09-22 — public viewer update and documentation review

- Published the validated eight-site display rebuild to the existing GitHub Pages
  site. All nine public HTML files returned HTTP 200 and matched prepared hashes.
- Added a tested publication packager that removes local paths from lineage while
  preserving scientific arrays and executable HTML.
- Added a documentation index, current design review draft and proposed repository
  folder layout; corrected stale workflow and release-status descriptions.

See the [publication record](docs/reviews/2026-09-22-public-viewer-update.md).

## 2026-09-21 — validated local display rebuild

- Re-exported and installed all eight explorers and the index, covering 1,664
  layers, from existing archives opened read-only.
- Included Detail-based Cells averaging, current product notes, stored/shown
  validity, mask controls, circular aspect export and label/date/unit fixes.
- Fixed the SNEX-026 derived metadata/audit contract for new writes; historical
  archive attributes remain unchanged.
- Made the reviewed Grand Mesa LiDAR reference-DTM description survive future
  exports, preserving original wording and correction provenance.
- Passed 390 offline Python tests, all 1,664 layer-note renderings, 856 independent
  native-block comparisons and 17 page/index checks. Export through installation
  took approximately 51 minutes.

Archive repairs and the corrected scientific release remain deferred pending
verified backup space. See the [rebuild record](docs/reviews/2026-09-21-explorer-rebuild.md).

## v1.0.05 — in preparation

SnowEx Field Atlas: aligned LiDAR/UAVSAR archives and interactive site explorers
for snow research across eight western U.S. field sites.

**Author:** Anant Gahlaut, CryoGARS, Boise State University.

### Release scope

- Per-site HDF5 construction with spatial alignment and LiDAR–radar matching.
- Enrichment with terrain derivatives, vegetation-based canopy fractions,
  coherence masks, and available approximate radar geometry.
- Eight browser explorers and a shared Field Atlas entrance.
- Archive verification, transfer manifests, and offline regression fixtures.
- Public-facing overview, workflow guide, scientific limitations, and source
  attribution links.

The current explorer snapshot contains 1,664 display layers, 15 site-specific
snow-depth dates, and 67 site/date-pair/flight-line radar groups. These counts
describe the existing exports, not independent observations or a new HDF5 audit.
In-situ points, trained retrieval models, radar-derived SWE, and NISAR products
are outside the current explorer release.

### Version convention

`v1.0.05` is the project release label. The final `05` denotes the fifth
enrichment cycle. This is a project convention; do not silently normalize the
release label or imply that it is the stored algorithm version.

Existing explorer payloads record `product_version = 0.1.0` and
`enrichment_version = 3.0.0`. The corrected producer writes enrichment `3.3.0`,
including cleaned-input derivatives and the later viewing-geometry corrections.
New derived metadata also records `derived_metadata_version=1.0`. Historical
archive repairs remain pending verified backups. Fresh display exports record
source hashes and runtime lineage, while unknown historical archive lineage
remains explicitly unknown. Documentation does not rewrite archive provenance.

### Validation during preparation — 2026-09-08

- All 250 offline Python tests passed in the existing scientific environment.
- Entrance interactions, DEM notes, and 1,664 standalone product-guide renders
  passed the selected Node.js checks.
- All 17 JavaScript blocks compiled across the eight explorers and entrance.

These are synthetic and script-level checks. Full archive hashes were not
recomputed, the data pipeline was not rerun, and browser appearance, WebGL
behaviour, accessibility, and hosted delivery were not assessed in this pass.
At that time, `check_workspace.js` was incompatible with the template. Its
fixtures were subsequently repaired and checked during the September 21 rebuild.

### Limitations and publication status

Known issues include absent snow-depth labels at Reynolds Creek, later canopy
inputs for Grand Mesa's 2017 observations, the current aspect convention,
approximate radar geometry, and incomplete historical processing provenance.
See [scientific notes](docs/scientific-notes.md).

The development repository is public at
[AnantGahlaut/cryogars-atlas](https://github.com/AnantGahlaut/cryogars-atlas).
A public [research viewer](https://anantgahlaut.github.io/cryogars-atlas-viewer/)
is available; the final scientific archive release remains pending. Licensing, distributable data
selection, clean-environment installation, and final release checks are tracked
in [the checklist](docs/release-checklist.md).
