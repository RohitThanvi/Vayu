"""Personalised PDF guide: plot-sized plan, planting window on local climate, personalisation, text safety, diagram robustness, rendering, endpoints.  pytest tests/test_pathway_pdf.py
Content tests run on the plain-data layer (no PDF parsing); rendering tests check structure; text-level checks use pdftotext when available (skipped otherwise)."""
import asyncio, copy, math, re, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from agri_fixtures import LOCATIONS
from test_pathways import BANNED, DOSE
from app.services.agri import crop_pathways as cp, pathway_diagrams as dg, pathway_pdf as pp, pathway_plan as pl
from app.services.agri.pathway_data import CROPS

IDS = [c["id"] for c in CROPS]
RJ = LOCATIONS["rajasthan_semiarid"]
HAS_PDFTOTEXT = shutil.which("pdftotext") is not None


def report_for(cid, prof=RJ, irrigated=False, ph=None):
    cl = cp.climate_from_profile(prof)
    if ph is not None: cl["ph"] = ph
    return cp.build_report(cid, cl, irrigated)


def pdf_for(cid, prof=RJ, area=2.5, **kw):
    return pp.make_pathway_pdf(report_for(cid, prof, **kw), prof, location_label=pp._loc_label(26.912, 75.787), area_acres=area, generated_on="2026-11-08")


def text_of(pdf):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf); f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True).stdout


# ---------------- plan arithmetic ----------------
def test_plant_counts_use_the_cited_spacing_and_the_plot_size():
    assert pl.compute_plan("dragon_fruit", 1)["count"] == math.floor(4046.8564224 / 9) == 449
    assert pl.compute_plan("dragon_fruit", 2.5)["count"] == 1124
    assert pl.compute_plan("moringa", 1)["count"] == math.floor(4046.8564224 / 6.25)
    assert pl.compute_plan("turmeric", 2.5)["count"] == math.floor(2.5 * 4046.8564224 / 0.125)


def test_materials_are_the_cited_amounts_times_the_plot():
    rows = {r["label"].split(" (")[0].split(" at ")[0]: r["value"] for r in pl.compute_plan("dragon_fruit", 2.5)["rows"]}
    assert rows["Posts with a ring on top"] == "1,124 posts" and rows["Farmyard manure or compost"] == "11 to 17 tonnes"
    t = pl.compute_plan("turmeric", 2.5)
    vals = [r["value"] for r in t["rows"]]
    assert {"2.0 to 2.5 tonnes", "30 to 40 tonnes", "12 to 15 tonnes"} <= set(vals), vals
    assert [r["value"] for r in pl.compute_plan("moringa", 10)["rows"]][-1] == "2.0 kg"       # 0.5 kg per hectare x 4.05 ha


def test_example_batch_is_labelled_an_example_and_saffron_has_no_plan():
    m = pl.compute_plan("oyster_mushroom", 5)
    assert m["count"] == 20 and m["rows"][0]["basis"] == "example" and "2,500 g" in m["rows"][1]["value"], "an area does not change a bag-based example"
    assert pl.compute_plan("saffron", 2) is None


def test_plot_size_validation():
    assert pl.clean_area(None) == 1.0 and pl.clean_area("2.5") == 2.5
    for bad in (0, -1, float("nan"), float("inf"), 1001, "abc"):
        with pytest.raises(ValueError): pl.clean_area(bad)


def test_every_plan_amount_traces_to_a_known_source():
    for c in CROPS:
        plan = c["plan"]
        if not plan: continue
        for _l, _lo, _hi, _u, src in plan["per_plant"] + plan["per_ha"]: assert all(k in c["sources"] for k in src), c["id"]
        assert all(k in c["sources"] for k in plan["spacing_src"]), c["id"]


# ---------------- planting window on local climate ----------------
RAIN = [10, 5, 5, 5, 20, 80, 200, 250, 150, 60, 10, 5]; TEMP = [15, 18, 24, 29, 32, 31, 28, 27, 27, 25, 20, 16]


def test_window_sums_rain_and_averages_temperature_over_the_cited_months():
    w = pl.planting_window("moringa", RAIN, TEMP)             # Jul-Oct
    assert w["months"] == [7, 8, 9, 10] and w["rain_in_window_mm"] == 200 + 250 + 150 + 60 and w["mean_temp_in_window_c"] == round((28 + 27 + 27 + 25) / 4, 1)
    w = pl.planting_window("oyster_mushroom", RAIN, TEMP)     # Oct-Mar wraps the year end
    assert w["rain_in_window_mm"] == 60 + 10 + 5 + 10 + 5 + 5


def test_window_is_none_without_cited_months_or_with_incomplete_data():
    assert pl.planting_window("dragon_fruit", RAIN, TEMP) is None
    assert pl.planting_window("moringa", None, None)["rain_in_window_mm"] is None
    assert pl.planting_window("moringa", [None] * 12, TEMP)["rain_in_window_mm"] is None


