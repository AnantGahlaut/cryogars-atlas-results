#!/bin/bash
#SBATCH -J snowex-export
#SBATCH -p short
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH -t 1-00:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# sbatch export_sites.sbatch site [site ...]   |   sbatch export_sites.sbatch --index
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 HDF5_USE_FILE_LOCKING=FALSE
STAGE=/bsuscratch/anantgahlaut/snowex-rebuild-3.3.0
echo "node=$(hostname) start=$(date -Is) args=$*"
cd /bsuhome/anantgahlaut/snowex-atlas
/usr/bin/time -v ~/.conda/envs/snowex-atlas/bin/python -B jobs/export_sites.py "$STAGE" "$STAGE/viewer" "$@" 2>&1 \
  | grep -vE "^\s+(Page|Volun|Invol|Swaps|File sys|Socket|Signals|Average|Exit status|Minor|Major|Percent)"
echo "end=$(date -Is)"
