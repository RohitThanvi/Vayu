#!/usr/bin/env python3
"""
learn_combination.py - does a LEARNED MONOTONE combination of the six factor scores beat the current min (Liebig) rule?  TUNE split only.

  python learn_combination.py --profiles validation_runs/_profiles_v2 [--split tune] [--boot 500]

Design (so the result can be trusted):
  * Features = the engine's own per-factor EFFECTIVE scores for each (case, crop) - temperature, water, ph, texture, organic_carbon, slope.
    No crop identity, no regional popularity: Vayu must stay an environment-only screen, so a per-crop intercept is deliberately NOT allowed.
  * Models are monotone by construction: score = sigmoid(b + sum_k w_k * log(x_k)), w_k >= 0, so a better factor can never lower the score.
    Unknown factors contribute log(1) = 0 (neutral), the same as the min rule ignoring them.
  * The hard temperature limit is kept outside the learned part: a hard-limited crop scores 0 under every model.
  * Evaluation is LEAVE-ONE-STATE-OUT: every case is scored by a model that never saw its state. Metrics come only from these out-of-fold scores.
  * Models compared: min (current engine rule), geomean (equal weights, nothing fitted), learned (L2-regularised, w >= 0).
  * Paired zone-bootstrap CI on the pooled-AUC and within-crop-AUC difference vs min.
Everything printed is descriptive and from the development set; it does NOT justify quoting a number externally. Adopt a learned rule only
if it wins here AND then wins once on untouched test states.
"""
import argparse, json, sys
from pathlib import Path
import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "backend"))
from client import ReplayClient                       # noqa: E402
from validators import build_request                  # noqa: E402
from metrics import roc_auc                           # noqa: E402

FACTORS = ["temperature", "water", "ph", "texture", "organic_carbon", "slope"]
EPS = 1e-3


def build_rows(cases, client):
    rows = []
    for c in cases:
        t = c["truth"]; season = t["season"]
        try:
            resp = client.call("crop_suitability", {"include_revenue": False, "season": season, **build_request(c, HERE)}, False)
        except Exception as e:                        # noqa: BLE001
            print("skip", c["case_id"], e); continue
        shares = t.get("mean_area_share", {})
        gt = list(t["gt_crops"])
        crops = [x for x in resp["crops"] if x.get("score") is not None and x.get("season") == season]
        for x in crops:
            f = x["factors"]
            feats = [f[k]["effective_score"] for k in FACTORS]
            rows.append(dict(case=c["case_id"], zone=c["zone_id"], state=c["agro_zone"], variant=c["variant"], season=season,
                             crop=x["crop_id"], hard=int(f["temperature"]["severity"] == "hard"), score_min=float(x["score"]),
                             y=int(float(shares.get(x["crop_id"], 0.0)) >= 0.10), gt=gt, k=int(t.get("k", 3)),
                             x=[np.nan if v is None else float(v) for v in feats]))
    return rows


def logX(rows):
    X = np.array([r["x"] for r in rows], float)
    X = np.where(np.isnan(X), 1.0, X)                 # unknown -> neutral
    return np.log(np.clip(X, EPS, 1.0))


def fit_monotone(L, y, l2=1.0):
    """sigmoid(b + L @ w), w >= 0, L2 on w. Returns (b, w)."""
    n, d = L.shape

    def nll(p):
        b, w = p[0], p[1:]
        z = b + L @ w
        ll = np.logaddexp(0, z) - y * z
        g = 1 / (1 + np.exp(-z)) - y
        return ll.mean() + l2 * (w @ w) / (2 * n) * 10, np.concatenate([[g.mean()], (L * g[:, None]).mean(0) + l2 * w / n * 10])
    p0 = np.concatenate([[0.0], np.full(d, 0.3)])
    r = minimize(nll, p0, jac=True, method="L-BFGS-B", bounds=[(None, None)] + [(0, None)] * d)
    return r.x[0], r.x[1:]


def predict(L, hard, b, w):
    s = 1 / (1 + np.exp(-(b + L @ w)))
    return np.where(hard == 1, 0.0, s)


def within_crop_auc(y, s, crop):
    out = []
    for c in sorted(set(crop)):
        m = crop == c
        if y[m].sum() >= 3 and (1 - y[m]).sum() >= 3:
            out.append(roc_auc(y[m], s[m]))
    return float(np.mean(out)) if out else float("nan")


def top_k_overlap(rows, s, idx_by_case):
    v = []
    for cid, idx in idx_by_case.items():
        r0 = rows[idx[0]]
        known = {rows[i]["crop"] for i in idx}
        truth = [g for g in r0["gt"] if g in known][: r0["k"]]
        if not truth: continue
        sc = s[idx]
        order = np.argsort(-sc, kind="stable")
        thr = sc[order[min(r0["k"], len(idx)) - 1]]
        sure = [i for i in idx if s[i] > thr]; tied = [i for i in idx if s[i] == thr]
        need = r0["k"] - len(sure)
        tr = set(truth)
        exp = sum(rows[i]["crop"] in tr for i in sure) + (need * sum(rows[i]["crop"] in tr for i in tied) / len(tied) if tied and need > 0 else 0)
        v.append(exp / min(r0["k"], len(truth)))
    return float(np.mean(v)) if v else float("nan")


