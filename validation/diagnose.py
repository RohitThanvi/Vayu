#!/usr/bin/env python3
"""
diagnose.py - WHY are the metrics what they are?  Works from one run's results.csv (needs a run made with the current harness/backend:
rows carry per-crop scores AND the per-factor inputs, `abl`).

  python diagnose.py validation_runs/<run_id>            # writes diagnosis.md next to results.csv

Everything is descriptive: it tells you which factor carries signal and which crops fail for which reason. It does not change the model.
Run it on a TUNE-split run when you intend to act on it; reading it off the test split uses that split up.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from metrics import cluster_bootstrap
from metrics import roc_auc

FACTORS = ["temperature", "water", "ph", "texture", "organic_carbon", "slope"]
FLOOR = 0.10
MAJOR = 0.10


def factor_scores(inp, irrigated):
    """Effective (soft-floored) factor scores, as the production aggregator sees them."""
    out = {"temperature": inp.get("temperature"), "water": inp.get("water_irrigated") if irrigated else inp.get("water_rainfed"),
           "ph": inp.get("ph"), "texture": inp.get("texture"), "organic_carbon": inp.get("organic_carbon"), "slope": inp.get("slope")}
    return {k: (None if v is None else max(float(v), FLOOR)) for k, v in out.items()}


def load(run):
    d = pd.read_csv(Path(run) / "results.csv"); d = d[(d.type == "crop_ranking") & (d.status == "ok")]
    recs = []
    for _, r in d.iterrows():
        if not isinstance(r.get("abl"), str) or not r["abl"]:
            continue
        abl = json.loads(r["abl"]); irr = bool(r.get("irrigated", 0))
        for cid, score, share in json.loads(r["pairs"]):
            if cid not in abl:
                continue
            fs = factor_scores(abl[cid], irr); known = {k: v for k, v in fs.items() if v is not None}
            lim = min(known, key=known.get) if known else None
            recs.append({"case": r["case_id"], "zone": r["zone_id"], "state": r["agro_zone"], "season": r["season"], "variant": r.get("variant", ""),
                         "crop": cid, "score": float(score), "major": int(share >= MAJOR), "share": float(share), "limiting": lim,
                         "hard": bool(abl[cid].get("temperature_hard")), **{f"f_{k}": v for k, v in fs.items()}})
    return pd.DataFrame(recs)


def boot_auc(df, col):
    d = df.dropna(subset=[col]);
    if d.major.nunique() < 2: return None, None
    y, s, z = d.major.values, d[col].values, d.zone.values
    ci = cluster_bootstrap(lambda i: roc_auc(y[i], s[i]), z, n_boot=500)
    return roc_auc(y, s), ci


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run_dir"); a = ap.parse_args()
    df = load(a.run_dir)
    if df.empty:
        sys.exit("No rows with per-factor inputs (`abl`). Re-run with the current harness against the current backend (or use --replay).")
    man = json.loads((Path(a.run_dir) / "manifest.json").read_text())
    L = [f"# Diagnosis - run {man['run_id']} ({man.get('split')} split, backend {man.get('backend_engine_version')}, harness {man.get('model_version')})", "",
         f"{len(df)} (case x crop) pairs, {df.case.nunique()} cases, {df.zone.nunique()} zones. 'Major' = crop holds >= {MAJOR:.0%} of observed modelled-crop area.",
         "Scores are suitability (can the land support it), the label is adoption (is it widely grown), so some disagreement is expected.", ""]

    L += ["## 1. Which factor carries signal? (AUC vs observed-major; 0.5 = no information, < 0.5 = pushes the wrong way)", "",
          "| factor | AUC | 95% CI (zones) | mean score when major | mean score when not major |", "|---|---|---|---|---|"]
    for col in ["score"] + [f"f_{f}" for f in FACTORS]:
        auc, ci = boot_auc(df, col)
        if auc is None: continue
        d = df.dropna(subset=[col])
        L.append(f"| {col.replace('f_', '')} | {auc:.3f} | {('%.3f-%.3f' % tuple(ci)) if ci else 'n/a'} | {d[d.major == 1][col].mean():.2f} | {d[d.major == 0][col].mean():.2f} |")
    L += ["", "A factor near or below 0.5 is not helping discriminate; a factor with a low score for MAJOR crops is the one rejecting crops farmers actually grow.", ""]

    L += ["## 2. Per crop: over- or under-rated?", "", "| crop | n major | mean score if major | mean score if not | recall at 0.5 | in model top-3 | in observed top-3 |", "|---|---|---|---|---|---|---|"]
    top3 = df.assign(rk=df.groupby("case").score.rank(ascending=False, method="min")); top3["m3"] = top3.rk <= 3
    obs3 = df.assign(rk=df.groupby("case").share.rank(ascending=False, method="min")); obs3["o3"] = (obs3.rk <= 3) & (obs3.share > 0)
    rows = []
    for crop, g in df.groupby("crop"):
        mj = g[g.major == 1]
        rows.append((crop, len(mj), mj.score.mean() if len(mj) else np.nan, g[g.major == 0].score.mean(),
                     (mj.score >= 0.5).mean() if len(mj) else np.nan, top3[top3.crop == crop].m3.mean(), obs3[obs3.crop == crop].o3.mean()))
    for crop, n, a1, a0, rc, m3, o3 in sorted(rows, key=lambda x: -x[1]):
        L.append(f"| {crop} | {n} | {a1:.2f} | {a0:.2f} | {('%.2f' % rc) if rc == rc else '-'} | {m3:.2f} | {o3:.2f} |")
    L += ["", "'in model top-3' far above 'in observed top-3' = over-ranked (e.g. a crop that CAN grow but is not what farmers grow); far below = under-ranked.", ""]

    L += ["## 3. Why are widely grown crops rated < 0.5? (limiting factor of the misses)", ""]
    miss = df[(df.major == 1) & (df.score < 0.5)]
    if miss.empty:
        L.append("No observed-major crop is rated below 0.5.")
    else:
        L += ["| crop | misses | limiting factors (count) |", "|---|---|---|"]
        for crop, g in miss.groupby("crop"):
            L.append(f"| {crop} | {len(g)} / {len(df[(df.crop == crop) & (df.major == 1)])} | " + ", ".join(f"{k}: {v}" for k, v in g.limiting.value_counts().items()) + " |")
        L += ["", "Overall: " + ", ".join(f"{k}: {v}" for k, v in miss.limiting.value_counts().items()) + f"  (hard temperature limit in {int(miss.hard.sum())})", ""]

    L += ["## 4. Why are crops that are NOT major rated >= 0.5? (false positives by crop)", "", "| crop | false positives | share of its non-major cases |", "|---|---|---|"]
    for crop, g in df[df.major == 0].groupby("crop"):
        fp = (g.score >= 0.5)
        if fp.sum(): L.append(f"| {crop} | {int(fp.sum())} | {fp.mean():.2f} |")
    L += ["", "False positives are partly expected (a crop can be suitable and still not grown). Persistently high ones for crops with no regional tradition point at missing evidence "
          "(humidity / disease, soil depth, market) rather than at a range that can be tuned.", ""]

    L += ["## 5. By season and state", "", "| group | n pairs | AUC | recall at 0.5 | false-positive rate at 0.5 |", "|---|---|---|---|---|"]
    for name, key in (("season", "season"), ("state", "state")):
        for g, d in df.groupby(key):
            auc = roc_auc(d.major.values, d.score.values) if d.major.nunique() > 1 else None
            rc = (d[d.major == 1].score >= 0.5).mean() if (d.major == 1).any() else None
            fpr = (d[d.major == 0].score >= 0.5).mean() if (d.major == 0).any() else None
            f = lambda v: "-" if v is None or v != v else f"{v:.2f}"
            L.append(f"| {name}={g} | {len(d)} | {f(auc)} | {f(rc)} | {f(fpr)} |")
    out = Path(a.run_dir) / "diagnosis.md"; out.write_text("\n".join(L) + "\n", encoding="utf-8"); print("\n".join(L)); print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
