#!/usr/bin/env python3
"""
parse_dacp.py - PILOT parser for ICAR-CRIDA District Agriculture Contingency Plans (DACPs).

  python parse_dacp.py <file.pdf|file.txt> [...]  > out.json     (PDF text needs `pip install pypdf`)

Extracts: ICAR agro-ecological sub-region, Planning-Commission and NARP agro-climatic zones, normal rainfall (annual, SW monsoon), major soils (% area),
land use / irrigation totals, and the field-crop area table by season split irrigated / rainfed, plus horticulture crops.
Every district gets `qa` flags (missing sections, rows whose irrigated+rainfed != total, season totals != grand total, shares that do not add up).
STATUS: written against ONE document (Agra, UP43-Agra-26.07.14.pdf). Templates differ by state; measure the parse rate and QA-flag rate on ~20
districts across states BEFORE trusting any extracted number. Areas are in '000 ha as printed. Nothing here is used by the engine yet.
"""
import json, re, sys
from pathlib import Path

NUM = r"(?:[-\u2013]|\d+(?:\.\d+)?)"
ROW8 = re.compile(rf"^([A-Za-z][A-Za-z .&/()'-]*?)\s+({NUM}(?:\s+{NUM}){{7}})\s*$")
ROW3 = re.compile(rf"^([A-Za-z][A-Za-z .&/()'-]*?)\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
# DACP crop name -> engine crop_id (only crops the engine models; None = not modelled)
CROP_MAP = {"wheat": "wheat", "paddy": "rice_paddy", "rice": "rice_paddy", "rapeseed mustard": "mustard", "mustard": "mustard", "jowar": "jowar_sorghum",
            "sorghum": "jowar_sorghum", "gram": "gram_chickpea", "chickpea": "gram_chickpea", "maize": "maize", "cotton": "cotton", "groundnut": "groundnut",
            "mango": "mango", "potato": "potato", "onion": "onion", "barley": "barley", "soybean": "soybean"}


def num(tok):
    return None if tok in ("-", "\u2013") else float(tok)


def read_text(path):
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    return p.read_text(encoding="utf-8", errors="replace")


def between(text, start, end):
    m = re.search(start, text, re.I)
    if not m: return ""
    rest = text[m.end():]
    e = re.search(end, rest, re.I)
    return rest[: e.start()] if e else rest


def first(rx, text, g=1, cast=str):
    m = re.search(rx, text, re.I | re.M)
    return cast(m.group(g).strip()) if m else None


def parse_text(text, source=""):
    qa, d = [], {"source": source}
    d["icar_agro_ecological_sub_region"] = (first(r"Agro-Ecological Sub Region\s*\(ICAR\)\s*(.+)$", text) or "").rstrip(", ") or None
    d["planning_commission_zone"] = first(r"Agro-Climatic Zone\s*\(Planning Commission\)\s*(.+)$", text)
    d["narp_zone"] = first(r"Agro-Climatic Zone\s*\(NARP\)\s*(.+)$", text)
    d["annual_rain_mm"] = first(r"^\s*Annual\s+(\d+(?:\.\d+)?)\s+\d+", text, cast=float)
    d["sw_monsoon_rain_mm"] = first(r"SW monsoon[^\n]*?\)\s+(\d+(?:\.\d+)?)\s+\d+", text, cast=float)
    land = {"net_sown_kha": first(r"Net sown area\s+(\d+(?:\.\d+)?)", text, cast=float), "gross_cropped_kha": first(r"Gross cropped area\s+(\d+(?:\.\d+)?)", text, cast=float),
            "net_irrigated_kha": first(r"Net irrigation area\s+(\d+(?:\.\d+)?)", text, cast=float), "gross_irrigated_kha": first(r"Gross irrigated area\s+(\d+(?:\.\d+)?)", text, cast=float),
            "rainfed_kha": first(r"Rain\s*fed area\s+(\d+(?:\.\d+)?)", text, cast=float)}
    d["land"] = land
    if land["net_sown_kha"] and land["net_irrigated_kha"] is not None: land["irrigated_share_net"] = round(land["net_irrigated_kha"] / land["net_sown_kha"], 3)
    if land["gross_cropped_kha"] and land["gross_irrigated_kha"] is not None: land["irrigated_share_gross"] = round(land["gross_irrigated_kha"] / land["gross_cropped_kha"], 3)

    soils = []
    for ln in between(text, r"1\.4\s+Major Soils[^\n]*\n", r"\n\s*1\.5").splitlines():
        m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*$", ln.strip())
        if m: soils.append({"soil": m.group(1), "area_kha": float(m.group(2)), "pct": float(m.group(3))})
    d["soils"] = soils
    if soils and abs(sum(s["pct"] for s in soils) - 100) > 3: qa.append(f"soil % sums to {sum(s['pct'] for s in soils):.1f}")

    crops = []
    for ln in between(text, r"Area under major field crops", r"Area under Horticulture").splitlines():
        m = ROW8.match(ln.strip())
        if not m: continue
        t = [num(x) for x in m.group(2).split()]
        r = {"crop": m.group(1).strip(), "engine_crop_id": CROP_MAP.get(m.group(1).strip().lower()),
             "kharif": {"irrigated": t[0], "rainfed": t[1], "total": t[2]}, "rabi": {"irrigated": t[3], "rainfed": t[4], "total": t[5]}, "summer": t[6], "total": t[7]}
        for s in ("kharif", "rabi"):
            a = r[s]
            if a["total"] is not None and abs((a["irrigated"] or 0) + (a["rainfed"] or 0) - a["total"]) > max(0.15, 0.03 * a["total"]): qa.append(f"{r['crop']} {s}: irrigated+rainfed != total")
        parts = [r["kharif"]["total"], r["rabi"]["total"], r["summer"]]
        if r["total"] is not None and abs(sum(p or 0 for p in parts) - r["total"]) > max(0.15, 0.03 * r["total"]): qa.append(f"{r['crop']}: season totals != grand total")
        crops.append(r)
    d["field_crops"] = crops
    if not crops: qa.append("no field-crop rows parsed")

    hort = []
    for ln in between(text, r"Area under Horticulture", r"Area under fodder").splitlines():
        m = ROW3.match(ln.strip())
        if m and not re.match(r"(Crops|Total|Vegetables)", m.group(1)):
            hort.append({"crop": m.group(1).strip(), "engine_crop_id": CROP_MAP.get(m.group(1).strip().lower()), "total": num(m.group(2)), "irrigated": num(m.group(3)), "rainfed": num(m.group(4))})
    d["horticulture"] = hort
    for k in ("narp_zone", "annual_rain_mm", "net_sown_kha"):
        if d.get(k) is None and land.get(k) is None: qa.append(f"missing {k}")
    d["qa"] = qa
    return d


if __name__ == "__main__":
    out = [parse_text(read_text(p), source=str(p)) for p in sys.argv[1:]]
    print(json.dumps(out, indent=1))
