"""Detailed agricultural risk assessment (v2): statistics, class boundaries, indicators, combination, sensitivity, vulnerability, soil panel, assembly, orchestration.
Pure Python plus mocked sources: Earth Engine is NOT reachable from the dev sandbox.  pytest tests/test_risk_assessment.py"""
import copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from agri_fixtures import LOCATIONS
from app.services import vdi_contract
from app.services.agri import risk_assessment as ra
from app.services.agri.water_balance import AWC_MM_PER_M

RJ = LOCATIONS["rajasthan_semiarid"]
# Built through the REAL packager (vdi_contract.package_vdi_indices), so this fixture has exactly the shape compute_vegetation_drought_index() returns (lower-case keys). A hand-written upper-case dict here once hid a
# production bug: the consumer read indices["VHI"], the producer wrote indices["vhi"], and the VHI indicator was always "unavailable".
_VDI_META = {k: (k + " label", "formula", "citation", "interpretation") for k in vdi_contract.VDI_KEYS}
def vdi_indices(vhi=28.0, vci=30.0, tci=26.0): return vdi_contract.package_vdi_indices({"VHI_mean": vhi, "VCI_mean": vci, "TCI_mean": tci}, _VDI_META, lambda k: None)
VDI = {"indices": vdi_indices(), "valid_pixel_fraction": 0.8, "baseline": {"ndvi_years_with_data": 20, "lst_years_with_data": 22, "years_requested": "2001-2024", "window": "w"}}
BASE = {"z_score": -1.2, "seasonal_normal_ndvi": 0.45, "seasonal_std_ndvi": 0.05, "years_used": 8, "window_days": 30, "as_of": "2026-11-01", "current_ndvi": 0.39, "status": "ok"}
PREC = {"status": "ok", "recent_total_mm": 50.0, "historical_mean_mm": 120.0, "recent_days": 90, "historical_totals_mm": [float(x) for x in range(60, 180, 4)]}   # 30 values
RZ = {"sm_rootzone_percentile": 15.0, "sm_rootzone_0_100cm": 0.21, "sm_surface_0_5cm": 0.1}
GW = {"status": "ok", "series": [{"date": f"d{i}", "value": float(i)} for i in range(24)], "latest_anomaly_cm": 2.0, "latest_date": "2026-08", "trend": "declining", "slope_cm_per_year": -2.0}
# A realistic compute_risk_score() result (same keys and metric names as the real function returns).
V1 = {"risk_score": 42, "band": "moderate", "confidence": 80.0, "reason": "elevated drought stress and declining soil moisture",
      "sub_scores": {"drought": 45.0, "vegetation_loss": 30.0, "moisture_deficit": 52.5}, "weights": {"drought": 0.40, "vegetation_loss": 0.35, "moisture_deficit": 0.25}, "min_assessed_pct": 20.0,
      "inputs_used": ["drought", "vegetation_loss", "moisture_deficit"], "inputs_failed": [], "period": {"start_date": "2025-11-10", "end_date": "2026-11-10"},
      "raw_metrics": {"vegetation": {"vegetation_loss_km2": 1.2, "vegetation_gain_km2": 0.4, "initial_vegetation_km2": 4.0, "net_change_km2": -0.8, "loss_pct": 30.0, "region_area_km2": 10.0, "vegetation_pct_of_aoi": 40.0, "small_base_caveat": False},
                      "drought": {"drought_affected_km2": 2.7, "severe_drought_km2": 0.9, "region_area_km2": 10.0, "assessed_area_pct": 60.0, "drought_pct_of_assessed": 45.0, "severe_pct_of_assessed": 15.0, "drought_affected_pct": 27.0,
                                  "severe_drought_pct": 9.0, "avg_nddi_start": 0.31, "avg_nddi_end": 0.52, "nddi_change": 0.21, "thresholds": {"drought": 0.5, "severe": 0.7}},
                      "soil_moisture": {"start_avg_soil_moisture": 0.24, "end_avg_soil_moisture": 0.19, "moisture_change": -0.05, "dry_stress_area_km2": 3.0, "dry_stress_pct": 30.0, "end_min_sm": 0.11, "end_max_sm": 0.27,
                                        "sampling": "aoi", "sampling_note": None, "data_available": True, "window_overlap_caveat": False}},
      "provenance": {"vegetation_source": "Sentinel-2", "drought_source": "Sentinel-2 NDDI", "moisture_source": "NASA_SMAP_SPL4SMGP_008", "computed_at": "2026-11-10T00:00:00Z"}}


