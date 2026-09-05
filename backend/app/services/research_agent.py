"""
research_agent.py — answers open-ended questions Vayu has no fixed
dataset for ("where should we add new mobile towers near Jaipur?") by
searching the live web, grounding an LLM's answer in the actual search
results, and naming ONE specific place for the frontend to geocode and
mark on the map.

Deliberately NOT a multi-agent framework (CrewAI etc.) — this is a
linear pipeline (search -> LLM extraction -> return), and a full agent
orchestration framework buys nothing here beyond dependency weight and
slower cold starts on Render's free tier, which has already caused real
problems elsewhere in this project. A plain function chain does the
same job and is far easier to debug.

Web search source: SerpApi (Google Search results, real JSON API,
serpapi.com). Replaced the previous public-SearXNG-probing approach —
that source proved unreliable in practice (unclear whether from failing
to find a working JSON-capable public instance, or finding one but
returning irrelevant results). Alternatives considered and rejected:
Google's own Custom Search API (discontinued for new signups, redirects
to the paid Vertex AI Search API), Brave Search API (requires card
details on file even for its free tier). SerpApi's free plan is
genuinely signup-only (email, no card) for 100 searches/month, which is
comfortably enough for an occasional out-of-scope-query fallback path,
not the primary traffic driver of this app.

Needs SERPAPI_KEY set in the environment (get one free at
serpapi.com/manage-api-key after signup). If it's missing or every
request fails, this returns an empty result rather than crashing — the
calling endpoint then reports "search unavailable" rather than
fabricating an answer, same failure-handling contract as before.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from .llm_client import _get_client, _extract_json
from . import data_router

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

_HEADERS = {"User-Agent": "VAYU-Research-Agent/1.0"}


async def search_web(query: str, num_results: int = 6) -> List[Dict[str, str]]:
    """Search the web via SerpApi (Google results, real JSON, no
    JSON-availability guessing needed unlike public SearXNG). Returns an
    empty list (not an exception) on any failure — missing key, quota
    exhausted, network error — so the research agent degrades to
    "search unavailable" instead of crashing the request."""
    if not SERPAPI_KEY:
        logger.warning("Research agent: SERPAPI_KEY not configured, skipping web search")
        return []

    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "engine": "google",
        "num": num_results,
    }
    try:
        async with httpx.AsyncClient(headers=_HEADERS) as client:
            resp = await client.get(SERPAPI_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Research agent: SerpApi search failed: {type(e).__name__}: {e}")
        return []

    if data.get("error"):
        # SerpApi returns HTTP 200 with an "error" field for things like
        # an exhausted monthly quota or an invalid key — treat the same
        # as a failed request rather than silently returning nothing
        # with no signal in the logs.
        logger.warning(f"Research agent: SerpApi returned an error: {data['error']}")
        return []

    results = []
    for r in (data.get("organic_results") or [])[:num_results]:
        title, url, snippet = r.get("title"), r.get("link"), r.get("snippet")
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet or ""})
    return results


RESEARCH_SYSTEM_PROMPT = """\
You are a geospatial research assistant helping identify specific real-world \
locations for questions a fixed dataset can't answer directly (e.g. "where \
should we consider adding a mobile tower near this area?").

You will be given a region context, a question, and a set of web search \
result snippets. Your job: identify one or more specific named places \
(a locality, neighborhood, landmark, or precise sub-area — NOT a whole city) \
that answer the question, grounded ONLY in the provided snippets. If the \
question is naturally answered by several distinct candidate spots (e.g. \
"where could we add a mobile tower in Jaipur" often has several reasonable \
areas, not one single answer), list each one separately rather than \
picking just one — but only ones the snippets actually support by name.

