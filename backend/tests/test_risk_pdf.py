"""Detailed risk assessment endpoints + PDF report: content completeness, honest handling of gaps, number formatting, diagram robustness, rendering, endpoint validation.
Earth Engine is NOT reachable from the dev sandbox: every source is mocked.  pytest tests/test_risk_pdf.py"""
import asyncio, copy, math, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from fastapi import HTTPException
from agri_fixtures import LOCATIONS
from test_risk_assessment import inputs
from app.services.agri import crop_suitability as cs, pathway_diagrams as dg, risk_assessment as ra, risk_diagrams as rd, risk_pdf as rp

HAS_PDFTOTEXT = shutil.which("pdftotext") is not None
FULL = ra.assemble_assessment(inputs())
NO_PROFILE = ra.assemble_assessment(inputs(profile={}))
EMPTY = ra.assemble_assessment({"errors": {"vdi": "boom", "baseline": "b", "prec": "p", "rz": "r", "gw": "g", "profile": "x"}, "as_of": "2026-11-20"})


def text_of(pdf):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf); f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True).stdout


def poly(i):                      # a distinct AOI per test so the in-memory result cache never leaks between tests
    return {"type": "Polygon", "coordinates": [[[75.0 + i, 26.0], [75.1 + i, 26.0], [75.1 + i, 26.1], [75.0 + i, 26.1], [75.0 + i, 26.0]]]}


# ---------------- formatting ----------------
def test_num_never_strips_zeros_from_whole_numbers():
    assert rp._num(50.0, 0) == "50" and rp._num(100.0, 0) == "100" and rp._num(0.0, 0) == "0" and rp._num(40.0) == "40"
    assert rp._num(56.25, 1) in ("56.2", "56.3") and rp._num(2.50, 2) == "2.5" and rp._num(None) == "n/a" and rp._num(7) == "7"


def test_json_safe_removes_nan_and_infinity_only():
    x = {"a": float("nan"), "b": [1.5, float("inf"), (2, float("-inf"))], "c": "t", "d": None, "e": 3}
    assert ra.json_safe(x) == {"a": None, "b": [1.5, None, [2, None]], "c": "t", "d": None, "e": 3}


# ---------------- content: every metric present ----------------
def test_every_indicator_metric_reaches_the_report():
    c = rp.build_content(FULL, location_label="X", generated_on="2026-11-20")
    assert [i["id"] for i in c["indicators"]] == ["vhi", "ndvi_z", "rain_pct", "rz_pct", "gw_pct"]
    for i, src in zip(c["indicators"], FULL["indicators"]):
        for k in ("value", "baseline", "percentile", "class", "stress", "reliability", "source", "resolution", "window"): assert i[k] not in (None, ""), (i["id"], k)
        assert i["source"] == src["source"] and i["reliability"] == src["reliability"] and i["resolution"] == src["resolution"] and i["window"] == src["window"]
    stress_pts = {i["id"]: i["stress"] for i in c["indicators"]}
    assert stress_pts == {"vhi": "50", "ndvi_z": "40", "rain_pct": "100", "rz_pct": "40", "gw_pct": "40"}, "stress points keep their trailing zeros"


def test_headline_numbers_match_the_assessment():
    c = rp.build_content(FULL)
    assert c["meta"]["risk_class"] == FULL["risk"]["class"] and c["meta"]["stress_score"] == FULL["stress"]["score"] and c["meta"]["vulnerability_score"] == FULL["vulnerability"]["score"]
    assert FULL["risk"]["class"] in c["headline"] and rp._num(FULL["stress"]["score"]) in c["headline"] and c["risk_rule"] == FULL["risk"]["rule"]
    assert c["meta"]["risk_index"] == ra.RISK_ORDER.index(FULL["risk"]["class"])


def test_vulnerability_soil_climate_sensitivity_and_legacy_are_carried():
    c = rp.build_content(FULL)
    assert [f["name"] for f in c["vulnerability"]["factors"]] == [f["name"] for f in FULL["vulnerability"]["factors"]] and len(c["vulnerability"]["factors"]) == 5
    assert {r["id"] for r in c["soil"]["properties"]} >= {"ph", "oc", "texture", "slope"} and c["soil"]["not_measurable"] and c["soil"]["advice"]
    assert c["climate"]["annual_rain_mm"] == FULL["climate"]["annual_rain_mm"]
    assert len(c["sensitivity"]["variants"]) == len(FULL["sensitivity"]["variants"]) and "spread" in c["sensitivity"]["text"]
    assert c["original"]["risk_score"] == 42 and c["methodology"] == FULL["methodology"] and c["limitations"] == FULL["limitations"]


