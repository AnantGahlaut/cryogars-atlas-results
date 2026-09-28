#!/bin/bash
#SBATCH -J snowex-anncov
#SBATCH -p short
#SBATCH -c 1
#SBATCH --mem=8G
#SBATCH -t 02:00:00
#SBATCH -o /bsuhome/anantgahlaut/snowex-atlas/logs/%x-%j.log
# Read-only: opens base archives with h5py mode "r"; writes only this log.
set -u
cd /bsuhome/anantgahlaut/snowex-atlas
echo "node: $(hostname)  start: $(date -Is)"
echo "== compute-node internet"
timeout 15 curl -sI https://datapool.asf.alaska.edu 2>&1 | head -1 || echo "no response"
echo "== annotation cache coverage"
~/.conda/envs/snowex-atlas/bin/python - <<'EOF'
import glob, os, re, h5py
D = "/bsushare/hpmarshall-shared/SNOWEX/LIDAR"
cache = set(os.listdir("ann_cache"))
key = lambda u: re.sub(r"[^A-Za-z0-9_.-]", "_", u.rsplit("/", 1)[-1]) + ".ann"
tot = miss = 0
for p in sorted(glob.glob(D + "/*.h5")):
    if ".enriched" in p:
        continue
    with h5py.File(p, "r") as f:
        if "science/UAVSAR" not in f:
            print(os.path.basename(p), "no UAVSAR"); continue
        root = f["science/UAVSAR"]
        missing, n = [], 0
        # <pair>/<line>/<pol>: the source_url sits on the pol (or line) group
        for pair in root:
            for line in root[pair]:
                g = root[pair][line]
                if not isinstance(g, h5py.Group):
                    continue
                urls = [g.attrs["source_url"]] if "source_url" in g.attrs else []
                for sub in g:
                    if isinstance(g[sub], h5py.Group) and "source_url" in g[sub].attrs:
                        urls.append(g[sub].attrs["source_url"])
                urls = [u.decode() if isinstance(u, bytes) else str(u) for u in urls]
                n += 1
                if not urls or not any(key(u) in cache for u in urls):
                    missing.append(f"{pair}/{line}" + ("" if urls else " (no source_url)"))
        tot += n; miss += len(missing)
        print(f"{os.path.basename(p)}: {n} groups, uncached {len(missing)} {missing}")
print("TOTAL", tot, "groups; uncached", miss)
EOF
echo "end: $(date -Is)"
