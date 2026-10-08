# SnowEx Field Atlas design

**v1.0.05 · updated 2026-10-05.** This document connects the data sources to the
stored products and the numbers and colours in the explorer. It describes the
enrichment 3.3.0 archives rebuilt on Borah on 2026-09-28 and the explorers
exported from them. It is not a claim that all historical processing can be
reproduced from today's source: the base archives predate forward provenance.

## 1. Scope and artifact status

The project aligns NASA SnowEx LiDAR and UAVSAR observations for inspection,
comparison and later analysis. It provides per-site HDF5 archives and a browser
atlas. It does not currently produce a validated radar snow-depth/SWE retrieval,
trained model, uncertainty map or NISAR product.

| Artifact | Current state |
| --- | --- |
| Base archives | Product version 0.1.0, unchanged since 2026-08-29. Byte-verified on Borah against the 2026-08-31 manifest (Slurm job 3280352). |
| Enriched archives | Enrichment 3.3.0, regenerated on Borah 2026-09-28 from the unchanged base files: corrected aspect, cleaned-input derivatives, corrected viewing geometry and `derived_metadata_version=1.0` metadata. They replace the 3.0.0 enriched files, which are not retained. |
| Archive location | `/bsushare/hpmarshall-shared/SNOWEX/LIDAR` on Boise State's Borah cluster: 16 files (8 base, 8 enriched) with `MANIFEST.json` and `MANIFEST.sha256`. |
| Explorers | Eight pages exported from the 3.3.0 archives, 1,664 layers. Remains a research preview. |
| Release label | v1.0.05 is the project label, separate from the product and enrichment algorithm versions. |

The [3.3.0 rebuild record](reviews/2026-10-05-archive-rebuild-3.3.0.md)
documents the regeneration, its checks and what changed. Earlier records —
the [September 21 display rebuild](reviews/2026-09-21-explorer-rebuild.md) and
[September 22 publication](reviews/2026-09-22-public-viewer-update.md) — describe
the 3.0.0-era explorers.

## 2. Sites and source selection

All archive grids have 3 m spacing. The reference raster supplies each site's
projected CRS; these are not interchangeable coordinate systems. Counts below
describe the retained current snapshot, not every available provider product.
Fine spacing applies to exported terrain/LiDAR; radar colours are coarser.

| Site | EPSG | SD dates | VH dates | Radar groups | Layers | Fine / radar export m |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Banner Summit | 6340 | 2 | 2 | 11 | 329 | 24 / 96 |
| Cameron Pass | 6342 | 1 | 1 | 5 | 149 | 12 / 48 |
| Dry Creek | 6340 | 1 | 1 | 2 | 64 | 15 / 60 |
| Fraser | 6342 | 2 | 2 | 5 | 159 | 15 / 60 |
| Grand Mesa | 32612 | 6 | 2 | 27 | 446 | 72 / 288 |
| Little Cottonwood Canyon | 6341 | 1 | 1 | 6 | 177 | 15 / 60 |
| Mores Creek | 6340 | 2 | 2 | 10 | 305 | 12 / 48 |
| Reynolds Creek | 26911 | 0 | 1 | 1 | 35 | 18 / 72 |
| Total | — | 15 | 12 | 67 | 1,664 | — |

A radar group is a site/date-pair/flight-line combination, not an independent
aircraft pass. Grand Mesa contains two campaigns over overlapping ground.
The 16 base/enriched files total 289.15 GB decimal (269.29 GiB); the viewer
contains much smaller derived display arrays.

### LiDAR source inventory

The six QSI sites use Version 1 `SNEX20_QSI_DEM_3m`, `SNEX20_QSI_SD_3m` and
`SNEX20_QSI_VH_3m`. Their filename pattern is
`SNEX20_QSI_<DEM|SD|VH>_3M_<site token>_<begin>_<end>.tif`.
The intervals below are the recorded source tokens, not inferred single-pass times.

