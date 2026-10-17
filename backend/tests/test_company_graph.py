import asyncio

import pytest

from app.services.intel import company_graph as cg, sec_company


def run(c):
    return asyncio.run(c)


# ── text cleaning ────────────────────────────────────────────────────────────
def test_clean_html_strips_tags_scripts_entities_and_inline_xbrl():
    raw = '<html><head><style>p{color:red}</style><script>var x=1</script></head><body><ix:header>HIDDEN</ix:header><p>Walmart&nbsp;Inc. accounted for <ix:nonFraction>25</ix:nonFraction>%&amp; more</p></body></html>'
    t = cg.clean_html(raw)
    assert "HIDDEN" not in t and "color:red" not in t and "var x" not in t
    assert t == "Walmart Inc. accounted for 25 %& more".replace("25 %", "25 %")


# ── customer extraction (centre company's own 10-K) ──────────────────────────
def names(text):
    return [(e["name"], e["pct"], e["anonymous"]) for e in cg.extract_concentration(text)]


def test_named_customers_in_common_disclosure_phrasings():
    t = ("Our top customers drive results. Walmart Inc. accounted for approximately 25% of our net sales in fiscal 2024. "
         "In fiscal 2023, Apple Inc. (\"Apple\"), our largest customer, accounted for 43% of net sales. "
         "Sales to Ford Motor Company represented 18.5% of total revenue. Our largest customer, Costco Wholesale Corporation, accounted for 21% of net sales.")
    got = names(t)
    assert ("Walmart Inc.", 25.0, False) in got and ("Apple Inc.", 43.0, False) in got
    assert ("Ford Motor Company", 18.5, False) in got and ("Costco Wholesale Corporation", 21.0, False) in got and len(got) == 4


def test_anonymised_customers_are_kept_but_marked():
    t = "Two customers matter. Customer A accounted for 14% of total revenue, and Customer B represented 11% of revenue."
    assert names(t) == [("Customer A", 14.0, True), ("Customer B", 11.0, True)]


def test_segment_splits_and_generic_phrases_are_not_customers():
    assert names("Automotive accounted for 80% of revenues. Services represented 20% of revenues.") == []        # no 'customer' nearby
    assert names("No single customer accounted for more than 10% of sales. We had one customer that accounted for 12% of revenue.") == []
    assert names("Our customers are diverse. Sales were 30% of net sales in Europe.") == []


def test_extraction_attaches_a_short_evidence_snippet_and_handles_empty_input():
    e = cg.extract_concentration("Intro words " * 30 + "Walmart Inc. accounted for 25% of our net sales, our largest customer. " + "tail words " * 40)[0]
    assert "Walmart Inc. accounted for 25% of our net sales" in e["snippet"] and len(e["snippet"]) <= 420
    assert cg.extract_concentration("") == [] and cg.extract_concentration(None) == []


# ── supplier detection (other companies' 10-Ks naming the centre company) ────
AL = cg.alias_forms(["General Motors Co", None, ""])


def test_alias_forms_adds_normalised_name_and_skips_junk():
    assert AL == ["General Motors Co", "general motors"] and cg.alias_forms(["GM", "Ab"]) == []


def test_supplier_filing_naming_the_company_as_a_major_customer():
    t = "We supply seat hardware. General Motors Company accounted for approximately 14% of our net sales in 2023, and our customers are concentrated."
    m = cg.find_mentions(t, AL)
    assert m["pct"] == 14.0 and "General Motors Company accounted for approximately 14%" in m["snippet"]


def test_defined_abbreviation_is_followed():
    t = ("Our customers include General Motors Company (\u201cGM\u201d) and others. " + "Filler text. " * 40 +
         "Sales to GM represented 16% of net sales, and GM is our largest customer.")
    m = cg.find_mentions(t, AL)
    assert m["pct"] == 16.0


def test_competitor_or_unrelated_mentions_are_not_edges():
    assert cg.find_mentions("We compete with General Motors and other automakers in several markets.", AL) is None
    assert cg.find_mentions("Our customers include General Motors, Ford and Toyota, among many others.", AL) is None     # named, but no revenue share
    far = "General Motors is mentioned here. " + "x " * 400 + " one customer accounted for 14% of net sales."
    assert cg.find_mentions(far, AL) is None
    assert cg.find_mentions("", AL) is None