def test_citations_listed_are_the_ones_used_and_exist():
    keys = [x["key"] for x in rp.build_content(FULL)["citations"]]
    assert {"kogan", "usdm", "chirps", "spi", "smap", "grace", "awc", "shc", "aislus", "unep", "openlandmap"} <= set(keys) and len(keys) == len(set(keys))
    assert all(k in FULL["citations"] for k in keys)
    assert "grace" not in [x["key"] for x in rp.build_content(ra.assemble_assessment(inputs(gw=None, profile={})))["citations"]], "an unused source is not cited"


def test_sensitivity_text_matches_robustness_flag():
    robust = rp._sensitivity_text({"range": 4.0, "min": 50.0, "max": 54.0, "robust": True, "most_influential": None})
    fragile = rp._sensitivity_text({"range": 25.0, "min": 40.0, "max": 65.0, "robust": False, "most_influential": "without rainfall"})
    assert "is robust" in robust and "depends noticeably" in fragile and "without rainfall" in fragile and "could not be computed" in rp._sensitivity_text({})


# ---------------- gaps are shown honestly, never as zero ----------------
def test_unavailable_indicator_is_listed_with_its_reason_and_no_stress_value():
    a = ra.assemble_assessment(inputs(prec=None, errors={"prec": "CHIRPS timeout"}))
    c = rp.build_content(a); row = next(i for i in c["indicators"] if i["id"] == "rain_pct")
    assert row["status"] == "unavailable" and "CHIRPS timeout" in row["baseline"] and row["stress"] == "-" and row["value"] == "not available"
    assert any("CHIRPS timeout" in u for u in c["summary"]["unavailable"])


def test_no_stress_data_is_cannot_assess_and_still_renders():
    c = rp.build_content(EMPTY)
    assert c["meta"]["risk_class"] == "Cannot assess" and c["meta"]["risk_index"] is None and "cannot be assessed" in c["headline"]
    assert {e["source"] for e in c["errors"]} >= {"Land profile (soil, slope, climate)", "Rainfall history"}
    assert rp.render_pdf(c)[:5] == b"%PDF-"


def test_missing_land_profile_says_so_and_does_not_invent_soil():
    c = rp.build_content(NO_PROFILE)
    assert c["soil"] is None and c["climate"] is None and not c["vulnerability"]["factors"] and "unavailable" in c["vulnerability"]["explanation"]
    assert rp.render_pdf(c)[:5] == b"%PDF-"


# ---------------- rendering ----------------
@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext not installed")
def test_rendered_text_has_every_section_and_the_unscaled_numbers():
    t = text_of(rp.make_risk_pdf(FULL, location_label="26.912N, 75.787E", generated_on="2026-11-20"))
    for needle in ("Risk: High", "Stress points by family", "Provenance of each indicator", "Land vulnerability", "Soil condition", "Climate of the area", "How sensitive", "The original three indicators, in full", "Area where NDDI could be computed", "Vegetation lost", "Dry-stress share", "NASA SMAP",
                   "Methodology", "Limitations", "Sources", "Vegetation Health Index", "Groundwater-storage", "26.912N"): assert needle in t, needle
    row = next(l for l in t.splitlines() if "50 mm" in l and "D4 exceptional" in l)           # the rainfall row of the values table
    assert row.split()[-1] == "100", f"stress points column must read 100, got: {row!r}"


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext not installed")
def test_pdf_never_prints_unsafe_characters_or_chemical_advice():
    a = copy.deepcopy(FULL); a["summary"]["drivers"] = ["Rain \u2192 low \u2265 5 \u201cquoted\u201d \u0939\u093f\u0902\u0926\u0940"]
    t = text_of(rp.make_risk_pdf(a))
    assert "->" not in t and "\u2192" not in t and " to low >= 5" in t and "?" in t, "arrows become words, unsupported scripts become ? rather than crashing"


def test_long_location_label_does_not_break_the_cover():
    assert rp.make_risk_pdf(FULL, location_label="Very long place name " * 30)[:5] == b"%PDF-"


