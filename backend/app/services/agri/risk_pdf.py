"""
risk_pdf.py - the detailed agricultural risk assessment (risk_assessment.compute_risk_assessment) as a PDF report with every metric.

Two layers, like pathway_pdf.py, so the content is testable without parsing a PDF:
  build_content(assessment, location_label=..., generated_on=...) -> plain dict: every number, class, source, reason and caveat shown in the report
  render_pdf(content) -> bytes (A4, reportlab, vector charts from risk_diagrams / pathway_diagrams)
Report order: cover and summary, stress indicators (chart, values table, provenance table, notes), land vulnerability, soil condition (descriptive, not scored), climate, sensitivity,
the original alert score, data gaps, methodology, limitations, sources.
Honesty rules carried into the text: nothing is validated against yield or loss data; class-to-points scaling is a convention; modelled layers are labelled modelled; an unavailable
indicator is listed with its reason and never shown as zero. Helvetica / WinAnsi only (text goes through pdf_safe). English only.
"""
import io
from datetime import date
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import pathway_diagrams as dg
from . import risk_diagrams as rd
from .pathway_pdf import DEEP, GREEN, INK, LINE, MUTED, PALE, _Outline, _fit, _p, _styles, pdf_safe
from .risk_assessment import FAMILIES, FAMILY_LABEL, RISK_ORDER, SENSITIVITY_ROBUST_RANGE

# Which citations back which indicator (the assessment names a free-text source per indicator, not citation keys).
INDICATOR_CITES = {"vhi": ["kogan"], "ndvi_z": ["usdm"], "rain_pct": ["chirps", "spi", "usdm"], "rz_pct": ["smap", "usdm"], "gw_pct": ["grace", "usdm"]}
ERROR_LABELS = {"vdi": "Vegetation health index", "baseline": "NDVI seasonal baseline", "prec": "Rainfall history", "rz": "Root-zone soil moisture", "gw": "Groundwater (GRACE)",
                "profile": "Land profile (soil, slope, climate)", "v1": "Original alert score"}
DISCLAIMER = ("This is a documented, standardised stress read built from satellite products and models. It is not a forecast of yield or loss and has not been validated against yield or loss data. "
              "Modelled layers (soil, SMAP root-zone moisture, climate) are estimates, not field measurements. For decisions that matter, confirm on the ground (soil test, local agriculture office or KVK).")


def _num(v: Any, nd: int = 1) -> str:
    if v is None or isinstance(v, bool): return "n/a"
    if isinstance(v, float):
        t = f"{v:.{nd}f}"
        return t.rstrip("0").rstrip(".") if "." in t else t           # strip only a fractional tail: "50" must stay "50", never "5"
    return str(v)


def _risk_index(cls: Optional[str]) -> Optional[int]:
    return RISK_ORDER.index(cls) if cls in RISK_ORDER else None


def _indicator_row(i: Dict[str, Any]) -> Dict[str, Any]:
    ok = i.get("status") == "ok"
    base = {"id": i["id"], "name": i["name"], "family": FAMILY_LABEL.get(i.get("family"), i.get("family")), "status": i.get("status"), "source": i.get("source"), "cites": INDICATOR_CITES.get(i["id"], []) if ok else []}       # an unavailable indicator cites nothing: no dangling tag, no source listed that was not used
    if not ok:
        return {**base, "value": "not available", "baseline": f"Not scored. Reason: {i.get('reason') or 'unknown'}", "percentile": "-", "class": "-", "stress": "-", "reliability": "-", "resolution": "-", "window": "-", "note": ""}
    unit = f" {i['unit']}" if i.get("unit") else ""
    if i.get("unit") == "0-100": unit = " (on a 0-100 scale)"
    return {**base, "value": f"{_num(i['value'], 2)}{unit}", "baseline": i.get("baseline") or "n/a", "percentile": _num(i.get("percentile")) if i.get("percentile") is not None else "n/a (VHI is classed directly)",
            "class": i.get("class") or "n/a", "stress": _num(i.get("stress"), 0), "reliability": i.get("reliability") or "n/a", "resolution": i.get("resolution") or "n/a", "window": i.get("window") or "n/a", "note": i.get("note") or ""}


