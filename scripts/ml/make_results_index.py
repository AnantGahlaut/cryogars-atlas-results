#!/usr/bin/env python3
"""Private CryoGARS model-results site, organised by run.

    # 1. describe one run (once per run)
    python make_results_index.py record --run-dir <root>/<run> --results <dir of <scale>/results.csv>
        --maps <dir of *_loso_prediction.json> --title "Random Forest Baseline" --date 2026-10-08
    # 2. rebuild the main page from every <root>/*/run.json
    python make_results_index.py index --root <root>

Each run folder holds run.json, the per-site results explorers and the prediction
GeoTIFFs. The main page shows one section per run, newest first: training setup,
results across scales and sites, feature importance, and every prediction.
A run folder's own index.html redirects to its section, so the explorers' "All"
link returns there.
"""
from __future__ import annotations

import argparse, html, json
from pathlib import Path

import pandas as pd

SCALES = ["3m", "6m", "15m", "30m", "60m", "99m"]
SITE_NAMES = {"banner_summit": "Banner Summit", "cameron_pass": "Cameron Pass", "dry_creek": "Dry Creek",
              "fraser": "Fraser", "grand_mesa": "Grand Mesa", "little_cottonwood": "Little Cottonwood Canyon",
              "mores_creek": "Mores Creek", "reynolds_creek": "Reynolds Creek"}
MODEL_NAMES = {"random_forest": "Random forest", "gradient_boosting": "Gradient boosting"}

TRAINING = {
    "evaluation": "Leave-one-site-out: each of the 7 labelled sites is predicted by models trained only on "
                  "the other 6, so every number and map shows generalisation to unseen ground. Reynolds "
                  "Creek has no snow-depth labels and is not used.",
    "data": "SnowEx Field Atlas v1.0.05 (enrichment 3.3.0) on Borah. One sample = one block of 3 m cells "
            "(3, 6, 15, 30, 60 or 99 m; cell means, kept when >=80% of cells have snow depth and radar "
            "geometry) x one LiDAR snow-depth date x one UAVSAR interferometric pair within 5 days. "
            "13 LiDAR dates across 7 sites.",
    "features": ["Radar, per polarisation HH / HV / VV: amplitude pass 1 and 2 (dB), amplitude ratio (dB), "
                 "coherence, unwrapped phase re-referenced to each scene's median, wrapped phase as cos/sin",
                 "Geometry: local and flat incidence angle",
                 "Terrain: elevation, slope, aspect as sin/cos",
                 "Canopy: vegetation height, canopy fraction (nearest vegetation date)",
                 "Timing: radar pair length, LiDAR-radar gap (days)"],
    "models": ["Random forest: 300 trees, min_samples_leaf 10, max_features 0.33 (fixed settings)",
               "Gradient boosting (scikit-learn HistGradientBoosting, 300 iterations, L2 1.0): learning "
               "rate 0.05/0.1 and 15/31 leaves chosen by grouped cross-validation over the training sites "
               "only"],
    "sampling": "Up to 60,000 training rows per site, sites weighted equally so Grand Mesa cannot dominate. "
                "The held-out site is never used for fitting, tuning or early stopping.",
    "reference": "Every model is compared with predicting the training mean; the old evaluation style "
                 "(random 80/20 pixel split, all sites pooled) is run on the same rows for contrast.",
    "code": "scripts/ml/ in the private cryogars-atlas-results repository; Slurm jobs on Borah.",
}

LIMITS = ["Labels are absolute snow depth while one radar pair measures about a week of change, so terrain "
          "and canopy dominate these baselines.",
          "Mean R² is pulled down by Fraser (≈ −2); medians and bias-removed errors give the fuller picture.",
          "Each block appears once per matched radar pair; prediction maps average those pairs.",
          "Values are block means of the 3 m archive, not native-resolution radar."]


