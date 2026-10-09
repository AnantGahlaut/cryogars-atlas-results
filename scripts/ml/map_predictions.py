#!/usr/bin/env python3
"""Leave-one-site-out prediction maps as GeoTIFFs for the explorer's Compare tool.

    python map_predictions.py --site <site> --date YYYYMMDD --block 10 \
        --blocks-dir <training tables at this scale> --archive <site>.enriched.h5 --out <dir>

Trains exactly as train_baselines.py does on every OTHER site, then predicts
every valid block of the held-out site for one LiDAR date (no sampling cap),
averaging over the radar pairs matched to that date. Writes a GeoTIFF on the
site's own CRS and common grid, so it lines up with the explorer:

  band 1  gradient-boosting prediction (m)
  band 2  random-forest prediction (m)
  band 3  LiDAR snow depth, same block mean (m) — the truth the models never saw
  band 4  gradient-boosting error, prediction − LiDAR (m)
  band 5  gradient-boosting prediction with the site-wide bias removed (m)

Load band 1, 2 or 5 as B against the site's snow-depth layer (A) to see the
model's map and its error on the 3D terrain.
"""
from __future__ import annotations

import argparse, importlib.util, json
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load("train_baselines")
X = load("extract_blocks")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--block", type=int, required=True)
    ap.add_argument("--blocks-dir", type=Path, required=True)
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-site", type=int, default=60_000)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    k, scale = args.block, f"{3 * args.block}m"

    # 1. Train on the other sites, as in the evaluation.
    df = pd.concat([pd.read_pickle(p) for p in sorted(args.blocks_dir.glob("*.pkl"))
                    if p.stem != args.site], ignore_index=True)
    df = df[np.isfinite(df[T.TARGET])]
    feats = [c for c in df.columns if c not in T.ID_COLS + [T.TARGET]]
    train = T.per_site_sample(df, args.per_site)
    w = T.site_weights(train["site"])
    params, _ = T.tune_hgb(train[feats], train[T.TARGET], w, train["site"])
    gbm = HistGradientBoostingRegressor(max_iter=300, l2_regularization=1.0, random_state=0, **params)
    rf = T.make_rf()
    gbm.fit(train[feats], train[T.TARGET], sample_weight=w)
    rf.fit(train[feats], train[T.TARGET], sample_weight=w)
    print(f"trained on {sorted(train['site'].unique())}, {len(train):,} rows, HGB {params}", flush=True)

    # 2. Every valid block of the held-out site for this date.
    tmp = args.out / f".{args.site}_{args.date}_{scale}.pkl"
    X.main([str(args.archive), str(tmp), "--block", str(k), "--max-rows-per-scene", "0",
            "--date", args.date])
    test = pd.read_pickle(tmp)
    tmp.unlink()
    test = test[np.isfinite(test[T.TARGET])].copy()
    test["gbm"] = gbm.predict(test[feats])
    test["rf"] = rf.predict(test[feats])
    per_block = test.groupby(["row", "col"]).agg(gbm=("gbm", "mean"), rf=("rf", "mean"),
                                                 lidar=(T.TARGET, "first"), scenes=("pair", "size"))
    bias = float(per_block["gbm"].mean() - per_block["lidar"].mean())

    # 3. GeoTIFF on the site's grid at this block size.
    with h5py.File(args.archive, "r") as f:
        ident = f["identification"].attrs
        epsg = int(ident["common_crs_epsg"])
        gh, gw = (int(v) for v in ident["common_grid_shape"])
        a, b, c, d, e, ff = (float(v) for v in ident["common_grid_transform"])
    h, wd = gh // k, gw // k
    bands = np.full((5, h, wd), np.nan, dtype="float32")
    r = per_block.index.get_level_values("row").to_numpy()
    cc = per_block.index.get_level_values("col").to_numpy()
    bands[0, r, cc] = per_block["gbm"]
    bands[1, r, cc] = per_block["rf"]
    bands[2, r, cc] = per_block["lidar"]
    bands[3, r, cc] = per_block["gbm"] - per_block["lidar"]
    bands[4, r, cc] = per_block["gbm"] - bias
    names = ["gradient boosting prediction (m)", "random forest prediction (m)",
             "LiDAR snow depth, block mean (m)", "gradient boosting error, prediction - LiDAR (m)",
             "gradient boosting prediction, site bias removed (m)"]
    path = args.out / f"{args.site}_{args.date}_{scale}_loso_prediction.tif"
    profile = dict(driver="GTiff", width=wd, height=h, count=5, dtype="float32", crs=f"EPSG:{epsg}",
                   transform=Affine(a * k, b, c, d, e * k, ff), nodata=float("nan"),
                   tiled=True, blockxsize=256, blockysize=256, compress="deflate")
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(bands)
        for i, n in enumerate(names, 1):
            dst.set_band_description(i, n)
        dst.update_tags(held_out_site=args.site, lidar_date=args.date, block_m=3 * k,
                        evaluation="leave-one-site-out: models never saw this site",
                        trained_on=",".join(sorted(train["site"].unique())))

    y, p = per_block["lidar"].to_numpy(), per_block["gbm"].to_numpy()
    stats = {"site": args.site, "date": args.date, "scale": scale, "blocks": int(len(per_block)),
             "radar_scenes_per_block_mean": float(per_block["scenes"].mean()),
             "gbm_rmse": float(np.sqrt(np.mean((p - y) ** 2))), "gbm_bias": bias,
             "gbm_corr": float(np.corrcoef(y, p)[0, 1]),
             "gbm_rmse_bias_removed": float(np.sqrt(np.mean((p - bias - y) ** 2))),
             "rf_rmse": float(np.sqrt(np.mean((per_block["rf"].to_numpy() - y) ** 2))),
             "lidar_mean": float(y.mean()), "hgb_params": params, "epsg": epsg, "tif": path.name}
    (args.out / path.with_suffix(".json").name).write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
