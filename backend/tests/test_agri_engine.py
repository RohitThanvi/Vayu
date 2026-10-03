"""
Permanent agricultural sanity suite + engine invariants. Pure Python (no GEE, no network).
  cd backend && python tests/test_agri_engine.py        (or: python -m pytest tests/test_agri_engine.py)
Assertions are QUALITATIVE (categories, directions of change, invariants) - never exact scores.
"""
import copy, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
from agri_fixtures import LOCATIONS
from app.services.agri.crop_requirements import CROPS
from app.services.agri.crop_profile import normalize_crop, validate_crop
from app.services.agri.calibration import Calibrator, fit_isotonic
from app.services.agri.suitability_engine import score_crop_v2, rank_crops
from app.services.agri.crop_suitability import score_crops
from app.services.agri.evidence import build_evidence

BY = {c["id"]: c for c in CROPS}


def crop_result(loc, crop_id, irrigated):
    out = score_crops(copy.deepcopy(LOCATIONS[loc]), irrigated)
    return next(r for r in out["crops"] if r["crop_id"] == crop_id)


# ───────────────────────── A. agricultural sanity: compatible combinations are never rejected on water logic
# (location, crop, irrigated?) - each is an established crop-location pairing
COMPATIBLE = [
    ("ratnagiri", "mango", False), ("ratnagiri", "rice_paddy", False), ("ratnagiri", "guava", False),
    ("ratnagiri", "amla_gooseberry", False), ("ratnagiri", "groundnut", False),
    ("rajasthan_semiarid", "bajra_pearl_millet", False), ("rajasthan_semiarid", "mustard", True),
    ("punjab", "wheat", True), ("punjab", "rice_paddy", True),
    ("madhya_pradesh", "soybean", False), ("uttar_pradesh", "wheat", True),
]


def test_compatible_pairs_not_rejected_by_water_logic():
    for loc, cid, irr in COMPATIBLE:
        r = crop_result(loc, cid, irr)
        assert r["category"] != "unsuitable", (loc, cid, r["category"], r["limiting_factors"]["primary"])
        assert not r["constraints"]["hard"], (loc, cid)
        assert r["components"]["water_score"] >= 0.5, (loc, cid, "water logic rejected an established pairing", r["factors"]["water"])


def test_ratnagiri_mango_not_unsuitable_and_not_water_limited():
    r = crop_result("ratnagiri", "mango", False)
    assert r["category"] in ("moderate", "high") and r["score"] > 0
    p = r["limiting_factors"]["primary"]
    assert p is None or p["factor"] != "water", p
    assert r["factors"]["water"]["status"] == "excess_rain"       # rain IS above the generic ceiling ...
    assert r["factors"]["water"]["effective_score"] >= 0.9         # ... but it is not what limits the crop here


def test_generic_mechanism_mango_not_zeroed_even_without_dry_window_attribute():
    """The softening must work from drainage evidence alone, with no crop-specific declaration."""
    c = copy.deepcopy(BY["mango"]); c["rainfall_seasonality"] = None
    r = score_crop_v2(c, LOCATIONS["ratnagiri"], False)
    assert r["category"] != "unsuitable" and r["score"] >= 0.4, r["score"]
    assert r["limiting_factors"]["primary"]["component"] == "excess"


def test_dry_window_penalises_year_round_wet_sites_by_the_same_rule():
    wet = dict(LOCATIONS["ratnagiri"]); wet["monthly_rain_mm"] = [300] * 12; wet["annual_rain_mm"] = 3600.0
    r = score_crop_v2(BY["mango"], wet, False)
    assert r["score"] < crop_result("ratnagiri", "mango", False)["score"]
    assert r["limiting_factors"]["primary"]["component"] == "seasonality"


# ───────────────────────── A2. seasonal suitability
from app.services.agri.crop_seasons import GROWING_SEASONS


def test_every_seasonal_crop_has_observed_seasons_and_a_primary():
    for c in CROPS:
        if c["season"] == "perennial":
            continue
        info = GROWING_SEASONS[c["id"]]
        assert info["seasons"] and info["primary"] in ("kharif", "rabi", "zaid")
        assert abs(sum(info["area_share"].values()) - 1.0) < 0.01
        assert all(info["area_share"][s] >= 0.05 for s in info["seasons"])


def test_best_season_is_the_highest_scoring_eligible_season_and_seasonal_block_is_consistent():
    for loc in LOCATIONS:
        for r in score_crops(copy.deepcopy(LOCATIONS[loc]), True)["crops"]:
            if r["best_season"] == "perennial":
                continue
            assert set(r["seasonal"]) <= set(r["eligible_seasons"])
            assert r["score"] == max(e["score"] for e in r["seasonal"].values())
            assert r["seasonal"][r["best_season"]]["score"] == r["score"]
            assert r["best_season"].capitalize()[:4] in r["season_highlight"] or "Summer" in r["season_highlight"]


