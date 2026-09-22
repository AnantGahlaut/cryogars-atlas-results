# Eight-site explorer rebuild — 2026-09-21

All eight local explorers and the index were installed after fresh display exports
from the existing HDF5 archives. Cells averages to the selected Detail spacing at
every site, with current product notes and stored/shown validity explanations.
The exports include circular aspect aggregation, unambiguous radar dates and
flight-line labels, angular unit suffixes and truthful stretch descriptions.

SNEX-026 is fixed in the enrichment writer and archive auditor. New derived
products carry validated dataset-local grid and lineage metadata, tagged
`derived_metadata_version=1.0`. The audit treats mask 255 as missing and 0 as valid,
checks declared empty-input cases independently, and still rejects missing
required metadata in old archives.

Final validation caught the unchanged Grand Mesa archive reintroducing its old
photogrammetry description. The reviewed SNEX-004 correction now runs in the
exporter, scoped to the exact reference filename. The installed page preserves
the original text/history. Its numeric export provenance remains unchanged;
the later text correction has a separate source-hashed provenance record. Every
other payload field, including packed arrays, was verified unchanged by this
text-only correction.

## Validation

- 390 offline Python tests passed, including tiny HDF5 metadata/audit and export
  fixtures. Tested source hashes stayed unchanged.
- All 1,664 packed layers and all 1,664 product-note renderings passed across eight
  sites. Layer rosters, stored valid counts and matching tables were preserved.
- 856 native block samples across 119 site/product-family combinations agreed
  with independent scalar/complex/circular calculations within packing precision.
- All 1,601 radar/geometry date labels and 150 angular unit suffixes passed;
  complete layer labels are unique within each site.
- 17 page/comparison/index checks passed. Index executable behavior and alternate
  viewers were preserved. Installed outputs match validated candidate hashes.
- Existing numerical Cells/rendering evidence remains applicable: renderer code
  did not change during this export. This run performed no visual browser review.

Export took **41 minutes 28 seconds**. From export start through final installation,
including the Grand Mesa correction and revalidation, elapsed time was
**50 minutes 43 seconds**. Source work and earlier tests preceded the export.

## Remaining scope

No HDF5 values or attributes were changed and no large archive backups were
created. Archives were opened read-only; filename/size/modification-time identities
were unchanged. That check is not a fresh full-content hash audit. Existing exact
HTML rollback copies were reused from `backups/site_cell_colors_20260921_verified/`.

The 375 historical derived datasets still lack the SNEX-026 metadata fields.
Stored aspect still has the historical north/south reflection; its display now
uses circular means of those stored angles. Historical derivative-input and
incidence-geometry issues, backup-dependent metadata repairs, height/navigation
validation, Grand Mesa native-spacing evidence and deferred SNEX-015–017 remain
open. This local display rebuild is not a corrected scientific-archive release
or a public deployment. Final design-document synthesis also remains separate.

## Local evidence

- `tmp/explorer_rebuild_20260921/installed.json`: installed hashes and scope.
- `tmp/explorer_rebuild_20260921/export_validation.json`: all-site packed-data,
  label and native-block results.
- `tmp/explorer_rebuild_20260921/notes_all_sites.json` and `page_checks.json`:
  product notes and standalone-page checks.
- `tmp/explorer_rebuild_20260921/metadata_postprocess.json`: Grand Mesa correction
  and original-versus-current exporter identities.
- `backups/snex004_export_20260922/python_full.json`: final 390-test run.
- `backups/snex026_source_20260921/`: failing-first tests, source manifests and
  SNEX-026 acceptance evidence.
- `backups/label_export_acceptance_20260922T041624Z/`: focused label/export checks.

These evidence folders are local and excluded from the source repository.
