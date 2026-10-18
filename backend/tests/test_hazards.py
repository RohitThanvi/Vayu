import asyncio
import datetime as dt
import json

import httpx
import pytest

from app.services.intel import hazards as hz
from app.services.intel.vessel_store import CHOKEPOINTS


def run(c):
    return asyncio.run(c)


def feat(eid=1102983, etype="FL", alert="Orange", iso3="ESP", country="Spain", frm="2024-10-27T00:00:00", to="2024-11-04T00:00:00", current="false", lon=-0.4, lat=39.5,
         sev=("2", "Flood severity 2", "")):
    p = {"eventid": eid, "eventname": "Flood in Spain", "alertlevel": alert, "episodealertlevel": alert, "iso3": iso3, "country": country, "fromdate": frm, "todate": to,
         "iscurrent": current, "severitydata": {"severity": sev[0], "severitytext": sev[1], "severityunit": sev[2]}, "source": "GLOFAS"}
    if etype:
        p["eventtype"] = etype
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": p}


# ── GDACS parsing ────────────────────────────────────────────────────────────
def test_parse_feature_normalises_fields_and_builds_report_link():
    e = hz.parse_gdacs_feature(feat())
    assert (e["type"], e["kind"], e["event_id"], e["alert"], e["iso3"]) == ("FL", "flood", 1102983, "orange", ["ESP"])
    assert (e["lat"], e["lon"]) == (39.5, -0.4) and e["from"] == dt.datetime(2024, 10, 27) and e["current"] is False
    assert e["severity_text"] == "Flood severity 2" and e["url"] == "https://www.gdacs.org/report.aspx?eventtype=FL&eventid=1102983"


def test_parse_feature_edge_cases():
    assert hz.parse_gdacs_feature(feat(etype=None), requested_type="TC")["type"] == "TC"          # type taken from the query when the feature omits it
    assert hz.parse_gdacs_feature(feat(etype=None)) is None and hz.parse_gdacs_feature(feat(etype="ZZ")) is None
    bad = feat(); bad["properties"]["eventid"] = "x"
    assert hz.parse_gdacs_feature(bad) is None and hz.parse_gdacs_feature(None) is None and hz.parse_gdacs_feature({}) is None
    assert hz.parse_gdacs_feature(feat(iso3="IND,BGD, xx"))["iso3"] == ["IND", "BGD"], "multi-country events keep every valid ISO3 code"
    assert hz.parse_gdacs_feature(feat(alert="purple"))["alert"] == "green", "an unknown alert level is never promoted to a warning"
    poly = feat(); poly["geometry"] = {"type": "Polygon", "coordinates": [[[10, 20], [12, 20], [12, 22], [10, 22]]]}
    assert (hz.parse_gdacs_feature(poly)["lat"], hz.parse_gdacs_feature(poly)["lon"]) == (21.0, 11.0)
    nogeo = feat(); nogeo["geometry"] = None
    assert hz.parse_gdacs_feature(nogeo)["lat"] is None
    outside = feat(lat=123, lon=0)
    assert hz.parse_gdacs_feature(outside)["lat"] is None
    withurl = feat(); withurl["properties"]["url"] = {"report": "https://www.gdacs.org/custom"}
    assert hz.parse_gdacs_feature(withurl)["url"] == "https://www.gdacs.org/custom"
    evil = feat(); evil["properties"]["url"] = {"report": "javascript:alert(1)"}
    assert hz.parse_gdacs_feature(evil)["url"].startswith("https://www.gdacs.org/report.aspx")


# ── GDACS fetching (client replaced) ─────────────────────────────────────────
class FakeResp:
    def __init__(self, body, status=200):
        self.text, self.status_code, self._b = ("" if body is None else json.dumps(body)), status, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", "u"), response=httpx.Response(self.status_code))

    def json(self):
        return self._b


class FakeClient:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    async def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.handler(params)


D0, D1 = dt.date(2024, 10, 1), dt.date(2024, 11, 10)


