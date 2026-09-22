# Product-notes installation record

The dated entries below record historical stages. **Current local status,
2026-09-21:** all eight explorers were rebuilt and installed with the current
product notes, adjustable coherence-mask controls, Cells averaging and live
validity explanations. All 1,664 active notes passed the rebuild checks. See the
[rebuild record](reviews/2026-09-21-explorer-rebuild.md).

Archive repairs remain deferred; the source/read-only display rebuild did not
modify HDF5 values or metadata. Public deployment is a separate operation and
is not established by local installation. Earlier entries below preserve what
was installed, staged or held on their own dates.

Backup, candidate and manifest paths below are local evidence records relative
to the project root. They are excluded from the source repository.

## 2026-09-14 — concise copy pass

The user requested direct product descriptions with fewer repetitive contrasts.
The shared notes template now leads with each product's definition and method.
Obvious comparisons to unrelated products and repeated general disclaimers
were removed. Equations, source links, provider-specific warnings, missing-value
rules and recorded metadata remain. CSS, drawer controls and rendering are unchanged.

This is a wording-only change. The separately authorized adjustable mask cutoff
is not included. Coherence-mask content also remains pending its separate
integration; SNEX-020 and the scientific rebuild hold remain open.

Pre-edit copy backup:
`backups/pre_concise_notes_20260914_130902_419663/`.
Fresh candidate stage:
`backups/pre_logo_notes_rollout_20260914_132245_965392/manifest.json`.
No working explorer has been replaced. Visual acceptance of this revised copy
is still pending; use the new stage's `validated_candidates` pages for review.

Captured source SHA-256:

- Notes template: `dd4ce5ade47e792ab205dd855bec89bf91861570013d0e2cdec4985e2e636f6e`
- Rollout checker: `f19ed8e20369d52cf8d84062520f12f0a0f2954761a6e1ffa8fec5a63659fcf4`
- Compact metadata: `f82abcc19b89e00d5ae16ff2419989726b0386a0a429ca9f5c80adb5c6442177`

Verification:

- 25 JavaScript semantic groups and the DEM/terrain/incidence checks passed.
- 47 Python regressions passed using temporary fixtures.
- All eight candidate structural/metadata/DOM checks passed across 1,664 layers.
  Renderer, payload, comparison, layout and Info controller preservation checks
  remain intact. These are offline checks, not new browser visual acceptance.
- Independent review confirmed prose-only edits, with all 14 equation literals,
  all 18 source links, conditional dispatch, CSS and controller code unchanged.
- Existing tests that demanded the removed disclaimers were updated to check
  positive definitions, recorded units and equations instead.

The incomplete `pre_logo_notes_rollout_20260914_132010_528923` attempt stopped
at a stale DEM wording assertion. It was not installed and must not be used
as an installation candidate.

## 2026-09-14 — earlier four-product stage, superseded

**Do not install this stage.** The user's copy feedback supersedes its wording.
It remains available as recoverable evidence of the earlier checks.

Approved content: snow depth, amplitude 1, amplitude 2 and interferogram
magnitude. Coherence-mask explanations remain excluded; SNEX-020 and the
scientific rebuild hold remain open. No public deployment is part of this update.

Stage and recoverable originals:
`backups/pre_logo_notes_rollout_20260914_125042_724469/manifest.json`.
The `validated_candidates` directory contains all eight site pages. Working
`viewer/*_explorer.html` pages have **not** been replaced by this batch.

Captured approved source SHA-256:

- `ui_preview/logo_notes_addon.html`: `f1ea2691c71b51f91ad313ab9c60c29e8f4aad8b4a6cbbfb6d240c71af99b8a9`
- `explorer_addon.py`: `97e400bf3a27c0d50d40e188d3ed03cf0947f1a8ab5c6cf35de7e7285b3413c1`

Fresh verification:

- Eight staged-page structural/metadata/DOM-stub checks passed across 1,664
  display layers. Each original renderer/payload/embedded comparison bridge
  prefix and comparison suffix is exact. The approved drawer CSS/controller,
  index and renderer template are unchanged.
- 47 Python regressions passed across `test_four_product_metadata`,
  `test_explorer_addon`, `test_refresh_explorers`, `test_build_provenance`, and
  `test_comparison_addon` using temporary fixtures.
- 15 new and 10 existing JavaScript semantic groups passed, plus the existing
  DEM/terrain/incidence checks.
- The saved metadata audit executed all 662 approved display layers: 15 snow
  depth, 217 amplitude 1, 217 amplitude 2 and 213 magnitude layers. All 213 mask
  layers retained the metadata-only explanation. Its output-file write was
  suppressed; original evidence files stayed unchanged.
- Independent preservation review confirmed that removing only the new note
  function and dispatch hook recovers the old template exactly; packaging is
  unchanged. All 33 protected source/page/evidence hashes matched the handoff.

Visual acceptance is pending: the browser tool rejected the local `file:`
preview URL under its security policy. No alternate URL, server, browser
surface or indirect execution was used to bypass that rejection. Offline
DOM checks are not a browser-rendering claim.

For a newly reviewed stage, the installer revalidates the source snapshot,
candidates, originals, metadata,
payload, protected files and final installed hashes; it rejects stale inputs
and rolls back replaced pages if an installation check fails. Do not rebuild
scientific payloads to install these notes.

## 2026-09-13 — previous approved notes installed

The prior approved snapshot was installed across all eight local site pages:
DEM, slope/aspect and incidence notes, plus vegetation height, canopy fraction,
coherence and wrapped/unwrapped phase. Original renders, payloads, comparison
code, widget layout, index, and renderer template were preserved. HDF5 files
were not accessed or modified.

Recoverable originals, exact candidates and hashes:
`backups/pre_logo_notes_rollout_20260913_185238_857660/manifest.json`.
The notes-template SHA-256 was
`2bc7e552a78cf015522bb96f7517fc9c0c2edb731ad352db5cbd621a8d467ed9`.
All eight installed hashes were independently checked against the manifest.
Staged Banner Summit browser checks covered the Info footer, product switching,
DEM/canopy/aspect/wrapped-phase content, close button, focused Escape and
Info hide/reopen behavior. This earlier visual check does not certify the new
September 14 content.
