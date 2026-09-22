# Color editor performance — 2026-09-22

The color editor called the complete layer-selection update for every pointer
movement. That copied and uploaded unchanged terrain-aligned values, rebuilt
Info (also notifying an open product-notes panel), and reuploaded Cells textures
for both active layers. The rendering loop also drew continuously while idle,
and direct event-driven draws duplicated animation-frame draws.

An exact-source benchmark with installed Banner Summit and Grand Mesa exports
confirmed the repeated work. At full Detail, 100 Banner Summit color edits issued
348 MB of buffer/texture upload calls (522 MB with an overlay); Grand Mesa issued
396 MB (594 MB with an overlay). These are byte counts passed to instrumented GL
calls, not measured GPU transfer rates. Warm averaging caches performed no new
decoding or aggregation, so their numerical implementation was retained.

Appearance edits now use an explicit style-only path through the existing
primary/overlay setters. It updates uniforms, ramps, range labels and saved
preferences without touching data buffers, Info or Cells textures. Both active
roles update if they share a layer. Layer selection and mask-cutoff changes keep
the full update path. Redraws coalesce into one animation frame, including the
comparison bridge at render time, and stop when idle or hidden. CSS canvas
resizing and returning to the viewer request fresh frames. Idle time is labeled
separately; pending-frame delays still count as rendering stalls.

Validation:

- Eight palette regressions verify 100 live edits with zero data uploads or Info,
  Cells, palette-selector or editor rebuilds; live colors/persistence, overlays,
  normal data refresh and actual mask bridge/cutoff behavior still work.
- Five scheduler regressions cover burst coalescing, late-bound comparison hooks,
  idle boot, genuine stalls, hidden/flat states and CSS resizing.
- Thirteen Cells aggregation/control tests and 138 comparison tests pass.
- Workspace checks render Info for all 1,664 layers across eight sites. The
  metadata-only pass avoids redundant full-grid decoding; synthetic validity
  tests continue to exercise the real calculations.
- Forty-six composition/provenance/refresh tests and seven public packaging tests
  pass. The add-on accepts both the new boot marker and older exported pages.

All eight local explorers were refreshed with their exact embedded payload bytes
preserved. The original UI pages were backed up under
`backups/pre_atlas_explorer_20260922_133137_509674/`. No HDF5 archive was opened
or changed. Local evidence is in `tmp/fps_diagnosis_20260922/`,
`tmp/fps_scheduler_20260922/` and `tmp/performance_release_20260922/`.

These checks establish the removed work and preserved contracts; browser frame
rates and visual responsiveness on the user's device have not been measured.

Published to the existing [public explorer](https://anantgahlaut.github.io/cryogars-atlas-viewer/)
in distribution commit [0addc3f](https://github.com/AnantGahlaut/cryogars-atlas-viewer/commit/0addc3f31cf5ffa3d095c2c5b663adedb178551d).
All nine canonical URLs returned HTTP 200 and matched the prepared SHA-256 hashes
on 2026-09-22T19:38:12Z. Source changes are committed separately.
