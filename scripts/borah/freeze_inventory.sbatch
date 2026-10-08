#!/bin/bash
#SBATCH -J snowex-inventory
#SBATCH -p short
#SBATCH -c 2
#SBATCH --mem=16G
#SBATCH -t 06:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Rediscover all eight sites and compare the result with the products the
# archive actually recorded. Read-only on the archive; writes the inventory
# and a comparison report into ~/snowex-atlas.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 HDF5_USE_FILE_LOCKING=FALSE
PY=/bsuhome/anantgahlaut/.conda/envs/snowex-atlas/bin/python
cd /bsuhome/anantgahlaut/snowex-atlas
echo "node=$(hostname) start=$(date -Is)"
"$PY" -B build_hdf5.py --mode preflight --inventory snowex_inventory.json
"$PY" -B - <<'EOF'
import glob, json, os, h5py
inv = json.load(open("snowex_inventory.json"))
D = "/bsushare/hpmarshall-shared/SNOWEX/LIDAR"
name = lambda u: str(u).rsplit("/", 1)[-1]
report = {}
for site, s in sorted(inv["sites"].items()):
    want = {name(x["url"]) for x in s["lidar"]} | {name(x["url"]) for x in s["uavsar_needed"]}
    got = set()
    with h5py.File(f"{D}/{site}.h5", "r") as f:
        def visit(n, o):
            for k in ("source_url", "source_filename", "source_file"):
                if k in o.attrs:
                    v = o.attrs[k]; v = v.decode() if isinstance(v, bytes) else str(v)
                    got.add(name(v))
        f.visititems(visit)
    report[site] = {"inventory": len(want), "archive": len(got),
                    "in_archive_not_inventory": sorted(got - want),
                    "in_inventory_not_archive": sorted(want - got)}
    r = report[site]
    print(f"{site}: inventory {r['inventory']}, archive {r['archive']}, "
          f"archive-only {len(r['in_archive_not_inventory'])}, "
          f"inventory-only {len(r['in_inventory_not_archive'])}")
json.dump(report, open("logs/inventory_vs_archive.json", "w"), indent=2)
EOF
echo "end=$(date -Is)"