def inputs(**kw): return {"vdi": VDI, "baseline": BASE, "prec": PREC, "rz": RZ, "gw": GW, "profile": copy.deepcopy(RJ), "v1": V1, "errors": {}, "as_of": "2026-11-10", **kw}


# ---------------- statistics and published classes ----------------
def test_percentile_rank_is_mid_rank_and_ignores_junk():
    assert ra.percentile_rank(50, [10, 20, 50, 80, 90]) == 50.0 and ra.percentile_rank(5, [10, 20]) == 0.0 and ra.percentile_rank(99, [10, 20]) == 100.0
    assert ra.percentile_rank(10, [10, 10, 10, 10]) == 50.0, "ties count half"
    assert ra.percentile_rank(3, [1, 2, 3, None, float("nan"), 4]) == pytest.approx(100 * (2 + 0.5) / 4)
    assert ra.percentile_rank(None, [1]) is None and ra.percentile_rank(1, []) is None


def test_usdm_percentile_bands_match_the_published_boundaries():
    cases = [(0, 5), (2, 5), (2.01, 4), (5, 4), (5.01, 3), (10, 3), (10.01, 2), (20, 2), (20.01, 1), (30, 1), (30.01, 0), (99, 0)]
    for p, code in cases: assert ra.usdm_class(p)[0] == code, p
    assert [ra.usdm_stress(p) for p in (1, 4, 8, 15, 25, 60)] == [100.0, 80.0, 60.0, 40.0, 20.0, 0.0] and ra.usdm_stress(None) is None
    assert ra.usdm_class(1)[1] == "D4 exceptional" and ra.usdm_class(60)[1] == "no drought signal"


def test_vhi_classes_match_kogan_limits():
    for v, code, label in ((9.9, 4, "extreme"), (10, 3, "severe"), (19.9, 3, "severe"), (20, 2, "moderate"), (30, 1, "mild"), (39.9, 1, "mild"), (40, 0, "no drought signal"), (80, 0, "no drought signal")):
        assert ra.vhi_class(v) == (code, label), v
    assert ra.vhi_stress(5) == 100.0 and ra.vhi_stress(25) == 50.0 and ra.vhi_stress(60) == 0.0 and ra.vhi_class(None)[1] == "unknown"


def test_z_and_percentile_round_trip_and_clamp():
    assert ra.z_to_percentile(0) == 50.0 and ra.percentile_to_z(50) == 0.0 and ra.percentile_to_z(97.72) == pytest.approx(2.0, abs=0.01)
    assert ra.percentile_to_z(0) == ra.percentile_to_z(0.1) and ra.percentile_to_z(100) == ra.percentile_to_z(99.9)
    assert ra.z_to_percentile(-1.2) == pytest.approx(11.51, abs=0.01)


# ---------------- indicators ----------------
def test_indicators_report_value_percentile_class_and_stress():
    v, n, r, z, g = ra.ind_vhi(VDI), ra.ind_ndvi(BASE), ra.ind_rain(PREC), ra.ind_rootzone(RZ), ra.ind_groundwater(GW)
    assert (v["value"], v["stress"], v["reliability"]) == (28.0, 50.0, "high") and "moderate" in v["class"] and "VCI 30.0" in v["note"] and "TCI 26.0" in v["note"]
    assert n["percentile"] == pytest.approx(11.51, abs=0.01) and n["stress"] == 40.0 and n["reliability"] == "medium"
    assert r["percentile"] == ra.percentile_rank(50.0, PREC["historical_totals_mm"]) == pytest.approx(0.0, abs=1) and r["stress"] == 100.0 and r["reliability"] == "high" and "SPI-like z" in r["note"]
    assert (z["percentile"], z["stress"], z["reliability"]) == (15.0, 40.0, "medium") and "modelled" in z["note"]
    assert g["reliability"] == "low" and g["percentile"] == ra.percentile_rank(2.0, [float(i) for i in range(24)]) == pytest.approx(10.42, abs=0.01) and g["stress"] == 40.0 and "D1" in g["class"]


def test_unavailable_indicators_say_why_and_never_score_zero():
    cases = [ra.ind_vhi(None, "gee down"), ra.ind_vhi({"indices": vdi_indices(vhi=None)}), ra.ind_vhi({**VDI, "valid_pixel_fraction": 0.1}), ra.ind_ndvi(None, "no base"), ra.ind_ndvi({"z_score": None, "reason": "too few years"}),
             ra.ind_rain({"status": "no_data"}), ra.ind_rain({**PREC, "historical_totals_mm": []}), ra.ind_rootzone({"sm_rootzone_percentile": None}), ra.ind_groundwater({"status": "ok", "series": [], "latest_anomaly_cm": 1}),
             ra.ind_groundwater({**GW, "series": GW["series"][:5]})]
    for c in cases: assert c["status"] == "unavailable" and c["stress"] is None and c["reason"], c
    assert "gee down" == cases[0]["reason"] and "too few valid pixels" in cases[2]["reason"]


