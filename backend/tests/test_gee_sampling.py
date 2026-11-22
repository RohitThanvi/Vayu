"""Region reductions that must not turn 'no data' into a zero.  pytest tests/test_gee_sampling.py   (fakes only: Earth Engine is not reachable from the dev sandbox)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.services import gee_sampling as gs


class Fake:
    """Image double: reduceRegion(geometry=...) answers per geometry kind; records every call."""
    def __init__(self, poly=None, point=None): self.poly, self.point, self.calls = poly, point, []
    def reduceRegion(self, **kw):
        self.calls.append(kw); g = kw["geometry"]; out = self.point if getattr(g, "is_point", False) else self.poly
        class R:
            def getInfo(_self): return out
        return R()


class Region:
    is_point = False
    def centroid(self, maxError=None):
        self.maxError = maxError; p = Region(); p.is_point = True; return p


def test_polygon_values_are_used_when_present():
    img = Fake(poly={"a_mean": 0.21}, point={"a_mean": 0.99}); stats, how = gs.reduce_stats(img, Region(), "R", 10000)
    assert stats == {"a_mean": 0.21} and how == gs.SAMPLING_AOI and len(img.calls) == 1 and img.calls[0]["scale"] == 10000 and img.calls[0]["bestEffort"] is True


def test_all_null_polygon_falls_back_to_the_pixel_under_the_centroid_and_says_so():
    img = Fake(poly={"a_mean": None, "a_min": None}, point={"a_mean": 0.18}); r = Region(); stats, how = gs.reduce_stats(img, r, "R", 10000)
    assert stats == {"a_mean": 0.18} and how == gs.SAMPLING_CENTROID and r.maxError == 100 and len(img.calls) == 2 and img.calls[1]["geometry"].is_point
    assert "smaller than one SMAP pixel" in gs.sampling_note(how, 9, "SMAP") and gs.sampling_note(gs.SAMPLING_AOI, 9, "SMAP") is None and gs.sampling_note(None, 9, "SMAP") is None


def test_empty_polygon_dict_also_falls_back():
    stats, how = gs.reduce_stats(Fake(poly={}, point={"a_mean": 5.0}), Region(), "R", 1000); assert (stats, how) == ({"a_mean": 5.0}, gs.SAMPLING_CENTROID)


def test_no_data_anywhere_is_empty_not_zero():
    for poly, point in (({"a": None}, {"a": None}), (None, None), ({}, {})):
        stats, how = gs.reduce_stats(Fake(poly=poly, point=point), Region(), "R", 1000); assert stats == {} and how is None


def test_a_genuine_zero_is_a_value_not_missing():
    stats, how = gs.reduce_stats(Fake(poly={"a_mean": 0.0}, point={"a_mean": 7.0}), Region(), "R", 1000)
    assert stats == {"a_mean": 0.0} and how == gs.SAMPLING_AOI and gs.has_values({"a": 0}) and not gs.has_values({"a": None}) and not gs.has_values(None) and not gs.has_values({})


def test_num_is_none_for_missing_and_rounds_real_values():
    assert gs.num({}, "a", 2) is None and gs.num({"a": None}, "a", 2) is None and gs.num({"a": 0.0}, "a", 2) == 0.0 and gs.num({"a": 0.12345}, "a", 3) == 0.123
