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

import argparse, base64, html, json
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


GROUPS = ["Terrain", "Canopy", "Radar geometry", "Radar · HH", "Radar · HV", "Radar · VV", "Timing"]


def feature_group(name: str) -> str:
    if name.startswith(("HH_", "HV_", "VV_")):
        return f"Radar · {name[:2]}"
    if name.startswith("inc_"):
        return "Radar geometry"
    if name in ("veg_height", "canopy_fraction"):
        return "Canopy"
    if name in ("pair_days", "gap_days"):
        return "Timing"
    return "Terrain"


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

    # Permutation importance on held-out sites, every feature, both models, every scale (cm of RMSE).
    importance = {}
    for s in SCALES:
        p = args.results / s / "permutation_importance.csv"
        if not p.exists():
            continue
        imp = pd.read_csv(p)
        table = imp.groupby(["feature", "model"])["rmse_increase"].mean().unstack("model") * 100
        table["mean"] = table.mean(axis=1)
        importance[s] = [{"feature": f, "group": feature_group(f), "cm": round(float(r["mean"]), 3),
                          **{m: round(float(r[m]), 3) for m in MODEL_NAMES if m in r}}
                         for f, r in table.sort_values("mean", ascending=False).iterrows()]

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
           "summary": args.summary, "findings": args.finding or [], "training": TRAINING, "limits": LIMITS, "scales": scales,
           "per_site_30m": per_site,
           "importance_cm": importance,
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


LOGO = Path(__file__).resolve().parents[2] / "assets" / "cryogars-logo.jpg"
OLD, HONEST = "#e0703f", "#2aa883"   # validated pair on the dark surface (dataviz validator)


def chart(scales) -> str:
    """R² by block size: the old random-pixel split vs leave-one-site-out (random forest)."""
    W, H, L, R, T, B = 640, 300, 52, 150, 28, 40
    lo, hi = -0.6, 0.8
    x = lambda i: L + i * (W - L - R) / (len(scales) - 1)
    y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    series = [("Random pixel split", OLD, [s["random_split_r2"] for s in scales], ""),
              ("Unseen sites · median", HONEST, [s["random_forest:all"]["median_r2"] for s in scales], "5 5"),
              ("Unseen sites · mean", HONEST, [s["random_forest:all"]["mean_r2"] for s in scales], "")]
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" aria-label="R squared by block size, '
           f'random pixel split versus leave-one-site-out">',
           f'<text x="{L}" y="14" class="axis-title">R² · random forest, all features</text>']
    for v in (-0.6, -0.4, -0.2, 0, 0.2, 0.4, 0.6, 0.8):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="{"zero" if v == 0 else "grid"}"/>'
                   f'<text x="{L - 10}" y="{y(v) + 4:.1f}" class="tick" text-anchor="end">{v:+.1f}</text>')
    for i, s in enumerate(scales):
        out.append(f'<text x="{x(i):.1f}" y="{H - 14}" class="tick" text-anchor="middle">{s["scale"]}</text>')
    out.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H}" class="tick" text-anchor="middle">block size</text>')
    for name, colour, vals, dash in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" stroke-width="2" stroke-dasharray="{dash}"/>')
        for i, v in enumerate(vals):
            out.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="4.5" fill="{colour}" class="dot">'
                       f'<title>{name}, {scales[i]["scale"]} blocks: R² {v:+.2f}</title></circle>')
        out.append(f'<text x="{W - R + 12}" y="{y(vals[-1]) + 4:.1f}" class="series" fill="{colour}">{name}</text>')
    out.append("</svg>")
    return "".join(out)


