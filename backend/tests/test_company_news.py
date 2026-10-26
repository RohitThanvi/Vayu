import asyncio

import httpx
import pytest

from app.services.intel import company_news as cn


def run(c):
    return asyncio.run(c)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cn._news_cache.clear(); cn._filings_cache.clear()
    cn._pace.update(last=0.0, waiting=0, lock=None, loop=None)
    yield
    cn._news_cache.clear(); cn._filings_cache.clear()


# ── query ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,clean_", [("Apple Inc.", "Apple"), ("Alphabet Inc. (Class A)", "Alphabet"), ("General Motors", "General Motors"), ("Ford Motor Company", "Ford Motor"),
                                         ("3M Company", "3M"), ("Reliance Industries Limited", "Reliance Industries"), ("Berkshire Hathaway", "Berkshire Hathaway"),
                                         ('He said "hi" Corp', "He said hi"), ("Coca-Cola Co", "Coca-Cola"), ("", ""), (None, "")])
def test_company_name_cleaning(raw, clean_):
    assert cn.clean_company_name(raw) == clean_


def test_query_quotes_the_name_and_adds_context_only_for_short_single_words():
    assert cn.build_query("General Motors Co.") == '"General Motors"'
    assert cn.build_query("Apple Inc.") == '"Apple" (stock OR shares OR earnings OR company)'
    assert cn.build_query("Tata Consultancy Services Limited") == '"Tata Consultancy Services"'
    assert cn.build_query("") is None and cn.build_query("X") is None and cn.build_query(None) is None
    assert cn.build_query('A"B C').count('"') == 2, "quotes inside a name can never break out of the quoted phrase"


def test_name_resolution_prefers_the_universe_over_client_input():
    assert cn.resolve_name("GM", "Some Spoofed Name") == "General Motors"
    assert cn.resolve_name("ZZUNKNOWN", "  Acme Corp ") == "Acme Corp" and cn.resolve_name("ZZUNKNOWN", None) is None and cn.resolve_name("ZZUNKNOWN", "   ") is None
    assert len(cn.resolve_name("ZZUNKNOWN", "x" * 200)) == cn.MAX_NAME_LEN


# ── parsing ──────────────────────────────────────────────────────────────────
def art(title, url="https://example.com/a", lang="English", seen="20260319T083000Z", domain="example.com", country="United States"):
    return {"title": title, "url": url, "language": lang, "seendate": seen, "domain": domain, "sourcecountry": country}


def test_parse_articles_dedupes_syndication_drops_unsafe_and_non_english_and_sorts_newest_first():
    raw = [art("GM recalls 50,000 trucks", "https://a.com/1", seen="20260319T080000Z"), art("GM Recalls 50,000 trucks!", "https://b.com/2", seen="20260319T090000Z"),
           art("GM CEO speaks", "https://c.com/3", seen="20260320T010000Z"), art("Unsafe", "javascript:alert(1)"), art("Plain http", "http://d.com/x"), art("", "https://e.com/x"),
           art("GM in Spanish", "https://f.com/x", lang="Spanish"), art("Bad date", "https://g.com/x", seen="garbage"), {"title": "No url"}, None]
    out = cn.parse_articles(raw)
    assert [a["title"] for a in out] == ["GM CEO speaks", "GM recalls 50,000 trucks", "Bad date"], "newest first, the first copy of a syndicated headline wins, undated last"
    assert out[1]["seen"] == "2026-03-19T08:00:00Z"
    assert out[0]["seen"] == "2026-03-20T01:00:00Z" and next(a for a in out if a["title"] == "Bad date")["seen"] is None
    assert all(a["url"].startswith("https://") for a in out) and not any("Spanish" in a["title"] for a in out)
    assert any("Spanish" in a["title"] for a in cn.parse_articles(raw, english_only=False)) and len(cn.parse_articles([art(f"T{i}", f"https://x.com/{i}") for i in range(50)], limit=20)) == 20
    assert cn.parse_articles(None) == [] and cn.parse_articles([]) == []


# ── pacing ───────────────────────────────────────────────────────────────────
def test_pacer_spaces_requests_and_refuses_when_the_queue_is_too_long(monkeypatch):
    clock = {"t": 100.0}
    slept = []

    async def sleep(s):
        slept.append(round(s, 2)); clock["t"] += s
    monkeypatch.setattr(cn, "_now", lambda: clock["t"]); monkeypatch.setattr(cn, "_sleep", sleep)

    async def go():
        await cn._paced(); await cn._paced()
        clock["t"] += 20                         # long idle: no wait needed
        await cn._paced()
    run(go())
    assert slept == [5.5], "only the back-to-back call waits, and for the full gap"

    async def crowded():
        await cn._paced()
        cn._pace["waiting"] = 5
        with pytest.raises(cn.Busy):
            await cn._paced()
        cn._pace["waiting"] = 4
        await cn._paced()                        # one below the limit still gets its turn
    run(crowded())


# ── the HTTP call ────────────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status=200, text="", body=None):
        self.status_code, self.text, self._b = status, text, body

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "u"), response=httpx.Response(self.status_code))