# ── EDGAR full-text hits ─────────────────────────────────────────────────────
HITS = {"hits": {"hits": [
    {"_id": "0000933034-24-000010:strt-20240630.htm", "_source": {"ciks": ["0000933034"], "adsh": "0000933034-24-000010", "display_names": ["STRATTEC SECURITY CORP  (STRT)  (CIK 0000933034)"], "file_date": "2024-08-30"}},
    {"_id": "0000933034-23-000009:strt-20230630.htm", "_source": {"ciks": ["0000933034"], "adsh": "0000933034-23-000009", "display_names": ["STRATTEC SECURITY CORP  (STRT)  (CIK 0000933034)"], "file_date": "2023-08-30"}},   # same filer: dedupe
    {"_id": "0001467858-24-000001:gm.htm", "_source": {"ciks": ["0001467858"], "display_names": ["General Motors Co  (GM)  (CIK 0001467858)"], "file_date": "2024-01-30"}},                      # the centre itself: excluded
    {"_id": "badid", "_source": {"ciks": ["1"], "display_names": ["X"]}},
    {"_id": "0000999999-24-000002:x.htm", "_source": {"ciks": [], "display_names": ["No CIK Co"]}},
    {"_id": "0000888888-24-000003:sup.htm", "_source": {"ciks": ["888888"], "display_names": ["Private-ish Supplier Inc  (CIK 0000888888)"], "file_date": "2024-03-01"}},
]}}


def test_parse_efts_hits_dedupes_excludes_centre_and_builds_archive_urls():
    out = cg.parse_efts_hits(HITS, exclude_cik=1467858)
    assert [h["cik"] for h in out] == [933034, 888888]
    assert out[0]["name"] == "STRATTEC SECURITY CORP" and out[0]["ticker"] == "STRT" and out[1]["ticker"] is None
    assert out[0]["url"] == "https://www.sec.gov/Archives/edgar/data/933034/000093303424000010/strt-20240630.htm"
    assert cg.parse_efts_hits({}, None) == [] and cg.parse_efts_hits(None, None) == [] and len(cg.parse_efts_hits(HITS, 1, limit=1)) == 1


# ── building the graph with the network replaced ─────────────────────────────
OWN_10K = ("Our customers are retailers and dealers. Walmart Inc. accounted for 25% of our net sales. Customer A accounted for 12% of revenue. "
           "Automotive accounted for 80% of revenues.")
SUPPLIER_DOC = "We make hardware. General Motors Company accounted for 14% of our net sales this year; our customers are concentrated."
NO_MENTION_DOC = "We compete with General Motors Company in some markets. Our customers vary widely."


@pytest.fixture
def net(monkeypatch):
    cg._cache.clear()
    state = {"fail_urls": set(), "fetched": []}

    async def cik(sym):
        return 1467858 if sym == "GM" else None

    async def latest(c):
        return {"cik": c, "accession": "0001467858-25-000001", "filed": "2025-01-31", "entity": "General Motors Co", "url": "https://sec.example/gm10k.htm"}

    async def efts(name, start, end):
        return HITS

    async def fetch(url):
        state["fetched"].append(url)
        if url in state["fail_urls"]:
            return None
        return {"https://sec.example/gm10k.htm": OWN_10K}.get(url, SUPPLIER_DOC if "strt" in url else NO_MENTION_DOC)
    monkeypatch.setattr(cg, "cik_for_symbol", cik); monkeypatch.setattr(cg, "latest_10k", latest)
    monkeypatch.setattr(cg, "efts_search", efts); monkeypatch.setattr(cg, "fetch_text", fetch)
    yield state
    cg._cache.clear()


