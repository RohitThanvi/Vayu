#!/usr/bin/env python3
"""
experiment.py - compare engine VARIANTS on saved profiles, offline and in seconds. Use the TUNE split only.

  python experiment.py --profiles validation_runs/_profiles_v2 --split tune \\
      --variant base \\
      --variant slope50:engine_config.SLOPE_MIN_SCORE=0.5 \\
      --variant slope100:engine_config.SLOPE_MIN_SCORE=1.0 \\
      --variant mai:water_balance.MAI_ZERO=0.2,water_balance.MAI_FULL=0.6

A variant is  name:module.KEY=value,module.KEY=value  (module = engine_config or water_balance; bare KEY means engine_config). The base code is whatever this
checkout contains. Every printed number is descriptive: pick a variant only if it has an agronomic rationale AND improves the tune split without hurting any
state group badly, then confirm once on untouched test states. Many variants on one split WILL overfit it, so keep the list short.
"""
import argparse, json, sys, importlib
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "backend"))
from client import ReplayClient                                   # noqa: E402
from validators import VALIDATORS                                 # noqa: E402
from metrics import roc_auc                                       # noqa: E402

HILL_STATES = ("Himachal Pradesh", "Sikkim", "Uttarakhand", "Meghalaya", "Nagaland", "Mizoram", "Arunachal Pradesh", "Jammu and Kashmir")


import math


def make_profile_transform(min_cropland):
    """Re-derive the slope the sampler would have used if _MIN_CROPLAND_FRACTION were `min_cropland` (uses the saved all-land and cropland slopes)."""
    def tf(p):
        p = dict(p); cf, sc, sa = p.get("cropland_fraction"), p.get("slope_cropland_deg"), p.get("slope_all_deg")
        use = sc is not None and cf is not None and cf >= min_cropland
        d = sc if use else sa
        if d is not None:
            p["slope_deg"] = round(d, 1); p["slope_pct"] = round(math.tan(math.radians(d)) * 100, 1)
        p["slope_basis"] = "cropland" if use else "all land"
        return p
    return tf


def parse_variant(spec):
    name, _, kv = spec.partition(":")
    sets = []
    for item in filter(None, kv.split(",")):
        k, _, v = item.partition("="); mod, _, key = k.rpartition("."); sets.append((mod or "engine_config", key, float(v)))
    return name, sets


def apply(sets):
    saved = []
    for mod, key, val in sets:
        if mod == "profile":
            continue
        m = importlib.import_module(f"app.services.agri.{mod}"); saved.append((m, key, getattr(m, key))); setattr(m, key, val)
    return saved


def run(cases, client, base_dir):
    rows = [VALIDATORS["crop_ranking"].run_case(c, client, base_dir, use_cache=False) for c in cases]
    return [r for r in rows if r["status"] == "ok"], sum(r["status"] != "ok" for r in rows)


def metrics(rows):
    out = {}
    for variant in sorted({r.get("variant", "") for r in rows}):
        g = [r for r in rows if r.get("variant", "") == variant]
        y, s, st, cr, state_rows = [], [], [], [], []
        for r in g:
            for cid, score, share in json.loads(r["pairs"]):
                y.append(int(share >= 0.10)); s.append(score); st.append(r["agro_zone"]); cr.append(cid)
        y, s, st = np.array(y), np.array(s), np.array(st)
        mj = y == 1
        hill = np.isin(st, HILL_STATES)
        # within-crop mean AUC (crops with >=3 major and >=3 non-major)
        wc = []
        for c in set(cr):
            m = np.array(cr) == c
            if y[m].sum() >= 3 and (1 - y[m]).sum() >= 3: wc.append(roc_auc(y[m], s[m]))
        out[variant or "all"] = {"n_pairs": len(y), "auc_pooled": round(roc_auc(y, s), 3), "auc_within_crop": round(float(np.mean(wc)), 3) if wc else None,
                                 "recall@0.5": round(float((s[mj] >= 0.5).mean()), 3), "fpr@0.5": round(float((s[~mj] >= 0.5).mean()), 3),
                                 "recall_hill": round(float((s[mj & hill] >= 0.5).mean()), 3) if (mj & hill).any() else None,
                                 "recall_plain": round(float((s[mj & ~hill] >= 0.5).mean()), 3) if (mj & ~hill).any() else None,
                                 "overlap_top3": round(float(np.mean([r["overlap_frac"] for r in g])), 3)}
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--profiles", required=True); ap.add_argument("--cases", default=str(HERE / "cases.json"))
    ap.add_argument("--split", default="tune", choices=["tune", "test", "all"]); ap.add_argument("--variant", action="append", default=["base"])
    a = ap.parse_args()
    if a.split != "tune": print("WARNING: experimenting on a non-tune split uses it up. Do not tune on test data.")
    cases = [c for c in json.loads(Path(a.cases).read_text(encoding="utf-8"))["cases"] if c["type"] == "crop_ranking" and (a.split == "all" or c["split"] == a.split)]
    client = ReplayClient(a.profiles)
    results = {}
    for spec in (["base"] if "base" not in a.variant else []) + a.variant:
        name, sets = parse_variant(spec)
        if name in results: continue
        saved = apply(sets)
        client.profile_transform = next((make_profile_transform(v) for m, k, v in sets if m == "profile" and k == "min_cropland"), None)
        try:
            rows, failed = run(cases, client, HERE)
            results[name] = (metrics(rows), failed, len(rows))
        finally:
            for m, k, v in saved: setattr(m, k, v)
    cols = ["auc_pooled", "auc_within_crop", "recall@0.5", "fpr@0.5", "recall_hill", "recall_plain", "overlap_top3"]
    for variant in sorted({v for r in results.values() for v in r[0]}):
        print(f"\n== {variant}   (cases scored: " + ", ".join(f"{n}: {r[2]}" for n, r in results.items()) + ")")
        print(f"{'variant':16s} " + " ".join(f"{c:>15s}" for c in cols))
        for name, (m, failed, n) in results.items():
            if variant in m: print(f"{name:16s} " + " ".join(f"{str(m[variant][c]):>15s}" for c in cols))


if __name__ == "__main__":
    main()
