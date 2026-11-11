"""
pathway_pdf.py - the farmer's personal guide as a PDF.

Two layers, so the personalisation is testable without parsing a PDF:
  build_content(...)  -> a plain dict: everything specific to THIS farmer (their location, their numbers, their gaps, their plot size) plus the guide text
  render_pdf(content) -> bytes (A4, reportlab, vector diagrams from pathway_diagrams)
Personal parts: the cover (location, date, tier), a climate chart of their own months against the crop's band and planting window, a gauge for each of their land checks,
"your priorities" ordered by their gaps, a planting plan sized to their plot, and one-line "for your land" leads inside the guide sections. Everything else is the sourced guide.
Helvetica/WinAnsi only: text goes through pdf_safe(). English only for now.
"""
import io
from datetime import date
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import pathway_diagrams as dg
from .crop_pathways import CHEMICAL_POLICY, SECTION_TITLES, build_report
from .pathway_plan import MONTH_NAMES, compute_plan, planting_window
from .pathway_data import CROPS

BY_ID = {c["id"]: c for c in CROPS}
GREEN, DEEP, INK, MUTED, GOLD = colors.HexColor("#2E7D4F"), colors.HexColor("#14432A"), colors.HexColor("#1F2A24"), colors.HexColor("#5C6B63"), colors.HexColor("#B07A10")
PALE, LINE = colors.HexColor("#EAF4EC"), colors.HexColor("#C9D3CC")
_REPLACE = {"\u2192": " to ", "\u2265": ">=", "\u2264": "<=", "\u2248": "about ", "\u2022": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ", "\u2011": "-", "\u2212": "-"}


def pdf_safe(s: Any) -> str:
    """Make text safe for the built-in Helvetica (WinAnsi / cp1252): swap common symbols, replace anything else with '?'."""
    s = "" if s is None else str(s)
    for k, v in _REPLACE.items():
        s = s.replace(k, v)
    s = s.encode("cp1252", "replace").decode("cp1252")
    while "  " in s:
        s = s.replace("  ", " ")
    return s


def _p(s): return escape(pdf_safe(s))


def _cap1(s: str) -> str:
    """Upper-case the first letter only (str.capitalize() would lower-case 'Kashmir')."""
    return s[:1].upper() + s[1:]


GAP_TITLES = {"heavy_soil": "Heavy soil", "ph": "Soil pH", "dry": "Low rainfall", "wet": "Too much rain", "frost": "Cold weather", "heat": "Heat", "not_kashmir_climate": "Climate", "indoor_climate_control": "Climate control", "dry_air": "Dry air"}


def gap_title(kind): return GAP_TITLES.get(kind, kind.replace("_", " ").capitalize())


def _loc_label(lat, lon, aoi_centre=None):
    if lat is not None and lon is not None:
        return f"{abs(lat):.3f}\u00b0{'N' if lat >= 0 else 'S'}, {abs(lon):.3f}\u00b0{'E' if lon >= 0 else 'W'}"
    if aoi_centre:
        return f"Your drawn area (centre {abs(aoi_centre[0]):.3f}\u00b0{'N' if aoi_centre[0] >= 0 else 'S'}, {abs(aoi_centre[1]):.3f}\u00b0{'E' if aoi_centre[1] >= 0 else 'W'})"
    return "Location not given"


def _check(verdict, name):
    return next((c for c in (verdict or {}).get("checks", []) if c["factor"] == name), None)


def _leads(verdict, window) -> Dict[str, List[str]]:
    """One-line 'for your land' sentences placed inside the guide sections, built from the farmer's own numbers."""
    out: Dict[str, List[str]] = {k: [] for k, _ in SECTION_TITLES}
    if window and window.get("rain_in_window_mm") is not None:
        out["land_and_planting"].append(f"In your climate the planting window ({window['label']}) brings about {window['rain_in_window_mm']:.0f} mm of rain and averages {window['mean_temp_in_window_c']} C.")
    if verdict:
        rain, ph = _check(verdict, "annual rainfall"), _check(verdict, "soil pH")
        trig = {g["if"]: g for g in verdict.get("triggered", [])}
        tex = _check(verdict, "soil texture")
        if tex and tex["status"] == "gap":
            out["land_and_planting"].append(f"Your soil is modelled as {str(tex['your_value']).lower()}, which drains slowly. Drainage comes first: build raised beds or mounds and mix in compost.")
        if rain and rain["status"] == "edge":
            out["irrigation"].append(f"Your rainfall (about {rain['your_value']:.0f} mm a year) is just outside this crop's usual {rain['crop_range']}, within the margin of error of modelled rainfall. Treat it as inside the range and watch the first season.")
        elif rain and rain["your_value"] is not None:
            if "dry" in trig and not trig["dry"]["addressed"]:
                out["irrigation"].append(f"Your area gets about {rain['your_value']:.0f} mm of rain a year, below this crop's usual {rain['crop_range']}. Irrigation matters more for you than for the average grower.")
            elif "dry" in trig:
                out["irrigation"].append(f"Your area gets about {rain['your_value']:.0f} mm of rain a year, below this crop's usual {rain['crop_range']}; you said you have reliable irrigation, which covers that.")
            elif "wet" in trig:
                out["irrigation"].append(f"Your area gets about {rain['your_value']:.0f} mm of rain a year, more than this crop's usual {rain['crop_range']}. Drainage comes before irrigation for you.")
                out["pests_diseases_organic"].append("Your wet climate raises disease pressure; put drainage, airflow and field hygiene first.")
            else:
                out["irrigation"].append(f"Your rainfall (about {rain['your_value']:.0f} mm a year) is inside this crop's usual range of {rain['crop_range']}.")
        if ph:
            if ph["your_value"] is None:
                out["nutrition_organic"].append("We could not read your soil pH. Get a soil test before adding anything to the soil.")
            elif ph["status"] == "edge":
                out["nutrition_organic"].append(f"Your soil pH (about {ph['your_value']}) is just outside the crop's range of {ph['crop_range']}, within the margin of error of the modelled value. A soil test will settle it.")
            elif ph["status"] == "gap":
                out["nutrition_organic"].append(f"Your soil pH is about {ph['your_value']} and the crop prefers {ph['crop_range']}. Get a soil test and build organic matter with compost.")
            else:
                out["nutrition_organic"].append(f"Your soil pH (about {ph['your_value']}) is inside the crop's range of {ph['crop_range']}. Still confirm with a soil test.")
        if "heat" in trig:
            out["pests_diseases_organic"].append("Your hottest month is hotter than this crop likes; protect plants and fruit from sun and keep the soil covered.")
    return out


def _priorities(report, verdict) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if verdict:
        for c in verdict.get("cautions", [])[:1]:
            if c.startswith("No climate data exists"):
                items.append({"title": "About your climate numbers", "text": c, "basis": "general", "src": []})
        applying = [g for g in verdict.get("triggered", []) if not g["addressed"]]
        for g in sorted(applying, key=lambda g: g["severity"] != "blocking"):
            items.append({"title": ("Stop and think: " if g["severity"] == "blocking" else "") + gap_title(g["if"]), "text": g["do"], "basis": g["basis"],
                          "src": [{"key": k, "url": report["sources"].get(k, "")} for k in g["src"] if k in report["sources"]]})
    if report["crop"]["setting"] == "controlled":
        items.append({"title": "Build only after you have seen one working", "text": "Visit or train with an operating unit before you spend money on a room, equipment or planting material.", "basis": "general", "src": []})
    if report["crop"]["setting"] != "controlled":
        items.append({"title": "Get your soil tested", "text": "Satellite and model data estimate soil pH and texture only roughly, and cannot see nutrients, salinity or soil health. Get a proper soil test (pH, organic carbon, nutrients, salinity) from your KVK or the state soil-testing laboratory; the Soil Health Card scheme provides this.", "basis": "general", "src": []})
    items.append({"title": "Talk to your KVK first", "text": "Take this guide to your nearest Krishi Vigyan Kendra and ask for their local advice and trusted planting material.", "basis": "general", "src": []})
    items.append({"title": "Start small", "text": "Plant or build a small trial first. Learn the crop on your own land, then expand from what actually worked.", "basis": "general", "src": []})
    items.append({"title": "Line up a buyer before you scale", "text": "Know who will buy, in what form and grade, and how it will reach them.", "basis": "general", "src": []})
    return items[:9]


def _greenness(profile):
    v = profile.get("monthly_ndvi")
    if v and len([x for x in v if x is not None]) >= 6:
        return {"values": v, "basis": profile.get("ndvi_basis")}
    return None


def build_content(report: Dict[str, Any], profile: Optional[Dict[str, Any]] = None, *, location_label: str = "Location not given", area_acres: Optional[float] = None,
                  generated_on: Optional[str] = None) -> Dict[str, Any]:
    """Everything personal and everything in the guide, as plain data."""
    crop_id = report["crop"]["id"]; crop = BY_ID[crop_id]; verdict = report.get("verdict"); profile = profile or {}
    rain, temp = profile.get("monthly_rain_mm"), profile.get("monthly_temp_c")
    window = planting_window(crop_id, rain, temp)
    fit = crop.get("fit")
    plan = compute_plan(crop_id, area_acres)
    leads = _leads(verdict, window)
    sections = []
    for key, title in SECTION_TITLES:
        sections.append({"key": key, "title": title, "leads": leads.get(key, []), "lines": report["guide"][key],
                         "diagrams": [{"id": d, "params": params, "caption": dg.CAPTIONS[d]} for d, params in (crop.get("diagrams", {}).get(key) or [])]})
    if plan and plan.get("spacing_m"):                          # the spacing diagram shows THEIR spacing
        for s in sections:
            for d in s["diagrams"]:
                if d["id"] == "spacing_grid": d["params"] = {"a": plan["spacing_m"][0], "b": plan["spacing_m"][1]}
    return {"meta": {"crop_id": crop_id, "crop_name": report["crop"]["name"], "location": location_label, "generated_on": generated_on or date.today().isoformat(),
                     "tier": verdict["tier"] if verdict else None, "tier_label": verdict["tier_label"] if verdict else None, "setting": report["crop"]["setting"]},
            "first_timer": report["first_timer"], "verdict": verdict, "controlled": report.get("controlled"),
            "chart": {"temp": temp, "rain": rain, "band": list(fit["tmean_c"]) if fit else None, "cold_limit": fit["cold_month_min_c"] if fit else None,
                      "hot_limit": fit["hot_month_max_c"] if fit else None, "window_months": window["months"] if window else None} if (temp or rain) else None,
            "window": window, "greenness": _greenness(profile), "plan": plan, "priorities": _priorities(report, verdict), "gap_actions": report["gap_actions"], "sections": sections,
            "sources": report["sources"], "chemical_policy": report["chemical_policy"], "disclaimer": report["disclaimer"]}


# ----------------------------------------------------------------------------------------------- rendering
def _styles():
    b = ParagraphStyle("b", fontName="Helvetica", fontSize=9.6, leading=14, textColor=INK)
    return {"b": b, "small": ParagraphStyle("s", parent=b, fontSize=8, leading=11, textColor=MUTED),
            "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=DEEP, spaceAfter=6, spaceBefore=2),
            "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=GREEN, spaceBefore=10, spaceAfter=4),
            "lead": ParagraphStyle("lead", parent=b, backColor=PALE, borderPadding=(4, 6, 4, 6), borderColor=GREEN, borderWidth=0.5, spaceBefore=4, spaceAfter=7, leftIndent=4, rightIndent=4),
            "li": ParagraphStyle("li", parent=b, leftIndent=11, bulletIndent=0, spaceAfter=3.5), "cap": ParagraphStyle("cap", parent=b, fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER),
            "cell": ParagraphStyle("cell", parent=b, fontSize=8.6, leading=11.5), "cellb": ParagraphStyle("cellb", parent=b, fontSize=8.6, leading=11.5, fontName="Helvetica-Bold")}


class _Outline(Flowable):
    def __init__(self, title, key, level=0):
        super().__init__(); self.title, self.key, self.level = title, key, level; self.width = self.height = 0
    def draw(self):
        c = self.canv; c.bookmarkPage(self.key); c.addOutlineEntry(self.title, self.key, level=self.level, closed=False)


def _fit(d, maxw):
    if d.width > maxw:
        f = maxw / d.width; d.scale(f, f); d.width *= f; d.height *= f
    return d


def _tags(line):
    if line["basis"] == "cited" and line["sources"]:
        return ' <font size="7" color="#2E7D4F">[' + ", ".join(s["key"] for s in line["sources"]) + "]</font>"
    return ' <font size="7" color="#B07A10">(general knowledge)</font>' if line["basis"] == "general" else ""


def _diagram(spec):
    f, p = dg.DIAGRAMS[spec["id"]], spec["params"] or {}
    return f(**p)


def _diagram_rows(specs, st, width):
    rows, half = [], (width - 12) / 2
    for i in range(0, len(specs), 2):
        pair = specs[i:i + 2]; cells = [[_fit(_diagram(s), half), Paragraph(_p(s["caption"]), st["cap"])] for s in pair]
        t = Table([[c[0] for c in cells], [c[1] for c in cells]], colWidths=[half + 6] * len(cells))
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        rows.append(KeepTogether([t, Spacer(1, 6)]))
    return rows


def _cover(canvas, doc, c):
    w, h = A4; m = c["meta"]
    canvas.saveState(); canvas.setFillColor(DEEP); canvas.rect(0, h - 262, w, 262, stroke=0, fill=1); canvas.setFillColor(GREEN); canvas.rect(0, h - 268, w, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#9ED6AE")); canvas.setFont("Helvetica-Bold", 11); canvas.drawString(51, h - 60, "VAYU  |  YOUR PERSONAL CROP GUIDE")
    canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 30)
    words, line, y = pdf_safe(m["crop_name"]).split(), "", h - 125
    for wd in words:
        if canvas.stringWidth((line + " " + wd).strip(), "Helvetica-Bold", 30) > w - 102: canvas.drawString(51, y, line); y -= 36; line = wd
        else: line = (line + " " + wd).strip()
    canvas.drawString(51, y, line); canvas.setFont("Helvetica", 13); canvas.setFillColor(colors.HexColor("#CFE8D5"))
    canvas.drawString(51, y - 30, "How to grow it on your land"); canvas.setFont("Helvetica", 10.5)
    lines, cur = [], ""
    for wd in f"Prepared for: {pdf_safe(m['location'])}".split():                  # long labels wrap onto their own lines, never into the date
        if canvas.stringWidth((cur + " " + wd).strip(), "Helvetica", 10.5) > w - 102: lines.append(cur); cur = wd
        else: cur = (cur + " " + wd).strip()
    lines.append(cur)
    if len(lines) > 2: lines = [lines[0], lines[1].rstrip(".,; ")[:-3] + "..."]
    for i, ln in enumerate(lines): canvas.drawString(51, h - 218 - 14 * i, ln)
    canvas.drawString(51, h - 218 - 14 * len(lines) - 2, f"Prepared on {m['generated_on']}")
    canvas.restoreState()


def _later(canvas, doc, c):
    w, h = A4; canvas.saveState(); canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5); canvas.line(51, h - 40, w - 51, h - 40); canvas.line(51, 42, w - 51, 42)
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(MUTED); canvas.drawString(51, h - 34, f"Vayu  |  {pdf_safe(c['meta']['crop_name'])}  |  {pdf_safe(c['meta']['location'])}")
    canvas.drawString(51, 31, "Advisory aid from published sources and general knowledge; not a guarantee of yield or income. Organic and cultural methods only."); canvas.drawRightString(w - 51, 31, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(c: Dict[str, Any]) -> bytes:
    st, W = _styles(), A4[0] - 102
    buf = io.BytesIO(); m, v, ft = c["meta"], c["verdict"], c["first_timer"]
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=51, rightMargin=51, topMargin=56, bottomMargin=56, title=pdf_safe(f"{m['crop_name']}: your personal growing guide"), author="Vayu",
                            subject=pdf_safe(f"Crop guide for {m['location']}"), creator="Vayu crop pathways")
    S: List[Any] = [Spacer(1, 252), _Outline("Cover", "cover")]
    S.append(Paragraph(_p(ft["why"]), st["b"])); S.append(Spacer(1, 10))
    chips = [["Difficulty", f"{ft['difficulty']} of 5"], ["Money needed", ft["capital"].capitalize()], ["Risk", ft["risk"].capitalize()], ["First return", ft["time_to_first_fruit"].split(" (")[0].split(";")[0]]]
    t = Table([[Paragraph(f'<font size="7.5" color="#5C6B63">{_p(a.upper())}</font><br/><b>{_p(b)}</b>', st["cell"]) for a, b in chips]], colWidths=[W / 4] * 4)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, -1), PALE), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    S += [t, Spacer(1, 14)]
    if v:
        S += [Paragraph("Can it grow on your land?", st["h2"]), dg.tier_scale(v["tier"], w=W), Spacer(1, 6), Paragraph(f"<b>{_p(v['tier_label'])}.</b> {_p(v['why'])}", st["b"]), Spacer(1, 6)]
    S.append(Paragraph("How to read this guide: <font color='#2E7D4F'>[S1]</font> marks a line backed by a source listed on the last page. <font color='#B07A10'>(general knowledge)</font> marks a line that has not been verified against a source. "
                       "Lines starting <b>For your land</b> use the numbers for your location.", st["small"]))
    inside = ["Your land at a glance: your own climate and soil against what this crop needs"] if v else []
    inside += ["Your priorities: what to do first, in order"] + (["Your planting plan: how much to buy and prepare for your plot"] if c["plan"] else [])
    inside += ["How to grow it: planting, water, nutrition, pests and diseases, monitoring, harvest, risks", "If your conditions are different, and the sources behind every line"]
    S += [Spacer(1, 10), Paragraph("In this guide", st["h2"])] + [Paragraph(_p(i), st["li"], bulletText="\u2022") for i in inside]
    S.append(PageBreak())
    S += [_Outline("Your land at a glance", "land"), Paragraph("Your land at a glance", st["h1"])]
    if c["chart"]:
        ch = c["chart"]; S += [Paragraph("Your own climate month by month, against what this crop likes", st["h2"]), _fit(dg.climate_chart(ch["temp"] or [None] * 12, ch["rain"] or [None] * 12, ch["band"], ch["cold_limit"], ch["hot_limit"], ch["window_months"]), W), Spacer(1, 4)]
        if c["window"]: S.append(Paragraph(f"<b>For your land:</b> {_p(c['window']['note'])}. {_p(c['window']['caution'])}", st["small"]))
    if c["greenness"]:
        S += [Paragraph("How green the land around you is, month by month", st["h2"]), _fit(dg.greenness_strip(c["greenness"]["values"]), W),
              Paragraph("Satellite NDVI (MODIS, " + _p(c["greenness"]["basis"] or "area average") + "): brown is bare ground, dark green is dense plants. It shows what is growing around you now, whether crop, forest or fallow, so it "
                        "tells you your local growing seasons. It does not decide whether this crop suits your land.", st["small"])]
    if v and v["checks"]:
        S.append(Paragraph("Your land against the crop's needs", st["h2"]))
        rows = [[Paragraph(f"<b>{_p(h)}</b>", st["cell"]) for h in ("Check", "Your value", "Where you sit", "Crop's range", "Result")]]
        for k in v["checks"]:
            val = "not available" if k["your_value"] is None else (k["your_value"] if isinstance(k["your_value"], str) else f"{k['your_value']}{k['unit']}")
            g = dg.range_gauge(k["your_value"], k["gauge"][0], k["gauge"][1], (k["unit"] or "").strip(), w=150, status=k["status"]) if k.get("gauge") else ""
            col = {"ok": "#2E9E5B", "gap": "#C8553D", "edge": "#B07A10"}.get(k["status"], "#5C6B63")
            rows.append([Paragraph(_p(k["factor"]), st["cell"]), Paragraph(_p(val), st["cell"]), g, Paragraph(_p(k["crop_range"]), st["cell"]), Paragraph(f'<font color="{col}"><b>{_p(k["status"])}</b></font>', st["cell"])])
        t = Table(rows, colWidths=[W * 0.27, W * 0.15, 158, W * 0.14, W * 0.09], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), PALE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        S.append(t)
    for caution in (v or {}).get("cautions", []): S.append(Paragraph(f"<b>Watch out:</b> {_p(caution)}", st["lead"]))
    if c["controlled"]: S += [Paragraph("What you would need to build", st["h2"]), Paragraph(f"{_p(_cap1(c['controlled']['what']))}. {_p(c['controlled']['season'])}", st["b"])]
    S += [_Outline("Your priorities", "priorities"), Paragraph("Your priorities", st["h1"]), Paragraph("Ordered by what matters most on your land.", st["small"]), Spacer(1, 4)]
    for i, it in enumerate(c["priorities"], 1):
        tag = (' <font size="7" color="#2E7D4F">[' + ", ".join(s["key"] for s in it["src"]) + "]</font>") if it["src"] else (' <font size="7" color="#B07A10">(general knowledge)</font>' if it["basis"] == "general" else "")
        S.append(Paragraph(f"<b>{i}. {_p(it['title'])}</b><br/>{_p(it['text'])}{tag}", st["li"]))
    if c["plan"]:
        pl = c["plan"]; S.append(_Outline("Your planting plan", "plan")); plan_head = Paragraph(f"Your planting plan for {pl['area_acres']:g} acre{'s' if pl['area_acres'] != 1 else ''}", st["h1"])
        rows = [[Paragraph("<b>What</b>", st["cell"]), Paragraph("<b>How much</b>", st["cell"]), Paragraph("<b>Basis</b>", st["cell"])]]
        for r in pl["rows"]:
            basis = "your plot size x cited amounts" if r["basis"] == "arithmetic" else "an example, not a recommendation"
            rows.append([Paragraph(_p(r["label"]), st["cell"]), Paragraph(f"<b>{_p(r['value'])}</b>", st["cell"]), Paragraph(_p(basis) + ((" [" + ", ".join(r["src"]) + "]") if r["src"] else ""), st["small"])])
        t = Table(rows, colWidths=[W * 0.5, W * 0.22, W * 0.28]); t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, LINE), ("BACKGROUND", (0, 0), (-1, 0), PALE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        S.append(KeepTogether([plan_head, t, Paragraph(_p(pl["note"]), st["small"]), Spacer(1, 8)]))
    for sec in c["sections"]:
        block: List[Any] = [_Outline(sec["title"], "sec_" + sec["key"], 0), Paragraph(_p(sec["title"]), st["h1"])]
        for ld in sec["leads"]: block.append(Paragraph(f"<b>For your land:</b> {_p(ld)}", st["lead"]))
        S += block
        paras = [Paragraph(f"{_p(ln['text'])}{_tags(ln)}", st["li"], bulletText="\u2022") for ln in sec["lines"]]
        rows = _diagram_rows(sec["diagrams"], st, W)
        S += paras[:-1]
        S.append(KeepTogether([paras[-1], Spacer(1, 4)] + rows[:1]))      # the last bullet travels with the first diagram row
        S += rows[1:]
    S += [Spacer(1, 10), _Outline("If your conditions are different", "gaps"), Paragraph("If your conditions are different", st["h1"]), Paragraph("What to do if your land, or the season, turns out different from the numbers above.", st["small"]), Spacer(1, 4)]
    for g in c["gap_actions"]:
        tag = (' <font size="7" color="#2E7D4F">[' + ", ".join(s["key"] for s in g["sources"]) + "]</font>") if g["sources"] else (' <font size="7" color="#B07A10">(general knowledge)</font>' if g["basis"] == "general" else "")
        S.append(Paragraph(f"<b>{_p(gap_title(g['if']))}</b> ({_p(g['severity'])}): {_p(g['do'])}{tag}", st["li"], bulletText="\u2022"))
    S += [Spacer(1, 8), _Outline("About chemicals", "chem"), Paragraph("About chemicals", st["h2"]), Paragraph(_p(c["chemical_policy"]), st["b"]), _Outline("Sources", "sources"), Paragraph("Sources", st["h1"])]
    for k, u in sorted(c["sources"].items(), key=lambda kv: int(kv[0][1:])): S.append(Paragraph(f"<b>[{_p(k)}]</b> {_p(u)}", st["small"]))
    S += [Spacer(1, 8), Paragraph(_p(c["disclaimer"]), st["small"])]
    doc.build(S, onFirstPage=lambda cv, d: _cover(cv, d, c), onLaterPages=lambda cv, d: _later(cv, d, c))
    return buf.getvalue()


def make_pathway_pdf(report: Dict[str, Any], profile: Optional[Dict[str, Any]], *, location_label: str, area_acres: Optional[float] = None, generated_on: Optional[str] = None) -> bytes:
    return render_pdf(build_content(report, profile, location_label=location_label, area_acres=area_acres, generated_on=generated_on))
