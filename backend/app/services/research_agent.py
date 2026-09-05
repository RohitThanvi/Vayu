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
result snippets. Your job: identify ONE specific named place (a locality, \
neighborhood, landmark, or precise sub-area — NOT a whole city) that best \
answers the question, grounded ONLY in the provided snippets.

Rules:
- Never invent a place name that isn't supported by the snippets. If the \
snippets don't contain enough specific information to name a real, precise \
place, set "place_name" to null rather than guessing.
- Write "reasoning" entirely in your own words — do not quote source text \
verbatim, even briefly.
- Suggest a radius in kilometers for marking this location on a map, scaled \
to how precise vs. broad the answer is (a single specific site suggestion: \
1-2; a broader named neighborhood/area: 2-5).
- Set "confidence" honestly based on how directly the snippets support the answer.

Return ONLY valid JSON, no markdown fences, no text outside the JSON object:
{
  "place_name": string or null,
  "reasoning": string,
  "radius_km": number,
  "confidence": "low" | "medium" | "high",
  "source_urls": [string, ...]
}
"""


def _synthesize_answer(question: str, region_context: Optional[str], search_results: List[Dict[str, str]]) -> Dict[str, Any]:
    if not search_results:
        return {
            "place_name": None,
            "reasoning": "No web search results were available to ground an answer — the search source was unreachable this time.",
            "radius_km": None,
            "confidence": "low",
            "source_urls": [],
        }

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
            max_tokens=600,
        )
        parsed = _extract_json(resp.choices[0].message.content)
    except Exception as e:
        logger.error(f"Research agent: LLM synthesis error: {e}")
        return {
            "place_name": None,
            "reasoning": f"The research agent hit an internal error while reasoning over search results ({type(e).__name__}).",
            "radius_km": None,
            "confidence": "low",
            "source_urls": [],
        }

    # Defensive defaults — a malformed/partial LLM response shouldn't crash the endpoint
    return {
        "place_name": parsed.get("place_name"),
        "reasoning": parsed.get("reasoning", ""),
        "radius_km": parsed.get("radius_km"),
        "confidence": parsed.get("confidence", "low"),
        "source_urls": parsed.get("source_urls") or [r["url"] for r in search_results[:3]],
    }


async def ask(question: str, region_context: Optional[str] = None) -> Dict[str, Any]:
    """Full pipeline: try Vayu's own live intel feeds first (AIS/ADS-B/
    USGS/FIRMS — see data_router.py) for questions those can genuinely
    answer, then fall back to search -> ground an LLM answer in the
    results -> return a structured answer for the frontend to geocode
    and draw."""
    live_answer = data_router.try_answer_from_live_data(question)
    if live_answer is not None:
        live_answer["search_results_used"] = 0
        return live_answer

    search_query = f"{question} {region_context}" if region_context else question
    results = await search_web(search_query)
    answer = _synthesize_answer(question, region_context, results)
    answer["search_results_used"] = len(results)
    return answer
