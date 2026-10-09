"""Unit tests for the dry-window soil-water balance and its effect on rabi / zaid suitability. Pure Python.  python tests/test_water_balance.py"""
import copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
from agri_fixtures import LOCATIONS
from app.services.agri import water_balance as wbm
from app.services.agri.crop_requirements import SEASON_MONTHS, TEXTURE_NAMES
from app.services.agri.crop_suitability import score_crops


def _crop(loc, cid, irrigated, **over):
    p = copy.deepcopy(LOCATIONS[loc]); p.update(over)
    return next(c for c in score_crops(p, irrigated)["crops"] if c["crop_id"] == cid)


def test_awc_table_covers_every_texture_class_and_is_physically_ordered():
    assert set(wbm.AWC_MM_PER_M) == set(TEXTURE_NAMES)
    assert wbm.AWC_MM_PER_M[12] < wbm.AWC_MM_PER_M[11] < wbm.AWC_MM_PER_M[9] < wbm.AWC_MM_PER_M[7] < wbm.AWC_MM_PER_M[4]   # sand < loamy sand < sandy loam < loam < clay loam


def test_thornthwaite_is_in_a_sane_range_and_monotone_in_temperature():
    hot = wbm.thornthwaite_pet([27.0] * 12, 17.0); mild = wbm.thornthwaite_pet([18.0] * 12, 30.0)
    assert 1200 < sum(hot) < 2200 and 500 < sum(mild) < 1300 and sum(hot) > sum(mild)
    assert wbm.thornthwaite_pet([0.0] * 12) == [0.0] * 12
    assert wbm.thornthwaite_pet([20.0] * 11 + [None]) is None
    assert wbm.thornthwaite_pet([20.0] * 12, None) is not None                      # latitude optional


def test_bucket_never_exceeds_capacity_and_more_rain_never_stores_less():
    pet = wbm.thornthwaite_pet(LOCATIONS["punjab"]["monthly_temp_c"], 30.9)
    low = wbm.stored_water_by_month(LOCATIONS["punjab"]["monthly_rain_mm"], pet, 150.0)
    wet = wbm.stored_water_by_month([r * 2 for r in LOCATIONS["punjab"]["monthly_rain_mm"]], pet, 150.0)
    assert all(0 <= s <= 150.0 + 1e-9 for s in low + wet) and all(w >= l - 1e-9 for w, l in zip(wet, low))
    small = wbm.stored_water_by_month([r * 2 for r in LOCATIONS["punjab"]["monthly_rain_mm"]], pet, 60.0)
    assert all(a <= b + 1e-9 for a, b in zip(small, wet))                           # smaller bucket never holds more


def test_window_wraps_the_year_end_and_uses_the_month_before_the_window():
    p = LOCATIONS["ratnagiri"]; wb = wbm.build_water_balance(p["monthly_rain_mm"], p["monthly_temp_c"], 4, 17.0)
    w = wbm.window_balance(wb, p["monthly_rain_mm"], SEASON_MONTHS["rabi"])
    assert abs(w["window_rain_mm"] - sum(p["monthly_rain_mm"][m - 1] for m in (11, 12, 1, 2, 3))) < 1e-6
    assert w["stored_mm"] == wb["stored_mm_end_of_month"][9]                        # end of October


# ───── behaviour on the regional fixtures
def test_rainfed_dry_season_crops_need_irrigation_at_a_wet_coast_but_not_at_a_gangetic_site():
    for cid in ("jowar_sorghum", "maize", "mustard", "potato"):
        coast = _crop("ratnagiri", cid, False)["seasonal"]["rabi"]
        assert coast["category"] in ("low", "very_low") and coast["score"] < 0.5, (cid, coast)
    for loc in ("punjab", "uttar_pradesh"):
        w = _crop(loc, "wheat", False)["factors"]["water"]
        assert w["balance"]["mai"] >= 0.8                                           # stored post-monsoon water + winter rain cover demand


def test_irrigation_lifts_dry_season_scores_at_a_wet_coast():
    for cid in ("jowar_sorghum", "maize", "gram_chickpea", "mustard"):
        a = _crop("ratnagiri", cid, False)["seasonal"]["rabi"]["score"]; b = _crop("ratnagiri", cid, True)["seasonal"]["rabi"]["score"]
        assert b >= a + 0.2, (cid, a, b)


def test_rice_leads_the_live_ratnagiri_profile_when_rainfed_and_no_dry_season_crop_is_high():
    out = score_crops(copy.deepcopy(LOCATIONS["ratnagiri_live"]), False)["crops"]
    assert out[0]["crop_id"] == "rice_paddy" and out[0]["category"] == "high", [(r["crop_id"], r["score"]) for r in out[:4]]
    assert all(r["category"] != "high" for r in out if r["season"] == "rabi")
    # and on the sandier fixture the same logic holds: perennial / kharif lead, nothing rabi-best is HIGH
    out2 = score_crops(copy.deepcopy(LOCATIONS["ratnagiri"]), False)["crops"]
    assert all(r["category"] != "high" for r in out2 if r["season"] == "rabi")


def test_semi_arid_rainfed_mustard_is_stressed_but_not_zeroed():
    r = _crop("rajasthan_semiarid", "mustard", False)
    assert 0.1 < r["seasonal"]["rabi"]["score"] < 0.9 and r["category"] != "unsuitable"


def _zaid_water(loc, cid, irrigated):
    r = next(c for c in score_crops(copy.deepcopy(LOCATIONS[loc]), irrigated, season="zaid")["crops"] if c["crop_id"] == cid)
    return r["components"]["water_score"]


def test_summer_water_score_depends_on_irrigation_everywhere():
    for loc in ("punjab", "rajasthan_semiarid", "uttar_pradesh", "ratnagiri_live"):
        assert _zaid_water(loc, "groundnut", True) > _zaid_water(loc, "groundnut", False), loc


def test_missing_texture_falls_back_to_the_envelope_and_never_scores_lower():
    for cid in ("wheat", "mustard", "groundnut"):
        full = _crop("punjab", cid, True); gap = _crop("punjab", cid, True, texture_class=None, texture_name=None)
        assert gap["factors"]["water"]["balance"] is None and gap["score"] >= full["score"] - 1e-9


def test_soil_depth_caveat_is_shown_for_dry_window_estimates_and_lowers_confidence():
    r = next(c for c in score_crops(copy.deepcopy(LOCATIONS["ratnagiri_live"]), False, season="rabi")["crops"] if c["crop_id"] == "maize")
    assert any(c["factor"] == "soil_depth" and c["scored"] is False for c in r["caveats"])
    assert r["confidence"] <= 0.9


def test_root_zone_override_reaches_awc_at_call_time():
    from app.services.agri import water_balance as wb
    base = wb.awc_mm(6)
    old = wb.ROOT_ZONE_M
    try:
        wb.ROOT_ZONE_M = old * 1.5
        assert abs(wb.awc_mm(6) - base * 1.5) < 1e-9          # was a silent no-op while the default was bound at import time
        assert abs(wb.awc_mm(6, 1.0) - wb.AWC_MM_PER_M[6]) < 1e-9   # explicit depth still wins
    finally:
        wb.ROOT_ZONE_M = old
    assert wb.awc_mm(None) is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]; failed = 0
    for f in fns:
        try: f(); print("ok  ", f.__name__)
        except AssertionError as e: failed += 1; print("FAIL", f.__name__, "->", str(e)[:300])
    print(f"{len(fns) - failed}/{len(fns)} passed"); sys.exit(1 if failed else 0)
