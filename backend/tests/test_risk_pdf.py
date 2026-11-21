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
    assert c["legacy"]["risk_score"] == 42 and c["methodology"] == FULL["methodology"] and c["limitations"] == FULL["limitations"]


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
    for needle in ("Risk: High", "Stress points by family", "Provenance of each indicator", "Land vulnerability", "Soil condition", "Climate of the area", "How sensitive", "The original alert score",
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


def test_pdf_endpoint_returns_a_pdf_with_the_area_label(api):
    a, _ = api
    resp = asyncio.run(a.risk_assessment_pdf(a.RiskScoreRequest(aoi_geojson=poly(15))))
    assert resp.media_type == "application/pdf" and resp.body[:5] == b"%PDF-" and "attachment" in resp.headers["content-disposition"]
    if HAS_PDFTOTEXT: assert "26.050" in text_of(resp.body) and "Location not given" not in text_of(resp.body)


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
