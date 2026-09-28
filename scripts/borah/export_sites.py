"""Export explorers from the 3.3.0 candidate archives and check them against
native blocks. Archives are opened read-only; pages go to a staging folder.

    python export_sites.py <stage_dir> <viewer_stage_dir> site [site ...]
    python export_sites.py <stage_dir> <viewer_stage_dir> --index

The native-block check is the one used for the 2026-09-21 rebuild
(tmp/explorer_rebuild_20260921/validate_export.py): for each product family it
recomputes sampled display cells from the archive and requires the packed code
to match within one quantisation step.
"""
import base64, hashlib, json, sys, time
from pathlib import Path
from types import SimpleNamespace

CODE = Path("/bsuhome/anantgahlaut/snowex-atlas")
sys.path.insert(0, str(CODE))
import h5py, numpy as np
from make_explorer import build_site, write_index
from explorer_addon import PAYLOAD
from build_provenance import file_identity

SITES = ["banner_summit", "cameron_pass", "dry_creek", "fraser", "grand_mesa",
         "little_cottonwood", "mores_creek", "reynolds_creek"]
sha = lambda b: hashlib.sha256(b).hexdigest()


def codes(a):
    q = np.frombuffer(base64.b64decode(a["b64"], validate=True),
                      dtype="<u2" if a["bits"] == 16 else "u1")
    assert q.size == a["w"] * a["h"], "Packed grid size"
    return q.reshape(a["h"], a["w"])


def sample_math(handle, payload):
    targets = {}
    for key, a in payload["arrays"].items():
        leaf = a["leaf"]
        family = "forest_cover_fraction" if leaf.startswith("forest_cover_fraction") else leaf
        targets.setdefault(family, (key, a))
    n = 0
    for family, (key, a) in targets.items():
        ds = handle[a["source"]]
        q = codes(a)
        step = round(a["cell_m"] / payload["grid"]["res_m"])
        positions = {0, q.size - 1, q.size // 2}
        finite = np.flatnonzero(q)
        if len(finite):
            positions.update(int(finite[i]) for i in
                             {0, len(finite) // 3, 2 * len(finite) // 3, len(finite) - 1})
        empty = np.flatnonzero(q == 0)
        if len(empty):
            positions.add(int(empty[len(empty) // 2]))
        for pos in sorted(positions):
            y, x = divmod(pos, a["w"])
            raw = ds[y * step:(y + 1) * step, x * step:(x + 1) * step]
            good = np.isfinite(raw)
            nodata = ds.attrs.get("nodata", ds.attrs.get("nodata_value"))
            if nodata is not None and not np.iscomplexobj(raw):
                try:
                    s = float(np.asarray(nodata).reshape(-1)[0])
                    if np.isfinite(s):
                        good &= raw != s
                except (TypeError, ValueError, IndexError):
                    pass
            vals = raw[good]
            v = np.nan
            if vals.size:
                if family == "aspect":
                    vct = np.exp(1j * np.deg2rad(vals.astype("float64") % 360)).mean()
                    if abs(vct) > 1e-12:
                        v = float(np.angle(vct, deg=True) % 360)
                    if v >= 360:
                        v = 0.0
                elif np.iscomplexobj(vals):
                    c = vals.astype("complex128")
                    v = float(np.abs(c).mean() if "|magnitude|" in family else np.angle(c.mean()))
                else:
                    v = float(vals.astype("float64").mean())
            got = int(q.flat[pos])
            if not np.isfinite(v):
                assert got == 0, (key, pos, "missing reference", got)
            else:
                assert got != 0, (key, pos, "unexpected missing output")
                span = a["hi"] - a["lo"] or 1.0
                want = 1 + round(float(np.clip((v - a["lo"]) / span, 0, 1)) * ((1 << a["bits"]) - 2))
                assert abs(got - want) <= 1, (key, pos, got, want, v)
            n += 1
    return {"product_families": len(targets), "native_block_samples": n}


def main(argv):
    stage, out = Path(argv[0]), Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    # Names come from the finished base archives: an enriched candidate may
    # still be open in a writer and must not be read until its job ends.
    roster = []
    for k in SITES:
        with h5py.File(stage / f"{k}.h5", "r") as f:
            name = f["identification"].attrs.get("site_name", k)
        roster.append({"key": k, "name": name.decode() if isinstance(name, bytes) else str(name),
                       "file": f"{k}_explorer.html"})
    if argv[2:] == ["--index"]:
        summaries = [json.loads((out / f"{k}.summary.json").read_text())["summary"] for k in SITES]
        write_index(out, summaries, time.strftime("%Y-%m-%d %H:%M"))
        print("index written;", sum(s["layers"] for s in summaries), "layers")
        return 0
    args = SimpleNamespace(terrain_stride=None, stride=None, with_insitu=False,
                           viewer_dir=out, template=CODE / "explorer_template.html")
    rc = 0
    for k in argv[2:]:
        src = stage / f"{k}.enriched.h5"
        before = file_identity(src)
        t = time.monotonic()
        code, summary = build_site(k, src, args, roster)
        if code:
            print(k, "export returned", code); rc = 1; continue
        page = out / f"{k}_explorer.html"
        payload = json.loads(PAYLOAD.search(page.read_text(encoding="utf-8"))[1])
        with h5py.File(src, "r") as f:
            check = sample_math(f, payload)
        assert file_identity(src) == before, "archive changed during export"
        rec = {"summary": summary, "check": check, "seconds": round(time.monotonic() - t, 1),
               "sha256": sha(page.read_bytes()), "input": before}
        (out / f"{k}.summary.json").write_text(json.dumps(rec, indent=2))
        print(k, "OK", summary["layers"], "layers,", check, flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
