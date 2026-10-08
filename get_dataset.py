#!/usr/bin/env python3
"""
get_dataset.py -- download the SnowEx sources and build the archive anywhere.

The archive is not hosted as a 340 GB download. This script rebuilds it from
the original NASA products instead, into a folder you choose:

    python get_dataset.py --dest /path/to/snowex                  # all 8 sites
    python get_dataset.py --dest D:/snowex --sites mores_creek    # one site
    python get_dataset.py --dest /path/to/snowex --plan           # sizes only

For each site it runs the same pipeline that produced the published archive:

  1. build_hdf5.py      download LiDAR + UAVSAR, align onto the 3 m grid
                        -> <site>.h5
  2. enrich_hdf5.py     terrain derivatives, canopy fraction, coherence masks,
                        radar geometry  -> <site>.enriched.h5
  3. verify_enriched.py re-check the enrichment invariants from the values
  4. manifest.py        SHA-256 and per-dataset fingerprints for the folder

Every step resumes: re-running after an interruption keeps finished arrays and
skips sites already built and verified.

Requirements: the conda environment in environment.yml, and a NASA Earthdata
login stored in ~/.netrc (Windows: ~/_netrc), e.g.

    machine urs.earthdata.nasa.gov login <user> password <password>

Products are taken from the frozen inventory (snowex_inventory.json) that
produced the published archive, so you get the same granules even if NASA's
catalogues change. --fresh-discovery searches the catalogues again instead.
Rebuilt files match the published ones dataset by dataset (shapes, dtypes,
valid counts); byte-for-byte identity is not expected, because HDF5 files also
record creation times and software versions.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SITES = ["cameron_pass", "dry_creek", "reynolds_creek", "little_cottonwood",
         "fraser", "mores_creek", "banner_summit", "grand_mesa"]

#: Approximate output sizes in GB (base .h5, enriched .h5), from the published
#: 3.3.0 archive. Used only to warn before starting.
SIZE_GB = {
    "banner_summit": (20.3, 18.4), "cameron_pass": (1.3, 1.2),
    "dry_creek": (1.2, 1.0), "fraser": (3.6, 3.2),
    "grand_mesa": (107.9, 114.0), "little_cottonwood": (3.3, 2.9),
    "mores_creek": (4.6, 4.1), "reynolds_creek": (1.1, 1.0),
}
#: Scratch space for the largest single download plus reprojection buffers.
WORK_GB = 25
CLOUD_SYNCED = ("onedrive", "dropbox", "google drive", "icloud")


def plan(sites, dest, work, enrich):
    need = sum(SIZE_GB[s][0] + (SIZE_GB[s][1] if enrich else 0) for s in sites)
    need += WORK_GB
    free = shutil.disk_usage(_existing(dest)).free / 1e9
    print(f"sites:        {', '.join(sites)}")
    print(f"destination:  {dest}")
    print(f"scratch:      {work}")
    print(f"needs about:  {need:,.0f} GB   (free: {free:,.0f} GB)")
    return need, free


def _existing(p: Path) -> Path:
    while not p.exists():
        p = p.parent
    return p


def run(step, fn, argv):
    print(f"\n== {step}: {' '.join(argv)}", flush=True)
    code = fn(argv)
    if code:
        raise SystemExit(f"{step} failed with exit code {code}; re-run to resume")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, required=True,
                    help="folder for the finished .h5 files")
    ap.add_argument("--sites", nargs="*", default=SITES, choices=SITES, metavar="SITE",
                    help="default: all eight (" + ", ".join(SITES) + ")")
    ap.add_argument("--work-dir", type=Path, default=None,
                    help="scratch for downloads (default: <dest>/_work)")
    ap.add_argument("--inventory", type=Path, default=HERE / "snowex_inventory.json",
                    help="frozen product inventory (default: the published one)")
    ap.add_argument("--fresh-discovery", action="store_true",
                    help="search NASA CMR/ASF again instead of the frozen inventory")
    ap.add_argument("--lidar-only", action="store_true",
                    help="skip UAVSAR (far smaller and faster)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="stop after the base <site>.h5 files")
    ap.add_argument("--plan", action="store_true",
                    help="print the sizes and exit without downloading")
    ap.add_argument("--yes", action="store_true",
                    help="do not stop when free space looks too small")
    args = ap.parse_args(argv)

    dest = args.dest.expanduser().resolve()
    work = (args.work_dir or dest / "_work").expanduser().resolve()
    sites = [s for s in SITES if s in args.sites]
    enrich = not args.no_enrich

    need, free = plan(sites, dest, work, enrich)
    if any(c in str(dest).lower() for c in CLOUD_SYNCED):
        print("warning: destination looks cloud-synced; sync clients lock large "
              "files mid-write and break HDF5. Choose a local folder.")
    if args.plan:
        return 0
    if free < need and not args.yes:
        print("not enough free space; free some or pass --yes to try anyway")
        return 2

    import build_hdf5, enrich_hdf5, verify_enriched, manifest

    inventory = args.inventory
    if args.fresh_discovery:
        inventory = work / "inventory.json"
        work.mkdir(parents=True, exist_ok=True)
        run("discover", build_hdf5.main,
            ["--mode", "preflight", "--sites", *sites, "--inventory", str(inventory)])
    elif not inventory.exists():
        print(f"missing frozen inventory {inventory}; use --fresh-discovery")
        return 2

    run("check Earthdata login", build_hdf5.main, ["--mode", "check-auth"])
    dest.mkdir(parents=True, exist_ok=True)

    for s in sites:
        build = ["--mode", "build", "--sites", s, "--inventory", str(inventory),
                 "--out-dir", str(dest), "--work-dir", str(work)]
        run(f"build {s}", build_hdf5.main, build + (["--skip-uavsar"] if args.lidar_only else []))
        if not enrich:
            continue
        done = dest / f"{s}.enriched.ok"
        if done.exists() and (dest / f"{s}.enriched.h5").exists():
            print(f"\n== enrich {s}: already built and verified, skipping")
            continue
        run(f"enrich {s}", enrich_hdf5.main, ["--site", s, "--out-dir", str(dest)])
        run(f"verify {s}", verify_enriched.main, ["--out-dir", str(dest), "--site", s])
        done.write_text("enriched and verified\n")

    run("manifest", manifest.main, ["--out-dir", str(dest)])
    print(f"\ndone: {len(sites)} site(s) in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