def importance_panel(run, idx) -> str:
    e = html.escape
    by_scale = run["importance_cm"]
    scales = [s for s in SCALES if s in by_scale]
    default = "30m" if "30m" in scales else scales[0]
    blocks = []
    for s in scales:
        items = by_scale[s]
        top = max(i["cm"] for i in items) or 1
        totals = {}
        for i in items:
            g = "Radar" if i["group"].startswith("Radar") else i["group"]
            totals[g] = totals.get(g, 0) + max(i["cm"], 0)
        tmax = max(totals.values()) or 1
        total_rows = "".join(
            f'<li><span>{e(g)}</span><span class="bar"><i style="width:{v / tmax * 100:.1f}%"></i></span>'
            f'<span class="num">{v:.2f} cm</span></li>'
            for g, v in sorted(totals.items(), key=lambda kv: -kv[1]))
        groups = []
        for g in GROUPS:
            rows = [i for i in items if i["group"] == g]
            if not rows:
                continue
            groups.append(f'<h4>{e(g)}</h4><ol class="bars">' + "".join(
                f'<li><span class="mono">{e(i["feature"])}</span><span class="bar"><i style="width:{max(i["cm"], 0) / top * 100:.1f}%"></i></span>'
                f'<span class="num">{i["cm"]:.2f} cm</span>'
                f'<span class="num faint">RF {i.get("random_forest", 0):.2f} · GB {i.get("gradient_boosting", 0):.2f}</span></li>'
                for i in rows) + "</ol>")
        blocks.append(f'<div class="imp-scale" data-scale="{s}"{"" if s == default else " hidden"}>'
                      f'<h4>Totals by input type</h4><ol class="bars totals">{total_rows}</ol>{"".join(groups)}</div>')
    picker = "".join(f'<button type="button" class="chip" data-imp="{s}" aria-pressed="{str(s == default).lower()}">{s}</button>'
                     for s in scales)
    return (f'<p class="note">Permutation importance on the held-out sites: how many centimetres the RMSE rises '
            f'when one input is shuffled. All 31 inputs, averaged over random forest and gradient boosting.</p>'
            f'<div class="chips" role="group" aria-label="Block size">{picker}</div>{"".join(blocks)}')


