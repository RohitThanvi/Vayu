"""Offline tests for the combined (web + Earth Engine + OpenStreetMap) siting agent.
GEE, Overpass, geocoding and the LLM are stubbed; these check the deterministic
parts that make the ranking auditable: utilities, scoring with missing evidence,
OSM completeness guards, candidate sanity checks, orchestration and degradation."""
import asyncio

import pytest

from app.services import siting_agent as sa
from app.services import research_agent as ra
from app.services import data_router, geocode_util


def run(coro):
    return asyncio.run(coro)


JAIPUR = (26.9124, 75.7873)


# ── pure scoring ──────────────────────────────────────────────────────────────
def test_utility_is_directional_and_clamped():
    assert sa.utility(6, 2, 10) == pytest.approx(0.5)       # lower is better
    assert sa.utility(1, 2, 10) == 1.0 and sa.utility(15, 2, 10) == 0.0
    assert sa.utility(8, 15, 1) == pytest.approx(0.5)       # higher is better
    assert sa.utility(30, 15, 1) == 1.0 and sa.utility(0, 15, 1) == 0.0


def test_profile_weights_sum_to_one():
    for key, prof in sa.PROFILES.items():
        assert sum(w for _, _, w in prof["criteria"].values()) == pytest.approx(1.0), key
        for metric in prof["criteria"]:
            assert metric in sa.METRICS, (key, metric)


def test_score_uses_only_available_criteria_and_reports_coverage():
    full = {"slope_deg": 1, "cropland_pct": 5, "tree_pct": 1, "water_pct": 0, "builtup_pct": 2,
            "substation_km": 3, "road_km": 1}
    s_full = sa.score_candidate("solar_farm", full)
    assert s_full["score"] == 100.0 and s_full["evidence_coverage"] == 1.0 and s_full["evidence_level"] == "strong"

    partial = {k: full[k] for k in ("slope_deg", "cropland_pct", "tree_pct", "water_pct", "builtup_pct")}   # no OSM
    s_part = sa.score_candidate("solar_farm", partial)
    assert s_part["evidence_coverage"] == pytest.approx(0.65)
    assert s_part["score"] == 100.0                      # missing criteria are NOT penalised or rewarded
    assert [c for c in s_part["criteria"] if c["metric"] == "substation_km"][0]["utility"] is None


def test_too_little_evidence_means_no_score():
    s = sa.score_candidate("solar_farm", {"slope_deg": 1})           # 25% coverage
    assert s["score"] is None and s["evidence_level"] == "weak"


def test_flags_and_ranking_order():
    bad = sa.score_candidate("solar_farm", {"slope_deg": 14, "cropland_pct": 70, "tree_pct": 40, "water_pct": 45,
                                            "builtup_pct": 5, "substation_km": 40, "road_km": 20})
    assert any("water" in f.lower() for f in bad["flags"]) and any("steep" in f.lower() for f in bad["flags"])
    good = sa.score_candidate("solar_farm", {"slope_deg": 1, "cropland_pct": 5, "tree_pct": 1, "water_pct": 0,
                                             "builtup_pct": 1, "substation_km": 4, "road_km": 1})
    weak = sa.score_candidate("solar_farm", {"slope_deg": 1})
    ranked = sa.rank_candidates([{**weak, "name": "W"}, {**bad, "name": "B"}, {**good, "name": "G"}])
    assert [r["name"] for r in ranked] == ["G", "B", "W"]
    assert [r["rank"] for r in ranked] == [1, 2, None]


def test_guess_use_case():
    assert sa.guess_use_case("where should we add a mobile tower near Jaipur") == "mobile_tower"
    assert sa.guess_use_case("best place for a solar farm in Jodhpur") == "solar_farm"
    assert sa.guess_use_case("cold storage for onions near Lasalgaon") == "cold_storage"
    assert sa.guess_use_case("what is the weather") is None


# ── OSM ───────────────────────────────────────────────────────────────────────
def test_overpass_query_and_parse():
    q = sa.build_overpass_query([(26.9, 75.8)], {"substation", "tower"})
    assert "[out:json]" in q and '"power"="substation"' in q and "around:30000,26.90000,75.80000" in q
    assert "around:5000" in q and q.rstrip().endswith("out center tags 4000;")

    els = [
        {"type": "node", "id": 1, "lat": 26.91, "lon": 75.81, "tags": {"power": "substation"}},
        {"type": "node", "id": 1, "lat": 26.91, "lon": 75.81, "tags": {"power": "substation"}},     # duplicate
        {"type": "way", "id": 2, "center": {"lat": 26.95, "lon": 75.85}, "tags": {"highway": "primary"}},
        {"type": "way", "id": 3, "center": {"lat": 26.95, "lon": 75.85}, "tags": {"highway": "residential"}},   # not major
        {"type": "node", "id": 4, "lat": 26.9, "lon": 75.8, "tags": {"man_made": "mast", "tower:type": "communication"}},
        {"type": "node", "id": 5, "lat": 26.9, "lon": 75.8, "tags": {"amenity": "charging_station"}},
    ]
    p = sa.parse_overpass(els)
    assert len(p["substation"]) == 1 and len(p["road"]) == 1 and len(p["tower"]) == 1 and len(p["charger"]) == 1


