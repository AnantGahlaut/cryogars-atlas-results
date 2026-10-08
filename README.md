<p align="center">
  <img src="assets/cryogars-logo-white.png" alt="CryoGARS" width="300">
</p>

# SnowEx Field Atlas

**Explore aligned LiDAR and UAVSAR observations, compare snow products, and
inspect the data behind them.**

Built by **Anant Gahlaut** at [CryoGARS](https://github.com/cryogars),
Cryosphere, Geophysics and Remote Sensing research lab, Boise State University.

**Research preview · 8 field sites · 1,664 display layers**

Release **v1.0.05** · enrichment 3.3.0 · MIT licensed.

[Viewer](#viewer) · [Get the archive](#get-the-archive) ·
[Status](#status)

## Viewer

### [Open the 3D Atlas →](https://anantgahlaut.github.io/cryogars-atlas-viewer/)

Choose a site and open its explorer. No sign-in, Python installation, or HDF5
download is required. Use a modern WebGL-capable browser; desktop viewing is
recommended.

![Banner Summit's LiDAR-derived terrain in the live 3D explorer](assets/screenshots/banner-summit-terrain.jpg)

*Banner Summit elevation, displayed on the existing 3D terrain mesh. The data
tree, colour legend, and comparison controls remain available beside the map.*

### What it does

- **Explore the observations in 3D.** View terrain, LiDAR snow depth and vegetation
  height, UAVSAR amplitude, coherence, phase, and available derived products.
- **Find the right layer.** Search the archive hierarchy, switch sites, and use
  the acquisition timeline to inspect survey dates and radar pairs.
- **Inspect context and methods.** Open product information and notes for units,
  grid spacing, data availability, source information, equations, and limitations.
- **Control the display.** Blend overlays, adjust terrain detail and lighting,
  choose palettes and colour stretches, and save custom palette presets by
  product type. Choose Smooth interpolation or Cells averaging at the selected
  Detail spacing, with one colour per displayed block.
- **Compare two products.** Select another same-product layer or temporarily
  import a numeric GeoTIFF. Switch between reference **A**, comparison **B**,
  and **B − A** on the existing 3D terrain.
- **Export a 2D figure.** Choose comparison colours and percentile limits, then
  export a north-up PNG with its legend, source labels, comparison direction,
  coordinate system, and analysis-grid resolution.

Imported GeoTIFFs and comparison results stay in the visitor's browser. They
are not uploaded or added to the permanent archive.

![Snow-depth difference displayed in 3D with A, B, difference, colour, and PNG export controls](assets/screenshots/snow-depth-comparison.jpg)

*An actual browser comparison at Banner Summit: B = 2021-03-15 snow depth,
A = 2020-02-18 snow depth. The screenshot uses a robust 2nd–98th percentile
colour stretch with symmetric difference limits. This illustrates differences
between two surveys, not model accuracy.*

### How it works

1. **Prepare a common reference grid.** The pipeline reprojects and resamples
   accepted rasters onto each site's 3 m DEM reference grid and stores the
   aligned layers, coordinates, dates, and metadata in HDF5. The same row and
   column represent the same mapped location across aligned raster layers.
2. **Create a browser-sized export.** The exporter samples and quantizes display
   arrays, then embeds them with the application in a self-contained HTML page.
   The browser does not read or download the full HDF5 archive.
3. **Render and compare locally.** WebGL draws a DEM-based terrain mesh and
   colours it with the selected product. Automatic terrain detail adapts the
   mesh to the view; it does not increase the underlying data resolution.
   Comparisons align B to A's exported grid and use their shared valid cells.

The **archive grid, exported colour grid, and displayed terrain mesh are
different resolutions**. A 3 m common grid does not create native 3 m radar
information or guarantee common dates, vertical datums, or measurement accuracy.
Current website colour values are 8-bit quantized, and some products have clipped
tails. Comparison PNGs use the exported analysis grid, not the original 3 m
archive or a perspective screenshot. Colour stretches change appearance, not
the comparison's underlying samples or statistics.

Use the full archive for quantitative modeling. Read the
[comparison guide and limitations](docs/BROWSER_COMPARISON.md) and
[scientific notes](docs/scientific-notes.md) before interpreting differences.

The eight explorer pages total approximately **114 MiB**; larger sites can take
longer to load. Application code and display data are embedded. Optional Google
Fonts have local fallbacks.

<details>
<summary>Field sites and current viewer coverage</summary>

| Site | State | Snow-depth dates | Vegetation-height dates | Radar groups | Display layers |
| --- | --- | ---: | ---: | ---: | ---: |
| Grand Mesa | Colorado | 6 | 2 | 27 | 446 |
| Banner Summit | Idaho | 2 | 2 | 11 | 329 |
| Mores Creek | Idaho | 2 | 2 | 10 | 305 |
| Little Cottonwood Canyon | Utah | 1 | 1 | 6 | 177 |
| Fraser | Colorado | 2 | 2 | 5 | 159 |
| Cameron Pass | Colorado | 1 | 1 | 5 | 149 |
| Dry Creek | Idaho | 1 | 1 | 2 | 64 |
| Reynolds Creek | Idaho | 0 | 1 | 1 | 35 |
| **Total** | **3 states** | **15** | **12** | **67** | **1,664** |

These counts describe the published viewer snapshot, not incoming data awaiting
integration. Dates are counted within each site. Radar groups are
site/date-pair/flight-line combinations; shared flights can appear at multiple
sites. Display layers include radar channels and derived products, not just
independent observations. Reynolds Creek currently supplies terrain,
vegetation, and radar context, but no LiDAR snow-depth labels in this snapshot.

</details>

## Get the archive

The full-resolution analysis archive is separate from the browser viewer and
this source repository. It contains per-site HDF5 files with aligned LiDAR and
UAVSAR products, a common 3 m grid, temporal match tables, and processing metadata.

Rather than host about 290 GB, the repository rebuilds the archive from the
original NASA products into any folder you choose:

```bash
conda env create -f environment.yml && conda activate snowex-atlas
python get_dataset.py --dest /path/to/snowex --plan      # sizes only
python get_dataset.py --dest /path/to/snowex             # all eight sites
python get_dataset.py --dest /path/to/snowex --sites cameron_pass
```

It needs a free [NASA Earthdata login](https://urs.earthdata.nasa.gov/) in
`~/.netrc` (Windows: `~/_netrc`). It downloads the exact granules recorded in the
v1.0.05 archive (`snowex_inventory.json`), builds `<site>.h5`, enriches it to
`<site>.enriched.h5`, re-verifies the enrichment and writes SHA-256 manifests.
Interrupted runs resume. Rebuilt files match the published archive dataset by
dataset; they are not byte-identical, because HDF5 records creation times and
software versions.

Grand Mesa alone is about 220 GB; `--plan` reports the space each site needs.
The CryoGARS lab's verified copy is on Boise State's Borah cluster at
`/bsushare/hpmarshall-shared/SNOWEX/LIDAR`, with its manifests.

### What is inside

- **LiDAR:** terrain elevation, dated snow-depth and vegetation-height layers.
- **UAVSAR:** available polarizations, amplitudes, coherence, interferograms,
  unwrapped phase, and approximate geometry.
- **Derived layers:** slope, aspect, vegetation-height-based canopy fractions,
  and coherence masks, with product-specific qualifications.
- **Context:** CRS and affine transforms, source identifiers, survey/date-pair
  information, radar–LiDAR matches, and processing metadata.

Product availability varies by site. Base `<site>.h5` files are already aligned
and processed; `<site>.enriched.h5` files contain the enrichment stage. Neither
should be described as untouched sensor data.

<details>
<summary>HDF5 layout and a small Python example</summary>

```text
identification/                       site, grid, and processing metadata
matches/                              LiDAR–radar matching records
science/
  LIDAR/
    DEM/grids/elevation
    SD/<survey_date>/snow_depth
    VH/<survey_date>/veg_height
    DERIVED/
  UAVSAR/
    <date1>_<date2>/<flight_line>/
      <polarization>/
      GEOMETRY/
```

Read a small window without loading an entire site:

```python
import h5py

with h5py.File("path/to/archive/banner_summit.enriched.h5", "r") as archive:
    info = archive["identification"].attrs
    print("CRS:", info["common_crs_epsg"])
    print("Grid:", info["common_grid_shape"])

    surveys = archive["science/LIDAR/SD"]
    survey_date = sorted(surveys.keys())[0]
    depth = surveys[survey_date]["snow_depth"]
    window = depth[:256, :256]
    print(survey_date, window.shape, dict(depth.attrs))
```

Use the recorded transform and CRS to locate pixels. Preserve missing-value
masks, and keep readers closed while an archive writer is active. See the
[workflow guide](docs/workflows.md) for acquisition, generation, verification,
and building a viewer from an existing archive.

</details>

## Status

**v1.0.05, released 2026-10-08.** All eight sites were re-enriched to version
3.3.0 on Borah from the unchanged, checksum-verified base archives: aspect now
points downhill (it was reflected north–south), terrain and canopy derivatives
use the cleaned inputs, radar viewing geometry uses the projected flight
heading, and every derived dataset carries its grid and lineage metadata. The
explorers were exported from the corrected archives. Only aspect, the two
incidence layers and a few canopy/slope edge cells changed; snow depth,
vegetation height, elevation and all radar channels are unchanged. See the
[rebuild record](docs/reviews/2026-10-05-archive-rebuild-3.3.0.md).

### Known limitations

| Area | Limitation |
| --- | --- |
| Labels | Only ten independent snow-on LiDAR dates archive-wide; Reynolds Creek has none. Label uncertainty is not established. |
| Radar geometry | Incidence is approximate: straight-track, constant-altitude platform; navigation and height compatibility unverified; no terrain occlusion. |
| Vertical reference | Six sites NAVD88/GEOID12b, Grand Mesa WGS84 ellipsoidal, Reynolds Creek unresolved; no conversion is applied. |
| Grand Mesa SWE/density | Two measured IOP rasters specified by the builder are not in this release. |
| Rebuilding | `get_dataset.py` depends on ASF's original UAVSAR URLs, which still redirect after ASF's 2026 reorganisation; fresh catalogue discovery needs a builder update. |
| Incoming ASO data | Deferred; georeferencing, date, duplicate and SWE-lineage issues must be resolved before ingestion. |

The full list, with the reasoning behind each, is in the
[design document](docs/design.md#accepted-limitations-for-v1005).

### Longer-term research

- Snow-depth modeling evaluated across held-out sites and later years.
- Density estimation and an eventual SWE product, with independent evaluation
  and explicit uncertainty and provenance.
- NISAR ingestion and visualization at substantially larger scales.

These are future research directions, not capabilities claimed by this release.
Browser comparison of a user-supplied prediction is already supported; a
validated retrieval model is not included.

### Documentation and attribution

[Documentation index](docs/README.md) ·
[Design document](docs/design.md) ·
[Comparison guide](docs/BROWSER_COMPARISON.md) ·
[Scientific notes](docs/scientific-notes.md) ·
[Data sources](docs/data-sources.md) ·
[Workflow guide](docs/workflows.md) ·
[Release notes](CHANGELOG.md) ·
[Release checklist](docs/release-checklist.md)

Observations are provided by the NASA SnowEx community, NASA/JPL UAVSAR, and
the NSIDC, ASF, and ORNL archives. Cite the original datasets and identify the
site, dates, subset, and processing version used in your analysis.

**Code license: [MIT](LICENSE).** Cite this software with
[CITATION.cff](CITATION.cff). Source datasets retain their own attribution and use
requirements. Developed in the CryoGARS lab with HP Marshall. The development repository,
[viewer distribution](https://github.com/AnantGahlaut/cryogars-atlas-viewer) and
[hosted Atlas](https://anantgahlaut.github.io/cryogars-atlas-viewer/) are public.
