"""Offline tests for the v2 research agent: search / page-fetch / LLM are stubbed,
so these check the pipeline logic (planning, ranking, passage retrieval, grounding,
reflection, degradation) — not live-web quality."""
import asyncio

import pytest

from app.services import research_agent as ra
from app.services import data_router


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _no_network_geocoding(monkeypatch):
    """The siting stage geocodes candidates; keep these unit tests fully offline."""
    from app.services import geocode_util

    async def nothing(q):
        return None
    monkeypatch.setattr(geocode_util, "geocode_place", nothing)


# ── pure helpers ──────────────────────────────────────────────────────────────
def test_html_to_text_drops_scripts_and_nav():
    html = ("<html><nav>MENU</nav><script>var x=1;</script><style>.a{}</style>"
            "<article><p>Jaipur plans a new metro corridor.</p><p>Demand is high in Mansarovar.</p></article>"
            "<footer>copyright</footer></html>")
    t = ra._html_to_text(html)
    assert "Jaipur plans" in t and "Mansarovar" in t
    assert "MENU" not in t and "var x" not in t and "copyright" not in t


def test_best_passages_prefers_relevant_text():
    filler = "Unrelated cookie policy and newsletter signup text goes here for padding purposes. " * 4
    relevant = "Mobile tower coverage gaps in Mansarovar Jaipur are driven by dense housing growth and weak signal."
    text = "\n".join([filler, filler, relevant, filler, filler])
    out = ra._best_passages(text, set(ra._tokens("mobile tower coverage Mansarovar Jaipur")), k=1)
    assert "Mansarovar" in out


def test_rank_results_caps_per_domain_and_prefers_title_match():
    results = [{"title": f"Page {i}", "url": f"https://spam.com/{i}", "snippet": ""} for i in range(5)]
    results.append({"title": "Mobile tower sites Jaipur", "url": "https://good.org/a", "snippet": "tower Jaipur"})
    ranked = ra._rank_results(results, "where to add mobile tower in Jaipur", "Jaipur", limit=6)
    assert ranked[0]["url"] == "https://good.org/a"
    assert sum(1 for r in ranked if "spam.com" in r["url"]) <= 2


def test_aoi_center_handles_feature_and_polygon():
    poly = {"type": "Polygon", "coordinates": [[[75.0, 26.0], [76.0, 26.0], [76.0, 27.0], [75.0, 27.0], [75.0, 26.0]]]}
    assert ra._aoi_center(poly) == (26.5, 75.5)
    assert ra._aoi_center({"type": "Feature", "geometry": poly}) == (26.5, 75.5)
    assert ra._aoi_center(None) is None


def test_place_grounding():
    corpus = "new towers are proposed in mansarovar and vaishali nagar according to the plan"
    assert ra._place_is_grounded("Mansarovar", corpus)
    assert ra._place_is_grounded("Vaishali Nagar", corpus)
    assert not ra._place_is_grounded("Malviya Nagar", corpus)


def test_router_uses_whole_words():
    q = "what is the relationship between the country and the planet"
    assert not data_router._has(q, data_router.MARITIME_WORDS)      # 'ship' in 'relationship'
    assert not data_router._has(q, data_router.COUNT_WORDS)         # 'count' in 'country'
    assert not data_router._has(q, data_router.AVIATION_WORDS)      # 'plane' in 'planet'
    assert data_router._has("how many ships are in hormuz", data_router.MARITIME_WORDS)
    assert data_router._match_chokepoint("any tankers near the strait of hormuz") == "strait_of_hormuz"


# ── pipeline with stubs ───────────────────────────────────────────────────────
class Stubs:
    def __init__(self):
        self.search_calls = []
        self.llm_calls = []


@pytest.fixture
def env(monkeypatch):
    st = Stubs()

    async def no_live(q):
        return None
    monkeypatch.setattr(data_router, "try_answer_from_live_data", no_live)

    async def fake_search(query, num_results=8, diag=None):
        st.search_calls.append(query)
        return [
            {"title": f"Tower siting in Jaipur ({len(st.search_calls)})", "url": f"https://site{len(st.search_calls)}.org/p",
             "snippet": "Mansarovar and Vaishali Nagar have coverage gaps.", "provider": "serpapi"},
            {"title": "Jaipur telecom overview", "url": f"https://other{len(st.search_calls)}.com/p",
             "snippet": "Telecom expansion in Jaipur.", "provider": "serpapi"},
        ]
    monkeypatch.setattr(ra, "search_web", fake_search)

    async def fake_fetch(client, url):
        return ("Operators report weak signal in Mansarovar and Vaishali Nagar because of dense housing growth. " * 5)
    monkeypatch.setattr(ra, "_fetch_page_text", fake_fetch)
    return st