def run_detail(run, n, total) -> str:
    e = html.escape
    tr = run["training"]
    s30 = next(s for s in run["scales"] if s["scale"] == "30m")
    best = max(run["scales"], key=lambda s: s["random_forest:all"]["median_r2"])
    findings = "".join(f"<li>{e(x)}</li>" for x in run.get("findings", []))
    preds = "".join(
        f'<li class="pred-row"><div class="pred-name"><span class="site-number">{i:02}</span>'
        f'<span class="site-label"><strong>{e(SITE_NAMES.get(p["site"], p["site"]))}</strong>'
        f'<span>LiDAR {p["date"][:4]}-{p["date"][4:6]}-{p["date"][6:]} · {p["blocks"]:,} blocks at 30 m</span></span></div>'
        f'<dl class="pred-stats"><div><dt>RMSE · RF</dt><dd>{f(p["rf_rmse"])} m</dd></div>'
        f'<div><dt>RMSE · GB</dt><dd>{f(p["gbm_rmse"])} m</dd></div>'
        f'<div><dt>Pattern r</dt><dd>{f(p["corr"])}</dd></div>'
        f'<div><dt>Bias</dt><dd>{f(p["bias"], sign=True)} m</dd></div>'
        f'<div><dt>Bias removed</dt><dd>{f(p["rmse_bias_removed"])} m</dd></div></dl>'
        f'<div class="pred-actions"><a class="launch small" href="{e(run["id"])}/{e(p["explorer"])}">Open 3D <span aria-hidden="true">↗</span></a>'
        + "".join(f'<a class="tif" href="{e(run["id"])}/{e(t)}" download>GeoTIFF {k}</a>' for k, t in p["tifs"].items())
        + "</div></li>" for i, p in enumerate(run["predictions"], 1))
    scale_rows = "".join(
        f'<tr><td>{s["scale"]}</td><td class="old">{f(s["random_split_r2"])}</td>'
        + "".join(f'<td>{f(s[f"{m}:all"]["mean_r2"], sign=True)}</td><td>{f(s[f"{m}:all"]["median_r2"], sign=True)}</td>'
                  f'<td>{f(s[f"{m}:all"]["rmse"])}</td>' for m in MODEL_NAMES)
        + f'<td>{f(s["random_forest:no_radar"]["median_r2"], sign=True)}</td>'
          f'<td>{f(s["random_forest:no_elevation"]["median_r2"], sign=True)}</td></tr>' for s in run["scales"])
    site_rows = "".join(
        f'<tr><td>{e(SITE_NAMES.get(r["site"], r["site"]))}</td>'
        + "".join(f'<td>{f(r[m]["r2"], sign=True)}</td><td>{f(r[m]["rmse"])}</td><td>{f(r[m]["bias"], sign=True)}</td>'
                  for m in MODEL_NAMES)
        + f'<td>{f(r["random_forest"]["baseline_rmse"])}</td></tr>' for r in run["per_site_30m"])
    tabs = [("overview", "Overview"), ("predictions", "Predictions"), ("importance", "Feature importance"),
            ("training", "Training"), ("results", "Full results")]
    rid = e(run["id"])
    return f'''
<section class="detail run-detail" id="{rid}" data-run="{rid}" aria-labelledby="{rid}-title"{"" if n == 1 else " hidden"}>
  <header class="detail-head"><div><p class="eyebrow">Run / {n:02} &nbsp;·&nbsp; {e(run["date"])}</p>
    <h2 id="{rid}-title">{e(run["title"])}</h2></div>
    <div class="preview-label">Random forest<br><span>+ gradient boosting</span></div></header>
  <div class="detail-body">
    <p class="lede">{e(run["summary"])}</p>
    <dl class="summary four"><div><dt>Old-style R² · 30 m</dt><dd class="old">{s30["random_split_r2"]:.2f}</dd></div>
      <div><dt>Unseen sites R² · 30 m mean</dt><dd>{s30["random_forest:all"]["mean_r2"]:+.2f}</dd></div>
      <div><dt>Best unseen median R²</dt><dd>{best["random_forest:all"]["median_r2"]:+.2f} <small>{best["scale"]}</small></dd></div>
      <div><dt>Error vs predicting the mean</dt><dd>−{s30["random_forest:all"]["skill"] * 100:.0f}%</dd></div></dl>
    <div class="tabs" role="tablist" aria-label="{e(run["title"])} sections">{"".join(
        f'<button type="button" role="tab" class="tab" data-tab="{k}" aria-selected="{str(k == "overview").lower()}">{v}</button>'
        for k, v in tabs)}</div>
    <div class="panel" data-panel="overview">
      <figure>{chart(run["scales"])}<figcaption>Scored the old way, skill appears to rise as blocks get coarser.
        Scored on sites the model never saw, it falls. Same rows, same model.</figcaption></figure>
      <h3>Key findings</h3><ul class="findings">{findings}</ul></div>
    <div class="panel" data-panel="predictions" hidden>
      <p class="note">Each map comes from models that never saw that site. The 3D explorer shows LiDAR snow depth, both
        predictions, the error (model − LiDAR) and the bias-removed prediction on the terrain; use <b>Compare</b> with
        LiDAR as A and a prediction as B.</p>
      <ul class="pred-list">{preds}</ul></div>
    <div class="panel" data-panel="importance" hidden>{importance_panel(run, n)}</div>
    <div class="panel" data-panel="training" hidden>
      <div class="about-copy">
        <div><h3>Evaluation</h3><p>{e(tr["evaluation"])}</p><h3>Data</h3><p>{e(tr["data"])}</p>
          <h3>Sampling</h3><p>{e(tr["sampling"])}</p><h3>Reference</h3><p>{e(tr["reference"])}</p></div>
        <div><h3>Inputs · 31 features</h3><ul>{"".join(f"<li>{e(x)}</li>" for x in tr["features"])}</ul>
          <h3>Models</h3><ul>{"".join(f"<li>{e(x)}</li>" for x in tr["models"])}</ul>
          <p class="mono">{e(tr["code"])}</p></div></div></div>
    <div class="panel" data-panel="results" hidden>
      <h3>Across block sizes</h3>
      <div class="scroll"><table><thead>
        <tr><th rowspan="2">Block</th><th rowspan="2">Random split R²</th><th colspan="3">Random forest · unseen sites</th>
          <th colspan="3">Gradient boosting · unseen sites</th><th colspan="2">RF median R² without</th></tr>
        <tr><th>mean R²</th><th>median R²</th><th>RMSE m</th><th>mean R²</th><th>median R²</th><th>RMSE m</th><th>radar</th><th>elevation</th></tr>
      </thead><tbody>{scale_rows}</tbody></table></div>
      <h3>Each held-out site · 30 m</h3>
      <div class="scroll"><table><thead>
        <tr><th rowspan="2">Held-out site</th><th colspan="3">Random forest</th><th colspan="3">Gradient boosting</th><th rowspan="2">Predict-mean RMSE m</th></tr>
        <tr><th>R²</th><th>RMSE m</th><th>bias m</th><th>R²</th><th>RMSE m</th><th>bias m</th></tr>
      </thead><tbody>{site_rows}</tbody></table></div>
      <h3>Limits</h3><ul class="findings">{"".join(f"<li>{e(x)}</li>" for x in run["limits"])}</ul></div>
  </div>
</section>'''


