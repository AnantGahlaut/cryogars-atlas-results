#!/usr/bin/env python3
"""Turn one enriched SnowEx archive into a table of block-averaged samples.

One row = one block of ground x one LiDAR snow-depth date x one matched
UAVSAR interferometric pair (flight line). Blocks are k x k cells of the 3 m
common grid (k = 10 -> 30 m), averaged — not resampled point values.

    python extract_blocks.py <site>.enriched.h5 out.pkl --block 10

Rules that fix the earlier study's leaks:
  * a block is kept only if >= MIN_VALID of its cells have snow depth and the
    pair has radar geometry there;
  * unwrapped phase is re-referenced per scene (pair x line x polarisation) by
    subtracting that scene's median over the site, so its arbitrary offset
    cannot act as a scene or site identifier;
  * aspect enters as sin/cos of its circular block mean;
  * canopy layers come from the vegetation date closest to the snow date.
Archives are opened read-only.
"""
from __future__ import annotations

import argparse, datetime as dt, sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

MIN_VALID = 0.8
POLS = ("HH", "HV", "VV")          # VH duplicates HV for reciprocal targets
MAX_ROWS_PER_SCENE = 150_000        # caps Grand Mesa; sampled reproducibly


def text(v):
    return v.decode() if isinstance(v, bytes) else str(v)


