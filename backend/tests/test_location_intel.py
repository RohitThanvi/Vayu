"""Offline tests for the free-data Real Estate / Location Intelligence stack: scoring, OSM amenity maths,
night-light and seismic parsing, location resolution, orchestration with partial failures, and the HTTP job
flow. Earth Engine, Overpass, USGS, Nominatim and Open-Meteo are stubbed."""
import asyncio
import datetime as dt

import pytest

from app.services import geocode_util, location_intel as li, osm_amenities as osm

JAIPUR = (26.9124, 75.7873)


def run(coro):
    return asyncio.run(coro)


# ── scoring ───────────────────────────────────────────────────────────────────
def test_group_weights_sum_to_one_and_profiles_cover_all_groups():
    for g, spec in li.GROUPS.items():
        assert sum(w for _, _, w in spec["criteria"].values()) == pytest.approx(1.0), g
        for m in spec["criteria"]:
            assert m in li.METRICS
    for p in li.PROFILES.values():
        assert set(p["weights"]) == set(li.GROUPS) and sum(p["weights"].values()) == pytest.approx(1.0)


def test_score_group_uses_available_criteria_only_and_needs_half_coverage():
    best = {"hospital_km": 1, "school_km": 0.5, "market_km": 1, "transit_km": 0.5, "park_km": 0.5, "road_km": 0.5}
    g = li.score_group("liveability", best)
    assert g["score"] == 100.0 and g["coverage"] == 1.0 and g["evidence_level"] == "strong"
    partial = li.score_group("liveability", {k: best[k] for k in ("hospital_km", "school_km", "transit_km")})   # .22+.2+.22 = .64
    assert partial["score"] == 100.0 and partial["coverage"] == pytest.approx(0.64)                              # absent != penalised
    thin = li.score_group("liveability", {"hospital_km": 1})
    assert thin["score"] is None and thin["evidence_level"] == "weak"
    worst = li.score_group("liveability", {"hospital_km": 12, "school_km": 9, "market_km": 10, "transit_km": 8, "park_km": 6, "road_km": 9})
    assert worst["score"] == 0.0


def test_overall_renormalises_over_scored_groups_and_profile_changes_the_answer():
    groups = {"liveability": {"score": 90.0}, "growth": {"score": 20.0}, "resilience": {"score": 60.0}}
    bal, cov = li.overall(groups, li.PROFILES["balanced"]["weights"])
    inv, _ = li.overall(groups, li.PROFILES["investor"]["weights"])
    lend, _ = li.overall(groups, li.PROFILES["lender_risk"]["weights"])
    assert cov == 1.0 and bal == pytest.approx(0.4 * 90 + 0.3 * 20 + 0.3 * 60) and inv < bal and lend < bal
    groups["growth"]["score"] = None
    ov, cov = li.overall(groups, li.PROFILES["balanced"]["weights"])
    assert ov == pytest.approx((0.4 * 90 + 0.3 * 60) / 0.7, abs=0.06) and cov == pytest.approx(0.7)
    groups["liveability"]["score"] = None
    assert li.overall(groups, li.PROFILES["balanced"]["weights"])[0] is None                                       # <50% coverage


def test_rank_orders_scored_first():
    items = [{"overall_score": None, "name": "N"}, {"overall_score": 50, "name": "B"}, {"overall_score": 80, "name": "A"}]
    r = li.rank(items)
    assert [i["name"] for i in r] == ["A", "B", "N"] and [i["rank"] for i in r] == [1, 2, None]


# ── data parsing ──────────────────────────────────────────────────────────────
def test_night_light_stats():
    pts = [{"date": f"20{22 + i // 12}-{i % 12 + 1:02d}-01", "value": 2.0 if i < 12 else 5.0 if i >= 36 else 3.0} for i in range(48)]
    s = li.night_light_stats(list(reversed(pts)))                    # unsorted input is fine
    assert s["night_light_level"] == 5.0 and s["night_light_change"] == 3.0 and s["night_light_growth_pct"] == 150.0
    dark = [{"date": f"2022-{i + 1:02d}-01", "value": 0.2} for i in range(12)] + [{"date": f"2024-{i + 1:02d}-01", "value": 0.4} for i in range(12)]
    s2 = li.night_light_stats(dark)
    assert "night_light_growth_pct" not in s2 and s2["night_light_change"] == pytest.approx(0.2)                   # no % on a ~zero base
    assert li.night_light_stats([{"date": "2022-01-01", "value": 1.0}]) == {}
    short = li.night_light_stats([{"date": f"2024-{i + 1:02d}-01", "value": 4.0} for i in range(8)])
    assert short == {"night_light_level": 4.0}