# ---------------- diagrams ----------------
def test_diagrams_survive_empty_and_partial_inputs():
    assert rd.class_scale(ra.RISK_ORDER, None).width > 0 and rd.class_scale(ra.RISK_ORDER, 3).width > 0
    assert rd.family_bars([{"label": "A", "score": None}, {"label": "B", "score": 140.0}, {"label": "C", "score": -5}]).height > 0, "out-of-range scores are clamped"
    assert rd.family_bars([]).height > 0
    assert rd.position_chart([]).height > 0 and rd.position_chart(EMPTY["indicators"]).height > 0 and rd.position_chart(FULL["indicators"]).height > 0
    assert rd.sensitivity_chart({"equal weights (headline)": None, "x": None}).height > 0 and rd.sensitivity_chart({}).height > 0 and rd.sensitivity_chart(FULL["sensitivity"]["variants"]).height > 0


def _strings(d):
    out = []
    def walk(n):
        if hasattr(n, "text"): out.append(n.text)
        for ch in getattr(n, "contents", []) or []: walk(ch)
    walk(d); return out


def test_climate_chart_crop_legend_is_default_and_can_be_turned_off():
    args = ([20.0] * 12, [50.0] * 12, None, None, None, None)
    assert "planting window" in _strings(dg.climate_chart(*args)), "default output unchanged for the crop guide"
    off = _strings(dg.climate_chart(*args, crop_legend=False))
    assert "planting window" not in off and "crop's comfortable temperature" not in off and "your monthly rainfall" in off


# ---------------- endpoints ----------------
@pytest.fixture
def api(monkeypatch):
    from app.api import agri_endpoints as a
    calls = {"compute": [], "profile": 0}
    def fake_compute(aoi, as_of=None, profile=None):
        calls["compute"].append({"as_of": as_of, "profile": profile}); return copy.deepcopy(FULL)
    def fake_profile(**k):
        calls["profile"] += 1; return copy.deepcopy(LOCATIONS["rajasthan_semiarid"])
    monkeypatch.setattr(ra, "compute_risk_assessment", fake_compute); monkeypatch.setattr(cs, "sample_location_profile", fake_profile)
    return a, calls


def test_endpoint_returns_the_assessment_and_passes_the_cached_profile_in(api):
    a, calls = api
    r = asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(aoi_geojson=poly(10), as_of="2026-11-01")))
    assert r["risk"]["class"] == FULL["risk"]["class"] and calls["compute"][0]["as_of"] == "2026-11-01" and calls["compute"][0]["profile"]["texture_class"] == LOCATIONS["rajasthan_semiarid"]["texture_class"]
    asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(aoi_geojson=poly(10), as_of="2026-11-01")))
    assert len(calls["compute"]) == 1 and calls["profile"] == 1, "second identical request is served from cache"


def test_endpoint_rejects_bad_inputs(api):
    a, _ = api
    for kw in ({"aoi_geojson": {}}, {"aoi_geojson": {"coordinates": []}}, {"aoi_geojson": poly(11), "as_of": "01/11/2026"}, {"aoi_geojson": poly(11), "as_of": "2999-01-01"}, {"aoi_geojson": poly(11), "as_of": "2026-13-40"}):
        with pytest.raises(HTTPException) as e: asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(**kw)))
        assert e.value.status_code == 422, kw


def test_endpoint_survives_profile_failure_and_reports_it(api, monkeypatch):
    a, calls = api
    def boom(**k): raise RuntimeError("soil layers down")
    monkeypatch.setattr(cs, "sample_location_profile", boom)
    r = asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(aoi_geojson=poly(12))))
    assert "soil layers down" in r["errors"]["profile"] and calls["compute"][0]["profile"] == {}, "compute is told there is no profile instead of retrying the failed sampling"


def test_endpoint_compute_failure_is_422(api, monkeypatch):
    a, _ = api
    monkeypatch.setattr(ra, "compute_risk_assessment", lambda **k: (_ for _ in ()).throw(RuntimeError("gee down")))
    with pytest.raises(HTTPException) as e: asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(aoi_geojson=poly(13))))
    assert e.value.status_code == 422 and "gee down" in e.value.detail


def test_endpoint_output_is_strict_json_even_if_a_source_returns_nan(api, monkeypatch):
    import json
    a, _ = api
    bad = copy.deepcopy(FULL); bad["indicators"][0]["value"] = float("nan"); bad["stress"]["score"] = float("inf")
    monkeypatch.setattr(ra, "compute_risk_assessment", lambda **k: bad)
    r = asyncio.run(a.risk_assessment_endpoint(a.RiskScoreRequest(aoi_geojson=poly(14))))
    json.dumps(r, allow_nan=False); assert r["indicators"][0]["value"] is None and r["stress"]["score"] is None


