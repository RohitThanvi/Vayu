"""High-value crop pathway knowledge base + feasibility engine. Pure Python, no GEE.  pytest tests/test_pathways.py
Data tests guard the sourcing and the NO-CHEMICALS policy; engine tests check tier logic with crafted climates (mechanics, not validation)."""
import asyncio, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import pytest
from agri_fixtures import LOCATIONS
from app.services.agri import crop_pathways as cp
from app.services.agri.pathway_data import CROPS, SEE_KVK

IDS = [c["id"] for c in CROPS]
SECTIONS = [k for k, _ in cp.SECTION_TITLES]
GOOD = {"tmean_c": 25.0, "coldest_month_c": 18.0, "hottest_month_c": 32.0, "annual_rain_mm": 1000.0, "ph": 6.5}


def all_text(c):
    out = [c["name"], c["first_timer"]["why"], c["first_timer"]["time_to_first_fruit"]] + [g["do"] for g in c["gaps"]]
    out += [t for k in SECTIONS for t, _b, _s in c["guide"][k]] + [t for t, _b, _s in c.get("site_cautions", [])]
    if c.get("controlled"): out += list(c["controlled"].values())
    return out


def test_eight_crops_with_unique_ids():
    assert len(IDS) == len(set(IDS)) == 8 and {"dragon_fruit", "saffron", "avocado", "oyster_mushroom"} <= set(IDS)


@pytest.mark.parametrize("cid", IDS)
def test_structure_and_sourcing(cid):
    c = cp.BY_ID[cid]
    assert c["setting"] in ("open_field", "controlled") and 1 <= c["first_timer"]["difficulty"] <= 5
    assert (c["fit"] is None) == (c["setting"] == "controlled") and (c["setting"] != "controlled" or c.get("controlled"))
    for k in SECTIONS:
        assert c["guide"][k], f"{cid}: empty section {k}"
    assert all(u.startswith("https://") for u in c["sources"].values())
    lines = [(k, ln) for k in SECTIONS for ln in c["guide"][k]] + [("gap", (g["do"], g["basis"], g["src"])) for g in c["gaps"]] + [("caution", ln) for ln in c.get("site_cautions", [])]
    for where, (text, basis, keys) in lines:
        assert basis in ("cited", "general") and isinstance(keys, list)
        assert text.strip() and not text.strip().startswith("TODO")
        assert all(k in c["sources"] for k in keys), f"{cid}/{where}: unknown source key in {keys}"
        if basis == "cited":
            assert keys, f"{cid}/{where}: a 'cited' line must name a source: {text[:60]}"
    assert SEE_KVK in c["guide"]["pests_diseases_organic"], "every crop must hand off to the KVK for anything chemical"
    for g in c["gaps"]:
        assert g["severity"] in ("fixable", "blocking")
    if c["fit"]:
        f = c["fit"]
        for lo, hi in (f["tmean_c"], f["rain_mm"], f["ph"]): assert lo < hi
        assert f["cold_month_min_c"] < f["tmean_c"][1] and f["hot_month_max_c"] > f["tmean_c"][0]
        assert f["cold_month_block_c"] < f["cold_month_min_c"], "hard-freeze limit must be colder than the cool limit"


BANNED = ["chlorpyrifos", "mancozeb", "imidacloprid", "carbendazim", "bavistin", "dimethoate", "malathion", "thiamethoxam", "quinalphos", "monocrotophos", "cypermethrin", "profenofos",
          "copper oxychloride", "bordeaux", "streptomycin", "streptocycline", "metalaxyl", "tebuconazole", "azoxystrobin", "glyphosate", "paraquat", "atrazine", "pendimethalin",
          "urea", "muriate of potash", "potassium chloride", "single super phosphate", "diammonium", "npk", "dap "]
DOSE = re.compile(r"\b\d+(\.\d+)?\s*(ml|g|gm|mg|kg)\s*(/|per)\s*(l|litre|liter|lt)\b|\b\d+(\.\d+)?\s*(ml|g|gm)\s*/\s*(l|litre|lt)\b|\b\d+(\.\d+)?\s*%\s*(solution|spray|emulsion)", re.I)


@pytest.mark.parametrize("cid", IDS)
def test_no_chemical_products_or_spray_doses_anywhere(cid):
    blob = " ".join(all_text(cp.BY_ID[cid])).lower() + " " + cp.CHEMICAL_POLICY.lower()
    hits = [b for b in BANNED if b in blob.replace("(", " ").replace(")", " ") and b not in cp.CHEMICAL_POLICY.lower()]
    assert not hits, f"{cid}: chemical names in guide text: {hits}"
    assert not DOSE.search(" ".join(all_text(cp.BY_ID[cid]))), f"{cid}: spray-dose pattern found"


def test_policy_statement_is_present_in_every_report():
    for cid in IDS:
        r = cp.build_report(cid); assert "does not name or dose pesticides" in r["chemical_policy"]
        assert "About chemicals" in cp.render_markdown(r)


# ---------------- engine ----------------
def tier(cid, **kw):
    clim = {**GOOD, **{k: v for k, v in kw.items() if k != "irrigated"}}
    return cp.assess(cid, clim, kw.get("irrigated", False))


def test_clean_climate_is_tier_1_and_frost_sensitive_crops_still_get_a_caution():
    v = tier("dragon_fruit"); assert v["tier"] == 1 and v["triggered"] == [] and any("cold nights" in c for c in v["cautions"])


