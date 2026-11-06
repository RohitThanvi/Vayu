"""NDVI as SUPPORTING evidence in the suitability engine (not part of the min() score). Pure Python, no GEE.  pytest tests/test_ndvi_evidence.py
These fixtures check MECHANICS (non-interference, season windows, labelling). They are not a validation of NDVI's usefulness: that needs real ground data."""
import copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from agri_fixtures import LOCATIONS
from app.services.agri import engine_config as cfg
from app.services.agri.crop_requirements import SEASON_MONTHS
from app.services.agri.crop_suitability import score_crops
from app.services.agri.evidence import build_evidence

RABI, KHARIF = SEASON_MONTHS["rabi"], SEASON_MONTHS["kharif"]


def with_ndvi(loc, values):
    p = copy.deepcopy(LOCATIONS[loc]); p["monthly_ndvi"] = values; p["ndvi_basis"] = "cropland"; return p


def flat(v): return [v] * 12


def by_id(out): return {r["crop_id"]: r for r in out["crops"]}


@pytest.mark.parametrize("loc,irr", [("punjab", True), ("rajasthan_semiarid", False), ("ratnagiri", False), ("himalayan_cold", False)])
@pytest.mark.parametrize("level", [0.6, 0.3, 0.05])
def test_ndvi_never_changes_scores_categories_or_ranking(loc, irr, level):
    base = score_crops(copy.deepcopy(LOCATIONS[loc]), irr); got = score_crops(with_ndvi(loc, flat(level)), irr)
    assert [r["crop_id"] for r in got["crops"]] == [r["crop_id"] for r in base["crops"]], "ranking must not depend on NDVI"
    b = by_id(base)
    for cid, r in by_id(got).items():
        assert (r["raw_score"], r["score"], r["category"]) == (b[cid]["raw_score"], b[cid]["score"], b[cid]["category"]), cid
        if level > cfg.NDVI_BARE:
            assert r["confidence"] == b[cid]["confidence"], cid


def test_bare_season_adds_a_caveat_and_a_small_confidence_penalty_only():
    base, got = by_id(score_crops(copy.deepcopy(LOCATIONS["punjab"]), True)), by_id(score_crops(with_ndvi("punjab", flat(0.05)), True))
    w, wb = got["wheat"], base["wheat"]
    cav = [c for c in w["caveats"] if c["factor"] == "observed_vegetation"]
    assert len(cav) == 1 and cav[0]["scored"] is False and "fallow" in cav[0]["message"]
    assert w["confidence"] == pytest.approx(wb["confidence"] - cfg.CONF_PENALTY_NDVI_BARE, abs=0.011)
    assert w["factors"]["ndvi"]["status"] == "bare" and "does not limit the score" in w["factors"]["ndvi"]["need"]


def test_vigorous_season_is_stated_as_consistent_not_as_proof_and_adds_no_confidence():
    base, got = by_id(score_crops(copy.deepcopy(LOCATIONS["punjab"]), True)), by_id(score_crops(with_ndvi("punjab", flat(0.6)), True))
    txt = " ".join(map(str, got["wheat"]["evidence_summary"]))
    assert "consistent with actively growing vegetation" in txt and "does not change the score" in txt
    assert got["wheat"]["confidence"] == base["wheat"]["confidence"]


def test_each_crop_is_judged_on_its_own_season_window():
    vals = [0.6 if m in RABI else 0.08 for m in range(1, 13)]           # green in rabi months only
    got = by_id(score_crops(with_ndvi("punjab", vals), True))
    assert got["wheat"]["factors"]["ndvi"]["status"] == "vigorous"       # rabi crop
    assert got["wheat"]["factors"]["ndvi"]["value"] == pytest.approx(0.6)
    k = next(r for r in got.values() if r["season"] == "kharif" and "ndvi" in r["factors"])
    assert k["factors"]["ndvi"]["status"] == "bare" and k["factors"]["ndvi"]["value"] == pytest.approx(0.08)


def test_components_factors_and_ablation_inputs_are_consistent():
    got = by_id(score_crops(with_ndvi("punjab", flat(0.3)), True))["wheat"]
    n = got["factors"]["ndvi"]
    assert got["components"]["remote_sensing_score"] == n["score"] == got["ablation_inputs"]["ndvi"] and 0 < n["score"] < 1
    plain = by_id(score_crops(copy.deepcopy(LOCATIONS["punjab"]), True))["wheat"]
    assert plain["components"]["remote_sensing_score"] is None and "ndvi" not in plain["factors"] and plain["ablation_inputs"].get("ndvi") is None


def test_too_few_valid_months_in_the_season_gives_no_ndvi_statement():
    months = list(RABI); vals = [None] * 12; vals[months[0] - 1] = 0.5   # one valid month in wheat's window
    assert cfg.NDVI_MIN_MONTHS > 1
    got = by_id(score_crops(with_ndvi("punjab", vals), True))["wheat"]
    assert "ndvi" not in got["factors"] and got["components"]["remote_sensing_score"] is None


def test_evidence_builder_passes_ndvi_through_and_treats_all_none_as_not_sampled():
    ev = build_evidence(with_ndvi("punjab", flat(0.4)))
    assert ev["remote_sensing"]["monthly_ndvi"] == flat(0.4) and ev["remote_sensing"]["basis"] == "cropland"
    assert build_evidence(with_ndvi("punjab", [None] * 12))["remote_sensing"] is None
    assert build_evidence(copy.deepcopy(LOCATIONS["punjab"]))["remote_sensing"] is None
    assert build_evidence(with_ndvi("punjab", flat(0.4)))["missing"] == build_evidence(copy.deepcopy(LOCATIONS["punjab"]))["missing"]


def test_thresholds_are_labelled_estimates_and_ordered():
    assert 0 < cfg.NDVI_BARE < cfg.NDVI_VIGOROUS < 1 and 0 < cfg.CONF_PENALTY_NDVI_BARE <= 0.1