def _png(color=(20, 120, 70)):
    import io
    from PIL import Image as PI
    buf = io.BytesIO(); PI.new("RGB", (48, 48), color).save(buf, "PNG"); return buf.getvalue()


@pytest.fixture
def maps(api, monkeypatch):
    """Replaces the Earth Engine map fetchers; records which were called."""
    a, _ = api; called = []
    def ok(name, val=None):
        def f(aoi, as_of): called.append((name, as_of)); return val if val is not None else _png()
        return f
    fetchers = {k: ok(k) for k in a._MAP_FETCHERS}; fetchers["moisture"] = ok("moisture", {"bytes": _png((120, 90, 50)), "min": 0.083, "max": 0.171})
    monkeypatch.setattr(a, "_MAP_FETCHERS", fetchers)
    return a, called, fetchers


def pdf_req(a, i, **kw): return a.RiskAssessmentPdfRequest(aoi_geojson=poly(i), **kw)


def test_pdf_endpoint_returns_a_pdf_with_the_area_label(maps):
    a, called, _ = maps
    resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 15)))
    assert resp.media_type == "application/pdf" and resp.body[:5] == b"%PDF-" and "attachment" in resp.headers["content-disposition"]
    if HAS_PDFTOTEXT: assert "26.050" in text_of(resp.body) and "Location not given" not in text_of(resp.body)


def test_pdf_has_all_seven_maps_with_legends_and_the_moisture_range_fitted_to_the_area(maps):
    a, called, _ = maps
    resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 16)))
    assert sorted(n for n, _ in called) == sorted(a._MAP_FETCHERS) and {d for _, d in called} == {FULL["as_of"]}, "every map is drawn for the assessment's as-of date"
    if HAS_PDFTOTEXT:
        t = text_of(resp.body)
        for needle in ("Maps of the indicators", "True-colour view", "Vegetation greenness (NDVI)", "Drought map (NDDI)", "Soil moisture (NASA SMAP)", "Accumulated rainfall", "Groundwater storage anomaly", "Land surface temperature",
                       "0.083", "0.171", "pixels are bare soil, water or cloud"): assert needle in t, needle
        assert "Not available in this run" not in t


def test_a_failed_map_is_listed_as_unavailable_and_never_fails_the_report(maps):
    a, called, fetchers = maps
    def boom(aoi, as_of): raise RuntimeError("earth engine down")
    fetchers["nddi"] = boom; fetchers["groundwater"] = lambda aoi, as_of: None
    resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 17)))
    assert resp.body[:5] == b"%PDF-"
    if HAS_PDFTOTEXT:
        t = text_of(resp.body); flat = " ".join(t.split())
        assert "Not available in this run" in t and "Drought map (NDDI)" in flat.split("Not available in this run:")[1] and "Groundwater storage anomaly (GRACE)" in flat.split("Not available in this run:")[1]
        assert "Vegetation greenness (NDVI)" in t.split("Not available in this run")[0], "the maps that worked are still drawn"


def test_slow_maps_time_out_and_the_report_still_arrives(maps, monkeypatch):
    import time
    a, called, fetchers = maps; monkeypatch.setattr(a, "IMAGERY_TIMEOUT_S", 0.2)
    for k in fetchers: fetchers[k] = lambda aoi, as_of: (time.sleep(1.0), _png())[1]
    t0 = time.time(); resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 18)))
    assert resp.body[:5] == b"%PDF-" and time.time() - t0 < 2.5, "does not wait for the slow maps"
    if HAS_PDFTOTEXT: assert "Not available in this run" in text_of(resp.body)


def test_maps_can_be_switched_off_and_are_then_not_fetched(maps):
    a, called, _ = maps
    resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 19, include_imagery=False)))
    assert called == [] and resp.body[:5] == b"%PDF-"
    if HAS_PDFTOTEXT: t = text_of(resp.body); assert "Maps were not requested for this report" in t and "Not available in this run" not in t


def test_the_place_name_goes_on_the_cover_cleaned_and_capped(maps):
    a, _, _ = maps
    r1 = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 20, region_name="  Sikar \n  district   ", include_imagery=False)))
    r2 = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 21, region_name="X" * 400, include_imagery=False)))
    assert r1.body[:5] == b"%PDF-" and r2.body[:5] == b"%PDF-"
    c = rp.build_content(FULL, location_label="Sikar district (26.050N, 75.050E)")
    assert c["meta"]["location"] == "Sikar district (26.050N, 75.050E)"
    if HAS_PDFTOTEXT:
        flat = " ".join(text_of(r1.body).split()); assert "Sikar district (26.050N, 95.050E)" in flat and "(Your drawn area" not in flat, "no doubled parentheses"
        assert "X" * 101 not in "".join(text_of(r2.body).split()).replace("XXXX", "XXXX"), "a long name is capped at 100 characters"