def fake_client(monkeypatch, resp, seen):
    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            seen.update(url=url, params=params)
            return resp
    monkeypatch.setattr(cn.httpx, "AsyncClient", C)

    async def nopace():
        pass
    monkeypatch.setattr(cn, "_paced", nopace)


def test_gdelt_request_shape_and_error_modes(monkeypatch):
    seen = {}
    fake_client(monkeypatch, FakeResp(body={"articles": [art("x")]}), seen)
    assert run(cn._gdelt('"General Motors"', "7d"))[0]["title"] == "x"
    assert seen["url"] == "https://api.gdeltproject.org/api/v2/doc/doc"
    assert seen["params"] == {"query": '"General Motors"', "mode": "ArtList", "format": "json", "sort": "DateDesc", "timespan": "7d", "maxrecords": 75}
    fake_client(monkeypatch, FakeResp(status=429, text="Please limit requests to one every 5 seconds."), {})
    with pytest.raises(cn.RateLimited):
        run(cn._gdelt("q", "7d"))
    fake_client(monkeypatch, FakeResp(status=200, text="Please limit requests to one every 5 seconds or contact"), {})
    with pytest.raises(cn.RateLimited):
        run(cn._gdelt("q", "7d"))
    fake_client(monkeypatch, FakeResp(status=200, text="Your query was too short or contained invalid characters"), {})
    with pytest.raises(cn.BadResponse):
        run(cn._gdelt("q", "7d"))
    fake_client(monkeypatch, FakeResp(status=500, text="boom"), {})
    with pytest.raises(httpx.HTTPStatusError):
        run(cn._gdelt("q", "7d"))
    fake_client(monkeypatch, FakeResp(body={}), {})
    assert run(cn._gdelt("q", "7d")) == []


# ── news flow ────────────────────────────────────────────────────────────────
@pytest.fixture
def gd(monkeypatch):
    st = {"calls": [], "behaviour": [art("GM unveils EV", "https://a.com/1")], "slept": []}

    async def gdelt(q, ts):
        st["calls"].append((q, ts))
        b = st["behaviour"]
        if isinstance(b, Exception):
            raise b
        if callable(b):
            return b(len(st["calls"]))
        return b

    async def sleep(s):
        st["slept"].append(s)
    monkeypatch.setattr(cn, "_gdelt", gdelt); monkeypatch.setattr(cn, "_sleep", sleep)
    return st


def test_news_success_is_cached_and_not_refetched(gd):
    r = run(cn.news("gm", None, "7d"))
    assert r["company"] == "General Motors" and r["query"] == '"General Motors"' and r["articles"][0]["title"] == "GM unveils EV" and r["stale"] is False and r["source"]["name"] == "GDELT DOC 2.0"
    run(cn.news("GM", "ignored", "7d"))
    assert len(gd["calls"]) == 1, "second lookup within 30 minutes costs no GDELT request"
    run(cn.news("GM", None, "30d"))
    assert len(gd["calls"]) == 2 and gd["calls"][1][1] == "30d", "a different window is a different query"


def test_news_retries_once_after_a_throttle_then_succeeds(gd):
    gd["behaviour"] = lambda n: (_ for _ in ()).throw(cn.RateLimited()) if n == 1 else [art("ok", "https://a.com/1")]
    r = run(cn.news("GM"))
    assert r["articles"][0]["title"] == "ok" and gd["slept"] == [6] and len(gd["calls"]) == 2


def test_throttled_twice_is_not_cached_and_says_so(gd):
    gd["behaviour"] = cn.RateLimited()
    r = run(cn.news("GM"))
    assert r["articles"] == [] and "throttling" in r["notes"][0] and len(gd["calls"]) == 2 and not cn._news_cache


def test_stale_result_is_served_when_the_source_fails(gd):
    run(cn.news("GM"))
    key = next(iter(cn._news_cache))
    t, v = cn._news_cache[key]
    cn._news_cache[key] = (t - cn.NEWS_TTL_S - 5, v)            # expire it
    gd["behaviour"] = RuntimeError("down")
    r = run(cn.news("GM"))
    assert r["stale"] is True and r["articles"][0]["title"] == "GM unveils EV" and "last result" in r["notes"][0]
    cn._news_cache[key] = (t - cn.STALE_OK_S - 5, v)            # too old even for stale
    r2 = run(cn.news("GM"))
    assert r2["stale"] is False and r2["articles"] == [] and "could not be reached" in r2["notes"][0]


@pytest.mark.parametrize("exc,needle", [(cn.Busy(), "queued"), (cn.BadResponse("x"), "could not parse"), (RuntimeError("x"), "could not be reached")])
def test_news_failure_notes_are_plain(gd, exc, needle):
    gd["behaviour"] = exc
    r = run(cn.news("GM"))
    assert needle in r["notes"][0] and r["articles"] == [] and "Traceback" not in str(r)


