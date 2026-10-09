#!/bin/bash
#SBATCH -J snowex-maps
#SBATCH -p short
#SBATCH -c 16
#SBATCH --mem=96G
#SBATCH -t 06:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Leave-one-site-out prediction GeoTIFF:  sbatch map_predictions.sbatch <site> <YYYYMMDD> <cells per block>
set -euo pipefail
SITE=${1:?site}; DATE=${2:?date}; K=${3:?cells per block}
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-ml/bin/python
export HDF5_USE_FILE_LOCKING=FALSE
echo "site=$SITE date=$DATE k=$K node=$(hostname) start=$(date -Is)"
"$PY" -W ignore -u /bsuhome/anantgahlaut/snowex-atlas/ml/map_predictions.py \
  --site "$SITE" --date "$DATE" --block "$K" \
  --blocks-dir "/bsuscratch/anantgahlaut/snowex-ml/blocks/$((3 * K))m" \
  --archive "/bsushare/hpmarshall-shared/SNOWEX/LIDAR/$SITE.enriched.h5" \
  --out /bsuhome/anantgahlaut/snowex-atlas/ml/maps
echo "end=$(date -Is)"