def test_season_filter_ranks_only_crops_grown_in_that_season_and_lists_the_rest():
    out = score_crops(copy.deepcopy(LOCATIONS["punjab"]), True, season="rabi")
    ids = {r["crop_id"] for r in out["crops"]}
    assert "wheat" in ids and "soybean" not in ids and "cotton" not in ids
    assert {n["crop_id"] for n in out["not_grown_in_season"]} >= {"soybean", "cotton", "bajra_pearl_millet"}
    assert all(r["season"] in ("rabi", "perennial") for r in out["crops"])
    assert out["season_requested"] == "rabi"


def test_summer_vs_winter_is_reported_when_a_crop_is_grown_in_both():
    """A heat-loving crop must score differently across windows and the highlight must say where it does well / badly."""
    hot_dry = copy.deepcopy(LOCATIONS["rajasthan_semiarid"])
    r = next(c for c in score_crops(hot_dry, True)["crops"] if c["crop_id"] == "onion")
    sc = {s: e["score"] for s, e in r["seasonal"].items()}
    assert len(sc) == 3 and len(set(sc.values())) > 1, sc
    assert "Grows best in" in r["season_highlight"]
    assert any(e["hard_limit"] for e in r["seasonal"].values()) or min(sc.values()) < max(sc.values())


def test_zaid_depends_on_irrigation_but_kharif_in_humid_site_does_not():
    dry = copy.deepcopy(LOCATIONS["rajasthan_semiarid"])
    a = next(c for c in score_crops(dry, False)["crops"] if c["crop_id"] == "groundnut")["seasonal"]["zaid"]["score"]
    b = next(c for c in score_crops(dry, True)["crops"] if c["crop_id"] == "groundnut")["seasonal"]["zaid"]["score"]
    assert b > a, (a, b)


def test_invalid_season_is_rejected():
    try:
        score_crops(copy.deepcopy(LOCATIONS["punjab"]), True, season="winter")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_paddy_is_not_penalised_for_monsoon_rain_at_a_high_rainfall_coast():
    r = next(c for c in score_crops(copy.deepcopy(LOCATIONS["ratnagiri"]), False, season="kharif")["crops"] if c["crop_id"] == "rice_paddy")
    assert r["seasonal"]["kharif"]["limiting_label"] != "Excess rainfall"


# ───────────────────────── A3. sourced range corrections and caveats
def _factor(loc, cid, factor, irrigated=True, **over):
    prof = copy.deepcopy(LOCATIONS[loc]); prof.update(over)
    r = next(c for c in score_crops(prof, irrigated)["crops"] if c["crop_id"] == cid)
    return r, r["factors"][factor]["score"]


def test_wheat_ph_range_accepts_normal_indo_gangetic_soils_but_still_penalises_sodic():
    _, ok = _factor("punjab", "wheat", "ph", ph=8.0)
    assert ok == 1.0                                              # pH 8.0 is normal non-sodic wheat soil (sodic threshold 8.2)
    _, mid = _factor("punjab", "wheat", "ph", ph=8.8)
    _, hi = _factor("punjab", "wheat", "ph", ph=9.4)
    assert 0 < mid < 1.0 and hi == 0.0                            # declines through the sodic range, none beyond the limit
    _, acid = _factor("punjab", "wheat", "ph", ph=5.0)
    assert acid == 0.0


def test_groundnut_ph_optimum_extends_to_7_5_only():
    assert _factor("rajasthan_semiarid", "groundnut", "ph", ph=7.4)[1] == 1.0
    assert _factor("rajasthan_semiarid", "groundnut", "ph", ph=8.2)[1] < 1.0


def test_unreviewed_crops_keep_their_original_ph_ranges():
    by = {c["id"]: c for c in CROPS}
    assert by["maize"]["ph"] == (4.5, 5.0, 7.0, 8.5) and by["soybean"]["ph"][1:3] == by["soybean"]["ph"][1:3]
    assert "ph_override_note" not in by["maize"] and "ph_override_note" not in by["soybean"]


def test_rice_slope_is_a_soft_terracing_requirement_not_a_cliff():
    flat = _factor("ratnagiri", "rice_paddy", "slope", slope_pct=2.0)
    mid = _factor("ratnagiri", "rice_paddy", "slope", slope_pct=12.0)
    steep = _factor("ratnagiri", "rice_paddy", "slope", slope_pct=45.0)
    assert flat[1] == 1.0 and 0.5 < mid[1] < 1.0 and steep[1] < 0.3 and steep[1] < mid[1]
    assert "terrac" in " ".join(mid[0]["evidence_summary"]).lower()
    # crops with no terraceable limit keep the old behaviour
    sm = BY["wheat"]["slope_max_pct"]
    assert abs(_factor("ratnagiri", "wheat", "slope", slope_pct=12.0)[1] - max(0.0, 1.0 - (12.0 - sm) / sm)) < 0.011