STYLE = """
:root{color-scheme:dark;--bg:#081216;--panel:#0d1a1e;--line:#293c40;--ink:#edf3ec;--muted:#a2b5b5;--accent:#a6e2cc;--yellow:#dbe8aa;--old:#e0703f;--honest:#2aa883;--mono:Consolas,"SFMono-Regular",monospace;--sans:"Segoe UI",Arial,sans-serif}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;color:var(--ink);font:16px/1.5 var(--sans);background:radial-gradient(ellipse at 83% 23%,#12313566,transparent 55%)}
button{font:inherit;cursor:pointer}a{color:inherit}button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--yellow);outline-offset:4px}
.skip{position:absolute;left:20px;top:-80px;background:var(--accent);color:var(--bg);padding:12px;z-index:4}.skip:focus{top:20px}
.shell{max-width:1560px;margin:auto;padding:0 4.3vw}.mono{font-family:var(--mono)}
.topbar{min-height:94px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);gap:24px}
.brand{text-decoration:none;display:flex;align-items:center;gap:13px}
.brand-logo-frame{display:block;position:relative;overflow:hidden;width:clamp(128px,12vw,168px);aspect-ratio:2.5;flex-shrink:0}
.brand-logo{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;filter:invert(1);mix-blend-mode:screen;opacity:.94}
.brand-context{border-left:1px solid var(--line);padding-left:16px;font:12px/1.6 var(--mono);letter-spacing:.1em;color:var(--muted)}
.topnav{display:flex;align-items:center;gap:32px;font-size:14px}.topnav a{text-decoration:none;color:var(--muted)}.topnav a:hover{color:var(--accent)}
.edition{font:12px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);display:flex;gap:9px;align-items:center}
.edition:before{content:"";width:6px;height:6px;background:var(--accent);border-radius:50%}
.intro{padding:44px 0 34px;display:flex;align-items:flex-end;justify-content:space-between;gap:24px}
.eyebrow{font:12px var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0 0 15px}
.intro h1{font-weight:450;font-size:clamp(42px,5.4vw,80px);line-height:1.02;letter-spacing:-.065em;margin:0}
.intro h1 em{font-style:normal;color:var(--accent);font-weight:300}.intro p:not(.eyebrow){color:var(--muted);margin:18px 0 0;max-width:560px}
.archive-counts{display:flex;gap:38px;margin:0 0 3px;padding:0;flex-shrink:0}.archive-counts div{border-left:1px solid var(--line);padding-left:22px;display:flex;flex-direction:column}
.archive-counts dt{color:var(--muted);font:12px var(--mono);margin-top:7px;order:2}.archive-counts dd{font-size:34px;line-height:1;margin:0;letter-spacing:-.04em;order:1}
.workbench{display:grid;grid-template-columns:320px minmax(0,1fr);border:1px solid var(--line);background:#0a1619;min-height:610px}
.directory{border-right:1px solid var(--line);background:#091316}
.section-label{padding:18px 20px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);font:12px var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.section-label span:last-child{color:var(--accent)}
.site-list,.pred-list{list-style:none;padding:0;margin:0}.site-row{border-bottom:1px solid #203237}
.site-select{border:0;background:transparent;color:var(--ink);width:100%;text-align:left;display:flex;align-items:center;gap:13px;padding:16px 16px 16px 20px;min-height:69px;transition:background .2s}
.site-select:hover{background:#14282b}.site-select[aria-pressed=true]{background:#183331;box-shadow:inset 3px 0 var(--accent)}
.site-number{font:12px var(--mono);color:var(--muted);align-self:flex-start;margin-top:3px}.site-label{flex:1}
.site-label strong{display:block;font-size:15px;font-weight:500;line-height:1.25}.site-label>span{display:block;font:12px var(--mono);color:var(--muted);margin-top:6px}
.selected-mark{color:var(--accent);display:none}.site-select[aria-pressed=true] .selected-mark{display:inline}
.detail{position:relative;min-width:0;background:radial-gradient(ellipse at 55% 25%,#123033aa,transparent 64%)}
.detail-head{display:flex;justify-content:space-between;align-items:start;padding:25px 30px 0;gap:20px}.detail-head .eyebrow{margin-bottom:7px}
.detail-head h2{font-size:clamp(24px,2.7vw,38px);font-weight:400;letter-spacing:-.04em;line-height:1.2;margin:0}
.preview-label{font:12px/1.6 var(--mono);color:var(--muted);text-align:right}.preview-label span{color:var(--accent)}
.detail-body{padding:8px 30px 28px}.lede{color:var(--muted);max-width:820px;margin:10px 0 0}
.summary{border-top:1px solid var(--line);border-bottom:1px solid var(--line);display:grid;grid-template-columns:repeat(4,1fr);margin:22px 0 0;padding:17px 0;gap:16px}
.summary dt{font:12px var(--mono);color:var(--muted);margin-bottom:4px}.summary dd{font:26px var(--mono);margin:0;letter-spacing:-.03em}.summary small{font-size:13px;color:var(--muted)}
.old{color:var(--old)}
.tabs{display:flex;gap:4px;margin:22px 0 0;border-bottom:1px solid var(--line);overflow-x:auto}
.tab{background:transparent;border:0;color:var(--muted);padding:10px 14px;font:13px var(--mono);letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;border-bottom:2px solid transparent}
.tab:hover{color:var(--ink)}.tab[aria-selected=true]{color:var(--accent);border-bottom-color:var(--accent)}
.panel{padding-top:22px}.panel h3{font-size:16px;font-weight:500;margin:22px 0 8px}.panel h3:first-child{margin-top:0}
.panel h4{font:12px var(--mono);text-transform:uppercase;letter-spacing:.08em;color:var(--accent);margin:20px 0 8px}
.note,figcaption{color:var(--muted);font-size:14px;max-width:820px}
figure{margin:0}.chart{width:100%;max-width:760px;height:auto;display:block}
.chart .grid{stroke:#1d2e32}.chart .zero{stroke:#46605f}.chart .tick,.chart .axis-title{fill:var(--muted);font:12px var(--mono)}
.chart .series{font:12px var(--mono)}.chart .dot{stroke:#0a1619;stroke-width:2}
.findings{color:var(--muted);padding-left:18px;max-width:880px}.findings li{margin:6px 0}
.pred-row{display:grid;grid-template-columns:minmax(220px,1.2fr) 2fr auto;gap:18px;align-items:center;padding:16px 0;border-bottom:1px solid #203237}
.pred-name{display:flex;gap:13px}
.pred-stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:0}.pred-stats dt{font:11px var(--mono);color:var(--muted)}.pred-stats dd{font:14px var(--mono);margin:2px 0 0}
.pred-actions{display:flex;flex-direction:column;gap:6px;align-items:stretch}
.launch{display:flex;justify-content:space-between;align-items:center;gap:22px;background:var(--accent);color:#0b2420;text-decoration:none;padding:10px 16px;font-weight:600;transition:background .2s}
.launch:hover{background:#c0f2df}.launch span{font-size:19px;line-height:1}
.tif{font:12px var(--mono);color:var(--muted);text-decoration:none;text-align:right}.tif:hover{color:var(--accent)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0 0}
.chip{border:1px solid var(--line);background:#0d1a1e;color:var(--muted);padding:6px 12px;font:12px var(--mono)}
.chip[aria-pressed=true]{border-color:var(--accent);color:var(--accent);background:#183331}
.bars{list-style:none;padding:0;margin:0;max-width:900px}
.bars li{display:grid;grid-template-columns:200px minmax(0,1fr) 80px 150px;gap:12px;align-items:center;padding:4px 0;font-size:14px}
.bars.totals li{grid-template-columns:200px minmax(0,1fr) 80px}
.bar{background:#14282b;height:10px}.bar i{display:block;height:10px;background:var(--honest)}
.num{font:13px var(--mono);text-align:right}.faint{color:var(--muted);font-size:11px}
.about-copy{display:grid;grid-template-columns:1fr 1fr;gap:48px;max-width:1100px}.about-copy p,.about-copy li{color:var(--muted)}
.about-copy ul{padding-left:18px}.about-copy .mono{font-size:12px;margin-top:13px;line-height:1.7}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font:13px var(--mono)}
th,td{padding:8px 10px;border-bottom:1px solid #203237;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}
thead th{color:var(--muted);font-weight:400}
.archive-note{display:flex;align-items:center;justify-content:space-between;padding:16px 0 24px;gap:20px;font:12px var(--mono);color:var(--muted)}
.archive-note p{margin:0}.archive-note strong{color:var(--accent);font-weight:400}.archive-note a{color:var(--accent)}
.about{border-top:1px solid var(--line);padding:22px 0 30px}.about summary{cursor:pointer;font-size:14px;color:var(--muted);width:fit-content}
.about .about-copy{padding-top:22px}.about h3{font-size:16px;font-weight:500;margin:0 0 8px}
footer{border-top:1px solid var(--line);padding:20px 0 30px;display:flex;justify-content:space-between;gap:20px;color:var(--muted);font:12px var(--mono)}
footer span:last-child{color:var(--accent)}[hidden]{display:none!important}
@media(max-width:1050px){.shell{padding:0 24px}.workbench{grid-template-columns:260px minmax(0,1fr)}.pred-row{grid-template-columns:1fr}.pred-actions{flex-direction:row;flex-wrap:wrap}.summary{grid-template-columns:1fr 1fr}}
@media(max-width:720px){.shell{padding:0 16px}.brand-context,.topnav .edition{display:none}.intro{display:block}.archive-counts{margin-top:25px;gap:20px}
.workbench{display:flex;flex-direction:column}.directory{border-right:0;border-bottom:1px solid var(--line)}.detail-head,.detail-body{padding-left:16px;padding-right:16px}
.pred-stats{grid-template-columns:repeat(3,minmax(0,1fr))}.bars li,.bars.totals li{grid-template-columns:120px minmax(0,1fr) 70px}.bars .faint{display:none}
.about-copy{grid-template-columns:1fr;gap:24px}footer{flex-direction:column;gap:8px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{transition:none!important}}
"""