def test_parse_usgs():
    gj = {"features": [
        {"properties": {"mag": 5.4}, "geometry": {"coordinates": [76.0, 27.0, 10]}},
        {"properties": {"mag": 6.1}, "geometry": {"coordinates": [75.0, 26.0, 10]}},
        {"properties": {"mag": 4.7}, "geometry": {"coordinates": [75.5, 26.5, 10]}},
        {"properties": {"mag": None}, "geometry": {"coordinates": [75.5, 26.5, 10]}}]}
    r = li.parse_usgs(gj, *JAIPUR)
    assert r["quake_m5_count"] == 2.0 and r["quake_max_mag"] == 6.1 and r["quake_m45_count"] == 3 and 0 < r["quake_nearest_m5_km"] < 150
    assert li.parse_usgs({}, *JAIPUR)["quake_m5_count"] == 0.0


def test_lst_window_is_the_latest_completed_pre_monsoon():
    assert li.lst_window(dt.date(2026, 10, 14)) == ("2026-03-01", "2026-06-30")
    assert li.lst_window(dt.date(2026, 2, 1)) == ("2025-03-01", "2025-06-30")
    assert li.lst_window(dt.date(2026, 6, 20)) == ("2025-03-01", "2025-06-30")


# ── OSM amenities ─────────────────────────────────────────────────────────────
def _node(i, lat, lon, **tags):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}


def test_osm_query_and_classification():
    q = osm.build_query([(26.9, 75.8), (26.95, 75.85)])
    assert q.count("out center tags") == len(osm.KINDS) and '"amenity"~"^(hospital|clinic)$"' in q and "around:10000,26.90000,75.80000" in q
    assert osm.classify({"tags": {"amenity": "hospital"}}) == {"hospital"} and osm.classify({"tags": {"highway": "bus_stop"}}) == {"bus"}
    assert osm.classify({"tags": {"shop": "bakery"}}) == set()
    p = osm.parse([_node(1, 26.9, 75.8, amenity="school"), _node(1, 26.9, 75.8, amenity="school"),
                   {"type": "way", "id": 2, "center": {"lat": 26.91, "lon": 75.81}, "tags": {"highway": "primary"}}])
    assert len(p["school"]) == 1 and len(p["road"]) == 1


def _dense_osm():
    base = JAIPUR
    return {
        "hospital": [(base[0] + 0.01, base[1])], "school": [(base[0] + 0.005, base[1]), (base[0], base[1] + 0.02)],
        "market": [(base[0] + 0.012, base[1] + 0.01)], "rail": [(base[0] + 0.05, base[1])],
        "bus": [(base[0] + 0.002, base[1] + 0.002)] + [(base[0] + 0.001 * i, base[1]) for i in range(6)],
        "park": [(base[0] + 0.02, base[1])], "road": [(base[0] + 0.004, base[1])],
    }


def test_osm_summary_nearest_counts_and_lower_bounds():
    s = osm.summarize(JAIPUR, _dense_osm())
    m = s["metrics"]
    assert not s["sparse"] and 0.9 < m["hospital_km"] < 1.3 and m["transit_km"] < 0.5 and m["road_km"] < 0.6
    assert s["counts"]["school"] == 1 and s["counts"]["bus"] == 7 and s["count_radius_km"]["bus"] == 1.5 and s["count_radius_km"]["school"] == 1.5
    far = {**_dense_osm(), "market": [], "park": []}
    s2 = osm.summarize(JAIPUR, far)
    assert s2["metrics"]["market_km"] == 8.0 and "lower bound" in s2["notes"]["market_km"] and s2["metrics"]["park_km"] == 5.0


def test_sparse_osm_drops_amenity_metrics_instead_of_scoring_a_mapping_gap():
    s = osm.summarize(JAIPUR, {"hospital": [], "school": [(26.92, 75.79)], "market": [], "rail": [], "bus": [], "park": [], "road": []})
    assert s["sparse"] and s["metrics"] == {}