def test_reliability_depends_on_record_length():
    assert ra.ind_vhi({**VDI, "baseline": {**VDI["baseline"], "ndvi_years_with_data": 9}})["reliability"] == "medium" and ra.ind_vhi({**VDI, "baseline": {**VDI["baseline"], "lst_years_with_data": 3}})["reliability"] == "low"
    assert ra.ind_rain({**PREC, "historical_totals_mm": PREC["historical_totals_mm"][:20]})["reliability"] == "medium" and ra.ind_rain({**PREC, "historical_totals_mm": PREC["historical_totals_mm"][:10]})["reliability"] == "low"
    assert ra.ind_ndvi({**BASE, "years_used": 10})["reliability"] == "high" and ra.ind_ndvi({**BASE, "years_used": 4})["reliability"] == "low"


# ---------------- combination, sensitivity, confidence, risk class ----------------
def test_family_means_and_renormalised_combination():
    inds = [ra.ind_vhi(VDI), ra.ind_ndvi(BASE), ra.ind_rain(PREC), ra.ind_rootzone(RZ), ra.ind_groundwater(GW)]
    fam = ra.family_scores(inds); assert fam == {"vegetation": 45.0, "rainfall": 100.0, "soil_moisture": 40.0, "groundwater": 40.0}
    assert ra.combine(fam) == pytest.approx(56.25, abs=0.06)
    assert ra.combine({**fam, "groundwater": None}) == pytest.approx((45 + 100 + 40) / 3, abs=0.06) and ra.combine({f: None for f in ra.FAMILIES}) is None
    assert ra.combine(fam, {"vegetation": 4, "rainfall": 2, "soil_moisture": 2, "groundwater": 2}) == pytest.approx((45 * 4 + 100 * 2 + 40 * 2 + 40 * 2) / 10, abs=0.06)


def test_sensitivity_range_robust_flag_and_leave_one_out():
    s = ra.sensitivity({"vegetation": 40.0, "rainfall": 40.0, "soil_moisture": 40.0, "groundwater": 40.0}); assert s["range"] == 0 and s["robust"] and s["most_influential"] is None
    s = ra.sensitivity({"vegetation": 0.0, "rainfall": 100.0, "soil_moisture": 50.0, "groundwater": None})
    assert s["variants"]["equal weights (headline)"] == 50.0 and "without rainfall" in s["variants"] and "without groundwater" not in s["variants"], "only available families are dropped"
    assert s["min"] == min(v for v in s["variants"].values()) and s["max"] == max(s["variants"].values()) and s["range"] == round(s["max"] - s["min"], 1) and not s["robust"] and s["most_influential"]
    one = ra.sensitivity({"vegetation": 30.0, "rainfall": None, "soil_moisture": None, "groundwater": None}); assert one["range"] == 0 and not any(k.startswith("without") for k in one["variants"])
    assert ra.sensitivity({f: None for f in ra.FAMILIES})["range"] is None


def test_confidence_is_coverage_times_reliability():
    all5 = [ra.ind_vhi(VDI), ra.ind_ndvi(BASE), ra.ind_rain(PREC), ra.ind_rootzone(RZ), ra.ind_groundwater(GW)]
    c = ra.confidence(all5); assert c["coverage"] == "4 of 4 indicator families" and c["mean_reliability"] == round((1 + 0.7 + 1 + 0.7 + 0.4) / 5, 2) and c["score"] == c["mean_reliability"] and c["missing"] == []
    c2 = ra.confidence([ra.ind_rain(PREC), ra.ind_vhi(None)]); assert c2["score"] == round(0.25 * 1.0, 2) and c2["label"] == "low" and "vegetation" in c2["missing"]
    assert ra.confidence([])["score"] == 0.0


def test_risk_class_shifts_one_step_and_never_leaves_the_scale():
    assert ra.risk_class(10, 90)["class"] == "Moderate" and ra.risk_class(10, 10)["class"] == "Low" and ra.risk_class(60, 90)["class"] == "Very high" and ra.risk_class(90, 90)["class"] == "Very high"
    assert ra.risk_class(60, 10)["class"] == "Moderate" and ra.risk_class(60, 50)["class"] == "High" and ra.risk_class(60, None)["class"] == "High"
    r = ra.risk_class(30, 80); assert r["shift"] == 1 and "moved up one step" in r["rule"]
    assert ra.risk_class(None, 50)["class"] == "Cannot assess"
    assert [ra.stress_level(x) for x in (0, 24.9, 25, 49.9, 50, 74.9, 75, 100)] == ["Low", "Low", "Moderate", "Moderate", "High", "High", "Very high", "Very high"]
    assert [ra.vuln_class(x) for x in (0, 33.2, 33.4, 66.6, 66.8, 100, None)] == ["Low", "Low", "Moderate", "Moderate", "High", "High", "unknown"]


