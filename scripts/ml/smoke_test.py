"""Smoke test: synthetic enriched archives -> extract_blocks -> train_baselines."""
import subprocess, sys
from pathlib import Path
import h5py, numpy as np

ROOT = Path(__file__).resolve().parent
TMP = Path("/bsuscratch/anantgahlaut/snowex-ml/smoke")
H, W = 120, 90
rng = np.random.default_rng(1)


def archive(site, offset):
    p = TMP / "arch" / f"{site}.enriched.h5"
    p.parent.mkdir(parents=True, exist_ok=True)
    elev = 2500 + offset + np.cumsum(rng.normal(0, 1, (H, W)), axis=0).astype("f4")
    with h5py.File(p, "w") as f:
        L = f.create_group("science/LIDAR")
        L["DEM/grids/elevation"] = elev
        L["DERIVED/slope"] = rng.uniform(0, 40, (H, W)).astype("f4")
        L["DERIVED/aspect"] = rng.uniform(0, 360, (H, W)).astype("f4")
        L["DERIVED/forest_cover_fraction_20210319"] = rng.uniform(0, 1, (H, W)).astype("f4")
        L["VH/20210319/veg_height"] = rng.uniform(0, 20, (H, W)).astype("f4")
        sd = (1 + 0.001 * (elev - 2500)).astype("f4")
        sd[:5] = np.nan
        L["SD/20210319/snow_depth"] = sd
        g = f.create_group("science/UAVSAR/20210310_20210316/LINE01234")
        g["GEOMETRY/local_incidence_angle"] = rng.uniform(30, 60, (H, W)).astype("f4")
        g["GEOMETRY/incidence_angle_flat"] = rng.uniform(30, 60, (H, W)).astype("f4")
        for pol in ("HH", "VV"):
            g[f"{pol}/amp1"] = rng.uniform(0.1, 1, (H, W)).astype("f4")
            g[f"{pol}/amp2"] = rng.uniform(0.1, 1, (H, W)).astype("f4")
            g[f"{pol}/cor"] = rng.uniform(0, 1, (H, W)).astype("f4")
            g[f"{pol}/unw"] = (sd * 3 + 100).astype("f4")
            g[f"{pol}/int"] = np.exp(1j * rng.uniform(-3, 3, (H, W))).astype("c8")
        m = f.create_group("matches")
        n = 2
        m["lidar_product_type"] = np.array([b"SD", b"SD"])
        m["verdict"] = np.array([b"match"] * n)
        m["uavsar_level"] = np.array([b"INTERFEROMETRY_GRD", b"AMPLITUDE_GRD"])
        m["lidar_date"] = np.array([b"20210319"] * n)
        m["uavsar_dates"] = np.array([b"20210310_20210316"] * n)
        m["group_name"] = np.array([f"{site.upper()}_CUTFROMLINE01234_20210310_20210316".encode()] * n)
        m["gap_days"] = np.array([3, 3], "i4")
    return p


py = sys.executable
for i, site in enumerate(["alpha", "bravo", "charlie", "delta"]):
    a = archive(site, i * 300)
    subprocess.run([py, str(ROOT / "extract_blocks.py"), str(a),
                    str(TMP / "blocks" / f"{site}.pkl"), "--block", "10"], check=True)
subprocess.run([py, str(ROOT / "train_baselines.py"), str(TMP / "blocks"),
                str(TMP / "results"), "--per-site", "500"], check=True)
print("SMOKE OK")
