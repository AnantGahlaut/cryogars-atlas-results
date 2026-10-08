#!/usr/bin/env python3
"""Rebuild the exact v1.0.05 product inventory from the archives' own records.

ASF reorganised UAVSAR in 2026: the original zip products no longer appear in
search, but their recorded URLs still resolve. This writes an inventory that
lists exactly the products each base archive ingested, so get_dataset.py
downloads the same granules instead of rediscovering different ones.

    python scripts/freeze_inventory.py --archive-dir <dir with <site>.h5> \
        --discovery snowex_inventory.json [--original inventory.json] \
        --out snowex_inventory.json

  --discovery  a fresh preflight inventory: supplies site definitions and the
               LiDAR granule records (footprints, dates), filtered here to the
               files each archive actually recorded
  --original   an older full preflight inventory whose UAVSAR records (with
               catalog MD5s and footprints) are preferred where they match
"""
from __future__ import annotations

import argparse, datetime, json, sys
from pathlib import Path

import h5py


def text(v):
    return v.decode() if isinstance(v, bytes) else str(v)


def site_records(path: Path) -> tuple[set[str], list[dict], list[dict]]:
    lidar, products, matches = set(), {}, []
    with h5py.File(path, "r") as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset) and "source_filename" in obj.attrs:
                lidar.add(text(obj.attrs["source_filename"]))
        f["science/LIDAR"].visititems(visit)
        if "science/UAVSAR" in f:
            for level, lg in f["science/UAVSAR"].items():
                for group in lg.values():
                    a = group.attrs
                    if "source_url" not in a:
                        continue
                    url = text(a["source_url"])
                    dates = [text(d) for d in a.get("acquisition_dates", [])]
                    # Groups sit under their processing level; DEM_TIFF groups
                    # do not repeat it (or their size) as attributes.
                    lvl = text(a.get("processing_level", level))
                    pid = text(a["original_product_id"])
                    fid = text(a["asf_file_id"])
                    products[fid] = {
                        "scene_name": pid, "file_id": fid,
                        "level": lvl, "url": url,
                        "filename": url.rsplit("/", 1)[-1],
                        "bytes_": int(a.get("source_bytes", 0)), "md5sum": "",
                        "date_ref": dates[0] if dates else None,
                        "date_sec": dates[-1] if dates else None,
                        "footprint_wkt": ""}
        if "matches" in f:
            m = f["matches"]
            cols = {k: [text(x) if isinstance(x, bytes) else x for x in m[k][...]] for k in m}
            for i in range(len(next(iter(cols.values()), []))):
                row = {k: (v[i].item() if hasattr(v[i], "item") else v[i]) for k, v in cols.items()}
                row = {k: (int(v) if k == "gap_days" else text(v)) for k, v in row.items()}
                row["uavsar_scene"] = row.pop("uavsar_flight_id", "")
                row["site_key"] = path.stem
                matches.append(row)
    return lidar, list(products.values()), matches


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-dir", type=Path, required=True)
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--original", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    disc = json.loads(args.discovery.read_text())
    original = {}
    if args.original and args.original.exists():
        for s in json.loads(args.original.read_text())["sites"].values():
            original.update({p["file_id"]: p for p in s.get("uavsar_needed", [])})

    out = {"version": disc.get("version"), "config": disc.get("config"),
           "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "frozen_from": "v1.0.05 base archive records", "sites": {}}
    ok = True
    for key, site in sorted(disc["sites"].items()):
        lidar_files, products, matches = site_records(args.archive_dir / f"{key}.h5")
        lidar = [g for g in site["lidar"] if g["filename"] in lidar_files]
        missing = lidar_files - {g["filename"] for g in lidar}
        if missing:
            print(f"{key}: archive LiDAR not in discovery: {sorted(missing)}", file=sys.stderr)
            ok = False
        products = [original.get(p["file_id"], p) for p in products]
        out["sites"][key] = {**{k: v for k, v in site.items()
                                if k not in ("lidar", "uavsar_needed", "matches", "summary")},
                             "lidar": lidar, "uavsar_needed": products, "matches": matches}
        print(f"{key}: lidar {len(lidar)}/{len(site['lidar'])} discovered, "
              f"uavsar {len(products)} ({sum(p['file_id'] in original for p in products)} with catalog MD5), "
              f"matches {len(matches)}")
    args.out.write_text(json.dumps(out, indent=1))
    print("wrote", args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
