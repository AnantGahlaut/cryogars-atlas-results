#!/usr/bin/env python3
"""Leave-one-site-out random forest and gradient-boosting baselines.

    python train_baselines.py <dir of <site>.pkl> <out dir> [--per-site 60000]

For every labelled site in turn: train on the others, test on it. The held-out
site is never used for fitting, tuning or early stopping.

  * training sites are weighted equally (Grand Mesa cannot dominate);
  * gradient boosting is tuned by grouped cross-validation over the TRAINING
    sites only; the random forest uses fixed, conventional settings;
  * every model is compared with predicting the training mean, the skill a
    model must beat to have learned anything that transfers;
  * feature sets: all, without elevation (a likely site proxy), without radar
    (does L-band add anything beyond terrain and canopy?);
  * for contrast, a pooled random-pixel split shows how optimistic the earlier
    evaluation style is on the same data.
"""
from __future__ import annotations

import argparse, json, time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold, train_test_split

ID_COLS = ["site", "lidar_date", "pair", "line", "row", "col"]
TARGET = "snow_depth"
TERRAIN = ["elevation", "slope", "aspect_sin", "aspect_cos", "veg_height", "canopy_fraction"]
RADAR_PREFIXES = ("HH_", "HV_", "VV_", "inc_")


def rmse(y, p):
    return float(np.sqrt(mean_squared_error(y, p)))


def site_weights(sites: pd.Series) -> np.ndarray:
    counts = sites.map(sites.value_counts())
    w = 1.0 / counts.to_numpy(dtype="f8")
    return w * len(w) / w.sum()


def per_site_sample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Up to n rows per site, reproducibly. (groupby.apply drops the key in pandas 3.)"""
    return pd.concat([g.sample(n=min(len(g), n), random_state=0)
                      for _, g in df.groupby("site", sort=True)])


def make_rf():
    return RandomForestRegressor(n_estimators=300, min_samples_leaf=10, max_features=0.33,
                                 n_jobs=-1, random_state=0)


def tune_hgb(X, y, w, groups):
    """Pick HGB settings by leave-sites-out CV inside the training sites."""
    grid = [dict(learning_rate=lr, max_leaf_nodes=leaves)
            for lr in (0.05, 0.1) for leaves in (15, 31)]
    cv = GroupKFold(n_splits=min(3, groups.nunique()))
    scores = []
    for params in grid:
        errs = []
        for tr, va in cv.split(X, y, groups):
            m = HistGradientBoostingRegressor(max_iter=300, l2_regularization=1.0,
                                              random_state=0, **params)
            m.fit(X.iloc[tr], y.iloc[tr], sample_weight=w[tr])
            errs.append(rmse(y.iloc[va], m.predict(X.iloc[va])))
        scores.append(float(np.mean(errs)))
    best = grid[int(np.argmin(scores))]
    return best, dict(zip([json.dumps(g) for g in grid], scores))


def metrics(y, p, base):
    return {"n": int(len(y)), "rmse": rmse(y, p), "mae": float(mean_absolute_error(y, p)),
            "bias": float(np.mean(p - y)), "r2": float(r2_score(y, p)),
            "rmse_mean_baseline": rmse(y, np.full(len(y), base)),
            "skill_vs_mean": 1 - rmse(y, p) / rmse(y, np.full(len(y), base))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--per-site", type=int, default=60_000, help="training rows sampled per site")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)

    df = pd.concat([pd.read_pickle(p) for p in sorted(args.data.glob("*.pkl"))], ignore_index=True)
    df = df[np.isfinite(df[TARGET])]
    feats_all = [c for c in df.columns if c not in ID_COLS + [TARGET]]
    sets = {"all": feats_all,
            "no_elevation": [c for c in feats_all if c != "elevation"],
            "no_radar": [c for c in feats_all if not c.startswith(RADAR_PREFIXES)
                         and c not in ("pair_days", "gap_days")]}
    sites = sorted(df["site"].unique())
    print(f"{len(df):,} rows, {len(sites)} sites, {len(feats_all)} features", flush=True)
    print(df.groupby("site").size().to_string(), flush=True)

    results, importances, preds = [], [], []
    for test_site in sites:
        t0 = time.time()
        test = df[df["site"] == test_site]
        train = per_site_sample(df[df["site"] != test_site], args.per_site)
        w = site_weights(train["site"])
        base = float(np.average(train[TARGET], weights=w))
        hgb_params = None
        out_pred = test[ID_COLS + [TARGET]].copy()
        for set_name, feats in sets.items():
            Xtr, ytr, Xte, yte = train[feats], train[TARGET], test[feats], test[TARGET]
            if hgb_params is None:  # tuned once per fold on the full feature set
                hgb_params, cv_scores = tune_hgb(Xtr, ytr, w, train["site"])
            for model_name, model in (("random_forest", make_rf()),
                                      ("gradient_boosting", HistGradientBoostingRegressor(
                                          max_iter=300, l2_regularization=1.0, random_state=0, **hgb_params))):
                model.fit(Xtr, ytr, sample_weight=w)
                p = model.predict(Xte)
                row = {"test_site": test_site, "model": model_name, "features": set_name,
                       **metrics(yte.to_numpy(), p, base)}
                if model_name == "gradient_boosting":
                    row["hgb_params"] = json.dumps(hgb_params)
                results.append(row)
                if set_name == "all":
                    out_pred[f"pred_{model_name}"] = p.astype("f4")
                    sub = test.sample(n=min(len(test), 20_000), random_state=0)
                    pi = permutation_importance(model, sub[feats], sub[TARGET], n_repeats=3,
                                                random_state=0, n_jobs=-1,
                                                scoring="neg_root_mean_squared_error")
                    importances += [{"test_site": test_site, "model": model_name, "feature": f,
                                     "rmse_increase": float(m)} for f, m in zip(feats, pi.importances_mean)]
                print(f"  {test_site:18} {model_name:17} {set_name:12} "
                      f"RMSE {row['rmse']:.3f}  R2 {row['r2']:+.3f}  skill {row['skill_vs_mean']:+.3f}", flush=True)
        preds.append(out_pred)
        print(f"{test_site} done in {time.time() - t0:.0f}s", flush=True)

    # The earlier evaluation style, on the same rows, for contrast.
    pooled = per_site_sample(df, args.per_site)
    tr, te = train_test_split(pooled, test_size=0.2, random_state=42)
    rf = make_rf().fit(tr[feats_all], tr[TARGET])
    contrast = metrics(te[TARGET].to_numpy(), rf.predict(te[feats_all]), float(tr[TARGET].mean()))
    contrast.update(test_site="random_pixel_split_all_sites", model="random_forest", features="all")
    results.append(contrast)
    print(f"random pixel split (old style): RMSE {contrast['rmse']:.3f} R2 {contrast['r2']:+.3f}")

    res = pd.DataFrame(results)
    res.to_csv(args.out / "results.csv", index=False)
    pd.DataFrame(importances).to_csv(args.out / "permutation_importance.csv", index=False)
    pd.concat(preds).to_pickle(args.out / "loso_predictions.pkl")
    summary = (res[res["test_site"] != "random_pixel_split_all_sites"]
               .groupby(["model", "features"])[["rmse", "mae", "bias", "r2", "skill_vs_mean"]].mean())
    print("\nMean over held-out sites:\n" + summary.round(3).to_string())
    summary.to_csv(args.out / "summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