def test_happy_path_returns_grounded_places_and_answer(env, monkeypatch):
    async def fake_llm(system, user, max_tokens):
        env.llm_calls.append(system[:20])
        if system is ra.PLANNER_SYSTEM:
            return {"question_type": "siting", "wants_places": True,
                    "queries": ["mobile tower coverage gaps Jaipur", "Jaipur telecom expansion plan"]}
        return {"answer": "Coverage gaps cluster in two dense localities.",
                "key_points": [{"point": "Weak signal in dense housing", "sources": [1, 2]}],
                "places": [
                    {"place_name": "Mansarovar", "reasoning": "dense housing, weak signal", "radius_km": 2, "confidence": "high", "sources": [1, 2]},
                    {"place_name": "Sanganer Airport Zone", "reasoning": "made up", "radius_km": 2, "confidence": "high", "sources": [1]},
                ],
                "gaps": [], "confidence": "high"}
    monkeypatch.setattr(ra, "_llm_json", fake_llm)

    res = run(ra.ask("where should we add a mobile tower", "Jaipur"))
    assert res["place_name"] == "Mansarovar"
    assert [p["place_name"] for p in res["places"]] == ["Mansarovar"]          # hallucinated place dropped
    assert any("Sanganer" in g for g in res["gaps"])
    assert res["answer"] and res["key_points"][0]["urls"]
    assert res["diagnostics"]["pages_fetched"] >= 1 and res["diagnostics"]["rounds"] == 1
    assert len(env.search_calls) == 2                                        # multi-query, not one raw query


def test_weak_first_round_triggers_reflection(env, monkeypatch):
    calls = {"synth": 0}

    async def fake_llm(system, user, max_tokens):
        if system is ra.PLANNER_SYSTEM:
            if "already tried" in user:
                return {"queries": ["jaipur residential growth towers"]}
            return {"question_type": "siting", "wants_places": True, "queries": ["tower jaipur"]}
        calls["synth"] += 1
        if calls["synth"] == 1:
            return {"answer": "Not enough detail.", "places": [], "gaps": ["no localities named"], "confidence": "low"}
        return {"answer": "Mansarovar looks underserved.", "places": [
            {"place_name": "Mansarovar", "reasoning": "r", "radius_km": 2, "confidence": "medium", "sources": [1]}],
            "gaps": [], "confidence": "medium"}
    monkeypatch.setattr(ra, "_llm_json", fake_llm)

    res = run(ra.ask("where to add a tower", "Jaipur"))
    assert res["diagnostics"]["rounds"] == 2
    assert res["place_name"] == "Mansarovar"


def test_llm_down_still_returns_sources_not_an_error(env, monkeypatch):
    async def boom(system, user, max_tokens):
        raise RuntimeError("groq unavailable")
    monkeypatch.setattr(ra, "_llm_json", boom)

    res = run(ra.ask("what is the latest on Jaipur metro", "Jaipur"))
    assert res["answer"]                         # extractive fallback, not an empty failure
    assert res["source_urls"]
    assert res["confidence"] == "low"
    assert any("summar" in g.lower() for g in res["gaps"])


def test_no_search_results_gives_explained_failure(monkeypatch):
    async def no_live(q):
        return None
    monkeypatch.setattr(data_router, "try_answer_from_live_data", no_live)

    async def empty_search(query, num_results=8, diag=None):
        if diag is not None:
            diag.setdefault("search_errors", []).append("SerpApi error: out of searches")
        return []
    monkeypatch.setattr(ra, "search_web", empty_search)

    async def llm(system, user, max_tokens):
        return {"question_type": "fact", "wants_places": False, "queries": ["a", "b"]}
    monkeypatch.setattr(ra, "_llm_json", llm)

    res = run(ra.ask("anything", "Jaipur"))
    assert res["places"] == [] and res["confidence"] == "low"
    assert "out of searches" in res["answer"]    # the real reason is surfaced, not a generic message


def test_timeout_returns_best_so_far(env, monkeypatch):
    monkeypatch.setattr(ra, "TOTAL_BUDGET_S", 0.3)

    async def slow_llm(system, user, max_tokens):
        if system is ra.PLANNER_SYSTEM:
            return {"question_type": "fact", "wants_places": False, "queries": ["q"]}
        await asyncio.sleep(2)
        return {}
    monkeypatch.setattr(ra, "_llm_json", slow_llm)

    res = run(ra.ask("q", "Jaipur"))
    assert "too long" in res["answer"].lower() or res["diagnostics"].get("notes")


def test_live_data_shortcut_still_wins(monkeypatch):
    async def live(q):
        return {"places": [], "place_name": None, "reasoning": "23 vessels in Hormuz", "confidence": "high",
                "radius_km": None, "source_urls": [], "live_data_source": "AIS"}
    monkeypatch.setattr(data_router, "try_answer_from_live_data", live)
    res = run(ra.ask("how many ships in hormuz", None))
    assert res["answer"] == "23 vessels in Hormuz" and res["live_data_source"] == "AIS"