def evaluate(rows, s, variant):
    ix = [i for i, r in enumerate(rows) if r["variant"] == variant]
    y = np.array([rows[i]["y"] for i in ix]); sc = s[ix]; cr = np.array([rows[i]["crop"] for i in ix])
    by = {}
    for j, i in enumerate(ix): by.setdefault(rows[i]["case"], []).append(i)
    return dict(auc=roc_auc(y, sc), within=within_crop_auc(y, sc, cr), top3=top_k_overlap(rows, s, by),
                ties=float(np.mean([len(set(np.round(s[v], 6))) < len(v) for v in by.values()])))


def boot_diff(rows, s_a, s_b, variant, B, seed=0):
    zones = sorted({r["zone"] for r in rows if r["variant"] == variant})
    rng = np.random.default_rng(seed); da, dw = [], []
    for _ in range(B):
        pick = rng.choice(zones, len(zones), replace=True)
        ix = [i for z in pick for i in zone_idx[(variant, z)]]
        y = np.array([rows[i]["y"] for i in ix]); cr = np.array([rows[i]["crop"] for i in ix])
        if y.sum() == 0 or y.sum() == len(y): continue
        da.append(roc_auc(y, s_a[ix]) - roc_auc(y, s_b[ix])); dw.append(within_crop_auc(y, s_a[ix], cr) - within_crop_auc(y, s_b[ix], cr))
    q = lambda a: [round(float(np.nanpercentile(a, 2.5)), 3), round(float(np.nanpercentile(a, 97.5)), 3)]
    return q(da), q(dw)


def main():
    global zone_idx
    ap = argparse.ArgumentParser(); ap.add_argument("--profiles", required=True); ap.add_argument("--cases", default=str(HERE / "cases.json"))
    ap.add_argument("--split", default="tune", choices=["tune"]); ap.add_argument("--boot", type=int, default=300); ap.add_argument("--l2", type=float, default=1.0)
    a = ap.parse_args()
    cases = [c for c in json.loads(Path(a.cases).read_text(encoding="utf-8"))["cases"] if c["type"] == "crop_ranking" and c["split"] == "tune"]
    rows = build_rows(cases, ReplayClient(a.profiles))
    L = logX(rows); y = np.array([r["y"] for r in rows]); hard = np.array([r["hard"] for r in rows])
    states = np.array([r["state"] for r in rows]); S = sorted(set(states))
    zone_idx = {}
    for i, r in enumerate(rows): zone_idx.setdefault((r["variant"], r["zone"]), []).append(i)
    print(f"{len(rows)} (case,crop) rows, {len(S)} states, {len({r['zone'] for r in rows})} zones, positives {y.mean():.2f}")

    s_min = np.array([r["score_min"] for r in rows])
    s_geo = np.where(hard == 1, 0.0, np.exp(L.mean(1)))                          # true geometric mean of the six effective factor scores
    s_hyb = np.where(hard == 1, 0.0, np.sqrt(np.clip(s_min, 0, 1) * s_geo))      # Liebig-tempered: geometric mean of (min rule, geomean)
    s_cv = np.zeros(len(rows)); coefs = []
    for st in S:                                       # leave-one-state-out
        tr, te = states != st, states == st
        b, w = fit_monotone(L[tr & (hard == 0)], y[tr & (hard == 0)], a.l2); coefs.append(w)
        s_cv[te] = predict(L[te], hard[te], b, w)
    b_all, w_all = fit_monotone(L[hard == 0], y[hard == 0], a.l2)
    print("\nlearned weights (all tune, w>=0):  " + "  ".join(f"{k}={v:.2f}" for k, v in zip(FACTORS, w_all)))
    print("fold-to-fold weight sd:             " + "  ".join(f"{k}={v:.2f}" for k, v in zip(FACTORS, np.std(coefs, 0))))
    print(f"\n{'variant':10s}{'model':10s}{'AUC':>7s}{'within':>8s}{'top3':>7s}{'ties':>7s}")
    for v in ("irrigated", "auto", "rainfed"):
        for name, s in (("min", s_min), ("geomean", s_geo), ("hybrid", s_hyb), ("learned", s_cv)):
            m = evaluate(rows, s, v)
            ix = np.array([i for i, r in enumerate(rows) if r["variant"] == v]); yy = y[ix].astype(bool); ss = s[ix]
            print(f"{v:10s}{name:10s}{m['auc']:7.3f}{m['within']:8.3f}{m['top3']:7.3f}{m['ties']:7.2f}   recall@.5 {np.mean(ss[yy] >= .5):.2f} fpr@.5 {np.mean(ss[~yy] >= .5):.2f}")
        ixv = np.array([i for i, r in enumerate(rows) if r["variant"] == v])
        for name, s in (("geomean", s_geo), ("hybrid", s_hyb)):
            w = n = 0
            for st in S:
                m_ = ixv[states[ixv] == st]; yy = y[m_]
                if 0 < yy.sum() < len(yy): n += 1; w += roc_auc(yy, s[m_]) > roc_auc(yy, s_min[m_])
            print(f"{'':10s}{name}: higher pooled AUC than min in {w}/{n} states")
        for name, s in (("geomean", s_geo), ("hybrid", s_hyb), ("learned", s_cv)):
            da, dw = boot_diff(rows, s, s_min, v, a.boot)
            print(f"{'':10s}{name}-min  AUC diff 95% zone-bootstrap CI {da}   within-crop diff CI {dw}")
    pass   # weights are printed above; nothing is written to disk


if __name__ == "__main__":
    main()
