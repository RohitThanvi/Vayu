"""Nearest-valid-data fallback for climate/soil sampling (ERA5-Land and CHIRPS are land-only and coarse, so a coastal/island/lake-edge point can be masked).
The Earth Engine calls themselves are NOT run live from the dev sandbox; these tests pin the selection logic, the radii and the reporting.  pytest tests/test_climate_fallback.py"""
import asyncio, copy, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from agri_fixtures import LOCATIONS
from app.services.agri import crop_pathways as cp, crop_suitability as cs

KEYS = [f"t{m}" for m in range(1, 13)]
FULL = {k: 20.0 for k in KEYS}
PART = {**FULL, "t5": None}


def test_pick_prefers_the_smallest_complete_radius():
    assert cs.pick_nearest_valid([FULL, FULL], (0, 12000), KEYS) == (FULL, 0)
    assert cs.pick_nearest_valid([None, PART, FULL, FULL], (0, 12000, 25000, 50000), KEYS) == (FULL, 25000)


def test_partial_or_missing_months_are_not_accepted_as_valid():
    assert cs.pick_nearest_valid([PART, {}, None], (0, 12000, 25000), KEYS) == ({}, None)
    assert cs.pick_nearest_valid([{k: 1 for k in KEYS[:11]}], (0,), KEYS) == ({}, None)      # a missing key is invalid too
    assert cs.pick_nearest_valid(None, (0,), KEYS) == ({}, None) and cs.pick_nearest_valid([], (), KEYS) == ({}, None)


def test_zero_is_a_valid_value_not_missing():
    zeros = {k: 0.0 for k in KEYS}
    assert cs.pick_nearest_valid([zeros], (0,), KEYS) == (zeros, 0)                          # 0 C / 0 mm must not trigger the fallback


class _Geom:
    def __init__(self, r=0): self.radius = r
    def buffer(self, r): return _Geom(r)


class _Img:
    """Returns data only when the requested geometry reaches `needs_radius` metres."""
    def __init__(self, needs_radius, log): self.needs, self.log = needs_radius, log
    def reduceRegion(self, **kw):
        self.log.append(kw["geometry"].radius); return FULL if kw["geometry"].radius >= self.needs else {k: None for k in KEYS}


class _EE:
    def __init__(self): self.list_calls = []
    class Reducer:
        @staticmethod
        def mean(): return "mean"
    def List(self, items):
        self.list_calls.append(len(items)); return type("L", (), {"getInfo": lambda s: items})()


def test_fallback_queries_every_radius_in_one_round_trip_and_uses_the_nearest_complete_one():
    ee, log = _EE(), []
    got, r = cs.reduce_with_nearest_fallback(ee, _Img(25000, log), _Geom(), 11132, KEYS, cs._CLIMATE_SEARCH_RADII_M)
    assert log == list(cs._CLIMATE_SEARCH_RADII_M) and ee.list_calls == [len(cs._CLIMATE_SEARCH_RADII_M)], "all radii evaluated server-side in a single getInfo"
    assert r == 25000 and got == FULL


def test_exact_spot_wins_when_it_has_data_and_nothing_is_found_beyond_the_last_radius():
    ee = _EE(); assert cs.reduce_with_nearest_fallback(ee, _Img(0, []), _Geom(), 11132, KEYS, cs._CLIMATE_SEARCH_RADII_M)[1] == 0
    assert cs.reduce_with_nearest_fallback(_EE(), _Img(10**9, []), _Geom(), 11132, KEYS, cs._CLIMATE_SEARCH_RADII_M) == ({}, None)


def test_search_radii_are_increasing_start_at_the_exact_spot_and_stay_local():
    for radii in (cs._CLIMATE_SEARCH_RADII_M, cs._SOIL_SEARCH_RADII_M):
        assert radii[0] == 0 and list(radii) == sorted(set(radii))
    assert cs._CLIMATE_SEARCH_RADII_M[-1] <= 100_000 and cs._SOIL_SEARCH_RADII_M[-1] <= 10_000


NOTE = "No climate data exists at the exact spot (water or coast pixel), so rainfall and temperature are land averages within about 25 km."


def test_the_note_reaches_the_pathway_verdict_first_and_the_suitability_profile():
    prof = {**copy.deepcopy(LOCATIONS["ratnagiri"]), "climate_note": NOTE, "climate_search_km": 25.0}
    cl = cp.climate_from_profile(prof); assert cl["climate_note"] == NOTE and cl["climate_search_km"] == 25.0
    v = cp.assess("moringa", cl); assert v["cautions"][0] == NOTE
    assert NOTE not in " ".join(cp.assess("moringa", cp.climate_from_profile(copy.deepcopy(LOCATIONS["ratnagiri"])))["cautions"])
    assert cs.score_crops(prof, False)["profile"]["climate_note"] == NOTE


def test_pathway_report_endpoint_survives_a_string_note_and_returns_it(monkeypatch):
    from app.api import agri_endpoints as a
    prof = {**copy.deepcopy(LOCATIONS["ratnagiri"]), "climate_note": NOTE, "climate_search_km": 25.0}
    monkeypatch.setattr(cs, "sample_location_profile", lambda **k: prof)
    r = asyncio.run(a.pathway_report(a.PathwayReportRequest(crop_id="moringa", lat=17.123, lon=73.456)))
    assert r["location_climate"]["climate_note"] == NOTE and r["location_climate"]["climate_search_km"] == 25.0
    assert r["verdict"]["cautions"][0] == NOTE and NOTE in r["manual_markdown"]