def test_levels_are_requested_one_at_a_time_and_results_deduped():
    def h(p):
        return FakeResp({"features": [feat(eid=1, alert=p["alertlevel"]), feat(eid=2 if p["alertlevel"] == "Red" else 3, alert=p["alertlevel"])]})
    c = FakeClient(h)
    out = run(hz.fetch_gdacs_parsed(c, ["FL", "TC"], ["Red", "Orange"], D0, D1))
    assert [p["alertlevel"] for p in c.calls] == ["Red", "Orange"] and all(p["eventlist"] == "FL;TC" for p in c.calls)
    assert all(p["fromDate"] == "2024-10-01" and p["toDate"] == "2024-11-10" and p["pageSize"] == 100 for p in c.calls)
    assert sorted(e["event_id"] for e in out) == [1, 2, 3], "event 1 appears under both levels but is kept once"


def test_pagination_stops_at_a_short_page_and_at_an_empty_body():
    pages = {1: [feat(eid=i) for i in range(100)], 2: [feat(eid=1000 + i) for i in range(3)]}
    c = FakeClient(lambda p: FakeResp({"features": pages.get(p["pageNumber"], [])}))
    out = run(hz.fetch_gdacs_parsed(c, ["FL"], ["Red"], D0, D1))
    assert len(out) == 103 and [p["pageNumber"] for p in c.calls] == [1, 2]
    c2 = FakeClient(lambda p: FakeResp(None))
    assert run(hz.fetch_gdacs_parsed(c2, ["FL"], ["Red"], D0, D1)) == [] and len(c2.calls) == 1
    c3 = FakeClient(lambda p: FakeResp({"features": [feat(eid=p["pageNumber"] * 1000 + i) for i in range(100)]}))
    assert len(run(hz.fetch_gdacs_parsed(c3, ["FL"], ["Red"], D0, D1, max_pages=3))) == 300 and len(c3.calls) == 3, "page cap protects the server"


def test_country_filter_matches_any_listed_country():
    c = FakeClient(lambda p: FakeResp({"features": [feat(eid=1, iso3="IND,BGD"), feat(eid=2, iso3="USA"), feat(eid=3, iso3="")]}))
    assert [e["event_id"] for e in run(hz.fetch_gdacs_parsed(c, ["FL"], ["Red"], D0, D1, iso3="BGD"))] == [1]


# ── GDACS -> intel events ────────────────────────────────────────────────────
NOW = dt.datetime(2024, 11, 5, 12, 0, 0)


def test_intel_event_shape_identity_and_timestamp_rules():
    recent = hz.parse_gdacs_feature(feat(frm="2024-11-05T03:00:00", current="true", alert="Red", etype="TC", sev=("185", "Tropical Storm", "km/h")))
    e = hz.gdacs_to_intel_event(recent, NOW)
    assert e["source"] == "GDACS" and e["tag"] == "CYCLONE" and e["severity"] == "critical" and e["ts"] == "2024-11-05T03:00:00Z"
    assert e["uid"] == "GDACS:TC:1102983:red" and e["dedup"] == "uid" and "Red alert" in e["title"] and "Tropical Storm" in e["detail"]
    assert e["meta"]["hazard"] == "cyclone" and e["meta"]["iso3"] == ["ESP"] and e["meta"]["url"].startswith("https://www.gdacs.org/")
    old = hz.gdacs_to_intel_event(hz.parse_gdacs_feature(feat(frm="2024-10-27T00:00:00", current="true")), NOW)
    assert old["ts"] != "2024-10-27T00:00:00Z" and old["ts"].endswith("Z"), "a long-running event is stamped now so the 24 h feed window keeps it"
    esc = hz.gdacs_to_intel_event(hz.parse_gdacs_feature(feat(alert="Red")), NOW)
    assert esc["uid"] != hz.gdacs_to_intel_event(hz.parse_gdacs_feature(feat(alert="Orange")), NOW)["uid"], "an escalation is a new feed item"
    assert hz.gdacs_to_intel_event(hz.parse_gdacs_feature(dict(feat(), geometry=None)), NOW) is None


