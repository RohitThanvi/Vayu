"""
research_agent.py — answers open-ended questions Vayu has no fixed dataset
for ("where should we add new mobile towers near Jaipur?", "what's the
latest on port congestion at Mundra?") by researching the live web.

v2 pipeline (still a plain async function chain — no agent framework, same
reasoning as before: cold starts on Render's free tier matter, and a linear
pipeline is easy to debug):

    resolve region  ->  live-data shortcut  ->  PLAN (LLM: 2-4 diverse queries)
      ->  SEARCH (parallel, cached, retried, Wikipedia fallback)
      ->  RANK + dedupe  ->  FETCH top pages  ->  pull the most relevant passages
      ->  SYNTHESIZE (LLM, cited, always returns an answer + known gaps)
      ->  GROUND-CHECK places against the retrieved text
      ->  if the answer is weak: REFLECT (follow-up queries) and go round once more

What v1 got wrong, and what this fixes:
  * one raw question -> one Google query -> 6 *snippets*. Now: several
    differently-phrased queries, full-page fetch, passage-level retrieval.
  * the drawn AOI was ignored, so "near this area" had no location at all.
    Now the AOI centre is reverse-geocoded into a region name.
  * the answer shape was "a named place or nothing". Fact/news questions
    "failed" by design. Now there is always a text `answer`; places are optional.
  * reasoning tokens of gpt-oss share max_tokens (900) -> truncated/empty JSON
    -> a generic "internal error". Now: low reasoning effort, bigger budget,
    one retry, empty-content handling, and a no-LLM extractive fallback.
  * SerpApi failure (quota/key/timeout) = dead end. Now: retry + cache +
    Wikipedia fallback, and `diagnostics` says exactly what happened.
  * the LLM could name a place that no snippet mentioned. Now: places not
    found in the retrieved text are dropped.

Needs SERPAPI_KEY for general web search (free tier: 100/month, so results are
cached for 6h). Without it the agent still works in a degraded mode via
Wikipedia and says so in `diagnostics`.
"""

import asyncio
import datetime
import logging
import math
import os
import re
import time
from html.parser import HTMLParser
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .llm_client import _get_client, _extract_json
from . import data_router
from . import geocode_util
from . import siting_agent
from . import marine_agent

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
WIKI_API = "https://en.wikipedia.org/w/api.php"
MODEL = "openai/gpt-oss-120b"

TOTAL_BUDGET_S = 45          # cap for the web-research phase
SITING_BUDGET_S = 100        # cap for the satellite/OSM evidence phase (runs only for siting/assessment questions)
MAX_ROUNDS = 2               # plan->retrieve->synthesize, plus one reflective retry
RESULTS_PER_QUERY = 8
MAX_PAGES_PER_ROUND = 6
MAX_SOURCES_IN_PROMPT = 10
PAGE_TIMEOUT_S = 8
MAX_PAGE_BYTES = 1_500_000
PASSAGE_CHARS = 700
PASSAGES_PER_SOURCE = 3
CACHE_TTL_S = 6 * 3600

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VAYU-Research-Agent/2.0)"}

_STOP = set(
    "the and for are was were with that this from have has had not but you your our their its into "
    "about what where when which who whom how why can could should would will shall may might "
    "near around best top any all some more most than then there here also just over under "
    "area areas place places new add adding".split()
)


# ── tiny TTL cache ────────────────────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}


def _cache_get(key: str):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key: str, value: Any):
    if len(_cache) > 500:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), value)


