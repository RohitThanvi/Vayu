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
  "metric": one of ["vegetation_change","builtup_change","water_change","flood_detection","fire_detection","drought_index","land_surface_temperature","deforestation","soil_moisture"],
  "region": string or null,
  "start_date": "YYYY-MM-DD" or null,
  "end_date": "YYYY-MM-DD" or null
}

Rules:
- If no start year mentioned, use 5 years before today.
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
- If a [Metric: X] prefix is present in the query, use that as the metric.
- Default metric if unclear: "vegetation_change".

Today is {TODAY}.

Examples:
Input: "how much green cover did this area lose since 2020"
Output: {"metric": "vegetation_change", "region": null, "start_date": "2020-01-01", "end_date": "{TODAY}"}

Input: "[Metric: deforestation] how much deforestation has happened in this region over 5 years"
Output: {"metric": "deforestation", "region": null, "start_date": "{FIVE_YEARS_AGO}", "end_date": "{TODAY}"}
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
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content
        logger.info(f"LLM raw response: {raw}")
        parsed = _extract_json(raw)

        # Inject defaults for missing dates
        if not parsed.get("start_date"):
            parsed["start_date"] = five_years_ago
        if not parsed.get("end_date"):
            parsed["end_date"] = today

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
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.1,
            max_tokens=60,
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
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _INSIGHT_SYSTEM},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.3,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Insight generation failed: {e}")
        return None
