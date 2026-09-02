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

Web search source: public SearXNG instances. This is a real, accepted
production tradeoff, not a naive choice -- most public instances disable
JSON output by default (operators do this deliberately, since JSON/CSV
access is a much cheaper way to scrape an instance at scale than
rendering full HTML, and plenty of operators want to discourage exactly
that), and there's no single public instance guaranteed to keep
supporting it. Mitigated by probing searx.space's live-checked instance
list at runtime rather than hardcoding a guess, verifying the actual
Content-Type of a live response before trusting it (not a claimed
capability), and caching whichever instance is confirmed working so
normal requests don't re-probe every time. If every candidate instance
fails, this returns an empty result rather than crashing -- the calling
endpoint then reports "search unavailable" rather than fabricating an
answer.
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from .llm_client import _get_client, _extract_json

logger = logging.getLogger(__name__)

SEARX_SPACE_INSTANCES_URL = "https://searx.space/data/instances.json"
INSTANCE_LIST_TTL = 24 * 60 * 60      # refresh the candidate list once a day
KNOWN_GOOD_TTL = 6 * 60 * 60          # re-probe even a working instance periodically, in case it silently changes settings
MAX_CANDIDATES_TRIED = 40             # cap how many instances one search will ever probe through

_instance_list_cache: List[str] = []
_instance_list_cached_at: float = 0.0
_known_good_instance: Optional[str] = None
_known_good_checked_at: float = 0.0

_HEADERS = {"User-Agent": "VAYU-Research-Agent/1.0"}


async def _get_candidate_instances(client: httpx.AsyncClient) -> List[str]:
    global _instance_list_cache, _instance_list_cached_at
    if _instance_list_cache and (time.time() - _instance_list_cached_at) < INSTANCE_LIST_TTL:
        return _instance_list_cache
    try:
        resp = await client.get(SEARX_SPACE_INSTANCES_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        instances_dict = data.get("instances", data) if isinstance(data, dict) else {}
        urls = [u.rstrip("/") for u in instances_dict.keys() if isinstance(u, str) and u.startswith("http")]
        # Don't try instances in the same order every deployment does — a
        # fixed "first alphabetically" order just concentrates everyone
        # building this same pattern onto the same few instances.
        random.shuffle(urls)
        _instance_list_cache = urls[:MAX_CANDIDATES_TRIED]
        _instance_list_cached_at = time.time()
        logger.info(f"Research agent: refreshed candidate instance list ({len(_instance_list_cache)} instances)")
    except Exception as e:
        logger.warning(f"Research agent: failed to fetch searx.space instance list: {type(e).__name__}: {e}")
        # Keep whatever we had before, even if stale, rather than going to nothing
    return _instance_list_cache


async def _probe_instance(client: httpx.AsyncClient, base_url: str) -> bool:
    """Ground truth check: does a real request to this instance actually
    return JSON? Never trust a claimed capability from a listing —
    verify the live Content-Type instead (see module docstring)."""
    try:
        resp = await client.get(f"{base_url}/search", params={"q": "test", "format": "json"}, timeout=8)
        return resp.status_code == 200 and "application/json" in resp.headers.get("content-type", "")
    except Exception:
        return False


async def _get_working_instance(client: httpx.AsyncClient) -> Optional[str]:
    global _known_good_instance, _known_good_checked_at
    if _known_good_instance and (time.time() - _known_good_checked_at) < KNOWN_GOOD_TTL:
        return _known_good_instance

    candidates = await _get_candidate_instances(client)
    for base_url in candidates:
        if await _probe_instance(client, base_url):
            _known_good_instance = base_url
            _known_good_checked_at = time.time()
            logger.info(f"Research agent: using SearXNG instance {base_url}")
            return base_url

    logger.warning(f"Research agent: none of {len(candidates)} candidate SearXNG instances serve JSON right now")
    _known_good_instance = None
    return None


async def search_web(query: str, num_results: int = 6) -> List[Dict[str, str]]:
    """Search the web via a live-probed public SearXNG instance. Returns
    an empty list (not an exception) on total failure — every candidate
    instance being down/JSON-disabled is a real, expected possibility
    with this free source, not a bug to crash on."""
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        base_url = await _get_working_instance(client)
        if not base_url:
            return []
        try:
            resp = await client.get(f"{base_url}/search", params={"q": query, "format": "json"}, timeout=15)
            if "application/json" not in resp.headers.get("content-type", ""):
                # This instance stopped serving JSON since it was last
                # probed (operators can flip this at any time) — don't
                # trust this response, and force a fresh probe next call.
                global _known_good_instance
                _known_good_instance = None
                return []
            data = resp.json()
        except Exception as e:
            logger.warning(f"Research agent: search failed against {base_url}: {type(e).__name__}: {e}")
            _known_good_instance = None
            return []

    results = []
    for r in (data.get("results") or [])[:num_results]:
        title, url, snippet = r.get("title"), r.get("url"), r.get("content")
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
    """Full pipeline: search -> ground an LLM answer in the results ->
    return a structured answer for the frontend to geocode and draw."""
    search_query = f"{question} {region_context}" if region_context else question
    results = await search_web(search_query)
    answer = _synthesize_answer(question, region_context, results)
    answer["search_results_used"] = len(results)
    return answer
