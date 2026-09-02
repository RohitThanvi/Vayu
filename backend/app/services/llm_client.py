import json
import logging
import re
import datetime
from typing import Optional

from groq import Groq
from dotenv import load_dotenv

from ..schemas import StructuredQuery
from ..core.config import settings

load_dotenv()
logger = logging.getLogger(__name__)

_client: Optional[Groq] = None

def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = settings.GROQ_API_KEY
        if not api_key:
            raise EnvironmentError("GROQ_API_KEY is not configured.")
        _client = Groq(api_key=api_key)
        logger.info("Groq client initialized.")
    return _client


_PARSE_SYSTEM = """\
You are a geospatial query parser. Extract structured fields from user questions.
Return ONLY a valid JSON object — no explanation, no markdown, no code fences.

JSON schema:
{
  "in_scope": true or false,
  "metric": one of ["vegetation_change","builtup_change","water_change","flood_detection","fire_detection","drought_index","land_surface_temperature","deforestation","soil_moisture"] or null,
  "region": string or null,
  "start_date": "YYYY-MM-DD" or null,
  "end_date": "YYYY-MM-DD" or null
}

Rules:
- "in_scope" is true ONLY if the question is genuinely asking to MEASURE
  CHANGE OVER TIME in one of the nine metrics below via satellite data —
  even if worded informally, vaguely, or with imperfect English, as long
  as the underlying intent clearly matches one of them.
- "in_scope" is false for anything else — site/location RECOMMENDATIONS
  ("where should we put a new mobile tower", "best location for a new
  warehouse", "where is underserved by X"), general research questions,
  questions about things this system has no dataset for, or anything
  that isn't a "how much did X change" measurement question. When false,
  set "metric" to null — do NOT force a guess at one of the nine metrics
  just because the question is geospatial in nature.
- Do not default to a metric when the question doesn't clearly match one
  — an unclear question that's actually about change-over-time should
  still map to its closest metric (in_scope true); an unclear question
  that ISN'T about change-over-time at all should be in_scope false, not
  forced into "vegetation_change" as a fallback guess.
- If no start year mentioned (and in_scope is true), use 5 years before today.
- If no end date, use today.
- "deforestation" for tree/forest loss queries.
- "drought_index" for drought, dry, water stress queries.
- "land_surface_temperature" for heat, temperature, urban heat island.
- "flood_detection" for flooding, inundation.
- "fire_detection" for fire, burn, wildfire.
- "soil_moisture" for soil, moisture, agriculture stress.
- "vegetation_change" for green cover, NDVI, plants.
- "builtup_change" for buildings, urban, construction.
- "water_change" for lakes, rivers, water bodies.
- If a [Metric: X] prefix is present in the query, use that as the metric and set in_scope true.

Today is {TODAY}.

Examples:
Input: "how much green cover did this area lose since 2020"
Output: {"in_scope": true, "metric": "vegetation_change", "region": null, "start_date": "2020-01-01", "end_date": "{TODAY}"}

Input: "[Metric: deforestation] how much deforestation has happened in this region over 5 years"
Output: {"in_scope": true, "metric": "deforestation", "region": null, "start_date": "{FIVE_YEARS_AGO}", "end_date": "{TODAY}"}

Input: "where should we add a new mobile tower near jaipur"
Output: {"in_scope": false, "metric": null, "region": "Jaipur", "start_date": null, "end_date": null}

Input: "best area for a new warehouse close to this AOI"
Output: {"in_scope": false, "metric": null, "region": null, "start_date": null, "end_date": null}
"""

_SUMMARY_SYSTEM = """\
You are a geospatial analyst writing concise findings for a dashboard.
Write exactly ONE sentence (max 25 words). State the primary numeric finding.
Round numbers to one decimal place. Be direct and factual.
Return only the sentence, no extra text.
"""

_INSIGHT_SYSTEM = """\
You are an expert environmental scientist writing a 2-3 sentence analysis for a geospatial dashboard.
Given analysis results, explain: (1) what the data shows, (2) likely causes, (3) recommended actions.
Be specific, scientific, and actionable. Max 60 words total.
Return only the analysis text, no extra formatting.
"""


def _extract_json(text: str) -> dict:
    """Extract JSON from text, handling markdown code fences and extra text."""
    # Remove markdown fences if present
    text = re.sub(r'```(?:json)?\s*', '', text).strip()
    text = re.sub(r'```\s*$', '', text).strip()
    
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to find JSON object within text
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    
    raise ValueError(f"Could not extract JSON from response: {text[:200]}")