def _sensitivity_text(s: Dict[str, Any]) -> str:
    if not s or s.get("range") is None: return "The stress score could not be computed, so there is nothing to test."
    t = (f"Across the equal-weight headline, four alternative weightings and each leave-one-family-out variant, the stress score runs from {_num(s['min'])} to {_num(s['max'])} points "
         f"(spread {_num(s['range'])}). ")
    t += (f"A spread of {SENSITIVITY_ROBUST_RANGE:g} points or less is called robust: this one is robust, so the result does not hinge on how the families are weighted."
          if s.get("robust") else f"This is more than {SENSITIVITY_ROBUST_RANGE:g} points, so the result depends noticeably on the weighting. Read the class, not the exact number.")
    if s.get("most_influential"): t += f" The variant that moves it most is \"{s['most_influential']}\"."
    return t


def build_content(a: Dict[str, Any], *, location_label: str = "Location not given", generated_on: Optional[str] = None) -> Dict[str, Any]:
    """Everything the report says, as plain data."""
    risk, stress, vuln, conf = a.get("risk") or {}, a.get("stress") or {}, a.get("vulnerability") or {}, a.get("confidence") or {}
    inds = [_indicator_row(i) for i in a.get("indicators") or []]
    fams = [{"id": f, "label": ((stress.get("families") or {}).get(f) or {}).get("label") or FAMILY_LABEL[f], "score": ((stress.get("families") or {}).get(f) or {}).get("score")} for f in FAMILIES]
    cls = risk.get("class") or "Cannot assess"
    if cls == "Cannot assess": headline = "Risk class: cannot be assessed. No stress indicator was available for this area and date."
    else: headline = f"Risk class: {cls}. Stress is {str(stress.get('class')).lower()} ({_num(stress.get('score'))} of 100); land vulnerability is {str(vuln.get('class')).lower()}" + (f" ({_num(vuln.get('score'))} of 100)." if vuln.get("score") is not None else ".")
    cover_missing = conf.get("missing") or []
    conf_text = (f"Confidence is {conf.get('label', 'n/a')} ({_num(conf.get('score'), 2)}): {conf.get('coverage', 'n/a')} contributed, and the mean reliability of the indicators used is {_num(conf.get('mean_reliability'), 2)}. "
                 "Confidence = coverage of the indicator families x mean indicator reliability (high 1.0, medium 0.7, low 0.4)."
                 + (f" Families with no data: {', '.join(FAMILY_LABEL[m].lower() for m in cover_missing if m in FAMILY_LABEL)}." if cover_missing else ""))
    sens = a.get("sensitivity") or {}
    variants = [{"name": k, "score": v} for k, v in (sens.get("variants") or {}).items()]
    soil, clim, legacy = a.get("soil_condition"), a.get("climate"), a.get("legacy")
    vf = [{"name": f["name"], "value": f["value"], "class": f["class"], "points": f["points"], "source": f.get("source") or "", "note": f.get("note") or ""} for f in vuln.get("factors") or []]
    cites_all = a.get("citations") or {}
    used: List[str] = ["ipcc"]
    for i in inds: used += i["cites"]
    for f in vf: used += [k.strip() for k in f["source"].split(",") if k.strip()]
    for r in (soil or {}).get("properties") or []: used += [k.strip() for k in (r.get("source") or "").split(",") if k.strip()]
    if clim and (clim.get("aridity") or {}): used.append("unep")
    seen: List[str] = []
    for k in used:
        if k in cites_all and k not in seen: seen.append(k)
    ndvi = (clim or {}).get("monthly_ndvi")
    return {"meta": {"location": location_label, "as_of": a.get("as_of"), "generated_on": generated_on or date.today().isoformat(), "risk_class": cls, "risk_index": _risk_index(cls),
                     "stress_class": stress.get("class") or "unknown", "stress_score": stress.get("score"), "vulnerability_class": vuln.get("class") or "unknown", "vulnerability_score": vuln.get("score"),
                     "confidence_label": conf.get("label"), "confidence_score": conf.get("score")},
            "headline": headline, "risk_rule": risk.get("rule") or "", "confidence_text": conf_text, "summary": a.get("summary") or {"drivers": [], "no_signal": [], "unavailable": []},
            "families": fams, "indicators": inds, "indicator_chart": [i for i in a.get("indicators") or []],
            "vulnerability": {"score": vuln.get("score"), "class": vuln.get("class"), "factors": vf, "factors_used": vuln.get("factors_used", 0), "of": vuln.get("of", 5),
                              "explanation": ("Land vulnerability is the mean of the documented ordinal ratings below (0 = least vulnerable, 100 = most). It is a static property of the land, not of this season. "
                                              f"{vuln.get('factors_used', 0)} of {vuln.get('of', 5)} factors could be rated here." if vf else "No land factor could be rated (the land profile was unavailable), so vulnerability is unknown and does not change the risk class.")},
            "soil": soil, "climate": clim, "ndvi": ndvi if ndvi and len([x for x in ndvi if x is not None]) >= 6 else None,
            "sensitivity": {"variants": variants, "min": sens.get("min"), "max": sens.get("max"), "range": sens.get("range"), "robust": sens.get("robust"), "most_influential": sens.get("most_influential"), "text": _sensitivity_text(sens),
                            "raw": sens.get("variants") or {}},
            "legacy": legacy, "errors": [{"source": ERROR_LABELS.get(k, k), "message": str(v)[:160]} for k, v in (a.get("errors") or {}).items()],
            "methodology": a.get("methodology") or [], "limitations": a.get("limitations") or [], "citations": [{"key": k, "text": cites_all[k]} for k in seen], "disclaimer": DISCLAIMER}


