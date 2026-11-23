"""
risk_pdf.py - the detailed agricultural risk assessment (risk_assessment.compute_risk_assessment) as a PDF report with every metric.

Two layers, like pathway_pdf.py, so the content is testable without parsing a PDF:
  build_content(assessment, location_label=..., generated_on=...) -> plain dict: every number, class, source, reason and caveat shown in the report
  render_pdf(content) -> bytes (A4, reportlab, vector charts from risk_diagrams / pathway_diagrams)
Report order: cover and summary, stress indicators (chart, values table, provenance table, notes), land vulnerability, soil condition (descriptive, not scored), climate, sensitivity,
the original three indicators (drought extent, vegetation loss, soil moisture) in full, maps of the indicators, what to do next, data gaps, methodology, limitations, sources.
Honesty rules carried into the text: nothing is validated against yield or loss data; class-to-points scaling is a convention; modelled layers are labelled modelled; an unavailable
indicator is listed with its reason and never shown as zero. Helvetica / WinAnsi only (text goes through pdf_safe). English only.
"""
import io
from datetime import date
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import pathway_diagrams as dg
from . import risk_diagrams as rd
from ..report_generator import GROUNDWATER_LEGEND, MOISTURE_LEGEND, NDDI_LEGEND, NDVI_LEGEND, PRECIP_LEGEND, THERMAL_LEGEND, _gradient_legend, _moisture_legend_for
from .pathway_pdf import DEEP, GREEN, LINE, MUTED, PALE, _Outline, _fit, _p, _styles, pdf_safe
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


# Maps shown in the report: (key, title, caption, legend). Legends are the SAME constants the Earth Engine thumbnails are drawn with (report_generator), so a legend cannot drift from its map.
MAP_SPECS = [
    ("optical", "True-colour view (Sentinel-2)", "Cloud-masked Sentinel-2 median around the as-of date.", None),
    ("ndvi", "Vegetation greenness (NDVI)", "The surface the vegetation-loss indicator is computed from. Red and orange: sparse or stressed vegetation; green: healthy.", NDVI_LEGEND),
    ("nddi", "Drought map (NDDI)", "The surface the drought-extent indicator is computed from. Blank pixels are bare soil, water or cloud, where the index is not defined.", NDDI_LEGEND),
    ("moisture", "Soil moisture (NASA SMAP)", "About 9 km resolution: a small area may fall inside one or two pixels. The colour range is fitted to this area, so compare shades within the map, not between maps.", MOISTURE_LEGEND),
    ("precipitation", "Accumulated rainfall, last 90 days (CHIRPS)", "About 5.5 km resolution.", PRECIP_LEGEND),
    ("groundwater", "Groundwater storage anomaly (GRACE)", "A regional signal of about 300 km: it does not resolve individual fields or wells. The latest available month is shown.", GROUNDWATER_LEGEND),
    ("temperature", "Land surface temperature (Landsat thermal)", "Thermal imagery over the last 90 days; it is a surface temperature, not air temperature.", THERMAL_LEGEND),
]


def _maps_content(images: Optional[Dict[str, Any]]) -> list:
    """Which maps this report has. The image bytes stay out of the content dict (it must stay plain, comparable data); only availability and the moisture colour range are recorded."""
    images = images or {}
    return [{"key": k, "title": t, "caption": cap, "available": bool((images.get(k) or {}).get("bytes")), "range": (images.get(k) or {}).get("range")} for k, t, cap, _ in MAP_SPECS]


def _recommendations(cls: str, conf_label: Optional[str], unavailable: list, original_failed: list) -> List[str]:
    """Plain next steps by risk class. Deterministic text, no chemical or input advice: only monitoring, verification and data-quality steps."""
    recs: List[str] = []
    if cls == "Cannot assess":
        recs.append("No stress indicator was available for this area and date, so no risk class is given. Try again in a few days, or draw a larger area if the selected area is very small.")
    elif cls in ("High", "Very high"):
        recs += ["Given the elevated risk class, make a field visit to this area before any operational, insurance or subsidy decision that rests on this assessment alone.",
                 "Use the maps to see which part of the area is driving the result: risk is rarely uniform across a large or irregular area."]
    elif cls == "Moderate":
        recs.append("Monitor this area every 2 to 4 weeks to see whether the moderate reading is stable, improving, or moving toward the high class.")
    else:
        recs.append("No immediate action is indicated by this assessment; continue routine monitoring at your normal cadence.")
    if conf_label == "low": recs.append("Confidence is low (few indicator families contributed, or those that did are low reliability), so treat the class as indicative and confirm on the ground.")
    if unavailable: recs.append(f"{len(unavailable)} indicator(s) had no data this run (listed under the summary and the indicator table). Consider re-running in a few weeks once satellite coverage improves, rather than treating a partial assessment as final.")
    if original_failed: recs.append("Part of the original three-indicator score had no usable reading: " + ", ".join(k.replace("_", " ") for k in original_failed) + ".")
    recs.append("If this area will be monitored repeatedly, add it to the Agri watchlist so future alerts can build a farmer and officer feedback history; that history improves confidence over time.")
    return recs


