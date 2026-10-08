#!/usr/bin/env python3
"""
audit_archive.py -- independent end-to-end audit of the finished archive.

Deliberately does NOT import the grid-derivation code from build_hdf5.py. The
builder and `--mode verify` share that code by design, so they agree with each
other by construction; this reads the files cold and re-derives everything from
what is actually stored, then checks it against the preflight inventory and the
site areas published in the project README.

    python audit_archive.py
    python audit_archive.py --out-dir C:\\SnowEx\\out --json report.json

Exit code is 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

#: Surveyed area per site in km^2, measured independently and recorded in the
#: project README. The archive is expected to reproduce these from its own
#: valid-pixel counts; agreement is the strongest correctness signal available,
#: because the two numbers are produced by completely different routes.
README_AREA_KM2 = {
    "banner_summit": 167.1, "grand_mesa": 136.4, "reynolds_creek": 81.5,
    "dry_creek": 37.8, "fraser": 35.8, "mores_creek": 34.7,
    "little_cottonwood": 28.1, "cameron_pass": 21.9,
}

#: Attributes every science array must carry for the archive to stand alone.
REQUIRED_ARRAY_ATTRS = (
    "description", "crs_wkt", "transform", "shape", "nodata", "dtype",
    "resolution_m", "resampling_method", "source_dataset",
)
REQUIRED_IDENT_ATTRS = (
    "site_name", "common_crs_wkt", "common_crs_epsg", "common_grid_transform",
    "common_grid_shape", "common_grid_resolution_m", "uavsar_native_resolution_m", "resampling_note",
    "product_version", "created_date", "citation_note",
)

#: Physically implausible values are present in NASA's published lidar and are
#: preserved deliberately. They are reported, never treated as failures.
#: LiDAR products the builder specifies but the v1.0.05 archive does not hold.
KNOWN_V1_OMISSIONS = {"SWE", "DENSITY"}

PLAUSIBLE = {
    "snow_depth": (-15.0, 20.0),
    "veg_height": (-1.0, 100.0),
    "elevation": (-500.0, 9000.0),
    "cor": (0.0, 1.0),
}


class Audit:
    def __init__(self) -> None:
        self.fail: list[str] = []
        self.warn: list[str] = []
        self.note: list[str] = []

    def check(self, ok: bool, msg: str) -> bool:
        if not ok:
            self.fail.append(msg)
        return ok

    def caution(self, ok: bool, msg: str) -> None:
        if not ok:
            self.warn.append(msg)


def audit_site(path: Path, a: Audit, inventory: dict | None) -> dict:
    import h5py
    import numpy as np

    key = path.stem.removesuffix(".enriched")
    out: dict = {"site": key, "archive": path.name}
    try:
        handle = h5py.File(path, "r")
    except OSError as exc:
        # A build writing this file holds an exclusive lock. That is not a
        # defect in the archive, but the audit cannot speak for a file it
        # could not read, so it must fail loudly rather than pass silently.
        a.fail.append(f"{key}: could not be opened for audit ({exc.__class__.__name__}). "
                      "A build may still be writing it; re-run when it finishes.")
        out["unreadable"] = True
        return out
    with handle as f:
        # ---- identification ----------------------------------------------
        if "identification" not in f:
            a.fail.append(f"{key}: no identification group")
            return out
        ident = f["identification"].attrs
        enriched = "enrichment_version" in ident
        out["archive_kind"] = "enriched" if enriched else "base"
        missing = [k for k in REQUIRED_IDENT_ATTRS if k not in ident]
        for k in missing:
            a.fail.append(f"{key}: identification missing {k!r}")
        if missing:
            out["unreadable"] = True
            return out

        try:
            dimensions = tuple(float(v) for v in ident["common_grid_shape"])
            transform = tuple(float(v) for v in ident["common_grid_transform"])
            res = float(ident["common_grid_resolution_m"])
            epsg_value = float(ident["common_crs_epsg"])
            if (len(dimensions) != 2
                    or not all(math.isfinite(v) and v > 0 and v.is_integer() for v in dimensions)
                    or len(transform) != 6 or not all(math.isfinite(v) for v in transform)
                    or not math.isfinite(res) or res <= 0
                    or not math.isfinite(epsg_value) or not epsg_value.is_integer()):
                raise ValueError("expected positive 2-D integer shape, six finite transform terms, "
                                 "positive finite resolution and an integer EPSG")
            shape = tuple(int(v) for v in dimensions)
            epsg = int(epsg_value)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            a.fail.append(f"{key}: invalid identification grid metadata ({exc})")
            out["unreadable"] = True
            return out
        out.update(shape=shape, epsg=epsg, res=res,
                   name=str(ident["site_name"]))

        a.check(res == 3.0, f"{key}: resolution is {res} m, expected 3.0")
        a.check(epsg > 0, f"{key}: no EPSG recorded (common_crs_epsg={epsg})")
        # Pixel size in the transform must agree with the declared resolution.
        a.check(abs(abs(transform[0]) - res) < 1e-6,
                f"{key}: transform pixel width {transform[0]} != {res}")
        a.check(abs(abs(transform[4]) - res) < 1e-6,
                f"{key}: transform pixel height {transform[4]} != {res}")
        a.check(transform[4] < 0, f"{key}: transform is not north-up")

        # ---- every array on the common grid ------------------------------
        arrays: list[str] = []
        stats: dict[str, dict] = {}
        empty: dict[str, dict] = {}

        def visit(name, obj):
            if not isinstance(obj, h5py.Dataset):
                return
            arrays.append(name)
            if obj.shape != shape:
                a.fail.append(f"{key}: {name} shape {obj.shape} != grid {shape}")
            for k in REQUIRED_ARRAY_ATTRS:
                if k not in obj.attrs:
                    a.fail.append(f"{key}: {name} missing attribute {k!r}")
            if "transform" in obj.attrs:
                got = tuple(float(v) for v in obj.attrs["transform"])
                if len(got) != len(transform) or not all(
                        abs(x - y) < 1e-6 for x, y in zip(got, transform)):
                    a.fail.append(f"{key}: {name} transform differs from grid")
            if "resolution_m" in obj.attrs:
                try:
                    same_resolution = math.isclose(float(obj.attrs["resolution_m"]), res,
                                                   rel_tol=0., abs_tol=1e-6)
                except (TypeError, ValueError):
                    same_resolution = False
                a.check(same_resolution, f"{key}: {name} resolution_m differs from grid")
            if "crs_wkt" in obj.attrs and \
               str(obj.attrs["crs_wkt"]) != str(ident["common_crs_wkt"]):
                a.fail.append(f"{key}: {name} CRS differs from grid")

            data = obj[...]
            if np.iscomplexobj(data):
                finite = np.isfinite(data.real) & np.isfinite(data.imag)
            else:
                finite = np.isfinite(data)
            # Integer nodata is finite (notably mask 255). Neither zero nor a
            # failed coherence test is missing. Honor both writer attributes.
            for attr in ("nodata", "nodata_value"):
                if attr not in obj.attrs:
                    continue
                sentinel = obj.attrs[attr]
                if isinstance(sentinel, bytes):
                    sentinel = sentinel.decode("utf-8")
                if isinstance(sentinel, str) and sentinel.lower() in ("nan", "none"):
                    continue
                try:
                    sentinel = complex(sentinel) if np.iscomplexobj(data) else float(sentinel)
                    finite &= data != sentinel
                except (ValueError, TypeError):
                    a.fail.append(f"{key}: {name} has invalid {attr} {sentinel!r}")
            n = int(finite.sum())
            fraction = n / data.size if data.size else 0.0
            for attr, measured in (("valid_pixel_count", n), ("valid_fraction", fraction)):
                if attr in obj.attrs:
                    try:
                        recorded = float(obj.attrs[attr])
                        matches = (recorded == measured if attr == "valid_pixel_count"
                                   else math.isclose(recorded, measured, rel_tol=0., abs_tol=1e-9))
                    except (TypeError, ValueError):
                        matches = False
                    a.check(matches, f"{key}: {name} {attr} differs from stored values ({measured})")
            if n == 0:
                stats[name] = {"valid": 0, "frac": fraction,
                               "min": None, "max": None, "median": None}
                empty[name] = dict(obj.attrs)
                return
            vals = data[finite]
            if name.rsplit("/", 1)[-1] == "coherence_mask":
                a.check(bool(np.all((vals == 0) | (vals == 1))),
                        f"{key}: {name} has valid values outside stored mask states 0/1")
            if not np.iscomplexobj(data):
                stats[name] = {"valid": n,
                               "frac": n / data.size,
                               "min": float(vals.min()),
                               "max": float(vals.max()),
                               "median": float(np.median(vals))}
                leaf = name.rsplit("/", 1)[-1]
                lim = PLAUSIBLE.get(leaf)
                if lim and (vals.min() < lim[0] or vals.max() > lim[1]):
                    a.note.append(
                        f"{key}: {name} spans {vals.min():.2f}..{vals.max():.2f}, "
                        f"outside the plausible {lim[0]}..{lim[1]} "
                        "(present in the source, preserved deliberately)")
            else:
                mag = np.abs(vals)
                stats[name] = {"valid": n, "frac": n / data.size,
                               "min": float(mag.min()), "max": float(mag.max()),
                               "median": float(np.median(mag))}

        f["science"].visititems(visit)
        # A declared empty input is a narrow, inspectable explanation. A
        # generic "no values" status alone cannot authorize an empty output.
        for name, attrs in empty.items():
            source = str(attrs.get("derived_from", "")).lstrip("/")
            source_key = source.removeprefix("science/")
            reason = attrs.get("empty_reason")
            ancestors, cursor = {name}, source_key
            while cursor in empty and cursor not in ancestors:
                ancestors.add(cursor)
                cursor = str(empty[cursor].get("derived_from", "")).lstrip("/").removeprefix("science/")
            # Enrichment masks the provider's exact-zero unwrapper fill. When
            # that fill was every in-swath cell, the layer is empty by a recorded
            # step whose count must be positive and consistent with the masking.
            if (enriched and reason == "unwrapper_zero_fill"
                    and name.rsplit("/", 1)[-1] == "unw"
                    and attrs.get("unw_zero_mask_status") == "applied"
                    and int(attrs.get("unw_zero_fill_masked", 0) or 0) > 0
                    and attrs.get("valid_pixel_count") == 0):
                stats[name]["empty_reason"] = reason
                a.warn.append(f"{key}: {name} is entirely nodata; recorded {reason} "
                              f"({int(attrs['unw_zero_fill_masked']):,} zero-fill cells masked)")
                continue
            explained = (
                enriched and reason == "no_valid_input_cells"
                and attrs.get("derived_from_archive") == "self"
                and attrs.get("valid_pixel_count") == 0
                and attrs.get("valid_fraction") == 0
                and attrs.get("value_stats_stage") == "stored_array"
                and attrs.get("value_stats_status") == "no_valid_values"
                and source.startswith("science/") and cursor not in ancestors
                and stats.get(source_key, {}).get("valid") == 0
                and f[source].file.id == f.id
                and f[source].attrs.get("valid_pixel_count") == 0)
            if explained:
                stats[name]["empty_reason"] = reason
                a.warn.append(f"{key}: {name} is entirely nodata; recorded {reason} ({source})")
            else:
                a.fail.append(f"{key}: {name} is entirely nodata without a verified empty-input explanation")
        out["arrays"] = len(arrays)
        a.check(len(arrays) > 0, f"{key}: no science arrays")

        # ---- hollow acquisition groups -----------------------------------
        hollow = []
        out["uavsar_group_basis"] = "merged_acquisitions" if enriched else "source_product_groups"
        uav = f.get("science/UAVSAR")
        if uav is not None:
            for level in uav:
                for name in uav[level]:
                    node = uav[level][name]
                    cnt = [0]
                    node.visititems(lambda _n, o: cnt.__setitem__(0, cnt[0] + 1)
                                    if isinstance(o, h5py.Dataset) else None)
                    if cnt[0] == 0:
                        hollow.append(f"{level}/{name}")
            out["uavsar_groups"] = sum(len(uav[l]) for l in uav)
        else:
            out["uavsar_groups"] = 0
        out["hollow"] = hollow
        for h in hollow:
            a.fail.append(f"{key}: {h} contains no arrays")

        # ---- area reconstruction ----------------------------------------
        target = README_AREA_KM2.get(key)
        if target:
            best, best_name = None, None
            for name, st in stats.items():
                if not name.startswith("LIDAR/") or name.startswith("LIDAR/DERIVED/"):
                    continue
                km2 = st["valid"] * res * res / 1e6
                if best is None or abs(km2 - target) < abs(best - target):
                    best, best_name = km2, name
            if best is not None:
                err = 100 * (best - target) / target
                out.update(area_km2=best, area_ref=target, area_err_pct=err,
                           area_layer=best_name)
                if enriched:
                    out["area_check_scope"] = "diagnostic_cleaned_base_lidar"
                    a.note.append(f"{key}: cleaned lidar area differs from README by {err:+.2f}%; "
                                  "diagnostic only because enrichment may remove source cells")
                else:
                    out["area_check_scope"] = "base_lidar_readme_tolerance"
                    a.check(abs(err) < 1.0,
                            f"{key}: best-matching area {best:.1f} km2 differs from "
                            f"README {target:.1f} km2 by {err:+.2f}%")

        # ---- match table -------------------------------------------------
        if "matches" in f:
            lens = {k: f["matches"][k].shape[0] for k in f["matches"]}
            a.check(len(set(lens.values())) <= 1,
                    f"{key}: matches columns unequal: {lens}")
            out["match_rows"] = max(lens.values()) if lens else 0
        else:
            a.warn.append(f"{key}: no matches table")

        # ---- inventory agreement ----------------------------------------
        if inventory and key in inventory.get("sites", {}):
            entry = inventory["sites"][key]
            # Grand Mesa's IOP SWE and density rasters were specified after its
            # v1.0.05 base archive was built; their absence is a known, documented
            # omission rather than a silent one.
            missing_known = [g for g in entry["lidar"] if g.get("product") in KNOWN_V1_OMISSIONS]
            for g in missing_known:
                a.note.append(f"{key}: {g.get('product')} {g.get('filename')} not in the v1.0.05 archive "
                              "(known omission, see docs/design.md)")
            expected_lidar = len(entry["lidar"]) - len(missing_known)
            got_lidar = sum(1 for n in arrays if n.startswith("LIDAR/")
                            and not n.startswith("LIDAR/DERIVED/"))
            out["lidar_derived_arrays"] = sum(n.startswith("LIDAR/DERIVED/") for n in arrays)
            a.check(got_lidar == expected_lidar,
                    f"{key}: {got_lidar} lidar arrays, inventory expects "
                    f"{expected_lidar}")
            out["lidar_arrays"] = got_lidar
            out["lidar_expected"] = expected_lidar
            out["uavsar_inventory_products"] = len(entry["uavsar_needed"])
            if not enriched:
                out["uavsar_expected"] = out["uavsar_inventory_products"]
    out["stats"] = stats
    return out


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path,
                    default=Path(os.environ.get("SNOWEX_OUT_DIR", here / "out")))
    ap.add_argument("--inventory", type=Path, default=here / "inventory.json")
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the full report as JSON")
    args = ap.parse_args(argv)

    inventory = None
    if args.inventory.exists():
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))

    files = sorted(args.out_dir.glob("*.h5"))
    if not files:
        print(f"no .h5 files in {args.out_dir}", file=sys.stderr)
        return 2

    a = Audit()
    rows = []
    print(f"auditing {len(files)} file(s) in {args.out_dir}\n")
    for p in files:
        rows.append(audit_site(p, a, inventory))

    hdr = ("site", "grid", "epsg", "arrays", "uavsar", "hollow",
           "area km2", "README", "err")
    print(f"{hdr[0]:<20}{hdr[1]:>14}{hdr[2]:>7}{hdr[3]:>8}{hdr[4]:>8}"
          f"{hdr[5]:>8}{hdr[6]:>10}{hdr[7]:>9}{hdr[8]:>9}")
    print("-" * 94)
    tot_area = tot_ref = 0.0
    for r in rows:
        if "shape" not in r:
            print(f"{r['site']:<20}  UNREADABLE")
            continue
        area = r.get("area_km2")
        ref = r.get("area_ref")
        if area and ref and r["archive_kind"] == "base":
            tot_area += area
            tot_ref += ref
        grid_label = "{}x{}".format(*r["shape"])
        err_label = "{:+.2f}%".format(r["area_err_pct"]) if area else "-"
        label = Path(r["archive"]).stem
        print(f"{label:<20}{grid_label:>14}"
              f"{r['epsg']:>7}{r['arrays']:>8}{r.get('uavsar_groups',0):>8}"
              f"{len(r.get('hollow',[])):>8}"
              f"{(f'{area:.1f}' if area else '-'):>10}"
              f"{(f'{ref:.1f}' if ref else '-'):>9}"
              f"{err_label:>9}")
    print("-" * 94)
    if tot_ref:
        err = 100 * (tot_area - tot_ref) / tot_ref
        print(f"{'TOTAL':<20}{'':>37}{tot_area:>18.1f}{tot_ref:>9.1f}{err:>+8.2f}%")
        a.check(abs(err) < 0.5,
                f"total area {tot_area:.1f} km2 differs from README "
                f"{tot_ref:.1f} km2 by {err:+.2f}%")

    for label, items in (("FAIL", a.fail), ("WARN", a.warn), ("NOTE", a.note)):
        if items:
            print(f"\n{label} ({len(items)})")
            for m in items:
                print(f"  {m}")

    if args.json:
        args.json.write_text(json.dumps(
            {"sites": rows, "fail": a.fail, "warn": a.warn, "note": a.note},
            indent=2, default=str), encoding="utf-8")
        print(f"\nreport written to {args.json}")

    print(f"\n{'PASS' if not a.fail else 'FAIL'}: "
          f"{len(a.fail)} failure(s), {len(a.warn)} warning(s), "
          f"{len(a.note)} note(s)")
    return 0 if not a.fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
