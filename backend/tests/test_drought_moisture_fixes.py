"""Regression tests for the drought (NDDI) and soil-moisture bugs found in the audit.  Earth Engine is NOT reachable from the dev sandbox: `ee` images are replaced by stand-ins that EVALUATE the maths on
numbers, so these check our formulas and null handling, not Earth Engine itself.   pytest tests/test_drought_moisture_fixes.py"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services import gee_client as gc
from app.services.agri import risk_scoring


class Px:
    """A one-pixel 'image' that evaluates arithmetic on numbers; a masked pixel is value None."""
    def __init__(self, v=None, bands=None): self.v, self.bands = v, bands
    def normalizedDifference(self, b): a, c = self.bands[b[0]], self.bands[b[1]]; return Px((a - c) / (a + c))
    def rename(self, *_): return self
    def add(self, o): return Px(None if self.v is None else self.v + o.v)
    def subtract(self, o): return Px(self.v - o.v)
    def divide(self, o): return Px(self.v / o.v)
    def gt(self, t): return Px(None if self.v is None else float(self.v > t))
    def updateMask(self, m): return Px(self.v if (m.v or 0) else None)


def nddi(**refl): return gc._nddi_image(Px(bands=refl)).v


HEALTHY = dict(B3=0.08, B4=0.05, B8=0.40, B11=0.20)
STRESSED = dict(B3=0.09, B4=0.10, B8=0.27, B11=0.25)
BARE = dict(B3=0.13, B4=0.20, B8=0.28, B11=0.38)


def test_healthy_dense_crop_is_not_drought():
    v = nddi(**HEALTHY)
    assert v == pytest.approx(0.405, abs=0.01) and v <= gc.NDDI_DROUGHT, "the old green/NIR formula gave ~13 here and flagged it SEVERE"


def test_stressed_vegetation_scores_higher_than_healthy_and_is_flagged():
    h, s = nddi(**HEALTHY), nddi(**STRESSED)
    assert s > h and s > gc.NDDI_SEVERE


def test_bare_soil_is_not_assessed_instead_of_producing_a_meaningless_ratio():
    assert nddi(**BARE) is None, "NDVI+NDWI is ~0 there: the ratio is unstable, so the pixel is masked"


def test_ndwi_comes_from_nir_and_swir_not_green():
    base = nddi(**STRESSED); assert nddi(**{**STRESSED, "B3": 0.9}) == base, "changing the green band must not change NDDI"
    assert nddi(**{**STRESSED, "B11": 0.05}) != base, "changing the SWIR band must"


def test_class_limits_and_guard_are_the_documented_constants():
    assert (gc.NDDI_DROUGHT, gc.NDDI_SEVERE, gc.NDDI_MIN_DENOMINATOR) == (0.5, 0.7, 0.1)


# ---------------- Analyze-tab soil moisture ----------------
class Res:
    def __init__(self, v): self.v = v
    def getInfo(self): return self.v


class Region:
    is_point = False
    def centroid(self, maxError=None): p = Region(); p.is_point = True; return p
    def __getattr__(self, n): return lambda *a, **k: self


class Node:
    def __init__(self, poly=None, point=None, n=3): self.poly, self.point, self.n = poly, point, n
    def reduceRegion(self, **kw): return Res(self.point if getattr(kw["geometry"], "is_point", False) else self.poly)
    def size(self): return Res(self.n)
    def getInfo(self): return 0
    def __getattr__(self, name): return lambda *a, **k: self


class Reducer:
    def __getattr__(self, name): return lambda *a, **k: self
    def combine(self, *a, **k): return self


@pytest.fixture
def moisture(monkeypatch):
    def run(poly, point, scenes=3):
        node = Node(poly, point, scenes)
        monkeypatch.setattr(gc, "ee", SimpleNamespace(ImageCollection=lambda n: node, Date=lambda x: node, Reducer=Reducer(), Image=lambda v: node))
        monkeypatch.setattr(gc, "_polygon_geometry", lambda aoi: Region()); monkeypatch.setattr(gc, "_cap_end_date", lambda d: node)
        monkeypatch.setattr(gc, "_validate_date_range", lambda *a: None); monkeypatch.setattr(gc, "_require_start_after", lambda *a: None); monkeypatch.setattr(gc, "_region_area_km2", lambda r: 10.0)
        return gc.compute_soil_moisture({"type": "Polygon"}, "2025-01-01", "2026-06-30")["metrics"]
    return run


VALS = {"sm_surface_mean": 0.2, "sm_surface_min": 0.1, "sm_surface_max": 0.3, "dry": 0.4}
NULLS = {k: None for k in VALS}


def test_moisture_normal_area_reports_dry_share_from_the_mask_mean(moisture):
    m = moisture(VALS, None); assert m["dry_stress_pct"] == 40.0 and m["dry_stress_area_km2"] == 4.0 and m["data_available"] is True and m["sampling"] == "aoi" and m["sampling_note"] is None


def test_moisture_small_area_uses_the_centre_pixel_and_is_labelled(moisture):
    m = moisture(NULLS, {**VALS, "dry": 1.0}); assert m["dry_stress_pct"] == 100.0 and m["data_available"] is True and m["sampling"] == "pixel_at_centroid" and "smaller than one SMAP pixel" in m["sampling_note"]


def test_moisture_with_no_value_is_missing_never_a_zero_percent(moisture):
    m = moisture(NULLS, NULLS)
    assert m["dry_stress_pct"] is None and m["dry_stress_area_km2"] is None and m["data_available"] is False and m["end_avg_soil_moisture"] is None and m["end_min_sm"] is None
    assert risk_scoring._moisture_subscore(m) is None, "a missing reading must not enter the risk score as a verified low-risk 0"


def test_moisture_genuinely_not_dry_is_zero_percent_a_real_value(moisture):
    m = moisture({**VALS, "dry": 0.0}, None); assert m["dry_stress_pct"] == 0.0 and m["dry_stress_area_km2"] == 0.0 and risk_scoring._moisture_subscore(m) is not None
