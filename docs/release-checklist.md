# v1.0.05 release checklist

**Status reviewed: 2026-09-22.** GitHub confirms that the source repository is
public; a separate public research viewer also exists. All eight local explorers were rebuilt
and validated on September 21. That bundle was published on September 22; all
nine public pages matched their prepared SHA-256 hashes. See the
[publication record](reviews/2026-09-22-public-viewer-update.md).
Archive repairs, archive distribution and the final scientific release remain
pending. Viewer publication does not change the source-repository visibility.

Release label: **v1.0.05**, with `05` denoting the fifth enrichment cycle.
Author: **Anant Gahlaut**. Destination organization:
[CryoGARS](https://github.com/cryogars), as a later transfer target. The current
public source repository is
[AnantGahlaut/cryogars-atlas](https://github.com/AnantGahlaut/cryogars-atlas).
Organization access and transfer remain separate steps.

## Historical documentation preparation — 2026-09-08

- [x] Rewrite the README around purpose, capabilities, site coverage, and entry points.
- [x] Read all eight current viewer payloads for site counts and recorded versions.
- [x] Separate the release label from product 0.1.0 and enrichment 3.0.0 provenance.
- [x] Add workflow, scientific-method, data-source, and release-note documents.
- [x] Credit Anant Gahlaut without personal development context.
- [x] Add local/data/credential exclusions and a candidate environment specification.
- [x] Run all 250 offline Python tests in an existing scientific environment.
- [x] Check entrance interactions, DEM notes, and standalone product-guide rendering.
- [x] Compile the 17 JavaScript blocks in the eight explorers and entrance.
- [x] Preserve and hash-check backups of the previous README and checklist.

## Current local explorer completion — 2026-09-21

- [x] Re-export and install all eight sites and the index from read-only archives.
- [x] Include current notes, Cells averaging, validity guidance and labels/dates/units.
- [x] Run 390 Python tests, all 1,664 layer/note checks and 856 native-block samples.
- [x] Complete SNEX-026 source/audit metadata fixes without exempting old archives.
- [x] Preserve the Grand Mesa source-description correction with separate provenance.
- [x] Publish the current viewer bundle and verify all nine hosted page hashes.

See the [rebuild record](reviews/2026-09-21-explorer-rebuild.md) for evidence and
remaining scope. The archive files themselves were not repaired.

## Decide before final source/release integration

- [x] Choose the initial private repository: `AnantGahlaut/cryogars-atlas`.
- [ ] Arrange any later CryoGARS transfer, preserving project history.
- [ ] Choose a code license and confirm additional contributors and acknowledgements.
- [ ] Add `LICENSE` and complete `CITATION.cff` with the final repository URL and
      contributor metadata; do not invent a DOI, funding award, or ORCID.
- [x] Distribute the browser viewer separately from the source repository.
- [ ] Decide final source-release scope and separate full-resolution archive hosting.
- [ ] Confirm attribution and redistribution requirements for the selected
      datasets and derived demo layers. Current explorers omit in-situ points.
- [ ] Review the draft README and final release scope with the author.

## Scientific release decisions

- [x] Document missing Reynolds Creek labels, Grand Mesa canopy/date substitutions,
      differing elevation sources, and unestablished label uncertainty.
- [x] Explain that common-grid spacing differs from native and display resolution.
- [x] Describe palette percentiles as colour stretches, not retained-observation counts.
- [ ] Correct or explicitly exclude the current aspect product from quantitative use.
- [x] Resolve the canopy-window choice (SNEX-006): retain the centered calculation
      and describe 11 × 11 cells / 33 × 33 m support; source/docs corrected.
- [x] Install the SNEX-006 effective-window explanation in all local explorers.
- [ ] Propagate explicit nominal/effective window metadata to backed-up archives.
- [x] Resolve derivative input stage (SNEX-007): new source uses the cleaned
      stored bases and records input-stage lineage; current cleaning retained.
- [ ] Regenerate affected terrain/canopy/incidence layers and projection-DEM
      diagnostics with verified backups; validate against their declared bases.
- [x] Correct incidence summary wording and denominator in source (SNEX-008):
      incidence >= 90 degrees over finite cells; empty summaries unavailable.
- [ ] Propagate the corrected incidence summary to backed-up final angle products
      and coordinated exports, preserving historical whole-grid metadata.
- [ ] Review approximate incidence geometry separately (remaining SNEX-008).
- [ ] Resolve inherited provenance statements that conflict with filtering/clipping.
- [x] Implement forward source/parameter/environment provenance and record it for
      the fresh display exports; preserve historical uncertainty.
- [ ] Verify complete provenance for regenerated scientific archives at release.
- [ ] Verify and hash the actual archives selected for distribution after writers stop.
      This documentation pass did not reread or rehash the large HDF5 files.
- [x] Make manifest verification fail missing/extra files and ambiguous names; use
      versioned stable dataset serialization, rejecting unsupported dtypes.
      Existing per-file SHA-256 hashes remain byte-integrity checks.

## Reproducibility and interaction

- [ ] Create a fresh environment from `environment.yml` and run the offline suites.
      The current pass validates an existing environment, not installation from scratch.
- [x] Repair `ui_preview/check_workspace.js` and validate current Info/validity behavior.
- [ ] Add portable automated checks in the selected repository. Separate checks
      requiring generated viewer pages from tests using synthetic source fixtures.
- [ ] Audit colours, legends, and saved palette copies against one palette definition.
- [ ] Have the author review palette controls, persistence, overlays, layer switching,
      automatic detail, keyboard access, and small-screen behaviour in a browser.
- [x] Verify the updated hosted viewer by HTTP and complete page hashes; final
      scientific archive downloads remain a separate release.

## Prepare the exact publishable contents

- [ ] Prepare and review the exact public release checkout. The working folder
      has Git initialized with the public source repository as `origin`.
- [ ] Include core source, templates, public docs, tests, environment, and logo.
- [ ] Retain `ui_preview/logo_notes_addon.html` and `ui_preview/check_preview.js`:
      production rendering and refresh still depend on those paths.
- [ ] Retain `ui_preview/check_logo_notes_rollout.js` for the documented add-on rollout.
- [ ] Exclude local launch/status helpers, internal handoffs, historical notes,
      logs, caches, backups, credentials, and generated inventories from source history.
- [ ] Review optional older atlas and preview helpers; omit obsolete ones from the
      chosen source package without deleting the working copies.
- [ ] Treat the eight generated explorers (about 105 MiB combined) as deliberate
      demo/release artifacts; omit older alternate viewers from that bundle.
- [ ] Scan the selected contents for credentials, local paths, private notes,
      oversized files, and unintended embedded source metadata.
- [ ] Replace draft distribution/license statements with finalized information.
- [x] Verify the authorized viewer publication against the exact selected bundle.
- [ ] Obtain final scope decisions for any later public source/scientific archive
      release, then verify its files, version, citations and download links.

The `.gitignore` is a preparation aid, not a substitute for inspecting the exact
files committed. Keep the full-resolution archive on its designated data host;
it does not need to be uploaded into Git source history.