Rules:
- Never invent a place name that isn't supported by the snippets. If the \
snippets don't contain enough specific information to name any real, \
precise place, return an empty "places" list rather than guessing.
- Each place must be a real, specific, named sub-area — not a repeat of \
the whole city/region already given as context.
- Write "reasoning" entirely in your own words — do not quote source text \
verbatim, even briefly. Each place's reasoning should explain why THAT \
specific spot fits, not restate the whole question.
- Suggest a radius in kilometers for marking each location on a map, scaled \
to how precise vs. broad that answer is (a single specific site suggestion: \
1-2; a broader named neighborhood/area: 2-5).
- Set "confidence" per place, honestly, based on how directly the snippets \
support that specific one.
- Order "places" from most to least well-supported by the snippets.

Return ONLY valid JSON, no markdown fences, no text outside the JSON object:
{
  "places": [
    {"place_name": string, "reasoning": string, "radius_km": number, "confidence": "low"|"medium"|"high"}
  ],
  "source_urls": [string, ...]
}
"""


def _empty_answer(reasoning: str) -> Dict[str, Any]:
    return {
        "places": [],
        "place_name": None,
        "reasoning": reasoning,
        "radius_km": None,
        "confidence": "low",
        "source_urls": [],
    }


def _synthesize_answer(question: str, region_context: Optional[str], search_results: List[Dict[str, str]]) -> Dict[str, Any]:
    if not search_results:
        return _empty_answer("No web search results were available to ground an answer — the search source was unreachable this time.")

    snippets_text = "\n\n".join(
        f"[{i+1}] {r['title']}\n{r['snippet']}\nSource: {r['url']}"
        for i, r in enumerate(search_results)
    )
    user_prompt = f"Region context: {region_context or 'not specified'}\nQuestion: {question}\n\nSearch results:\n{snippets_text}"

    client = _get_client()
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=900,
        )
        parsed = _extract_json(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"Research agent: LLM synthesis error: {e}")
        return _empty_answer(f"The research agent hit an internal error while reasoning over search results ({type(e).__name__}).")

    # Defensive parsing — a malformed/partial LLM response shouldn't crash
    # the endpoint. Accept both the new {"places": [...]} shape and, just
    # in case the model reverts to the old single-place shape under load,
    # a lone place_name/reasoning/radius_km/confidence set too.
    raw_places = parsed.get("places")
    if not isinstance(raw_places, list):
        raw_places = [parsed] if parsed.get("place_name") else []

    places = []
    for p in raw_places:
        if not isinstance(p, dict) or not p.get("place_name"):
            continue
        places.append({
            "place_name": p.get("place_name"),
            "reasoning": p.get("reasoning", ""),
            "radius_km": p.get("radius_km") or 2,
            "confidence": p.get("confidence", "low"),
        })

    source_urls = parsed.get("source_urls") or [r["url"] for r in search_results[:3]]
    first = places[0] if places else {}
    return {
        "places": places,
        # Backward-compatible single-place fields, mirroring the
        # best-supported candidate — older frontend code that only reads
        # these still works, and the map draws one circle per place in
        # "places" when present (see App.jsx).
        "place_name": first.get("place_name"),
        "reasoning": first.get("reasoning", "" if places else "The available search results didn't name a specific enough place to mark on the map."),
        "radius_km": first.get("radius_km"),
        "confidence": first.get("confidence", "low"),
        "source_urls": source_urls,
    }


async def ask(question: str, region_context: Optional[str] = None) -> Dict[str, Any]:
    """Full pipeline: try Vayu's own live intel feeds first (AIS/ADS-B/
    USGS/FIRMS — see data_router.py) for questions those can genuinely
    answer, then fall back to search -> ground an LLM answer in the
    results -> return a structured answer (one or more candidate places)
    for the frontend to geocode and draw."""
    live_answer = data_router.try_answer_from_live_data(question)
    if live_answer is not None:
        live_answer["search_results_used"] = 0
        return live_answer

    search_query = f"{question} {region_context}" if region_context else question
    results = await search_web(search_query)
    answer = _synthesize_answer(question, region_context, results)
    answer["search_results_used"] = len(results)
    return answer