# ---------------- personalisation (content layer) ----------------
def content(cid, prof, area=1, **kw): return pp.build_content(report_for(cid, prof, **kw), prof, location_label="X", area_acres=area, generated_on="2026-11-08")


def test_different_land_gives_different_guides():
    dry, wet = copy.deepcopy(RJ), copy.deepcopy(RJ)
    dry["annual_rain_mm"], wet["annual_rain_mm"] = 300.0, 3000.0
    a, b = content("dragon_fruit", dry), content("dragon_fruit", wet)
    ia, ib = " ".join(a["sections"][2]["leads"]), " ".join(b["sections"][2]["leads"])
    assert "below this crop's usual" in ia and "Irrigation matters more" in ia and "Drainage comes before irrigation" in ib and ia != ib
    assert [p["title"] for p in a["priorities"]][:2] != [p["title"] for p in b["priorities"]][:2]
    assert any("Low rainfall" == p["title"] for p in a["priorities"]) and any("Too much rain" == p["title"] for p in b["priorities"])


def test_irrigation_answer_changes_the_lead_and_the_priorities():
    dry = copy.deepcopy(RJ); dry["annual_rain_mm"] = 300.0
    no, yes = content("dragon_fruit", dry, irrigated=False), content("dragon_fruit", dry, irrigated=True)
    assert "Low rainfall" in [p["title"] for p in no["priorities"]] and "Low rainfall" not in [p["title"] for p in yes["priorities"]]
    assert "you said you have reliable irrigation" in " ".join(yes["sections"][2]["leads"])


def test_soil_ph_lead_follows_the_farmers_own_value():
    assert "Your soil pH is about 6.5 and" not in " ".join(content("dragon_fruit", RJ, ph=6.5)["sections"][3]["leads"])
    ok = " ".join(content("dragon_fruit", RJ, ph=6.5)["sections"][3]["leads"]); gap = " ".join(content("dragon_fruit", RJ, ph=8.4)["sections"][3]["leads"])
    assert "inside the crop's range" in ok and "prefers 5.5-7.0" in gap and "8.4" in gap
    none = copy.deepcopy(RJ); none["ph"] = None
    assert "could not read your soil pH" in " ".join(content("dragon_fruit", none)["sections"][3]["leads"])


def test_plot_size_changes_the_plan_and_the_spacing_diagram_uses_their_spacing():
    one, ten = content("dragon_fruit", RJ, 1), content("dragon_fruit", RJ, 10)
    assert one["plan"]["count"] == 449 and ten["plan"]["count"] == 4496
    d = next(d for s in ten["sections"] for d in s["diagrams"] if d["id"] == "spacing_grid"); assert d["params"] == {"a": 3.0, "b": 3.0}
    t = next(d for s in content("turmeric", RJ)["sections"] for d in s["diagrams"] if d["id"] == "spacing_grid"); assert t["params"] == {"a": 0.5, "b": 0.25}


def test_chart_data_is_this_locations_climate_and_the_crops_limits():
    c = content("pomegranate", RJ)["chart"]
    assert c["temp"] == RJ["monthly_temp_c"] and c["rain"] == RJ["monthly_rain_mm"] and c["band"] == [20, 35] and (c["cold_limit"], c["hot_limit"]) == (0, 45)
    assert content("moringa", RJ)["chart"]["window_months"] == [7, 8, 9, 10] and content("dragon_fruit", RJ)["chart"]["window_months"] is None
    assert pp.build_content(cp.build_report("moringa"), None, location_label="G")["chart"] is None, "no location, no personal chart"


def test_blocking_gap_leads_the_priorities_and_the_climate_note_comes_first():
    cold = copy.deepcopy(LOCATIONS["himalayan_cold"]); cold["climate_note"] = "No climate data exists at the exact spot (water or coast pixel), so x."
    p = content("dragon_fruit", cold)["priorities"]
    assert p[0]["title"] == "About your climate numbers" and p[1]["title"].startswith("Stop and think: Cold weather")
    assert [x["title"] for x in p][-3:] == ["Talk to your KVK first", "Start small", "Line up a buyer before you scale"] and len(p) <= 8


def test_controlled_crops_get_the_build_first_advice():
    assert any("Build only after you have seen one working" == p["title"] for p in content("saffron", RJ)["priorities"])
    assert not any("Build only after" in p["title"] for p in content("moringa", RJ)["priorities"])


def test_general_guide_without_location_still_builds():
    c = pp.build_content(cp.build_report("lemongrass"), None, location_label="General guide")
    assert c["verdict"] is None and c["chart"] is None and c["plan"]["area_acres"] == 1.0 and len(c["sections"]) == 8
    assert pp.render_pdf(c).startswith(b"%PDF")


