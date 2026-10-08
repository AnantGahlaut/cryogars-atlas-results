# Enrichment 3.3.0 archive rebuild — 2026-09-28 to 2026-10-08

The eight enriched archives were regenerated on Boise State's Borah cluster
from the unchanged base archives, replacing the enrichment 3.0.0 files. This
applies the source fixes that had waited for verified backup storage:
SNEX-001 (aspect direction), SNEX-007 (cleaned derivative inputs), SNEX-008
(look-side and projected heading) and SNEX-026 (derived-product metadata).

## Method

- **Environment:** created fresh on Borah from `environment.yml`. The clean
  install exposed a missing `pandas` dependency, now pinned.
- **Isolation:** one Slurm job per site (`scripts/borah/enrich_site.sbatch`)
  read each verified base file through a symlink in a scratch staging folder,
  so no job could write into `SNOWEX/LIDAR`. Each job recorded the base file's
  size and modification time before and after; all were unchanged.
- **Swap:** each candidate was copied beside its target, its SHA-256 compared
  with the staged candidate, and only then renamed over the 3.0.0 file
  (`scripts/borah/replace_enriched.sbatch`). New manifests were written and
  checked with `sha256sum -c` (16 of 16 OK, 2026-10-08). The archive now totals
  289.15 GB (269.29 GiB; Grand Mesa enriched 120.07 → 114.03 GB). The previous
  manifests are kept as `MANIFEST_2026-08-31.*`. The 3.0.0 enriched files were not retained.

## Checks

| Site | Enrichment time | `verify_enriched` checks | Explorer layers |
| --- | ---: | ---: | ---: |
| Cameron Pass | 1.6 min | 275 | 149 |
| Dry Creek | 1.3 min | 118 | 64 |
| Reynolds Creek | 1.3 min | 62 | 35 |
| Little Cottonwood | 3.6 min | 338 | 177 |
| Fraser | 3.8 min | 285 | 159 |
| Mores Creek | 4.4 min | 552 | 305 |
| Banner Summit | 19.9 min | 607 | 329 |
| Grand Mesa | 3 h 17 min | 851 | 446 |
| **Total** | | **3,088** | **1,664** |

- **Aspect:** checked against an independent DEM-gradient bearing on about one
  million steep Cameron Pass cells: median difference 1.0°, against 68° for the
  3.0.0 archive.
- **Explorers:** layer counts identical to the previous explorers; all 856
  sampled display cells matched values recomputed from the archive within one
  quantisation step; Node page checks passed for all eight pages and the index.
  Viewer code is unchanged — only embedded data and metadata differ.
- **Independent audit (`audit_archive.py`):** the first pass found 21 failures.
  Nineteen were unwrapped-phase layers left empty because the provider filled
  pairs it could not unwrap with 0.0, which enrichment masks; enrichment now
  records `empty_reason = unwrapper_zero_fill` and the audit verifies it.
  Two were Grand Mesa's measured SWE and density rasters, which the builder
  specifies but the base archive predates; they are now reported as a known
  omission. The four affected sites were re-enriched and re-audited.
- **Reproduction:** `get_dataset.py` rebuilt Cameron Pass from NASA into an
  empty folder in 17 minutes using the frozen inventory
  (`scripts/borah/repro_check.sbatch`). All 127 base and 132 enriched datasets
  matched the published files in shape, type and valid-cell count; 111 were
  bit-identical and the remaining 148 agreed within 1e-4 relative, the residue
  of a newer GDAL. None differed. The test also exposed that GDAL 3.12 rejects
  the bearer-header login used in August; the builder now uses netrc and a
  cookie jar.

## What changed in the data

Display-level comparison of every layer with the 3.0.0 explorers. No layer was
added or removed. Cell percentages count display cells that moved by more than
one quantisation step.

| Site | Aspect | Flat incidence | Local incidence |
| --- | --- | --- | --- |
| Banner Summit | 66% of cells, median 75° | 68%, 0.97° | 57%, 0.96° |
| Cameron Pass | 60%, 72° | 23%, 0.05° | 4%, 0.14° |
| Dry Creek | 49%, 84° | 51%, 0.76° | 41%, 0.73° |
| Fraser | 71%, 89° | 66%, 0.16° | 3%, 0.18° |
| Grand Mesa | 87%, 95° | 66%, 0.46° | 55%, 0.49° |
| Little Cottonwood | 36%, 86° | 5%, 0.03° | 1%, 0.22° |
| Mores Creek | 52%, 81° | 51%, 0.06° | 14%, 0.21° |
| Reynolds Creek | 69%, 75° | 17%, 0.04° | 0.04%, 0.16° |

- **Grand Mesa incidence coverage fell by 19%** (738,693 to 595,424 display
  cells). Some flight tracks cross this large site; the look-side correction now
  leaves the side the radar did not view empty instead of assigning it angles.
- Canopy fraction and slope changed in at most 0.05% of cells, from the cleaned
  input stage. Wrapped-phase layers differ by rounding only.
- Snow depth, vegetation height, elevation, amplitudes, coherence, coherence
  masks, unwrapped phase and interferogram magnitude are unchanged.

## Limits

These checks establish that the stored values follow the documented methods
and that files transferred intact. They do not establish measurement accuracy.
Radar geometry remains approximate, and the other limitations listed in the
[design document](../design.md#accepted-limitations-for-v1005) still apply.