# ── search ────────────────────────────────────────────────────────────────────
async def _search_serpapi(query: str, num_results: int) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Returns (results, error_reason). Retries once on transient failure."""
    if not SERPAPI_KEY:
        return [], "SERPAPI_KEY not configured"
    params = {"q": query, "api_key": SERPAPI_KEY, "engine": "google", "num": num_results}
    last = "unknown"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(headers=_HEADERS) as client:
                resp = await client.get(SERPAPI_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            if data.get("error"):
                # HTTP 200 + "error" = exhausted quota / bad key. Not worth retrying.
                return [], f"SerpApi error: {data['error']}"
            out = []
            for r in (data.get("organic_results") or [])[:num_results]:
                title, url, snippet = r.get("title"), r.get("link"), r.get("snippet")
                if title and url:
                    out.append({"title": title, "url": url, "snippet": snippet or "", "provider": "serpapi"})
            return out, (None if out else "SerpApi returned zero organic results")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(0.6 * (attempt + 1))
    return [], f"SerpApi request failed ({last})"


async def _search_wikipedia(query: str, num_results: int) -> List[Dict[str, str]]:
    """Free, keyless fallback — good for factual/background questions, weak for
    local-siting or breaking news, which is why it is only the fallback."""
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=10) as client:
            resp = await client.get(WIKI_API, params={
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": min(num_results, 6), "utf8": 1,
            })
            resp.raise_for_status()
            hits = resp.json().get("query", {}).get("search", [])
    except Exception as e:
        logger.warning(f"Research agent: Wikipedia fallback failed: {type(e).__name__}: {e}")
        return []
    out = []
    for h in hits:
        title = h.get("title")
        if not title:
            continue
        snippet = re.sub(r"<[^>]+>", "", h.get("snippet", ""))
        out.append({
            "title": f"{title} — Wikipedia",
            "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "snippet": snippet, "provider": "wikipedia",
        })
    return out


async def search_web(query: str, num_results: int = RESULTS_PER_QUERY, diag: Optional[dict] = None) -> List[Dict[str, str]]:
    """Cached, retried search with a keyless fallback. Never raises; the reason
    for any degradation is recorded in `diag` instead of vanishing."""
    key = f"{num_results}|{query.strip().lower()}"
    cached = _cache_get(key)
    if cached is not None:
        if diag is not None:
            diag.setdefault("cache_hits", 0)
            diag["cache_hits"] += 1
        return cached

    results, err = await _search_serpapi(query, num_results)
    if err:
        logger.warning(f"Research agent: primary search problem for {query!r}: {err}")
        if diag is not None:
            diag.setdefault("search_errors", [])
            if err not in diag["search_errors"]:
                diag["search_errors"].append(err)
    if not results:
        results = await _search_wikipedia(query, num_results)
    if results:
        _cache_put(key, results)
    return results


# ── page fetch + passage retrieval ────────────────────────────────────────────
class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe", "template"}
    _BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def _html_to_text(html_text: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html_text)
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


async def _fetch_page_text(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        resp = await client.get(url, timeout=PAGE_TIMEOUT_S, follow_redirects=True)
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code != 200 or ("html" not in ctype and "text" not in ctype):
            return None
        raw = resp.content[:MAX_PAGE_BYTES]
        text = _html_to_text(raw.decode(resp.encoding or "utf-8", errors="ignore"))
        return text if len(text) > 200 else None
    except Exception as e:
        logger.info(f"Research agent: page fetch failed for {url}: {type(e).__name__}")
        return None


def _tokens(s: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in _STOP]


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _best_passages(text: str, q_tokens: set, k: int = PASSAGES_PER_SOURCE, size: int = PASSAGE_CHARS) -> str:
    """Query-focused extraction: split into ~size-char windows of whole
    paragraphs, keep the k that best match the question terms (in document order)."""
    paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    windows, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            windows.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        windows.append(cur)
    if not windows:
        return text[:size]
    scored = []
    for i, w in enumerate(windows):
        wt = set(_tokens(w))
        overlap = len(wt & q_tokens)
        scored.append((overlap / math.sqrt(len(wt) + 1), i, w))
    top = sorted(scored, reverse=True)[:k]
    return "\n…\n".join(w for _, _, w in sorted(top, key=lambda x: x[1]))


def _rank_results(results: List[Dict[str, str]], question: str, region: Optional[str], limit: int) -> List[Dict[str, str]]:
    q_tokens = set(_tokens(question))
    r_tokens = set(_tokens(region or ""))
    scored = []
    for idx, r in enumerate(results):
        title_t, snip_t = set(_tokens(r["title"])), set(_tokens(r.get("snippet", "")))
        score = 2.0 * len(title_t & q_tokens) + 1.0 * len(snip_t & q_tokens)
        if r_tokens and (r_tokens & (title_t | snip_t)):
            score += 1.5
        score += 1.0 / (1 + idx)
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    out, per_domain = [], {}
    for _, r in scored:
        d = _domain(r["url"])
        if per_domain.get(d, 0) >= 2:
            continue
        per_domain[d] = per_domain.get(d, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


async def _gather_evidence(queries: List[str], question: str, region: Optional[str],
                           seen_urls: set, diag: dict) -> List[Dict[str, str]]:
    lists = await asyncio.gather(*[search_web(q, diag=diag) for q in queries], return_exceptions=True)
    merged: Dict[str, Dict[str, str]] = {}
    for lst in lists:
        if isinstance(lst, Exception):
            continue
        for r in lst:
            if r["url"] not in seen_urls and r["url"] not in merged:
                merged[r["url"]] = r
    for r in merged.values():
        diag["search_providers"][r.get("provider", "?")] = diag["search_providers"].get(r.get("provider", "?"), 0) + 1
    if not merged:
        return []

    top = _rank_results(list(merged.values()), question, region, MAX_PAGES_PER_ROUND)
    q_tokens = set(_tokens(question)) | set(_tokens(region or ""))
    async with httpx.AsyncClient(headers=_HEADERS) as client:
        pages = await asyncio.gather(*[_fetch_page_text(client, r["url"]) for r in top], return_exceptions=True)

    sources = []
    for r, page in zip(top, pages):
        seen_urls.add(r["url"])
        text = page if isinstance(page, str) else None
        if text:
            diag["pages_fetched"] += 1
            excerpt = _best_passages(text, q_tokens)
        else:
            diag["pages_failed"] += 1
            excerpt = r.get("snippet", "")
        sources.append({"title": r["title"], "url": r["url"], "snippet": r.get("snippet", ""), "excerpt": excerpt})
    return sources


# ── LLM helpers ───────────────────────────────────────────────────────────────
def _llm_json_sync(system: str, user: str, max_tokens: int) -> dict:
    """gpt-oss counts reasoning tokens against max_tokens, so a tight budget can
    return an empty/truncated message. Low reasoning effort + generous budget +
    one retry at double the budget."""
    client = _get_client()
    last_err: Exception = ValueError("no attempt made")
    for budget in (max_tokens, max_tokens * 2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.2, max_tokens=budget, reasoning_effort="low",
            )
            choice = resp.choices[0]
            content = choice.message.content
            if not content or not content.strip():
                last_err = ValueError(f"empty completion (finish_reason={getattr(choice, 'finish_reason', '?')})")
                continue
            return _extract_json(content)
        except Exception as e:
            last_err = e
            logger.warning(f"Research agent: LLM call failed (budget={budget}): {type(e).__name__}: {e}")
    raise last_err


async def _llm_json(system: str, user: str, max_tokens: int) -> dict:
    return await asyncio.to_thread(_llm_json_sync, system, user, max_tokens)


PLANNER_SYSTEM = """\
You plan web research for a geospatial intelligence tool. Given a question and a
region, produce search queries that will surface the BEST evidence.

