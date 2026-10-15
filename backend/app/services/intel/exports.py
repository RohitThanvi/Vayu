"""exports.py — CSV downloads of the Business tab's datasets, so the numbers can leave the screen (Excel / Sheets / Python).

Security: several columns are text from external feeds (news headlines, event titles). A cell beginning with = + - @ (or a tab
/ carriage return) is executed as a FORMULA by Excel and Sheets, so such cells are neutralised with a leading apostrophe
(OWASP's CSV-injection guidance). Genuine numeric values are written as numbers and are untouched.
"""
import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

DATASETS = ("markets", "commodities", "chokepoint-risk", "events", "yield-curve")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in _FORMULA_PREFIXES:
        return "'" + v
    return v


def _iso(unix) -> str:
    try:
        return datetime.fromtimestamp(float(unix), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return ""


def to_csv(columns: List[str], rows: Iterable[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([safe_cell(r.get(c, "")) if r.get(c) is not None else "" for c in columns])
    return buf.getvalue()


def _markets_rows(markets: Dict[str, Any]) -> List[Dict[str, Any]]:
    dq = markets.get("data_quality", {})
    rows = []
    for group in ("indices", "sectors", "fx", "stocks"):
        for q in markets.get(group, []):
            rows.append({"group": group, "symbol": q.get("symbol"), "name": q.get("name"), "value": q.get("value"), "change_pct": q.get("change_pct"),
                         "prev_close": q.get("prev_close"), "quote_time_utc": _iso(q.get("market_time")), "stale": q.get("stale", False),
                         "source": dq.get("source"), "delay_note": dq.get("delay_note")})
    return rows


def _commodity_rows(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    dq = c.get("data_quality", {})
    return [{"symbol": q.get("symbol"), "name": q.get("name"), "unit": q.get("unit"), "category": q.get("category"), "value": q.get("value"),
             "change_pct": q.get("change_pct"), "prev_close": q.get("prev_close"), "quote_time_utc": _iso(q.get("market_time")),
             "stale": q.get("stale", False), "source": dq.get("source")} for q in c.get("commodities", [])]


def build_csv(dataset: str, payload: Any) -> Tuple[str, str]:
    """(filename, csv text). `payload` is whatever the dataset's endpoint already serves."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    if dataset == "markets":
        cols = ["group", "symbol", "name", "value", "change_pct", "prev_close", "quote_time_utc", "stale", "source", "delay_note"]
        return f"vayu-markets-{stamp}.csv", to_csv(cols, _markets_rows(payload))
    if dataset == "commodities":
        cols = ["symbol", "name", "unit", "category", "value", "change_pct", "prev_close", "quote_time_utc", "stale", "source"]
        return f"vayu-commodities-{stamp}.csv", to_csv(cols, _commodity_rows(payload))
    if dataset == "chokepoint-risk":
        cols = ["chokepoint", "chokepoint_display", "score", "band"]
        return f"vayu-chokepoint-risk-{stamp}.csv", to_csv(cols, payload)
    if dataset == "events":
        cols = ["id", "source", "tag", "severity", "ts", "lat", "lon", "title", "detail"]
        return f"vayu-intel-feed-{stamp}.csv", to_csv(cols, payload)
    if dataset == "yield-curve":
        curves = (payload or {}).get("curves", {})
        rows = [{"tenor": t, "latest": curves.get("latest", {}).get(t), "one_month_ago": curves.get("one_month_ago", {}).get(t),
                 "one_year_ago": curves.get("one_year_ago", {}).get(t), "as_of": (payload or {}).get("as_of"), "source": (payload or {}).get("source")}
                for t in (payload or {}).get("tenors", [])]
        return f"vayu-us-yield-curve-{stamp}.csv", to_csv(["tenor", "latest", "one_month_ago", "one_year_ago", "as_of", "source"], rows)
    raise ValueError(f"Unknown dataset '{dataset}'. Valid: {', '.join(DATASETS)}")
