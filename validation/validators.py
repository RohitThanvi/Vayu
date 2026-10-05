"""
validators.py - one validator per KIND of ground truth. Each has:
    run_case(case, client, base_dir) -> row dict   (never raises; failures become status='error: ...')
    summarize(rows, ctx)             -> dict of metrics (+ optional '_matrix' for confusion output)
A case is a JSON object (see cases.example.json). Common keys:
    case_id, zone_id, agro_zone, split ("tune"|"test"), type, endpoint, aoi (path to GeoJSON), request{}, truth{}, source
"""
import csv, json, re
from datetime import date
from pathlib import Path
import numpy as np
from metrics import (regression_metrics, classification_from_matrix, classification_metrics,
                     binary_metrics, ranking_row, cluster_bootstrap)
import ranking_metrics as RM
from ablation import STAGES, stage_scores

CROP_IDS = ["wheat", "rice_paddy", "bajra_pearl_millet", "jowar_sorghum", "maize", "barley", "gram_chickpea",
            "mustard", "groundnut", "soybean", "cotton", "sugarcane", "potato", "onion", "lemon", "guava",
            "amla_gooseberry", "mango", "avocado"]


# ------------------------------------------------------------------ helpers
def get_path(obj, path):
    """'indices.ndvi.mean' | 'classes[label=Cropland].pct_of_aoi' | 'points[0].value'. Returns None if absent."""
    cur = obj
    for tok in re.findall(r"[^.\[\]]+|\[[^\]]+\]", path):
        if cur is None:
            return None
        if tok.startswith("["):
            inner = tok[1:-1]
            if "=" in inner:
                k, v = inner.split("=", 1)
                cur = next((x for x in (cur or []) if str(x.get(k)) == v), None)
            else:
                try: cur = cur[int(inner)]
                except Exception: return None
        else:
            cur = cur.get(tok) if isinstance(cur, dict) else None
    return cur


def load_aoi(case, base_dir):
    p = case.get("aoi")
    if not p:
        return None
    return json.loads((Path(base_dir) / p).read_text(encoding="utf-8"))


def build_request(case, base_dir):
    req = dict(case.get("request", {}))
    aoi = load_aoi(case, base_dir)
    if aoi is not None:
        req["aoi_geojson"] = aoi
    return req


def base_row(case):
    return {"case_id": case["case_id"], "zone_id": case.get("zone_id", case["case_id"]),
            "agro_zone": case.get("agro_zone", ""), "split": case.get("split", "test"),
            "type": case["type"], "endpoint": case["endpoint"], "source": case.get("source", ""),
            "variant": case.get("variant", ""), "status": "ok"}


def safe(fn):
    def wrapper(self, case, client, base_dir, use_cache=True):
        row = base_row(case)
        try:
            row.update(fn(self, case, client, base_dir, use_cache))
        except Exception as e:
            row["status"] = f"error: {type(e).__name__}: {e}"[:300]
        return row
    return wrapper


def ok_rows(rows):
    return [r for r in rows if r["status"] == "ok"]


def ci_str(ci):
    return None if ci is None else [round(ci[0], 4), round(ci[1], 4)]


def req_irr(case):
    return case.get("request", {}).get("irrigation_available", False)


