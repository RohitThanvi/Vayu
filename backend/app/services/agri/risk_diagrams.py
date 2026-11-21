"""
risk_diagrams.py - vector graphics for the detailed risk assessment PDF, drawn only from the assessment's own numbers (reportlab graphics, no image files).
  class_scale        four-step Low..Very high scale with the current class highlighted
  family_bars        stress points (0-100) of each indicator family, over the four stress classes
  position_chart     where each stress indicator sits against ITS published class bands (US Drought Monitor percentile bands; Kogan VHI classes)
  sensitivity_chart  the stress score under every alternative weighting and leave-one-family-out, against the headline score
Every chart prints the numbers it draws; nothing is shown that is not in the assessment. Helvetica / WinAnsi text only.
"""
from typing import Any, Dict, List, Optional, Sequence

from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect
from reportlab.lib import colors

from .pathway_diagrams import INK, LINE, MUTED, WHITE, _t

CLASS_COLORS = [colors.HexColor("#2E9E5B"), colors.HexColor("#C99A2E"), colors.HexColor("#D9742B"), colors.HexColor("#C8553D")]    # Low, Moderate, High, Very high
BAND_TINTS = [colors.HexColor("#E3F2E8"), colors.HexColor("#FBF0D0"), colors.HexColor("#F8DDC5"), colors.HexColor("#F3CFC6")]
NEUTRAL = colors.HexColor("#E9EEEA")
STRESS_BANDS = [(0, 25), (25, 50), (50, 75), (75, 100)]                                                   # same limits as risk_assessment.STRESS_CLASSES
# Drought bands, drawn in the order they appear on a 0-100 axis. USDM percentile bands are cumulative from the dry end: 0-2, 2-5, 5-10, 10-20, 20-30, then "no signal".
USDM_BANDS = [(0, 2, "D4"), (2, 5, "D3"), (5, 10, "D2"), (10, 20, "D1"), (20, 30, "D0"), (30, 100, "")]
KOGAN_BANDS = [(0, 10, "extreme"), (10, 20, "severe"), (20, 30, "moderate"), (30, 40, "mild"), (40, 100, "")]
_BAND_COLORS = [colors.HexColor("#C8553D"), colors.HexColor("#D9742B"), colors.HexColor("#E3A92D"), colors.HexColor("#F1D27A"), colors.HexColor("#F8E9B0"), colors.HexColor("#E3F2E8")]


def class_scale(labels: Sequence[str], active: Optional[int], w=330, h=44, caption: str = "you are here"):
    """A row of boxes, one per class, the active one filled in its colour (index into labels, or None for 'cannot assess')."""
    d = Drawing(w, h); n = len(labels); bw = (w - 6 * (n - 1)) / n
    for i, lab in enumerate(labels):
        on = active == i; col = CLASS_COLORS[min(i, len(CLASS_COLORS) - 1)]
        d.add(Rect(i * (bw + 6), 8, bw, 26, rx=4, ry=4, fillColor=col if on else NEUTRAL, strokeColor=col if on else LINE, strokeWidth=1.2 if on else 0.6))
        _t(d, i * (bw + 6) + bw / 2, 17, lab, 7.8, WHITE if on else MUTED, "middle", on)
    if active is not None:
        x = active * (bw + 6) + bw / 2; col = CLASS_COLORS[min(active, len(CLASS_COLORS) - 1)]
        d.add(Polygon([x - 4, 38, x + 4, 38, x, 33], fillColor=col, strokeColor=None)); _t(d, x, 40, caption, 6.5, col, "middle", True)
    return d


def _axis(d, x0, w, y, ticks=(0, 25, 50, 75, 100)):
    for t in ticks: _t(d, x0 + t / 100.0 * w, y, f"{t}", 6.3, MUTED, "middle")