SCRIPT = """
(function(){
  var rows=[].slice.call(document.querySelectorAll('.site-select'));
  var runs=[].slice.call(document.querySelectorAll('.run-detail'));
  function show(id){
    if(!runs.some(function(r){return r.id===id;}))id=runs.length?runs[0].id:'';
    runs.forEach(function(r){r.hidden=r.id!==id;});
    rows.forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.run===id));});
  }
  rows.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.run);history.replaceState(null,'','#'+b.dataset.run);});});
  runs.forEach(function(run){
    var tabs=[].slice.call(run.querySelectorAll('.tab')),panels=[].slice.call(run.querySelectorAll('.panel'));
    tabs.forEach(function(t){t.addEventListener('click',function(){
      tabs.forEach(function(x){x.setAttribute('aria-selected',String(x===t));});
      panels.forEach(function(p){p.hidden=p.dataset.panel!==t.dataset.tab;});});});
    var chips=[].slice.call(run.querySelectorAll('.chip')),blocks=[].slice.call(run.querySelectorAll('.imp-scale'));
    chips.forEach(function(c){c.addEventListener('click',function(){
      chips.forEach(function(x){x.setAttribute('aria-pressed',String(x===c));});
      blocks.forEach(function(b){b.hidden=b.dataset.scale!==c.dataset.imp;});});});
  });
  show(location.hash.slice(1));
})();
"""