# ----------------------------------------------------------------------------------------------- rendering
def _tbl(rows, widths, st, header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    sty = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if header: sty.append(("BACKGROUND", (0, 0), (-1, 0), PALE))
    t.setStyle(TableStyle(sty)); return t


def _hdr(labels, st): return [Paragraph(f"<b>{_p(h)}</b>", st["cell"]) for h in labels]


def _cell(s, st, bold=False): return Paragraph(f"<b>{_p(s)}</b>" if bold else _p(s), st["cell"])


def _tag(keys): return (' <font size="7" color="#2E7D4F">[' + ", ".join(keys) + "]</font>") if keys else ""


def _cover(canvas, doc, c):
    w, h = A4; m = c["meta"]
    canvas.saveState(); canvas.setFillColor(DEEP); canvas.rect(0, h - 262, w, 262, stroke=0, fill=1); canvas.setFillColor(GREEN); canvas.rect(0, h - 268, w, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#9ED6AE")); canvas.setFont("Helvetica-Bold", 11); canvas.drawString(51, h - 60, "VAYU  |  DETAILED RISK ASSESSMENT")
    canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 28); canvas.drawString(51, h - 112, "Agricultural risk assessment")
    idx = m["risk_index"]; pill = rd.CLASS_COLORS[idx] if idx is not None else colors.HexColor("#5C6B63")
    canvas.setFillColor(pill); canvas.roundRect(51, h - 170, 210, 34, 6, stroke=0, fill=1); canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(63, h - 160, pdf_safe(f"Risk: {m['risk_class']}"))
    canvas.setFillColor(colors.HexColor("#CFE8D5")); canvas.setFont("Helvetica", 10.5)
    lines, cur = [], ""
    for wd in f"Area: {pdf_safe(m['location'])}".split():
        if canvas.stringWidth((cur + " " + wd).strip(), "Helvetica", 10.5) > w - 102: lines.append(cur); cur = wd
        else: cur = (cur + " " + wd).strip()
    lines.append(cur)
    if len(lines) > 2: lines = [lines[0], lines[1].rstrip(".,; ")[:-3] + "..."]
    for k, ln in enumerate(lines): canvas.drawString(51, h - 200 - 14 * k, ln)
    canvas.drawString(51, h - 200 - 14 * len(lines) - 2, pdf_safe(f"Conditions as of {m['as_of'] or 'n/a'}   |   Prepared on {m['generated_on']}"))
    canvas.restoreState()


def _later(canvas, doc, c):
    w, h = A4; canvas.saveState(); canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5); canvas.line(51, h - 40, w - 51, h - 40); canvas.line(51, 42, w - 51, 42)
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(MUTED); canvas.drawString(51, h - 34, pdf_safe(f"Vayu  |  Risk assessment  |  {c['meta']['location']}"))
    canvas.drawString(51, 31, "Indicative. Not validated against yield or loss data; modelled layers are estimates, not measurements."); canvas.drawRightString(w - 51, 31, f"Page {doc.page}"); canvas.restoreState()