def test_map_section_survives_a_corrupt_image(maps):
    a, called, fetchers = maps; fetchers["ndvi"] = lambda aoi, as_of: b"not an image"
    resp = asyncio.run(a.risk_assessment_pdf(pdf_req(a, 22)))
    assert resp.body[:5] == b"%PDF-"
    if HAS_PDFTOTEXT: assert "Vegetation greenness (NDVI)" in " ".join(text_of(resp.body).split()).split("Not available in this run:")[1]


def test_content_records_which_maps_exist_without_holding_image_bytes():
    c = rp.build_content(FULL, images={"ndvi": {"bytes": b"x"}, "moisture": {"bytes": b"y", "range": (0.1, 0.2)}})
    got = {m["key"]: m["available"] for m in c["maps"]}; assert got["ndvi"] and got["moisture"] and not got["nddi"] and len(c["maps"]) == 7
    assert "bytes" not in repr(c) and next(m for m in c["maps"] if m["key"] == "moisture")["range"] == (0.1, 0.2)


def test_map_legends_are_the_report_generator_constants_the_thumbnails_use():
    from app.services import report_generator as rg
    legends = {k: leg for k, _, _, leg in rp.MAP_SPECS}
    assert legends["ndvi"] is rg.NDVI_LEGEND and legends["nddi"] is rg.NDDI_LEGEND and legends["precipitation"] is rg.PRECIP_LEGEND and legends["groundwater"] is rg.GROUNDWATER_LEGEND and legends["temperature"] is rg.THERMAL_LEGEND


def test_aoi_centre_handles_feature_and_multipolygon():
    from app.api import agri_endpoints as a
    p = poly(0)
    for g in (p, {"type": "Feature", "geometry": p, "properties": {}}, {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": p}]}, {"type": "MultiPolygon", "coordinates": [p["coordinates"]]}):
        lat, lon = a._aoi_centre(g); assert lat == pytest.approx(26.05) and lon == pytest.approx(75.05), "closing vertex not double-counted"
    assert a._aoi_centre({"type": "Point", "coordinates": [1, 2]}) is None


# ---------------- the frontend's test fixtures must keep the real shape ----------------
def _shape(x):
    if isinstance(x, dict): return {k: _shape(v) for k, v in x.items() if k not in ("citations", "variants", "errors", "families")}
    if isinstance(x, list): return [_shape(x[0])] if x else []
    return type(x).__name__ if x is not None else "None"


def test_frontend_fixture_has_the_same_top_level_shape_as_the_real_assessment():
    import json
    fx = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "ui-tests" / "fixtures" / "risk_assessment.json").read_text())
    for name in ("full", "empty", "partial"):
        assert set(fx[name]) == set(FULL), (name, set(fx[name]) ^ set(FULL))
        for i in fx[name]["indicators"]: assert {"id", "name", "family", "status", "source"} <= set(i)
    assert set(fx["full"]["indicators"][0]) == set(FULL["indicators"][0]), "regenerate the fixture: indicator fields changed"
    assert set(fx["full"]["vulnerability"]) == set(FULL["vulnerability"]) and set(fx["full"]["soil_condition"]) == set(FULL["soil_condition"])


# ---------------- the ONE report carries the original three indicators in full ----------------
def test_the_single_report_shows_every_original_metric_with_its_value():
    o = rp.build_content(FULL)["original"]; rm = V1_RAW = FULL["legacy"]["raw_metrics"]
    assert [x["name"] for x in o["components"]] == ["Drought extent (NDDI)", "Vegetation loss (Sentinel-2 NDVI)", "Soil-moisture deficit (SMAP)"]
    assert [x["weight"] for x in o["components"]] == ["40%", "35%", "25%"] and [x["sub_score"] for x in o["components"]] == ["45", "30", "52.5"]
    flat = " | ".join(" ".join(r) for r in o["drought"] + o["vegetation"] + o["moisture"])
    for needle in ("2.70 km2", "27% of the area", "Share of the assessable area under drought 45%", "Area where NDDI could be computed 60%", "0.31, 0.52, 0.21", "1.20 km2", "30% of the initial", "-0.80 km2", "0.24 and 0.19", "-0.05", "30% (3.00 km2)"): assert needle in flat, needle
    assert o["period"] == "2025-11-10 to 2026-11-10" and o["reason"] and len(o["method"]) == 4


