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


# ------------------------------------------------------------ 1. crop ranking
class CropRanking:
    """Vayu ranks 19 crops by suitability; truth = observed crops by sown-area share (largest first)."""
    @safe
    def run_case(self, case, client, base_dir, use_cache):
        t = case["truth"]; season = t["season"]; k = int(t.get("k", 3))
        resp = client.call("crop_suitability", {"include_revenue": False, **build_request(case, base_dir)}, use_cache)
        crops = [c for c in resp["crops"] if c.get("score") is not None
                 and (c.get("season") == season or (t.get("include_perennials") and c.get("season") == "perennial"))]
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
        return {"pred": "|".join(pred[:k]), "truth": "|".join(truth[:k]), "k": k, **m,
                "baseline_overlap_frac": None if b is None else b["overlap_frac"], "tie_at_cutoff": int(tied)}

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
        return out


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


VALIDATORS = {"crop_ranking": CropRanking(), "scalar": Scalar(), "class_areas": ClassAreas(),
              "confusion": Confusion(), "event": Event(), "phenology_dates": PhenologyDates(),
              "categorical": Categorical()}
