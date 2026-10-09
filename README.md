<p align="center">
  <img src="assets/cryogars-logo-white.png" alt="CryoGARS" width="300">
</p>

# SnowEx Model Results

**Machine-learning runs for snow-depth retrieval from airborne L-band InSAR,
evaluated on the SnowEx Field Atlas.**

### [Open the results site →](https://anantgahlaut.github.io/cryogars-atlas-results/)

Built by **Anant Gahlaut** at [CryoGARS](https://github.com/cryogars), Boise State
University, with Ibrahim Alabi and HP Marshall.

This repository holds the model runs built on the
[SnowEx Field Atlas](https://github.com/AnantGahlaut/cryogars-atlas) (v1.0.05):
eight western U.S. sites where NASA SnowEx airborne LiDAR and UAVSAR L-band radar
are aligned on a common 3 m grid. The atlas is the data; this is what models do
with it.

## The results site

One section per run, newest first. Each section contains:

- **Training:** how the model was evaluated, the data, every input feature, the
  model settings, sampling and weighting.
- **Results across block sizes:** how skill changes from 3 m to 99 m, compared
  with the evaluation style used in earlier work.
- **Each held-out site:** error, bias and correlation per site and model.
- **Feature importance:** which inputs the models actually rely on.
- **Predictions:** for every site, a 3D explorer showing LiDAR snow depth, the
  predictions, the error (model − LiDAR) and the bias-removed prediction on the
  terrain, plus downloadable GeoTIFFs. The explorer's **Compare** tool shows any
  prediction against LiDAR directly.

Every prediction map comes from a model that **never saw that site**.

## Runs

### Random Forest Baseline — Oct 8, 2026

Random forest, with gradient boosting for comparison, predicting LiDAR snow depth
from UAVSAR (amplitude, coherence, phase per polarization), incidence angle,
terrain and canopy. Leave-one-site-out over the seven labelled sites, at block
sizes from 3 to 99 m.

| | 3 m | 30 m | 99 m |
| --- | ---: | ---: | ---: |
| Random pixel split (earlier evaluation style), R² | 0.54 | 0.71 | 0.77 |
| Unseen sites, mean R² | −0.11 | −0.30 | −0.51 |
| Unseen sites, median R² | 0.12 | 0.09 | 0.06 |

- **Random pixel splits overstate skill, and more so at coarser blocks.**
  Neighbouring blocks share information across the split; holding out whole
  sites removes that.
- **Most error is each site's overall snow amount,** with biases up to ±0.5 m.
  The spatial pattern is partly captured: correlation 0.5–0.75 at Mores Creek,
  Dry Creek and Banner Summit, about 0.1 at Grand Mesa.
- **Terrain and canopy dominate.** Radar adds little, and re-referenced
  unwrapped phase almost nothing. One radar pair measures about a week of snow
  *change*, while the LiDAR labels are absolute depth.
- **Random forest and gradient boosting are statistically tied.**

This is the reference every later model has to beat.

## How a run is produced

Everything runs on Boise State's Borah cluster from the atlas archives
(`scripts/ml/`):

1. `extract_blocks.py` turns each site's archive into a table of block-averaged
   samples (one row per block, LiDAR date and matched radar pair).
2. `train_baselines.py` trains and scores the models leave-one-site-out, with
   tuning inside the training sites only, a predict-the-mean reference and the
   random-split contrast.
3. `map_predictions.py` predicts every block of each held-out site and writes
   GeoTIFFs on the site's own grid.
4. `make_results_explorer.py` builds each site's 3D results explorer.
5. `make_results_index.py record` describes the run; `make_results_index.py
   index` rebuilds the site from every run.

The site is published from the `gh-pages` branch. Local machine and cluster paths
are removed from the pages' provenance before publication.

## Related

- [SnowEx Field Atlas](https://github.com/AnantGahlaut/cryogars-atlas): the
  aligned archive, `get_dataset.py` to rebuild it from NASA, and the public
  [3D viewer](https://anantgahlaut.github.io/cryogars-atlas-viewer/).
- [Design document](docs/design.md) and [scientific notes](docs/scientific-notes.md)
  for how the archive was built and its limitations.

Observations are provided by the NASA SnowEx community, NASA/JPL UAVSAR, and the
NSIDC, ASF and ORNL archives. Code is [MIT licensed](LICENSE); cite with
[CITATION.cff](CITATION.cff).
