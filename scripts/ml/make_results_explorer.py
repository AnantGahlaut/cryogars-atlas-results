#!/usr/bin/env python3
"""Private 'results edition' explorers: model predictions on the 3D terrain.

    python make_results_explorer.py --run <run name> --maps <dir of *_loso_prediction.tif/.json>
        --archive-dir <dir of <site>.enriched.h5> --results <dir of <scale>/results.csv>
        --work <scratch dir> --out <viewer dir> [--scale 30m]

For each site with a prediction map it writes a small results archive in the
same HDF5 layout the explorer exporter reads — the site's DEM and LiDAR snow
depth copied unchanged, plus four model layers on the 3 m grid — and renders it
with make_explorer.build_site. Methods, training setup and metrics travel as
attributes, so they appear in the explorer's information panels. Archives are
opened read-only; nothing is added to the published atlas.
"""
from __future__ import annotations

import argparse, json, shutil, sys, time
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pandas as pd
import rasterio

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from make_explorer import build_site, write_index  # noqa: E402

LAYERS = [  # (tif band, leaf, description)
    (1, "predicted_snow_depth_gbm", "Snow depth predicted by histogram gradient boosting for this "
                                    "held-out site, averaged over the radar pairs matched to the date."),
    (2, "predicted_snow_depth_rf", "Snow depth predicted by a random forest for this held-out site."),
    (4, "snow_depth_error_gbm", "Gradient-boosting prediction minus LiDAR snow depth (m). Positive = "
                                "model too deep. Compare spatial patterns with terrain and canopy."),
    (5, "predicted_snow_depth_gbm_debiased", "Gradient-boosting prediction with the site-wide mean bias "
                                             "removed: shows how well the model captures the pattern of "
                                             "deep and shallow snow, separately from the overall amount."),
]

METHODS = (
    "Leave-one-site-out evaluation: for this site, models were trained only on the other six "
    "labelled SnowEx sites and never saw this site's data, so these maps show how a model "
    "generalizes to new ground. One sample = one {scale} block (mean of 3 m cells, kept when >=80% "
    "of cells have snow depth and radar geometry) x one LiDAR date x one matched UAVSAR pair "
    "(<=5 days). 31 features: radar per polarization (HH, HV, VV: amplitudes in dB and their ratio, "
    "coherence, unwrapped phase re-referenced to each scene's median, wrapped phase as cos/sin), "
    "local and flat incidence, elevation, slope, aspect (sin/cos), vegetation height, canopy fraction, "
    "pair length and LiDAR-radar gap. Training sites weighted equally; up to 60,000 rows per site. "
    "Random forest: 300 trees, min_samples_leaf 10, max_features 0.33 (fixed). Gradient boosting: "
    "scikit-learn HistGradientBoosting, learning rate and leaf count chosen by grouped cross-"
    "validation over the training sites only. Predictions averaged over the radar pairs matched to "
    "the date. The labels are absolute depth while one radar pair measures roughly a week of change, "
    "so terrain and canopy dominate these baselines.")


def upsample(band: np.ndarray, k: int, shape: tuple[int, int]) -> np.ndarray:
    out = np.full(shape, np.nan, dtype="float32")
    up = np.repeat(np.repeat(band, k, axis=0), k, axis=1)
    out[:up.shape[0], :up.shape[1]] = up
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--maps", type=Path, required=True)
    ap.add_argument("--archive-dir", type=Path, required=True)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scale", default="30m")
    args = ap.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    k = int(args.scale.rstrip("m")) // 3

    res = pd.read_csv(args.results / args.scale / "results.csv")
    loso = res[res["test_site"] != "random_pixel_split_all_sites"]
    table = (loso[loso["features"] == "all"]
             .pivot(index="test_site", columns="model", values=["r2", "rmse", "bias"]).round(3))
    contrast = res[res["test_site"] == "random_pixel_split_all_sites"].iloc[0]
    summary_text = (f"All held-out sites at {args.scale} (R2 / RMSE m / bias m):\n" + table.to_string() +
                    f"\n\nSame data scored the old way (random pixel split, random forest): "
                    f"R2 {contrast['r2']:.3f}, RMSE {contrast['rmse']:.3f} m.")

    jobs = []
    for meta in sorted(args.maps.glob(f"*_{args.scale}_loso_prediction.json")):
        stats = json.loads(meta.read_text())
        jobs.append((stats["site"], stats, meta.with_suffix(".tif")))
    roster = [{"key": s, "name": s.replace("_", " ").title(), "file": f"{s}_results_explorer.html"}
              for s, _, _ in jobs]
    summaries = []
    for site, stats, tif in jobs:
        t0 = time.time()
        src = args.archive_dir / f"{site}.enriched.h5"
        dst = args.work / f"{site}.results.h5"
        date = stats["date"]
        with h5py.File(src, "r") as f, h5py.File(dst, "w") as g:
            ident = g.create_group("identification")
            for key, val in f["identification"].attrs.items():
                ident.attrs[key] = val
            shape = tuple(int(v) for v in f["identification"].attrs["common_grid_shape"])
            ident.attrs.update({
                "results_run": args.run, "results_scale": args.scale, "results_lidar_date": date,
                "results_methods": METHODS.format(scale=args.scale),
                "results_this_site": json.dumps({k2: v for k2, v in stats.items() if k2 != "tif"}),
                "results_all_sites": summary_text,
                "results_note": "Private research results. Not part of the public SnowEx Field Atlas.",
            })
            for path in ("science/LIDAR/DEM/grids/elevation", f"science/LIDAR/SD/{date}/snow_depth"):
                g.require_group(path.rsplit("/", 1)[0])
                f.copy(f[path], g[path.rsplit("/", 1)[0]], name=path.rsplit("/", 1)[1])
            grp = g.require_group(f"science/LIDAR/RESULTS/{args.run}/{date}")
            grp.attrs.update({"description": METHODS.format(scale=args.scale),
                              "held_out_site": site, "trained_on": "all other labelled sites",
                              "metrics": json.dumps({k2: v for k2, v in stats.items() if k2 != "tif"})})
            with rasterio.open(tif) as r:
                for band, leaf, desc in LAYERS:
                    data = upsample(r.read(band), k, shape)
                    ds = grp.create_dataset(leaf, data=data, chunks=(256, 256), compression="gzip",
                                            compression_opts=4)
                    ds.attrs.update({"description": desc, "units": "m", "nodata": "NaN",
                                     "block_m": 3 * k, "model_run": args.run,
                                     "rmse_m": stats["gbm_rmse"] if "gbm" in leaf else stats["rf_rmse"],
                                     "bias_m": stats["gbm_bias"], "correlation": stats["gbm_corr"]})
        build_args = SimpleNamespace(terrain_stride=None, stride=None, with_insitu=False,
                                     viewer_dir=args.work / "_pages", template=REPO / "explorer_template.html")
        code, summary = build_site(site, dst, build_args, roster)
        if code:
            print(f"{site}: export failed ({code})"); return code
        page = args.work / "_pages" / f"{site}_explorer.html"
        shutil.move(str(page), str(args.out / f"{site}_results_explorer.html"))  # scratch -> share: different filesystems
        summary["file"] = f"{site}_results_explorer.html"
        summaries.append(summary)
        print(f"{site}: {summary['layers']} layers in {time.time() - t0:.0f}s", flush=True)
    write_index(args.out, summaries, time.strftime("%Y-%m-%d %H:%M"))
    (args.out / "RESULTS_SUMMARY.txt").write_text(f"Run: {args.run}\n\n{METHODS.format(scale=args.scale)}\n\n{summary_text}\n")
    print("index and summary written to", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
