"""Spectra soil moisture and the vegetation drought index (VHI) must never turn 'no data' into a zero, and must still answer for an area smaller than one pixel.
Earth Engine is NOT reachable from the dev sandbox: `ee` is replaced by a small stand-in; these tests exercise OUR logic (null handling, centroid fallback, labelling), not Earth Engine's behaviour.
pytest tests/test_spectra_honest_reductions.py"""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services import gee_remote_sensing as g
from app.services import vdi_contract


class Res:
    def __init__(self, v): self.v = v
    def getInfo(self): return self.v


class Region:
    is_point = False
    def centroid(self, maxError=None): p = Region(); p.is_point = True; return p


class Node:
    """An image/collection stand-in: any chained method returns itself; reduceRegion answers per geometry kind (polygon vs the centroid point); size() answers the scene count."""
    def __init__(self, poly=None, point=None, n=5, mask=None): self.poly, self.point, self.n, self._mask = poly, point, n, mask
    def reduceRegion(self, **kw): return Res(self.point if getattr(kw["geometry"], "is_point", False) else self.poly)
    def size(self): return Res(self.n)
    def mask(self): return self._mask or self
    def clip(self, region): return Node(n=self.n)      # models Earth Engine's clip(): for an area smaller than a pixel nothing survives, so every statistic on the clipped image is null
    def __getattr__(self, name): return lambda *a, **k: self


class Reducer:
    def __getattr__(self, name): return lambda *a, **k: self
    def combine(self, *a, **k): return self


def fake_ee(col=None, image=None):
    return SimpleNamespace(ImageCollection=lambda name: col, Date=lambda x: x, Reducer=Reducer(), Image=lambda v: image or Node())


@pytest.fixture
def sm(monkeypatch):
    def run(poly, point, valid_poly=None, valid_point=None, scenes=5):
        valid = Node(poly=valid_poly if valid_poly is not None else {"sm_surface": 1.0}, point=valid_point if valid_point is not None else {"sm_surface": 1.0})
        col = Node(poly=poly, point=point, n=scenes, mask=valid)
        monkeypatch.setattr(g, "ee", fake_ee(col=col)); monkeypatch.setattr(g, "_polygon_geometry", lambda aoi: Region()); monkeypatch.setattr(g, "_cap_end_date", lambda d: d); monkeypatch.setattr(g, "_validate_date_range", lambda *a: None); monkeypatch.setattr(g, "_require_start_after", lambda *a: None)
        monkeypatch.setattr(g, "_region_area_km2", lambda r: 4.2); monkeypatch.setattr(g, "_tile_layer", lambda *a, **k: {"tiles": "x"}); monkeypatch.setattr(g, "_download_url", lambda *a, **k: "u")
        return g.compute_soil_moisture({"type": "Polygon"}, "2026-06-01", "2026-06-30")
    return run


SM = {"sm_surface_mean": 0.213, "sm_surface_min": 0.15, "sm_surface_max": 0.27, "sm_surface_stdDev": 0.02}


def test_soil_moisture_real_values_pass_through_with_aoi_sampling(sm):
    r = sm(SM, None); assert r["sm_surface"]["mean"] == 0.213 and r["sm_surface"]["max"] == 0.27 and r["sampling"] == "aoi" and r["sampling_note"] is None and r["valid_pixel_fraction"] == 1.0


def test_soil_moisture_small_area_uses_the_pixel_under_the_centre_and_says_so(sm):
    r = sm({k: None for k in SM}, SM)
    assert r["sm_surface"]["mean"] == 0.213 and r["sampling"] == "pixel_at_centroid" and "smaller than one SMAP pixel" in r["sampling_note"]


def test_soil_moisture_with_no_value_is_missing_not_zero(sm):
    r = sm({k: None for k in SM}, {k: None for k in SM}, valid_poly={"sm_surface": None}, valid_point={"sm_surface": None})
    assert r["sm_surface"] is None and r["sampling"] is None and "not zero moisture" in r["method"] and r["scene_count"] == 5 and "valid_pixel_fraction" not in r


def test_soil_moisture_a_genuine_zero_stays_a_value(sm):
    r = sm({**SM, "sm_surface_min": 0.0}, None); assert r["sm_surface"]["min"] == 0.0 and r["sampling"] == "aoi"


def test_soil_moisture_no_scenes_is_unchanged(sm):
    r = sm(SM, None, scenes=0); assert r["sm_surface"] is None and r["scene_count"] == 0 and "No SMAP coverage" in r["method"]