def test_graph_has_customers_on_the_right_suppliers_on_the_left_with_provenance(net):
    g = run(cg.relationships("gm"))
    assert g["center"]["symbol"] == "GM" and g["center"]["name"] == "General Motors" and g["partial"] is False
    cust = {c["name"]: c for c in g["customers"]}
    assert cust["Walmart Inc."]["symbol"] == "WMT" and cust["Walmart Inc."]["pct"] == 25.0 and cust["Walmart Inc."]["source_url"] == "https://sec.example/gm10k.htm"
    assert "Customer A (undisclosed)" in cust and cust["Customer A (undisclosed)"]["symbol"] is None and cust["Customer A (undisclosed)"]["anonymous"]
    assert "Automotive" not in cust
    assert [c["pct"] for c in g["customers"]] == [25.0, 12.0], "strongest relationship first"
    assert len(g["suppliers"]) == 1
    s = g["suppliers"][0]
    assert s["name"] == "STRATTEC SECURITY CORP" and s["symbol"] == "STRT" and s["pct"] == 14.0 and s["filed"] == "2024-08-30" and s["confidence"] == "extracted"
    assert s["source_url"].endswith("strt-20240630.htm") and "verify" in s["source_label"].lower() and "General Motors Company accounted for" in s["evidence"]
    assert any("missing relationship" in n for n in g["notes"])


def test_unreadable_documents_mark_the_result_partial_and_are_not_cached(net):
    net["fail_urls"].add("https://www.sec.gov/Archives/edgar/data/933034/000093303424000010/strt-20240630.htm")
    g = run(cg.relationships("GM"))
    assert g["partial"] is True and "incomplete" in g["notes"][0] and g["suppliers"] == [] and g["customers"]
    net["fail_urls"].clear()
    n = len(net["fetched"])
    g2 = run(cg.relationships("GM"))
    assert len(net["fetched"]) > n and g2["partial"] is False and len(g2["suppliers"]) == 1, "partial results are retried, not cached"
    n = len(net["fetched"])
    run(cg.relationships("GM"))
    assert len(net["fetched"]) == n, "a complete result is cached"


def test_search_failure_still_returns_customers_and_says_partial(net, monkeypatch):
    async def boom(name, start, end):
        raise RuntimeError("down")
    monkeypatch.setattr(cg, "efts_search", boom)
    g = run(cg.relationships("GM"))
    assert g["partial"] and g["customers"] and g["suppliers"] == []


def test_non_sec_symbols_return_an_honest_empty_graph_without_network(net):
    g = run(cg.relationships("RELIANCE.NS"))
    assert g["customers"] == [] and g["suppliers"] == [] and "No SEC filer" in g["notes"][0] and net["fetched"] == []
    assert g["center"]["name"] == "Reliance Industries"


def test_exposures_come_only_from_the_published_bellwether_mapping(net):
    xom = cg.exposures_for("XOM")
    assert xom and all("not the company's disclosure" in e["basis"] for e in xom) and cg.exposures_for("NOPE") == []
    assert run(cg.relationships("XOM"))["exposures"] == xom, "exposures appear even where no SEC graph can be built"


def test_endpoint(net):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/v1/intel/company/relationships").status_code == 422
    assert c.get("/api/v1/intel/company/relationships", params={"symbol": "no way!"}).status_code == 422
    r = c.get("/api/v1/intel/company/relationships", params={"symbol": "GM"})
    assert r.status_code == 200 and r.json()["suppliers"][0]["symbol"] == "STRT"


def test_generic_labels_are_not_customers_even_next_to_customer_language():
    t = "Customer A accounted for 12% of revenue. Automotive accounted for 80% of revenues. North America accounted for 60% of revenue. Walmart accounted for 25% of net sales."
    assert [(n, p) for n, p, a in names(t)] == [("Customer A", 12.0), ("Walmart", 25.0)]


def test_supplier_share_must_be_in_the_same_sentence_as_the_company_name():
    sep = ("We compete with General Motors Company. Our customers include many OEMs. One customer accounted for 14% of our net sales.")
    assert cg.find_mentions(sep, AL) is None, "a percentage in a different sentence must not be attributed to the named company"
    same = "Our customers are OEMs. General Motors Company accounted for 14% of our net sales."
    assert cg.find_mentions(same, AL)["pct"] == 14.0
    assert cg._sentence_span("Walmart Inc. accounted for 25% of sales. Next sentence.", 0, 12)[1] == len("Walmart Inc. accounted for 25% of sales.")
