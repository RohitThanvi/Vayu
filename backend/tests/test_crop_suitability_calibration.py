"""
Regression tests for the crop-suitability calibration fixes (pure Python, no GEE).
Run:  cd backend && python -m pytest tests/test_crop_suitability_calibration.py   (or: python tests/test_crop_suitability_calibration.py)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.agri.crop_requirements import CROPS
from app.services.agri.crop_suitability import score_crop, score_crops

BY_ID = {c["id"]: c for c in CROPS}


def profile(annual, monthly_rain, temp=None, ph=7.0, tex=4):
    return {"monthly_temp_c": temp or [18, 21, 27, 32, 34, 33, 30, 29, 29, 27, 22, 17],
            "monthly_rain_mm": monthly_rain, "annual_rain_mm": annual, "ph": ph, "texture_class": tex,
            "texture_name": "Clay loam", "organic_carbon_gkg": 10, "slope_pct": 1,
            # fields score_crops() echoes back in its response
            "mode": "aoi", "annual_mean_temp_c": 25.0, "climate_years": "2016-2025",
            "value_source": {}, "sources": {}}


# humid east (West-Bengal-like): ~1600 mm/yr, nearly all in Jun-Sep, winter dry
HUMID = profile(1600, [10, 25, 20, 40, 120, 300, 380, 360, 280, 120, 25, 5])
# semi-arid Rajasthan-like: ~560 mm/yr
ARID = profile(560, [5, 8, 6, 3, 15, 70, 190, 160, 70, 15, 3, 3])


def test_wheat_not_penalised_for_monsoon_rain():
    r = score_crop(BY_ID["wheat"], HUMID, irrigation_available=True)
    assert r["factors"]["water"]["score"] >= 0.9, r["factors"]["water"]      # was ~0.0 before the fix
    assert r["factors"]["water"]["status"] != "excess_rain"


def test_rabi_deficit_side_still_uses_annual_rain():
    # Bharatpur-style fix must be preserved: dry annual rain is still a deficit w/o irrigation
    dry = score_crop(BY_ID["wheat"], ARID, irrigation_available=False)["factors"]["water"]
    assert dry["score"] < 1.0 and dry["status"] in ("supplemental_irrigation", "irrigation_required")
    # and the old false-zero stays fixed (annual 560 > abs_min 300)
    assert dry["score"] > 0.0


def test_rabi_excess_still_fires_on_genuinely_wet_winter():
    wet_winter = profile(2600, [380, 300, 250, 200, 200, 100, 100, 100, 100, 100, 300, 400])
    assert score_crop(BY_ID["wheat"], wet_winter, True)["factors"]["water"]["status"] == "excess_rain"


def test_kharif_and_perennial_water_logic_unchanged():
    # rice (kharif) and mango (perennial) must behave exactly as before: excess uses their own window
    rice = score_crop(BY_ID["rice_paddy"], ARID, False)["factors"]["water"]
    assert rice["status"] in ("irrigation_required", "supplemental_irrigation")
    mango = score_crop(BY_ID["mango"], HUMID, True)["factors"]["water"]
    assert mango["value"] == 1600


def test_jowar_not_zeroed_at_monsoon_rainfall():
    # Jun-Oct rain ~ 650 mm (Marathwada-like) must be fully optimal, 1000 mm still scoring
    mid = profile(800, [0, 0, 0, 0, 20, 110, 200, 180, 120, 40, 5, 0])
    assert score_crop(BY_ID["jowar_sorghum"], mid, False)["factors"]["water"]["score"] == 1.0
    wet = profile(1250, [0, 0, 0, 0, 30, 200, 330, 300, 200, 40, 5, 0])        # Jun-Oct = 1070 mm
    assert score_crop(BY_ID["jowar_sorghum"], wet, False)["factors"]["water"]["score"] > 0.3


def test_tie_break_is_not_alphabetical():
    ranked = score_crops(HUMID, True)["crops"]
    names_by_rank = [r["name"] for r in ranked if r["score"] == ranked[0]["score"]]
    # among crops tied at the top score, the one with more headroom elsewhere comes first
    def mean_f(r):
        v = [f["score"] for f in r["factors"].values() if f["score"] is not None]; return sum(v) / len(v)
    tied = [r for r in ranked if r["score"] == ranked[0]["score"]]
    assert [round(mean_f(r), 4) for r in tied] == sorted([round(mean_f(r), 4) for r in tied], reverse=True)


def test_output_schema_unchanged():
    r = score_crops(HUMID, True)
    assert set(r) >= {"profile", "irrigation_available", "crops", "unscored", "requirements_source", "disclaimer"}
    assert all({"crop_id", "score", "rating", "limiting_factor", "factors", "water_status", "amendments"} <= set(c) for c in r["crops"])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns: f(); print("ok  ", f.__name__)
    print(f"{len(fns)} passed")
