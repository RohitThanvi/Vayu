"""business_brief.py — a one-document PDF "daily brief" of the Business tab: markets, commodities, chokepoint risk, notable events,
India food-and-fuel indicators and the US yield curve, each with its source, age and staleness stated. Built from the same
cached data the screen shows (no extra upstream calls), so it is quick and reproducible.
"""
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from .report_generator import _styles, _header_flowables, _footer_canvas, LINE, TABLE_HEAD_BG


def esc(s: Any) -> str:
    return escape(str(s)) if s is not None else ""


def fmt(v: Any, nd: int = 2, suffix: str = "") -> str:
    if v is None or v == "":
        return "n/a"
    try:
        return f"{float(v):,.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return esc(v)


def _grid(styles, header: List[str], rows: List[List[Any]], widths_mm: List[float]) -> Table:
    data = [[Paragraph(f"<b>{esc(h)}</b>", styles["meta_label"]) for h in header]]
    data += [[Paragraph(esc(c), styles["body"]) for c in r] for r in rows]
    t = Table(data, colWidths=[w * mm for w in widths_mm], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TABLE_HEAD_BG), ("LINEBELOW", (0, 0), (-1, 0), 0.75, LINE),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3)]))
    return t


def _quality_line(styles, dq: Optional[Dict[str, Any]]) -> Paragraph:
    if not dq:
        return Paragraph("Source and age not available.", styles["caveat"])
    age = dq.get("age_seconds")
    age_txt = "never fetched" if age is None else (f"{age // 60} min old" if age < 3600 else f"{age // 3600} h old")
    stale = " — STALE: the last refresh did not complete on schedule" if dq.get("stale") else ""
    return Paragraph(esc(f"Source: {dq.get('source', 'n/a')} · data {age_txt}{stale}. {dq.get('delay_note', '')}"), styles["caveat"])


def _section(styles, flow: List, n: int, title: str):
    flow.append(Paragraph(f"{n}. {esc(title)}", styles["section_head"]))


def _quote_rows(quotes: List[Dict[str, Any]]) -> List[List[Any]]:
    return [[q.get("name") or q.get("symbol"), fmt(q.get("value"), 2), fmt(q.get("change_pct"), 2, "%"), "stale" if q.get("stale") else ""] for q in quotes]


