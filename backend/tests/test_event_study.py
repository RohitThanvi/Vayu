import asyncio
import datetime as dt
import random

import pytest

from app.services.intel import event_study as es


def run(c):
    return asyncio.run(c)


def weekdays(n, start=dt.date(2022, 1, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


DAYS = weekdays(600)


def flat(v=100.0):
    return {d: v for d in DAYS}


def jump_series(at_idx, factor=1.10, base=100.0):
    return {d: base * (factor if i >= at_idx else 1.0) for i, d in enumerate(DAYS)}


def noisy(seed, vol=0.01):
    rng, p, out = random.Random(seed), 100.0, {}
    for d in DAYS:
        p *= 1 + rng.gauss(0, vol)
        out[d] = p
    return out


def ev(*dates):
    return [{"date": d} for d in dates]


def test_exact_abnormal_return_against_a_flat_benchmark():
    r = es.compute(jump_series(300), flat(), ev(DAYS[300]), "XYZ", "^GSPC")
    e = r["events"][0]
    for k in ("0", "1", "3", "5"):
        assert e["ret"][k]["stock"] == 10.0 and e["ret"][k]["bench"] == 0.0 and e["ret"][k]["abnormal"] == 10.0
        assert e["ret"][k]["unusual"] is True, "a +10% jump on a flat history is outside the ordinary range"
    assert e["aligned_date"] == DAYS[300] and e["note"] is None and e["overlaps_other_event"] is False
    assert r["summary"]["0"]["n"] == 1 and "note" in r["summary"]["0"] and "std_error_pct" not in r["summary"]["0"]


def test_benchmark_move_is_subtracted():
    bench = {d: 100.0 * (1.02 if i >= 300 else 1.0) for i, d in enumerate(DAYS)}
    e = es.compute(jump_series(300, 1.05), bench, ev(DAYS[300]))["events"][0]["ret"]["3"]
    assert (e["stock"], e["bench"], e["abnormal"]) == (5.0, 2.0, 3.0)


def test_weekend_event_aligns_to_next_trading_day_and_same_day_is_counted_once():
    mon_idx = next(i for i in range(280, 320) if dt.date.fromisoformat(DAYS[i]).weekday() == 0)
    sat, sun = (dt.date.fromisoformat(DAYS[mon_idx]) - dt.timedelta(days=2)).isoformat(), (dt.date.fromisoformat(DAYS[mon_idx]) - dt.timedelta(days=1)).isoformat()
    r = es.compute(jump_series(mon_idx), flat(), ev(sat, sun))
    assert len(r["events"]) == 1 and r["events"][0]["aligned_date"] == DAYS[mon_idx] and "next trading day" in r["events"][0]["note"]
    assert r["events"][0]["ret"]["0"]["abnormal"] == 10.0
    assert r["skipped"] == [{"date": sun, "reason": "same trading day as an earlier event (counted once)"}]


def test_events_outside_the_data_are_skipped_with_reasons():
    r = es.compute(flat(), flat(), ev("2019-01-02", DAYS[0], "2030-01-01"))
    reasons = {s["date"]: s["reason"] for s in r["skipped"]}
    assert reasons["2019-01-02"].startswith("before") and reasons[DAYS[0]].startswith("no prior close") and reasons["2030-01-01"].startswith("after")
    assert r["events"] == [] and r["summary"] == {}


def test_windows_truncated_at_the_end_of_the_data_are_none_and_excluded_from_summary():
    r = es.compute(jump_series(len(DAYS) - 1), flat(), ev(DAYS[-1], DAYS[-30]))
    last = next(e for e in r["events"] if e["date"] == DAYS[-1])
    assert last["ret"]["0"]["abnormal"] == 10.0 and last["ret"]["1"] is None and last["ret"]["5"] is None
    assert r["summary"]["0"]["n"] == 2 and r["summary"]["5"]["n"] == 1


def test_benchmark_holiday_gaps_are_forward_filled():
    bench = flat()
    for d in DAYS[298:303]:
        del bench[d]
    r = es.compute(jump_series(300), bench, ev(DAYS[300]))
    assert r["events"][0]["ret"]["5"]["abnormal"] == 10.0


def test_too_little_history_says_so():
    short = {d: 100.0 for d in DAYS[:30]}
    r = es.compute(short, flat(), ev(DAYS[10]))
    assert r["events"] == [] and "Not enough price history" in r["notes"][0]
    assert "Not enough" in es.compute(flat(), {}, ev(DAYS[300]))["notes"][0]


def test_summary_statistics_match_hand_computation():
    # four events with abnormal day-0 returns +10, +4, -2, +8 (flat benchmark; stock steps at each event, then returns)
    idxs = [100, 200, 300, 400]
    steps = {100: 1.10, 200: 1.04, 300: 0.98, 400: 1.08}
    stock, p = {}, 100.0
    for i, d in enumerate(DAYS):
        if i in steps:
            p *= steps[i]
        elif i - 1 in steps:
            p /= steps[i - 1]                      # step lasts one day only, so windows k>0 differ; we check k=0 exactly
        stock[d] = p
    s = es.compute(stock, flat(), ev(*[DAYS[i] for i in idxs]))["summary"]["0"]
    assert s["n"] == 4 and s["mean_abnormal_pct"] == 5.0 and s["median_abnormal_pct"] == 6.0 and s["share_positive_pct"] == 75.0
    assert s["std_error_pct"] == pytest.approx(((((10 - 5) ** 2 + (4 - 5) ** 2 + (-2 - 5) ** 2 + (8 - 5) ** 2) / 4) ** 0.5) / 2, abs=0.01)
    assert 0 <= s["percentile_vs_random_windows"] <= 100


def test_percentile_ranking_is_deterministic_and_flags_extreme_averages():
    stock = noisy(11)
    big = dict(stock)
    idxs = [100, 200, 300, 400, 500]
    for i in idxs:                                  # inject a +8% one-day pop on each event day
        for j in range(i, len(DAYS)):
            big[DAYS[j]] *= 1.08
    a = es.compute(big, noisy(12), ev(*[DAYS[i] for i in idxs]))["summary"]["0"]
    b = es.compute(big, noisy(12), ev(*[DAYS[i] for i in idxs]))["summary"]["0"]
    assert a == b, "fixed seed => reproducible"
    assert a["percentile_vs_random_windows"] >= 99 and a["mean_abnormal_pct"] > 5
    quiet = es.compute(stock, noisy(12), ev(*[DAYS[i] for i in idxs]))["summary"]["0"]
    assert quiet["percentile_vs_random_windows"] < 99


def test_baseline_ordinary_range_is_reported_per_window():
    r = es.compute(noisy(3), noisy(4), ev(DAYS[300]))
    b = r["baseline"]["5"]
    assert b["p05"] < b["median"] < b["p95"] and b["n"] > 500


def test_overlapping_events_are_flagged():
    r = es.compute(noisy(5), noisy(6), ev(DAYS[300], DAYS[303], DAYS[450]))
    ov = {e["date"]: e["overlaps_other_event"] for e in r["events"]}
    assert ov[DAYS[300]] and ov[DAYS[303]] and not ov[DAYS[450]] and any("overlap" in n for n in r["notes"])


def test_event_list_is_capped():
    many = ev(*DAYS[100:200])
    assert len(es.compute(noisy(1), noisy(2), many)["events"]) <= es.MAX_EVENTS


# ── inputs ───────────────────────────────────────────────────────────────────
def test_parse_dates_labels_separators_and_problems():
    ev_, bad = es.parse_dates("2024-03-01 Port strike, 2024-05-02;2024-03-01\n2024-13-45, soon, 2999-01-01 future")
    assert ev_ == [{"date": "2024-03-01", "label": "Port strike"}, {"date": "2024-05-02", "label": None}]
    assert len(bad) == 3 and any("not a real date" in b for b in bad) and any("not a date" in b for b in bad) and any("future" in b for b in bad)
    assert es.parse_dates("") == ([], []) and es.parse_dates(None) == ([], [])


def test_default_benchmark_by_listing():
    assert es.default_benchmark("RELIANCE.NS") == "^NSEI" and es.default_benchmark("tcs.bo") == "^NSEI" and es.default_benchmark("GM") == "^GSPC"


def test_to_closes_skips_junk():
    assert es.to_closes([{"date": 1700000000, "value": 5}, {"date": "x", "value": 1}, {"value": 2}, None]) == {"2023-11-14": 5.0}


def test_parse_quakes_keeps_strongest_per_day_and_skips_bad_rows():
    t = lambda s: int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    data = {"features": [{"properties": {"time": t("2024-04-03T00:58:00"), "mag": 7.4, "place": "18 km SSW of Hualien City, Taiwan"}},
                         {"properties": {"time": t("2024-04-03T05:00:00"), "mag": 6.4, "place": "aftershock"}},
                         {"properties": {"time": t("2024-04-23T09:00:00"), "mag": 6.0, "place": None}}, {"properties": {"mag": 6}}, {"properties": {"time": "x", "mag": 6}}, {}]}
    out = es.parse_quakes(data)
    assert out == [{"date": "2024-04-03", "label": "M7.4 18 km SSW of Hualien City, Taiwan"}, {"date": "2024-04-23", "label": "M6.0"}]
    assert es.parse_quakes({}) == [] and es.parse_quakes(None) == []


# ── network-level behaviour with the network replaced ────────────────────────
def pts(series):
    return [{"date": int(dt.datetime.fromisoformat(d).replace(tzinfo=dt.timezone.utc).timestamp()), "value": v} for d, v in series.items()]


@pytest.fixture
def hist(monkeypatch):
    calls = []

    async def get_history(sym, rng="1y"):
        calls.append((sym, rng))
        if sym == "NOPE":
            return {"symbol": sym, "points": [], "error": "history unavailable (unknown symbol or upstream unavailable)"}
        return {"symbol": sym, "points": pts(jump_series(300) if sym == "XYZ" else flat())}
    monkeypatch.setattr(es.watchlist, "get_history", get_history)
    return calls


def test_study_fetches_both_series_with_the_right_default_benchmark(hist):
    r = run(es.study("xyz", ev(DAYS[300])))
    assert sorted(hist) == [("XYZ", "5y"), ("^GSPC", "5y")] and r["benchmark"] == "^GSPC" and r["events"][0]["ret"]["1"]["abnormal"] == 10.0
    hist.clear()
    run(es.study("TCS.NS", ev(DAYS[300])))
    assert ("^NSEI", "5y") in hist
    hist.clear()
    run(es.study("XYZ", ev(DAYS[300]), benchmark="^FTSE"))
    assert ("^FTSE", "5y") in hist


def test_study_rejects_bad_symbols_and_reports_missing_history(hist):
    with pytest.raises(ValueError):
        run(es.study("bad symbol!", ev(DAYS[300])))
    with pytest.raises(ValueError):
        run(es.study("XYZ", ev(DAYS[300]), benchmark="bad bench!"))
    r = run(es.study("NOPE", ev(DAYS[300])))
    assert r["events"] == [] and "unavailable" in r["notes"][0]


def test_quake_study_uses_usgs_events_and_documents_the_source(monkeypatch, hist):
    async def quakes(region, mag):
        return [{"date": DAYS[300], "label": "M7.4 Taiwan"}, {"date": DAYS[420], "label": "M6.1 Taiwan"}]
    monkeypatch.setattr(es, "fetch_quakes", quakes)
    r = run(es.study_quakes("XYZ", "taiwan", 6.0))
    assert [e["label"] for e in r["events"]] == ["M7.4 Taiwan", "M6.1 Taiwan"]
    assert r["source"]["name"] == "USGS Earthquake Catalog" and r["source"]["region"].endswith("(rough box)") and r["source"]["events_found"] == 2


def test_quake_study_failure_modes(monkeypatch, hist):
    async def boom(region, mag):
        raise RuntimeError("down")
    monkeypatch.setattr(es, "fetch_quakes", boom)
    r = run(es.study_quakes("XYZ", "japan", 6.0))
    assert r["events"] == [] and "could not be fetched" in r["notes"][0]
    with pytest.raises(ValueError):
        run(es.study_quakes("XYZ", "atlantis", 6.0))

    async def none(region, mag):
        return []
    monkeypatch.setattr(es, "fetch_quakes", none)
    assert "No earthquakes" in run(es.study_quakes("XYZ", "japan", 7.5))["notes"][0]

    async def many(region, mag):
        return [{"date": d, "label": "q"} for d in DAYS[60:560:5]]
    monkeypatch.setattr(es, "fetch_quakes", many)
    r = run(es.study_quakes("XYZ", "japan", 6.0))
    assert any("only the most recent" in n for n in r["notes"]) and len(r["events"]) <= es.MAX_EVENTS


def test_fetch_quakes_validates_before_any_network_call():
    with pytest.raises(ValueError):
        run(es.fetch_quakes("atlantis", 6.0))
    for bad in (4.9, 9.0):
        with pytest.raises(ValueError):
            run(es.fetch_quakes("japan", bad))


# ── HTTP layer ───────────────────────────────────────────────────────────────
def test_endpoints(hist, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    base = "/api/v1/intel/company/event-study"
    o = c.get(base + "/options").json()
    assert {r["id"] for r in o["regions"]} == set(es.QUAKE_REGIONS) and o["windows"] == [0, 1, 3, 5]
    assert c.get(base, params={"symbol": "XYZ"}).status_code == 422
    r = c.get(base, params={"symbol": "XYZ", "dates": "nonsense, 2999-01-01"})
    assert r.status_code == 422 and "not a date" in r.json()["detail"]
    r = c.get(base, params={"symbol": "XYZ", "dates": f"{DAYS[300]} Strike, 2024-99-99"})
    assert r.status_code == 200 and r.json()["events"][0]["label"] == "Strike" and r.json()["input_problems"]
    assert c.get(base, params={"symbol": "bad sym!", "dates": DAYS[300]}).status_code == 422
    too_many = ",".join(f"2023-{m:02d}-{d:02d}" for m in range(1, 7) for d in range(1, 9))
    assert c.get(base, params={"symbol": "XYZ", "dates": too_many}).status_code == 422

    async def quakes(region, mag):
        return [{"date": DAYS[300], "label": "M7.0 test"}]
    monkeypatch.setattr(es, "fetch_quakes", quakes)
    q = c.get(base + "/quakes", params={"symbol": "XYZ", "region": "taiwan", "min_magnitude": 6.5})
    assert q.status_code == 200 and q.json()["source"]["min_magnitude"] == 6.5
    assert c.get(base + "/quakes", params={"symbol": "XYZ", "region": "atlantis"}).status_code == 422
    assert c.get(base + "/quakes", params={"symbol": "XYZ", "region": "japan", "min_magnitude": 3}).status_code == 422