# ------------------------------------------------------------ 1. crop ranking
class CropRanking:
    """Vayu ranks 19 crops by suitability; truth = observed crops by sown-area share (largest first)."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        t = case["truth"]; season = t["season"]; k = int(t.get("k", 3))
        resp = client.call("crop_suitability", {"include_revenue": False, "season": season, **build_request(case, base_dir)}, use_cache)
        crops = [c for c in resp["crops"] if c.get("score") is not None
                 and (c.get("season") == season or (t.get("include_perennials") and c.get("season") == "perennial"))]
        # `season` is sent so the engine ranks every crop GROWN in that season by that season's score (older backends ignore it).
        # A backend that advertises the seasonal engine must echo the season back; otherwise the comparison would silently be wrong.
        if "seasonal_v1" in (resp.get("engine_features") or []) and resp.get("season_requested") != season:
            raise ValueError(f"backend returned season_requested={resp.get('season_requested')!r} for a {season!r} case (harness/backend mismatch)")
        pred = [c["crop_id"] for c in crops]
        known = lambda L: [c for c in L if c in CROP_IDS]
        truth, base = known(t["gt_crops"]), known(t.get("baseline_crops", []))
        m = ranking_row(pred, truth, k)
        if m is None:
            raise ValueError("no overlap between modelled crops and ground-truth crops for this season")
        b = ranking_row(base, truth, k) if base else None
        # tie awareness: how many of the model's top-k share a score with the crop just outside the top-k
        top_scores = [round(c["score"], 3) for c in crops[:k + 1]]
        tied = len(top_scores) > k and top_scores[k - 1] == top_scores[k]
        shares = t.get("mean_area_share", {})
        pairs = [[c["crop_id"], c["score"], float(shares.get(c["crop_id"], 0.0))]
                 for c in crops if c.get("season") == season]
        abl = {c["crop_id"]: c["ablation_inputs"] for c in crops if c.get("season") == season and c.get("ablation_inputs")}
        cal = str(resp.get("calibration_status", "unknown"))
        return {"pred": "|".join(pred[:k]), "truth": "|".join(truth[:k]), "k": k, **m,
                "baseline_overlap_frac": None if b is None else b["overlap_frac"], "tie_at_cutoff": int(tied),
                "season": season, "top1_pred": pred[0] if pred else "", "top1_truth": truth[0] if truth else "",
                "pairs": json.dumps(pairs), "baseline_list": "|".join(base), "irrigated": int(bool(resp.get("irrigation_available", req_irr(case)))),
                "irrigation_auto": int(bool(resp.get("irrigation_auto", False))),
                "calibrated": int(cal.startswith("fitted")), "abl": json.dumps(abl) if abl else "",
                "engine_version": resp.get("engine_version", "pre-versioning"), "n_crops_ranked": len(crops)}

    def summarize(self, rows, ctx):
        """Separate metric blocks per `variant` (e.g. irrigated vs rainfed) so conditions are never pooled."""
        variants = sorted({r.get("variant", "") for r in rows})
        if variants == [""]:
            return self._summ(rows)
        return {f"variant={v or 'default'}": self._summ([r for r in rows if r.get("variant", "") == v]) for v in variants}

    def _summ(self, rows):
        r = ok_rows(rows); out = {"n_cases": len(rows), "n_ok": len(r)}
        if not r: return out
        for key in ("model_top1_in_truth_topk", "truth_top1_in_model_topk", "overlap_frac", "baseline_overlap_frac"):
            v = np.array([x[key] for x in r if x.get(key) is not None], float)
            if len(v):
                zz = [x["zone_id"] for x in r if x.get(key) is not None]
                out[key] = {"mean": round(float(v.mean()), 4), "ci95_zone_bootstrap": ci_str(
                    cluster_bootstrap(lambda idx, v=v: v[idx].mean(), zz))}
        out["share_cases_with_tie_at_topk_cutoff"] = round(float(np.mean([x["tie_at_cutoff"] for x in r])), 3)
        by = {}
        for x in r: by.setdefault(x["agro_zone"] or "unspecified", []).append(x["overlap_frac"])
        out["overlap_by_agro_zone"] = {k: {"n": len(v), "mean": round(float(np.mean(v)), 3)} for k, v in by.items()}
        out.update(self._classification(r))
        return out

    SUITABLE_SCORE = 0.5       # Vayu "suitable" (rating moderate or better)
    MAJOR_SHARE = 0.10         # observed "major crop" = >=10% of modelled-crop area

    def _classification(self, r):
        """Suitability as yes/no per (district x season x crop) + top-1 confusion matrices per season."""
        out = {}
        Y, S, Z, C = [], [], [], []
        for x in r:
            for cid, score, share in json.loads(x["pairs"]):
                Y.append(int(share >= self.MAJOR_SHARE)); S.append(float(score)); Z.append(x["zone_id"]); C.append(cid)
        if Y:
            Y, S, Z, C = np.array(Y), np.array(S), np.array(Z), np.array(C)
            b = binary_metrics(Y, S, self.SUITABLE_SCORE)
            b = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items()}
            b["definition"] = f"observed major crop = share >= {self.MAJOR_SHARE}; Vayu suitable = score >= {self.SUITABLE_SCORE}"
            b["recall_ci95_zone_bootstrap"] = ci_str(cluster_bootstrap(
                lambda i: float((S[i][Y[i] == 1] >= self.SUITABLE_SCORE).mean()), list(Z)))
            b["roc_auc_ci95_zone_bootstrap"] = ci_str(cluster_bootstrap(
                lambda i: binary_metrics(Y[i], S[i], self.SUITABLE_SCORE)["roc_auc"], list(Z)))
            b["recall_by_crop"] = {c: f"{int((S[(C == c) & (Y == 1)] >= self.SUITABLE_SCORE).sum())}/{int(((C == c) & (Y == 1)).sum())}"
                                   for c in sorted(set(C[Y == 1]))}
            out["suitability_binary"] = b
        out.update(self._ranking_blocks(r))
        for season in sorted({x["season"] for x in r}):
            g = [x for x in r if x["season"] == season and x["top1_truth"] and x["top1_pred"]]
            if len(g) >= 3:
                m, M, labels = classification_metrics([x["top1_truth"] for x in g], [x["top1_pred"] for x in g])
                m = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
                m["_matrix"] = {"labels": labels, "rows_true_cols_pred": M.tolist()}
                out[f"top1_confusion_{season}"] = m
        return out


    # ------------------------------------------------------------------------------------------
    BASELINES = {
        "state_prior": "crops ranked by their share of sown area in the REST of the same state (district itself left out). "
                       "Uses observed statistics that Vayu does NOT see, so it is a deliberately strong informed prior.",
        "random": "no information: every crop tied; metrics are the expectation under uniformly random tie-breaking "
                  "(analytic, not simulated).",
        "climate_only": "Vayu with only temperature + rainfall amount (ablation stage 1): the same inputs, minus soil/water/irrigation logic.",
    }

    def _case_scores(self, x):
        pairs = json.loads(x["pairs"]); ids = [p[0] for p in pairs]
        return ids, [float(p[1]) for p in pairs], [float(p[2]) for p in pairs]

    def _ranking_blocks(self, r):
        out, k = {}, int(r[0]["k"])
        keys = ("top1_acc", "top3_recall", "overlap_frac", "ndcg_k", "mrr", "kendall_tau_b", "spearman")
        per = {"vayu": [], "state_prior": [], "random": [], "climate_only": []}
        used = []
        for x in r:
            ids, sc, rel = self._case_scores(x)
            m = RM.case_ranking_metrics(ids, sc, rel, k)
            if m is None: continue
            base = x["baseline_list"].split("|") if x["baseline_list"] else []
            bs = [float(len(base) - base.index(i)) if i in base else 0.0 for i in ids]
            per["vayu"].append(m); per["state_prior"].append(RM.case_ranking_metrics(ids, bs, rel, k))
            per["random"].append(RM.case_ranking_metrics(ids, [0.0] * len(ids), rel, k))
            abl = json.loads(x["abl"]) if x.get("abl") else None
            if abl:
                cs = [stage_scores(abl[i], bool(x["irrigated"]))["1_climate_only"] if i in abl else 0.0 for i in ids]
                per["climate_only"].append(RM.case_ranking_metrics(ids, [c if c is not None else 0.0 for c in cs], rel, k))
            else:
                per["climate_only"].append(None)
            used.append(x)
        if not used: return out
        zones = [x["zone_id"] for x in used]

        def agg(name, rows, keys=keys):
            idx = [i for i, m in enumerate(rows) if m is not None]
            res = {}
            for key in keys:
                vals = [(i, rows[i][key]) for i in idx if rows[i].get(key) is not None]
                if not vals: continue
                v = np.array([b for _, b in vals], float); zz = [zones[i] for i, _ in vals]
                res[key] = {"mean": round(float(v.mean()), 4), "ci95_zone_bootstrap": ci_str(
                    cluster_bootstrap(lambda i, v=v: v[i].mean(), zz)), "n": len(v)}
            return res
        out["ranking_metrics_tie_aware"] = {"k": k, **{n: agg(n, per[n]) for n in per if any(m is not None for m in per[n])}}
        out["ranking_metrics_tie_aware"]["baseline_definitions"] = self.BASELINES
        out["ranking_metrics_tie_aware"]["evaluated_on"] = f"exactly the same {len(used)} cases for every method"
        out["tie_policy"] = {"rule": "equal scores share positions; metrics are expectations under random tie-breaking",
                             "share_cases_tie_at_top": round(float(np.mean([m["tie_at_top"] for m in per["vayu"]])), 3),
                             "share_cases_tie_at_cutoff": round(float(np.mean([m["tie_at_cutoff"] for m in per["vayu"]])), 3),
                             "production_tiebreak": "calibrated score, then headroom (mean factor score), then name; tied crops share a rank"}
        # paired comparison Vayu - baseline (same cases)
        for b in ("state_prior", "random"):
            pairs = [(per["vayu"][i]["overlap_frac"] - per[b][i]["overlap_frac"], zones[i]) for i in range(len(used)) if per[b][i]]
            d = np.array([p[0] for p in pairs]); zz = [p[1] for p in pairs]
            out["ranking_metrics_tie_aware"][f"paired_overlap_gain_vs_{b}"] = {
                "mean": round(float(d.mean()), 4), "ci95_zone_bootstrap": ci_str(cluster_bootstrap(lambda i: d[i].mean(), zz))}
        # support
        crop_sup, zone_sup, season_sup, var_sup = {}, {}, {}, {}
        for x in used:
            ids, sc, rel = self._case_scores(x)
            for cid, rl in zip(ids, rel):
                if rl >= self.MAJOR_SHARE: crop_sup[cid] = crop_sup.get(cid, 0) + 1
            zone_sup[x["agro_zone"] or "unspecified"] = zone_sup.get(x["agro_zone"] or "unspecified", 0) + 1
            season_sup[x["season"]] = season_sup.get(x["season"], 0) + 1
            var_sup[x.get("variant", "") or "default"] = var_sup.get(x.get("variant", "") or "default", 0) + 1
        out["support"] = {"n_cases": len(used), "n_zones": len(set(zones)), "n_crops_observed_major": len(crop_sup),
                          "observed_major_by_crop": dict(sorted(crop_sup.items(), key=lambda kv: -kv[1])),
                          "cases_by_agro_zone": zone_sup, "cases_by_season": season_sup, "cases_by_variant": var_sup,
                          "note": "per-crop / per-zone figures with small support are indicative only"}
        # robustness: by season and by zone (overlap with CI where possible)
        def sub(keyfn, label):
            res = {}
            for g in sorted({keyfn(x) for x in used}):
                ii = [i for i, x in enumerate(used) if keyfn(x) == g]
                v = np.array([per["vayu"][i]["overlap_frac"] for i in ii]); zz = [zones[i] for i in ii]
                res[g or "default"] = {"n": len(ii), "overlap_mean": round(float(v.mean()), 3),
                                       "ci95_zone_bootstrap": ci_str(cluster_bootstrap(lambda j, v=v: v[j].mean(), zz))}
            return res
        out["robustness_overlap"] = {"by_season": sub(lambda x: x["season"], "season"),
                                     "by_agro_zone": sub(lambda x: x["agro_zone"] or "unspecified", "zone")}
        # calibration (only meaningful if scores are fitted/calibrated)
        flat = [(1 if rl >= self.MAJOR_SHARE else 0, float(sc_), x["zone_id"]) for x in used
                for (_, sc_, rl) in json.loads(x["pairs"])]
        Yb = np.array([f[0] for f in flat]); Pb = np.array([f[1] for f in flat])
        pa = RM.pr_auc(Yb, Pb)
        out["suitability_pr_auc"] = None if pa is None else {"value": round(pa, 4), "prevalence_baseline": round(float(Yb.mean()), 4)}
        if all(x.get("calibrated") for x in used):
            rows, ece = RM.reliability(Yb, Pb)
            out["calibration"] = {"brier": round(RM.brier(Yb, Pb), 4), "brier_of_constant_prevalence": round(RM.brier(Yb, np.full(len(Yb), Yb.mean())), 4),
                                  "ece": round(ece, 4), "reliability_curve": rows}
        else:
            out["calibration"] = {"status": "NOT EVALUATED: scores are uncalibrated limiting-factor indices, not probabilities, so Brier / "
                                            "reliability would be misleading. Fit a calibrator on the development split (fit_calibration.py)."}
        # ablation
        have = [x for x in used if x.get("abl")]
        if have:
            out["ablation"] = self._ablation(have, k)
        else:
            out["ablation"] = {"status": "NOT AVAILABLE: responses lack ablation_inputs (produced by the pre-refactor engine)."}
        return out

    def _ablation(self, rows, k):
        res = {}
        for variant in sorted({x.get("variant", "") for x in rows}):
            g = [x for x in rows if x.get("variant", "") == variant]
            res_v = {}
            for stage in STAGES:
                if stage == "5_plus_remote_sensing":
                    res_v[stage] = "NOT AVAILABLE: the suitability pipeline samples no remote-sensing evidence yet"; continue
                ov, t1, ys, ss, zz = [], [], [], [], []
                for x in g:
                    ids, sc, rel = self._case_scores(x); abl = json.loads(x["abl"])
                    st = []
                    for i, s_ in zip(ids, sc):
                        st.append(float(s_) if stage == "6_full_vayu" else (stage_scores(abl[i], bool(x["irrigated"])).get(stage) or 0.0) if i in abl else 0.0)
                    m = RM.case_ranking_metrics(ids, st, rel, k)
                    if m: ov.append(m["overlap_frac"]); t1.append(m["top1_acc"]); zz.append(x["zone_id"])
                    ys += [1 if rl >= self.MAJOR_SHARE else 0 for rl in rel]; ss += st
                auc = binary_metrics(np.array(ys), np.array(ss), 0.5)
                res_v[stage] = {"overlap_frac": round(float(np.mean(ov)), 4), "top1_acc": round(float(np.mean(t1)), 4),
                                "roc_auc": None if auc["roc_auc"] is None else round(auc["roc_auc"], 4),
                                "recall_at_0.5": None if auc["recall_POD"] is None else round(auc["recall_POD"], 4),
                                "fpr_at_0.5": None if auc["false_alarm_rate_FPR"] is None else round(auc["false_alarm_rate_FPR"], 4),
                                "n_cases": len(ov)}
            res[variant or "default"] = res_v
        return res


# -------------------------------------------------- 2. scalar vs reference value
class Scalar:
    """Any numeric output vs a reference value (DEM/survey elevation, in-situ LST/soil moisture, reference flood
    area, lake area, snow pct, NDVI from independent source, ...). Regression metrics per (endpoint, output_path)."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        resp = client.call(case["endpoint"], build_request(case, base_dir), use_cache)
        path = case["output_path"]; v = get_path(resp, path)
        if v is None:
            raise ValueError(f"output_path '{path}' not found in response")
        t = case["truth"]
        row = {"output_path": path, "unit": t.get("unit", ""), "pred": float(v), "obs": float(t["value"])}
        if "baseline_value" in t: row["baseline"] = float(t["baseline_value"])
        return row

    def summarize(self, rows, ctx):
        out = {}
        groups = {}
        for r in ok_rows(rows): groups.setdefault((r["endpoint"], r["output_path"], r["unit"]), []).append(r)
        for (ep, path, unit), g in groups.items():
            y = np.array([x["obs"] for x in g]); p = np.array([x["pred"] for x in g])
            b = np.array([x["baseline"] for x in g]) if all("baseline" in x for x in g) else None
            m = regression_metrics(y, p, b); z = [x["zone_id"] for x in g]
            if len(g) >= 5:
                m["rmse_ci95"] = ci_str(cluster_bootstrap(lambda i: np.sqrt(np.mean((p[i] - y[i]) ** 2)), z))
                m["mae_ci95"] = ci_str(cluster_bootstrap(lambda i: np.mean(np.abs(p[i] - y[i])), z))
            m = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
            m["unit"] = unit
            if len(g) < 10: m["warning"] = "n<10: treat as indicative, not conclusive"
            out[f"{ep} :: {path}"] = m
        return out