# ---------------- land context ----------------
def test_vulnerability_factors_use_documented_classes():
    sand = ra.vulnerability({"texture_class": 12, "texture_name": "Sand", "organic_carbon_gkg": 4.0, "slope_pct": 0.5}, None)
    by = {f["id"]: f for f in sand["factors"]}
    assert by["awc"]["points"] == 100 and by["oc"]["class"] == "low" and by["oc"]["points"] == 80 and by["slope"]["class"] == "nearly level" and by["slope"]["points"] == 0
    best = max(AWC_MM_PER_M, key=AWC_MM_PER_M.get); assert ra.vulnerability({"texture_class": best}, None)["factors"][0]["points"] == 0
    for gkg, label in ((4.9, "low"), (5.0, "medium"), (7.4, "medium"), (7.5, "high"), (20, "high")): assert ra._band(gkg / 10, ra.OC_CLASSES)[0] == label, gkg
    for pct, label in ((0.9, "nearly level"), (1, "very gently sloping"), (4.9, "gently sloping"), (10, "strongly sloping"), (24, "moderately steep"), (49, "very steep"), (70, "extremely steep")): assert ra._band(pct, ra.SLOPE_CLASSES)[0] == label, pct
    for ai, label in ((0.04, "hyper-arid"), (0.05, "arid"), (0.2, "semi-arid"), (0.5, "dry sub-humid"), (0.65, "humid"), (2, "humid")): assert ra._band(ai, ra.ARIDITY_CLASSES)[0] == label, ai


def test_vulnerability_skips_missing_factors_and_averages_the_rest():
    v = ra.vulnerability({"organic_carbon_gkg": 2.0}, None); assert v["factors_used"] == 1 and v["score"] == 80.0 and v["class"] == "High"
    assert ra.vulnerability({}, None)["score"] is None and ra.vulnerability({}, None)["class"] == "unknown"
    full = ra.vulnerability(copy.deepcopy(RJ), GW); assert full["factors_used"] == 5 and full["score"] == round(sum(f["points"] for f in full["factors"]) / 5, 1)
    assert [ra.vulnerability({}, {"status": "ok", "trend": t, "slope_cm_per_year": 0})["score"] for t in ("declining", "stable", "rising")] == [80.0, 25.0, 0.0]
    assert ra.vulnerability({}, {"status": "no_data"})["factors_used"] == 0


def test_aridity_uses_unep_classes_and_tolerates_missing_climate():
    a = ra.aridity_index(copy.deepcopy(RJ)); assert a["class"] == "semi-arid" and a["value"] == round(RJ["annual_rain_mm"] / a["annual_pet_mm"], 2)
    assert ra.aridity_index({}) is None and ra.aridity_index({"monthly_temp_c": [10] * 11, "annual_rain_mm": 500}) is None and ra.aridity_index({"monthly_temp_c": [None] + [10] * 11, "annual_rain_mm": 500}) is None


def test_soil_panel_uses_soil_health_card_terms_and_scores_nothing_it_should_not():
    def row(ph): return next(r for r in ra.soil_condition({"ph": ph})["properties"] if r["id"] == "ph")
    for ph, cls in ((6.4, "acidic"), (6.5, "neutral"), (7.0, "neutral"), (7.1, "alkaline")): assert row(ph)["class"] == cls, ph
    assert row(5.2)["interpretation"].count("general knowledge") == 1 and row(9.0)["interpretation"].count("general knowledge") == 1 and "general knowledge" not in row(6.8)["interpretation"]
    assert row(6.8)["scored"] is False, "pH is a fertility constraint, never part of a drought score"
    sc = ra.soil_condition(copy.deepcopy(RJ)); assert {r["id"] for r in sc["properties"]} == {"ph", "oc", "texture", "slope"} and any("nitrogen" in n for n in sc["not_measurable"]) and "soil test" in sc["advice"]
    assert next(r for r in sc["properties"] if r["id"] == "texture")["class"] == "freely drained (light)" and ra.soil_condition({})["properties"] == []


