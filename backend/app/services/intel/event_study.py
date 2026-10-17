"""event_study.py — how did a stock behave around dated events, compared with the market and with its own ordinary days?

Method (standard "market-adjusted event study", kept deliberately simple and descriptive):
  * Event day = the first trading day on or after the event date (so a weekend event aligns to Monday).
  * For each window k in (0, 1, 3, 5) trading days: return from the CLOSE BEFORE the event day to the close k days after the
    event day, for the stock and for a benchmark (^GSPC for US names, ^NSEI for .NS/.BO), then abnormal = stock - benchmark.
    No beta is estimated, so "abnormal" means "moved differently from the market", not "caused by the event".
  * Context, so a number is not read in a vacuum: the same window measured on EVERY day of the stock's own history gives its
    ordinary 5th–95th percentile range; and for n >= 3 events the average abnormal return is ranked against 2,000 averages of n
    randomly chosen windows (fixed seed, so results are reproducible). A percentile near 0 or 100 means "unusual vs random days";
    it is NOT a significance test and says nothing about cause.
Event sources: dates the analyst types in, or USGS earthquakes (M >= chosen magnitude) inside a rough region box.
"""
import bisect
import datetime as dt
import logging
import math
import random
import re
from statistics import mean, median, pstdev
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import watchlist
from .commodity_prices import _HEADERS

logger = logging.getLogger(__name__)

WINDOWS = (0, 1, 3, 5)
MAX_EVENTS = 40
MIN_EVENTS_FOR_STATS = 3
BOOTSTRAP_DRAWS = 2000
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Rough bounding boxes chosen by Vayu for "where might a quake matter to companies here"; NOT official regions.
QUAKE_REGIONS: Dict[str, Dict[str, Any]] = {
    "taiwan": {"label": "Taiwan (rough box)", "box": (21.5, 25.8, 119.5, 122.5)},
    "japan": {"label": "Japan (rough box)", "box": (30.0, 46.0, 128.0, 146.5)},
    "india": {"label": "Indian subcontinent (rough box)", "box": (6.0, 36.0, 68.0, 98.0)},
    "california": {"label": "California (rough box)", "box": (32.0, 42.0, -125.0, -114.0)},
    "chile": {"label": "Chile (rough box)", "box": (-56.0, -17.0, -76.0, -66.0)},
    "turkey": {"label": "Turkey (rough box)", "box": (36.0, 42.0, 26.0, 45.0)},
}


def default_benchmark(symbol: str) -> str:
    s = (symbol or "").upper()
    return "^NSEI" if s.endswith(".NS") or s.endswith(".BO") else "^GSPC"


