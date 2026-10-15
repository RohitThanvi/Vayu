"""Asset one-pagers, India pressure indicators, yield curve, CSV exports and the PDF brief. No network."""
import asyncio
import math
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.intel import asset_profile as ap
from app.services.intel import exports as ex
from app.services.intel import india_risk as ir
from app.services.intel import strategic_sites, yield_curve as yc
from app.services import business_brief as bb


def run(coro):
    return asyncio.run(coro)


# ── price statistics ─────────────────────────────────────────────────────────
def test_stats_constant_growth_has_zero_volatility_and_exact_return():
    closes = [100 * (2 ** (i / 252)) for i in range(253)]            # doubles over 252 steps, constant daily log return
    s = ap.price_stats(closes)
    assert s["volatility_annualised_pct"] == 0.0 and s["max_drawdown_pct"] == 0.0
    assert s["return_1y_pct"] == pytest.approx(100.0, abs=0.01)
    assert s["position_in_52w_range_pct"] == 100.0 and s["n_days"] == 253


def test_stats_drawdown_and_volatility_against_hand_calculation():
    closes = [100.0] * 30 + [120.0, 60.0, 90.0]
    s = ap.price_stats(closes)
    assert s["max_drawdown_pct"] == -50.0                           # 120 -> 60
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    m = sum(rets) / len(rets)
    exp = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    assert s["volatility_annualised_pct"] == pytest.approx(exp, abs=0.01)
    assert s["low_52w"] == 60.0 and s["high_52w"] == 120.0


def test_stats_refuse_to_guess_on_short_or_dirty_history():
    assert ap.price_stats([1.0] * 29)["volatility_annualised_pct"] is None
    assert ap.price_stats([])["n_days"] == 0
    assert ap.price_stats([100.0] * 30 + [None, 0, -5])["n_days"] == 30   # junk values dropped, not computed on


# ── profiles ─────────────────────────────────────────────────────────────────
def test_ticker_links_come_only_from_the_published_mapping():
    risk = {"strait_of_hormuz": {"band": "elevated", "score": 41.0}}
    xom = ap.ticker_profile("XOM", {"name": "ExxonMobil"}, [100.0] * 40, risk)
    assert "strait_of_hormuz" in {c["chokepoint"] for c in xom["linked_chokepoints"]}
    assert next(c for c in xom["linked_chokepoints"] if c["chokepoint"] == "strait_of_hormuz")["band"] == "elevated"
    none = ap.ticker_profile("AAPL", None, [], {})
    assert none["linked_chokepoints"] == [] and "not one of Vayu's chokepoint bellwethers" in " ".join(none["notes"])


def test_site_link_only_where_the_sites_own_description_names_a_chokepoint():
    sites = {s["id"]: s for s in strategic_sites.list_sites()}
    jebel = ap.site_profile(sites["jebel_ali"], [], [], [], {"strait_of_hormuz": {"band": "normal", "score": 10.0}})
    assert [c["chokepoint"] for c in jebel["linked_chokepoints"]] == ["strait_of_hormuz"] and "XOM" in jebel["related_tickers"]
    rott = ap.site_profile(sites["rotterdam"], [], [], [], {})
    assert rott["linked_chokepoints"] == [] and rott["related_tickers"] == []
    for site_id, cp in ap.SITE_CHOKEPOINT.items():                  # every explicit link is backed by the site's own note
        note = sites[site_id]["note"].lower()
        assert {"strait_of_hormuz": "hormuz", "strait_of_malacca": "malacca"}[cp] in note


# ── India indicators ─────────────────────────────────────────────────────────
def test_percentile_change_and_wording():
    assert ir.percentile_rank([1, 2, 3, 4, 5], 5) == 100.0 and ir.percentile_rank([1, 2, 3, 4, 5], 1) == 20.0 and ir.percentile_rank([], 1) is None
    assert ir.pct_change([100.0] * 21 + [110.0], 21) == 10.0 and ir.pct_change([1.0], 21) is None
    assert ir.position_label(95).startswith("top fifth") and ir.position_label(5).startswith("bottom fifth") and ir.position_label(50).startswith("middle")


def _points(n, start=100.0, step=0.5):
    return [{"date": 1_700_000_000 + 86400 * i, "value": start + step * i} for i in range(n)]