def test_news_without_a_name_or_with_a_bad_window(gd):
    r = run(cn.news("ZZUNKNOWN", None))
    assert r["articles"] == [] and "No company name" in r["notes"][0] and gd["calls"] == []
    with pytest.raises(ValueError):
        run(cn.news("GM", None, "1y"))
    gd["behaviour"] = []
    assert "No English-language articles" in run(cn.news("GM"))["notes"][0]


# ── filings ──────────────────────────────────────────────────────────────────
SUB = {"name": "General Motors Co", "filings": {"recent": {
    "form": ["8-K", "4", "10-Q", "8-K", "10-K", "SC 13G", "8-K/A"],
    "filingDate": ["2026-02-03", "2026-02-02", "2026-01-30", "2026-01-15", "2026-01-31", "2026-01-10", "2026-01-05"],
    "accessionNumber": ["0001467858-26-000010", "0001467858-26-000009", "0001467858-26-000008", "0001467858-26-000007", "0001467858-26-000006", "x", "0001467858-26-000005"],
    "primaryDocument": ["gm-8k.htm", "form4.xml", "gm-10q.htm", "", "gm-10k.htm", "x", "gm-8ka.htm"],
    "items": ["2.02,9.01", "", "", ["5.02", "7.01"], "", "", "8.01,9.99"]}}}


def test_parse_filings_keeps_reports_labels_8k_items_and_builds_links():
    f = cn.parse_filings(SUB, 1467858)
    assert [x["form"] for x in f] == ["8-K", "10-Q", "8-K", "10-K", "8-K/A"], "Form 4 and 13G are filtered out as noise"
    assert f[0]["label"] == "Current report" and f[0]["items"] == [{"code": "2.02", "label": "Results of operations (earnings)"}], "the routine exhibits item (9.01) is dropped when others exist"
    assert f[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1467858/000146785826000010/gm-8k.htm"
    assert [i["code"] for i in f[2]["items"]] == ["5.02", "7.01"] and f[2]["url"] is None, "no primary document => no link, never a broken one"
    assert f[4]["items"][1] == {"code": "9.99", "label": "Item 9.99"}, "an unknown item code is shown as-is, not invented"
    assert len(cn.parse_filings(SUB, 1, limit=2)) == 2 and cn.parse_filings({}, 1) == [] and cn.parse_filings(None, 1) == []


def test_filings_flow_and_failures(monkeypatch):
    calls = []

    async def getj(url, params=None):
        calls.append(url)
        return SUB
    monkeypatch.setattr(cn, "_get_json", getj)
    a = run(cn.filings("gm"))
    assert a["available"] and a["entity"] == "General Motors Co" and len(a["filings"]) == 5
    run(cn.filings("GM"))
    assert len(calls) == 1, "cached for 30 minutes"
    na = run(cn.filings("RELIANCE.NS"))
    assert na["available"] is False and na["filings"] == [] and "No SEC filer" in na["notes"][0] and len(calls) == 1

    def err(code):
        async def g(url, params=None):
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", url), response=httpx.Response(code))
        return g
    cn._filings_cache.clear()
    monkeypatch.setattr(cn, "_get_json", err(403))
    assert "SEC_USER_AGENT" in run(cn.filings("AAPL"))["notes"][0]
    monkeypatch.setattr(cn, "_get_json", err(500))
    assert "returned an error" in run(cn.filings("AAPL"))["notes"][0]

    async def down(url, params=None):
        raise httpx.ConnectError("x")
    monkeypatch.setattr(cn, "_get_json", down)
    assert "could not be reached" in run(cn.filings("AAPL"))["notes"][0] and not cn._filings_cache

    async def empty(url, params=None):
        return {"name": "X", "filings": {"recent": {}}}
    monkeypatch.setattr(cn, "_get_json", empty)
    assert "No recent annual" in run(cn.filings("AAPL"))["notes"][0]


# ── HTTP layer ───────────────────────────────────────────────────────────────
def test_endpoints(gd, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    base = "/api/v1/intel/company"
    assert c.get(base + "/news").status_code == 422
    assert c.get(base + "/news", params={"symbol": "bad sym!"}).status_code == 422
    assert c.get(base + "/news", params={"symbol": "GM", "timespan": "1y"}).status_code == 422
    assert c.get(base + "/news", params={"symbol": "GM", "name": "x" * 81}).status_code == 422
    r = c.get(base + "/news", params={"symbol": "GM", "timespan": "3d"})
    assert r.status_code == 200 and r.json()["articles"][0]["title"] == "GM unveils EV" and gd["calls"][-1] == ('"General Motors"', "3d")
    r = c.get(base + "/news", params={"symbol": "ZZUNKNOWN", "name": "Acme\x00 Corp\x07"})
    assert r.status_code == 200 and r.json()["company"] == "Acme"                     # control characters stripped; suffix dropped

    async def getj(url, params=None):
        return SUB
    monkeypatch.setattr(cn, "_get_json", getj)
    assert c.get(base + "/filings", params={"symbol": "GM"}).json()["filings"][0]["form"] == "8-K"
    assert c.get(base + "/filings", params={"symbol": "bad sym!"}).status_code == 422
