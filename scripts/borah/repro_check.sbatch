#!/bin/bash
#SBATCH -J snowex-repro
#SBATCH -p short
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH -t 1-00:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# End-to-end reproduction: get_dataset.py rebuilds one site from NASA into a
# scratch folder, then every dataset is compared with the published archive.
# Usage: sbatch repro_check.sbatch <site>
set -euo pipefail
SITE=${1:?site}
CODE=/bsuhome/anantgahlaut/snowex-atlas
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-atlas/bin/python
DEST=/bsuscratch/anantgahlaut/snowex-repro/$SITE
export PYTHONDONTWRITEBYTECODE=1 HDF5_USE_FILE_LOCKING=FALSE
echo "node=$(hostname) start=$(date -Is)"
cd "$CODE"
"$PY" -W ignore -B get_dataset.py --dest "$DEST" --sites "$SITE" \
  --inventory snowex_inventory_v1.0.05.json --yes
echo "== compare with published archive"
"$PY" -W ignore -B - "$SITE" "$DEST" <<'EOF'
import sys, h5py, numpy as np
site, dest = sys.argv[1], sys.argv[2]
pairs = [(f"/bsushare/hpmarshall-shared/SNOWEX/LIDAR/{site}.h5", f"{dest}/{site}.h5"),
         (f"/bsuscratch/anantgahlaut/snowex-rebuild-3.3.0/{site}.enriched.h5", f"{dest}/{site}.enriched.h5")]
for ref, new in pairs:
    def sets(p):
        out = {}
        with h5py.File(p, "r") as f:
            def v(n, o):
                if isinstance(o, h5py.Dataset) and n.startswith("science/"):
                    a = o[...]
                    fin = np.isfinite(a) if a.dtype.kind in "fc" else np.ones(a.shape, bool)
                    out[n] = (a.shape, str(a.dtype), int(fin.sum()), a[fin] if fin.any() else None)
            f.visititems(v)
        return out
    A, B = sets(ref), sets(new)
    print(f"\n{ref.rsplit('/',1)[-1]}: published {len(A)} datasets, rebuilt {len(B)}")
    only = sorted(set(A) ^ set(B))
    if only: print("  present in only one:", only[:10])
    exact = close = differ = 0
    for n in sorted(set(A) & set(B)):
        (sa, da, ca, va), (sb, db, cb, vb) = A[n], B[n]
        if (sa, da) != (sb, db) or ca != cb:
            differ += 1; print(f"  DIFF {n}: shape/dtype/valid {sa},{da},{ca} vs {sb},{db},{cb}"); continue
        if va is None or np.array_equal(va, vb): exact += 1; continue
        d = np.abs(va.astype("c16") - vb.astype("c16"))
        if d.max() <= 1e-4 * max(1.0, float(np.abs(va).max())): close += 1
        else: differ += 1; print(f"  DIFF {n}: max |delta| {d.max():.4g}")
    print(f"  identical {exact}, within 1e-4 relative {close}, different {differ}")
EOF
echo "end=$(date -Is)"