def index(args) -> int:
    e = html.escape
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.root.glob("*/run.json"))]
    runs.sort(key=lambda r: r["date"], reverse=True)
    logo = "data:image/jpeg;base64," + base64.b64encode(LOGO.read_bytes()).decode("ascii")
    sites = {p["site"] for r in runs for p in r["predictions"]}
    best = max((s["random_forest:all"]["median_r2"] for r in runs for s in r["scales"]), default=0)
    rows = "".join(
        f'<li class="site-row"><button type="button" class="site-select" data-run="{e(r["id"])}" '
        f'aria-pressed="{str(i == 1).lower()}" aria-controls="{e(r["id"])}"><span class="site-number">{i:02}</span>'
        f'<span class="site-label"><strong>{e(r["title"])}</strong><span>{e(r["date"])} · {len(r["predictions"])} sites</span></span>'
        f'<span class="selected-mark" aria-hidden="true">●</span></button></li>'
        for i, r in enumerate(runs, 1))
    details = "".join(run_detail(r, i, len(runs)) for i, r in enumerate(runs, 1))
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#081216">
<meta name="description" content="CryoGARS machine-learning runs for snow-depth retrieval from airborne L-band InSAR, each scored on SnowEx sites the model never saw.">
<title>SnowEx Model Results · CryoGARS</title><style>{STYLE}</style></head>
<body><a class="skip" href="#runs">Skip to runs</a><div class="shell">
<header class="topbar">
  <a class="brand" href="index.html" aria-label="SnowEx Model Results home"><span class="brand-logo-frame"><img class="brand-logo" src="{logo}" alt="CryoGARS" width="2681" height="1059"></span><span class="brand-context" aria-hidden="true">SNOWEX<br>MODEL RESULTS</span></a>
  <nav class="topnav" aria-label="Main"><a href="#runs">Runs</a><a href="#about">About the results</a>
    <a href="https://anantgahlaut.github.io/cryogars-atlas-viewer/">Data atlas ↗</a><span class="edition">Research results</span></nav>