def test_frost_severity_depends_on_how_cold():
    assert tier("dragon_fruit", coldest_month_c=3)["tier"] == 2          # cool but above the hard-freeze limit (0): fixable
    assert tier("dragon_fruit", coldest_month_c=-2)["tier"] == 4 and tier("dragon_fruit", coldest_month_c=0)["tier"] == 4
    v = tier("dragon_fruit", coldest_month_c=-2); assert v["triggered"][0]["if"] == "frost" and v["triggered"][0]["severity"] == "blocking" and "hard-freeze" in v["checks"][0]["note"]


def test_rainfall_gaps_and_irrigation():
    assert tier("dragon_fruit", annual_rain_mm=300)["tier"] == 2
    v = tier("dragon_fruit", annual_rain_mm=300, irrigated=True); assert v["tier"] == 1 and v["triggered"][0]["addressed"] and "irrigation assumed" in v["why"]
    assert tier("dragon_fruit", annual_rain_mm=2500)["tier"] == 2 and tier("dragon_fruit", annual_rain_mm=2500, irrigated=True)["tier"] == 2   # irrigation does not fix too much rain


def test_ph_gap_and_unknown_ph():
    assert tier("dragon_fruit", ph=8.3)["tier"] == 2
    v = tier("dragon_fruit", ph=None); assert v["tier"] == 1 and any(c["factor"] == "soil pH" and c["status"] == "unknown" for c in v["checks"])


def test_avocado_extreme_heat_is_blocking_and_hot_wind_caution_always_shows():
    assert tier("avocado", hottest_month_c=39)["tier"] == 4 and tier("avocado", hottest_month_c=36)["tier"] == 2
    assert any("hot dry winds" in c.lower() for c in tier("avocado")["cautions"])


def test_controlled_crops_are_tier_3_whatever_the_climate():
    for cid in ("saffron", "oyster_mushroom"):
        for clim in (GOOD, {**GOOD, "coldest_month_c": -10}, {}):
            v = cp.assess(cid, clim); assert v["tier"] == 3 and "not what decides" in v["why"]


def test_missing_temperature_cannot_be_assessed():
    v = cp.assess("dragon_fruit", {"annual_rain_mm": 900, "ph": 6.5}); assert v["tier"] is None and v["tier_label"] == "Cannot assess"


def test_unknown_crop_raises_keyerror():
    with pytest.raises(KeyError): cp.assess("not_a_crop", GOOD)


def test_fixture_locations_behave_realistically():
    rj, hm = cp.climate_from_profile(LOCATIONS["rajasthan_semiarid"]), cp.climate_from_profile(LOCATIONS["himalayan_cold"])
    assert cp.assess("pomegranate", rj)["tier"] == 1 and cp.assess("moringa", rj)["tier"] == 1     # semi-arid fits the drought-hardy crops
    assert cp.assess("dragon_fruit", hm)["tier"] == 4 and cp.assess("avocado", hm)["tier"] == 4      # hard frost blocks the tropical/subtropical ones
    assert cp.assess("pomegranate", hm)["tier"] == 2                                                   # deciduous and cold-hardier


def test_climate_from_profile_computes_the_extremes_and_tolerates_gaps():
    c = cp.climate_from_profile({"monthly_temp_c": [10, 20, None, 30] + [15] * 8, "annual_rain_mm": 700, "ph": 7.0})
    assert (c["coldest_month_c"], c["hottest_month_c"], c["annual_rain_mm"], c["ph"]) == (10, 30, 700, 7.0)
    assert c["tmean_c"] == pytest.approx((10 + 20 + 30 + 15 * 8) / 11)
    e = cp.climate_from_profile({}); assert e["coldest_month_c"] is None and e["tmean_c"] is None


# ---------------- report / manual ----------------
def test_report_sources_are_exactly_the_ones_cited_and_have_urls():
    for cid in IDS:
        r = cp.build_report(cid, GOOD)
        cited = {s["key"] for sec in r["guide"].values() for ln in sec for s in ln["sources"]} | {s["key"] for g in r["gap_actions"] for s in g["sources"]}
        assert set(r["sources"]) == cited and all(u.startswith("https://") for u in r["sources"].values())


def test_markdown_labels_general_knowledge_and_cites_sources():
    md = cp.render_markdown(cp.build_report("dragon_fruit", {**GOOD, "ph": 8.0}))
    assert "# Dragon fruit (Kamalam): how to grow it" in md and "## Can it grow here? Open-field with help" in md
    assert "(general knowledge, not verified against a source)" in md and "[S2]" in md and "## Sources" in md and "| soil pH | 8.0 | 5.5-7.0 | gap |" in md
    assert "None" not in md and "nan" not in md.lower().replace("finance", "")
    plain = cp.render_markdown(cp.build_report("moringa")); assert "Can it grow here?" not in plain and "## Irrigation" in plain


def test_endpoints_list_manual_and_404():
    from app.api import agri_endpoints as a
    from fastapi import HTTPException
    assert len(asyncio.run(a.pathway_crops())["crops"]) == 8
    r = asyncio.run(a.pathway_manual("saffron")); assert r.status_code == 200 and r.media_type == "application/pdf" and r.body.startswith(b"%PDF") and r.body.rstrip().endswith(b"%%EOF")
    assert "Vayu_saffron_guide.pdf" in r.headers["content-disposition"]
    with pytest.raises(HTTPException) as e:
        asyncio.run(a.pathway_manual("nope"))
    assert e.value.status_code == 404