def test_haversine_known_distance():
    assert sa.haversine_km(26.9124, 75.7873, 28.6139, 77.2090) == pytest.approx(237, abs=6)   # Jaipur-Delhi


def _cands(n=2):
    return [{"cand": {"name": f"C{i}", "lat": 26.9, "lon": 75.8}, "metrics": {}, "notes": {}, "errors": []} for i in range(n)]


def test_osm_guard_drops_unmapped_kinds_instead_of_rewarding_them():
    cands = _cands()
    caveats = []
    osm = {"substation": [], "road": [(26.9, 75.81)], "tower": [], "charger": []}
    for c in cands:
        m, n = sa._osm_metrics_for((c["cand"]["lat"], c["cand"]["lon"]), osm, {"substation", "road", "tower"})
        c["metrics"].update(m)
        c["notes"].update(n)
    sa._apply_osm_guards(cands, osm, {"substation", "road", "tower"}, caveats)
    for c in cands:
        assert "substation_km" not in c["metrics"]        # nothing mapped anywhere -> not scored
        assert "tower_density" not in c["metrics"]        # <5 towers mapped -> zero is not informative
        assert "road_km" in c["metrics"]
    assert any("substation" in x for x in caveats) and any("telecom" in x or "tower" in x for x in caveats)


def test_osm_none_near_candidate_is_lower_bound_when_area_is_mapped():
    cands = _cands()
    osm = {"substation": [(26.91, 75.81)], "road": [], "tower": [], "charger": []}
    far = (30.0, 78.0)
    m_near, _ = sa._osm_metrics_for((26.9, 75.8), osm, {"substation"})
    m_far, n_far = sa._osm_metrics_for(far, osm, {"substation"})
    assert m_near["substation_km"] < 5
    assert m_far["substation_km"] == sa.OSM_RADIUS_KM["substation"] and "lower bound" in n_far["substation_km"]


# ── candidates ────────────────────────────────────────────────────────────────
def _stub_geocode(monkeypatch, table):
    async def fake(q):
        for key, (lat, lon) in table.items():
            if key in q:
                return {"geometry": {"type": "Point", "coordinates": [lon, lat]}}
        return None
    monkeypatch.setattr(geocode_util, "geocode_place", fake)
    monkeypatch.setattr(sa, "NOMINATIM_GAP_S", 0)


def test_resolve_candidates_rejects_unfound_and_far_away_names(monkeypatch):
    _stub_geocode(monkeypatch, {"Mansarovar": (26.85, 75.76), "Springfield": (39.78, -89.65)})
    caveats = []
    places = [{"place_name": "Mansarovar", "radius_km": 9}, {"place_name": "Springfield"}, {"place_name": "Nowhere"}]
    out = run(sa.resolve_candidates(places, "Jaipur, Rajasthan", JAIPUR, caveats))
    assert [c["name"] for c in out] == ["Mansarovar"]
    assert out[0]["radius_km"] == 4.0                        # clamped
    assert out[0]["aoi"]["type"] == "Polygon"
    assert any("Springfield" in c for c in caveats) and any("Nowhere" in c for c in caveats)


# ── orchestration ─────────────────────────────────────────────────────────────
def _stub_gee(monkeypatch, per_name=None, fail=()):
    per_name = per_name or {}

    def name_of(aoi):
        # the circle's first vertex encodes which candidate this is: use the latitude
        lat = aoi["coordinates"][0][0][1]
        return min(per_name, key=lambda n: abs(per_name[n]["lat"] - lat)) if per_name else None

    async def terrain(aoi, sem):
        if "terrain" in fail:
            raise RuntimeError("GEE quota")
        return {"slope_deg": per_name[name_of(aoi)]["slope"]}

    async def lulc(aoi, sem):
        d = per_name[name_of(aoi)]
        return {"cropland_pct": d["crop"], "builtup_pct": d["built"], "tree_pct": 1.0, "water_pct": 0.0}

    async def night(aoi, sem):
        return {"night_light": per_name[name_of(aoi)]["nl"]}

    monkeypatch.setattr(sa, "_gee_terrain", terrain)
    monkeypatch.setattr(sa, "_gee_lulc", lulc)
    monkeypatch.setattr(sa, "_gee_night_lights", night)