# ---------------- VHI ----------------
META = {k: (k + " label", "formula", "cite", "interp") for k in vdi_contract.VDI_KEYS}
ST = {"VCI_mean": 30.0, "VCI_min": 5.0, "VCI_max": 60.0, "VCI_stdDev": 9.0, "TCI_mean": 26.0, "TCI_min": 2.0, "TCI_max": 70.0, "TCI_stdDev": 11.0, "VHI_mean": 28.0, "VHI_min": 4.0, "VHI_max": 65.0, "VHI_stdDev": 10.0}
NONE = {k: None for k in ST}


@pytest.fixture
def vhi(monkeypatch):
    def run(stack_poly, stack_point, valid_poly, valid_point, hist_poly=None, hist_point=None):
        stack = Node(poly=stack_poly, point=stack_point); valid = Node(poly=valid_poly, point=valid_point)
        vhi_img = Node(mask=valid); stack_img = Node(poly=stack_poly, point=stack_point)
        cls = Node(poly=hist_poly, point=hist_point)
        monkeypatch.setattr(g, "ee", fake_ee(image=cls)); monkeypatch.setattr(g, "_polygon_geometry", lambda aoi: Region())
        monkeypatch.setattr(g, "_build_vhi_stack", lambda region, s, e: {"stack": stack_img, "vhi": vhi_img, "year": 2026, "y0": 2001, "y1": 2025, "m1": 6, "d1": 1, "m2": 6, "d2": 30, "ndvi_years": 24, "lst_years": 24, "ndvi_composites_used": 2, "lst_composites_used": 4})
        monkeypatch.setattr(g, "_region_area_km2", lambda r: 3.0); monkeypatch.setattr(g, "_tile_layer", lambda *a, **k: {"tiles": "x"}); monkeypatch.setattr(g, "_download_url", lambda *a, **k: "u")
        return g.compute_vegetation_drought_index({"type": "Polygon"}, "2026-06-01", "2026-06-30")
    return run


def test_vhi_real_values_and_the_keys_the_consumer_reads(vhi):
    r = vhi(ST, None, {"VHI": 0.9}, None, {"class": {"3": 6, "4": 4}}, None)
    assert set(r["indices"]) == {vdi_contract.vdi_index_key(k) for k in vdi_contract.VDI_KEYS} == {"vci", "tci", "vhi"}
    assert r["indices"]["vhi"]["mean"] == 28.0 and r["valid_pixel_fraction"] == 0.9 and r["sampling"] == "aoi" and r["sampling_note"] is None
    assert {c["code"]: c["pct_of_valid_pixels"] for c in r["drought_classes"]}[3] == 60.0


def test_vhi_small_area_samples_the_centre_pixel_and_says_so(vhi):
    r = vhi(NONE, ST, {"VHI": None}, {"VHI": 1.0}, {"class": None}, {"class": {"1": 1}})
    assert r["indices"]["vhi"]["mean"] == 28.0 and r["sampling"] == "pixel_at_centroid" and "smaller than one MODIS" in r["sampling_note"] and r["valid_pixel_fraction"] == 1.0


def test_vhi_with_no_data_is_none_everywhere_never_zero_which_would_mean_extreme_drought(vhi):
    r = vhi(NONE, NONE, {"VHI": None}, {"VHI": None}, {"class": None}, {"class": None})
    for k in ("vci", "tci", "vhi"):
        assert r["indices"][k]["mean"] is None and r["indices"][k]["min"] is None and r["indices"][k]["max"] is None and r["indices"][k]["std_dev"] is None
    assert r["valid_pixel_fraction"] is None and r["sampling"] is None and all(c["pct_of_valid_pixels"] is None for c in r["drought_classes"])


def test_vhi_genuine_zero_is_kept_as_a_value(vhi):
    r = vhi({**ST, "VHI_mean": 0.0}, None, {"VHI": 1.0}, None, {"class": {"0": 3}}, None); assert r["indices"]["vhi"]["mean"] == 0.0


def test_packaged_indices_feed_the_v2_assessment_end_to_end(vhi):
    from app.services.agri import risk_assessment as ra
    ok = vhi(ST, None, {"VHI": 0.9}, None, {"class": {"3": 1}}, None); i = ra.ind_vhi(ok)
    assert i["status"] == "ok" and i["value"] == 28.0, "the v2 consumer must read what the producer wrote (lower-case keys)"
    empty = vhi(NONE, NONE, {"VHI": None}, {"VHI": None}, {"class": None}, {"class": None}); m = ra.ind_vhi(empty)
    assert m["status"] == "unavailable" and "no valid MODIS pixel" in m["reason"]
    small = vhi(NONE, ST, {"VHI": None}, {"VHI": 1.0}, {"class": None}, {"class": {"1": 1}}); assert ra.ind_vhi(small)["status"] == "ok"