def test_diagrams_requested_by_the_data_all_exist():
    for c in CROPS:
        for secs in c["diagrams"].values():
            for d, params in secs: assert d in dg.DIAGRAMS and d in dg.CAPTIONS, (c["id"], d)


# ---------------- text safety ----------------
def test_all_guide_text_renders_in_helvetica_without_question_marks():
    for c in CROPS:
        texts = [c["name"], c["first_timer"]["why"], c["first_timer"]["time_to_first_fruit"]] + [g["do"] for g in c["gaps"]] + [t for k in c["guide"] for t, _b, _s in c["guide"][k]]
        texts += [t for t, _b, _k in c.get("site_cautions", [])] + [v for v in (c.get("controlled") or {}).values()] + [cp.CHEMICAL_POLICY, cp.build_report(c["id"])["disclaimer"]]
        for t in texts: assert pp.pdf_safe(t).count("?") == t.count("?"), (c["id"], t[:60])


def test_pdf_safe_swaps_symbols_and_never_raises():
    assert pp.pdf_safe("a \u2192 b \u2265 3 \u2264 4 \u2248 5 \u2019") == "a to b >= 3 <= 4 about 5 '" and pp.pdf_safe(None) == "" and pp.pdf_safe("\u0939\u093f") == "??"
    assert pp.pdf_safe("20\u00b0C \u2013 25 \u00d7 3") == "20\u00b0C \u2013 25 \u00d7 3"


# ---------------- diagrams ----------------
def test_every_diagram_builds_with_positive_size():
    for name, fn in dg.DIAGRAMS.items():
        d = fn(a=3.0, b=3.0) if name == "spacing_grid" else (fn(size="1 x 1 m") if name == "pit_planting" else fn())
        assert d.width > 0 and d.height > 0 and len(d.contents) > 3, name


@pytest.mark.parametrize("a,b", [(0.25, 0.5), (6, 6), (0.45, 0.45), (0.5, 0.25), (3, 0.3)])
def test_spacing_grid_handles_extreme_spacings(a, b):
    assert dg.spacing_grid(a, b).width > 0


def test_data_graphics_survive_missing_and_out_of_range_values():
    assert dg.range_gauge(None, 5, 10).width > 0 and dg.range_gauge(500, 5, 10).width > 0 and dg.range_gauge(-500, 5, 10).width > 0
    assert dg.climate_chart([None] * 12, [None] * 12, None, None, None).width > 0
    assert dg.climate_chart([10] * 12, [0] * 12, (20, 30), None, None, [1, 2]).width > 0 and dg.tier_scale(None).width > 0 and dg.tier_scale(4).width > 0


# ---------------- rendering ----------------
@pytest.mark.parametrize("cid", IDS)
def test_pdf_is_valid_complete_and_reasonably_sized(cid):
    b = pdf_for(cid)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", b))
    assert b.startswith(b"%PDF-") and b.rstrip().endswith(b"%%EOF") and 4 <= pages <= 12 and 12_000 < len(b) < 250_000, (cid, pages, len(b))
    assert b"/Outlines" in b and b"Vayu" in b


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext (poppler) not installed")
def test_in_this_guide_lists_only_what_the_guide_contains():
    saffron, general = text_of(pdf_for("saffron")), text_of(pp.make_pathway_pdf(cp.build_report("moringa"), None, location_label="General guide"))
    assert "In this guide" in saffron and "Your planting plan" not in saffron.split("\f")[0], "saffron has no area-based plan"
    assert "Your land at a glance: your own climate" not in general.split("\f")[0], "a general guide has no personal land section"


def test_different_inputs_make_different_pdfs_and_same_inputs_are_stable_in_size():
    assert pdf_for("moringa", area=1) != pdf_for("moringa", area=9)
    cold = LOCATIONS["himalayan_cold"]; assert pdf_for("dragon_fruit", cold) != pdf_for("dragon_fruit", RJ)
    assert abs(len(pdf_for("moringa")) - len(pdf_for("moringa"))) < 400


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext (poppler) not installed")
def test_pdf_text_carries_the_personal_facts():
    t = text_of(pdf_for("dragon_fruit", area=2.5))
    for needle in ("Dragon fruit (Kamalam)", "26.912", "75.787", "Prepared on 2026-11-08", "For your land", "1,124", "Your planting plan for 2.5 acres", "Your land at a glance", "Your priorities",
                   "In this guide", "Your planting plan: how much to buy", "Planting material", "Pests and diseases", "About chemicals", "Sources", "[S2]", "general knowledge", "https://epubs.icar.org.in"):
        assert needle in t, needle
    assert re.search(r"annual rainfall\s+568\.0 mm", t) and re.search(r"soil pH\s+8\.0", t)


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext (poppler) not installed")
@pytest.mark.parametrize("cid", IDS)
def test_pdf_text_never_contains_chemical_products_or_spray_doses(cid):
    t = text_of(pdf_for(cid)).lower()
    policy = cp.CHEMICAL_POLICY.lower()
    assert not [b for b in BANNED if b in t.replace("(", " ").replace(")", " ") and b not in policy], cid
    assert not DOSE.search(t), cid


