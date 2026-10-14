"""
sea_safety.py — deterministic go / caution / no-go screen for going to sea.

This is deliberately NOT an LLM judgement. Given a normalised forecast
(services/marine_data.py), a time window and a boat class, it compares the worst
forecast value in the window against explicit thresholds and returns the verdict
together with every number, threshold and reason behind it, so a fisherman, a
reviewer or an evaluator can check exactly why it said what it said.

Principles:
  * Fail safe: if the wave or wind data needed for the verdict is missing for the
    window, the verdict is UNKNOWN — never GO.
  * Worst hour wins: one NO-GO hour in the window makes the window NO-GO, because a
    boat out at 06:00 is still out at 14:00.
  * Thresholds are conservative SCREENING defaults for each boat class (edit
    BOAT_CLASSES), not official standards. Official advisories from IMD / INCOIS /
    the Coast Guard / state fisheries departments always override this screen.
"""

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

LEVELS = ["GO", "CAUTION", "NO-GO"]          # index = severity
THUNDER_CODES = {95, 96, 99}                 # WMO: thunderstorm (slight/moderate, with hail)

# (caution_at, nogo_at). Higher value = worse unless noted.
BOAT_CLASSES: Dict[str, Dict[str, Any]] = {
    "small": {"label": "small / traditional boat", "wave_m": (1.5, 2.5), "gust_kmh": (29, 39), "thunder": 2},
    "mechanised": {"label": "mechanised fishing boat", "wave_m": (2.5, 3.5), "gust_kmh": (39, 50), "thunder": 2},
    "large": {"label": "large vessel / trawler", "wave_m": (3.5, 5.0), "gust_kmh": (50, 62), "thunder": 1},
}
COMMON = {
    "visibility_m": (2000, 500),     # LOWER is worse
    "precip_mm_h": (10, 25),
    "cape": (2500, None),            # convective instability: caution only
}
DISCLAIMER = ("This is an automated screening from forecast data, not an official advisory. Always follow the latest "
              "IMD / INCOIS / Coast Guard / state fisheries warnings, and local port-authority signals.")


def parse_t(t: str) -> dt.datetime:
    return dt.datetime.fromisoformat(t)


def now_local(forecast: Dict[str, Any], now_utc: Optional[dt.datetime] = None) -> dt.datetime:
    base = now_utc or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    return base + dt.timedelta(seconds=forecast.get("utc_offset_s", 0))


def resolve_window(label: str, now: dt.datetime) -> Tuple[dt.datetime, dt.datetime, str]:
    """Map a coarse time label to a [start, end) window in the location's local time."""
    floor = now.replace(minute=0, second=0, microsecond=0)
    today = floor.replace(hour=0)

    def at(day: int, hour: int) -> dt.datetime:
        return today + dt.timedelta(days=day, hours=hour)

    if label == "now":
        return floor, floor + dt.timedelta(hours=6), "the next 6 hours"
    if label == "today":
        return floor, max(at(1, 0), floor + dt.timedelta(hours=3)), "the rest of today"
    if label == "tonight":
        start = at(0, 18) if floor < at(0, 18) else floor
        return start, at(1, 6), "tonight"
    if label == "tomorrow_morning":
        return at(1, 5), at(1, 12), "tomorrow morning (05:00-12:00)"
    if label == "tomorrow_afternoon":
        return at(1, 12), at(1, 18), "tomorrow afternoon (12:00-18:00)"
    if label == "tomorrow":
        return at(1, 5), at(1, 19), "tomorrow (05:00-19:00)"
    if label == "next_48h":
        return floor, floor + dt.timedelta(hours=48), "the next 48 hours"
    return floor, floor + dt.timedelta(hours=24), "the next 24 hours"


def _level(value: Optional[float], caution: Optional[float], nogo: Optional[float], lower_is_worse: bool = False) -> Optional[int]:
    if value is None:
        return None
    if lower_is_worse:
        if nogo is not None and value < nogo:
            return 2
        if caution is not None and value < caution:
            return 1
        return 0
    if nogo is not None and value >= nogo:
        return 2
    if caution is not None and value >= caution:
        return 1
    return 0