Return ONLY JSON: {"question_type": "siting"|"assessment"|"fact"|"news"|"comparison"|"other",
 "wants_places": true|false,
 "use_case": "solar_farm"|"mobile_tower"|"warehouse_logistics"|"cold_storage"|"ev_charging"|"none",
 "queries": [string, ...]}

Rules:
- 3 to 4 queries, each phrased DIFFERENTLY (different vocabulary, angle, or source type) —
  outlets use different terminology, so cover synonyms and related concepts, not rewordings.
- Put the place name in queries where location matters. Keep each query under 12 words.
- For siting/"where should" questions, include queries for the decision factors
  (demand, existing coverage/supply, planned development, regulations) and for named
  localities, not just the generic question.
- For news/"latest" questions, include the current year.
- "wants_places" is true only if the user is asking WHERE (locations to consider/mark on a map).
- "siting" = choosing between locations; "assessment" = judging ONE given area ("is this area suitable for...").
- "use_case" is one of the listed site types ONLY when the question is about siting/assessing that kind of
  facility; otherwise "none".
"""

SYNTH_SYSTEM = """\
You are a careful geospatial research analyst. You are given a question, a region
and numbered sources (excerpts of web pages). Answer using ONLY these sources.

Return ONLY JSON:
{
 "answer": string,            // direct answer, 2-6 sentences, in your own words
 "key_points": [{"point": string, "sources": [int, ...]}],
 "places": [{"place_name": string, "reasoning": string, "radius_km": number,
             "confidence": "low"|"medium"|"high", "sources": [int, ...]}],
 "gaps": [string, ...],       // what the sources did NOT establish
 "confidence": "low"|"medium"|"high"
}