# ---------------- endpoints ----------------
def test_endpoint_json_has_plan_window_label_and_no_markdown(monkeypatch):
    from app.api import agri_endpoints as a
    monkeypatch.setattr(pp.__dict__.get("cs", None) or __import__("app.services.agri.crop_suitability", fromlist=["x"]), "sample_location_profile", lambda **k: copy.deepcopy(RJ))
    r = asyncio.run(a.pathway_report(a.PathwayReportRequest(crop_id="moringa", lat=26.1, lon=75.1, area_acres=3)))
    assert r["plan"]["area_acres"] == 3.0 and r["location_label"].startswith("26.100") and r["planting_window"]["months"] == [7, 8, 9, 10] and "manual_markdown" not in r


def test_endpoint_pdf_headers_and_validation(monkeypatch):
    from app.api import agri_endpoints as a
    from fastapi import HTTPException
    monkeypatch.setattr(__import__("app.services.agri.crop_suitability", fromlist=["x"]), "sample_location_profile", lambda **k: copy.deepcopy(RJ))
    r = asyncio.run(a.pathway_report_pdf(a.PathwayReportRequest(crop_id="turmeric", lat=26.2, lon=75.2, area_acres=2)))
    assert r.media_type == "application/pdf" and r.body.startswith(b"%PDF") and 'filename="Vayu_turmeric_guide.pdf"' in r.headers["content-disposition"]
    for kw, code in ((dict(crop_id="turmeric", lat=26.3, lon=75.3, area_acres=0), 422), (dict(crop_id="nope", lat=1, lon=1), 404), (dict(crop_id="turmeric"), 422)):
        with pytest.raises(HTTPException) as e: asyncio.run(a.pathway_report_pdf(a.PathwayReportRequest(**kw)))
        assert e.value.status_code == code, kw
    assert a._aoi_centre({"type": "Polygon", "coordinates": [[[75, 26], [77, 26], [77, 28], [75, 28]]]}) == (27.0, 76.0) and a._aoi_centre({"bad": 1}) is None


def test_blocking_gaps_come_before_fixable_ones_whatever_order_they_were_found_in():
    prof = copy.deepcopy(RJ); prof["monthly_temp_c"] = [5.0] + [25.0] * 10 + [39.0]; prof["annual_rain_mm"] = 1200.0; prof["ph"] = 6.0
    v = report_for("avocado", prof)["verdict"]
    assert [g["if"] for g in v["triggered"]] == ["frost", "heat"] and [g["severity"] for g in v["triggered"]] == ["fixable", "blocking"], "fixture must find the fixable gap first"
    titles = [p["title"] for p in pp.build_content(report_for("avocado", prof), prof, location_label="X")["priorities"]]
    assert titles[:2] == ["Stop and think: Heat", "Cold weather"]


def test_source_tags_are_shown_inline_on_every_guide_line():
    cited = {"text": "t", "basis": "cited", "sources": [{"key": "S1", "url": "u"}, {"key": "S2", "url": "u"}]}
    assert "[S1, S2]" in pp._tags(cited) and "(general knowledge)" not in pp._tags(cited)
    assert "(general knowledge)" in pp._tags({"text": "t", "basis": "general", "sources": []}) and pp._tags({"text": "t", "basis": "cited", "sources": []}) == ""
    c = content("moringa", RJ)
    for sec in c["sections"]:
        for ln in sec["lines"]: assert pp._tags(ln) != "" or (ln["basis"] == "cited" and not ln["sources"]), (sec["key"], ln["text"][:40])


@pytest.mark.skipif(not HAS_PDFTOTEXT, reason="pdftotext (poppler) not installed")
def test_a_long_location_label_never_overlaps_the_date_on_the_cover():
    long_label = "SAMPLE: illustrative climate data, not measured (semi-arid, Rajasthan-like, near the Aravalli range, Jaipur district, Rajasthan, India)"
    pdf = pp.make_pathway_pdf(report_for("moringa"), RJ, location_label=long_label, area_acres=1, generated_on="2026-11-08")
    first = text_of(pdf).split("\f")[0]
    assert "Prepared on 2026-11-08" in first and "Prepared for:" in first
    assert not [ln for ln in first.splitlines() if "Prepared for" in ln and "Prepared on" in ln], "label and date must be on different lines"
    assert "..." in first or "Jaipur" in first