</header>
<main>
  <section class="intro" aria-labelledby="title">
    <div><p class="eyebrow">Machine learning / snow depth &nbsp;·&nbsp; leave-one-site-out</p>
      <h1 id="title">SnowEx <em>Model Results.</em></h1>
      <p>Every model, scored on ground it never saw.<br>Training, results and predictions for each run.</p></div>
    <dl class="archive-counts"><div><dt>RUNS</dt><dd>{len(runs)}</dd></div><div><dt>SITES PREDICTED</dt><dd>{len(sites)}</dd></div>
      <div><dt>BEST UNSEEN MEDIAN R²</dt><dd>{best:+.2f}</dd></div></dl>
  </section>
  <section class="workbench" id="runs" aria-label="Choose a run">
    <div class="directory"><div class="section-label"><span>Run directory</span><span>{len(runs)} run{"s" if len(runs) != 1 else ""}</span></div>
      <ul class="site-list">{rows}</ul></div>
    {details}
  </section>
  <div class="archive-note"><p><strong>Leave-one-site-out.</strong><br>Every number and map comes from models that never saw that site.</p>
    <p>Data: <a href="https://github.com/AnantGahlaut/cryogars-atlas">SnowEx Field Atlas v1.0.05 ↗</a></p></div>
  <details class="about" id="about"><summary>About these results</summary><div class="about-copy">
    <div><h3>Why leave-one-site-out.</h3><p>Neighbouring pixels share information, so a random train/test split lets a
      model memorise each site and look far better than it is. Holding out a whole site asks the real question: does the
      model work somewhere new? Earlier evaluations in this project used random splits; every run here is scored on unseen
      sites.</p></div>
    <div><h3>What the numbers mean.</h3><p>R² compares a model with the held-out site's own mean depth, so a negative
      value means it would have been better to know that site's average. "Error vs predicting the mean" compares RMSE
      with predicting the training sites' average, which is all a model without site knowledge could do. Pattern r is
      the correlation between predicted and LiDAR depth across the site.</p></div></div></details>
</main>
<footer><span>CryoGARS &nbsp; / &nbsp; SnowEx model results</span><span>Random forest · gradient boosting · {len(sites)} sites</span></footer>
</div><script>{SCRIPT}</script></body></html>'''
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
    r.add_argument("--finding", action="append", help="one key finding (repeatable)")
    i = sub.add_parser("index")
    i.add_argument("--root", type=Path, required=True)
    args = ap.parse_args(argv)
    return record(args) if args.cmd == "record" else index(args)


if __name__ == "__main__":
    raise SystemExit(main())
