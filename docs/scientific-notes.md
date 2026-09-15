# Processing and scientific limitations

Updated 2026-09-14. These are living methods notes: descriptions of corrected
source below do not imply that the existing archives or installed explorers
have been regenerated. Their historical derivatives and display payloads still
await the coordinated repair and rebuild, which remains on hold. The dated
product trace (`docs/product_trace/README.md`) and quality review
(`docs/product_trace/10_product_quality_review.md`) preserve what was observed
at each review. These are local evidence records, excluded from the source
repository. Historical exports do not identify their exact source
commit, so today's code cannot establish how every archived value was produced.

## Alignment and matching

Each site has its own projected 3 m common grid. Read `identification` for its
CRS, affine transform, shape, and grid spacing; sites do not all use the same
UTM zone or datum. Resampling does not increase an instrument's measured
spatial information.

The builder searches by footprint and matches LiDAR survey dates to UAVSAR
acquisition endpoints with a five-day maximum minimum separation. A match can
be close to one radar pass without being close to both. Neither footprint
intersection nor a temporal match establishes complete spatial overlap or
unchanged snow conditions.

For a multi-day LiDAR survey, matching uses the closest survey/radar endpoints.
A timeline symbol marking the survey start can therefore have a different
visible separation from the stored endpoint gap. The matching table contains
recorded match rows and unmatched verdicts; it is not a ledger of every search
candidate considered.

Continuous rasters and complex interferograms require different treatment.
The builder selects resampling by product, using nearest-neighbour sampling
for complex interferograms. The explorer derives displayed phase from averaged
complex values rather than averaging wrapped phase angles directly.

## Source differences and missing labels

| Issue | Consequence for analysis |
| --- | --- |
| Grand Mesa combines five 2017 ASO snow-depth surveys with one 2020 survey | Review campaign methods and processing before pooling labels. |
| Grand Mesa's selected HRSI DEM is a LiDAR-derived snow-off terrain reference, resampled from 1 m to 3 m | Distinguish this DTM from the collection's satellite-derived snow-on DEMs; see [source details](data-sources.md). |
| Reynolds Creek's DEM is from 2014; its vegetation survey is from 2020 | Temporal agreement cannot be assumed from grid alignment. |
| No snow-depth layer exists at Reynolds Creek | It cannot supply supervised snow-depth labels in this snapshot. |
| Grand Mesa's 2017 scenes use later canopy information | Vegetation-derived predictors are not contemporaneous with those snow-depth surveys. |
| Label uncertainty has not been established by this pipeline | Passing range checks does not make LiDAR labels error-free. |

The current exports contain no SWE, snow-density, or in-situ layers. Optional
ingestion paths in the source should not be presented as available products.

## Enrichment and cleaning

The builder cleans scalar arrays after common-grid resampling. It removes
configured sentinels and out-of-range values, screens isolated spikes where
configured, and optionally fills missing cells from at least three finite
cardinal neighbours. The default is two filling passes, not a measured limit
of one- or two-cell holes. Removed cells can be filled again. Complex
interferograms bypass this builder cleaner.

Enrichment subsequently applies heuristic validity rules, including:

- Snow depths in `[-0.25, 0)` m become zero; values below that interval or
  above 15 m become missing.
- Product-specific plausible ranges and small-component removal filter LiDAR
  arrays. Components use four-neighbour connectivity and a 100-cell threshold;
  a sole component is retained even if it is smaller than that threshold.
- Swath validity is inferred from available radar amplitude/coherence arrays;
  finite/nonzero tests are combined across available inputs and the mask is
  applied to layers with matching shape. Finite exact-zero unwrapped phase is
  then masked where this swath step was applied. True zero phase can also be
  removed by that convention. Group swath counts describe mask area, including
  already missing cells, rather than newly removed values in each layer.
- Coherence masks record a thresholded condition. They do not establish phase
  accuracy or guarantee a usable snow-depth retrieval.

These rules can remove measurements as well as artifacts. They are processing
choices that must be reviewed for the intended analysis. Floating arrays use
NaN for missing data; coherence masks use 255 as a missing-value code. Read the
array's metadata before interpreting zeros or calculating statistics.

**Source correction, 2026-09-13 (SNEX-005).** New writes record processing stages,
settings, current-pass event counts and preserved input metadata in
`processing_history`. Recleaning preserves earlier passes and refreshes stored
array statistics. Enrichment computes its own min/max/median and does not copy
input percentiles into output statistics. Empty and complex outputs carry an
explicit statistics status without a scalar distribution. Historical missing
counts/settings remain unrecorded; these records do not provide pixel lineage.

