"""HTTP-level tests of the Business routes with every upstream call replaced."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.intel_endpoints as ep
import app.api.report_endpoints as rep
from app.services.intel import watchlist, yield_curve, india_risk, market_indices, commodity_prices
from app.services.intel.store import IntelStore


def run(c):
    return asyncio.run(c)


RISK = [{"chokepoint": "strait_of_hormuz", "chokepoint_display": "Strait of Hormuz", "score": 41.0, "band": "elevated"},
        {"chokepoint": "strait_of_malacca", "chokepoint_display": "Strait of Malacca", "score": 5.0, "band": "normal"}]


@pytest.fixture
def client(monkeypatch):
    store = IntelStore()
    monkeypatch.setattr(ep, "intel_store", store)
    async def fake_risk(): return list(RISK)
    monkeypatch.setattr(ep, "compute_risk_scores", fake_risk)
    ep._alerts_cache.clear()
    watchlist.clear_caches()
    app = FastAPI()
    app.include_router(ep.router, prefix="/api/v1")
    app.include_router(rep.router, prefix="/api/v1")
    return TestClient(app), store


def chart(price=100.0, prev=98.0, closes=None):
    closes = closes if closes is not None else [100.0 + i * 0.1 for i in range(260)]
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "previousClose": prev, "shortName": "Test"}, "timestamp": list(range(len(closes))),
                                  "indicators": {"quote": [{"close": closes}]}}]}}


def test_quotes_endpoint_validates_and_caps(client, monkeypatch):
    c, _ = client
    async def fake_chart(cl, sym, rng, interval="1d"): return chart()
    monkeypatch.setattr(watchlist, "_fetch_chart", fake_chart)
    ok = c.get("/api/v1/intel/quotes", params={"symbols": "aapl,INR=X,bad symbol"})
    assert ok.status_code == 200 and [q["symbol"] for q in ok.json()["quotes"]] == ["AAPL", "INR=X"] and "bad symbol" in ok.json()["errors"]
    assert c.get("/api/v1/intel/quotes", params={"symbols": ",".join(f"S{i}" for i in range(13))}).status_code == 422
    assert c.get("/api/v1/intel/quotes", params={"symbols": " , "}).status_code == 422
    assert c.get("/api/v1/intel/quote-history", params={"symbol": "../x"}).status_code == 422


def test_asset_profile_ticker_and_site(client, monkeypatch):
    c, _ = client
    async def fake_chart(cl, sym, rng, interval="1d"): return chart()
    monkeypatch.setattr(watchlist, "_fetch_chart", fake_chart)
    t = c.get("/api/v1/intel/asset-profile", params={"kind": "ticker", "id": "xom"}).json()
    assert t["id"] == "XOM" and t["stats"]["n_days"] == 260 and any(l["chokepoint"] == "strait_of_hormuz" and l["band"] == "elevated" for l in t["linked_chokepoints"])
    assert c.get("/api/v1/intel/asset-profile", params={"kind": "ticker", "id": "a b"}).status_code == 422
    s = c.get("/api/v1/intel/asset-profile", params={"kind": "site", "id": "jebel_ali"}).json()
    assert s["name"] == "Jebel Ali Port" and s["linked_chokepoints"][0]["band"] == "elevated"
    assert c.get("/api/v1/intel/asset-profile", params={"kind": "site", "id": "nope"}).status_code == 404
    assert c.get("/api/v1/intel/asset-profile", params={"kind": "bogus", "id": "x"}).status_code == 422


def test_india_and_yield_routes_wire_through(client, monkeypatch):
    c, _ = client
    async def fake_hist(symbol, range_): return {"symbol": symbol, "points": [{"date": i, "value": 100.0 + i * 0.1} for i in range(260)]}
    monkeypatch.setattr(india_risk.watchlist, "get_history", fake_hist)
    india_risk._cache.update({"data": None, "at": 0.0, "error": None})
    r = c.get("/api/v1/intel/india-risk").json()
    assert len(r["indicators"]) == len(india_risk.INDICATORS) and [x["chokepoint"] for x in r["chokepoints"]] == ["strait_of_hormuz", "strait_of_malacca"]
    async def fake_yc(force=False): return {"as_of": "2026-10-09", "tenors": ["10Y"], "curves": {"latest": {"10Y": 4.3}}, "spreads": {}}
    monkeypatch.setattr(ep.yield_curve, "get_yield_curve", fake_yc)
    assert c.get("/api/v1/intel/yield-curve").json()["as_of"] == "2026-10-09"


def test_alerts_snapshot_is_small_cached_and_includes_watched_symbols(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ep.market_indices, "get_markets", lambda: {"indices": [{"symbol": "^NSEI", "value": 25000.0, "change_pct": 0.5, "market_time": 1}], "stocks": [], "sectors": [], "fx": [], "data_quality": {"stale": False}})
    monkeypatch.setattr(ep.commodity_prices, "get_commodities", lambda: {"commodities": [{"symbol": "BZ=F", "value": 80.0, "change_pct": 1.0, "market_time": 2}]})
    calls = []
    async def fake_chart(cl, sym, rng, interval="1d"):
        calls.append(sym); return chart(price=250.0, prev=240.0)
    monkeypatch.setattr(watchlist, "_fetch_chart", fake_chart)
    r = c.get("/api/v1/intel/alerts-snapshot", params={"symbols": "TSLA,^NSEI,bad symbol"})
    j = r.json()
    assert r.status_code == 200 and set(j["quotes"]) == {"^NSEI", "BZ=F", "TSLA"} and j["quotes"]["TSLA"]["value"] == 250.0       # curated symbol not re-fetched
    assert calls == ["TSLA"] and j["risk"]["strait_of_hormuz"]["band"] == "elevated" and isinstance(j["sites"], dict)
    assert len(r.content) < 20_000                                                                                              # tiny, by design
    c.get("/api/v1/intel/alerts-snapshot", params={"symbols": "TSLA,^NSEI,bad symbol"})
    assert calls == ["TSLA"]                                                                                                    # 60 s cache


def test_csv_export_route(client, monkeypatch):
    c, store = client
    run(store.ingest([{"id": "e1", "source": "GDELT", "tag": "T", "title": "=BAD()", "detail": "", "lat": 1.0, "lon": 2.0, "severity": "warn", "ts": "2026-10-12T00:00:00Z", "meta": {}}]))
    r = c.get("/api/v1/intel/export/events.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"] and "'=BAD()" in r.text
    assert c.get("/api/v1/intel/export/secrets.csv").status_code == 404
    mk = c.get("/api/v1/intel/export/chokepoint-risk.csv")
    assert "Strait of Hormuz,41.0,elevated" in mk.text


def test_business_brief_route_returns_a_pdf_even_when_sources_fail(client, monkeypatch):
    c, _ = client
    async def boom(force=False): raise RuntimeError("fred down")
    monkeypatch.setattr(yield_curve, "get_yield_curve", boom)
    async def india_boom(): raise RuntimeError("yahoo down")
    monkeypatch.setattr(india_risk, "get_india_risk", india_boom)
    r = c.get("/api/v1/report/business-brief")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-" and "vayu-business-brief-" in r.headers["content-disposition"]