def record(args) -> int:
    rows = []
    for s in SCALES:
        p = args.results / s / "results.csv"
        if p.exists():
            r = pd.read_csv(p)
            r["scale"] = s
            rows.append(r)
    res = pd.concat(rows)
    loso = res[res["test_site"] != "random_pixel_split_all_sites"]
    split = res[res["test_site"] == "random_pixel_split_all_sites"].set_index("scale")

    scales = []
    for s in [x for x in SCALES if x in set(res["scale"])]:
        entry = {"scale": s, "random_split_r2": float(split.loc[s, "r2"])}
        for model in MODEL_NAMES:
            for feats in ("all", "no_radar", "no_elevation"):
                g = loso[(loso.scale == s) & (loso.model == model) & (loso.features == feats)]
                entry[f"{model}:{feats}"] = {"mean_r2": float(g.r2.mean()), "median_r2": float(g.r2.median()),
                                             "rmse": float(g.rmse.mean()), "skill": float(g.skill_vs_mean.mean())}
        scales.append(entry)

    per_site = []
    for site, g in loso[(loso.scale == "30m") & (loso.features == "all")].groupby("test_site"):
        row = {"site": site}
        for _, x in g.iterrows():
            row[x.model] = {"r2": float(x.r2), "rmse": float(x.rmse), "bias": float(x.bias),
                            "baseline_rmse": float(x.rmse_mean_baseline)}
        per_site.append(row)

    imp = pd.read_csv(args.results / "30m" / "permutation_importance.csv")
    importance = (imp.groupby("feature")["rmse_increase"].mean().sort_values(ascending=False).head(12) * 100)

    predictions = []
    for meta in sorted(args.maps.glob("*_30m_loso_prediction.json")):
        st = json.loads(meta.read_text())
        tif99 = meta.name.replace("_30m_", "_99m_").replace(".json", ".tif")
        predictions.append({"site": st["site"], "date": st["date"], "blocks": st["blocks"],
                            "explorer": f"{st['site']}_results_explorer.html",
                            "tifs": {"30 m": st["tif"], "99 m": tif99},
                            "gbm_rmse": st["gbm_rmse"], "rf_rmse": st["rf_rmse"], "bias": st["gbm_bias"],
                            "corr": st["gbm_corr"], "rmse_bias_removed": st["gbm_rmse_bias_removed"],
                            "lidar_mean": st["lidar_mean"]})

    run = {"id": args.run_dir.name, "title": args.title, "date": args.date,
           "summary": args.summary, "training": TRAINING, "limits": LIMITS, "scales": scales,
           "per_site_30m": per_site,
           "importance_30m_cm": [{"feature": f, "cm": round(float(v), 2)} for f, v in importance.items()],
           "predictions": predictions}
    args.run_dir.mkdir(parents=True, exist_ok=True)
    (args.run_dir / "run.json").write_text(json.dumps(run, indent=1))
    (args.run_dir / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><title>{html.escape(args.title)}</title>'
        f'<meta http-equiv="refresh" content="0; url=../index.html#{run["id"]}">'
        f'<a href="../index.html#{run["id"]}">Open the results page</a>\n', encoding="utf-8")
    print("wrote", args.run_dir / "run.json")
    return 0


def f(v, d=2, sign=False):
    return "–" if v is None else (f"{v:+.{d}f}" if sign else f"{v:.{d}f}")


def chart(scales) -> str:
    """Line chart: random-split R² vs leave-one-site-out mean and median R² by block size."""
    W, H, L, R, T, B = 560, 260, 48, 120, 16, 34
    ys = [-0.6, -0.4, -0.2, 0, 0.2, 0.4, 0.6, 0.8]
    x = lambda i: L + i * (W - L - R) / (len(scales) - 1)
    y = lambda v: T + (0.8 - v) / 1.4 * (H - T - B)
    series = [("Random pixel split (old style)", "var(--c2)", [s["random_split_r2"] for s in scales], ""),
              ("Leave-one-site-out, median", "var(--c1)",
               [s["random_forest:all"]["median_r2"] for s in scales], "6 4"),
              ("Leave-one-site-out, mean", "var(--c1)", [s["random_forest:all"]["mean_r2"] for s in scales], "")]
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="R squared by block size" class="chart">']
    for v in ys:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid{" zero" if v == 0 else ""}"/>'
                   f'<text x="{L - 8}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v:.1f}</text>')
    for i, s in enumerate(scales):
        out.append(f'<text x="{x(i):.1f}" y="{H - 12}" class="tick" text-anchor="middle">{s["scale"]}</text>')
    for name, colour, vals, dash in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="2" '
                   f'stroke-dasharray="{dash}"/>')
        for i, v in enumerate(vals):
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4" fill="{colour}" class="dot">'
                       f'<title>{name}, {scales[i]["scale"]}: R² {v:.2f}</title></circle>')
        out.append(f'<text x="{W - R + 8}" y="{y(vals[-1]) + 4:.1f}" class="lab">{name.split(",")[-1].strip()}</text>')
    out.append(f'<text x="{L}" y="{T - 4}" class="tick">R² (random forest, all features)</text></svg>')
    return "".join(out)


