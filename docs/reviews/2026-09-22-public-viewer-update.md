# Public viewer update — 2026-09-22

The September 21 eight-site explorer rebuild is now published at the existing
[SnowEx Field Atlas URL](https://anantgahlaut.github.io/cryogars-atlas-viewer/).
Distribution commit:
[`58ae308`](https://github.com/AnantGahlaut/cryogars-atlas-viewer/commit/58ae308fe9cf68013a88b6de22cd909decad082e).

The update includes current product notes and methods, Detail-based Cells
averaging, Smooth mode, stored/shown validity help, adjustable coherence-mask
views, circular display aggregation and label/date/unit corrections across
1,664 layers. It publishes the validated display exports from unchanged
historical scientific archives; the remaining archive limitations still apply.

Only the index, eight site pages and distribution README changed. The existing
`.nojekyll` was retained. No HDF5 archives, local backups or review files were
uploaded. The source repository was not pushed as part of this deployment.

Publication packaging removes absolute local-machine paths from lineage JSON.
Original record/recipe digests are explicitly labeled pre-redaction identities;
redacted records do not claim their original recipe checksum is still verifiable.
All scientific JSON fields, all 1,664 layer arrays, executable HTML/scripts and
local viewer source files were independently checked unchanged by packaging.

Seven focused packaging tests passed. After deployment, HTTP GETs to the
canonical index and all eight explorer URLs returned 200. Their complete
SHA-256 hashes matched the prepared public files. This verifies served bytes;
it does not claim new visual, cross-browser or accessibility acceptance.

The [local rebuild record](2026-09-21-explorer-rebuild.md) contains the scientific
display-export tests and remaining archive-repair scope. Existing HDF5 files
were not opened or changed for this publication.

Local evidence is in `tmp/public_site_20260922/`: `package_report.json`,
`package_tests.json`, `independent_checks.json`, `published.json` and
`live_verification.json`. The reusable packaging command is documented in the
[workflow guide](../workflows.md#prepare-a-public-viewer-bundle).
