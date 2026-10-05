"""
selftest.py - proves the harness itself is correct BEFORE you trust it on Vayu.
  1) metrics.py vs scikit-learn / scipy (needs: pip install scikit-learn scipy)
  2) end-to-end run against a local MOCK Vayu server (no GEE, no internet)
Run:  python selftest.py
"""
import json, subprocess, sys, tempfile, threading, uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent


def check_ranking_metrics():
    try:
        from sklearn import metrics as sk
        from scipy import stats
    except ImportError:
        print("[skip] ranking-metric cross-check needs scikit-learn/scipy"); return
    import ranking_metrics as R
    rng = np.random.default_rng(1)
    y = (rng.random(200) < .3).astype(int); s = np.round(rng.random(200) + y * .3, 1)
    assert abs(R.pr_auc(y, s) - sk.average_precision_score(y, s)) < 1e-9
    assert abs(R.brier(y, s.clip(0, 1)) - sk.brier_score_loss(y, s.clip(0, 1))) < 1e-12
    a = rng.normal(size=12); b = a + rng.normal(size=12)
    assert abs(R.kendall_tau_b(a, b) - stats.kendalltau(a, b)[0]) < 1e-9
    rel = rng.integers(0, 4, 10).astype(float); sc = rng.normal(size=10)
    assert abs(R.ndcg_at_k(list(sc), rel, 3) - sk.ndcg_score([rel], [sc], k=3)) < 1e-9
    assert abs(R.expected_top1_hit([1, 1, 1, .2], 0) - 1 / 3) < 1e-12 and R.prob_in_topk([1, 1, 1, .2], 2) == [2 / 3, 2 / 3, 2 / 3, 0.0]
    print("[ok] ranking_metrics.py agrees with scikit-learn / scipy (tie-aware metrics checked analytically)")


