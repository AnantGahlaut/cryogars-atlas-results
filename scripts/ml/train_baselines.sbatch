#!/bin/bash
#SBATCH -J snowex-baselines
#SBATCH -p short
#SBATCH -c 32
#SBATCH --mem=128G
#SBATCH -t 12:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Leave-one-site-out RF and gradient boosting at one scale:
#   sbatch train_baselines.sbatch <scale, e.g. 30m>
set -euo pipefail
SCALE=${1:?scale}
BLOCKS=/bsuscratch/anantgahlaut/snowex-ml/blocks
OUT=/bsuhome/anantgahlaut/snowex-atlas/ml/results
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-ml/bin/python
echo "scale=$SCALE node=$(hostname) start=$(date -Is)"
"$PY" -u /bsuhome/anantgahlaut/snowex-atlas/ml/train_baselines.py "$BLOCKS/$SCALE" "$OUT/$SCALE"
echo "end=$(date -Is)"