def _km2(v: Any) -> str: return "n/a" if v is None else f"{v:,.2f} km2"


def _pc(v: Any, nd: int = 1) -> str: return "n/a" if v is None else f"{_num(v, nd)}%"


def _original_content(lg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The original three indicators (drought extent, vegetation loss, soil moisture) with every raw metric, their sub-scores and weights, as one section of this single report."""
    if not lg: return None
    rm = lg.get("raw_metrics") or {}; d, v, m = rm.get("drought") or {}, rm.get("vegetation") or {}, rm.get("soil_moisture") or {}
    ss, w, failed, minp = lg.get("sub_scores") or {}, lg.get("weights") or {}, lg.get("inputs_failed") or [], lg.get("min_assessed_pct")

    def why_missing(key: str, src: str) -> Optional[str]:
        if ss.get(key) is not None: return None
        if key == "drought" and d.get("assessed_area_pct") is not None and minp is not None and d["assessed_area_pct"] < minp:
            return f"Not assessed: only {_pc(d['assessed_area_pct'])} of the area could be scored (the rest is bare soil, water or cloud), so a drought share would not be representative."
        return "Not assessed: no usable reading for this area and period." if (src in rm or key in failed) else "Not assessed."
    comps = [{"name": nm, "weight": _pc(100 * w[k], 0) if k in w else "n/a", "sub_score": _num(ss.get(k)) if ss.get(k) is not None else "not assessed", "reason": why_missing(k, src)}
             for k, nm, src in (("drought", "Drought extent (NDDI)", "drought"), ("vegetation_loss", "Vegetation loss (Sentinel-2 NDVI)", "vegetation"), ("moisture_deficit", "Soil-moisture deficit (SMAP)", "soil_moisture"))]
    drought = [("Drought-affected area (NDDI above 0.5)", f"{_km2(d.get('drought_affected_km2'))} ({_pc(d.get('drought_affected_pct'))} of the area)", "end of the period"),
               ("Share of the assessable area under drought", _pc(d.get("drought_pct_of_assessed")), "this is what the sub-score uses"),
               ("Severe drought area (NDDI above 0.7)", f"{_km2(d.get('severe_drought_km2'))} ({_pc(d.get('severe_drought_pct'))} of the area; {_pc(d.get('severe_pct_of_assessed'))} of the assessable area)", ""),
               ("Area where NDDI could be computed", _pc(d.get("assessed_area_pct")), "the rest is bare soil, water or cloud and is not scored"),
               ("Mean NDDI at start, end, change", f"{_num(d.get('avg_nddi_start'), 3)}, {_num(d.get('avg_nddi_end'), 3)}, {_num(d.get('nddi_change'), 3)}", "higher = more drought stress"),
               ("Class limits", "0.5 (drought), 0.7 (severe)", "conventions of this platform, not published limits")] if d else []
    veg = [("Vegetation lost", f"{_km2(v.get('vegetation_loss_km2'))} ({_pc(v.get('loss_pct'))} of the initial vegetated area)", "NDVI fell below 0.2 between the two years"),
           ("Vegetation gained", _km2(v.get("vegetation_gain_km2")), ""), ("Net change", _km2(v.get("net_change_km2")), ""),
           ("Initial vegetated area", f"{_km2(v.get('initial_vegetation_km2'))} ({_pc(v.get('vegetation_pct_of_aoi'))} of the area)", "")] if v else []
    if v.get("no_baseline_vegetation"): veg.append(("Caution", "No vegetation was detected at the start of the period", "so there is nothing to lose and no loss percentage is given (this is not a verified 0%)"))
    elif v.get("small_base_caveat"): veg.append(("Caution", "Vegetation covers under 2% of the area", "the loss percentage rests on very few pixels and can swing a lot"))
    moist = [("Average soil moisture, start and end", f"{_num(m.get('start_avg_soil_moisture'), 3)} and {_num(m.get('end_avg_soil_moisture'), 3)} m3/m3", "SMAP surface layer, 0-5 cm"),
             ("Change in soil moisture", f"{_num(m.get('moisture_change'), 3)} m3/m3", "negative = drying"),
             ("Lowest and highest end value", f"{_num(m.get('end_min_sm'), 3)} and {_num(m.get('end_max_sm'), 3)} m3/m3", ""),
             ("Dry-stress share (moisture below 0.1 m3/m3)", f"{_pc(m.get('dry_stress_pct'))} ({_km2(m.get('dry_stress_area_km2'))})", "end of the period"),
             ("Reading available", "yes" if m.get("data_available") else "no", "")] if m else []
    if m.get("window_overlap_caveat"): moist.append(("Caution", "The start and end windows overlap", "the change shares some of the same imagery, so it understates real change"))
    if m.get("sampling_note"): moist.append(("Coverage", m["sampling_note"], ""))
    per = lg.get("period") or {}
    return {"risk_score": lg.get("risk_score"), "band": lg.get("band"), "confidence": lg.get("confidence"), "reason": lg.get("reason"), "components": comps, "drought": drought, "vegetation": veg, "moisture": moist,
            "period": f"{per.get('start_date')} to {per.get('end_date')}" if per else None,
            "method": ["Drought: NDDI = (NDVI - NDWI) / (NDVI + NDWI) from cloud-masked Sentinel-2 medians (one year each side), NDWI = (NIR - SWIR) / (NIR + SWIR) (Gu et al. 2007; Gao 1996).",
                       "Vegetation: two adjacent one-year Sentinel-2 NDVI medians; vegetated means NDVI of 0.2 or more (a convention).",
                       "Soil moisture: NASA SMAP L4 surface moisture (about 9 km); the score combines the fall in moisture with the dry-stress share.",
                       "The composite is the weighted mean of the sub-scores that could be computed; the weights are renormalised over the available ones. Alerts and the watchlist use this composite."],
            "note": lg.get("note")}


def _sensitivity_text(s: Dict[str, Any]) -> str:
    if not s or s.get("range") is None: return "The stress score could not be computed, so there is nothing to test."
    t = (f"Across the equal-weight headline, four alternative weightings and each leave-one-family-out variant, the stress score runs from {_num(s['min'])} to {_num(s['max'])} points "
         f"(spread {_num(s['range'])}). ")
    t += (f"A spread of {SENSITIVITY_ROBUST_RANGE:g} points or less is called robust: this one is robust, so the result does not hinge on how the families are weighted."
          if s.get("robust") else f"This is more than {SENSITIVITY_ROBUST_RANGE:g} points, so the result depends noticeably on the weighting. Read the class, not the exact number.")
    if s.get("most_influential"): t += f" The variant that moves it most is \"{s['most_influential']}\"."
    return t


def build_content(a: Dict[str, Any], *, location_label: str = "Location not given", generated_on: Optional[str] = None, images: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    sm_unavail = (a.get("summary") or {}).get("unavailable") or []
    variants = [{"name": k, "score": v} for k, v in (sens.get("variants") or {}).items()]
    soil, clim, legacy = a.get("soil_condition"), a.get("climate"), a.get("legacy")
    vf = [{"name": f["name"], "value": f["value"], "class": f["class"], "points": f["points"], "source": f.get("source") or "", "note": f.get("note") or ""} for f in vuln.get("factors") or []]
    cites_all = a.get("citations") or {}
    used: List[str] = ["ipcc"]
    for i in inds: used += i["cites"]
    for f in vf: used += [k.strip() for k in f["source"].split(",") if k.strip()]
    for r in (soil or {}).get("properties") or []: used += [k.strip() for k in (r.get("source") or "").split(",") if k.strip()]
    if clim and (clim.get("aridity") or {}): used.append("unep")
    if legacy: used += ["nddi", "gao", "smap"]
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
            "original": _original_content(legacy), "maps": _maps_content(images), "recommendations": _recommendations(cls, conf.get("label"), sm_unavail, (legacy or {}).get("inputs_failed") or []), "errors": [{"source": ERROR_LABELS.get(k, k), "message": str(v)[:160]} for k, v in (a.get("errors") or {}).items()],
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


def _wrap_text(text: str, measure, width: float) -> List[str]:
    """Greedy word wrap that also hard-breaks a single token wider than the line (a long name with no spaces must not run off the page)."""
    lines: List[str] = []; cur = ""
    for wd in text.split():
        while wd and measure(wd) > width:
            k = len(wd)
            while k > 1 and measure(wd[:k]) > width: k -= 1
            if cur: lines.append(cur); cur = ""
            lines.append(wd[:k]); wd = wd[k:]
        if not wd: continue
        if cur and measure(cur + " " + wd) > width: lines.append(cur); cur = wd
        else: cur = (cur + " " + wd).strip()
    if cur: lines.append(cur)
    return lines


def _cover(canvas, doc, c):
    w, h = A4; m = c["meta"]
    canvas.saveState(); canvas.setFillColor(DEEP); canvas.rect(0, h - 262, w, 262, stroke=0, fill=1); canvas.setFillColor(GREEN); canvas.rect(0, h - 268, w, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#9ED6AE")); canvas.setFont("Helvetica-Bold", 11); canvas.drawString(51, h - 60, "VAYU  |  DETAILED RISK ASSESSMENT")
    canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 28); canvas.drawString(51, h - 112, "Agricultural risk assessment")
    idx = m["risk_index"]; pill = rd.CLASS_COLORS[idx] if idx is not None else colors.HexColor("#5C6B63")
    canvas.setFillColor(pill); canvas.roundRect(51, h - 170, 210, 34, 6, stroke=0, fill=1); canvas.setFillColor(colors.white); canvas.setFont("Helvetica-Bold", 15)
    canvas.drawString(63, h - 160, pdf_safe(f"Risk: {m['risk_class']}"))
    canvas.setFillColor(colors.HexColor("#CFE8D5")); canvas.setFont("Helvetica", 10.5)
    lines = _wrap_text(f"Area: {pdf_safe(m['location'])}", lambda t: canvas.stringWidth(t, "Helvetica", 10.5), w - 102)
    if len(lines) > 2: lines = [lines[0], lines[1].rstrip(".,; ")[:-3] + "..."]
    for k, ln in enumerate(lines): canvas.drawString(51, h - 200 - 14 * k, ln)
    canvas.drawString(51, h - 200 - 14 * len(lines) - 2, pdf_safe(f"Conditions as of {m['as_of'] or 'n/a'}   |   Prepared on {m['generated_on']}"))
    canvas.restoreState()


def _later(canvas, doc, c):
    w, h = A4; canvas.saveState(); canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5); canvas.line(51, h - 40, w - 51, h - 40); canvas.line(51, 42, w - 51, 42)
    canvas.setFont("Helvetica", 7.5); canvas.setFillColor(MUTED); canvas.drawString(51, h - 34, pdf_safe(f"Vayu  |  Risk assessment  |  {c['meta']['location']}"))
    canvas.drawString(51, 31, "Indicative. Not validated against yield or loss data; modelled layers are estimates, not measurements."); canvas.drawRightString(w - 51, 31, f"Page {doc.page}"); canvas.restoreState()


def _map_section(c: Dict[str, Any], images: Dict[str, Any], st, W, requested: bool = True) -> list:
    """The maps the report could draw, two per row with their legends, then one line naming any map that was not available (never silently dropped)."""
    specs = {k: leg for k, _, _, leg in MAP_SPECS}; have = [m for m in c["maps"] if m["available"] and (images.get(m["key"]) or {}).get("bytes")]; missing = [m["title"] for m in c["maps"] if m not in have]
    out: list = [PageBreak(), _Outline("Maps", "maps"), Paragraph("Maps of the indicators", st["h1"]),
                 Paragraph("These are the surfaces the indicators above are computed from, drawn for the area you selected. A map shows where within the area a signal sits; it does not add a score.", st["small"]), Spacer(1, 4)]
    if not requested:
        out.append(Paragraph("Maps were not requested for this report.", st["b"])); return out
    cells = []
    for m in have:
        raw = images[m["key"]]["bytes"]
        try: iw, ih = ImageReader(io.BytesIO(raw)).getSize()
        except Exception: missing.append(m["title"]); continue
        w = min(W / 2 - 14, 200); leg = specs[m["key"]]       # 200 pt cap so two rows of maps fit on a page
        if m["key"] == "moisture": leg = {**(_moisture_legend_for(m["range"]) if m.get("range") else MOISTURE_LEGEND), "unit": "Soil moisture, colour range fitted to this area"}
        cell: list = [Paragraph(f"<b>{_p(m['title'])}</b>", st["cell"]), Spacer(1, 2), Image(io.BytesIO(raw), width=w, height=w * ih / max(iw, 1)), Spacer(1, 4)]
        if leg: cell.append(_gradient_legend(leg["palette"], leg["min"], leg["max"], leg["unit"], leg.get("labels"), width=int(w), height=44))
        cell.append(Paragraph(_p(m["caption"]), st["small"])); cells.append(cell)
    if cells:
        if len(cells) % 2: cells.append("")
        t = Table([cells[i:i + 2] for i in range(0, len(cells), 2)], colWidths=[W / 2, W / 2])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)])); out.append(t)
    if missing: out.append(Paragraph("<b>Not available in this run:</b> " + _p("; ".join(missing)) + ". They could not be drawn for this area and date; the indicators above do not depend on them.", st["small"]))
    return out


def render_pdf(c: Dict[str, Any], images: Optional[Dict[str, Any]] = None) -> bytes:
    requested = images is not None            # None = maps were switched off; {} = they were tried and none came back
    images = images or {}
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
                    "the original three indicators in full; data gaps; methodology; limitations; sources.", st["small"]), PageBreak()]
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
    # ---- the original three indicators, in full
    og = c["original"]
    S += [PageBreak(), _Outline("The original three indicators", "original"), Paragraph("The original three indicators, in full", st["h1"])]
    if og:
        S.append(Paragraph(f"The original composite combines drought extent, vegetation loss and soil moisture for {_p(og['period'] or 'the same period')}. It is {_num(og['risk_score'], 0)} of 100 ({_p(str(og['band']))}), confidence {_num(og['confidence'], 0)}%. "
                           "It is the score alerts and the watchlist use; it has no land-vulnerability or long-record context, so it can differ from the assessment above.", st["b"]))
        if og["reason"]: S += [Spacer(1, 2), Paragraph(f"<b>Why:</b> {_p(og['reason'])}", st["lead"])]
        rows = [_hdr(("Indicator", "Weight", "Sub-score (0-100)", "Status"), st)] + [[_cell(x["name"], st, True), _cell(x["weight"], st), _cell(x["sub_score"], st), _cell(x["reason"] or "scored", st)] for x in og["components"]]
        S += [Spacer(1, 6), _tbl(rows, [W * 0.32, W * 0.10, W * 0.18, W * 0.40], st)]
        for title, key in (("Drought extent (NDDI, Sentinel-2)", "drought"), ("Vegetation loss (Sentinel-2 NDVI)", "vegetation"), ("Soil moisture (NASA SMAP)", "moisture")):
            S.append(Paragraph(title, st["h2"]))
            if og[key]:
                rows = [_hdr(("Metric", "Value", "Note"), st)] + [[_cell(a, st, True), _cell(b2, st), _cell(n, st)] for a, b2, n in og[key]]
                S.append(_tbl(rows, [W * 0.34, W * 0.38, W * 0.28], st))
            else: S.append(Paragraph("No reading was available for this indicator in this run.", st["b"]))
        S += [Paragraph("How the original score is built", st["h2"])] + [Paragraph(_p(t), st["li"], bulletText="\u2022") for t in og["method"]]
    else:
        S.append(Paragraph("The original three-indicator score was not available for this run.", st["b"]))
    # ---- maps
    S += _map_section(c, images, st, W, requested)
    # ---- what to do
    S += [Spacer(1, 6), _Outline("What to do", "actions"), Paragraph("What to do with this assessment", st["h1"])] + [Paragraph(_p(t), st["li"], bulletText="\u2022") for t in c["recommendations"]]
    # ---- gaps, method, limits, sources
    if c["errors"]:
        S += [Paragraph("Data gaps in this run", st["h2"])] + [Paragraph(f"<b>{_p(e['source'])}:</b> {_p(e['message'])}", st["li"], bulletText="\u2022") for e in c["errors"]]
    S += [PageBreak(), _Outline("Methodology", "method"), Paragraph("Methodology", st["h1"])] + [Paragraph(f"{k}. {_p(t)}", st["li"]) for k, t in enumerate(c["methodology"], 1)]
    S += [Spacer(1, 6), _Outline("Limitations", "limits"), Paragraph("Limitations", st["h1"])] + [Paragraph(_p(t), st["li"], bulletText="\u2022") for t in c["limitations"]]
    S += [Spacer(1, 6), _Outline("Sources", "sources"), Paragraph("Sources", st["h1"])] + [Paragraph(f"<b>[{_p(x['key'])}]</b> {_p(x['text'])}", st["small"]) for x in c["citations"]]
    S += [Spacer(1, 8), Paragraph(_p(c["disclaimer"]), st["small"])]
    doc.build(S, onFirstPage=lambda cv, d: _cover(cv, d, c), onLaterPages=lambda cv, d: _later(cv, d, c))
    return buf.getvalue()


def make_risk_pdf(assessment: Dict[str, Any], *, location_label: str = "Location not given", generated_on: Optional[str] = None, images: Optional[Dict[str, Any]] = None) -> bytes:
    return render_pdf(build_content(assessment, location_label=location_label, generated_on=generated_on, images=images), images)
