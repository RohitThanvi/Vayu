"""Root-zone + stage-aware irrigation advisory. Pure logic plus mocked data fetchers (Earth Engine is NOT reachable from the dev sandbox).  pytest tests/test_irrigation_advisory.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services.agri import irrigation_advisory as ia, root_zone as rz
from app.services.agri.crop_stages import stage_info

RZ = lambda pctl: rz.summarize_root_zone({"smap": {"sm_surface": .1, "sm_rootzone": .25, "sm_rootzone_pctl": pctl}, "era5": {"layer2_7_28cm": .2, "layer3_28_100cm": .22}})
NO_RZ = rz.summarize_root_zone({"smap": None, "era5": None, "errors": ["smap: boom"]})
LOW, OK = -40.0, 5.0                                   # rainfall anomaly %


def D(dry, anomaly, root, stage=None, rain_ok=True):
    return ia.decide_irrigation(dry, rain_ok, anomaly, "x", root, stage)


def test_classify_root_zone_thresholds_are_inclusive_at_the_upper_edge():
    assert [rz.classify_root_zone(v) for v in (None, 0, 20, 20.1, 30, 30.1, 100)] == ["unknown", "dry", "dry", "below_normal", "below_normal", "adequate", "adequate"]


def test_surface_only_fallback_keeps_v1_behaviour():
    assert D(80, LOW, NO_RZ)[0] == "irrigate_now" and D(80, OK, NO_RZ)[0] == "monitor" and D(10, OK, NO_RZ)[0] == "hold_off" and D(35, OK, NO_RZ)[0] == "monitor"
    assert D(80, None, NO_RZ, rain_ok=False)[0] == "irrigate_now"
    assert "surface SMAP only" in D(80, LOW, NO_RZ)[3][0]


def test_a_dry_surface_over_an_adequate_root_zone_is_not_an_irrigation_signal():
    rec, reason, urgency, basis = D(95, LOW, RZ(60))
    assert rec == "hold_off" and "0-5 cm surface" in reason and "60.0th percentile" in basis[0]


def test_dry_root_zone_decisions_depend_on_rainfall():
    assert D(10, LOW, RZ(10))[:3:2] == ("irrigate_now", "high")                   # even with a wet surface reading
    assert D(10, OK, RZ(10))[0] == "monitor"
    assert D(10, None, RZ(10), rain_ok=False)[0] == "irrigate_now"
    assert D(80, LOW, RZ(25))[0] == "monitor"                                      # below normal is a watch, not an alarm


def test_late_season_dry_down_crop_is_not_told_to_irrigate():
    st = stage_info("wheat", "2025-11-15", "2026-03-10")                           # late season, dry-down crop
    rec, reason, urgency, basis = D(80, LOW, RZ(5), st)
    assert (rec, urgency) == ("monitor", "low") and "allowed to dry down" in reason and "Late season" in basis[1]
    st2 = stage_info("maize", "2025-10-15", "2026-02-05")                          # day 114 -> late season, NOT a dry-down crop
    assert st2["stage"] == "late season" and not st2["dry_down_crop"] and D(80, LOW, RZ(5), st2)[0] == "irrigate_now"


def test_mid_season_raises_urgency_and_can_upgrade_a_watch():
    st = stage_info("wheat", "2025-11-15", "2026-01-20")
    assert st["stage"] == "mid-season"
    rec, reason, urgency, _ = D(50, LOW, RZ(25), st)                               # below normal + low rain -> upgrade
    assert (rec, urgency) == ("irrigate_now", "high") and "peaks" in reason
    assert D(50, OK, RZ(25), st)[0] == "monitor"                                   # rain not low -> no upgrade
    assert D(50, LOW, RZ(60), st)[0] == "hold_off"                                 # adequate root zone is never upgraded
    assert D(50, LOW, RZ(5), st)[2] == "high"


def test_unusable_stage_is_ignored_but_explained():
    bad = stage_info("wheat", "2025-11-15", "2025-10-01")
    rec, _, _, basis = D(80, LOW, RZ(5), bad)
    assert rec == "irrigate_now" and "crop stage not applied" in basis[-1]


def _patch(monkeypatch, root, moisture=None, rain=None):
    monkeypatch.setattr(ia, "compute_soil_moisture", lambda **k: {"metrics": moisture or {"data_available": True, "dry_stress_pct": 70.0, "moisture_change": -1, "end_avg_soil_moisture": .12}})
    monkeypatch.setattr(ia, "compute_precipitation_context", lambda **k: rain or {"status": "ok", "condition": "well below normal", "anomaly_pct": -40, "recent_total_mm": 3})
    monkeypatch.setattr(ia, "fetch_root_zone", root)


def test_full_path_returns_root_zone_stage_and_keeps_v1_keys(monkeypatch):
    _patch(monkeypatch, lambda aoi, d: {"smap": {"sm_surface": .1, "sm_rootzone": .2, "sm_rootzone_pctl": 12}, "era5": {"layer2_7_28cm": .18, "layer3_28_100cm": .2}})
    r = ia.compute_irrigation_advisory({"type": "Polygon"}, "2026-01-20", crop_id="wheat", sowing_date="2025-11-15")
    assert r["recommendation"] == "irrigate_now" and r["urgency"] == "high"
    assert r["root_zone"]["sm_rootzone_percentile"] == 12.0 and r["root_zone"]["era5_layer2_7_28cm"] == .18 and "15-25 cm" in r["root_zone"]["depth_note"]
    assert r["crop_stage"]["stage"] == "mid-season" and {"soil_moisture", "rainfall", "disclaimer", "method", "reason"} <= set(r)
    assert "FAO-56 indicative" in r["disclaimer"]
    plain = ia.compute_irrigation_advisory({"type": "Polygon"}, "2026-01-20")
    assert plain["crop_stage"] is None and "no crop or growth stage" in plain["disclaimer"]


def test_root_zone_failure_degrades_to_surface_read(monkeypatch):
    def boom(aoi, d): raise RuntimeError("gee down")
    _patch(monkeypatch, boom)
    r = ia.compute_irrigation_advisory({"type": "Polygon"}, "2026-01-20")
    assert r["root_zone"]["available"] is False and r["recommendation"] == "irrigate_now" and "gee down" in r["root_zone"]["source_errors"][0]


def test_no_surface_reading_still_returns_insufficient_data(monkeypatch):
    _patch(monkeypatch, lambda a, d: {}, moisture={"data_available": False})
    assert ia.compute_irrigation_advisory({"type": "Polygon"}, "2026-01-20")["recommendation"] == "insufficient_data"


# ---- fetch_root_zone against a fake Earth Engine: pin dataset ids and band names, and that one source failing leaves the other ----
class _Col:
    def __init__(self, log, n=1): self.log, self.n = log, n
    def filterBounds(self, r): return self
    def filterDate(self, a, b): return self
    def select(self, bands): self.log.append(("select", tuple(bands))); return self
    def size(self): return type("S", (), {"getInfo": lambda s: self.n})()
    def mean(self): return "IMG"


class _EE:
    def __init__(self, fail_smap=False):
        self.log, self.fail_smap = [], fail_smap
    def ImageCollection(self, cid):
        self.log.append(("collection", cid))
        if cid.startswith("NASA/SMAP") and self.fail_smap: raise RuntimeError("smap exploded")
        return _Col(self.log)


def _wire(monkeypatch, ee):
    from app.services import gee_client as g
    class Date:
        def advance(self, *a): return self
    monkeypatch.setattr(g, "ee", ee); monkeypatch.setattr(g, "_polygon_geometry", lambda aoi: "REGION"); monkeypatch.setattr(g, "_cap_end_date", lambda d: Date())
    monkeypatch.setattr(rz, "_region_mean", lambda img, region: {"sm_surface": .11, "sm_rootzone": .27, "sm_rootzone_pctl": 42.0, "volumetric_soil_water_layer_2": .21, "volumetric_soil_water_layer_3": .24})


def test_fetch_uses_the_documented_datasets_and_bands(monkeypatch):
    ee = _EE(); _wire(monkeypatch, ee)
    out = rz.fetch_root_zone({"type": "Polygon"}, "2026-01-20")
    assert ("collection", "NASA/SMAP/SPL4SMGP/008") in ee.log and ("collection", "ECMWF/ERA5_LAND/DAILY_AGGR") in ee.log
    assert ("select", ("sm_surface", "sm_rootzone", "sm_rootzone_pctl")) in ee.log and ("select", ("volumetric_soil_water_layer_2", "volumetric_soil_water_layer_3")) in ee.log
    s = rz.summarize_root_zone(out)
    assert (s["sm_rootzone_percentile"], s["sm_rootzone_0_100cm"], s["era5_layer2_7_28cm"], s["status"]) == (42.0, .27, .21, "adequate")


def test_one_failing_source_does_not_take_down_the_other(monkeypatch):
    _wire(monkeypatch, _EE(fail_smap=True))
    out = rz.fetch_root_zone({"type": "Polygon"}, "2026-01-20")
    assert out["smap"] is None and out["era5"]["layer2_7_28cm"] == .21 and "smap exploded" in out["errors"][0]
    s = rz.summarize_root_zone(out)
    assert s["available"] is False and s["status"] == "unknown" and s["era5_layer2_7_28cm"] == .21