def test_a_not_assessed_original_indicator_says_why_instead_of_a_number():
    a = copy.deepcopy(FULL); lg = a["legacy"]; lg["sub_scores"]["drought"] = None; lg["inputs_failed"] = ["drought"]; lg["raw_metrics"]["drought"]["assessed_area_pct"] = 5.0
    row = rp.build_content(a)["original"]["components"][0]
    assert row["sub_score"] == "not assessed" and "only 5% of the area could be scored" in row["reason"]


def test_the_original_section_survives_a_missing_legacy_score_and_missing_metrics():
    a = copy.deepcopy(FULL); a["legacy"] = None; c = rp.build_content(a); assert c["original"] is None and rp.render_pdf(c)[:5] == b"%PDF-"
    b = copy.deepcopy(FULL); b["legacy"]["raw_metrics"] = {}; b["legacy"]["sub_scores"] = {k: None for k in b["legacy"]["sub_scores"]}; c = rp.build_content(b)
    assert c["original"]["drought"] == [] and rp.render_pdf(c)[:5] == b"%PDF-"


def test_the_nddi_and_ndwi_citations_are_listed_when_the_original_section_is_present():
    keys = [x["key"] for x in rp.build_content(FULL)["citations"]]; assert {"nddi", "gao", "smap"} <= set(keys)


def test_wrap_text_breaks_long_words_and_keeps_every_line_within_the_width():
    m = lambda t: len(t) * 6; W = 120
    for text in ("Area: " + "X" * 100, "Area: Sikar district (26.050N, 95.050E)", "a " * 40, "", "Supercalifragilisticexpialidocious " * 3):
        lines = rp._wrap_text(text, m, W)
        assert all(m(l) <= W for l in lines), (text, lines)
        assert "".join("".join(lines).split()) == "".join(text.split()), "no characters lost or invented"
    assert rp._wrap_text("one two three", m, 1000) == ["one two three"]


# ---------------- what to do ----------------
def _recs(cls, conf="high", unavailable=(), failed=()): return rp._recommendations(cls, conf, list(unavailable), list(failed))


def test_recommendations_follow_the_risk_class():
    assert any("field visit" in r for r in _recs("High")) and any("field visit" in r for r in _recs("Very high")) and any("which part of the area" in r for r in _recs("High"))
    assert any("every 2 to 4 weeks" in r for r in _recs("Moderate")) and not any("field visit" in r for r in _recs("Moderate"))
    assert any("No immediate action" in r for r in _recs("Low")) and not any("field visit" in r for r in _recs("Low"))
    assert any("no risk class is given" in r for r in _recs("Cannot assess")) and not any("No immediate action" in r for r in _recs("Cannot assess")), "an unassessed area must never read as 'no action needed'"


def test_recommendations_react_to_low_confidence_and_missing_data_and_always_end_with_the_watchlist_step():
    r = _recs("Moderate", conf="low", unavailable=["GRACE (x)"], failed=["drought", "moisture_deficit"])
    assert any("Confidence is low" in x for x in r) and any("1 indicator(s) had no data" in x for x in r) and any("drought, moisture deficit" in x for x in r) and "watchlist" in r[-1]
    clean = _recs("Low", conf="high"); assert not any("Confidence is low" in x or "had no data" in x or "original three-indicator" in x for x in clean)


def test_recommendations_never_give_chemical_or_input_advice():
    banned = ("pesticide", "fungicide", "herbicide", "fertiliser", "fertilizer", "insecticide", "spray", "urea", "dose")
    for cls in ("Cannot assess", "Low", "Moderate", "High", "Very high"):
        text = " ".join(_recs(cls, conf="low", unavailable=["a"], failed=["drought"])).lower(); assert not any(b in text for b in banned), cls


def test_the_report_carries_the_recommendations_for_its_class():
    c = rp.build_content(FULL); assert c["recommendations"] and any("field visit" in r for r in c["recommendations"])
    if HAS_PDFTOTEXT: assert "What to do with this assessment" in text_of(rp.render_pdf(c))
    e = rp.build_content(EMPTY); assert any("no risk class is given" in r for r in e["recommendations"])