| Site / token | DEM interval | SD and VH intervals |
| --- | --- | --- |
| Banner Summit / USIDBS | 2021-09-17 | 2020-02-18–19; 2021-03-15 |
| Cameron Pass / USCOCP | 2021-09-18 | 2021-03-19 |
| Dry Creek / USIDDC | 2021-09-16 | 2020-02-19 |
| Fraser / USCOFR | 2021-09-18–21 | 2020-02-11; 2021-03-19–20 |
| Little Cottonwood / USUTLC | 2021-09-21 | 2021-03-18 |
| Mores Creek / USIDMC | 2021-09-17 | 2020-02-09; 2021-03-15 |

Grand Mesa uses `SNEX_HRSI_SD_DEM_CO_GM_DTM_1m_V01.0.tif`, the collection's
**LiDAR-derived snow-off reference DTM**, resampled to 3 m. Its six snow-depth
products comprise five ASO 3 m surveys (2017-02-08, 16, 20, 21 and 25) and
`SNEX20_GM_Lidar_SD_20200201_20200202_v01.0.tif`. The latter's saved catalog
date is February 1 while its filename spans February 1–2; that discrepancy and
its exact native spacing remain unresolved. QSI VH intervals are 2020-02-01–02
and 2020-02-13. They are later than the 2017 snow observations.

Reynolds Creek uses the ORNL `RCEW_DEM_1m.tif` reference (2014-08-23–31),
resampled to 3 m, and QSI VH from 2020-02-18–20. There is no snow-depth layer.
Collection identifiers and provider references are in [Data sources](data-sources.md).
Incoming ASO packages, in-situ observations, SWE/density and radar projection DEMs
are outside the current displayed product inventory.

### Radar product availability at every site

Amplitude 1/2 come from `AMPLITUDE_GRD`; complex interferograms, coherence and
unwrapped phase come from `INTERFEROMETRY_GRD`, distributed through ASF.
Annotations retain pass dates, flight line, polarization and available geometry.
The exact product IDs and members belong to each layer's source metadata.

| Site | Amp1 / Amp2 each | Magnitude / wrapped / coherence / mask each | Unwrapped shown / stored | Flat / local incidence each |
| --- | ---: | ---: | ---: | ---: |
| Banner Summit | 44 | 44 | 34 / 40 | 11 |
| Cameron Pass | 20 | 20 | 13 / 16 | 5 |
| Dry Creek | 8 | 8 | 6 / 8 | 2 |
| Fraser | 20 | 20 | 20 / 20 | 5 |
| Grand Mesa | 57 | 53 | 53 / 53 | 27 |
| Little Cottonwood | 24 | 24 | 15 / 24 | 6 |
| Mores Creek | 40 | 40 | 36 / 36 | 10 |
| Reynolds Creek | 4 | 4 | 4 / 4 | 1 |

Every site has one DEM, slope and aspect. Canopy products match the site's VH
dates. Thus the site tables and product contracts below cover all 15 displayed
families. A complete public per-granule/channel appendix is still needed; these
tables do not substitute for exact retained source identities.

## 3. Acquisition, alignment and storage

```text
Provider catalogs and downloaded rasters/annotations
  → discovery inventory and spatial/temporal selection
  → common-grid alignment and builder cleaning
  → <site>.h5
  → enrichment cleaning, grouping and derivation
  → <site>.enriched.h5
  → display block reduction and quantization
  → embedded HTML payload → browser rendering and comparison
```

[The builder](../build_hdf5.py) searches NASA CMR and ASF by the configured
site/product footprint. LiDAR–radar matching requires footprint intersection
and a minimum survey-endpoint/radar-endpoint gap of at most five days. Nearness
to one radar pass does not establish nearness to both. Timeline survey markers
identify survey starts; match tables preserve the endpoint-based gap.

