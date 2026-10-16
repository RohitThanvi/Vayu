import asyncio

import httpx
import pytest

from app.services.intel import sec_company as sec, universe


def run(c):
    return asyncio.run(c)


def fact(end, val, start=None, form="10-K", fp="FY", filed=None, accn="0000000000-24-000001"):
    d = {"end": end, "val": val, "form": form, "fp": fp, "fy": int(end[:4]), "filed": filed or f"{int(end[:4]) + 1}-02-01", "accn": accn}
    if start:
        d["start"] = start
    return d


FACTS = {"cik": 1234, "entityName": "Example Motors Corp", "facts": {"us-gaap": {
    # older tag stops in 2021, newer tag continues: the newer one must win
    "SalesRevenueNet": {"units": {"USD": [fact("2019-12-31", 100, "2019-01-01"), fact("2020-12-31", 110, "2020-01-01"), fact("2021-12-31", 120, "2021-01-01")]}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
        fact("2021-12-31", 120, "2021-01-01"), fact("2022-12-31", 150, "2022-01-01"), fact("2023-12-31", 180, "2023-01-01"),
        fact("2023-03-31", 40, "2023-01-01", form="10-Q", fp="Q1"),                  # quarterly: ignored
        fact("2023-12-31", 999, "2023-10-01"),                                        # 'FY' but a 91-day duration: ignored
    ]}},
    "NetIncomeLoss": {"units": {"USD": [fact("2021-12-31", 12, "2021-01-01"), fact("2022-12-31", -5, "2022-01-01"),
                                        fact("2023-12-31", 18, "2023-01-01", filed="2024-02-01"),
                                        fact("2023-12-31", 19, "2023-01-01", filed="2024-04-15", form="10-K/A")]}},   # restatement wins
    "Assets": {"units": {"USD": [fact("2022-12-31", 500), fact("2023-12-31", 560)]}},                                  # balance item: no start
    "EarningsPerShareDiluted": {"units": {"USD/shares": [fact("2023-12-31", 1.25, "2023-01-01")]}},
}}}


def test_annual_series_picks_best_tag_ignores_quarters_and_short_periods_and_prefers_restatements():
    rev = sec.annual_series(FACTS, *sec.METRICS["revenue"][:1], "USD", True)
    assert [p["end"] for p in rev] == ["2021-12-31", "2022-12-31", "2023-12-31"] and [p["value"] for p in rev] == [120, 150, 180]
    ni = sec.annual_series(FACTS, ["NetIncomeLoss"], "USD", True)
    assert [p["value"] for p in ni] == [12, -5, 19] and ni[-1]["form"] == "10-K/A"
    assert [p["value"] for p in sec.annual_series(FACTS, ["Assets"], "USD", False)] == [500, 560]
    assert sec.annual_series(FACTS, ["Nope"], "USD", True) == [] and sec.annual_series({}, ["Assets"], "USD", False) == []


def test_series_is_capped_to_most_recent_years():
    many = {"facts": {"us-gaap": {"Assets": {"units": {"USD": [fact(f"{y}-12-31", y) for y in range(2005, 2024)]}}}}}
    s = sec.annual_series(many, ["Assets"], "USD", False, years=8)
    assert len(s) == 8 and s[0]["end"] == "2016-12-31" and s[-1]["end"] == "2023-12-31"


def test_derived_growth_and_margins_use_only_same_period_values():
    out = sec.build_financials(FACTS, 1234, "EXM")
    d = out["derived"]
    assert d["revenue_growth_pct"] == [{"end": "2022-12-31", "pct": 25.0}, {"end": "2023-12-31", "pct": 20.0}]
    assert {"end": "2022-12-31", "pct": -3.3} in d["net_margin_pct"] and {"end": "2023-12-31", "pct": 10.6} in d["net_margin_pct"]
    assert d["operating_margin_pct"] == [] and "operating_income" not in out["series"]            # missing metric: absent, never zero-filled
    assert out["entity"] == "Example Motors Corp" and out["as_of"] == "2024-04-15" and out["source_url"].endswith("CIK=0000001234&type=10-K")
    assert out["series"]["eps_diluted"][0]["value"] == 1.25


