#!/bin/bash
#SBATCH -J snowex-extract
#SBATCH -p short
#SBATCH -c 2
#SBATCH --mem=64G
#SBATCH -t 12:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Block table for one site at one scale:
#   sbatch extract_blocks.sbatch <site> <cells per block side> [archive dir]
# Block size in metres = 3 x cells (1 -> 3 m, 10 -> 30 m, 33 -> 99 m).
set -euo pipefail
SITE=${1:?site}
K=${2:?cells per block side}
ARCHIVE=${3:-/bsuscratch/anantgahlaut/snowex-rebuild-3.3.0}
OUT=/bsuscratch/anantgahlaut/snowex-ml/blocks
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-ml/bin/python
export HDF5_USE_FILE_LOCKING=FALSE
echo "site=$SITE k=$K node=$(hostname) start=$(date -Is)"
"$PY" -u /bsuhome/anantgahlaut/snowex-atlas/ml/extract_blocks.py \
  "$ARCHIVE/$SITE.enriched.h5" "$OUT/$((3 * K))m/$SITE.pkl" --block "$K"
echo "end=$(date -Is)"