# ── locations ─────────────────────────────────────────────────────────────────
def test_parse_latlon():
    assert li._parse_latlon("26.85, 75.76") == (26.85, 75.76) and li._parse_latlon("26.85;75.76") == (26.85, 75.76)
    assert li._parse_latlon("Jaipur, Rajasthan") is None and li._parse_latlon("95, 75") is None


def test_resolve_locations(monkeypatch):
    monkeypatch.setattr(li, "NOMINATIM_GAP_S", 0)
    seen = []

    async def geo(q, diag=None):
        seen.append(q)
        if "nowhere" in q.lower():
            return None
        return (26.85, 75.76)
    monkeypatch.setattr(geocode_util, "geocode_point", geo)
    caveats = []
    out = run(li.resolve_locations([{"query": "Mansarovar, Jaipur"}, {"query": "Nowhere Land"}, {"query": "26.9, 75.8"},
                                    {"name": "Named", "lat": 26.1, "lon": 75.1}, {"query": "5th"}, {"query": "6th"}], 9.0, caveats))
    assert [o["name"] for o in out] == ["Mansarovar, Jaipur", "26.9, 75.8", "Named"]                  # only the first 4 inputs are considered
    assert all(o["radius_km"] == 4.0 for o in out)                                                         # radius clamped
    assert any("Nowhere" in c for c in caveats) and any("Only the first" in c for c in caveats)
    assert seen == ["Mansarovar, Jaipur", "Nowhere Land"]                                                  # coordinates never hit the geocoder
    assert out[0]["aoi"]["type"] == "Polygon" and out[0]["ring"]["type"] == "Polygon"


# ── orchestration ─────────────────────────────────────────────────────────────
def _stub_sources(monkeypatch, per_name, fail=(), osm_data=None, osm_fail=False):
    """per_name: {name -> metric dict}; sources are keyed by the location's lat (unique per test location)."""
    by_lat = {round(v["lat"], 3): v for v in per_name.values()}

    def pick(lat_or_aoi):
        if isinstance(lat_or_aoi, dict):
            ys = [c[1] for c in lat_or_aoi["coordinates"][0]]
            lat = (min(ys) + max(ys)) / 2
        else:
            lat = lat_or_aoi
        return by_lat[min(by_lat, key=lambda k: abs(k - lat))]

    async def terrain(aoi, ring, sem):
        if "terrain" in fail:
            raise RuntimeError("GEE quota")
        d = pick(aoi)
        return {"slope_deg": d["slope"], "elevation_m": 400.0, "relative_elev_m": d["rel"]}

    async def lulc(aoi, sem):
        return {"landcover_builtup_pct": 40.0, "landcover_cropland_pct": 10.0, "landcover_tree_pct": 5.0, "landcover_water_pct": 1.0}

    async def night(aoi, sem):
        d = pick(aoi)
        return {"night_light_level": d["nl"], "night_light_change": d["nlc"], "night_light_growth_pct": d["nlg"]}

    async def lst(aoi, sem):
        return {"lst_c": pick(aoi)["lst"]}

    async def water(aoi, sem):
        return {"seasonal_water_pct": pick(aoi)["water"], "permanent_water_pct": 0.5}

    async def quakes(lat, lon):
        return {"quake_m5_count": pick(lat)["quakes"], "quake_max_mag": 5.8, "quake_m45_count": 7, "quake_nearest_m5_km": 120.0}

    async def air(lat, lon):
        return {"us_aqi": 160.0, "category": "Unhealthy", "pm2_5": 70, "observed_at": "now"}

    async def fetch_osm(centers, diag=None):
        if osm_fail:
            diag["osm_error"] = "OpenStreetMap server timed out"
            return None
        return osm_data if osm_data is not None else _dense_osm()
    for name, fn in (("_src_terrain", terrain), ("_src_landcover", lulc), ("_src_night", night), ("_src_lst", lst),
                     ("_src_water", water), ("_src_quakes", quakes), ("_src_air", air)):
        monkeypatch.setattr(li, name, fn)
    monkeypatch.setattr(osm, "fetch", fetch_osm)


