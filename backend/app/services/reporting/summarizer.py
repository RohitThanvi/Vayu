"""
summarizer.py — feeds the aggregator's raw snapshot to the LLM (reuses
the existing Groq client from llm_client.py — same provider/auth this
project already depends on, no new API key needed) and gets back a
structured executive narrative: a headline, a few section summaries,
and a short list of the things most worth an executive's attention.

Deliberately summarized ONCE per report cycle, not once per subscriber
— the report content is identical for every recipient (this is a
market/business intelligence digest, not personalized per-user), so
generating it once and reusing it for every send is both cheaper and
faster, and avoids subtle inconsistencies between subscribers' reports
that would come from independent LLM calls on the same data.

Falls back to a deterministic, template-built summary (no narrative
prose, just the numbers) if the LLM call fails — a subscriber should
still get SOME report on a bad LLM day, not silently receive nothing.
"""

import json
import logging
from typing import Any, Dict

from ..llm_client import _get_client, _extract_json

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """\
You are writing the narrative sections of a daily executive intelligence \
briefing for Vayu, a geospatial/business intelligence platform. You will be \
given a JSON snapshot of live data: global commodity prices, maritime \
chokepoint traffic status, aviation activity, air quality, global hazard/ \
conflict event feeds (USGS earthquakes, NASA FIRMS fires, GDELT/ACLED news \
events), and any agricultural risk watchlist regions.

Write in the voice of a market/intelligence analyst — direct, factual, no \
hedging filler, no "as an AI" framing, no advice disclaimers. Never invent \
numbers not present in the data; if a section has no data, say so plainly \
rather than fabricating detail.

Return ONLY valid JSON, no markdown fences, no text outside the object:
{
  "headline": string,           // one sentence, the single most notable thing today
  "market_summary": string,     // 2-3 sentences on commodity moves worth attention
  "supply_chain_summary": string, // 2-3 sentences on chokepoint/maritime status
  "hazard_summary": string,     // 2-3 sentences on notable global events (quakes/fires/conflict)
  "agri_summary": string,       // 2-3 sentences on watchlist regions, or "No regions on the watchlist yet." if empty
  "key_watchpoints": [string, string, string]   // up to 3 short bullet-style items worth watching tomorrow
}
"""


def _fallback_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    movers = sorted(
        [c for c in snapshot.get("commodities", []) if c.get("change_pct") is not None],
        key=lambda c: -abs(c["change_pct"]),
    )[:3]
    market = ("Largest moves: " + ", ".join(f"{c['name']} {c['change_pct']:+.1f}%" for c in movers) + ".") if movers else "No commodity data available today."

    disrupted = [c for c in snapshot.get("supply_chain", {}).get("chokepoints", []) if c["status"] in ("elevated", "disrupted")]
    supply = (", ".join(f"{c['name']} ({c['status']})" for c in disrupted) + " showing unusual traffic.") if disrupted else "All monitored chokepoints within normal range."

    return {
        "headline": "Daily data snapshot (auto-generated fallback — narrative summary unavailable today).",
        "market_summary": market,
        "supply_chain_summary": supply,
        "hazard_summary": f"{snapshot.get('intel_stats', {}).get('current_events', 0)} active global events being tracked.",
        "agri_summary": f"{len(snapshot.get('agri_watchlist', []))} region(s) on the watchlist." if snapshot.get("agri_watchlist") else "No regions on the watchlist yet.",
        "key_watchpoints": [],
    }


def generate_summary(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": json.dumps(snapshot, default=str)[:12000]},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        parsed = _extract_json(resp.choices[0].message.content)
        fallback = _fallback_summary(snapshot)
        return {**fallback, **{k: v for k, v in parsed.items() if v}}
    except Exception as e:
        logger.warning(f"summarizer: LLM call failed, using fallback: {e}")
        return _fallback_summary(snapshot)