def build_business_brief(snap: Dict[str, Any]) -> bytes:
    """snap: {markets, commodities, risk(list), events(list), india(dict|None), yield_curve(dict|None)}"""
    styles = _styles()
    styles["section_head"].keepWithNext = 1   # never strand a heading at the bottom of a page
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm)
    flow: List = _header_flowables(styles, "Business Intelligence Brief", f"Generated {generated_at}")
    n = 0

    markets = snap.get("markets") or {}
    n += 1; _section(styles, flow, n, "Markets")
    if markets.get("indices"):
        flow.append(_grid(styles, ["Index", "Last", "Day", ""], _quote_rows(markets["indices"]), [75, 35, 30, 25]))
    if markets.get("fx"):
        flow += [Spacer(1, 6), _grid(styles, ["Currency / dollar index", "Last", "Day", ""], _quote_rows(markets["fx"]), [75, 35, 30, 25])]
    secs = sorted(markets.get("sectors", []), key=lambda q: (q.get("change_pct") is None, -(q.get("change_pct") or 0)))
    if secs:
        flow += [Spacer(1, 6), _grid(styles, ["India sector (best to worst today)", "Last", "Day", ""], _quote_rows(secs), [75, 35, 30, 25])]
    flow.append(_quality_line(styles, markets.get("data_quality")))

    comm = snap.get("commodities") or {}
    n += 1; _section(styles, flow, n, "Commodities")
    flow.append(_grid(styles, ["Commodity", "Last", "Day", ""], _quote_rows(comm.get("commodities", [])), [75, 35, 30, 25]) if comm.get("commodities")
                 else Paragraph("Commodity data not available in this brief.", styles["body"]))
    flow.append(_quality_line(styles, comm.get("data_quality")))

    n += 1; _section(styles, flow, n, "Chokepoint risk")
    risk = snap.get("risk") or []
    flow.append(_grid(styles, ["Chokepoint", "Score (0-100)", "Band"], [[r.get("chokepoint_display"), fmt(r.get("score"), 1), r.get("band")] for r in risk], [85, 40, 40])
                if risk else Paragraph("Chokepoint risk not available in this brief.", styles["body"]))
    flow.append(Paragraph("Vayu's business-risk score blends vessel-traffic anomaly, regional news tone, nearby seismic activity and sanctions screening; "
                          "see the method in the app. It is a monitoring aid, not a prediction.", styles["caveat"]))

    n += 1; _section(styles, flow, n, "Notable events (warning / critical, newest first)")
    notable = [e for e in (snap.get("events") or []) if e.get("severity") in ("warn", "critical")][:15]
    flow.append(_grid(styles, ["Time (UTC)", "Source", "Level", "Event"], [[(e.get("ts") or "")[:16].replace("T", " "), e.get("source"), e.get("severity"), (e.get("title") or "")[:90]] for e in notable], [32, 28, 18, 87])
                if notable else Paragraph("No warning or critical events in the current feed.", styles["body"]))

    india = snap.get("india")
    n += 1; _section(styles, flow, n, "India food and fuel pressure")
    if india and india.get("indicators"):
        flow.append(_grid(styles, ["Indicator", "Latest", "1m", "Where it sits (12 months)"],
                          [[i["label"], f"{fmt(i['latest'], 2)} {i['unit']}", fmt(i.get("change_1m_pct"), 1, "%"), i.get("position") or "n/a"] for i in india["indicators"]], [48, 42, 20, 55]))
        flow.append(Paragraph(esc(india.get("method", "")), styles["caveat"]))
        flow.append(_quality_line(styles, india.get("data_quality")))
    else:
        flow.append(Paragraph("India indicators not available in this brief.", styles["body"]))

    yc = snap.get("yield_curve")
    n += 1; _section(styles, flow, n, "US Treasury yield curve")
    if yc and yc.get("curves"):
        sp = yc.get("spreads", {})
        def sp_txt(k): 
            s = sp.get(k) or {}
            return "n/a" if s.get("value") is None else f"{fmt(s['value'], 2)} pp" + (" (inverted)" if s.get("inverted") else "")
        flow.append(_grid(styles, ["Spread", "Value"], [["10-year minus 2-year", sp_txt("10y_minus_2y")], ["10-year minus 3-month", sp_txt("10y_minus_3m")]], [85, 60]))
        flow.append(Spacer(1, 6))
        c = yc["curves"]
        flow.append(_grid(styles, ["Tenor", "Latest %", "1 month ago %", "1 year ago %"],
                          [[t, fmt(c["latest"].get(t)), fmt(c["one_month_ago"].get(t)), fmt(c["one_year_ago"].get(t))] for t in yc.get("tenors", [])], [30, 35, 40, 40]))
        flow.append(Paragraph(esc(f"As of {yc.get('as_of')}. Inverted means the longer yield is below the shorter one; no recession forecast is implied."), styles["caveat"]))
        flow.append(_quality_line(styles, yc.get("data_quality")))
    else:
        flow.append(Paragraph("Yield curve not available in this brief.", styles["body"]))

    n += 1; _section(styles, flow, n, "Data quality and limits")
    for line in ("Market prices come from Yahoo Finance's unofficial chart API: typically 15–20 minutes delayed, with no service-level guarantee. They are reference prices and not suitable for trading decisions.",
                 "Rows marked 'stale' show the last good value because the latest refresh for that symbol failed.",
                 "Vayu does not provide real-time exchange data, licensed fixed-income pricing or trade execution. This brief is informational and is not investment advice."):
        flow.append(Paragraph(esc(line), styles["body"]))

    doc.build(flow, onFirstPage=lambda c, d: _footer_canvas(c, d, generated_at), onLaterPages=lambda c, d: _footer_canvas(c, d, generated_at))
    return buf.getvalue()