# ---------------- assembly and orchestration ----------------
def test_assembly_is_consistent_and_carries_everything_a_report_needs():
    a = ra.assemble_assessment(inputs())
    assert a["stress"]["score"] == ra.combine(ra.family_scores(a["indicators"])) and a["stress"]["class"] == ra.stress_level(a["stress"]["score"]) and len(a["indicators"]) == 5
    assert a["risk"] == ra.risk_class(a["stress"]["score"], a["vulnerability"]["score"]) and a["confidence"]["coverage"] == "4 of 4 indicator families" and a["sensitivity"]["range"] is not None
    assert a["legacy"]["risk_score"] == 42 and a["soil_condition"] and a["climate"]["annual_rain_mm"] == RJ["annual_rain_mm"] and a["citations"] and a["limitations"] and len(a["methodology"]) == 6
    assert a["summary"]["drivers"] and all(": " in d for d in a["summary"]["drivers"]) and a["summary"]["unavailable"] == []


def test_assembly_degrades_honestly_when_sources_fail():
    a = ra.assemble_assessment(inputs(vdi=None, prec=None, gw=None, errors={"vdi": "boom", "prec": "timeout"}))
    assert [i["status"] for i in a["indicators"]] == ["unavailable", "ok", "unavailable", "ok", "unavailable"] and a["errors"] == {"vdi": "boom", "prec": "timeout"}
    assert a["stress"]["families"]["rainfall"]["score"] is None and a["confidence"]["missing"] == ["rainfall", "groundwater"] and a["confidence"]["label"] != "high"
    assert any("boom" in u for u in a["summary"]["unavailable"]) and a["stress"]["score"] == ra.combine({"vegetation": 40.0, "soil_moisture": 40.0})
    none = ra.assemble_assessment({"errors": {}, "as_of": "x"}); assert none["stress"]["score"] is None and none["risk"]["class"] == "Cannot assess" and none["soil_condition"] is None and none["legacy"] is None


def _patch_sources(monkeypatch, fail=(), calls=None):
    import app.services.gee_remote_sensing as g, app.services.agri.baseline as b, app.services.agri.groundwater as gw, app.services.agri.precipitation as p, app.services.agri.risk_scoring as rs, app.services.agri.root_zone as rz
    def mk(name, val):
        def f(**k):
            if calls is not None: calls[name] = k
            if name in fail: raise RuntimeError(f"{name} down")
            return val
        return f
    monkeypatch.setattr(g, "compute_vegetation_drought_index", mk("vdi", VDI)); monkeypatch.setattr(b, "compute_seasonal_baseline", mk("baseline", BASE)); monkeypatch.setattr(gw, "compute_groundwater_trend", mk("gw", GW))
    monkeypatch.setattr(p, "compute_precipitation_context", mk("prec", PREC)); monkeypatch.setattr(rs, "compute_risk_score", mk("v1", V1))
    monkeypatch.setattr(rz, "fetch_root_zone", mk("rz", {"smap": {"sm_surface": .1, "sm_rootzone": .21, "sm_rootzone_pctl": 15.0}, "era5": {}}))


def test_orchestration_isolates_failures_and_asks_for_a_30_year_record(monkeypatch):
    calls = {}; _patch_sources(monkeypatch, fail=("vdi", "gw"), calls=calls)
    a = ra.compute_risk_assessment({"type": "Polygon"}, "2026-11-10", profile=copy.deepcopy(RJ))
    assert set(a["errors"]) == {"vdi", "gw"} and "vdi down" in a["errors"]["vdi"] and [i["status"] for i in a["indicators"]] == ["unavailable", "ok", "ok", "ok", "unavailable"]
    assert calls["prec"]["years_back"] == 30 and calls["prec"]["recent_days"] == 90 and a["stress"]["score"] is not None and a["legacy"]["risk_score"] == 42


def test_vhi_window_stays_inside_one_calendar_year(monkeypatch):
    calls = {}; _patch_sources(monkeypatch, calls=calls)
    ra.compute_risk_assessment({"type": "Polygon"}, "2026-11-10", profile=copy.deepcopy(RJ)); assert (calls["vdi"]["start_date"], calls["vdi"]["end_date"]) == ("2026-09-11", "2026-11-10")
    ra.compute_risk_assessment({"type": "Polygon"}, "2026-03-01", profile=copy.deepcopy(RJ)); assert calls["vdi"]["start_date"] == "2026-01-01"
    ra.compute_risk_assessment({"type": "Polygon"}, "2026-01-05", profile=copy.deepcopy(RJ)); assert (calls["vdi"]["start_date"], calls["vdi"]["end_date"]) == ("2025-11-01", "2025-12-31")