def family_bars(families: List[Dict[str, Any]], w=500, row=20):
    """families: [{label, score (0-100 or None)}]. Bars over the four stress-class tints; an unavailable family says so instead of drawing zero."""
    n = max(len(families), 1); h = 34 + n * row; x0, bw = 150, w - 150 - 40; d = Drawing(w, h)
    for i, (lo, hi) in enumerate(STRESS_BANDS): d.add(Rect(x0 + lo / 100 * bw, 16, (hi - lo) / 100 * bw, n * row, fillColor=BAND_TINTS[i], strokeColor=None))
    for i, f in enumerate(families):
        y = 16 + (n - 1 - i) * row; _t(d, x0 - 6, y + 6, f["label"], 7.6, INK, "end")
        sc = f.get("score")
        if sc is None: _t(d, x0 + 6, y + 6, "no data: not scored", 7, MUTED)
        else:
            sc = min(max(float(sc), 0.0), 100.0); band = min(int(sc // 25), 3)
            d.add(Rect(x0, y + 3, max(sc / 100 * bw, 0.8), row - 8, fillColor=CLASS_COLORS[band], strokeColor=None)); _t(d, x0 + sc / 100 * bw + 4, y + 6, f"{sc:g}", 7.4, INK, "start", True)
    for i, lab in enumerate(("Low", "Moderate", "High", "Very high")): _t(d, x0 + (i * 25 + 12.5) / 100 * bw, h - 12, lab, 6.5, MUTED, "middle")
    _axis(d, x0, bw, 5)
    return d


def position_chart(indicators: List[Dict[str, Any]], w=500, row=22):
    """One row per AVAILABLE stress indicator: its percentile (or VHI) on a 0-100 axis over its own published drought bands. Unavailable indicators are omitted here and listed in the table."""
    rows = [i for i in indicators if i.get("status") == "ok" and i.get("stress") is not None]
    n = max(len(rows), 1); h = 40 + n * row; x0, bw = 190, w - 190 - 24; d = Drawing(w, h)
    if not rows:
        _t(d, w / 2, h / 2, "No stress indicator was available", 9, MUTED, "middle"); return d
    for k, i in enumerate(rows):
        y = 18 + (n - 1 - k) * row; vhi = i["id"] == "vhi"; bands = KOGAN_BANDS if vhi else USDM_BANDS
        _t(d, x0 - 6, y + 6, _short(i["name"]), 7.2, INK, "end")
        for j, (lo, hi, lab) in enumerate(bands):
            c = _BAND_COLORS[(0, 1, 2, 3, 5)[j]] if vhi else _BAND_COLORS[j]            # Kogan has five classes: skip the pale D0 tint
            d.add(Rect(x0 + lo / 100 * bw, y + 2, (hi - lo) / 100 * bw, row - 8, fillColor=c, strokeColor=None))
        v = i["value"] if vhi else i.get("percentile")
        if v is None: continue
        v = min(max(float(v), 0.0), 100.0); x = x0 + v / 100 * bw
        d.add(Line(x, y, x, y + row - 4, strokeColor=INK, strokeWidth=1.4)); d.add(Circle(x, y + row - 4, 2.6, fillColor=INK, strokeColor=WHITE, strokeWidth=0.5))
        _t(d, min(x + 5, w - 4) if v < 88 else x - 5, y + 5, f"{v:g}", 6.8, INK, "start" if v < 88 else "end", True)
    _axis(d, x0, bw, 6)
    _t(d, x0, h - 12, "Dry end (left) = more stress. Percentile rows use the US Drought Monitor bands (D4 to D0); the VHI row uses Kogan's classes.", 6.4, MUTED)
    return d


def _short(name: str) -> str:
    return name.replace("Root-zone soil moisture percentile", "Root-zone moisture (SMAP)").replace("Groundwater-storage percentile (GRACE)", "Groundwater (GRACE)").replace(
        "Rainfall percentile (SPI-like)", "Rainfall (CHIRPS)").replace("NDVI anomaly vs seasonal normal", "NDVI anomaly").replace("Vegetation Health Index (VHI)", "Vegetation health (VHI)")


def sensitivity_chart(variants: Dict[str, Optional[float]], w=500, row=17):
    """The stress score under each weighting / leave-one-out variant, as dots on a 0-100 axis; the headline score is a vertical line. Variants with no score are listed as 'not computed'."""
    items = list(variants.items()); n = max(len(items), 1); h = 34 + n * row; x0, bw = 190, w - 190 - 40; d = Drawing(w, h)
    for i, (lo, hi) in enumerate(STRESS_BANDS): d.add(Rect(x0 + lo / 100 * bw, 16, (hi - lo) / 100 * bw, n * row, fillColor=BAND_TINTS[i], strokeColor=None))
    base = variants.get("equal weights (headline)")
    if base is not None: d.add(Line(x0 + base / 100 * bw, 14, x0 + base / 100 * bw, 18 + n * row, strokeColor=INK, strokeWidth=0.8, strokeDashArray=[3, 2]))
    for k, (name, v) in enumerate(items):
        y = 16 + (n - 1 - k) * row; head = name.startswith("equal weights"); _t(d, x0 - 6, y + 5, name, 7, INK, "end", head)
        if v is None: _t(d, x0 + 6, y + 5, "not computed", 6.8, MUTED); continue
        v = min(max(float(v), 0.0), 100.0); band = min(int(v // 25), 3); x = x0 + v / 100 * bw
        d.add(Circle(x, y + 8, 3.6 if head else 3.0, fillColor=CLASS_COLORS[band], strokeColor=INK if head else WHITE, strokeWidth=0.8 if head else 0.5)); _t(d, x + 7, y + 5, f"{v:g}", 6.8, INK)
    _axis(d, x0, bw, 5)
    return d