def to_closes(points: List[dict]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p in points or []:
        try:
            out[dt.datetime.fromtimestamp(p["date"], tz=dt.timezone.utc).strftime("%Y-%m-%d")] = float(p["value"])
        except (KeyError, ValueError, TypeError, OSError, OverflowError):
            continue
    return out


def _pct(a: float, b: float) -> Optional[float]:
    return None if not a or a <= 0 or b is None else (b / a - 1.0) * 100.0


def _percentile(sorted_vals: List[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


class _Series:
    """Stock closes on the stock's own trading calendar; benchmark values forward-filled onto it (holiday calendars differ)."""

    def __init__(self, stock: Dict[str, float], bench: Dict[str, float]):
        self.dates = sorted(stock)
        self.px = [stock[d] for d in self.dates]
        self.bdates = sorted(bench)
        self.bpx = [bench[d] for d in self.bdates]

    def bench_at(self, date: str) -> Optional[float]:
        i = bisect.bisect_right(self.bdates, date) - 1
        return self.bpx[i] if i >= 0 else None

    def window(self, idx0: int, k: int) -> Optional[Dict[str, float]]:
        """Close before the event day -> close k days after it. None if the data does not reach."""
        if idx0 < 1 or idx0 + k >= len(self.px):
            return None
        b0, b1 = self.bench_at(self.dates[idx0 - 1]), self.bench_at(self.dates[idx0 + k])
        s = _pct(self.px[idx0 - 1], self.px[idx0 + k])
        bm = _pct(b0, b1) if b0 and b1 else None
        if s is None or bm is None:
            return None
        return {"stock": round(s, 2), "bench": round(bm, 2), "abnormal": round(s - bm, 2)}

    def ordinary(self, k: int) -> List[float]:
        out = []
        for i in range(1, len(self.px) - k):
            w = self.window(i, k)
            if w:
                out.append(w["abnormal"])
        return out


def compute(stock: Dict[str, float], bench: Dict[str, float], events: List[Dict[str, str]], symbol: str = "", benchmark: str = "") -> Dict[str, Any]:
    """events: [{"date": "YYYY-MM-DD", "label": optional}]. Pure function: no network."""
    ser = _Series(stock, bench)
    notes = ["Abnormal = the stock's move minus the benchmark's over the same days. No beta is estimated, so it means \"moved differently from the market\", not \"was caused by the event\".",
             "Descriptive only: other news, earnings or sector moves can land in the same window. Small samples are noisy; read n before the averages."]
    out: Dict[str, Any] = {"symbol": symbol, "benchmark": benchmark, "windows": list(WINDOWS), "events": [], "skipped": [], "summary": {}, "baseline": {}, "notes": notes,
                           "data_range": {"start": ser.dates[0] if ser.dates else None, "end": ser.dates[-1] if ser.dates else None, "trading_days": len(ser.dates)}}
    if len(ser.dates) < 60 or not ser.bdates:
        notes.insert(0, "Not enough price history for the symbol or the benchmark to run an event study.")
        return out
    base = {k: sorted(ser.ordinary(k)) for k in WINDOWS}
    out["baseline"] = {str(k): {"p05": round(_percentile(v, 0.05), 2), "median": round(_percentile(v, 0.5), 2), "p95": round(_percentile(v, 0.95), 2), "n": len(v)} for k, v in base.items() if v}
    seen_idx: set = set()
    rows: List[Dict[str, Any]] = []
    for ev in events[:MAX_EVENTS]:
        d = ev["date"]
        if d < ser.dates[0]:
            out["skipped"].append({"date": d, "reason": "before the available price history"}); continue
        idx0 = bisect.bisect_left(ser.dates, d)
        if idx0 >= len(ser.dates):
            out["skipped"].append({"date": d, "reason": "after the latest price data"}); continue
        if idx0 < 1:
            out["skipped"].append({"date": d, "reason": "no prior close to measure from"}); continue
        if idx0 in seen_idx:
            out["skipped"].append({"date": d, "reason": "same trading day as an earlier event (counted once)"}); continue
        seen_idx.add(idx0)
        ret: Dict[str, Any] = {}
        for k in WINDOWS:
            w = ser.window(idx0, k)
            if w:
                b = base[k]
                w["unusual"] = bool(b) and (w["abnormal"] < _percentile(b, 0.05) or w["abnormal"] > _percentile(b, 0.95))
            ret[str(k)] = w
        rows.append({"date": d, "aligned_date": ser.dates[idx0], "label": ev.get("label"), "ret": ret,
                     "note": f"aligned to the next trading day ({ser.dates[idx0]})" if ser.dates[idx0] != d else None, "_idx": idx0})
    idxs = sorted(r["_idx"] for r in rows)
    for r in rows:
        r["overlaps_other_event"] = any(0 < abs(i - r["_idx"]) <= max(WINDOWS) for i in idxs)
        r.pop("_idx")
    out["events"] = rows
    if any(r["overlaps_other_event"] for r in rows):
        notes.append("Some events fall within 5 trading days of each other, so their windows overlap and are not independent.")
    for k in WINDOWS:
        vals = [r["ret"][str(k)]["abnormal"] for r in rows if r["ret"].get(str(k))]
        if not vals:
            continue
        s: Dict[str, Any] = {"n": len(vals), "mean_abnormal_pct": round(mean(vals), 2), "median_abnormal_pct": round(median(vals), 2),
                             "share_positive_pct": round(100.0 * sum(1 for v in vals if v > 0) / len(vals), 0)}
        if len(vals) >= MIN_EVENTS_FOR_STATS:
            s["std_error_pct"] = round(pstdev(vals) / math.sqrt(len(vals)), 2)
            pool = ser.ordinary(k)
            if len(pool) >= 30:
                rng = random.Random(1000 * k + len(vals))
                obs = mean(vals)
                means = [mean(rng.sample(pool, len(vals)) if len(vals) <= len(pool) else rng.choices(pool, k=len(vals))) for _ in range(BOOTSTRAP_DRAWS)]
                s["percentile_vs_random_windows"] = round(100.0 * sum(1 for m in means if m <= obs) / BOOTSTRAP_DRAWS, 0)
        else:
            s["note"] = f"Fewer than {MIN_EVENTS_FOR_STATS} events: no spread or ranking is shown."
        out["summary"][str(k)] = s
    return out


def parse_dates(raw: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """'2024-03-01, 2024-05-02' (also newline/semicolon separated, optional ' label') -> events, plus problems found."""
    events, problems, seen = [], [], set()
    for tok in re.split(r"[,;\n]+", raw or ""):
        tok = tok.strip()
        if not tok:
            continue
        date, _, label = tok.partition(" ")
        if not DATE_RE.match(date):
            problems.append(f"'{tok[:20]}' is not a date (use YYYY-MM-DD)"); continue
        try:
            d = dt.date.fromisoformat(date)
        except ValueError:
            problems.append(f"'{date}' is not a real date"); continue
        if d > dt.date.today():
            problems.append(f"'{date}' is in the future"); continue
        if date in seen:
            continue
        seen.add(date)
        events.append({"date": date, "label": label.strip()[:60] or None})
    return events, problems


async def study(symbol: str, events: List[Dict[str, str]], benchmark: Optional[str] = None) -> Dict[str, Any]:
    sym = watchlist.normalize_symbol(symbol)
    if not sym:
        raise ValueError("Invalid symbol.")
    bm = watchlist.normalize_symbol(benchmark) if benchmark else default_benchmark(sym)
    if not bm:
        raise ValueError("Invalid benchmark symbol.")
    import asyncio
    h_s, h_b = await asyncio.gather(watchlist.get_history(sym, "5y"), watchlist.get_history(bm, "5y"))
    res = compute(to_closes(h_s.get("points")), to_closes(h_b.get("points")), events, sym, bm)
    if not h_s.get("points"):
        res["notes"].insert(0, h_s.get("error") or "Price history for this symbol is unavailable right now.")
    elif not h_b.get("points"):
        res["notes"].insert(0, f"Benchmark {bm} history is unavailable right now.")
    return res


# ── USGS earthquakes as an event source ───────────────────────────────────────
def parse_quakes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One event per UTC day (the strongest), with magnitude and place as the label."""
    best: Dict[str, Dict[str, Any]] = {}
    for f in (data or {}).get("features", []) or []:
        p = f.get("properties") or {}
        try:
            day = dt.datetime.fromtimestamp(float(p["time"]) / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%d")
            mag = float(p["mag"])
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            continue
        if day not in best or mag > best[day]["mag"]:
            best[day] = {"date": day, "mag": mag, "label": f"M{mag:.1f} {str(p.get('place') or '')[:40]}".strip()}
    return [{"date": e["date"], "label": e["label"]} for e in sorted(best.values(), key=lambda e: e["date"])]


async def fetch_quakes(region: str, min_magnitude: float) -> List[Dict[str, Any]]:
    reg = QUAKE_REGIONS.get(region)
    if not reg:
        raise ValueError("Unknown region.")
    if not (5.0 <= min_magnitude <= 8.5):
        raise ValueError("Minimum magnitude must be between 5.0 and 8.5.")
    la0, la1, lo0, lo1 = reg["box"]
    start = (dt.date.today() - dt.timedelta(days=5 * 365 - 5)).isoformat()
    params = {"format": "geojson", "minlatitude": la0, "maxlatitude": la1, "minlongitude": lo0, "maxlongitude": lo1,
              "minmagnitude": min_magnitude, "starttime": start, "orderby": "time", "limit": 500}
    async with httpx.AsyncClient(headers=_HEADERS, timeout=20) as client:
        resp = await client.get(USGS_URL, params=params)
        resp.raise_for_status()
        return parse_quakes(resp.json())


async def study_quakes(symbol: str, region: str, min_magnitude: float, benchmark: Optional[str] = None) -> Dict[str, Any]:
    reg = QUAKE_REGIONS.get(region)
    if not reg:
        raise ValueError("Unknown region.")
    try:
        events = await fetch_quakes(region, min_magnitude)
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"USGS quake history failed: {type(e).__name__}")
        return {"symbol": symbol, "events": [], "skipped": [], "summary": {}, "baseline": {}, "windows": list(WINDOWS), "source": None,
                "notes": ["USGS earthquake history could not be fetched right now. Try again shortly."]}
    capped = len(events) > MAX_EVENTS
    res = await study(symbol, events[-MAX_EVENTS:], benchmark)
    res["source"] = {"name": "USGS Earthquake Catalog", "url": "https://earthquake.usgs.gov/fdsnws/event/1/", "region": reg["label"], "min_magnitude": min_magnitude,
                     "events_found": len(events), "note": "One event per day (the strongest). The region is a rough box chosen by Vayu."}
    if capped:
        res["notes"].append(f"{len(events)} qualifying days were found; only the most recent {MAX_EVENTS} are analysed.")
    if not events:
        res["notes"].insert(0, "No earthquakes at or above this magnitude were found in this region over the last five years.")
    return res