def test_end_to_end_solar_ranking(monkeypatch):
    table = {"Alpha": (26.90, 75.80), "Beta": (26.95, 75.85)}
    _stub_geocode(monkeypatch, table)
    _stub_gee(monkeypatch, {"Alpha": {"lat": 26.90, "slope": 1.0, "crop": 5, "built": 2, "nl": 0.5},
                            "Beta": {"lat": 26.95, "slope": 9.0, "crop": 55, "built": 10, "nl": 3.0}})

    async def fake_osm(centers, kinds, diag):
        return {"substation": [(26.91, 75.81), (26.96, 75.86)], "road": [(26.9, 75.805), (26.95, 75.9)],
                "tower": [], "charger": []}
    monkeypatch.setattr(sa, "fetch_osm", fake_osm)

    seen_progress = []

    async def llm(system, user, max_tokens):
        assert "Alpha" in user and "Ranking (authoritative)" in user
        return {"recommendation": "Shortlist Alpha first: flat terrain and little cropland.",
                "tradeoffs": ["Beta is closer to demand but steeper"], "conflicts": [], "next_steps": ["Check land records"]}

    res = run(sa.evaluate_candidates(
        question="best place for a solar farm", region="Jaipur", region_center=JAIPUR,
        places=[{"place_name": "Beta", "reasoning": "web says barren"}, {"place_name": "Alpha", "reasoning": "web says flat"}],
        aoi=None, use_case="solar_farm", llm=llm, progress=lambda l, p: seen_progress.append((l, p)),
        web_summary="x", key_points=[], diag={}))

    names = [r["name"] for r in res["ranking"]]
    assert names == ["Alpha", "Beta"]                          # input order was Beta, Alpha; measurements decide
    assert res["ranking"][0]["score"] > res["ranking"][1]["score"]
    assert res["ranking"][0]["evidence_level"] == "strong"
    assert res["recommendation"].startswith("Shortlist Alpha")
    assert all(c["value"] is not None for c in res["ranking"][0]["criteria"])
    assert seen_progress and seen_progress[-1][1] >= 90 and "methodology" in res and "pre-feasibility" in res["methodology"]


def test_tool_failure_degrades_to_partial_evidence_not_a_crash(monkeypatch):
    _stub_geocode(monkeypatch, {"Alpha": (26.90, 75.80)})
    _stub_gee(monkeypatch, {"Alpha": {"lat": 26.90, "slope": 1.0, "crop": 5, "built": 2, "nl": 0.5}}, fail=("terrain",))

    async def osm_down(centers, kinds, diag):
        diag["osm_error"] = "timeout"
        return None
    monkeypatch.setattr(sa, "fetch_osm", osm_down)

    res = run(sa.evaluate_candidates(question="q", region="Jaipur", region_center=JAIPUR,
                                     places=[{"place_name": "Alpha"}], aoi=None, use_case="solar_farm",
                                     llm=None, diag={}))
    r = res["ranking"][0]
    # land-cover criteria only: 0.15+0.10+0.05+0.10 = 0.40 coverage < 0.5 -> must NOT be scored
    assert r["score"] is None and r["evidence_level"] == "weak"
    assert any("GEE quota" in c for c in res["caveats"]) and any("OpenStreetMap query failed" in c for c in res["caveats"])
    assert "not scored" in res["recommendation"] or "enough measurable evidence" in res["recommendation"]


def test_assessment_of_a_drawn_aoi(monkeypatch):
    aoi = {"type": "Polygon", "coordinates": [[[75.8, 26.9], [75.82, 26.9], [75.82, 26.92], [75.8, 26.92], [75.8, 26.9]]]}
    _stub_gee(monkeypatch, {"Selected area": {"lat": 26.9, "slope": 2.0, "crop": 8, "built": 3, "nl": 1.0}})

    async def fake_osm(centers, kinds, diag):
        return {"substation": [(26.91, 75.81)], "road": [(26.91, 75.81)], "tower": [], "charger": []}
    monkeypatch.setattr(sa, "fetch_osm", fake_osm)
    res = run(sa.evaluate_candidates(question="is this area suitable for solar", region=None, region_center=None,
                                     places=[], aoi=aoi, use_case="solar_farm", llm=None, diag={}))
    assert res["ranking"][0]["name"] == "Selected area" and res["ranking"][0]["score"] is not None