def blocks(a: np.ndarray, k: int):
    """(sum, count) of finite values per k x k block, cropping partial edges."""
    h, w = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    a = a[:h, :w].reshape(h // k, k, w // k, k)
    finite = np.isfinite(a)
    s = np.where(finite, a, 0).sum(axis=(1, 3), dtype="f8")
    return s, finite.sum(axis=(1, 3))


def bmean(ds, k):
    s, n = blocks(ds[...].astype("f4"), k)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (s / n).astype("f4"), n / (k * k)


def bphase(ds, k):
    """Direction of the block-mean complex interferogram."""
    z = ds[...]
    h, w = (z.shape[0] // k) * k, (z.shape[1] // k) * k
    z = z[:h, :w].reshape(h // k, k, w // k, k)
    ok = np.isfinite(z.real) & np.isfinite(z.imag)
    m = np.where(ok, z, 0).sum(axis=(1, 3))
    ang = np.angle(m).astype("f4")
    ang[(ok.sum(axis=(1, 3)) == 0) | (np.abs(m) < 1e-12)] = np.nan
    return ang


def nearest(dates: list[str], target: str) -> str:
    t = dt.date.fromisoformat(f"{target[:4]}-{target[4:6]}-{target[6:]}")
    return min(dates, key=lambda d: abs((dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}") - t).days))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--block", type=int, default=10, help="cells per block side (3 m cells)")
    ap.add_argument("--max-rows-per-scene", type=int, default=MAX_ROWS_PER_SCENE,
                    help="sample cap per scene; 0 keeps every valid block (for maps)")
    ap.add_argument("--date", default=None, help="only this LiDAR date (YYYYMMDD)")
    args = ap.parse_args(argv)
    k = args.block
    site = args.archive.name.split(".")[0]
    rng = np.random.default_rng(0)
    frames = []

    with h5py.File(args.archive, "r") as f:
        S, L = f["science"], f["science/LIDAR"]
        m = f["matches"]
        rows = [{c: text(m[c][i]) for c in m} for i in range(m["verdict"].shape[0])]
        pairs = sorted({(r["lidar_date"], r["uavsar_dates"], r["group_name"].split("CUTFROM")[1].rsplit("_", 2)[0],
                         int(r["gap_days"]))
                        for r in rows if r["lidar_product_type"] == "SD" and r["verdict"] == "match"
                        and r["uavsar_level"] == "INTERFEROMETRY_GRD"})
        if args.date:
            pairs = [p for p in pairs if p[0] == args.date]
        if not pairs:
            print(f"{site}: no snow-depth/interferogram matches"); return 0

        # Static terrain, once per site.
        elev, _ = bmean(L["DEM/grids/elevation"], k)
        slope, _ = bmean(L["DERIVED/slope"], k)
        asp = L["DERIVED/aspect"][...]
        s_sin, n_a = blocks(np.sin(np.deg2rad(asp)), k)
        s_cos, _ = blocks(np.cos(np.deg2rad(asp)), k)
        with np.errstate(invalid="ignore"):
            a_sin, a_cos = s_sin / n_a, s_cos / n_a
        norm = np.hypot(a_sin, a_cos)
        with np.errstate(invalid="ignore"):
            a_sin, a_cos = (a_sin / norm).astype("f4"), (a_cos / norm).astype("f4")
        del asp
        vh_dates = sorted(L["VH"].keys())
        canopy = {}
        for d in vh_dates:
            canopy[d] = (bmean(L[f"VH/{d}/veg_height"], k)[0],
                         bmean(L[f"DERIVED/forest_cover_fraction_{d}"], k)[0])
        rr, cc = np.indices(elev.shape)

        snow = {d: bmean(L[f"SD/{d}/snow_depth"], k) for d in sorted({p[0] for p in pairs})}
        nan = np.full(elev.shape, np.nan, "f4")

        # Radar features depend only on the scene, so each scene is read once and
        # then joined to every LiDAR date it matches.
        for pair, line in sorted({(p[1], p[2]) for p in pairs}):
            base = f"UAVSAR/{pair}/{line}"
            if base not in S:
                print(f"  {site} {base}: missing in enriched archive"); continue
            g = S[base]
            grids = {}
            if "GEOMETRY/local_incidence_angle" in g:
                grids["inc_local"], inc_frac = bmean(g["GEOMETRY/local_incidence_angle"], k)
            else:
                grids["inc_local"], inc_frac = nan, np.zeros(elev.shape)
            grids["inc_flat"] = (bmean(g["GEOMETRY/incidence_angle_flat"], k)[0]
                                 if "GEOMETRY/incidence_angle_flat" in g else nan)
            for pol in POLS:
                p = g[pol] if pol in g else {}
                get = lambda leaf: bmean(p[leaf], k)[0] if leaf in p else nan
                with np.errstate(divide="ignore", invalid="ignore"):
                    a1, a2 = 20 * np.log10(get("amp1")), 20 * np.log10(get("amp2"))
                grids.update({f"{pol}_amp1_db": a1, f"{pol}_amp2_db": a2,
                              f"{pol}_amp_ratio_db": a2 - a1, f"{pol}_coherence": get("cor")})
                unw = get("unw")
                ref = np.nanmedian(unw) if np.isfinite(unw).any() else np.nan
                grids[f"{pol}_unw_rel"] = unw - ref
                ph = bphase(p["int"], k) if "int" in p else nan
                grids[f"{pol}_wrapped_cos"], grids[f"{pol}_wrapped_sin"] = np.cos(ph), np.sin(ph)
            d1, d2 = pair.split("_")
            days = (dt.date.fromisoformat(f"{d2[:4]}-{d2[4:6]}-{d2[6:]}") -
                    dt.date.fromisoformat(f"{d1[:4]}-{d1[4:6]}-{d1[6:]}")).days

            for lidar_date, _, _, gap in [q for q in pairs if q[1] == pair and q[2] == line]:
                sd, sd_frac = snow[lidar_date]
                veg, fcf = canopy[nearest(vh_dates, lidar_date)]
                keep = (sd_frac >= MIN_VALID) & (inc_frac >= MIN_VALID)
                idx = np.flatnonzero(keep.ravel())
                if idx.size == 0:
                    continue
                cap = args.max_rows_per_scene
                if cap and idx.size > cap:
                    idx = np.sort(rng.choice(idx, cap, replace=False))
                cols = {"site": site, "lidar_date": lidar_date, "pair": pair, "line": line,
                        "row": rr.ravel()[idx].astype("i4"), "col": cc.ravel()[idx].astype("i4"),
                        "snow_depth": sd.ravel()[idx],
                        "elevation": elev.ravel()[idx], "slope": slope.ravel()[idx],
                        "aspect_sin": a_sin.ravel()[idx], "aspect_cos": a_cos.ravel()[idx],
                        "veg_height": veg.ravel()[idx], "canopy_fraction": fcf.ravel()[idx],
                        "pair_days": days, "gap_days": gap}
                cols.update({name: grid.ravel()[idx] for name, grid in grids.items()})
                frames.append(pd.DataFrame(cols))
                print(f"  {site} {lidar_date} {pair} {line}: {idx.size:,} blocks", flush=True)
            del grids

    out = pd.concat(frames, ignore_index=True)
    for c in out.columns:
        if out[c].dtype == "float64":
            out[c] = out[c].astype("float32")
    out.attrs["block_m"] = 3 * k
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_pickle(args.out)
    print(f"{site}: {len(out):,} rows, {out['lidar_date'].nunique()} dates, "
          f"{out[['pair','line']].drop_duplicates().shape[0]} scenes -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