def _world(monkeypatch, **kw):
    monkeypatch.setattr(li, "NOMINATIM_GAP_S", 0)
    table = {"Alpha": (26.90, 75.80), "Beta": (26.95, 75.85)}

    async def geo(q, diag=None):
        for k, (lat, lon) in table.items():
            if k in q:
                return (lat, lon)
        return None
    monkeypatch.setattr(geocode_util, "geocode_point", geo)
    per = {"Alpha": {"lat": 26.90, "slope": 2, "rel": 1.0, "nl": 12, "nlc": 3.0, "nlg": 40, "lst": 38, "water": 1, "quakes": 0},
           "Beta": {"lat": 26.95, "slope": 12, "rel": -14.0, "nl": 2, "nlc": -0.5, "nlg": -10, "lst": 45, "water": 25, "quakes": 6}}
    _stub_sources(monkeypatch, per, **kw)


def test_end_to_end_comparison_and_explanations(monkeypatch):
    _world(monkeypatch)
    prog = []
    res = run(li.assess_locations([{"query": "Alpha, Jaipur"}, {"query": "Beta, Jaipur"}], 2.0, "balanced", lambda l, p: prog.append(p)))
    a, b = res["locations"]
    assert a["name"] == "Alpha, Jaipur" and a["rank"] == 1 and b["rank"] == 2 and a["overall_score"] > b["overall_score"]
    assert all(g["evidence_level"] == "strong" for g in a["groups"].values()) and a["overall_coverage"] == 1.0
    assert any("flood-prone" in f for f in b["flags"]) and any("below its surroundings" in f for f in b["flags"]) and any("earthquakes" in f for f in b["flags"])
    assert a["strengths"] and b["weaknesses"] and a["context"]["air_quality"]["us_aqi"] == 160.0
    assert any("unhealthy" in f.lower() for f in a["flags"])
    assert "prices" in res["not_included"][0].lower() and "NOT estimate prices" in res["methodology"] and res["sources"]
    assert prog == sorted(prog) and prog[-1] == 100


def test_profile_can_flip_the_ranking(monkeypatch):
    _world(monkeypatch)
    # Beta: growth poor, but make Alpha's resilience poor and see the lender profile punish it
    per = {"Alpha": {"lat": 26.90, "slope": 20, "rel": -14, "nl": 12, "nlc": 3.0, "nlg": 40, "lst": 45, "water": 25, "quakes": 7},
           "Beta": {"lat": 26.95, "slope": 2, "rel": 1.0, "nl": 3, "nlc": 0.5, "nlg": 5, "lst": 38, "water": 1, "quakes": 0}}
    _stub_sources(monkeypatch, per)
    inv = run(li.assess_locations([{"query": "Alpha"}, {"query": "Beta"}], 2.0, "investor"))
    lend = run(li.assess_locations([{"query": "Alpha"}, {"query": "Beta"}], 2.0, "lender_risk"))
    assert lend["locations"][0]["name"] == "Beta"                                      # safe beats fast for a lender
    assert inv["locations"][0]["name"] == "Alpha"                                      # growth beats safe for an investor
    assert inv["profile_label"] == "Growth investor" and lend["weights"]["resilience"] == 0.6


def test_partial_failures_reduce_coverage_never_crash(monkeypatch):
    _world(monkeypatch, fail=("terrain",))
    res = run(li.assess_locations([{"query": "Alpha"}], 2.0))
    loc = res["locations"][0]
    assert any("GEE quota" in c for c in res["caveats"])
    res_c = {c["metric"]: c for c in loc["groups"]["resilience"]["criteria"]}
    assert res_c["slope_deg"]["utility"] is None and res_c["seasonal_water_pct"]["utility"] is not None
    assert loc["groups"]["resilience"]["coverage"] == pytest.approx(0.70)                # lost slope .10 + relative elevation .20
    assert loc["overall_score"] is not None


def test_osm_failure_and_sparse_osm_are_stated(monkeypatch):
    _world(monkeypatch, osm_fail=True)
    res = run(li.assess_locations([{"query": "Alpha"}], 2.0))
    assert res["locations"][0]["groups"]["liveability"]["score"] is None
    assert any("OpenStreetMap could not be reached" in c for c in res["caveats"]) and res["locations"][0]["overall_score"] is not None
    _world(monkeypatch, osm_data={k: [] for k in osm.KINDS})
    res2 = run(li.assess_locations([{"query": "Alpha"}], 2.0))
    assert res2["locations"][0]["groups"]["liveability"]["score"] is None
    assert any("sparsely mapped" in f for f in res2["locations"][0]["flags"])


