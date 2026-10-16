import asyncio

import pytest

from app.services.intel import universe as u


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def fresh():
    u.reset_for_tests()
    yield
    u.reset_for_tests()


def test_universe_loads_sp500_plus_vayu_curated_without_duplicates():
    rows = u.load()
    syms = [r["symbol"] for r in rows]
    assert len(syms) == len(set(syms)) and len(rows) >= 500
    assert {"AAPL", "GM", "^GSPC", "^NSEI", "RELIANCE.NS", "INR=X"} <= set(syms)
    gm = u.by_symbol("gm")
    assert gm["name"] == "General Motors" and gm["cik"] and gm["sector"] and gm["source"] == "S&P 500"
    assert u.by_symbol("RELIANCE.NS")["source"] == "Vayu curated"


def test_yahoo_spelling_for_class_shares():
    assert u.yahoo_symbol("BRK.B") == "BRK-B" and u.by_symbol("BRK-B") and not u.by_symbol("BRK.B")


def test_local_search_ranks_exact_then_prefix_then_name():
    assert u.search_local("gm")[0]["symbol"] == "GM"
    assert [r["symbol"] for r in u.search_local("general mot")][:1] == ["GM"]
    r = u.search_local("nifty")
    assert r and all(x["kind"] == "index" for x in r[:3])
    assert u.search_local("") == [] and u.search_local("zzzzqqqq") == []
    assert len(u.search_local("a", limit=5)) == 5


def test_name_matching_is_conservative():
    assert u.by_name("Walmart Inc.")["symbol"] == "WMT" and u.by_name("WALMART INC")["symbol"] == "WMT"
    assert u.by_name("Apple Hospitality REIT") is None            # similar words must never resolve to AAPL
    assert u.by_name("") is None and u.by_cik(320193)["symbol"] == "AAPL" and u.by_cik(1) is None


def test_parse_yahoo_search_filters_junk_and_maps_kinds():
    data = {"quotes": [{"symbol": "TCS.NS", "longname": "Tata Consultancy Services Limited", "quoteType": "EQUITY", "exchDisp": "NSE"},
                       {"symbol": "bad symbol!", "quoteType": "EQUITY"}, {"symbol": "XYZ", "quoteType": "OPTION"},
                       {"symbol": "^NSEI", "shortname": "NIFTY 50", "quoteType": "INDEX"}, {"quoteType": "EQUITY"}]}
    out = u.parse_yahoo_search(data)
    assert [r["symbol"] for r in out] == ["TCS.NS", "^NSEI"] and out[0]["exchange"] == "NSE" and out[0]["source"] == "Yahoo search" and out[1]["kind"] == "index"
    assert u.parse_yahoo_search({}) == [] and u.parse_yahoo_search(None) == []


def test_search_merges_local_first_then_remote_without_duplicates(monkeypatch):
    async def remote(q, limit=8):
        return [{"symbol": "GM", "name": "dup", "kind": "equity", "source": "Yahoo search"}, {"symbol": "GMDX", "name": "Other", "kind": "equity", "source": "Yahoo search"}]
    monkeypatch.setattr(u, "search_yahoo", remote)
    out = run(u.search("gm"))
    syms = [r["symbol"] for r in out["results"]]
    assert syms[0] == "GM" and "GMDX" in syms and syms.count("GM") == 1 and out["remote_ok"] is True


def test_remote_failure_never_breaks_local_results_and_is_not_cached(monkeypatch):
    calls = {"n": 0}

    async def down(q, limit=8):
        calls["n"] += 1
        return []
    monkeypatch.setattr(u, "search_yahoo", down)
    a = run(u.search("tata"))                       # no local 'tata' -> remote needed, fails
    assert a["results"] == [] and a["remote_ok"] is False
    run(u.search("tata"))
    assert calls["n"] == 2, "a failed lookup must be retried, not cached"
    b = run(u.search("apple"))
    assert b["results"] and b["results"][0]["symbol"] == "AAPL"


def test_endpoint_validates_and_returns(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    async def none(q, limit=8):
        return []
    monkeypatch.setattr(u, "search_yahoo", none)
    c = TestClient(app)
    assert c.get("/api/v1/intel/symbol-search").status_code == 422
    assert c.get("/api/v1/intel/symbol-search", params={"q": "x" * 80}).status_code == 422
    r = c.get("/api/v1/intel/symbol-search", params={"q": "general motors"})
    assert r.status_code == 200 and r.json()["results"][0]["symbol"] == "GM"
