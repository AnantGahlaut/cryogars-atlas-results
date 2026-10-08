#!/bin/bash
#SBATCH -J snowex-replace
#SBATCH -p short
#SBATCH -c 2
#SBATCH --mem=8G
#SBATCH -t 2-00:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Swap verified 3.3.0 enriched candidates into the archive, then re-manifest.
#
# For each site: copy the candidate beside its target as *.h5.incoming,
# require the copy's SHA-256 to equal the candidate's, then rename it over
# <site>.enriched.h5 (same filesystem, so the rename is atomic). Base <site>.h5
# files are never written. Any mismatch stops the job before that site's swap.
# The previous manifests are kept under their creation date.
set -euo pipefail
SRC=/bsuscratch/anantgahlaut/snowex-rebuild-3.3.0
DST=/bsushare/hpmarshall-shared/SNOWEX/LIDAR
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-atlas/bin/python
SITES="cameron_pass dry_creek reynolds_creek little_cottonwood fraser mores_creek banner_summit grand_mesa"
echo "node=$(hostname) start=$(date -Is)"

for f in MANIFEST.json MANIFEST.sha256; do
  kept="$DST/${f%.*}_2026-08-31.${f##*.}"
  [ -e "$kept" ] || cp -p "$DST/$f" "$kept"
  cmp "$DST/$f" "$kept"
done
echo "previous manifests kept as MANIFEST_2026-08-31.*"

for s in $SITES; do
  cand="$SRC/$s.enriched.h5"; target="$DST/$s.enriched.h5"; tmp="$target.incoming"
  [ -f "$cand" ] && [ ! -L "$cand" ] && [ -f "$target" ]
  want=$(sha256sum "$cand" | cut -d' ' -f1)
  cp "$cand" "$tmp"
  got=$(sha256sum "$tmp" | cut -d' ' -f1)
  if [ "$want" != "$got" ]; then
    echo "MISMATCH $s: candidate $want copy $got"; rm -f "$tmp"; exit 4
  fi
  old=$(stat -c '%s' "$target")
  mv -f "$tmp" "$target"
  echo "$s: replaced ($old -> $(stat -c '%s' "$target") bytes) sha256 $got  $(date -Is)"
done

echo "== new manifest"
cd /bsuhome/anantgahlaut/snowex-atlas
PYTHONDONTWRITEBYTECODE=1 HDF5_USE_FILE_LOCKING=FALSE "$PY" -B manifest.py --out-dir "$DST"
echo "== sha256sum -c"
(cd "$DST" && sha256sum -c MANIFEST.sha256)
ls -la "$DST"
echo "end=$(date -Is)"