def test_unfindable_locations_raise_a_clear_error(monkeypatch):
    _world(monkeypatch)
    with pytest.raises(ValueError, match="None of the locations"):
        run(li.assess_locations([{"query": "Atlantis"}], 2.0))


# ── HTTP job flow ─────────────────────────────────────────────────────────────
def _client(monkeypatch, behaviour):
    pytest.importorskip("slowapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import realestate_endpoints as ep
    from app.core.rate_limit import limiter

    async def fake_assess(inputs, radius, profile, progress):
        progress("Measuring", 50)
        return behaviour(inputs, radius, profile)
    monkeypatch.setattr(li, "assess_locations", fake_assess)
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(ep.router, prefix="/api/v1")
    return TestClient(app)


def test_http_assess_flow_and_validation(monkeypatch):
    c = _client(monkeypatch, lambda inputs, radius, profile: {"profile": profile, "locations": [{"name": inputs[0]["query"]}], "radius_km": radius})
    r = c.post("/api/v1/realestate/assess", json={"locations": [{"query": "Mansarovar, Jaipur"}], "radius_km": 3, "profile": "investor"})
    assert r.status_code == 202
    got = c.get(f"/api/v1/realestate/assess/{r.json()['request_id']}").json()
    assert got["status"] == "done" and got["result"]["profile"] == "investor" and got["result"]["radius_km"] == 3
    assert c.post("/api/v1/realestate/assess", json={"locations": [{"query": "x"}], "profile": "nope"}).status_code == 422
    assert c.post("/api/v1/realestate/assess", json={"locations": [{"query": str(i)} for i in range(5)]}).status_code == 422
    assert c.post("/api/v1/realestate/assess", json={"locations": [{}]}).status_code == 422
    assert c.post("/api/v1/realestate/assess", json={"locations": [{"query": "x"}], "radius_km": 99}).status_code == 422
    p = c.get("/api/v1/realestate/profiles").json()
    assert {x["id"] for x in p["profiles"]} == set(li.PROFILES) and p["max_locations"] == 4


def test_http_failed_job_reports_the_reason(monkeypatch):
    def boom(inputs, radius, profile):
        raise ValueError("None of the locations could be found.")
    c = _client(monkeypatch, boom)
    rid = c.post("/api/v1/realestate/assess", json={"locations": [{"query": "zzz"}]}).json()["request_id"]
    r = c.get(f"/api/v1/realestate/assess/{rid}")
    assert r.status_code == 422 and "could be found" in r.json()["detail"]
    assert c.get("/api/v1/realestate/assess/00000000-0000-0000-0000-000000000000").status_code == 404


# ── OSM rings, failure detection, nearby list ─────────────────────────────────
import asyncio


def _el(i, lat, lon, **tags):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}


def test_overpass_runtime_remark_is_a_failure_not_an_empty_result(monkeypatch):
    calls = []

    class Resp:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            calls.append(url)
            return Resp({"elements": [], "remark": 'runtime error: Query timed out in "query" at line 3 after 61 seconds.'})
    monkeypatch.setattr(osm.httpx, "AsyncClient", Client)
    diag = {}
    assert asyncio.run(osm.fetch([JAIPUR], diag)) is None
    assert "did not complete" in diag["osm_error"] and len(calls) == len(osm.OVERPASS_URLS)   # tried the mirror too


def _fake_post(monkeypatch, handler):
    seen = []

    async def post(q):
        seen.append(q)
        return handler(len(seen), q)
    monkeypatch.setattr(osm, "_post", post)
    monkeypatch.setattr(osm, "RING_GAP_S", 0)
    return seen


def test_rings_stop_early_when_everything_is_found_close(monkeypatch):
    near = [_el(1, JAIPUR[0] + .002, JAIPUR[1], amenity="hospital", name="City Hospital"),
            _el(2, JAIPUR[0] + .003, JAIPUR[1], amenity="school"), _el(3, JAIPUR[0] + .004, JAIPUR[1], shop="supermarket"),
            _el(4, JAIPUR[0] + .005, JAIPUR[1], railway="station"), _el(5, JAIPUR[0] + .001, JAIPUR[1], highway="bus_stop"),
            _el(6, JAIPUR[0] + .006, JAIPUR[1], leisure="park"), {"type": "way", "id": 7, "center": {"lat": JAIPUR[0] + .007, "lon": JAIPUR[1]}, "tags": {"highway": "primary"}}]
    seen = _fake_post(monkeypatch, lambda n, q: near)
    pool = asyncio.run(osm.fetch([JAIPUR], {}))
    assert len(seen) == 1 and "around:1500" in seen[0] and "around:10000" not in seen[0]
    assert pool["hospital"][0][2] == "City Hospital"