def test_indicator_needs_history_and_places_latest_in_its_range():
    spec = ir.INDICATORS[0]
    assert ir.build_indicator(spec, _points(10)) is None
    ind = ir.build_indicator(spec, _points(260))
    assert ind["percentile_12m"] == 100.0 and "top fifth" in ind["position"] and ind["latest"] == 100.0 + 0.5 * 259
    assert len(ind["sparkline"]) <= 70 and "why" in ind


def test_india_risk_is_cached_partial_failures_reported_and_chokepoints_attached(monkeypatch):
    calls = []
    async def fake_hist(symbol, range_):
        calls.append(symbol)
        return {"symbol": symbol, "points": [] if symbol == "CT=F" else _points(260)}
    monkeypatch.setattr(ir.watchlist, "get_history", fake_hist)
    ir._cache.update({"data": None, "at": 0.0, "error": None})
    out = run(ir.get_india_risk())
    assert len(out["indicators"]) == len(ir.INDICATORS) - 1 and "only" in out["data_quality"]["last_error"]
    n = len(calls)
    run(ir.get_india_risk())
    assert len(calls) == n                                            # cached for an hour
    risk = [{"chokepoint": "strait_of_hormuz", "chokepoint_display": "Strait of Hormuz", "band": "normal", "score": 12.0},
            {"chokepoint": "suez_canal", "chokepoint_display": "Suez Canal", "band": "normal", "score": 5.0}]
    full = ir.attach_chokepoints(out, risk)
    assert [c["chokepoint"] for c in full["chokepoints"]] == ["strait_of_hormuz"] and "no combined index" in full["method"].lower().replace("deliberately ", "")
    assert not any("score" in k and "composite" in k for k in out)   # there is no composite index anywhere in the payload


# ── yield curve ──────────────────────────────────────────────────────────────
def test_parse_fred_csv_skips_header_and_missing_values():
    text = "observation_date,DGS10\n2026-10-01,4.10\n2026-10-02,.\n2026-10-03,\nbad,1\n2026-10-04,4.20\n"
    assert yc.parse_fred_csv(text) == [("2026-10-01", 4.10), ("2026-10-04", 4.20)]
    assert yc.parse_fred_csv("") == []


def _series(today, level):
    out = {}
    for tenor, _ in yc.TENORS:
        v = level.get(tenor)
        if v is None:
            continue
        out[tenor] = [((today - timedelta(days=k)).isoformat(), v + (0.0 if k < 20 else 0.5 if k < 200 else 1.0)) for k in range(400, -1, -1)]
    return out


def test_curve_shape_spreads_and_inversion_flags():
    today = date(2026, 10, 9)
    lvl = {t: v for t, v in zip([t for t, _ in yc.TENORS], [5.3, 5.2, 5.0, 4.8, 4.6, 4.5, 4.4, 4.4, 4.3, 4.5, 4.4])}   # inverted at the short end
    b = yc.build_curves(_series(today, lvl))
    assert b["as_of"] == "2026-10-09" and b["spreads"]["10y_minus_2y"]["value"] == round(4.3 - 4.6, 2)
    assert b["spreads"]["10y_minus_2y"]["inverted"] is True and b["spreads"]["10y_minus_3m"]["inverted"] is True
    assert b["curves"]["latest"]["10Y"] == 4.3 and b["curves"]["one_month_ago"]["10Y"] == 4.8 and b["curves"]["one_year_ago"]["10Y"] == 5.3
    assert b["spread_10y_2y_history"] and b["spread_10y_2y_history"][-1]["value"] == -0.3
    assert yc.build_curves({}) is None


def test_missing_tenor_is_none_not_invented():
    b = yc.build_curves(_series(date(2026, 10, 9), {"10Y": 4.3, "2Y": 4.0}))
    assert b["curves"]["latest"]["5Y"] is None and b["spreads"]["10y_minus_3m"]["value"] is None and b["spreads"]["10y_minus_3m"]["inverted"] is None


def test_get_yield_curve_caches_and_keeps_last_good_on_failure(monkeypatch):
    today = date.today()
    def csv_for(sid):
        rows = "\n".join(f"{(today - timedelta(days=k)).isoformat()},{4.0 + 0.01 * k}" for k in range(60, -1, -1))
        return "observation_date," + sid + "\n" + rows
    calls = []
    async def fake_fetch(client, sid, start):
        calls.append(sid)
        return csv_for(sid)
    monkeypatch.setattr(yc, "_fetch_series", fake_fetch)
    yc._cache.update({"data": None, "at": 0.0, "error": None})
    out = run(yc.get_yield_curve())
    assert out["spreads"]["10y_minus_2y"]["value"] == 0.0 and len(calls) == len(yc.TENORS) and out["data_quality"]["stale"] is False
    run(yc.get_yield_curve())
    assert len(calls) == len(yc.TENORS)                               # cached
    async def boom(client, sid, start): raise RuntimeError("down")
    monkeypatch.setattr(yc, "_fetch_series", boom)
    again = run(yc.get_yield_curve(force=True))
    assert again["curves"]["latest"]["10Y"] is not None               # last good curve still served...
    assert "unavailable" in again["data_quality"]["last_error"]       # ...and the failure is stated