def render_pdf(c: Dict[str, Any]) -> bytes:
    st, W = _styles(), A4[0] - 102
    buf = io.BytesIO(); m = c["meta"]
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=51, rightMargin=51, topMargin=56, bottomMargin=56, title=pdf_safe(f"Agricultural risk assessment: {m['location']}"), author="Vayu",
                            subject=pdf_safe(f"Detailed risk assessment as of {m['as_of']}"), creator="Vayu risk assessment")
    S: List[Any] = [Spacer(1, 252), _Outline("Cover and summary", "cover")]
    S += [rd.class_scale(RISK_ORDER, m["risk_index"], w=W), Spacer(1, 6), Paragraph(f"<b>{_p(c['headline'])}</b>", st["b"]), Spacer(1, 8)]
    chips = [["Stress (now)", f"{m['stress_class']}" + (f", {_num(m['stress_score'])}/100" if m["stress_score"] is not None else "")],
             ["Land vulnerability", f"{m['vulnerability_class']}" + (f", {_num(m['vulnerability_score'])}/100" if m["vulnerability_score"] is not None else "")],
             ["Risk class", m["risk_class"]], ["Confidence", f"{m['confidence_label'] or 'n/a'}" + (f" ({_num(m['confidence_score'], 2)})" if m["confidence_score"] is not None else "")]]
    t = Table([[Paragraph(f'<font size="7.5" color="#5C6B63">{_p(a.upper())}</font><br/><b>{_p(b)}</b>', st["cell"]) for a, b in chips]], colWidths=[W / 4] * 4)
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, -1), PALE), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    S += [t, Spacer(1, 10), Paragraph("What is driving it", st["h2"])]
    sm = c["summary"]
    if sm["drivers"]: S += [Paragraph(_p(d), st["li"], bulletText="\u2022") for d in sm["drivers"]]
    else: S.append(Paragraph("No indicator is at a moderate stress level or worse.", st["b"]))
    if sm["no_signal"]: S.append(Paragraph(f"<b>No drought signal:</b> {_p('; '.join(sm['no_signal']))}.", st["small"]))
    if sm["unavailable"]: S.append(Paragraph(f"<b>Not available (not scored, never treated as zero):</b> {_p('; '.join(sm['unavailable']))}.", st["small"]))
    S += [Paragraph("How the class is reached", st["h2"]), Paragraph(_p(c["risk_rule"] or "n/a"), st["b"]), Spacer(1, 4), Paragraph(_p(c["confidence_text"]), st["small"]), Spacer(1, 8),
          Paragraph("In this report: stress indicators with every value and source; land vulnerability; soil condition (descriptive, not scored); climate of the area; how sensitive the score is to the weighting; "
                    "the original alert score; data gaps; methodology; limitations; sources.", st["small"]), PageBreak()]
    # ---- stress
    S += [_Outline("Stress indicators", "stress"), Paragraph("Stress: what the satellites and models show now", st["h1"]),
          Paragraph("Each indicator is compared with its own multi-year history and placed in a published class. Classes become 0-100 stress points by convention (20 per US Drought Monitor category, 25 per VHI class); "
                    "the points are not a measurement. Points are averaged within each family, then families are averaged with equal weights over those available.", st["small"]), Spacer(1, 4),
          Paragraph("Stress points by family", st["h2"]), _fit(rd.family_bars(c["families"]), W), Paragraph("Where each indicator sits against its published bands", st["h2"]), _fit(rd.position_chart(c["indicator_chart"]), W),
          Paragraph("All indicators", st["h2"])]
    rows = [_hdr(("Indicator", "Value", "Baseline", "Percentile", "Class", "Stress pts"), st)]
    for i in c["indicators"]: rows.append([_cell(i["name"], st, True), _cell(i["value"], st), _cell(i["baseline"], st), _cell(i["percentile"], st), _cell(i["class"], st), _cell(i["stress"], st)])
    S.append(_tbl(rows, [W * 0.22, W * 0.13, W * 0.25, W * 0.14, W * 0.16, W * 0.10], st))
    S += [Spacer(1, 6), Paragraph("Provenance of each indicator", st["h2"])]
    rows = [_hdr(("Indicator", "Reliability", "Source", "Resolution", "Window"), st)]
    for i in c["indicators"]: rows.append([_cell(i["name"], st, True), _cell(i["reliability"], st), Paragraph(_p(i["source"] or "n/a") + _tag(i["cites"]), st["cell"]), _cell(i["resolution"], st), _cell(i["window"], st)])
    S.append(_tbl(rows, [W * 0.22, W * 0.11, W * 0.29, W * 0.14, W * 0.24], st))
    notes = [i for i in c["indicators"] if i["note"]]
    if notes:
        S += [Spacer(1, 6), Paragraph("Notes on the indicators", st["h2"])] + [Paragraph(f"<b>{_p(i['name'])}:</b> {_p(i['note'])}", st["li"], bulletText="\u2022") for i in notes]
    # ---- vulnerability
    v = c["vulnerability"]
    S += [Spacer(1, 12), _Outline("Land vulnerability", "vuln"), Paragraph("Land vulnerability", st["h1"]), Paragraph(_p(v["explanation"]), st["small"]), Spacer(1, 4)]
    if v["factors"]:
        S.append(Paragraph(f"Vulnerability: {_p(str(v['class']))}" + (f", {_num(v['score'])} of 100" if v["score"] is not None else ""), st["h2"]))
        rows = [_hdr(("Factor", "Value", "Class", "Points", "Why it matters"), st)]
        for f in v["factors"]: rows.append([_cell(f["name"], st, True), _cell(f["value"], st), _cell(f["class"], st), _cell(_num(f["points"], 0), st), Paragraph(_p(f["note"]) + _tag([k.strip() for k in f["source"].split(",") if k.strip()]), st["cell"])])
        S.append(_tbl(rows, [W * 0.23, W * 0.19, W * 0.12, W * 0.08, W * 0.38], st))
    # ---- soil
    S += [Spacer(1, 8), _Outline("Soil condition", "soil"), Paragraph("Soil condition (descriptive)", st["h1"])]
    soil = c["soil"]
    if soil and soil.get("properties"):
        S.append(Paragraph("pH, salinity and nutrients are fertility constraints, not drought vulnerability, so pH is described here and never enters a score. The values are 250 m model estimates.", st["small"]))
        rows = [_hdr(("Property", "Value", "Class", "In a score?", "Reading"), st)]
        for r in soil["properties"]: rows.append([_cell(r["name"], st, True), _cell(r["value"] if isinstance(r["value"], str) else (f"{r['value']:.1f}" if r.get("id") == "ph" else _num(r["value"])), st), _cell(r["class"], st), _cell("Yes, vulnerability" if r.get("scored") else "No", st), Paragraph(_p(r["interpretation"]) + _tag([k.strip() for k in (r.get("source") or "").split(",") if k.strip()]), st["cell"])])
        S += [Spacer(1, 4), _tbl(rows, [W * 0.18, W * 0.12, W * 0.16, W * 0.13, W * 0.41], st), Spacer(1, 6),
              Paragraph("<b>Cannot be sensed from space:</b> " + _p("; ".join(soil["not_measurable"])) + ".", st["b"]), Spacer(1, 3), Paragraph(f"<b>What to do:</b> {_p(soil['advice'])}", st["lead"])]
    else:
        S.append(Paragraph("The land profile was not available for this area, so no soil properties are shown. Get a soil test (Soil Health Card scheme) from a KVK or the state soil-testing laboratory.", st["b"]))
    # ---- climate
    cl = c["climate"]
    if cl:
        S += [PageBreak(), _Outline("Climate of the area", "climate"), Paragraph("Climate of the area", st["h1"]), Paragraph("Modelled climate normals (CHIRPS rainfall, ERA5-Land temperature) for context. They are not part of any score.", st["small"]), Spacer(1, 4)]
        ar = cl.get("aridity") or {}
        facts = [("Annual rainfall", f"{_num(cl.get('annual_rain_mm'), 0)} mm"), ("Annual mean temperature", f"{_num(cl.get('annual_mean_temp_c'))} C"), ("Coldest month, mean", f"{_num(cl.get('coldest_month_c'))} C"),
                 ("Hottest month, mean", f"{_num(cl.get('hottest_month_c'))} C"), ("Aridity index (P/PET, UNEP)", f"{_num(ar.get('value'), 2)} ({ar.get('class', 'n/a')}); PET about {_num(ar.get('annual_pet_mm'), 0)} mm" if ar else "n/a"),
                 ("Climate record (years)", _num(cl.get("years")))]
        rows = [[_cell(a, st, True), _cell(b, st)] for a, b in facts]
        S.append(_tbl(rows, [W * 0.4, W * 0.6], st, header=False))
        if cl.get("note"): S += [Spacer(1, 4), Paragraph(f"<b>Note:</b> {_p(cl['note'])}", st["lead"])]
        if cl.get("monthly_temp_c") and cl.get("monthly_rain_mm"):
            S += [Paragraph("Month by month", st["h2"]), _fit(dg.climate_chart(cl["monthly_temp_c"], cl["monthly_rain_mm"], None, None, None, None, crop_legend=False), W)]
        if c["ndvi"]: S += [Paragraph("How green the land is, month by month (satellite NDVI)", st["h2"]), _fit(dg.greenness_strip(c["ndvi"]), W), Paragraph("Brown is bare ground, dark green is dense vegetation. NDVI shows what is growing, not whether the land is stressed.", st["small"])]
    # ---- sensitivity
    sn = c["sensitivity"]
    S += [Spacer(1, 8), _Outline("Sensitivity", "sens"), Paragraph("How sensitive is the score to the weighting?", st["h1"]), Paragraph(_p(sn["text"]), st["b"]), Spacer(1, 4), _fit(rd.sensitivity_chart(sn["raw"]), W)]
    if sn["variants"]:
        rows = [_hdr(("Variant", "Stress score"), st)] + [[_cell(x["name"], st), _cell(_num(x["score"]) if x["score"] is not None else "not computed", st)] for x in sn["variants"]]
        S += [Spacer(1, 4), _tbl(rows, [W * 0.6, W * 0.4], st)]
    # ---- legacy
    lg = c["legacy"]
    S += [Spacer(1, 8), _Outline("Original alert score", "legacy"), Paragraph("The original alert score", st["h2"])]
    if lg and lg.get("risk_score") is not None:
        S.append(Paragraph(f"The original 3-indicator score (drought extent, vegetation loss, soil moisture) for the same area is {_num(lg['risk_score'], 0)} of 100 ({_p(str(lg.get('band')))}), with confidence {_num(lg.get('confidence'), 2)}. "
                           "It is kept unchanged for alerts and the watchlist. It uses fewer inputs and no land context, so it can differ from the assessment above.", st["b"]))
    else:
        S.append(Paragraph("The original alert score was not available for this run.", st["b"]))
    # ---- gaps, method, limits, sources
    if c["errors"]:
        S += [Paragraph("Data gaps in this run", st["h2"])] + [Paragraph(f"<b>{_p(e['source'])}:</b> {_p(e['message'])}", st["li"], bulletText="\u2022") for e in c["errors"]]
    S += [PageBreak(), _Outline("Methodology", "method"), Paragraph("Methodology", st["h1"])] + [Paragraph(f"{k}. {_p(t)}", st["li"]) for k, t in enumerate(c["methodology"], 1)]
    S += [Spacer(1, 6), _Outline("Limitations", "limits"), Paragraph("Limitations", st["h1"])] + [Paragraph(_p(t), st["li"], bulletText="\u2022") for t in c["limitations"]]
    S += [Spacer(1, 6), _Outline("Sources", "sources"), Paragraph("Sources", st["h1"])] + [Paragraph(f"<b>[{_p(x['key'])}]</b> {_p(x['text'])}", st["small"]) for x in c["citations"]]
    S += [Spacer(1, 8), Paragraph(_p(c["disclaimer"]), st["small"])]
    doc.build(S, onFirstPage=lambda cv, d: _cover(cv, d, c), onLaterPages=lambda cv, d: _later(cv, d, c))
    return buf.getvalue()


def make_risk_pdf(assessment: Dict[str, Any], *, location_label: str = "Location not given", generated_on: Optional[str] = None) -> bytes:
    return render_pdf(build_content(assessment, location_label=location_label, generated_on=generated_on))