def test_rings_widen_only_for_kinds_not_yet_found(monkeypatch):
    far_hospital = _el(1, JAIPUR[0] + .05, JAIPUR[1], amenity="hospital", name="Far Hospital")     # ~5.5 km
    school = _el(2, JAIPUR[0] + .004, JAIPUR[1], amenity="school")

    def handler(n, q):
        return [school] if n == 1 else [far_hospital] if "around:10000" in q else []
    seen = _fake_post(monkeypatch, handler)
    diag = {}
    pool = asyncio.run(osm.fetch([JAIPUR], diag))
    assert len(seen) == 3                                    # 1.5 km, 3 km, full radius for the missing kinds
    assert 'hospital|clinic' in seen[2] and 'school|college' not in seen[2]
    assert pool["hospital"][0][2] == "Far Hospital" and "osm_error" not in diag and "osm_partial" not in diag


def test_later_ring_failure_keeps_earlier_data_and_drops_unchecked_criteria(monkeypatch):
    dense = [_el(i, JAIPUR[0] + .001 * i, JAIPUR[1], **t) for i, t in enumerate(
        [{"amenity": "school"}, {"shop": "supermarket"}, {"railway": "station"}, {"leisure": "park"},
         {"highway": "bus_stop"}, {"highway": "bus_stop"}, {"highway": "bus_stop"}, {"highway": "bus_stop"}], 1)]

    def handler(n, q):
        if n == 1:
            return dense
        raise osm.OverpassError("OpenStreetMap server timed out")
    _fake_post(monkeypatch, handler)
    diag = {}
    pool = asyncio.run(osm.fetch([JAIPUR], diag))
    assert pool is not None and diag["osm_partial"] and "hospital" in diag["osm_unverified"][0]
    assert "osm_error" not in diag


def test_assess_drops_unverified_kinds_and_states_it(monkeypatch):
    _world(monkeypatch)

    async def fetch_osm(centers, diag=None):
        diag["osm_partial"] = "OpenStreetMap server timed out"
        diag["osm_unverified"] = {0: ["hospital"], 1: ["hospital"]}
        return {**_dense_osm(), "hospital": []}
    monkeypatch.setattr(osm, "fetch", fetch_osm)
    res = asyncio.run(li.assess_locations([{"query": "Alpha, Jaipur"}, {"query": "Beta, Jaipur"}]))
    crit = {c["metric"]: c for c in res["locations"][0]["groups"]["liveability"]["criteria"]}
    assert crit["hospital_km"]["value"] is None and crit["school_km"]["value"] is not None
    assert any("did not finish" in c for c in res["caveats"])


def test_summary_lists_nearest_amenities_with_name_distance_direction():
    pts = {"hospital": [(JAIPUR[0] + .01, JAIPUR[1], "SMS Hospital", "hospital"), (JAIPUR[0] - .02, JAIPUR[1], "", "clinic")],
           "school": [(JAIPUR[0], JAIPUR[1] + .01, "DPS", "school")], "market": [], "rail": [], "bus": [(JAIPUR[0], JAIPUR[1] - .003)] * 1,
           "park": [(JAIPUR[0] + .004, JAIPUR[1])] * 1, "road": []}
    s = osm.summarize(JAIPUR, {**_dense_osm(), **pts})
    h = s["nearby"]["hospital"]
    assert h[0]["name"] == "SMS Hospital" and h[0]["direction"] == "N" and 1.0 < h[0]["distance_km"] < 1.2
    assert h[1]["direction"] == "S" and h[1]["type"] == "clinic"
    assert s["nearby"]["school"][0]["direction"] == "E"
    assert s["nearby"]["bus"][0]["name"] == ""                    # 2-tuples (no label) still work


def test_dense_city_nearest_is_exact_because_inner_ring_is_not_capped_by_far_results():
    # 500 far clinics would fill a 400-result cap in the old single 10 km query; the 1.5 km ring only sees near ones.
    q = osm.build_query_pairs([(JAIPUR[0], JAIPUR[1], "hospital", 1.5)])
    assert "around:1500" in q and "around:10000" not in q and q.count("out center tags") == 1