# ── exports ──────────────────────────────────────────────────────────────────
def test_csv_injection_is_neutralised_but_numbers_are_untouched():
    assert ex.safe_cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")" and ex.safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert ex.safe_cell("+1+1") == "'+1+1" and ex.safe_cell("-2+3") == "'-2+3" and ex.safe_cell("\tcmd") == "'\tcmd"
    assert ex.safe_cell(-5.2) == -5.2 and ex.safe_cell("Normal headline") == "Normal headline"
    name, text = ex.build_csv("events", [{"id": "1", "source": "GDELT", "title": "=cmd|' /C calc'!A0", "severity": "warn", "ts": "t", "lat": 1, "lon": 2, "detail": ""}])
    lines = text.split("\r\n")
    assert lines[0] == "id,source,tag,severity,ts,lat,lon,title,detail" and "'=cmd" in lines[1] and name.startswith("vayu-intel-feed-")


def test_markets_and_yield_exports_have_stable_columns():
    mk = {"indices": [{"symbol": "^NSEI", "name": "Nifty 50", "value": 25000.5, "change_pct": 0.4, "market_time": 1_700_000_000, "stale": True}],
          "sectors": [], "fx": [{"symbol": "INR=X", "name": "USD/INR", "value": 88.1}], "stocks": [], "data_quality": {"source": "Yahoo", "delay_note": "d"}}
    _, text = ex.build_csv("markets", mk)
    rows = text.strip().split("\r\n")
    assert rows[0].startswith("group,symbol,name,value") and "indices,^NSEI,Nifty 50,25000.5,0.4" in rows[1] and "2023-11-14T22:13:20Z" in rows[1] and ",True," in rows[1]
    _, ytext = ex.build_csv("yield-curve", {"tenors": ["2Y"], "curves": {"latest": {"2Y": 4.1}, "one_month_ago": {"2Y": None}, "one_year_ago": {"2Y": 5.0}}, "as_of": "2026-10-09", "source": "FRED"})
    assert "2Y,4.1,,5.0,2026-10-09,FRED" in ytext
    with pytest.raises(ValueError, match="Unknown dataset"):
        ex.build_csv("secrets", [])


# ── PDF brief ────────────────────────────────────────────────────────────────
def test_brief_builds_with_full_and_with_empty_data_and_escapes_markup():
    snap = {"markets": {"indices": [{"name": "Nifty 50", "symbol": "^NSEI", "value": 25000.0, "change_pct": 0.5}], "sectors": [{"name": "Nifty IT", "value": 1.0, "change_pct": -1.0, "stale": True}],
                        "fx": [], "data_quality": {"source": "Yahoo", "age_seconds": 7300, "stale": True, "delay_note": "delayed"}},
            "commodities": {"commodities": [{"name": "Brent <b>", "value": 80.0, "change_pct": 1.0}], "data_quality": None},
            "risk": [{"chokepoint_display": "Strait of Hormuz", "score": 41.2, "band": "elevated"}],
            "events": [{"ts": "2026-10-12T10:00:00Z", "source": "GDELT", "severity": "critical", "title": "Port <script>alert(1)</script> & fire"}],
            "india": {"indicators": [{"label": "Brent crude", "latest": 80.0, "unit": "USD/barrel", "change_1m_pct": 2.0, "position": "middle of its 12-month range"}], "method": "m", "data_quality": None},
            "yield_curve": {"curves": {"latest": {"10Y": 4.3}, "one_month_ago": {"10Y": 4.2}, "one_year_ago": {"10Y": 3.9}}, "tenors": ["10Y"], "as_of": "2026-10-09",
                            "spreads": {"10y_minus_2y": {"value": -0.3, "inverted": True}, "10y_minus_3m": {"value": None, "inverted": None}}, "data_quality": None}}
    pdf = bb.build_business_brief(snap)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 2500
    empty = bb.build_business_brief({})
    assert empty[:5] == b"%PDF-"