# ------------------------------------------- 3. class-area shares (lulc, DW)
class ClassAreas:
    """lulc / dynamic_world area breakdown vs reference class shares for the same AOI (e.g. official land-use stats).
    NOTE: lulc is ESA WorldCover and dynamic_world is Google's model - this validates those products at your AOIs,
    it does not validate Vayu code."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        resp = client.call(case["endpoint"], build_request(case, base_dir), use_cache)
        pred = {c["label"]: float(c.get("pct_of_aoi", 0)) for c in resp["classes"]}
        truth = {k: float(v) for k, v in case["truth"]["class_pct"].items()}
        labels = sorted(set(pred) | set(truth))
        diffs = {l: pred.get(l, 0.0) - truth.get(l, 0.0) for l in labels}
        return {"mae_pct_points": float(np.mean([abs(d) for d in diffs.values()])),
                "total_variation_distance_pct": float(0.5 * sum(abs(d) for d in diffs.values())),
                "pairs": json.dumps([[l, truth.get(l, 0.0), pred.get(l, 0.0)] for l in labels])}

    def summarize(self, rows, ctx):
        r = ok_rows(rows); out = {"n_ok": len(r)}
        if not r: return out
        z = [x["zone_id"] for x in r]
        for key in ("mae_pct_points", "total_variation_distance_pct"):
            v = np.array([x[key] for x in r])
            out[key] = {"mean": round(float(v.mean()), 3),
                        "ci95_zone_bootstrap": ci_str(cluster_bootstrap(lambda i, v=v: v[i].mean(), z))}
        pairs = [(l, t, p) for x in r for l, t, p in json.loads(x["pairs"])]
        y = np.array([t for _, t, _ in pairs]); p = np.array([p for _, _, p in pairs])
        m = regression_metrics(y, p)
        out["class_share_pooled"] = {k: round(v, 4) for k, v in m.items() if isinstance(v, (int, float)) and v is not None}
        per = {}
        for l, t, pr in pairs: per.setdefault(l, []).append(pr - t)
        out["per_class_mean_bias_pct_points"] = {l: round(float(np.mean(v)), 2) for l, v in sorted(per.items())}
        return out


# --------------------------------------------- 4. confusion matrix (points)
class Confusion:
    """rs:accuracy_assessment with your reference points. Matrices are pooled across zones; every metric is
    recomputed here from the raw matrix (and compared with the server's own overall accuracy / kappa)."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        req = build_request(case, base_dir)
        pts = case.get("reference_points")
        if case.get("reference_points_file"):
            with open(Path(base_dir) / case["reference_points_file"], newline="", encoding="utf-8") as f:
                pts = [{"lat": float(r["lat"]), "lon": float(r["lon"]), "true_class": r["true_class"]}
                       for r in csv.DictReader(f)]
        if not pts: raise ValueError("no reference points")
        req["reference_points"] = pts
        resp = client.call("rs:accuracy_assessment", req, use_cache)
        if resp.get("status") != "ok":
            raise ValueError(f"server: {resp.get('status')} - {resp.get('note')}")
        return {"assess_tool": req.get("assess_tool"), "points_used": resp["points_used"],
                "points_dropped": resp["points_dropped_no_data"] + resp["points_dropped_unrecognized_class"],
                "server_oa": resp["overall_accuracy"], "server_kappa": resp["kappa"],
                "class_order": json.dumps(resp.get("class_options", resp["classes"])),
                "matrix": json.dumps(resp["confusion_matrix"])}      # matrix[true][pred]

    def summarize(self, rows, ctx):
        out = {}
        for tool in sorted({r["assess_tool"] for r in ok_rows(rows)}):
            g = [r for r in ok_rows(rows) if r["assess_tool"] == tool]
            order = []
            for r in g:
                for l in json.loads(r["class_order"]):
                    if l not in order: order.append(l)
            mats = []
            for r in g:
                m = json.loads(r["matrix"]); M = np.zeros((len(order), len(order)), int)
                for t, row in m.items():
                    for p, c in row.items(): M[order.index(t), order.index(p)] += int(c)
                mats.append(M)
            pooled = sum(mats)
            used = [i for i in range(len(order)) if pooled[i].sum() or pooled[:, i].sum()]   # drop never-seen classes
            labels = [order[i] for i in used]; P = pooled[np.ix_(used, used)]
            ordinal = tool == "burn_severity"
            m = classification_from_matrix(P, labels, ordinal=ordinal)
            z = [r["zone_id"] for r in g]
            oa = lambda idx: sum(np.trace(mats[i]) for i in idx) / max(sum(mats[i].sum() for i in idx), 1)
            m["overall_accuracy_ci95_zone_bootstrap"] = ci_str(cluster_bootstrap(oa, z))
            m["n_zones"] = len(set(z)); m["points_dropped_total"] = int(sum(r["points_dropped"] for r in g))
            # chance-level reference and class-imbalance flag
            m["majority_class_baseline_accuracy"] = round(float(P.sum(1).max() / P.sum()), 4)
            m["server_vs_recomputed_oa_max_abs_diff"] = round(max(
                abs(r["server_oa"] - np.trace(M) / M.sum()) for r, M in zip(g, mats)), 4)
            for k in ("overall_accuracy", "kappa", "balanced_accuracy", "macro_f1", "kappa_quadratic_weighted",
                      "within_one_class_accuracy"):
                if isinstance(m.get(k), float): m[k] = round(m[k], 4)
            if P.sum() < 50 * max(len(labels), 1): m["warning"] = "fewer than ~50 points per class: low statistical power"
            out[tool] = m
            out[tool]["_matrix"] = {"labels": labels, "rows_true_cols_pred": P.tolist()}
        return out


# ------------------------------------ 5. event detection (risk score vs event)
class Event:
    """risk_score (0-100) vs a known bad / normal season label. Reports ROC-AUC and POD / false-alarm at the
    alert threshold (Vayu's default region risk_threshold is 60)."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        resp = client.call(case["endpoint"], build_request(case, base_dir), use_cache)
        s = get_path(resp, case.get("output_path", "risk_score"))
        if s is None: raise ValueError("score not found in response")
        return {"score": float(s), "event": int(case["truth"]["event"]), "threshold": float(case.get("threshold", 60))}

    def summarize(self, rows, ctx):
        r = ok_rows(rows)
        if not r: return {"n_ok": 0}
        y = np.array([x["event"] for x in r]); s = np.array([x["score"] for x in r])
        m = binary_metrics(y, s, r[0]["threshold"])
        if len(set(y)) < 2: m["warning"] = "only one class present: AUC undefined - include non-event cases"
        if len(r) < 20: m["warning"] = "n<20: indicative only"
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}


# ---------------------------------------------------- 6. phenology dates
class PhenologyDates:
    """green_up_date / peak_date / senescence_date vs observed dates (crop calendar, MODIS/VIIRS phenology, field log).
    Error in days (pred - obs); negative = Vayu early."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        resp = client.call("phenology", build_request(case, base_dir), use_cache)
        if resp.get("status") != "ok": raise ValueError(f"phenology status={resp.get('status')}")
        row = {}
        for k in ("green_up_date", "peak_date", "senescence_date"):
            if k in case["truth"]:
                pv = resp.get(k)
                row[f"{k}_err_days"] = None if pv is None else (date.fromisoformat(pv[:10]) - date.fromisoformat(case["truth"][k][:10])).days
        return row

    def summarize(self, rows, ctx):
        out = {}
        for k in ("green_up_date", "peak_date", "senescence_date"):
            v = np.array([r[f"{k}_err_days"] for r in ok_rows(rows) if r.get(f"{k}_err_days") is not None], float)
            if len(v):
                out[k] = {"n": len(v), "mae_days": round(float(np.abs(v).mean()), 2), "bias_days": round(float(v.mean()), 2),
                          "rmse_days": round(float(np.sqrt((v ** 2).mean())), 2),
                          "within_15_days_share": round(float((np.abs(v) <= 15).mean()), 3)}
        return out


# ------------------------------------------------ 7. categorical advisory
class Categorical:
    """A label output (irrigation_advisory.recommendation, risk band, ...) vs an expert / observed label."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        resp = client.call(case["endpoint"], build_request(case, base_dir), use_cache)
        v = get_path(resp, case.get("output_path", "recommendation"))
        if v is None: raise ValueError("label not found")
        return {"pred": str(v), "obs": str(case["truth"]["label"])}

    def summarize(self, rows, ctx):
        r = ok_rows(rows)
        if not r: return {"n_ok": 0}
        order = case_order = ctx.get("label_order")
        m, M, labels = classification_metrics([x["obs"] for x in r], [x["pred"] for x in r], order,
                                              ordinal=bool(order))
        m = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
        m["_matrix"] = {"labels": labels, "rows_true_cols_pred": M.tolist()}
        return m


# ------------------------------------------- 8. perennial / established-crop presence
class Presence:
    """Where a crop is an established MAJOR crop (observed area above a threshold), the engine must not reject it.
    This is the Ratnagiri->mango class of failure: a sanity RECALL test, deliberately not a ranking test."""
    NOT_REJECTED = 0.25

    @safe
    def run_case(self, case, client, base_dir, use_cache):
        cid = case["truth"]["crop_id"]
        resp = client.call("crop_suitability", {"include_revenue": False, **build_request(case, base_dir)}, use_cache)
        c = next((x for x in resp["crops"] if x["crop_id"] == cid), None)
        if c is None or c.get("score") is None: raise ValueError(f"{cid} not scored")
        cat = c.get("category") or c.get("rating")
        lf = c.get("limiting_factors")
        primary = (lf or {}).get("primary") if isinstance(lf, dict) else None
        return {"crop_id": cid, "score": float(c["score"]), "category": cat,
                "not_rejected": int(cat != "unsuitable" and float(c["score"]) >= self.NOT_REJECTED),
                "suitable_ge_0.5": int(float(c["score"]) >= 0.5),
                "primary_limitation": (primary or {}).get("label") or c.get("limiting_label", ""),
                "water_status": c.get("water_status", "")}

    def summarize(self, rows, ctx):
        r = ok_rows(rows)
        if not r: return {"n_ok": 0}
        z = [x["zone_id"] for x in r]; out = {"n_ok": len(r), "n_zones": len(set(z))}
        for key in ("not_rejected", "suitable_ge_0.5"):
            v = np.array([x[key] for x in r], float)
            out[key] = {"mean": round(float(v.mean()), 4), "ci95_zone_bootstrap": ci_str(cluster_bootstrap(lambda i, v=v: v[i].mean(), z))}
        by = {}
        for x in r: by.setdefault(x["crop_id"], []).append(x)
        out["by_crop"] = {c: {"n": len(g), "not_rejected": f"{sum(x['not_rejected'] for x in g)}/{len(g)}",
                              "suitable_ge_0.5": f"{sum(x['suitable_ge_0.5'] for x in g)}/{len(g)}"} for c, g in by.items()}
        rej = [x for x in r if not x["not_rejected"]]
        out["rejected_cases"] = [{"case": x["case_id"], "score": x["score"], "category": x["category"],
                                  "primary_limitation": x["primary_limitation"], "water_status": x["water_status"]} for x in rej][:25]
        out["rejected_primary_limitations"] = {k: sum(1 for x in rej if x["primary_limitation"] == k) for k in sorted({x["primary_limitation"] for x in rej})}
        return out


VALIDATORS = {"presence": Presence(), "crop_ranking": CropRanking(), "scalar": Scalar(), "class_areas": ClassAreas(),
              "confusion": Confusion(), "event": Event(), "phenology_dates": PhenologyDates(),
              "categorical": Categorical()}