def parse_natural_language_query(text: str) -> StructuredQuery:
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    five_years_ago = (datetime.datetime.utcnow() - datetime.timedelta(days=5*365)).strftime("%Y-%m-%d")
    
    system = _PARSE_SYSTEM.replace("{TODAY}", today).replace("{FIVE_YEARS_AGO}", five_years_ago)

    client = _get_client()
    logger.info(f"LLM: parsing '{text[:80]}'")

    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=500,
            reasoning_effort="low",
        )
        raw = resp.choices[0].message.content
        logger.info(f"LLM raw response: {raw}")
        parsed = _extract_json(raw)

        # Inject defaults for missing dates — only meaningful for in-scope
        # (metric) queries; an out-of-scope query genuinely has no date
        # range to default, so leave those as None rather than injecting
        # a misleading 5-years-to-today window onto a question that was
        # never about a time-series measurement in the first place.
        if parsed.get("in_scope", True):
            if not parsed.get("start_date"):
                parsed["start_date"] = five_years_ago
            if not parsed.get("end_date"):
                parsed["end_date"] = today
        else:
            parsed.setdefault("start_date", today)
            parsed.setdefault("end_date", today)

        return StructuredQuery(**parsed)

    except Exception as e:
        logger.error(f"LLM parse error: {e}")
        raise


def generate_summary(query: StructuredQuery, metrics: dict) -> str:
    client = _get_client()
    payload = {
        "metric": query.metric,
        "region": query.region,
        "metrics": metrics,
        "start_date": query.start_date,
        "end_date": query.end_date,
    }
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.1,
            max_tokens=200,
            reasoning_effort="low",
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Summary generation failed: {e}")
        val = next(iter(metrics.values()), 0)
        return f"Detected {val:.1f} km² of change in {query.region or 'selected area'}."


def generate_insight(query: StructuredQuery, metrics: dict) -> Optional[str]:
    client = _get_client()
    payload = {
        "metric": query.metric,
        "region": query.region,
        "metrics": metrics,
        "period": f"{query.start_date} to {query.end_date}",
    }
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": _INSIGHT_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.3,
            max_tokens=350,
            reasoning_effort="low",
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Insight generation failed: {e}")
        return None


def get_llm_synthesis(context: dict) -> Optional[str]:
    """Optional narrative synthesis for a report, grounded strictly in
    numbers already computed elsewhere. The model is explicitly forbidden
    from introducing any figure not present in the context — this is a
    prose synthesis layer on top of deterministic results, not a source of
    new numbers. Returns None (silently) if unavailable/fails, since this
    is additive polish, not something a report should ever depend on."""
    try:
        client = _get_client()
    except EnvironmentError:
        return None

    system = (
        "You are writing the narrative synthesis section of a professional satellite remote-sensing "
        "analysis report. Using ONLY the data in the JSON context, write 3-4 well-developed paragraphs "
        "(roughly 250-400 words total) covering, in order: (1) the headline finding and what's driving "
        "it, (2) a closer look at the specific metrics or sub-scores that support that finding, "
        "individually, (3) any historical, seasonal, or regional environmental context provided (e.g. "
        "groundwater, rainfall, or temperature readings) and how it supports or complicates the picture "
        "\u2014 if such context is present in the JSON, address it explicitly rather than omitting it, and "
        "make clear it is contextual and not part of the composite score if the context says so, and "
        "(4) the practical implication for someone deciding whether to act on this assessment. Never "
        "invent, estimate, or state a number that isn't present in the context. If the context includes "
        "'deterministic_findings' and/or 'stated_limitations' text, that is the report's own, "
        "already-reviewed framing of what this data does and doesn't establish \u2014 stay within its level "
        "of certainty rather than asserting something more definite. A satellite classifier detecting a "
        "change in land-cover *category* between two dates is not the same as confirming the real-world "
        "event that category change usually reflects (e.g. a 'built' classification change is not itself "
        "confirmed construction, a burned-area detection is not itself a confirmed cause); use hedged "
        "language ('consistent with', 'may indicate', 'is worth verifying against') for causal or "
        "real-world claims the data doesn't directly establish, the same way the deterministic findings "
        "text does, rather than stating them as settled fact. Use the dataset's own terminology for what "
        "it measures rather than substituting a different technical term that sounds similar but means "
        "something else (e.g. a land-cover classifier's 'built' class is not the same measurement as "
        "'impervious surface', even though the two often overlap in practice \u2014 name the actual class "
        "or metric the context gives you). Write connected analytical prose, not a list of restated "
        "list of restated numbers. Formal, measured, non-alarmist tone, as a careful analyst would write "
        "for a reader making a real decision. No markdown, no bullet points, no headers \u2014 plain prose "
        "only, paragraphs separated by a blank line."
    )
    try:
        resp = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context)},
            ],
            temperature=0.3,
            max_tokens=1400,
            reasoning_effort="medium",
        )
        text = resp.choices[0].message.content.strip()
        return text or None
    except Exception as e:
        logger.warning(f"LLM report synthesis failed, continuing without it: {e}")
        return None