def section(run) -> str:
    e = html.escape
    tr = run["training"]
    best = max(run["scales"], key=lambda s: s["random_forest:all"]["median_r2"])
    s30 = next(s for s in run["scales"] if s["scale"] == "30m")
    cards = (f'<div class="stats">'
             f'<div class="stat"><b>{s30["random_split_r2"]:.2f}</b><span>R² the old way<br>(random pixel split, 30 m)</span></div>'
             f'<div class="stat"><b>{s30["random_forest:all"]["mean_r2"]:+.2f}</b><span>R² on unseen sites<br>(mean, 30 m)</span></div>'
             f'<div class="stat"><b>{best["random_forest:all"]["median_r2"]:+.2f}</b><span>best median R²<br>({best["scale"]} blocks)</span></div>'
             f'<div class="stat"><b>{s30["random_forest:all"]["skill"] * 100:.0f}%</b><span>lower error than<br>predicting the mean</span></div></div>')

    scale_rows = "".join(
        f'<tr><td>{s["scale"]}</td><td>{f(s["random_split_r2"])}</td>'
        + "".join(f'<td>{f(s[f"{m}:all"]["mean_r2"], sign=True)}</td><td>{f(s[f"{m}:all"]["median_r2"], sign=True)}</td>'
                  f'<td>{f(s[f"{m}:all"]["rmse"])}</td>' for m in MODEL_NAMES)
        + f'<td>{f(s["random_forest:no_radar"]["median_r2"], sign=True)}</td>'
          f'<td>{f(s["random_forest:no_elevation"]["median_r2"], sign=True)}</td></tr>'
        for s in run["scales"])
    site_rows = "".join(
        f'<tr><td>{e(SITE_NAMES.get(r["site"], r["site"]))}</td>'
        + "".join(f'<td>{f(r[m]["r2"], sign=True)}</td><td>{f(r[m]["rmse"])}</td><td>{f(r[m]["bias"], sign=True)}</td>'
                  for m in MODEL_NAMES)
        + f'<td>{f(r["random_forest"]["baseline_rmse"])}</td></tr>' for r in run["per_site_30m"])
    top = run["importance_30m_cm"][0]["cm"] or 1
    imp_rows = "".join(
        f'<li><span class="fname">{e(i["feature"])}</span><span class="bar"><i style="width:{max(i["cm"], 0) / top * 100:.0f}%"></i></span>'
        f'<span class="num">{i["cm"]:.2f} cm</span></li>' for i in run["importance_30m_cm"])
    preds = "".join(
        f'<article class="pred"><h4>{e(SITE_NAMES.get(p["site"], p["site"]))}</h4>'
        f'<p class="meta">LiDAR {p["date"][:4]}-{p["date"][4:6]}-{p["date"][6:]} · {p["blocks"]:,} blocks at 30 m</p>'
        f'<dl><dt>RMSE</dt><dd>RF {f(p["rf_rmse"])} m · GB {f(p["gbm_rmse"])} m</dd>'
        f'<dt>Pattern (corr.)</dt><dd>{f(p["corr"])}</dd>'
        f'<dt>Overall bias</dt><dd>{f(p["bias"], sign=True)} m (true mean {f(p["lidar_mean"])} m)</dd>'
        f'<dt>Error, bias removed</dt><dd>{f(p["rmse_bias_removed"])} m</dd></dl>'
        f'<p class="links"><a class="btn" href="{e(run["id"])}/{e(p["explorer"])}">Open 3D predictions</a> '
        + " ".join(f'<a href="{e(run["id"])}/{e(t)}" download>GeoTIFF {k}</a>' for k, t in p["tifs"].items())
        + "</p></article>" for p in run["predictions"])

    return f'''
<section class="run" id="{e(run["id"])}">
  <header><p class="kicker">Run · {e(run["date"])}</p><h2>{e(run["title"])}</h2>
  <p class="lede">{e(run["summary"])}</p></header>
  {cards}
  <h3>Training</h3>
  <dl class="training">
    <dt>Evaluation</dt><dd>{e(tr["evaluation"])}</dd>
    <dt>Data</dt><dd>{e(tr["data"])}</dd>
    <dt>Features (31)</dt><dd><ul>{"".join(f"<li>{e(x)}</li>" for x in tr["features"])}</ul></dd>
    <dt>Models</dt><dd><ul>{"".join(f"<li>{e(x)}</li>" for x in tr["models"])}</ul></dd>
    <dt>Sampling</dt><dd>{e(tr["sampling"])}</dd>
    <dt>Reference</dt><dd>{e(tr["reference"])}</dd>
    <dt>Code</dt><dd>{e(tr["code"])}</dd>
  </dl>
  <h3>Results across block sizes</h3>
  <figure>{chart(run["scales"])}
  <figcaption>The old random-pixel split looks better as blocks get coarser; scored on sites the model never saw, skill falls instead.</figcaption></figure>
  <div class="scroll"><table>
    <thead><tr><th rowspan="2">Block</th><th rowspan="2">Random split R²</th>
      <th colspan="3">Random forest (unseen sites)</th><th colspan="3">Gradient boosting (unseen sites)</th>
      <th colspan="2">RF median R² without</th></tr>
      <tr><th>mean R²</th><th>median R²</th><th>RMSE m</th><th>mean R²</th><th>median R²</th><th>RMSE m</th><th>radar</th><th>elevation</th></tr></thead>
    <tbody>{scale_rows}</tbody></table></div>
  <h3>Each held-out site at 30 m</h3>
  <div class="scroll"><table>
    <thead><tr><th rowspan="2">Held-out site</th><th colspan="3">Random forest</th><th colspan="3">Gradient boosting</th><th rowspan="2">Predict-mean RMSE m</th></tr>
      <tr><th>R²</th><th>RMSE m</th><th>bias m</th><th>R²</th><th>RMSE m</th><th>bias m</th></tr></thead>
    <tbody>{site_rows}</tbody></table></div>
  <h3>What the models rely on (30 m)</h3>
  <p class="note">Permutation importance on the held-out sites: how much RMSE rises when a feature is shuffled.</p>
  <ol class="imp">{imp_rows}</ol>
  <h3>Predictions</h3>
  <p class="note">Each map comes from models that never saw that site. The 3D explorer shows LiDAR snow depth, both predictions, the error (model − LiDAR) and the bias-removed prediction; use <b>Compare</b> with LiDAR as A and a prediction as B.</p>
  <div class="preds">{preds}</div>
  <h3>Limits</h3><ul class="limits">{"".join(f"<li>{e(x)}</li>" for x in run["limits"])}</ul>
</section>'''