def test_humid_site_adds_unscored_disease_caveat_and_lowers_confidence_for_chickpea_only():
    wet, _ = _factor("ratnagiri", "gram_chickpea", "ph")
    dry, _ = _factor("rajasthan_semiarid", "gram_chickpea", "ph")
    has = lambda r: [c for c in r["caveats"] if c["factor"] == "humidity_disease"]
    assert has(wet) and not has(dry) and has(wet)[0]["scored"] is False
    assert wet["confidence"] < dry["confidence"]
    assert any("NOT modelled" in l for l in wet["evidence_summary"])
    assert not has(_factor("ratnagiri", "mustard", "ph")[0])                 # no sourced humidity trait -> no claim


def test_caveat_does_not_change_the_score():
    r, _ = _factor("ratnagiri", "gram_chickpea", "ph")
    prof = copy.deepcopy(LOCATIONS["ratnagiri"])
    c = copy.deepcopy(BY["gram_chickpea"]); c.pop("humidity_sensitive")
    assert score_crop_v2(c, prof, True, season="rabi")["score"] == r["seasonal"]["rabi"]["score"] or r["score"] == score_crop_v2(c, prof, True, season=r["best_season"])["score"]


# ───────────────────────── B. paired irrigation tests (same site + season + candidates, irrigated vs rainfed)
def _water_pair(loc, cid):
    a, b = crop_result(loc, cid, False), crop_result(loc, cid, True)
    return a["components"]["water_score"], b["components"]["water_score"], a["score"], b["score"]


def test_irrigation_helps_water_sensitive_crops_in_dry_site():
    for cid in ("rice_paddy", "wheat", "sugarcane", "cotton", "groundnut"):
        wr, wi, sr, si = _water_pair("rajasthan_semiarid", cid)
        assert wi >= wr + 0.10, (cid, wr, wi)
        assert si >= sr, (cid, sr, si)


def test_irrigation_barely_matters_for_drought_adapted_crops_and_for_humid_sites():
    for loc, cid in (("rajasthan_semiarid", "bajra_pearl_millet"), ("rajasthan_semiarid", "jowar_sorghum"),
                     ("ratnagiri", "mango"), ("ratnagiri", "guava")):
        wr, wi, *_ = _water_pair(loc, cid)
        assert abs(wi - wr) <= 0.05, (loc, cid, wr, wi)


def test_irrigation_never_lowers_a_score():
    rng = random.Random(3)
    for _ in range(300):
        prof = _random_profile(rng)
        a = {r["crop_id"]: r["score"] for r in score_crops(copy.deepcopy(prof), False)["crops"]}
        b = {r["crop_id"]: r["score"] for r in score_crops(copy.deepcopy(prof), True)["crops"]}
        assert all(b[k] >= a[k] - 1e-9 for k in a)


# ───────────────────────── C. constraint system and explanation invariants
def _random_profile(rng):
    mr = [rng.uniform(0, 600) * rng.choice([0.05, 1]) for _ in range(12)]
    return {"monthly_temp_c": [rng.uniform(2, 40) for _ in range(12)], "monthly_rain_mm": mr, "annual_rain_mm": sum(mr),
            "annual_mean_temp_c": 25.0, "ph": rng.uniform(3.5, 9.5), "texture_class": rng.randint(1, 12), "texture_name": "x",
            "organic_carbon_gkg": rng.uniform(0, 30), "slope_pct": rng.uniform(0, 40), "mode": "aoi", "climate_years": "t",
            "value_source": {}, "sources": {}}


def test_only_a_hard_constraint_can_make_a_crop_unsuitable():
    rng = random.Random(11)
    for _ in range(400):
        for r in score_crops(_random_profile(rng), rng.random() < 0.5)["crops"]:
            if r["category"] == "unsuitable":
                assert r["constraints"]["hard"] and all(h["factor"] == "temperature" for h in r["constraints"]["hard"])
            else:
                assert r["score"] >= 0.1 - 1e-9, r["score"]          # soft floor: nothing is zeroed by a soft limitation