def _gust(h: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """-> (gust km/h, estimated?). A measured/forecast gust always wins; the estimate (mean wind x 1.5,
    only produced by the MET Norway fallback) is used so we can still warn, but it is flagged."""
    g = h.get("gust_kmh")
    if g is not None:
        return g, False
    est = h.get("gust_est_kmh")
    return (est, True) if est is not None else (None, False)


def _hour_levels(h: Dict[str, Any], cls: Dict[str, Any]) -> Dict[str, Optional[int]]:
    wc, ng = cls["wave_m"]
    gc, gn = cls["gust_kmh"]
    code = h.get("wcode")
    thunder = None if code is None else (cls["thunder"] if int(code) in THUNDER_CODES else 0)
    return {
        "wave": _level(h.get("wave_h"), wc, ng),
        "gust": _level(_gust(h)[0], gc, gn),
        "thunder": thunder,
        "visibility": _level(h.get("vis_m"), *COMMON["visibility_m"], lower_is_worse=True),
        "rain": _level(h.get("precip_mm"), *COMMON["precip_mm_h"]),
        "cape": _level(h.get("cape"), *COMMON["cape"]),
    }


def _overall(levels: Dict[str, Optional[int]]) -> Optional[int]:
    vals = [v for v in levels.values() if v is not None]
    return max(vals) if vals else None


def _hour_state(levels: Dict[str, Optional[int]], h: Optional[Dict[str, Any]] = None) -> str:
    """Per-hour state; UNKNOWN unless both wave and wind are known (required inputs). If the gust is
    only an estimate, the best this hour can be is CAUTION."""
    if levels["wave"] is None or levels["gust"] is None:
        return "UNKNOWN"
    state = LEVELS[_overall(levels)]
    if state == "GO" and h is not None and _gust(h)[1]:
        return "CAUTION"
    return state


def assess(forecast: Dict[str, Any], start: dt.datetime, end: dt.datetime, boat_class: str = "small",
           now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    cls = BOAT_CLASSES.get(boat_class) or BOAT_CLASSES["small"]
    boat_class = boat_class if boat_class in BOAT_CLASSES else "small"
    base: Dict[str, Any] = {"boat_class": boat_class, "boat_label": cls["label"], "disclaimer": DISCLAIMER,
                            "window": {"start": start.isoformat(timespec="minutes"), "end": end.isoformat(timespec="minutes")}}
    if not forecast.get("ok"):
        return {**base, "verdict": "UNKNOWN", "reasons": ["No forecast data could be retrieved."], "hazards": [],
                "go_windows": [], "series": [], "context": {}, "data_coverage": 0.0}

    hours = [(parse_t(h["t"]), h) for h in forecast["hours"]]
    in_win = [(t, h) for t, h in hours if start <= t < end]
    now = now or now_local(forecast)
    now_floor = now.replace(minute=0, second=0, microsecond=0)

    # ── whole-forecast per-hour states (for the strip and for "safer windows") ──
    states = [(t, h, _hour_levels(h, cls)) for t, h in hours]
    series = [{"t": t.isoformat(timespec="minutes"), "state": _hour_state(lv, h),
               "wave_h": h.get("wave_h"), "gust_kmh": _gust(h)[0], "gust_estimated": _gust(h)[1]}
              for t, h, lv in states if now_floor <= t < now_floor + dt.timedelta(hours=48)]

    go_windows: List[Dict[str, Any]] = []
    run: List[dt.datetime] = []
    for t, h, lv in states + [(None, None, None)]:
        if t is not None and t >= now_floor and _hour_state(lv, h) == "GO":
            run.append(t)
            continue
        if len(run) >= 3:
            go_windows.append({"start": run[0].isoformat(timespec="minutes"),
                               "end": (run[-1] + dt.timedelta(hours=1)).isoformat(timespec="minutes"), "hours": len(run)})
        run = []
    go_windows = go_windows[:3]

    if not in_win:
        return {**base, "verdict": "UNKNOWN", "reasons": ["The requested time is outside the forecast range."], "hazards": [],
                "go_windows": go_windows, "series": series, "context": {}, "data_coverage": 0.0}

    # ── window hazards ─────────────────────────────────────────────────────────
    win_levels = [(t, h, _hour_levels(h, cls)) for t, h in in_win]

    def coverage(key: str) -> float:
        return sum(1 for _, _, lv in win_levels if lv[key] is not None) / len(win_levels)

    cov_wave, cov_gust = coverage("wave"), coverage("gust")
    wc, wn = cls["wave_m"]
    gc, gn = cls["gust_kmh"]
    spec = [
        ("wave", "Wave height", "m", "wave_h", wc, wn, False, max),
        ("gust", "Wind gusts", "km/h", "_gust_eff", gc, gn, False, max),
        ("visibility", "Visibility", "m", "vis_m", COMMON["visibility_m"][0], COMMON["visibility_m"][1], True, min),
        ("rain", "Rainfall", "mm/h", "precip_mm", COMMON["precip_mm_h"][0], COMMON["precip_mm_h"][1], False, max),
        ("cape", "Storm energy (CAPE)", "J/kg", "cape", COMMON["cape"][0], COMMON["cape"][1], False, max),
    ]
    est_hours = sum(1 for _, h in in_win if _gust(h)[1])
    gust_estimated = est_hours > 0
    hazards: List[Dict[str, Any]] = []
    for key, label, unit, field, c_at, n_at, lower, agg in spec:
        if field == "_gust_eff":
            vals = [(g, t) for t, h, _ in win_levels if (g := _gust(h)[0]) is not None]
            if gust_estimated:
                label = "Wind gusts (ESTIMATED from mean wind)"
        else:
            vals = [(h[field], t) for t, h, _ in win_levels if h.get(field) is not None]
        if not vals:
            continue
        peak_v, peak_t = agg(vals, key=lambda x: x[0])
        lvl = _level(peak_v, c_at, n_at, lower)
        hazards.append({"key": key, "label": label, "unit": unit, "peak": round(float(peak_v), 2),
                        "peak_time": peak_t.isoformat(timespec="minutes"), "level": LEVELS[lvl],
                        "caution_at": c_at, "nogo_at": n_at, "lower_is_worse": lower,
                        "coverage": round(coverage(key), 2)})
    thunder_hours = [t for t, h, lv in win_levels if lv["thunder"] and h.get("wcode") is not None and int(h["wcode"]) in THUNDER_CODES]
    if coverage("thunder") > 0:
        hazards.append({"key": "thunder", "label": "Thunderstorm", "unit": "", "peak": 1.0 if thunder_hours else 0.0,
                        "peak_time": thunder_hours[0].isoformat(timespec="minutes") if thunder_hours else None,
                        "level": LEVELS[cls["thunder"] if thunder_hours else 0], "caution_at": None, "nogo_at": None,
                        "lower_is_worse": False, "coverage": round(coverage("thunder"), 2)})

    worst = max(LEVELS.index(hz["level"]) for hz in hazards) if hazards else None
    required_ok = cov_wave >= 0.5 and cov_gust >= 0.5
    if worst is None or not required_ok:
        verdict = "UNKNOWN"
        missing = [n for n, c in (("wave height", cov_wave), ("wind gust", cov_gust)) if c < 0.5]
        reasons = [f"Not enough {' and '.join(missing)} data for this window to give a safe verdict."]
    else:
        verdict = LEVELS[worst]
        reasons = []
        for hz in sorted(hazards, key=lambda z: -LEVELS.index(z["level"])):
            if hz["level"] == "GO":
                continue
            when = hz["peak_time"][11:16] if hz.get("peak_time") else ""
            if hz["key"] == "thunder":
                reasons.append(f"Thunderstorm forecast in the window (from about {when}) — lightning risk for open boats.")
                continue
            sym = "below" if hz["lower_is_worse"] else "above"
            thr = hz["nogo_at"] if hz["level"] == "NO-GO" and hz["nogo_at"] is not None else hz["caution_at"]
            word = "NO-GO" if hz["level"] == "NO-GO" else "caution"
            extreme = "down to" if hz["lower_is_worse"] else "up to"
            reasons.append(f"{hz['label']} {extreme} {hz['peak']:g} {hz['unit']} around {when} ({word} {sym} {thr:g} {hz['unit']} for a {cls['label']}).")
        if verdict == "GO" and gust_estimated:
            verdict = "CAUTION"
            reasons = ["No measured wind-gust forecast was available (gusts were estimated from mean wind), so a GO cannot be issued. "
                       "Treat conditions as unconfirmed and check official warnings."]
        elif verdict == "GO":
            reasons = ["All checked hazards stay below the caution thresholds for this boat class throughout the window."]
        elif gust_estimated:
            reasons.append("Note: wind gusts here are estimated from mean wind, not forecast directly.")

    def vals_of(field):
        return [h[field] for _, h in in_win if h.get(field) is not None]
    sl = vals_of("sea_level_m")
    context = {
        "swell_peak_m": max(vals_of("swell_h")) if vals_of("swell_h") else None,
        "wave_period_s": round(sum(vals_of("wave_period")) / len(vals_of("wave_period")), 1) if vals_of("wave_period") else None,
        "sst_c": round(sum(vals_of("sst_c")) / len(vals_of("sst_c")), 1) if vals_of("sst_c") else None,
        "current_peak_kmh": max(vals_of("current_kmh")) if vals_of("current_kmh") else None,
        "tide_range_m": round(max(sl) - min(sl), 2) if len(sl) >= 2 else None,
    }
    return {**base, "verdict": verdict, "reasons": reasons, "hazards": hazards, "go_windows": go_windows,
            "series": series, "context": {**context, "gust_estimated": gust_estimated}, "data_coverage": round(min(cov_wave, cov_gust), 2)}