The DEM reference determines the 3 m projected grid and grid phase. Current
extent selection intersects the DEM and science extent; the 500 m source-read
buffer is not an empty border in the stored grid. For affine coefficients
`[a,b,c,d,e,f]`, the center of row r, column k is:

```text
x = c + a*(k+0.5) + b*(r+0.5)
y = f + d*(k+0.5) + e*(r+0.5)
```

Scalar rasters use the configured product resampler (bilinear for the displayed
continuous imports); complex interferograms use nearest-neighbour alignment.
Resampling onto 3 m cells does not create native 3 m radar information.
The base archive already contains aligned, processed values; it is not untouched
sensor data. Compression preserves these stored values without further averaging.

Builder cleaning removes configured sentinels/range failures, screens configured
spikes, then optionally fills missing cells from at least three finite cardinal
neighbours, by default for two passes. Complex interferograms bypass this scalar
cleaner. Enrichment applies further range and connected-component screens. Small
LiDAR components use four-neighbour connectivity and a 100-cell cutoff, with a
sole component retained. Historical processing counts are incomplete; finite
values can be measured, interpolated or derived.

For each radar polarization, swath support is the intersection of finite,
nonzero values in the available amplitude1/amplitude2/coherence inputs. Matching
arrays are masked to that support; exact-zero unwrapped phase is additionally
removed where the swath rule applies. That convention can remove true zero phase.
See [cleaning details and limitations](scientific-notes.md#enrichment-and-cleaning).

## 4. Product contracts and processing mathematics

Current derivations are implemented in [enrich_hdf5.py](../enrich_hdf5.py).
The 3.3.0 archives derive every product below from the cleaned DEM and VH
layers stored in the same enriched file, and record that input stage. Floating missing values
are NaN; native coherence masks use 255, distinct from valid 0 and 1.

| Family (displayed count) | Source / stored meaning and units | Computation and display reduction |
| --- | --- | --- |
| Elevation (8) | Selected provider terrain, metres; source-specific height reference | Imported/aligned/cleaned. Finite block mean; separate 16-bit terrain and 8-bit colour copies. |
| Snow depth (15) | Imported provider depth, metres; QSI, ASO 2017 and Grand Mesa 2020 methods differ | Not computed by subtracting the displayed DEM. Enrichment clips `[-0.25,0)` m to zero, rejects below −0.25 or above 15 m, and screens components. Finite block mean. |
| Vegetation height (12) | Imported QSI vegetation height, metres | Not recomputed from displayed elevation. Finite block mean; survey/surface epoch matters. |
| Canopy fraction (12) | VH-based proxy, dimensionless | Count finite VH≥2 m / count finite VH in centered 11×11 cells (33×33 m). Truncated edge window; empty window missing; missing center may still have a fraction. Finite block mean of fractions. |
| Slope (8) | Derived DEM slope, degrees | `atan(hypot(gx,gy))` converted to degrees. Display averages fine-grid slopes, not slope recalculated from a coarse DEM. |
| Aspect (8) | Derived downhill grid bearing, degrees clockwise from grid north | `degrees(atan2(-gx,-gy)) mod 360`; flat/missing is undefined. Checked against an independent DEM-gradient bearing on about 1 million steep Cameron Pass cells: median difference 1.0° (the 3.0.0 archive's reflected aspect differed by 68°). Export uses circular means. |
| Amplitude 1 (217) | Provider linear amplitude for pass 1 | Imported/aligned/cleaned/masked; finite mean, then P2–P98 packing. No local logarithm or dB conversion. |
| Amplitude 2 (217) | Provider linear amplitude for pass 2 | Same operations, distinct annotated pass. |
| Interferogram magnitude (213) | Magnitude of imported complex I; recorded magnitude units (cached annotations identify linear power) | `mean(abs(I))` over finite complex inputs, then P2–P98 packing; not `abs(mean(I))`. |
| Wrapped phase (213) | Phase of imported complex I, radians | `arg(mean(I))`; source magnitudes weight the resulting direction. Near cancellation can destabilize phase. Browser angular interpolation is a later operation. |
| Unwrapped phase (181) | Provider-unwrapped phase, radians | Imported, not unwrapped locally. Swath/zero screening as above; finite scalar block mean. Reference/settings are not fully recorded. |
| Coherence (213) | Imported dimensionless provider estimate | Aligned/cleaned/masked, then finite mean. A display mean is not a new coherence estimate from complex observations. |
| Coherence mask (213) | `1` for finite cleaned coherence≥0.30; `0` below; `255` missing | Export mean of valid 0/1 cells gives passing fraction. Browser Average/0–1 uses that fraction and an adjustable display cutoff. |
| Flat incidence (67) | Approximate angle from vertical, degrees | `acos(lz)`, where l is the unit ground-to-platform vector. Uses ground elevation but omits surface tilt. Finite scalar block mean. |
| Local incidence (67) | Approximate angle from upward surface normal, degrees | `acos(clamp(n·l,-1,1))`. Same approximate platform geometry; finite scalar block mean. |

For a north-up DEM neighborhood `a b c / d z f / g h i` and spacing s:

```text
gx = ((c+2*f+i) - (a+2*d+g)) / (8*s)
gy = ((a+2*b+c) - (g+2*h+i)) / (8*s)
n  = (-gx,-gy,1) / sqrt(gx²+gy²+1)
```

Horn slope/aspect repeats outermost cells and propagates missing neighbours.
Aspect is undefined when `hypot(gx,gy)<1e-9`. Incidence instead substitutes the
finite DEM mean for missing normal-stencil neighbours and masks missing centers.

The corrected incidence source approximates a straight track through the peg,
converts geographic heading to grid bearing using projected geodesic endpoints,
and places the platform at the nearest track point at constant average altitude.
It retains only the declared look side. Compared with 3.0.0, median display
changes were 0.03–0.97° for flat incidence and 0.14–0.96° for local incidence.
Aircraft altitude reference, time-resolved navigation and terrain/aircraft height
compatibility remain unverified. Six QSI terrain references are NAVD88/GEOID12b;
Grand Mesa is WGS84 ellipsoidal; Reynolds' exact reference remains unresolved.
No vertical conversion, terrain-occlusion model or physical accuracy bound is
established. Incidence≥90° is an angle condition, not proof of radar shadow.

## 5. Exported values to screen colours

[make_explorer.py](../make_explorer.py) reads the chosen archive without changing
it. Only science arrays matching the site's common-grid shape become colour
layers. Complete blocks are retained; partial bottom/right archive blocks are
cropped. For finite scalar inputs, a block is `sum(v)/count(v)`; no finite inputs
means missing. One finite input can make a whole display block finite.
Aspect instead averages `(cos θ,sin θ)` and recovers `atan2`; resultant magnitude
≤1e-12 is missing. Complex magnitude and phase use their table-specific reducers.

There are 1,471 stored scientific rasters plus 213 additional complex views,
minus 20 omitted unwrapped rasters: 1,664 display layers. Nineteen omitted arrays
have no valid stored values. Cameron Pass's remaining omitted raster has 124 valid
cells entirely in a discarded bottom strip; the approved cropping rule remains.

For b-bit packing, code 0 means missing; finite values have:

```text
Q = 1 + round_to_even(clamp((v-lo)/span,0,1) * (2^b-2))
span = hi-lo when positive, otherwise 1
decoded = lo + (Q-1)/(2^b-2) * (hi-lo)
```

Terrain uses b=16; colour layers use b=8 (254 intervals). Ordinary layers use
finite min/max bounds; amplitude/magnitude use finite block P2/P98, falling back
to min/max when those coincide. Clipped tails cannot be recovered by a palette
change. Probes read approximate decoded values, not native archive values.

The [renderer](../explorer_template.html) maps exported cell centers into the
terrain grid using origins and signed spacing, leaving unsupported positions
missing. Detail level L uses terrain spacing×2^L and
`ceil(W/2^L) × ceil(H/2^L)` positions.

| Control | Actual mathematics |
| --- | --- |
| Smooth | Interpolates finite values across terrain vertices with finite-support weighting. Aspect/wrapped phase interpolate unit directions; cancellation is missing. Palette mapping and terrain illumination follow. |
| Cells | At current Detail spacing, averages decoded exported values by overlap area: `sum(Ai*vi)/sum(Ai)` over finite inputs. Blocks start at terrain origin; final partial blocks clip to its retained rectangle. Each block gets one palette colour without neighbour blending or terrain shading. |
| Angular Cells | Area-weighted mean unit directions; cancelling directions are missing. Unwrapped phase remains scalar. |
| Mask Cells | Average exported passing fractions before applying the selected cutoff once. This is not a pooled native passing fraction because original block valid-count denominators are unavailable. |
| Overlay | Selected opacity blends primary and overlay colours. Cells still respects this blend. |
| Palette/stretch | Maps decoded values to colour; does not change archive values or restore export clipping. Percentile labels describe stretches, not retained observations. |

Palette, reverse and contrast edits update shader uniforms and the legend while
retaining the uploaded values and Cells textures. Layer selection, Detail changes
and mask classification still refresh the affected data. Redraw requests are
coalesced into one animation frame, with no repeated terrain draws when the scene
is unchanged or hidden. The FPS indicator marks inactivity as idle.

At 192 m Detail, Cells therefore displays one colour per 192 m block, even if
its source display array is 24 m. Auto changes the spacing with resolved detail.
For temporary categorical results, binary Cells uses overlap-weighted majority
(ties→1); signed −1/0/+1 transitions use a unique weighted mode (ties→missing).

**Valid stored** is finite stored cells / all archive cells at archive spacing.
**Valid display** in Details is finite exported cells / all exported cells.
**Valid shown** follows the current rendering grid: finite sampled values in
Smooth or finite block aggregates in Cells, with a finite corresponding terrain
sample, divided by all rendering positions. Zero is valid. These are whole-grid
counts, not visible screen pixels or viewport coverage; camera detail changes can
alter Valid shown while panning at fixed detail does not.

The [comparison module](../viewer_compare/) aligns B to A's exported analysis
grid and uses shared finite support for B−A. Angular differences wrap to their
shortest signed interval; temporary comparisons have no stored archive count.
Figure export uses the analysis grid rather than a terrain screenshot. See the
[comparison contract](BROWSER_COMPARISON.md) for import rules and statistics.

## 6. Schema and reproducibility boundaries

```text
identification/                    site, CRS, transform, shape, versions
matches/                           recorded LiDAR–radar matches
science/LIDAR/DEM/grids/elevation
science/LIDAR/SD/<survey>/snow_depth
science/LIDAR/VH/<survey>/veg_height
science/LIDAR/DERIVED/              slope, aspect, canopy fractions
science/UAVSAR/<date1>_<date2>/<flight line>/
    <polarization>/                amp1, amp2, int, cor, unw, coherence_mask
    GEOMETRY/                      flat/local incidence
```

Source IDs, dates, units, transforms, cleaning metadata and input lineage belong
with the relevant products. New derived arrays record validated dataset-local
CRS/transform/spacing, local product identity, exact input path and
`resampling_method=none`: no new warp occurs during derivation, though the input
may already have been resampled. Every derived dataset in the 3.3.0 archives
carries these fields; the 375 3.0.0 datasets that lacked them were replaced.

[Build provenance](../build_provenance.py) records forward source identities and
processing stages; it cannot reconstruct unknown historical commands or software.
The Grand Mesa viewer-only source-description correction preserves prior text
and has separate provenance, without changing numeric arrays. Per-file hashes
verify bytes; array audits and synthetic tests verify particular contracts.
Neither establishes measurement accuracy or scientific fitness.

## 7. Validation and remaining review

### 3.3.0 rebuild checks

The enrichment ran as one Slurm job per site on Borah
([scripts/borah](../scripts/borah/)), reading the verified base files through
symlinks in a staging folder so no job could write into the archive directory.
Each job recorded the base file's size and modification time before and after.

- **Environment:** created fresh from `environment.yml`; the install exposed a
  missing `pandas` dependency, since added. 384 of 393 offline Python tests pass
  there; the other nine need Node.js, which runs the page checks locally.
- **Enrichment invariants:** `verify_enriched.py` passed 3,088 checks across the
  eight sites (Grand Mesa 851). Every base file was unchanged.
- **Aspect:** independently re-derived, as in §4.
- **Explorer export:** layer counts are identical to the previous explorers at
  every site (1,664 in total). All 856 sampled display cells matched values
  recomputed from the archive within one quantisation step. Node page checks
  passed for all eight explorers and the index; viewer code is unchanged.
- **Reproduction:** `get_dataset.py` rebuilt Cameron Pass from NASA into an
  empty folder; all 259 datasets matched the published files (111 bit-identical,
  148 within 1e-4 relative after a GDAL upgrade).
- **Independent audit:** `audit_archive.py` reads every array of all sixteen
  files. Nineteen unwrapped-phase layers are empty because the provider filled
  pairs it could not unwrap with zero, which enrichment masks; enrichment now
  records that reason and the audit verifies it.
- **Swap:** each enriched file was copied into the archive directory and renamed
  over its predecessor only after its SHA-256 matched the staged candidate. New
  manifests were then written and checked with `sha256sum -c`.

The [rebuild record](reviews/2026-10-05-archive-rebuild-3.3.0.md) lists the
per-site results and the layer-by-layer differences from 3.0.0. In short, only
aspect (median 72–89° at every site), flat and local incidence (under 1°), and
a few edge cells of canopy fraction and slope changed. Snow depth, vegetation
height, elevation and all radar channels are unchanged.

### Accepted limitations for v1.0.05

These are documented rather than resolved. None changes the stored values; each
limits how they may be interpreted.

- **Radar geometry is approximate.** Aircraft altitude reference, time-resolved
  navigation and terrain/aircraft height compatibility are unverified, and no
  terrain occlusion is modelled. Incidence ≥90° is an angle condition, not a
  shadow mask.
- **Vertical references differ.** Six QSI sites are NAVD88/GEOID12b, Grand Mesa
  is WGS84 ellipsoidal and the Reynolds Creek reference is unresolved; no
  vertical conversion is applied.
- **Grand Mesa 2020 snow depth** has an unresolved catalog date (February 1 vs
  1–2) and native spacing.
- **Label uncertainty is not established.** There are only ten independent
  snow-on LiDAR dates archive-wide; Reynolds Creek has none.
- **Historical lineage is incomplete.** Forward provenance is recorded for 3.3.0;
  the base archives' exact build commands cannot be reconstructed.
- **Grand Mesa's measured SWE and snow density are not in the archive.** The
  builder specifies two SnowEx20 IOP rasters (lidar + GPR, 1 m, covering ~6% of
  the site), but they were added after the Grand Mesa base file was built. The
  audit reports them as a known omission; they are the natural first addition
  to a later release.
- **Rebuilding relies on ASF's original product URLs.** ASF reorganised UAVSAR
  in 2026: the zipped products this archive used no longer appear in ASF search,
  though their recorded URLs still redirect to the files. `get_dataset.py`
  therefore downloads from a frozen inventory rebuilt from the archives' own
  records (`snowex_inventory.json`); fresh discovery currently finds no UAVSAR
  until the builder supports ASF's new per-file layout.

Deferred to later work: incoming ASO packages (SNEX-015–017), an exhaustive
per-granule appendix, and broader cross-browser and accessibility acceptance.
