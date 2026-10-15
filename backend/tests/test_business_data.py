"""Business-tab data layer: data-quality descriptors, stale retention, sectors/FX, and the validated watchlist service.
No network: the single upstream-touching function in each module is replaced."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services.intel import data_quality as dq
from app.services.intel import market_indices as mi
from app.services.intel import watchlist as wl


def run(coro):
    return asyncio.run(coro)


def chart(price=100.0, prev=98.0, name="Test Co", closes=None, ts0=1_700_000_000):
    closes = closes if closes is not None else [prev, price]
    return {"chart": {"result": [{
        "meta": {"regularMarketPrice": price, "previousClose": prev, "shortName": name, "currency": "USD",
                 "fullExchangeName": "NYSE", "regularMarketTime": ts0 + 86400 * len(closes)},
        "timestamp": [ts0 + 86400 * i for i in range(len(closes))],
        "indicators": {"quote": [{"close": closes}]},
    }]}}


# ── data quality ─────────────────────────────────────────────────────────────
def test_describe_fresh_stale_and_never():
    fresh = dq.describe(1000.0, 900, None, now=1100.0)
    assert fresh["age_seconds"] == 100 and fresh["stale"] is False and "Yahoo" in fresh["source"]
    assert dq.describe(1000.0, 900, None, now=1000.0 + 1801)["stale"] is True      # more than 2 intervals old
    assert dq.describe(1000.0, 900, None, now=1000.0 + 1800)["stale"] is False
    never = dq.describe(0.0, 900, "boom")
    assert never["stale"] is True and never["cached_at"] is None and never["last_error"] == "boom"


def test_merge_keeps_last_good_quote_flagged_stale_and_follows_expected_order():
    expected = [("A", "a", "u", "c"), ("B", "b", "u", "c"), ("C", "c", "u", "c")]
    prev = [{"symbol": "A", "value": 1}, {"symbol": "B", "value": 2}]
    fresh = [{"symbol": "B", "value": 20}, {"symbol": "C", "value": 30}]
    out = dq.merge_quotes(prev, fresh, expected)
    assert [(q["symbol"], q["value"], q["stale"]) for q in out] == [("A", 1, True), ("B", 20, False), ("C", 30, False)]
    assert dq.merge_quotes([], [], expected) == []                       # never fetched: no invented rows


# ── market indices ───────────────────────────────────────────────────────────
def test_markets_include_sectors_fx_and_data_quality(monkeypatch):
    async def fake_fetch(client, symbol, label, unit, category):
        return None if symbol == "^CNXIT" else {"symbol": symbol, "name": label, "unit": unit, "category": category, "value": 10.0, "change_pct": 1.0}
    async def no_sleep(_): return None
    monkeypatch.setattr(mi, "_fetch_one", fake_fetch)
    monkeypatch.setattr(mi.asyncio, "sleep", no_sleep)
    mi._cache.update({"cached_at": 0.0, "last_error": None, **{g: [] for g, _ in mi.GROUPS}})
    n = run(mi.refresh(force=True))
    m = mi.get_markets()
    assert n == len(mi.ALL_SYMBOLS) - 1
    assert {"indices", "stocks", "sectors", "fx", "data_quality"} <= set(m)
    assert len(m["sectors"]) == len(mi.SECTORS) - 1 and "^CNXIT" not in {q["symbol"] for q in m["sectors"]}
    assert "INR=X" in {q["symbol"] for q in m["fx"]} and m["data_quality"]["stale"] is False
    assert "1/" not in (m["last_error"] or "") and "only" in m["last_error"]


def test_failed_symbol_keeps_its_last_good_quote_next_cycle(monkeypatch):
    calls = {"n": 0}
    async def fake_fetch(client, symbol, label, unit, category):
        if calls["n"] >= 1 and symbol == "^GSPC":
            return None
        return {"symbol": symbol, "name": label, "unit": unit, "category": category, "value": 5.0 + calls["n"], "change_pct": 0.1}
    async def no_sleep(_): return None
    monkeypatch.setattr(mi, "_fetch_one", fake_fetch)
    monkeypatch.setattr(mi.asyncio, "sleep", no_sleep)
    mi._cache.update({"cached_at": 0.0, "last_error": None, **{g: [] for g, _ in mi.GROUPS}})
    run(mi.refresh(force=True))
    calls["n"] = 1
    run(mi.refresh(force=True))
    sp = next(q for q in mi.get_markets()["indices"] if q["symbol"] == "^GSPC")
    assert sp["stale"] is True and sp["value"] == 5.0                 # still shown, honestly flagged
    assert next(q for q in mi.get_markets()["indices"] if q["symbol"] == "^DJI")["stale"] is False


def test_scheduler_intervals_follow_the_cache_ttls():
    from app.services.intel import scheduler, commodity_prices
    assert scheduler.INTERVAL_MARKETS == mi.CACHE_TTL_SECONDS == 900
    assert scheduler.INTERVAL_COMMODITIES == commodity_prices.CACHE_TTL_SECONDS == 900


# ── watchlist service ────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("reliance.ns", "RELIANCE.NS"), ("^nsei", "^NSEI"), ("INR=X", "INR=X"), ("BRK-B", "BRK-B"), ("  aapl ", "AAPL"),
    ("", None), ("a b", None), ("x" * 16, None), ("../etc/passwd", None), ("AAPL;DROP", None), ("a/b", None), ("A?x=1", None),
    (None, None), (123, None),
])
def test_symbol_normalisation_and_rejection(raw, expected):
    assert wl.normalize_symbol(raw) == expected


def test_parse_quote_prefers_meta_falls_back_to_last_close_and_rejects_empty():
    q = wl.parse_quote(chart(105.0, 100.0), "T")
    assert q["value"] == 105.0 and q["change_pct"] == 5.0 and q["name"] == "Test Co" and q["currency"] == "USD"
    noprice = chart(None, 100.0, closes=[99.0, None, 101.5])
    assert wl.parse_quote(noprice, "T")["value"] == 101.5
    assert wl.parse_quote({"chart": {"result": None}}, "T") is None
    assert wl.parse_quote(chart(None, None, closes=[None, None]), "T") is None


def test_quotes_are_cached_deduplicated_and_isolated(monkeypatch):
    wl.clear_caches()
    fetched = []
    async def fake_chart(client, symbol, range_, interval="1d"):
        fetched.append(symbol)
        if symbol == "BAD":
            raise RuntimeError("upstream secret details")
        return chart(110.0, 100.0, name=symbol)
    monkeypatch.setattr(wl, "_fetch_chart", fake_chart)
    out = run(wl.get_quotes(["aapl", "AAPL", "BAD", "msft", "not a symbol"]))
    assert [q["symbol"] for q in out["quotes"]] == ["AAPL", "MSFT"]                    # order kept, duplicate collapsed
    assert "BAD" in out["errors"] and "not a symbol" in out["errors"]
    assert "secret" not in str(out)                                                    # upstream error text never echoed
    n_first = len(fetched)
    run(wl.get_quotes(["AAPL", "MSFT"]))
    assert len(fetched) == n_first                                                     # served from cache: no new upstream calls
    assert out["data_quality"]["stale"] is False


def test_too_many_or_no_symbols_is_a_clear_error():
    with pytest.raises(ValueError, match="At most"):
        run(wl.get_quotes([f"S{i}" for i in range(wl.MAX_SYMBOLS_PER_REQUEST + 1)]))
    with pytest.raises(ValueError, match="at least one"):
        run(wl.get_quotes([]))


def test_cache_is_size_capped(monkeypatch):
    wl.clear_caches()
    monkeypatch.setattr(wl, "MAX_CACHE_ENTRIES", 5)
    for i in range(20):
        wl._cache_put(wl._quote_cache, f"S{i}", {"symbol": f"S{i}"})
    assert len(wl._quote_cache) == 5 and "S19" in wl._quote_cache and "S0" not in wl._quote_cache


def test_history_cached_validated_and_failure_is_not_cached(monkeypatch):
    wl.clear_caches()
    calls = []
    async def fake_chart(client, symbol, range_, interval="1d"):
        calls.append((symbol, range_))
        return chart(110.0, 100.0, closes=[100.0, 101.0, None, 103.0])
    monkeypatch.setattr(wl, "_fetch_chart", fake_chart)
    h = run(wl.get_history("aapl", "1y"))
    assert h["symbol"] == "AAPL" and [p["value"] for p in h["points"]] == [100.0, 101.0, 103.0]
    run(wl.get_history("AAPL", "1y"))
    assert len(calls) == 1
    run(wl.get_history("AAPL", "bogus-range"))                                          # unknown range falls back to 1y (cached)
    assert len(calls) == 1
    with pytest.raises(ValueError):
        run(wl.get_history("../x"))
    async def boom(client, symbol, range_, interval="1d"): raise RuntimeError("x")
    monkeypatch.setattr(wl, "_fetch_chart", boom)
    wl.clear_caches()
    bad = run(wl.get_history("ZZZ"))
    assert bad["points"] == [] and "error" in bad and ("ZZZ", "1y") not in wl._history_cache