Rules:
- Never invent facts or place names. Every key point and place must cite source numbers.
- If the sources only partly answer the question, answer the part they support and list the
  rest in "gaps". A partial, honest answer is better than an empty one.
- "places": only when the question asks where; each must be a specific named locality,
  landmark or sub-area (not the whole city already given as context) that appears in the
  sources. radius_km: 1-2 for a specific site, 2-5 for a neighbourhood. Order best-supported first.
- Do not quote sources verbatim; paraphrase. Prefer recent information and say if a source looks old.
- If sources disagree, say so in the answer and lower confidence.
"""


def _fallback_queries(question: str, region: Optional[str]) -> List[str]:
    year = datetime.datetime.utcnow().year
    q = question.strip()
    base = q if (region and region.lower() in q.lower()) else f"{q} {region or ''}".strip()
    kw = " ".join(_tokens(q)[:7])
    out = [base]
    if kw:
        out.append(f"{kw} {region or ''}".strip())
    out.append(f"{kw or q} {region or ''} {year}".strip())
    seen, uniq = set(), []
    for s in out:
        if s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
    return uniq


async def _plan(question: str, region: Optional[str], center: Optional[Tuple[float, float]], diag: dict) -> dict:
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    user = f"Today: {today}\nRegion: {region or 'not specified'}\nQuestion: {question}"
    try:
        plan = await _llm_json(PLANNER_SYSTEM, user, max_tokens=1200)
        queries = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()][:4]
        if not queries:
            raise ValueError("planner returned no queries")
        uc = plan.get("use_case")
        if uc not in siting_agent.PROFILES:
            uc = siting_agent.guess_use_case(question)
        return {"question_type": plan.get("question_type", "other"),
                "wants_places": bool(plan.get("wants_places")), "use_case": uc, "queries": queries}
    except Exception as e:
        diag["notes"].append(f"planner fallback ({type(e).__name__})")
        wants = bool(re.search(r"\b(where|which (area|areas|locality|localities|neighbou?rhoods?)|best (place|location|site)s?)\b", question.lower()))
        return {"question_type": "siting" if wants else "other", "wants_places": wants,
                "use_case": siting_agent.guess_use_case(question), "queries": _fallback_queries(question, region)}


async def _followup_queries(question: str, region: Optional[str], tried: List[str], gaps: List[str]) -> List[str]:
    user = (f"Region: {region or 'not specified'}\nQuestion: {question}\n"
            f"Queries already tried: {tried}\nWhat was still missing: {gaps or ['a specific, well-supported answer']}\n"
            "Write 3 NEW queries that approach the missing evidence from different angles "
            "(different vocabulary, narrower sub-topics, or other source types).")
    try:
        plan = await _llm_json(PLANNER_SYSTEM, user, max_tokens=1000)
        new = [str(q).strip() for q in (plan.get("queries") or []) if str(q).strip()]
        tried_l = {t.lower() for t in tried}
        return [q for q in new if q.lower() not in tried_l][:3]
    except Exception:
        # No LLM: widen by dropping the region qualifier / adding the gap text as a query.
        extra = [f"{g} {region or ''}".strip() for g in gaps[:2]]
        extra.append(" ".join(_tokens(question)[:8]))
        tried_l = {t.lower() for t in tried}
        return [q for q in extra if q and q.lower() not in tried_l][:3]


# ── synthesis + grounding ─────────────────────────────────────────────────────
def _source_block(sources: List[Dict[str, str]]) -> str:
    return "\n\n".join(
        f"[{i + 1}] {s['title']} ({_domain(s['url'])})\n{s['excerpt']}"
        for i, s in enumerate(sources[:MAX_SOURCES_IN_PROMPT])
    )


def _clean_ids(ids: Any, n: int) -> List[int]:
    out = []
    for x in ids if isinstance(ids, list) else []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= n and i not in out:
            out.append(i)
    return out


_GENERIC_PLACE_WORDS = {"road", "rd", "nagar", "area", "sector", "colony", "city", "district", "street", "market", "near", "the"}


def _place_is_grounded(place_name: str, corpus_lower: str) -> bool:
    name = place_name.lower().strip()
    if name and name in corpus_lower:
        return True
    toks = [t for t in re.findall(r"[a-z0-9]+", name) if len(t) > 3 and t not in _GENERIC_PLACE_WORDS]
    return bool(toks) and all(t in corpus_lower for t in toks)


def _extractive_fallback(sources: List[Dict[str, str]]) -> dict:
    """LLM unavailable: still hand back the evidence instead of a dead end."""
    bits = [f"{s['title']}: {s['snippet'] or s['excerpt'][:200]}" for s in sources[:3]]
    return {
        "answer": ("Automatic summarisation was unavailable, so here is the most relevant material found — "
                   + " | ".join(bits)) if bits else "",
        "key_points": [{"point": s["title"], "sources": [i + 1]} for i, s in enumerate(sources[:4])],
        "places": [], "gaps": ["Automatic summarisation failed; review the linked sources directly."],
        "confidence": "low",
    }


async def _synthesize(question: str, region: Optional[str], center: Optional[Tuple[float, float]],
                      plan: dict, sources: List[Dict[str, str]], diag: dict) -> dict:
    used = sources[:MAX_SOURCES_IN_PROMPT]
    loc = f"{region or 'not specified'}" + (f" (centre {center[0]:.4f}, {center[1]:.4f})" if center else "")
    user = (f"Region: {loc}\nQuestion type: {plan.get('question_type')}  |  asks for places: {plan.get('wants_places')}\n"
            f"Question: {question}\n\nSources:\n{_source_block(used)}")
    try:
        raw = await _llm_json(SYNTH_SYSTEM, user, max_tokens=2500)
    except Exception as e:
        diag["notes"].append(f"synthesis fallback ({type(e).__name__})")
        raw = _extractive_fallback(used)

    n = len(used)
    corpus = " ".join(f"{s['title']} {s['snippet']} {s['excerpt']}" for s in used).lower()
    gaps = [str(g) for g in (raw.get("gaps") or []) if str(g).strip()]

    places = []
    for p in raw.get("places") or []:
        if not isinstance(p, dict) or not p.get("place_name"):
            continue
        name = str(p["place_name"])
        if not _place_is_grounded(name, corpus):
            gaps.append(f"Dropped '{name}': it wasn't found in the retrieved sources.")
            continue
        try:
            radius = float(p.get("radius_km") or 2)
        except (TypeError, ValueError):
            radius = 2.0
        places.append({
            "place_name": name, "reasoning": str(p.get("reasoning", "")),
            "radius_km": max(0.5, min(radius, 25.0)),
            "confidence": p.get("confidence") if p.get("confidence") in ("low", "medium", "high") else "low",
            "source_urls": [used[i - 1]["url"] for i in _clean_ids(p.get("sources"), n)],
        })

    key_points = []
    for kp in raw.get("key_points") or []:
        if isinstance(kp, dict) and kp.get("point"):
            key_points.append({"point": str(kp["point"]),
                               "urls": [used[i - 1]["url"] for i in _clean_ids(kp.get("sources"), n)]})

    cited = []
    for item in key_points + places:
        for u in item.get("urls", []) or item.get("source_urls", []):
            if u not in cited:
                cited.append(u)
    if not cited:
        cited = [s["url"] for s in used[:3]]

    # Confidence is capped by independent corroboration: one domain can't earn "high".
    conf = raw.get("confidence") if raw.get("confidence") in ("low", "medium", "high") else "low"
    if len({_domain(u) for u in cited}) < 2 and conf == "high":
        conf = "medium"

    return {"answer": str(raw.get("answer") or "").strip(), "key_points": key_points, "places": places,
            "gaps": gaps, "confidence": conf, "source_urls": cited}


def _good_enough(synth: dict, plan: dict) -> bool:
    if not synth["answer"] or synth["confidence"] == "low":
        return False
    if plan.get("wants_places") and not synth["places"]:
        return False
    return True


# ── region resolution ─────────────────────────────────────────────────────────
def _aoi_center(aoi: Optional[dict]) -> Optional[Tuple[float, float]]:
    if not aoi:
        return None
    pts: List[Tuple[float, float]] = []

    def walk(c):
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(v, (int, float)) for v in c[:2]):
            pts.append((float(c[1]), float(c[0])))   # GeoJSON is lon,lat
        elif isinstance(c, (list, tuple)):
            for x in c:
                walk(x)

    def grab(g):
        if not isinstance(g, dict):
            return
        t = g.get("type")
        if t == "FeatureCollection":
            for f in g.get("features", []):
                grab(f)
        elif t == "Feature":
            grab(g.get("geometry"))
        else:
            walk(g.get("coordinates"))

    grab(aoi)
    if not pts:
        return None
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return ((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2)


async def _resolve_region(region: Optional[str], aoi: Optional[dict], diag: dict) -> Tuple[Optional[str], Optional[Tuple[float, float]]]:
    center = _aoi_center(aoi)
    if region:
        return region, center
    if center:
        try:
            name = await geocode_util.reverse_geocode_name(*center)
            if name:
                diag["notes"].append("region inferred from drawn AOI")
                return name, center
        except Exception as e:
            logger.info(f"Research agent: reverse geocode failed: {e}")
    return None, center


# ── orchestration ─────────────────────────────────────────────────────────────
def _empty_answer(reasoning: str, diag: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "answer": reasoning, "key_points": [], "gaps": [reasoning],
        "places": [], "place_name": None, "reasoning": reasoning, "radius_km": None,
        "confidence": "low", "source_urls": [], "search_results_used": 0, "diagnostics": diag or {},
    }


def _format_result(synth: dict, n_sources: int, diag: dict) -> Dict[str, Any]:
    places = synth["places"]
    first = places[0] if places else {}
    answer = synth["answer"] or "The retrieved sources didn't contain enough to answer this."
    return {
        "answer": answer,
        "key_points": synth["key_points"],
        "gaps": synth["gaps"],
        "places": places,
        # backward-compatible single-place fields, mirroring the best-supported candidate
        "place_name": first.get("place_name"),
        "reasoning": first.get("reasoning") or answer,
        "radius_km": first.get("radius_km"),
        "confidence": synth["confidence"],
        "source_urls": synth["source_urls"],
        "search_results_used": n_sources,
        "diagnostics": diag,
    }


def _progress(cb: Optional[Callable[[str, int], None]], label: str, pct: int) -> None:
    if cb:
        try:
            cb(label, pct)
        except Exception:
            pass


async def _web_phase(question: str, region_context: Optional[str], aoi_geojson: Optional[dict],
                     diag: dict, state: dict, progress) -> None:
    """Resolve region -> plan -> (search -> read -> synthesize -> reflect). Writes
    everything useful into `state` as it goes, so a timeout still leaves partial results."""
    region, center = await _resolve_region(region_context, aoi_geojson, diag)
    state["region"], state["center"] = region, center
    _progress(progress, "Planning the research", 32)
    plan = await _plan(question, region, center, diag)
    state["plan"] = plan
    queries = plan["queries"]
    tried: List[str] = []
    sources: List[Dict[str, str]] = []
    seen: set = set()

    for round_no in range(1, MAX_ROUNDS + 1):
        diag["rounds"] = round_no
        diag["queries"].extend(queries)
        tried.extend(queries)
        _progress(progress, "Searching and reading sources", 36 + 8 * round_no)
        sources.extend(await _gather_evidence(queries, question, region, seen, diag))
        state["sources"] = sources

        if not sources:
            if round_no == 1:
                queries = _fallback_queries(question, region)
                queries = [q for q in queries if q.lower() not in {t.lower() for t in tried}] or [question]
                continue
            return

        _progress(progress, "Synthesizing findings", 46 + 6 * round_no)
        synth = await _synthesize(question, region, center, plan, sources, diag)
        state["synth"] = synth
        state["best"] = _format_result(synth, len(sources), diag)
        if _good_enough(synth, plan) or round_no == MAX_ROUNDS:
            return
        queries = await _followup_queries(question, region, tried, synth["gaps"])
        if not queries:
            return


def _merge_siting(result: Dict[str, Any], siting: Dict[str, Any]) -> Dict[str, Any]:
    """Fold the evidence-backed ranking into the web-research result (keeps every
    field the existing frontend reads)."""
    ranking = siting["ranking"]
    web_places = {p["place_name"]: p for p in result.get("places", [])}
    ordered = []
    for r in ranking:
        base = dict(web_places.get(r["name"], {}))
        base.update({"place_name": r["name"], "radius_km": r["radius_km"], "lat": r["lat"], "lon": r["lon"],
                     "score": r["score"], "rank": r["rank"], "evidence_coverage": r["evidence_coverage"],
                     "evidence_level": r["evidence_level"]})
        base.setdefault("reasoning", r.get("web_reasoning", ""))
        base.setdefault("confidence", "medium" if r["evidence_level"] == "strong" else "low")
        ordered.append(base)
    measured = {r["name"] for r in ranking}
    ordered.extend(p for p in result.get("places", []) if p["place_name"] not in measured)

    result["web_summary"] = result.get("answer", "")
    result["answer"] = siting["recommendation"]
    result["places"] = ordered
    first = ordered[0] if ordered else {}
    result["place_name"] = first.get("place_name")
    result["radius_km"] = first.get("radius_km")
    result["reasoning"] = siting["recommendation"]
    result["ranking"] = ranking
    result["use_case"] = siting["use_case_label"]
    result["tradeoffs"] = siting["tradeoffs"]
    result["conflicts"] = siting["conflicts"]
    result["next_steps"] = siting["next_steps"]
    result["siting_caveats"] = siting["caveats"]
    result["methodology"] = siting["methodology"]
    return result


async def _ask_inner(question: str, region_context: Optional[str], aoi_geojson: Optional[dict],
                     diag: dict, state: dict, progress, history: Optional[List[dict]] = None,
                     user_position: Optional[dict] = None) -> Dict[str, Any]:
    live = await data_router.try_answer_from_live_data(question)
    if live is not None:
        live.setdefault("answer", live.get("reasoning"))
        live.setdefault("key_points", [])
        live.setdefault("gaps", [])
        live["search_results_used"] = 0
        live["diagnostics"] = diag
        return live

    # Sea questions ("is it safe to fish near Porbandar tomorrow?") go to the marine agent: live
    # forecast data + a deterministic safety screen, in the user's language. It returns None if
    # its planner decides the question isn't actually about the sea, and we carry on normally.
    if marine_agent.looks_marine(question, history):
        marine = await marine_agent.answer(question, history=history, region_hint=region_context, aoi=aoi_geojson,
                                           user_position=user_position, llm=_llm_json, progress=progress)
        if marine is not None:
            marine["diagnostics"] = {**diag, **(marine.get("diagnostics") or {})}
            return marine

    timed_out = False
    try:
        await asyncio.wait_for(_web_phase(question, region_context, aoi_geojson, diag, state, progress), TOTAL_BUDGET_S)
    except asyncio.TimeoutError:
        timed_out = True
        diag["notes"].append("web phase hit its time budget")

    synth = state.get("synth")
    if synth is None:
        if timed_out:
            return _empty_answer("Research took too long to finish; try a narrower question.", diag)
        reasons = diag.get("search_errors") or ["no usable search results"]
        return _empty_answer("Couldn't retrieve any web sources for this question (" + "; ".join(reasons) + ").", diag)

    result = state["best"]
    if timed_out:
        result["gaps"] = list(result.get("gaps", [])) + ["Web research hit its time limit; this is the best answer found so far."]

    # ── combined stage: measure the candidates with Vayu's own data ───────────
    plan = state.get("plan", {})
    use_case = plan.get("use_case")
    qtype = plan.get("question_type")
    if use_case in siting_agent.PROFILES and qtype in ("siting", "assessment") and not timed_out:
        assess_aoi = aoi_geojson if (qtype == "assessment" and aoi_geojson) else None
        places = [] if assess_aoi else synth["places"]
        if assess_aoi or places:
            try:
                siting = await asyncio.wait_for(
                    siting_agent.evaluate_candidates(
                        question=question, region=state.get("region"), region_center=state.get("center"),
                        places=places, aoi=assess_aoi, use_case=use_case, llm=_llm_json, progress=progress,
                        web_summary=synth["answer"], key_points=synth["key_points"], diag=diag),
                    SITING_BUDGET_S)
                if siting:
                    result = _merge_siting(result, siting)
                else:
                    diag["notes"].append("siting stage produced no result")
            except asyncio.TimeoutError:
                diag["notes"].append("siting stage hit its time budget")
                result["gaps"] = list(result.get("gaps", [])) + ["Satellite/OSM measurement timed out; places below are web-sourced only and unmeasured."]
            except Exception as e:
                logger.error(f"Siting stage failed: {type(e).__name__}: {e}", exc_info=True)
                diag["notes"].append(f"siting stage error: {type(e).__name__}")
                result["gaps"] = list(result.get("gaps", [])) + ["Satellite/OSM measurement failed; places below are web-sourced only and unmeasured."]
    return result


async def ask(question: str, region_context: Optional[str] = None, aoi_geojson: Optional[dict] = None,
              progress: Optional[Callable[[str, int], None]] = None, history: Optional[List[dict]] = None,
              user_position: Optional[dict] = None) -> Dict[str, Any]:
    """Never raises. Always returns an answer dict (possibly low-confidence, with
    `gaps` and `diagnostics` explaining what was and wasn't found). `progress(label, pct)`
    is called as stages complete so the UI can show what the agent is doing."""
    t0 = time.monotonic()
    diag: Dict[str, Any] = {"rounds": 0, "queries": [], "search_providers": {}, "pages_fetched": 0,
                            "pages_failed": 0, "notes": []}
    state: Dict[str, Any] = {"best": None}
    try:
        result = await asyncio.wait_for(
            _ask_inner(question, region_context, aoi_geojson, diag, state, progress, history, user_position),
            TOTAL_BUDGET_S + SITING_BUDGET_S + 10)
    except asyncio.TimeoutError:
        diag["notes"].append("overall time budget exceeded")
        result = state["best"] or _empty_answer("Research took too long to finish; try a narrower question.", diag)
    except Exception as e:
        logger.error(f"Research agent crashed: {type(e).__name__}: {e}", exc_info=True)
        diag["notes"].append(f"crash: {type(e).__name__}")
        result = state["best"] or _empty_answer(f"The research agent hit an internal error ({type(e).__name__}).", diag)
    diag["elapsed_s"] = round(time.monotonic() - t0, 1)
    logger.info(f"Research agent done: {diag}")
    return result