def test_unknown_evidence_is_not_unsuitable_and_lowers_confidence():
    full = copy.deepcopy(LOCATIONS["punjab"])
    gap = {**full, "ph": None, "texture_class": None, "texture_name": None, "organic_carbon_gkg": None, "slope_pct": None}
    a = {r["crop_id"]: r for r in score_crops(full, True)["crops"]}
    b = {r["crop_id"]: r for r in score_crops(gap, True)["crops"]}
    for cid, r in b.items():
        assert r["category"] != "unsuitable" or r["constraints"]["hard"]
        assert set(r["constraints"]["unknown"]) >= {"ph", "texture", "organic_carbon", "slope"}
        assert r["confidence"] < a[cid]["confidence"]
        assert r["score"] >= a[cid]["score"] - 1e-9                      # fewer known factors can't lower a Liebig minimum


def test_explanation_is_generated_from_factors_that_reduced_the_score():
    rng = random.Random(5)
    for _ in range(300):
        for r in score_crops(_random_profile(rng), rng.random() < 0.5)["crops"]:
            p = r["limiting_factors"]["primary"]
            if r["limiting_factor"] == "water":
                assert p["reduction"] >= 0.05 and p["component"] in ("deficit", "excess", "seasonality")
                assert r["limiting_label"] != "Rainfall / water supply"      # never the ambiguous generic label
            if p is None:
                assert r["limiting_factor"] == "none" and r["limiting_label"] == "No significant limitation"
                assert r["raw_score"] >= 0.94
            else:
                assert abs((1 - p["score"]) - p["reduction"]) < 0.011
                assert p["reduction"] == max(l["reduction"] for l in [p] + r["limiting_factors"]["secondary"])


def test_water_message_distinguishes_excess_from_deficit():
    assert "Excess rainfall" in crop_result("ratnagiri", "bajra_pearl_millet", False)["limiting_factors"]["primary"]["message"]
    assert "Rainfall deficit" in crop_result("rajasthan_semiarid", "rice_paddy", False)["limiting_factors"]["primary"]["message"]


# ───────────────────────── D. ranking, calibration, schema
def test_ranking_is_deterministic_and_tie_aware():
    a = score_crops(copy.deepcopy(LOCATIONS["ratnagiri"]), False)["crops"]
    b = score_crops(copy.deepcopy(LOCATIONS["ratnagiri"]), False)["crops"]
    assert [r["crop_id"] for r in a] == [r["crop_id"] for r in b]
    for r in a:
        same = [o for o in a if o["rank"] == r["rank"]]
        assert r["tied_with"] == len(same) - 1
        assert all(o["calibrated_score"] == r["calibrated_score"] for o in same)
    assert [r["calibrated_score"] for r in a] == sorted([r["calibrated_score"] for r in a], reverse=True)


def test_calibration_is_identity_until_fitted_and_monotone_when_fitted():
    assert Calibrator().apply(0.37) == 0.37
    knots = fit_isotonic([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], [0, 1, 0, 0, 1, 1])
    ys = [k[1] for k in knots]
    assert ys == sorted(ys)
    cal = Calibrator({"global": knots}, "test")
    xs = [i / 20 for i in range(21)]
    assert [cal.apply(x) for x in xs] == sorted(cal.apply(x) for x in xs)


def test_ranking_uses_calibrated_scores():
    ev = build_evidence(LOCATIONS["punjab"]); cal = Calibrator({"global": [[0.0, 0.0], [1.0, 0.5]]}, "halve")
    rs = rank_crops([score_crop_v2(c, LOCATIONS["punjab"], True, ev=ev, calibrator=cal) for c in CROPS])
    assert all(abs(r["calibrated_score"] - r["raw_score"] / 2) < 0.011 for r in rs)


def test_every_crop_profile_is_schema_valid():
    assert {c["id"]: validate_crop(c) for c in CROPS if validate_crop(c)} == {}
    assert normalize_crop(BY["bajra_pearl_millet"])["excess_sensitivity"] == "high"
    assert normalize_crop(BY["rice_paddy"])["excess_sensitivity"] == "tolerant"
    assert normalize_crop(BY["mango"])["kind"] == "perennial"


def test_response_exposes_the_requested_components():
    r = crop_result("punjab", "wheat", True)
    for k in ("temperature_score", "rainfall_score", "water_score", "soil_score", "season_score", "irrigation_score",
              "remote_sensing_score", "climate_score", "risk_penalty", "raw_score", "calibrated_score", "final_rank"):
        assert k in r["components"], k
    assert r["components"]["remote_sensing_score"] is None            # not sampled by the suitability pipeline (yet)
    for k in ("category", "confidence", "constraints", "limiting_factors", "evidence_summary", "rank"):
        assert k in r


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for f in fns:
        try: f(); print("ok  ", f.__name__)
        except AssertionError as e: failed += 1; print("FAIL", f.__name__, "->", str(e)[:300])
    print(f"{len(fns) - failed}/{len(fns)} passed"); sys.exit(1 if failed else 0)