# ── geocoding resilience (Nominatim 429 -> retry -> Photon; cache; honest messages) ──
def _fake_http(monkeypatch, nominatim, photon):
    """nominatim/photon: callables (call_no) -> (status, json_body, headers)."""
    calls = {"nom": 0, "pho": 0}

    class Resp:
        def __init__(self, status, body, headers=None):
            self.status_code, self.body, self.headers = status, body, headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self.body

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if "nominatim" in url:
                calls["nom"] += 1
                return Resp(*nominatim(calls["nom"]))
            calls["pho"] += 1
            return Resp(*photon(calls["pho"]))
    monkeypatch.setattr(geocode_util.httpx, "AsyncClient", Client)

    async def nosleep(s):
        pass
    monkeypatch.setattr(geocode_util.asyncio, "sleep", nosleep)
    geocode_util._POINT_CACHE.clear()
    return calls


PHOTON_HIT = (200, {"features": [{"geometry": {"type": "Point", "coordinates": [75.76, 26.85]}}]}, {})


def test_nominatim_429_retries_then_succeeds(monkeypatch):
    calls = _fake_http(monkeypatch, lambda n: (429, {}, {"Retry-After": "1"}) if n == 1 else (200, [{"lat": "26.85", "lon": "75.76"}], {}),
                       lambda n: PHOTON_HIT)
    d = {}
    assert run(geocode_util.geocode_point("Mansarovar, Jaipur", d)) == (26.85, 75.76)
    assert calls == {"nom": 2, "pho": 0} and not d


def test_nominatim_persistently_429_falls_back_to_photon(monkeypatch):
    calls = _fake_http(monkeypatch, lambda n: (429, {}, {}), lambda n: PHOTON_HIT)
    d = {}
    assert run(geocode_util.geocode_point("Mansarovar, Jaipur", d)) == (26.85, 75.76)     # Photon note: [lon, lat] order handled
    assert calls["nom"] == 2 and calls["pho"] == 1 and not d


def test_both_services_busy_reports_a_service_error_not_not_found(monkeypatch):
    _fake_http(monkeypatch, lambda n: (429, {}, {}), lambda n: (429, {}, {}))
    d = {}
    assert run(geocode_util.geocode_point("Mansarovar, Jaipur", d)) is None
    assert "Nominatim" in d["geocode_error"] and "Photon" in d["geocode_error"] and "429" in d["geocode_error"]


def test_genuine_no_match_is_not_reported_as_a_service_error(monkeypatch):
    _fake_http(monkeypatch, lambda n: (200, [], {}), lambda n: (200, {"features": []}, {}))
    d = {}
    assert run(geocode_util.geocode_point("Qwertyuiop Nowhere", d)) is None and "geocode_error" not in d


def test_successful_lookups_are_cached(monkeypatch):
    calls = _fake_http(monkeypatch, lambda n: (200, [{"lat": "26.85", "lon": "75.76"}], {}), lambda n: PHOTON_HIT)
    assert run(geocode_util.geocode_point("Mansarovar, Jaipur")) == (26.85, 75.76)
    assert run(geocode_util.geocode_point("  mansarovar,   JAIPUR ")) == (26.85, 75.76)
    assert calls["nom"] == 1


def test_assess_says_lookup_service_is_busy_instead_of_not_found(monkeypatch):
    monkeypatch.setattr(li, "NOMINATIM_GAP_S", 0)

    async def busy(q, diag=None):
        diag["geocode_error"] = "Nominatim is rate-limiting this server (HTTP 429)"
        return None
    monkeypatch.setattr(geocode_util, "geocode_point", busy)
    with pytest.raises(ValueError) as e:
        run(li.assess_locations([{"query": "Mansarovar, Jaipur"}]))
    assert "busy or unreachable" in str(e.value) and "429" in str(e.value) and "'lat, lon'" in str(e.value)


def test_coordinates_never_need_the_geocoder(monkeypatch):
    async def boom(q, diag=None):
        raise AssertionError("geocoder must not be called for coordinates")
    monkeypatch.setattr(geocode_util, "geocode_point", boom)
    out = run(li.resolve_locations([{"query": "26.85, 75.76"}], 2.0, []))
    assert out and out[0]["lat"] == 26.85
