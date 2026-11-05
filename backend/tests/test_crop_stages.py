"""FAO-56 crop-stage / Kc module. Pure Python.  pytest tests/test_crop_stages.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services.agri import crop_stages as cs
from app.services.agri.crop_requirements import CROPS_BY_ID

# Hand-copied from FAO-56 Table 11 (stage lengths) and Table 12 (Kc) as read from fao.org: guards against silent edits to the data.
FAO = {"wheat": ((15, 25, 50, 30), .30, 1.15, .325), "barley": ((15, 25, 50, 30), .30, 1.15, .25), "maize": ((20, 35, 40, 30), .30, 1.20, .475),
       "bajra_pearl_millet": ((15, 25, 40, 25), .30, 1.00, .30), "jowar_sorghum": ((20, 35, 40, 30), .30, 1.05, .55), "groundnut": ((25, 35, 45, 25), .40, 1.15, .60),
       "soybean": ((15, 15, 40, 15), .40, 1.15, .50), "cotton": ((30, 50, 60, 55), .35, 1.175, .60), "potato": ((25, 30, 45, 30), .50, 1.15, .75), "onion": ((15, 25, 70, 40), .70, 1.05, .75)}


@pytest.mark.parametrize("cid", list(FAO))
def test_values_match_fao56_tables(cid):
    L, ki, km, ke = FAO[cid]; c = cs.CROPS[cid]
    assert tuple(c["stages"].values()) == L and c["total_days"] == sum(L)
    assert (c["kc_ini"], c["kc_mid"], c["kc_end"]) == (ki, km, ke)


def test_exactly_the_documented_crops_and_all_exist_in_the_suitability_table():
    assert set(cs.CROPS) == set(FAO)
    assert set(cs.CROPS) <= set(CROPS_BY_ID)
    for unsupported in ("gram_chickpea", "mustard", "rice_paddy", "sugarcane"):
        assert unsupported not in cs.CROPS and cs.stage_info(unsupported, "2025-11-01", "2025-12-01")["status"] == "unsupported"


def test_dry_down_flag_follows_the_fao_0_45_rule():
    assert {c for c, v in cs.CROPS.items() if v["dry_down"]} == {"wheat", "barley", "bajra_pearl_millet"}


def test_kc_curve_is_flat_then_rises_linearly_then_falls():
    w = cs.CROPS["wheat"]
    assert cs.kc_on_day(w, 1) == .30 and cs.kc_on_day(w, 15) == .30
    assert cs.kc_on_day(w, 40) == pytest.approx(1.15) and cs.kc_on_day(w, 90) == 1.15
    assert cs.kc_on_day(w, 27) == pytest.approx(.30 + 12 / 25 * .85)            # inside development: linear
    assert cs.kc_on_day(w, 120) == pytest.approx(w["kc_end"]) and cs.kc_on_day(w, 105) == pytest.approx((1.15 + w["kc_end"]) / 2)
    assert cs.kc_on_day(w, 0) is None and cs.kc_on_day(w, 121) is None
    ks = [cs.kc_on_day(w, d) for d in range(1, 121)]
    assert max(ks) == 1.15 and ks.index(max(ks)) + 1 == 40


def test_stage_info_stages_dates_and_demand():
    r = cs.stage_info("wheat", "2025-11-15", "2025-11-15")                       # sowing day = day 1
    assert r["status"] == "ok" and r["das"] == 1 and r["stage"] == "initial" and r["kc"] == .3 and r["relative_demand"] == round(.3 / 1.15, 2)
    r = cs.stage_info("wheat", "2025-11-15", "2026-01-20")                       # day 67 -> mid-season (41-90)
    assert r["das"] == 67 and r["stage"] == "mid-season" and r["relative_demand"] == 1.0 and r["stage_day"] == 27 and r["days_left_in_stage"] == 90 - 67
    assert cs.stage_info("wheat", "2025-11-15", "2026-03-10")["stage"] == "late season"      # day 116
    cal = r["calendar"]
    assert [c["stage"] for c in cal] == list(cs.STAGE_NAMES) and cal[0]["start_date"] == "2025-11-15"
    assert all(cal[i]["end_das"] + 1 == cal[i + 1]["start_das"] for i in range(3)) and cal[-1]["end_das"] == 120
    assert cal[-1]["end_date"] == "2026-03-14"                                   # 2025-11-15 + 119 days


def test_stage_info_never_raises_and_says_why():
    assert cs.stage_info("wheat", "2025-11-15", "2025-11-01")["status"] == "before_sowing"
    assert cs.stage_info("wheat", "2025-11-15", "2026-06-01")["status"] == "after_cycle"
    assert cs.stage_info("wheat", "15/11/2025", "2025-12-01")["status"] == "invalid_sowing_date"
    assert cs.stage_info("wheat", None, "2025-12-01")["status"] == "invalid_sowing_date"
    assert cs.stage_info(None, "2025-11-15", "2025-12-01")["status"] == "unsupported"
    assert "caveat" in cs.stage_info("wheat", "2025-11-15", "2025-12-01")