def test_company_without_us_gaap_data_says_why():
    out = sec.build_financials({"entityName": "Foreign Co", "facts": {"ifrs-full": {}}}, 9, "FRN")
    assert out["series"] == {} and "foreign private issuers" in out["notes"][0].lower()


def test_parse_ticker_map_both_shapes_and_class_share_spelling():
    a = sec.parse_ticker_map({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, "1": {"cik_str": 1067983, "ticker": "BRK.B"}, "2": {"bad": 1}})
    assert a == {"AAPL": 320193, "BRK-B": 1067983}
    b = sec.parse_ticker_map({"fields": ["cik", "name", "ticker", "exchange"], "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], ["x"], [1, "n", "t"]]})
    assert b["AAPL"] == 320193 and sec.parse_ticker_map(None) == {} and sec.parse_ticker_map({"fields": ["a"], "data": []}) == {}


@pytest.fixture(autouse=True)
def clean():
    sec._facts_cache.clear(); sec._ticker_map.update(at=0.0, map={})
    yield
    sec._facts_cache.clear(); sec._ticker_map.update(at=0.0, map={})


def test_financials_for_sp500_symbol_uses_universe_cik_and_caches(monkeypatch):
    calls = []

    async def get(url, timeout=20):
        calls.append(url)
        return FACTS
    monkeypatch.setattr(sec, "_get_json", get)
    a = run(sec.financials("gm"))
    assert a["available"] and a["series"]["revenue"] and calls == [sec.FACTS_URL.format(cik=universe.by_symbol("GM")["cik"])]
    run(sec.financials("GM"))
    assert len(calls) == 1, "second call is served from cache"


def test_non_us_and_index_symbols_never_hit_the_sec(monkeypatch):
    async def boom(url, timeout=20):
        raise AssertionError("must not call the SEC")
    monkeypatch.setattr(sec, "_get_json", boom)
    for s in ("RELIANCE.NS", "^GSPC", "INR=X", "TCS.BO"):
        out = run(sec.financials(s))
        assert out["available"] is False and out["series"] == {} and "no sec filer" in out["notes"][0].lower()


def test_unknown_us_ticker_resolves_through_sec_ticker_file(monkeypatch):
    async def get(url, timeout=20):
        return {"0": {"cik_str": 4242, "ticker": "ZZTOP", "title": "Zed"}} if "company_tickers" in url else FACTS
    monkeypatch.setattr(sec, "_get_json", get)
    assert run(sec.cik_for_symbol("zztop")) == 4242 and run(sec.cik_for_symbol("NOPE")) is None


def _status(code):
    async def get(url, timeout=20):
        raise httpx.HTTPStatusError("x", request=httpx.Request("GET", url), response=httpx.Response(code))
    return get


@pytest.mark.parametrize("code,needle", [(403, "SEC_USER_AGENT"), (429, "SEC_USER_AGENT"), (404, "no xbrl facts"), (500, "returned an error")])
def test_sec_http_errors_become_actionable_notes(monkeypatch, code, needle):
    monkeypatch.setattr(sec, "_get_json", _status(code))
    out = run(sec.financials("AAPL"))
    assert out["available"] is False and needle.lower() in out["notes"][0].lower() and "Traceback" not in str(out)
    assert not sec._facts_cache, "failures are never cached"


def test_network_failure_is_friendly(monkeypatch):
    async def get(url, timeout=20):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(sec, "_get_json", get)
    assert "could not be reached" in run(sec.financials("AAPL"))["notes"][0]


def test_endpoint_validation_and_shape(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    async def get(url, timeout=20):
        return FACTS
    monkeypatch.setattr(sec, "_get_json", get)
    c = TestClient(app)
    assert c.get("/api/v1/intel/company/financials").status_code == 422
    assert c.get("/api/v1/intel/company/financials", params={"symbol": "bad symbol!"}).status_code == 422
    r = c.get("/api/v1/intel/company/financials", params={"symbol": "GM"})
    assert r.status_code == 200 and r.json()["series"]["revenue"][-1]["value"] == 180