def test_live_fetch_keeps_current_or_just_ended_and_never_raises(monkeypatch):
    today = dt.datetime.utcnow()
    rows = [hz.parse_gdacs_feature(feat(eid=1, current="true", frm=(today - dt.timedelta(hours=5)).isoformat())),
            hz.parse_gdacs_feature(feat(eid=2, current="false", to=(today - dt.timedelta(days=1)).isoformat())),
            hz.parse_gdacs_feature(feat(eid=3, current="false", to=(today - dt.timedelta(days=6)).isoformat()))]

    async def parsed(client, types, levels, start, end, iso3=None, max_pages=5):
        assert levels == ["Red", "Orange"] and set(types) == {"EQ", "TC", "FL", "VO"} and (end - start).days == 10
        return rows
    monkeypatch.setattr(hz, "fetch_gdacs_parsed", parsed)
    ev = run(hz.fetch_gdacs_events(None))
    assert sorted(int(e["meta"]["event_id"]) for e in ev) == [1, 2]

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(hz, "fetch_gdacs_parsed", boom)
    assert run(hz.fetch_gdacs_events(None)) == []


# ── tsunami.gov Atom ─────────────────────────────────────────────────────────
ATOM = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#" xmlns:georss="http://www.georss.org/georss">
<id>urn:uuid:5527feac</id><title>Tsunami Information Statement Number 1</title><updated>2026-05-10T22:49:19Z</updated>
<author><name>NWS National Tsunami Warning Center Palmer AK</name></author>
<entry><title>40 miles SW of Sandspit, Haida Gwaii</title><updated>2026-05-10T22:49:19Z</updated><geo:lat>53.121</geo:lat><geo:long>-132.808</geo:long>
<id>urn:uuid:e39857d5</id><link rel="related" title="CapXML document" href="https://www.tsunami.gov/events/PAAQ/x/PAAQCAP.xml" type="application/cap+xml" />
<link rel="alternate" title="Bulletin" href="https://www.tsunami.gov/events/PAAQ/x/WEAK53.txt" type="application/xml" /></entry></feed>'''


def test_tsunami_atom_parses_message_location_and_bulletin():
    m = hz.parse_tsunami_atom(ATOM, "NTWC")
    assert len(m) == 1 and m[0]["message"] == "Tsunami Information Statement Number 1" and m[0]["location"].startswith("40 miles SW of Sandspit")
    assert (m[0]["lat"], m[0]["lon"]) == (53.121, -132.808) and m[0]["bulletin"].endswith("WEAK53.txt") and m[0]["updated"] == "2026-05-10T22:49:19Z"
    e = hz.tsunami_to_intel_event(m[0])
    assert e["source"] == "NOAA Tsunami" and e["tag"] == "TSUNAMI" and e["severity"] == "info" and e["ts"] == "2026-05-10T22:49:19Z"
    assert e["uid"] == "NOAA-TSUNAMI:NTWC:urn:uuid:e39857d5" and e["meta"]["url"].endswith("WEAK53.txt") and "authoritative" in e["detail"]


def test_tsunami_atom_robustness():
    assert hz.parse_tsunami_atom("not xml", "PTWC") == []
    assert hz.parse_tsunami_atom('<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title></feed>', "PTWC") == [], "a feed with no entry is simply quiet"
    trailing = ATOM.replace("53.121", "52.").replace("<geo:long>-132.808", "<geo:long>176.1")
    assert hz.parse_tsunami_atom(trailing, "PTWC")[0]["lat"] == 52.0, "tsunami.gov writes '52.' for some latitudes"
    assert hz.parse_tsunami_atom(ATOM.replace("53.121", "north"), "PTWC") == [] and hz.parse_tsunami_atom(ATOM.replace("53.121", "123.0"), "PTWC") == []
    js = ATOM.replace("https://www.tsunami.gov/events/PAAQ/x/WEAK53.txt", "javascript:alert(1)")
    assert hz.parse_tsunami_atom(js, "NTWC")[0]["bulletin"] is None


def test_tsunami_severity_comes_only_from_the_message_type():
    for t, s in [("TSUNAMI WARNING NUMBER 2", "critical"), ("Tsunami Watch", "warn"), ("Tsunami Advisory #1", "warn"), ("TSUNAMI INFORMATION STATEMENT NUMBER 1", "info"),
                 ("Tsunami Threat Message", "info"), ("", "info"), (None, "info")]:
        assert hz.classify_tsunami(t) == s, t


def test_tsunami_fetch_survives_one_centre_failing():
    def h(url_params):
        return None

    class C:
        async def get(self, url, timeout=None):
            if "PHEB" in url:
                raise httpx.ConnectError("down")
            return type("R", (), {"text": ATOM, "raise_for_status": lambda self: None})()
    ev = run(hz.fetch_tsunami_events(C()))
    assert len(ev) == 1 and ev[0]["meta"]["centre"] == "NTWC"


# ── market context ───────────────────────────────────────────────────────────
def test_exposure_lists_nearby_chokepoints_and_home_indices():
    cp, ((la1, lo1), (la2, lo2)) = next(iter(CHOKEPOINTS.items()))
    near = hz.exposure_for((la1 + la2) / 2 + 0.5, (lo1 + lo2) / 2, ["IND", "TWN"])
    assert near["chokepoints"] and near["chokepoints"][0]["chokepoint"] == cp and near["chokepoints"][0]["distance_km"] < 100
    assert [c["symbol"] for c in near["country_indices"]] == ["^NSEI"], "TWN has no index in Vayu's table, so none is invented"
    assert "not a forecast" in near["basis"]
    far = hz.exposure_for(-60.0, -120.0, ["USA"])
    assert far["chokepoints"] == [] and far["country_indices"][0]["symbol"] == "^GSPC"
    none = hz.exposure_for(None, None, None)
    assert none["chokepoints"] == [] and none["country_indices"] == []


# ── history for the event study ──────────────────────────────────────────────
def test_history_dates_validate_input_before_any_network_call():
    for args in (("ZZ", "IND", ["Red"]), ("FL", "in", ["Red"]), ("FL", "IND", ["Purple"]), ("FL", "", ["Red"])):
        with pytest.raises(ValueError):
            run(hz.fetch_history_dates(*args))


def test_history_dates_one_per_day_sorted(monkeypatch):
    rows = [hz.parse_gdacs_feature(feat(eid=1, frm="2024-08-02T00:00:00", alert="Red")), hz.parse_gdacs_feature(feat(eid=2, frm="2024-07-01T00:00:00")),
            hz.parse_gdacs_feature(feat(eid=3, frm="2024-08-02T00:00:00")), dict(hz.parse_gdacs_feature(feat(eid=4)), **{"from": None})]
    seen = {}

    async def parsed(client, types, levels, start, end, iso3=None, max_pages=5):
        seen.update(types=types, levels=levels, iso3=iso3, years=(end - start).days)
        return rows
    monkeypatch.setattr(hz, "fetch_gdacs_parsed", parsed)
    out = run(hz.fetch_history_dates("FL", "IND", ["Red", "Orange"]))
    assert [e["date"] for e in out] == ["2024-07-01", "2024-08-02"] and out[1]["label"].startswith("Flood Flood in Spain") and len(out[1]["label"]) <= 60
    assert seen["types"] == ["FL"] and seen["iso3"] == "IND" and 1815 <= seen["years"] <= 1825


# ── HTTP layer ───────────────────────────────────────────────────────────────
def test_scheduler_has_hazard_pollers():
    from app.services.intel.scheduler import IntelScheduler
    for n in ("_poll_gdacs", "_poll_tsunami"):
        assert hasattr(IntelScheduler, n), n


def test_hazards_endpoint_returns_sorted_events_with_market_context(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import intel_endpoints as ie
    cp, ((la1, lo1), (la2, lo2)) = next(iter(CHOKEPOINTS.items()))
    evs = [{"id": "a", "source": "GDACS", "title": "Flood — x", "detail": "d", "severity": "warn", "ts": "2024-11-01T00:00:00Z", "lat": 10.0, "lon": 10.0,
            "meta": {"hazard": "flood", "alert": "orange", "iso3": ["IND"], "country": "India", "url": "https://www.gdacs.org/r", "current": True, "from": "2024-10-28T00:00:00"}},
           {"id": "b", "source": "GDACS", "title": "Cyclone — y", "detail": "d", "severity": "critical", "ts": "2024-10-01T00:00:00Z", "lat": (la1 + la2) / 2, "lon": (lo1 + lo2) / 2,
            "meta": {"hazard": "cyclone", "alert": "red", "iso3": ["JPN"], "country": "Japan"}},
           {"id": "c", "source": "NOAA Tsunami", "title": "Tsunami Information Statement", "detail": "d", "severity": "info", "ts": "2024-11-02T00:00:00Z", "lat": 53.0, "lon": -132.0,
            "meta": {"hazard": "tsunami", "url": "https://www.tsunami.gov/x"}}]
    asked = {}

    def query(sources=None, limit=100, **k):
        asked.update(sources=sources, limit=limit)
        return evs
    monkeypatch.setattr(ie.intel_store, "query", query)
    r = TestClient(app).get("/api/v1/intel/hazards", params={"limit": 10})
    j = r.json()
    assert r.status_code == 200 and asked == {"sources": ["GDACS", "NOAA Tsunami"], "limit": 10}
    assert [e["id"] for e in j["events"]] == ["b", "a", "c"], "most severe first, then newest"
    b = j["events"][0]
    assert b["exposure"]["chokepoints"][0]["chokepoint"] == cp and b["exposure"]["country_indices"][0]["symbol"] == "^N225"
    assert j["events"][1]["exposure"]["country_indices"][0]["symbol"] == "^NSEI" and j["events"][2]["url"].startswith("https://www.tsunami.gov")
    assert j["events"][1]["from"] == "2024-10-28T00:00:00" and j["events"][0]["from"] is None
    assert any("not a forecast" in n for n in j["notes"]) and len(j["sources"]) == 2
    assert TestClient(app).get("/api/v1/intel/hazards", params={"limit": 0}).status_code == 422


def test_sources_listing_marks_hazard_feeds_live():
    from fastapi.testclient import TestClient
    from app.main import app
    src = {s["id"]: s for s in TestClient(app).get("/api/v1/intel/sources").json()["sources"]}
    assert src["GDACS"]["status"] == "live" and src["NOAA Tsunami"]["status"] == "live" and src["NOAA Tsunami"]["auth"] is False


def test_event_study_options_list_hazard_types_and_countries():
    from fastapi.testclient import TestClient
    from app.main import app
    o = TestClient(app).get("/api/v1/intel/company/event-study/options").json()
    assert {t["id"] for t in o["hazard_types"]} == {"EQ", "TC", "FL", "VO"} and {"id": "IND", "label": "India"} in o["hazard_countries"]


def test_disaster_event_study_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.intel import event_study as es
    c = TestClient(app)
    base = "/api/v1/intel/company/event-study/disasters"
    good = {"symbol": "^NSEI", "hazard": "FL", "country": "IND"}
    for bad in ({"hazard": "XX"}, {"country": "IN"}, {"levels": "all"}, {"hazard": "F"}):
        assert c.get(base, params={**good, **bad}).status_code == 422, bad
    seen = {}

    async def hist(type_code, iso3, levels, years=5):
        seen.update(t=type_code, c=iso3, l=levels)
        return [{"date": "2024-07-01", "label": "Flood"}, {"date": "2024-08-02", "label": "Flood"}]

    async def study(symbol, events, benchmark=None):
        seen.update(sym=symbol, n=len(events), bm=benchmark)
        return {"symbol": symbol, "events": [], "skipped": [], "summary": {}, "baseline": {}, "windows": [0, 1, 3, 5], "notes": ["n"]}
    monkeypatch.setattr(hz, "fetch_history_dates", hist); monkeypatch.setattr(es, "study", study)
    r = c.get(base, params={**good, "levels": "red", "benchmark": "^GSPC"})
    j = r.json()
    assert r.status_code == 200 and seen == {"t": "FL", "c": "IND", "l": ["Red"], "sym": "^NSEI", "n": 2, "bm": "^GSPC"}
    assert j["source"]["name"] == "GDACS" and j["source"]["region"] == "India" and j["source"]["hazard"] == "Flood" and j["source"]["levels"] == "Red only" and j["source"]["events_found"] == 2
    c.get(base, params={**good, "hazard": "fl", "country": "ind"})
    assert seen["l"] == ["Red", "Orange"] and seen["c"] == "IND", "codes are case-insensitive; default is Orange + Red"

    async def none(*a, **k):
        return []
    monkeypatch.setattr(hz, "fetch_history_dates", none)
    assert "no events" in c.get(base, params=good).json()["notes"][0].lower()

    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(hz, "fetch_history_dates", boom)
    r = c.get(base, params=good)
    assert r.status_code == 200 and "could not be fetched" in r.json()["notes"][0]
