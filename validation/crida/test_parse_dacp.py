import sys; from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from parse_dacp import parse_text, read_text
D = parse_text(read_text(Path(__file__).parent / "fixtures" / "agra_excerpt.txt"), "agra")

def test_zones_and_rain():
    assert D["narp_zone"].startswith("UP-3") and D["planning_commission_zone"] == "Upper Gangetic Plain Region" and D["icar_agro_ecological_sub_region"] == "Western Plain Zone"
    assert D["annual_rain_mm"] == 655.5 and D["sw_monsoon_rain_mm"] == 584.3
def test_land_and_irrigation_share():
    L = D["land"]; assert L["net_sown_kha"] == 284.3 and L["gross_irrigated_kha"] == 283.6
    assert abs(L["irrigated_share_net"] - 0.908) < 0.002 and abs(L["irrigated_share_gross"] - 0.669) < 0.002
def test_soils():
    assert len(D["soils"]) == 4 and D["soils"][0]["pct"] == 67.0
def test_crop_table_columns():
    c = {r["crop"]: r for r in D["field_crops"]}; assert len(c) == 6
    assert c["Wheat"]["rabi"] == {"irrigated": 137.263, "rainfed": 0.3, "total": 137.6} and c["Wheat"]["kharif"]["total"] is None
    assert c["Pearl millet"]["kharif"]["rainfed"] == 115.7 and c["Paddy"]["engine_crop_id"] == "rice_paddy" and c["Rapeseed Mustard"]["engine_crop_id"] == "mustard"
def test_horticulture_and_qa():
    assert {h["crop"] for h in D["horticulture"]} >= {"Mango", "Guava", "Potato", "Onion"} and D["qa"] == []
def test_qa_flags_inconsistent_rows():
    bad = parse_text("Area under major field crops\nWheat - - - 100 50 120 - 120\nArea under Horticulture\n")
    assert any("irrigated+rainfed" in q for q in bad["qa"])
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]; bad = 0
    for f in fns:
        try: f(); print("ok  ", f.__name__)
        except AssertionError as e: bad += 1; print("FAIL", f.__name__, e)
    print(f"{len(fns)-bad}/{len(fns)} passed"); sys.exit(bad)