Existing production metadata is unchanged pending verified backups. All eight
sites have contradictory preservation notes. Historical recleaning may have
left stale distribution statistics, and 19 empty enriched phase rasters retain
old numeric distributions. Do not assume an old `value_*` field describes the
currently stored array. See the [evaluation and provenance limits](#evaluation-and-provenance).
The full explorer rebuild remains on hold.

## Derived layers requiring qualification

**Aspect (SNEX-001/002/003, corrected source).** Horn derivatives use projected
east and north coordinates. Downhill bearing is
`degrees(atan2(-dzdx, -dzdy)) mod 360`, clockwise from grid north; nearly flat
cells (`hypot(dzdx, dzdy) < 1e-9`) and missing centers have no aspect. This
corrects the historical north/south reflection. The exporter averages finite
unit directions within each retained block; a mean resultant of at most
`1e-12` has no defined direction. Renderer interpolation also uses unit vectors
for aspect in degrees and wrapped phase in radians, with its separate
`1e-6 * valid_weight` cancellation tolerance. These are numerical tolerances,
not terrain-dispersion filters. Slope and unwrapped phase stay scalar.

The old archived aspect and previously exported arithmetic block means remain
historical until regeneration. A corrected renderer cannot recover directions
already lost in those means. Verify the regenerated derivatives and downstream
bearing convention before directional analysis; source regression tests alone
do not verify the installed products.

**Canopy fraction.** This is a fraction of valid neighbourhood cells at or above
the 2 m vegetation-height threshold, not an independently classified land-cover
map. The centered window contains 11 × 11 cells, or 33 × 33 m at 3 m spacing,
because both radius endpoints are included. The nominal request remains 30 m.
Raster edges truncate the window without padding; the denominator counts only
finite vegetation-height cells from the declared input stage in the available
window. A missing center can receive a fraction; no finite cells in the window
gives NaN.

**Statement correction, 2026-09-13 (SNEX-006).** The user approved retaining this
calculation. New source metadata describes the actual support and records
`window_effective_m=33` and `window_cells=11` at 3 m spacing. The legacy
`window_m=30` field is explicitly nominal. Existing archive/page metadata awaits
the coordinated metadata repair and export; no canopy values were recalculated
for this correction.

**Derivative inputs (SNEX-007, source corrected 2026-09-13).** New enrichment
outputs calculate slope, aspect, canopy fraction and both incidence layers from
the cleaned DEM/vegetation-height arrays stored in that output archive.
Projection-DEM diagnostics also compare against the cleaned DEM. Each derivative
records its input path, `derived_from_archive=self`,
`derived_from_stage=enriched_base_after_cleaning` and `derivation_version=1.0`.
The enrichment version advances to `3.1.0` because derived values can change.
Existing production archives/explorers retain the earlier pre-enrichment input
policy until the coordinated regeneration; a notes refresh cannot change that.

The cleaning thresholds and derivative formulas are retained. Horn derivatives
propagate missing neighbours; canopy ratios use finite neighbours and can exist
at a missing center. Incidence calculations still substitute the finite DEM mean
for missing neighbours in normal stencils and mask missing centers in their
outputs. Thus derivative validity masks need not equal the base mask. Empty
cleaned inputs produce missing derivatives; an empty projection comparison is
explicitly recorded as having no valid overlap. Aspect direction is corrected
in source under SNEX-001, with artifact regeneration pending. Physical geometry
validation under SNEX-008 remains unresolved.

The user accepts current enrichment cleaning for now. Improvements to its
thresholds, footprint screens and interpolation policy are deferred for a later
review; SNEX-007 changes the input stage, not those rules.

**Radar incidence (SNEX-008, corrected source).** The implementation approximates
a straight flight track through the annotation peg. It projects endpoints
100 m forward/backward along the WGS84 geodesic heading to obtain the local
grid bearing. For each ground-cell center it places the platform at the nearest
point on that projected track, at the annotation's constant average altitude.
Only the declared Left or Right side is retained; wrong-side cells and cells
within `1e-7 m` of the track are missing. This is a side restriction, not a
measured beam footprint or terrain-occlusion test.

Flat incidence is the angle of the ground-to-platform vector from vertical.
Local incidence is its angle from the upward DEM surface normal. Both use the
same ground elevation and assumed platform position; flat incidence omits the
surface tilt. The implementation still lacks time-resolved navigation, squint,
full track curvature, terrain occlusion and vertical-reference reconciliation.
The archived earlier geometry predates the look-side and heading corrections.

Keep these products labeled approximate geometry. Six QSI terrain references
are NAVD88/GEOID12b, Grand Mesa's DTM is WGS84 ellipsoidal, and Reynolds Creek's
exact terrain reference remains unresolved. The cached aircraft field says
GPS altitude without an explicit height reference. No vertical conversion was
applied, and neither a horizontal EPSG code nor the radar projection DEM datum
proves aircraft/terrain height compatibility. The local vertical-reference audit
is retained at `docs/product_trace/vertical_reference_audit.md` and is excluded
from the source repository.

The independent geometry comparison (local evidence at
`docs/product_trace/geometry_model_comparison.md`, excluded from the source repository)
used 603 sparse synthetic positions on flat 2,000 m terrain, retaining 564 under
the common side test. Its model agreement does not validate actual navigation,
terrain, per-pixel incidence or physical accuracy. Preserve these qualifications
in comparisons and exported results as well as product notes.

**Incidence summary (SNEX-008, source corrected 2026-09-13).** The recorded
condition is incidence >= 90 degrees, not radar shadow. Its percentage divides
the number of finite angles meeting that threshold by all finite angle cells.
NaN and infinities do not enter either count; no finite angles gives an
unavailable fraction, not zero percent. The denominator uses the native angle
grid and is not restricted to the radar swath. No geometry values or masks are
changed by this summary correction.

New outputs use `incidence_ge_90_fraction` with explicit numerator, denominator,
status and note. Historical `radar_shadow_fraction` values use the old whole-grid
denominator and must not be relabeled as the corrected fraction. Source notes
distinguish the two and handle missing metrics explicitly. Production metadata
repair and page updates remain queued; the geometry review stays open.

## Browser values and statistics

Browser exports are sampled, block-averaged, and quantized display products.
The archive grid, colour grid, terrain mesh, and entrance preview have separate
sampling scales. The current site meshes range from 12 m to 72 m spacing; an
entrance preview is coarser still and uses 2× vertical exaggeration.

Ordinary scalar blocks average the finite source cells; one finite cell can
supply the block. Missing-only blocks remain missing, and incomplete bottom/
right blocks are cropped. A visible display block is therefore not evidence
that its entire area was sampled. Read each layer's actual `cell_m`; radar
colour grids are coarser than the terrain mesh. Terrain heights are packed
separately at 16 bits; ordinary colour layers use 8 bits with a missing code.
Quantized probes are approximate readouts, not the original 3 m cells.

Amplitude/magnitude packing uses a sampled 2nd–98th percentile range. Values
clipped during export cannot be recovered by editing a palette. Palette
percentiles describe colour stretches, not the fraction of observations
retained. Use HDF5 arrays for quantitative analysis.

The revised Info card separates two finite-cell counts (SNEX-024):

- **Valid stored:** finite cells divided by all cells in the stored archive grid,
  at the archive resolution (normally 3 m). These include processed and derived
  values; finite does not establish measurement quality.
- **Valid shown:** finite selected-layer samples with finite terrain support,
  divided by all positions on the current rendering grid. Its dimensions and
  spacing match Shown. Counts refresh when the selected layer or rendering
  detail changes, including automatic detail changes while zooming. A numeric
  zero counts as valid, including a mask value classified as zero.

At rendering level L, the viewer samples rows and columns at stride 2^L on
its terrain grid. The denominator is ceil(W / 2^L) × ceil(H / 2^L), and spacing
is the base terrain spacing × 2^L. Layer values are first mapped to the terrain
grid by the existing coordinate mapping. The numerator counts sampled positions
where both the mapped layer value and terrain elevation are finite. This measures
support on that whole sampling grid; it does not count screen pixels, restrict to
the camera viewport, or count only vertices referenced by rendered triangles.
It does not imply a new scientific resampling or retrieval.

Details retains **Valid display** for the exported layer grid, before terrain
mapping and render-detail sampling, at that layer's `cell_m`. New exports retain
`display_valid` / `display_total`; older pages count their decoded packed array.
A scalar block with one finite input and three missing inputs can produce one
finite display cell: its stored fraction is 25% and display fraction is 100%,
without adding measurements. Circular means can instead become undefined on
cancellation. Whole-block cropping also changes the denominator.

Temporary comparisons have no stored archive dataset, so their Info card shows
only Valid shown, using their mapped result at the current detail level. For a
difference the result requires shared finite A/B values; A and B views retain
their own result support. Details reports the exported result-grid count. The
separate comparison summary continues to report overlap used by the analysis.
Palette limits and mask cutoff do not change finite support. Zooming may change
Valid shown through automatic rendering detail; panning and clipping at the
same detail level do not. Stored and exported counts remain fixed.

The old DEM-relative percentage was a ratio of counts, not a spatial intersection
test; it is removed from the revised card. Installed pages remain unchanged until
an approved UI rollout. Missing elevation samples remain gaps rather than zero
elevation.

## Evaluation and provenance

Define spatial and temporal train/test separation before making tiles or
pooling sites. Shared flight lines, overlapping ground, neighbouring tiles,
and repeat observations can leak information across splits. Distinguish a
within-site temporal experiment from a claim of generalization to unseen sites.

Current exports record `product_version = 0.1.0` and
`enrichment_version = 3.0.0`. The corrected producer writes `3.1.0` for new
cleaned-input derivatives; this does not rewrite historical versions. The
v1.0.05 release label denotes the fifth
enrichment cycle and does not rewrite those historical fields. Future archive
builds should capture the source commit, source product versions, parameters,
and environment alongside their data manifests.

Archive checks, transfer hashes, synthetic tests, browser checks, and scientific
validation answer different questions. A hash establishes byte integrity;
an invariant check establishes a specific processing condition. Neither
establishes retrieval accuracy, measurement uncertainty, or scientific fitness
for every downstream use.