def index(args) -> int:
    runs = [json.loads(p.read_text()) for p in sorted(args.root.glob("*/run.json"))]
    runs.sort(key=lambda r: r["date"], reverse=True)
    nav = "".join(f'<li><a href="#{html.escape(r["id"])}">{html.escape(r["title"])} <span>{html.escape(r["date"])}</span></a></li>'
                  for r in runs)
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SnowEx Model Results</title>
<style>
:root{{--bg:#fcfcfb;--panel:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--line:#e3e2dd;--c1:#2a78d6;--c2:#eb6834;--accent:#2a78d6}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#1a1a19;--panel:#222220;--ink:#ffffff;--ink2:#c3c2b7;--line:#383835;--c1:#3987e5;--c2:#d95926;--accent:#3987e5}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px 64px}}
.top h1{{margin:0;font-size:28px}} .top p{{color:var(--ink2);margin:6px 0 0}}
.badge{{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;color:var(--ink2);margin-top:10px}}
nav ul{{list-style:none;padding:0;margin:20px 0;display:flex;flex-wrap:wrap;gap:8px}}
nav a{{display:block;padding:8px 12px;border:1px solid var(--line);border-radius:8px;color:var(--ink);text-decoration:none;background:var(--panel)}}
nav a span{{color:var(--ink2);font-size:13px;margin-left:6px}}
.run{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:24px;margin-top:24px}}
.kicker{{margin:0;color:var(--ink2);font-size:13px;text-transform:uppercase;letter-spacing:.06em}}
.run h2{{margin:4px 0 8px;font-size:24px}} .lede{{color:var(--ink2);margin:0;max-width:75ch}}
h3{{margin:32px 0 10px;font-size:17px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:20px}}
.stat{{border:1px solid var(--line);border-radius:10px;padding:14px}} .stat b{{display:block;font-size:30px;font-variant-numeric:tabular-nums}}
.stat span{{color:var(--ink2);font-size:13px}}
dl.training{{display:grid;grid-template-columns:140px 1fr;gap:8px 16px;margin:0}} dl.training dt{{color:var(--ink2)}}
dl.training dd{{margin:0}} dl.training ul{{margin:0;padding-left:18px}}
@media (max-width:640px){{dl.training{{grid-template-columns:1fr}}}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:14px}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} thead th{{color:var(--ink2);font-weight:600}}
figure{{margin:0}} figcaption,.note{{color:var(--ink2);font-size:13px}}
.chart{{width:100%;max-width:640px;height:auto}} .chart .grid{{stroke:var(--line)}} .chart .zero{{stroke:var(--ink2)}}
.chart .tick,.chart .lab{{fill:var(--ink2);font-size:12px}} .chart .dot{{stroke:var(--panel);stroke-width:2}}
ol.imp{{list-style:none;padding:0;margin:0;max-width:640px}} ol.imp li{{display:grid;grid-template-columns:170px 1fr 70px;gap:10px;align-items:center;padding:3px 0}}
.fname{{font-family:ui-monospace,monospace;font-size:13px}} .bar{{background:var(--line);border-radius:4px;height:10px}}
.bar i{{display:block;height:10px;background:var(--c1);border-radius:4px}} .num{{text-align:right;color:var(--ink2);font-size:13px}}
.preds{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}}
.pred{{border:1px solid var(--line);border-radius:10px;padding:14px}} .pred h4{{margin:0}} .meta{{color:var(--ink2);font-size:13px;margin:2px 0 8px}}
.pred dl{{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;margin:0;font-size:13px}} .pred dt{{color:var(--ink2)}} .pred dd{{margin:0;text-align:right}}
.links{{margin:12px 0 0;font-size:13px}} .links a{{color:var(--accent);margin-right:8px}}
.btn{{display:inline-block;background:var(--accent);color:#fff !important;padding:6px 10px;border-radius:6px;text-decoration:none}}
.limits{{color:var(--ink2);font-size:14px}}
</style></head><body><div class="wrap">
<header class="top"><h1>SnowEx Model Results</h1>
<p>CryoGARS lab, Boise State University — model runs on the SnowEx Field Atlas v1.0.05.</p>
<span class="badge">Research results · shared by link · not part of the public SnowEx Field Atlas</span></header>
<nav aria-label="Runs"><ul>{nav}</ul></nav>
{"".join(section(r) for r in runs)}
</div></body></html>'''
    (args.root / "index.html").write_text(page, encoding="utf-8")
    print("wrote", args.root / "index.html", "with", len(runs), "run(s)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--run-dir", type=Path, required=True)
    r.add_argument("--results", type=Path, required=True)
    r.add_argument("--maps", type=Path, required=True)
    r.add_argument("--title", required=True)
    r.add_argument("--date", required=True)
    r.add_argument("--summary", default="")
    i = sub.add_parser("index")
    i.add_argument("--root", type=Path, required=True)
    args = ap.parse_args(argv)
    return record(args) if args.cmd == "record" else index(args)


if __name__ == "__main__":
    raise SystemExit(main())