# ── integration through research_agent.ask ────────────────────────────────────
def test_ask_runs_combined_stage_and_merges_results(monkeypatch):
    async def no_live(q):
        return None
    monkeypatch.setattr(data_router, "try_answer_from_live_data", no_live)

    async def fake_search(query, num_results=8, diag=None):
        return [{"title": "Solar siting Jaipur", "url": "https://a.org/1", "snippet": "Alpha and Beta have barren land.", "provider": "serpapi"},
                {"title": "Rajasthan solar policy", "url": "https://b.com/2", "snippet": "Alpha Beta policy", "provider": "serpapi"}]
    monkeypatch.setattr(ra, "search_web", fake_search)

    async def fake_fetch(client, url):
        return "Barren land near Alpha and Beta is being considered for solar projects in Jaipur. " * 6
    monkeypatch.setattr(ra, "_fetch_page_text", fake_fetch)

    async def llm(system, user, max_tokens):
        if system is ra.PLANNER_SYSTEM:
            return {"question_type": "siting", "wants_places": True, "use_case": "solar_farm", "queries": ["solar jaipur"]}
        if system is sa.NARRATE_SYSTEM:
            return {"recommendation": "Alpha ranks first on measured terrain and land cover.", "tradeoffs": [], "conflicts": [], "next_steps": []}
        return {"answer": "Alpha and Beta are discussed.", "key_points": [{"point": "barren land", "sources": [1]}],
                "places": [{"place_name": "Beta", "reasoning": "barren", "radius_km": 2, "confidence": "medium", "sources": [1]},
                           {"place_name": "Alpha", "reasoning": "flat", "radius_km": 2, "confidence": "medium", "sources": [1]}],
                "gaps": [], "confidence": "medium"}
    monkeypatch.setattr(ra, "_llm_json", llm)

    _stub_geocode(monkeypatch, {"Alpha": (26.90, 75.80), "Beta": (26.95, 75.85)})
    _stub_gee(monkeypatch, {"Alpha": {"lat": 26.90, "slope": 1.0, "crop": 5, "built": 2, "nl": 0.5},
                            "Beta": {"lat": 26.95, "slope": 9.0, "crop": 55, "built": 10, "nl": 3.0}})

    async def fake_osm(centers, kinds, diag):
        return {"substation": [(26.91, 75.81)], "road": [(26.9, 75.805)], "tower": [], "charger": []}
    monkeypatch.setattr(sa, "fetch_osm", fake_osm)

    res = run(ra.ask("best place for a solar farm near Jaipur", "Jaipur, Rajasthan"))
    assert res["place_name"] == "Alpha"                                   # reordered by measured score
    assert res["places"][0]["rank"] == 1 and res["places"][0]["lat"] is not None
    assert res["answer"].startswith("Alpha ranks first") and res["web_summary"].startswith("Alpha and Beta")
    assert res["ranking"] and res["methodology"] and res["use_case"] == "Solar farm"
    assert res["key_points"]                                              # web evidence still present


def test_ask_siting_failure_keeps_web_answer(monkeypatch):
    async def no_live(q):
        return None
    monkeypatch.setattr(data_router, "try_answer_from_live_data", no_live)

    async def fake_search(query, num_results=8, diag=None):
        return [{"title": "Mobile towers Jaipur", "url": "https://a.org/1", "snippet": "Mansarovar tower gaps.", "provider": "serpapi"}]
    monkeypatch.setattr(ra, "search_web", fake_search)

    async def fake_fetch(client, url):
        return "Mansarovar has weak mobile coverage and few towers according to residents. " * 6
    monkeypatch.setattr(ra, "_fetch_page_text", fake_fetch)

    async def llm(system, user, max_tokens):
        if system is ra.PLANNER_SYSTEM:
            return {"question_type": "siting", "wants_places": True, "use_case": "mobile_tower", "queries": ["towers"]}
        return {"answer": "Mansarovar is underserved.", "key_points": [], "places": [
            {"place_name": "Mansarovar", "reasoning": "r", "radius_km": 2, "confidence": "medium", "sources": [1]}],
            "gaps": [], "confidence": "medium"}
    monkeypatch.setattr(ra, "_llm_json", llm)

    async def boom(*a, **k):
        raise RuntimeError("everything on fire")
    monkeypatch.setattr(sa, "evaluate_candidates", boom)

    res = run(ra.ask("where should we add a mobile tower", "Jaipur"))
    assert res["place_name"] == "Mansarovar" and "underserved" in res["answer"]
    assert any("measurement failed" in g for g in res["gaps"])
