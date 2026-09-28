#!/bin/bash
#SBATCH -J snowex-enrich
#SBATCH -p short
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH -t 1-00:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Enrichment 3.3.0 candidate for one site:  sbatch enrich_site.sbatch <site>
#
# The verified archive is only ever read. Outputs go to a staging directory
# holding symlinks to the base files, so enrich_hdf5.py (which deletes an
# existing destination before writing) cannot touch anything in $SRC.
set -euo pipefail
SITE=${1:?usage: sbatch enrich_site.sbatch <site>}
SRC=/bsushare/hpmarshall-shared/SNOWEX/LIDAR
STAGE=/bsuscratch/anantgahlaut/snowex-rebuild-3.3.0
CODE=/bsuhome/anantgahlaut/snowex-atlas
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-atlas/bin/python
export PYTHONDONTWRITEBYTECODE=1 HDF5_USE_FILE_LOCKING=FALSE

echo "site=$SITE node=$(hostname) start=$(date -Is)"
mkdir -p "$STAGE"
[ -e "$STAGE/$SITE.h5" ] || ln -s "$SRC/$SITE.h5" "$STAGE/$SITE.h5"
[ -L "$STAGE/$SITE.h5" ] || { echo "refusing: $STAGE/$SITE.h5 is not a symlink"; exit 3; }
sha_before=$(stat -c '%s %Y' "$SRC/$SITE.h5")

cd "$CODE"
echo "== enrich"
/usr/bin/time -v "$PY" -B enrich_hdf5.py --site "$SITE" --out-dir "$STAGE" 2>&1
echo "== verify_enriched"
"$PY" -B verify_enriched.py --out-dir "$STAGE" --site "$SITE" 2>&1 || echo "verify exit $?"

sha_after=$(stat -c '%s %Y' "$SRC/$SITE.h5")
[ "$sha_before" = "$sha_after" ] && echo "source size/mtime unchanged" || echo "WARNING: source changed"
ls -la "$STAGE"
echo "end=$(date -Is)"