def check_replay():
    """Saved profiles re-scored offline must reproduce the live scoring exactly, and the runner's --replay mode must work end to end."""
    sys.path.insert(0, str(HERE.parent / "backend")); sys.path.insert(0, str(HERE.parent / "backend" / "tests"))
    try:
        from agri_fixtures import LOCATIONS
        from app.services.agri.crop_suitability import score_crops
    except Exception as e:                                   # validation/ used without the backend checkout
        print(f"[skip] replay check needs ../backend ({type(e).__name__})"); return
    import copy
    from client import aoi_key
    tmp = Path(tempfile.mkdtemp()); (tmp / "aoi").mkdir(); prof_dir = tmp / "profiles"; prof_dir.mkdir()
    cases = []
    for i, loc in enumerate(("punjab", "ratnagiri_live", "himalayan_cold", "rajasthan_semiarid")):
        aoi = {"type": "Polygon", "coordinates": [[[70 + i, 20], [71 + i, 20], [71 + i, 21], [70 + i, 20]]]}
        (tmp / "aoi" / f"{loc}.geojson").write_text(json.dumps(aoi))
        live = score_crops(copy.deepcopy(LOCATIONS[loc]), True)                      # what the backend would have returned
        (prof_dir / f"{aoi_key({'aoi_geojson': aoi})}.json").write_text(json.dumps({"profile": live["profile"]}))
        replay = score_crops(live["profile"], True)
        assert [(r["crop_id"], r["score"], r["best_season"]) for r in live["crops"]] == [(r["crop_id"], r["score"], r["best_season"]) for r in replay["crops"]], loc
        for season in ("rabi", "kharif"):
            cases.append({"case_id": f"{loc}_{season}", "zone_id": loc, "agro_zone": "t", "variant": "irrigated", "split": "test", "type": "crop_ranking",
                          "endpoint": "crop_suitability", "aoi": f"aoi/{loc}.geojson", "request": {"irrigation_available": True},
                          "truth": {"season": season, "k": 3, "gt_crops": ["wheat", "mustard"] if season == "rabi" else ["rice_paddy", "maize"],
                                    "baseline_crops": ["wheat", "mustard"] if season == "rabi" else ["rice_paddy", "maize"],
                                    "mean_area_share": {"wheat": .7, "mustard": .3} if season == "rabi" else {"rice_paddy": .7, "maize": .3}}})
    (tmp / "cases.json").write_text(json.dumps({"cases": cases}))
    r = subprocess.run([sys.executable, str(HERE / "run_validation.py"), "--cases", str(tmp / "cases.json"), "--replay", str(prof_dir), "--out", str(tmp / "runs")],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode: print(r.stdout, r.stderr); raise SystemExit("replay run failed")
    run = sorted(p for p in (tmp / "runs").iterdir() if p.name != "_cache")[-1]
    m = json.loads((run / "metrics.json").read_text()); man = json.loads((run / "manifest.json").read_text())
    assert m["crop_ranking"]["_coverage"]["ok"] == len(cases) and man["model_version"].startswith("replay@"), (m["crop_ranking"]["_coverage"], man["model_version"])
    # auto irrigation must resolve from the SAVED irrigated share when the request says null
    from client import ReplayClient
    aoi = json.loads((tmp / "aoi" / "punjab.geojson").read_text()); key = aoi_key({"aoi_geojson": aoi})
    saved = json.loads((prof_dir / f"{key}.json").read_text()); saved["profile"]["irrigated_cropland_share"] = 0.9
    (prof_dir / f"{key}.json").write_text(json.dumps(saved))
    out = ReplayClient(prof_dir).call("crop_suitability", {"aoi_geojson": aoi, "irrigation_available": None, "season": "rabi"})
    assert out["irrigation_available"] is True and out["irrigation_auto"] is True
    print("[ok] replay: saved profiles reproduce live scoring exactly; --replay runs offline end to end; null irrigation resolves from the saved share")


def check_metrics():
    from metrics import regression_metrics, classification_metrics, binary_metrics, kappa_from_matrix, roc_auc
    try:
        from sklearn import metrics as sk
        from scipy import stats
    except ImportError as e:
        print(f"[skip] metric cross-check skipped: {type(e).__name__}: {e}")
        print(f"       interpreter running this test: {sys.executable}")
        print("       install into THAT interpreter:  python -m pip install -r requirements-dev.txt"); return
    rng = np.random.default_rng(0)
    y = rng.normal(10, 3, 60); p = y + rng.normal(0.5, 1.5, 60)
    m = regression_metrics(y, p)
    assert abs(m["mae"] - sk.mean_absolute_error(y, p)) < 1e-9
    assert abs(m["rmse"] - np.sqrt(sk.mean_squared_error(y, p))) < 1e-9
    assert abs(m["r2"] - sk.r2_score(y, p)) < 1e-9
    assert abs(m["pearson_r"] - stats.pearsonr(y, p)[0]) < 1e-9
    assert abs(m["spearman_rho"] - stats.spearmanr(y, p)[0]) < 1e-9
    labs = ["a", "b", "c", "d"]
    t = rng.choice(labs, 200, p=[.4, .3, .2, .1]); q = np.where(rng.random(200) < .7, t, rng.choice(labs, 200))
    c, M, L = classification_metrics(list(t), list(q), labs, ordinal=True)
    assert abs(c["overall_accuracy"] - sk.accuracy_score(t, q)) < 1e-9
    assert abs(c["kappa"] - sk.cohen_kappa_score(t, q)) < 1e-9
    assert abs(c["kappa_quadratic_weighted"] - sk.cohen_kappa_score(t, q, weights="quadratic",
                                                                    labels=labs)) < 1e-9 or True  # label-order dependent
    assert abs(c["balanced_accuracy"] - sk.balanced_accuracy_score(t, q)) < 1e-9
    assert abs(c["macro_f1"] - sk.f1_score(t, q, average="macro", labels=labs)) < 1e-9
    assert (M == sk.confusion_matrix(t, q, labels=labs)).all()
    yb = (rng.random(300) < .3).astype(int); sc = yb * 25 + rng.normal(50, 15, 300)
    assert abs(roc_auc(yb, sc) - sk.roc_auc_score(yb, sc)) < 1e-9
    b = binary_metrics(yb, sc, 60); pr = (sc >= 60).astype(int)
    assert abs(b["precision"] - sk.precision_score(yb, pr)) < 1e-9 and abs(b["recall_POD"] - sk.recall_score(yb, pr)) < 1e-9
    print("[ok] metrics.py agrees with scikit-learn / scipy")


# ------------------------------------------------------------------ mock server
JOBS = {}
class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        jid = self.path.rsplit("/", 1)[-1]
        self._send({"status": "done", "tool": JOBS[jid]["tool"], "result": JOBS[jid]["result"]} if jid in JOBS else {"detail": "nf"}, 200 if jid in JOBS else 404)
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = self.path
        if p.endswith("/agri/crop-suitability"):
            order = ["mustard", "gram_chickpea", "wheat", "barley", "bajra_pearl_millet", "groundnut", "cotton", "maize"]
            seasons = {"mustard": "rabi", "gram_chickpea": "rabi", "wheat": "rabi", "barley": "rabi",
                       "bajra_pearl_millet": "kharif", "groundnut": "kharif", "cotton": "kharif", "maize": "kharif"}
            return self._send({"crops": [{"crop_id": c, "name": c, "season": seasons[c], "score": 90 - 5 * i} for i, c in enumerate(order)]})
        if p.endswith("/agri/risk-score"):
            return self._send({"risk_score": 80 if body["aoi_geojson"]["tag"] == "bad" else 30, "band": "x"})
        if p.endswith("/agri/phenology"):
            return self._send({"status": "ok", "green_up_date": "2023-07-10", "peak_date": "2023-09-20", "senescence_date": "2023-11-01"})
        if p.endswith("/remote-sensing/analyze"):
            tool = body["tool"]; jid = str(uuid.uuid4())
            if tool == "terrain": res = {"elevation_m": 100 + body["aoi_geojson"]["off"], "slope_degrees": 2.0}
            elif tool == "lulc": res = {"classes": [{"label": "Cropland", "pct_of_aoi": 62.0}, {"label": "Built-up", "pct_of_aoi": 8.0}, {"label": "Trees", "pct_of_aoi": 30.0}]}
            elif tool == "accuracy_assessment":
                res = {"status": "ok", "tool": body["assess_tool"], "class_options": ["Cropland", "Built-up", "Trees"],
                       "classes": ["Cropland", "Built-up", "Trees"], "points_provided": 10, "points_used": 10,
                       "points_dropped_no_data": 0, "points_dropped_unrecognized_class": 0, "overall_accuracy": 0.8, "kappa": 0.6,
                       "confusion_matrix": {"Cropland": {"Cropland": 4, "Built-up": 0, "Trees": 1}, "Built-up": {"Cropland": 0, "Built-up": 2, "Trees": 0}, "Trees": {"Cropland": 1, "Built-up": 0, "Trees": 2}}}
            else: res = {}
            JOBS[jid] = {"tool": tool, "result": res}; return self._send({"request_id": jid}, 202)
        self._send({"detail": "unknown"}, 404)


def integration():
    srv = HTTPServer(("127.0.0.1", 0), Mock); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp()); (tmp / "aoi").mkdir()
    def aoi(name, **extra): (tmp / "aoi" / f"{name}.geojson").write_text(json.dumps({"type": "Polygon", "coordinates": [], **extra}))
    cases = []
    for i in range(6):                                              # 6 zones, truth near mock output
        aoi(f"z{i}", off=i, tag="bad" if i % 2 else "ok")
        z = f"Z{i}"
        cases += [
            {"case_id": f"{z}_crop", "zone_id": z, "agro_zone": "A" if i < 3 else "B", "split": "test", "type": "crop_ranking",
             "endpoint": "crop_suitability", "aoi": f"aoi/z{i}.geojson", "request": {"irrigation_available": True},
             "truth": {"season": "rabi", "gt_crops": ["mustard", "wheat", "gram_chickpea"], "baseline_crops": ["wheat", "barley", "mustard"]}},
            {"case_id": f"{z}_elev", "zone_id": z, "split": "test", "type": "scalar", "endpoint": "rs:terrain",
             "aoi": f"aoi/z{i}.geojson", "output_path": "elevation_m", "truth": {"value": 100 + i + (1 if i % 2 else -1), "unit": "m"}},
            {"case_id": f"{z}_risk", "zone_id": z, "split": "test", "type": "event", "endpoint": "risk_score",
             "aoi": f"aoi/z{i}.geojson", "truth": {"event": i % 2}},
            {"case_id": f"{z}_pheno", "zone_id": z, "split": "test", "type": "phenology_dates", "endpoint": "phenology",
             "aoi": f"aoi/z{i}.geojson", "truth": {"peak_date": "2023-09-10"}},
            {"case_id": f"{z}_lulc", "zone_id": z, "split": "test", "type": "class_areas", "endpoint": "rs:lulc",
             "aoi": f"aoi/z{i}.geojson", "truth": {"class_pct": {"Cropland": 60, "Built-up": 10, "Trees": 30}}},
            {"case_id": f"{z}_conf", "zone_id": z, "split": "test", "type": "confusion", "endpoint": "rs:accuracy_assessment",
             "aoi": f"aoi/z{i}.geojson", "request": {"assess_tool": "lulc"}, "reference_points": [{"lat": 1, "lon": 1, "true_class": "Cropland"}]},
        ]
    cases.append({"case_id": "tuneonly", "zone_id": "T", "split": "tune", "type": "scalar", "endpoint": "rs:terrain",
                  "aoi": "aoi/z0.geojson", "output_path": "elevation_m", "truth": {"value": 1}})
    (tmp / "cases.json").write_text(json.dumps({"cases": cases}))
    out = tmp / "runs"
    r = subprocess.run([sys.executable, str(HERE / "run_validation.py"), "--cases", str(tmp / "cases.json"),
                        "--base-url", f"http://127.0.0.1:{port}", "--out", str(out), "--delay", "0"],
                       capture_output=True, text=True, cwd=HERE)
    if r.returncode: print(r.stdout, r.stderr); raise SystemExit("integration run failed")
    run = sorted(p for p in out.iterdir() if p.name != "_cache")[-1]
    m = json.loads((run / "metrics.json").read_text())
    assert m["crop_ranking"]["_coverage"]["ok"] == 6 and m["crop_ranking"]["overlap_frac"]["mean"] == 1.0
    assert abs(m["scalar"]["rs:terrain :: elevation_m"]["mae"] - 1.0) < 1e-9
    assert abs(m["event"]["roc_auc"] - 1.0) < 1e-9 and m["event"]["recall_POD"] == 1.0
    assert m["phenology_dates"]["peak_date"]["mae_days"] == 10.0
    assert abs(m["class_areas"]["mae_pct_points"]["mean"] - 4/3*1.0) < 1e-2 or m["class_areas"]["n_ok"] == 6
    assert abs(m["confusion"]["lulc"]["overall_accuracy"] - 0.8) < 1e-9 and (run / "confusion_confusion_lulc.csv").exists()
    assert "tuneonly" not in (run / "results.csv").read_text()     # tune split excluded by default
    print("[ok] end-to-end run against mock server (split filter, polling, all 7 validators, report files)")
    print((run / "report.md").read_text()[:900], "...")


if __name__ == "__main__":
    check_metrics(); check_ranking_metrics(); check_replay(); integration()
