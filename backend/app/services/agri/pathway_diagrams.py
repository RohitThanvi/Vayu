"""
pathway_diagrams.py - generic, reusable vector diagrams for the farmer's PDF guide (reportlab graphics; crisp at any zoom, no image files, nothing downloaded).
Two kinds:
  * generic how-to diagrams (raised bed, drip layout, planting pit, mulch ring, trellis post, ridges, spacing grid, windbreak, IPM loop, scouting path, mushroom room, indoor room)
  * personal data graphics drawn from the farmer's own numbers (climate_chart, range_gauge, tier_scale)
All diagrams are schematic ("not to scale") and carry only labels already supported by the guide text. Helvetica / WinAnsi text only.
"""
import math
from typing import Dict, List, Optional, Sequence

from reportlab.graphics.shapes import Circle, Drawing, Ellipse, Group, Line, Polygon, PolyLine, Rect, String
from reportlab.lib import colors

GREEN, LEAF, PALE = colors.HexColor("#2E7D4F"), colors.HexColor("#5FAE74"), colors.HexColor("#DCEFE0")
SOIL, SOIL_DK, SOIL_LT = colors.HexColor("#A67C52"), colors.HexColor("#7A5632"), colors.HexColor("#E4D0B4")
WATER, SUN, INK, MUTED, RED = colors.HexColor("#4A90C8"), colors.HexColor("#E3A92D"), colors.HexColor("#1F2A24"), colors.HexColor("#5C6B63"), colors.HexColor("#C8553D")
STRAW, WHITE, LINE = colors.HexColor("#D9C27A"), colors.white, colors.HexColor("#C9D3CC")
TIER_COLORS = {1: colors.HexColor("#2E9E5B"), 2: colors.HexColor("#C99A2E"), 3: colors.HexColor("#3F83B7"), 4: colors.HexColor("#C8553D")}
MONTHS = "J F M A M J J A S O N D".split()


def _t(d, x, y, s, size=7.5, color=INK, anchor="start", bold=False):
    d.add(String(x, y, s, fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=size, fillColor=color, textAnchor=anchor))


def _arrow(d, x1, y1, x2, y2, color=MUTED, w=0.9, head=4.0):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=w))
    a = math.atan2(y2 - y1, x2 - x1)
    d.add(Polygon([x2, y2, x2 - head * math.cos(a - 0.45), y2 - head * math.sin(a - 0.45), x2 - head * math.cos(a + 0.45), y2 - head * math.sin(a + 0.45)], fillColor=color, strokeColor=color, strokeWidth=0.3))


def _dim(d, x1, y1, x2, y2, label, color=MUTED, off=(0, 0), size=7):
    _arrow(d, x1, y1, x2, y2, color, 0.7, 3); _arrow(d, x2, y2, x1, y1, color, 0.7, 3)
    _t(d, (x1 + x2) / 2 + off[0], (y1 + y2) / 2 + off[1], label, size, color, "middle")


def _plant(d, x, y, h=16, r=6, color=LEAF):
    d.add(Line(x, y, x, y + h, strokeColor=GREEN, strokeWidth=1.4)); d.add(Circle(x, y + h + r * 0.6, r, fillColor=color, strokeColor=GREEN, strokeWidth=0.6))


def raised_bed(w=250, h=140):
    d = Drawing(w, h); g = 38
    d.add(Rect(0, g - 12, w, 12, fillColor=colors.HexColor("#CFE3F3"), strokeColor=None))
    d.add(Polygon([48, g, 78, g + 46, w - 78, g + 46, w - 48, g], fillColor=SOIL, strokeColor=SOIL_DK, strokeWidth=0.8))
    d.add(Line(0, g, w, g, strokeColor=SOIL_DK, strokeWidth=0.8))
    for x in (w * 0.38, w * 0.5, w * 0.62): _plant(d, x, g + 46)
    _dim(d, w - 30, g, w - 30, g + 46, "15-30 cm", off=(-2, 0)); _t(d, 24, g - 9, "furrow", 7, WATER, "middle"); _t(d, w - 24, g - 9, "furrow", 7, WATER, "middle")
    _t(d, w / 2, g - 9, "water drains away from the roots", 7, MUTED, "middle"); _t(d, w / 2, g + 22, "raised bed", 8, WHITE, "middle", True)
    return d


def drip_layout(w=250, h=150):
    d = Drawing(w, h)
    d.add(Rect(36, 12, w - 46, h - 30, fillColor=PALE, strokeColor=GREEN, strokeWidth=0.8))
    d.add(Rect(4, h - 30, 26, 22, fillColor=WATER, strokeColor=INK, strokeWidth=0.6)); _t(d, 17, h - 38, "tank", 6.5, MUTED, "middle")
    d.add(Line(30, h - 19, 46, h - 19, strokeColor=INK, strokeWidth=1.6)); d.add(Line(46, h - 19, 46, 18, strokeColor=INK, strokeWidth=1.6))
    for i, y in enumerate((h - 34, h - 62, h - 90, h - 118)):
        d.add(Line(46, y, w - 16, y, strokeColor=WATER, strokeWidth=1.0))
        for x in range(70, int(w - 22), 34):
            d.add(Circle(x, y, 1.6, fillColor=WATER, strokeColor=WATER)); d.add(Circle(x, y + 7, 5, fillColor=LEAF, strokeColor=GREEN, strokeWidth=0.5))
    _t(d, 52, h - 8, "main line", 6.5, MUTED); _t(d, w - 18, h - 14, "lateral lines", 6.5, MUTED, "end"); _t(d, w - 18, 3, "one emitter at each plant", 6.5, WATER, "end")
    return d


def pit_planting(size="45 x 45 x 45 cm", w=250, h=165):
    d = Drawing(w, h); gy = 105
    d.add(Rect(0, 20, w, gy - 20, fillColor=SOIL_LT, strokeColor=None)); d.add(Line(0, gy, w, gy, strokeColor=SOIL_DK, strokeWidth=1))
    d.add(Rect(78, 38, 94, gy - 38, fillColor=SOIL_DK, strokeColor=INK, strokeWidth=0.8))
    d.add(Ellipse(125, 58, 17, 11, fillColor=colors.HexColor("#8C6A47"), strokeColor=INK, strokeWidth=0.5))
    d.add(Line(125, 58, 125, gy + 28, strokeColor=GREEN, strokeWidth=1.8)); d.add(Circle(125, gy + 40, 12, fillColor=LEAF, strokeColor=GREEN, strokeWidth=0.6))
    _dim(d, 78, 28, 172, 28, size, off=(0, -9)); _t(d, 3, gy - 28, "topsoil mixed with", 7, MUTED); _t(d, 3, gy - 38, "compost or manure", 7, MUTED)
    _arrow(d, 70, gy - 33, 90, gy - 33, MUTED); _t(d, 182, gy + 3, "ground level", 7, MUTED); _t(d, 182, gy - 14, "keep the stem collar", 7, MUTED); _t(d, 182, gy - 24, "at ground level", 7, MUTED)
    return d


def mulch_ring(w=250, h=140):
    d = Drawing(w, h); gy = 62
    d.add(Rect(0, 8, w, gy - 8, fillColor=SOIL_LT, strokeColor=None))
    for x1, x2 in ((22, 98), (152, 228)): d.add(Rect(x1, gy, x2 - x1, 11, fillColor=STRAW, strokeColor=SOIL_DK, strokeWidth=0.6))
    d.add(Line(0, gy, w, gy, strokeColor=SOIL_DK, strokeWidth=0.8)); d.add(Line(125, gy, 125, gy + 56, strokeColor=GREEN, strokeWidth=2)); d.add(Circle(125, gy + 66, 13, fillColor=LEAF, strokeColor=GREEN))
    for dx in (-30, -12, 12, 30): d.add(Line(125, gy, 125 + dx, gy - 38, strokeColor=SOIL_DK, strokeWidth=0.8))
    _t(d, 60, gy + 16, "mulch layer", 7.5, MUTED, "middle"); _t(d, 190, gy + 16, "mulch layer", 7.5, MUTED, "middle"); _t(d, 125, gy - 52, "roots", 7, MUTED, "middle")
    _dim(d, 98, gy + 5, 152, gy + 5, "", off=(0, 0)); _t(d, 125, gy + 23, "", 6); _t(d, 125, 2, "keep mulch off the stem; it saves water and holds weeds down", 7, MUTED, "middle")
    return d


def trellis_post(w=250, h=175):
    d = Drawing(w, h); gy = 22
    d.add(Rect(0, gy - 10, w, 10, fillColor=SOIL_LT, strokeColor=None)); d.add(Line(0, gy, w, gy, strokeColor=SOIL_DK, strokeWidth=1))
    d.add(Rect(120, gy, 7, 112, fillColor=SOIL_DK, strokeColor=INK, strokeWidth=0.5))
    d.add(Circle(123.5, gy + 120, 15, fillColor=None, strokeColor=INK, strokeWidth=1.6))
    d.add(Line(123.5, gy, 123.5, gy + 112, strokeColor=GREEN, strokeWidth=2.2))
    for dx in (-28, 28, -18, 18):
        d.add(PolyLine([123.5, gy + 133, 123.5 + dx * 0.8, gy + 150, 123.5 + dx * 1.6, gy + 128, 123.5 + dx * 1.9, gy + 98], strokeColor=GREEN, strokeWidth=1.6))
        d.add(Circle(123.5 + dx * 1.9, gy + 94, 6, fillColor=colors.HexColor("#D6336C"), strokeColor=colors.HexColor("#8F1D48"), strokeWidth=0.5))
    _dim(d, 108, gy, 108, gy + 112, "1.5-2 m post", off=(-30, 0)); _t(d, 188, gy + 118, "ring holds", 7, MUTED); _t(d, 188, gy + 109, "the branches", 7, MUTED)
    d.add(Rect(30, gy, 4, 55, fillColor=SOIL_DK, strokeColor=None)); d.add(Rect(213, gy, 4, 55, fillColor=SOIL_DK, strokeColor=None))
    _t(d, 124, 5, "one post per plant, in full sun", 7, MUTED, "middle")
    return d


def ridge_planting(w=250, h=130):
    d = Drawing(w, h); g = 30
    d.add(Rect(0, g - 12, w, 12, fillColor=SOIL_LT, strokeColor=None))
    for cx in (50, 125, 200):
        d.add(Polygon([cx - 32, g, cx - 14, g + 28, cx + 14, g + 28, cx + 32, g], fillColor=SOIL, strokeColor=SOIL_DK, strokeWidth=0.7)); _plant(d, cx, g + 28, 14, 6)
    d.add(Line(0, g, w, g, strokeColor=SOIL_DK, strokeWidth=0.8)); _t(d, 88, g - 9, "furrow", 7, WATER, "middle"); _t(d, 163, g - 9, "furrow", 7, WATER, "middle")
    _dim(d, 50, g + 62, 125, g + 62, "ridge to ridge: per crop table", off=(0, 4)); _t(d, 125, 4, "plant on the ridge; the furrow carries excess water", 7, MUTED, "middle")
    return d


def spacing_grid(a=3.0, b=3.0, w=250, h=170):
    """Top view; a = distance between plants in a row, b = distance between rows. Schematic, not to scale."""
    d = Drawing(w, h); big, small = max(a, b), min(a, b); ratio = min(big / max(small, 1e-9), 3.0)
    base = 40.0
    ux = base if a >= b else base / ratio
    uy = base if b >= a else base / ratio
    x0, y0 = 36 + ux / 2, 32 + uy / 2
    d.add(Rect(8, 12, w - 16, h - 28, fillColor=PALE, strokeColor=GREEN, strokeWidth=0.8))
    cols, rows = 3, 3
    for i in range(cols):
        for j in range(rows): d.add(Circle(x0 + i * ux, y0 + j * uy, 6.5, fillColor=LEAF, strokeColor=GREEN, strokeWidth=0.6))
    _dim(d, x0, y0 - uy / 2 - 3, x0 + ux, y0 - uy / 2 - 3, f"{a:g} m", off=(0, -9))
    _dim(d, x0 + (cols - 1) * ux + 18, y0, x0 + (cols - 1) * ux + 18, y0 + uy, f"{b:g} m", off=(14, -2))
    _t(d, w - 14, 17, "schematic, not to scale", 6.5, MUTED, "end"); _t(d, 14, h - 24, f"each plant has about {a * b:g} m2", 7, MUTED)
    return d


def windbreak(w=250, h=150):
    d = Drawing(w, h)
    d.add(Rect(8, 10, w - 16, h - 20, fillColor=PALE, strokeColor=GREEN, strokeWidth=0.8))
    for j in range(5): d.add(Circle(32, 28 + j * 20, 7, fillColor=GREEN, strokeColor=INK, strokeWidth=0.4))
    for i in range(4):
        for j in range(5): d.add(Circle(92 + i * 38, 32 + j * 22, 8, fillColor=LEAF, strokeColor=GREEN, strokeWidth=0.5))
    for y in (50, 80, 110): _arrow(d, 2, y, 20, y, RED, 1.4, 5)
    _t(d, 32, 14, "windbreak", 7, GREEN, "middle", True); _t(d, 4, 128, "wind", 7, RED); _t(d, 172, h - 7, "protected trees", 7, MUTED, "middle")
    return d


def ipm_cycle(w=250, h=205):
    d = Drawing(w, h); cx, cy, r = w / 2, h / 2 - 2, 70
    steps = [("1 Scout weekly", GREEN), ("2 Identify it", colors.HexColor("#3F83B7")), ("3 Cultural fix", colors.HexColor("#C99A2E")), ("4 Organic option", colors.HexColor("#7A4FA3")), ("5 Record, review", MUTED)]
    pts = []
    for i, (label, col) in enumerate(steps):
        a = math.pi / 2 - i * 2 * math.pi / 5; x, y = cx + 90 * math.cos(a), cy + r * math.sin(a); pts.append((x, y, a))
    for i in range(5):
        x1, y1, a1 = pts[i]; x2, y2, a2 = pts[(i + 1) % 5]
        mx1, my1 = x1 + (x2 - x1) * 0.38, y1 + (y2 - y1) * 0.38; mx2, my2 = x1 + (x2 - x1) * 0.62, y1 + (y2 - y1) * 0.62
        _arrow(d, mx1, my1, mx2, my2, MUTED, 1.2, 5)
    for (x, y, a), (label, col) in zip(pts, steps):
        d.add(Rect(x - 36, y - 10, 72, 20, rx=5, ry=5, fillColor=col, strokeColor=None)); _t(d, x, y - 3, label, 7.2, WHITE, "middle", True)
    _t(d, cx, cy + 2, "prevent first;", 7, MUTED, "middle"); _t(d, cx, cy - 7, "no chemicals here", 7, MUTED, "middle")
    return d


def scouting_path(w=250, h=150):
    d = Drawing(w, h)
    d.add(Rect(10, 10, w - 20, h - 22, fillColor=PALE, strokeColor=GREEN, strokeWidth=0.8))
    xs = [30, 70, 110, 150, 190, 222]; ys = [h - 28, 28, h - 28, 28, h - 28, 28]
    pts = []
    for x, y in zip(xs, ys): pts += [x, y]
    d.add(PolyLine(pts[:10], strokeColor=INK, strokeWidth=1.1, strokeDashArray=[3, 2]))
    for i, (x, y) in enumerate(list(zip(xs, ys))[:5]):
        d.add(Circle(x, y, 7, fillColor=SUN, strokeColor=INK, strokeWidth=0.6)); _t(d, x, y - 2.5, str(i + 1), 7, INK, "middle", True)
    _t(d, w / 2, h - 7, "walk a W across the field and check at least 5 spots", 6.8, MUTED, "middle")
    return d


def mushroom_room(w=250, h=170):
    d = Drawing(w, h)
    d.add(Rect(8, 14, w - 16, h - 40, fillColor=colors.HexColor("#F2EFE6"), strokeColor=INK, strokeWidth=0.9)); d.add(Rect(8, 14, w - 16, 8, fillColor=colors.HexColor("#CFE3F3"), strokeColor=None))
    d.add(Line(8, h - 26, w / 2, h - 8, strokeColor=INK, strokeWidth=0.9)); d.add(Line(w / 2, h - 8, w - 8, h - 26, strokeColor=INK, strokeWidth=0.9))
    for rx in (34, 112):
        d.add(Line(rx, 26, rx, h - 36, strokeColor=SOIL_DK, strokeWidth=1.6)); d.add(Line(rx + 64, 26, rx + 64, h - 36, strokeColor=SOIL_DK, strokeWidth=1.6))
        for ry in (h - 52, h - 82, h - 112): d.add(Line(rx, ry, rx + 64, ry, strokeColor=SOIL_DK, strokeWidth=1.2))
        for ry in (h - 52, h - 82, h - 112):
            for k in range(4): d.add(Rect(rx + 6 + k * 14, ry - 17, 9, 15, rx=3, ry=3, fillColor=STRAW, strokeColor=SOIL_DK, strokeWidth=0.4))
    d.add(Rect(w - 38, h - 78, 22, 36, fillColor=colors.HexColor("#E8F0E5"), strokeColor=INK, strokeWidth=0.6))
    for k in range(5): d.add(Line(w - 38, h - 44 - k * 7, w - 16, h - 44 - k * 7, strokeColor=MUTED, strokeWidth=0.3))
    _t(d, w - 27, h - 90, "fine net", 6.5, MUTED, "middle"); _t(d, w / 2, 3, "dark, clean, ventilated room; 20-30 C; humidity 70-90 percent", 7, MUTED, "middle")
    _t(d, 12, 28, "wet floor", 6.5, WATER)
    return d


def controlled_room(w=250, h=170):
    d = Drawing(w, h)
    d.add(Rect(8, 14, w - 16, h - 30, fillColor=colors.HexColor("#EEF3F8"), strokeColor=INK, strokeWidth=1.2))
    d.add(Rect(14, h - 54, 40, 28, fillColor=WATER, strokeColor=INK, strokeWidth=0.6)); _t(d, 34, h - 44, "chiller", 7, WHITE, "middle", True)
    d.add(Rect(w - 52, h - 54, 38, 28, fillColor=colors.HexColor("#8EB8D8"), strokeColor=INK, strokeWidth=0.6)); _t(d, w - 33, h - 44, "humidifier", 6.5, INK, "middle", True)
    d.add(Rect(70, h - 28, w - 140, 6, fillColor=SUN, strokeColor=INK, strokeWidth=0.5)); _t(d, w / 2, h - 18, "grow lights", 7, MUTED, "middle")
    for ry in (30, 62, 94):
        d.add(Line(40, ry, w - 40, ry, strokeColor=SOIL_DK, strokeWidth=1.6))
        for k in range(8): d.add(Rect(48 + k * 20, ry + 2, 14, 8, fillColor=colors.HexColor("#8E6BB0"), strokeColor=INK, strokeWidth=0.4))
    _t(d, w / 2, 3, "insulated room: temperature and humidity set for each stage; restricted entry", 7, MUTED, "middle")
    return d


def tier_scale(tier: Optional[int], w=330, h=44):
    d = Drawing(w, h); labels = ["Open-field", "With help", "Controlled", "Not realistic"]; bw = (w - 18) / 4
    for i, lab in enumerate(labels):
        on = tier == i + 1; col = TIER_COLORS[i + 1]
        d.add(Rect(i * (bw + 6), 8, bw, 26, rx=4, ry=4, fillColor=col if on else colors.HexColor("#E9EEEA"), strokeColor=col if on else LINE, strokeWidth=1.2 if on else 0.6))
        _t(d, i * (bw + 6) + bw / 2, 17, lab, 7.8, WHITE if on else MUTED, "middle", on)
    if tier:
        i = tier - 1; x = i * (bw + 6) + bw / 2; d.add(Polygon([x - 4, 38, x + 4, 38, x, 33], fillColor=TIER_COLORS[tier], strokeColor=None)); _t(d, x, 40, "you are here", 6.5, TIER_COLORS[tier], "middle", True)
    return d


def range_gauge(value: Optional[float], lo: float, hi: float, unit: str = "", w=170, h=26, status="ok"):
    """Where the farmer's own value sits against the crop's range. status: ok | gap | unknown."""
    d = Drawing(w, h); span = max(hi - lo, 1e-9); mn, mx = lo - span * 0.6, hi + span * 0.6; x0, x1 = 6, w - 6
    X = lambda v: x0 + (min(max(v, mn), mx) - mn) / (mx - mn) * (x1 - x0)
    d.add(Rect(x0, 10, x1 - x0, 6, fillColor=colors.HexColor("#E9EEEA"), strokeColor=None)); d.add(Rect(X(lo), 10, X(hi) - X(lo), 6, fillColor=colors.HexColor("#A9D8B8"), strokeColor=None))
    _t(d, X(lo), 1, f"{lo:g}", 6, MUTED, "middle"); _t(d, X(hi), 1, f"{hi:g}{unit}", 6, MUTED, "middle")
    if value is None:
        _t(d, w / 2, 19, "no data", 6.8, MUTED, "middle"); return d
    col = TIER_COLORS[1] if status == "ok" else RED; x = X(value)
    d.add(Polygon([x - 4, 24, x + 4, 24, x, 17], fillColor=col, strokeColor=None)); d.add(Line(x, 10, x, 17, strokeColor=col, strokeWidth=1.2))
    return d


def climate_chart(temp: Sequence[Optional[float]], rain: Sequence[Optional[float]], band: Optional[Sequence[float]], cold_limit: Optional[float], hot_limit: Optional[float],
                  window_months: Optional[Sequence[int]] = None, w=500, h=190):
    """THIS location's monthly temperature (line) and rainfall (bars) against the crop's temperature band, its cold/hot limits and its planting window."""
    d = Drawing(w, h); x0, y0, cw, ch = 40, 30, w - 84, h - 58
    tv = [v for v in temp if v is not None]; rv = [v for v in rain if v is not None]
    if not tv and not rv:
        _t(d, w / 2, h / 2, "No monthly climate data for this location", 9, MUTED, "middle"); return d
    tlo = min(tv + [x for x in (cold_limit, band[0] if band else None) if x is not None] or [0]) - 3; thi = max(tv + [x for x in (hot_limit, band[1] if band else None) if x is not None] or [30]) + 3
    rmax = max(rv + [10]) * 1.15; bw = cw / 12
    TY = lambda v: y0 + (v - tlo) / (thi - tlo) * ch; RY = lambda v: y0 + v / rmax * ch
    d.add(Rect(x0, y0, cw, ch, fillColor=WHITE, strokeColor=LINE, strokeWidth=0.6))
    for m in (window_months or []): d.add(Rect(x0 + (m - 1) * bw, y0, bw, ch, fillColor=colors.HexColor("#FFF2CC"), strokeColor=None))
    if band: d.add(Rect(x0, TY(band[0]), cw, TY(band[1]) - TY(band[0]), fillColor=colors.HexColor("#DCEFE0"), strokeColor=None))
    for m, v in enumerate(rain):
        if v is not None: d.add(Rect(x0 + m * bw + bw * 0.18, y0, bw * 0.64, RY(v) - y0, fillColor=colors.HexColor("#9CC6E6"), strokeColor=None))
    for lim, lab in ((cold_limit, "cool limit"), (hot_limit, "hot limit")):
        if lim is not None: d.add(Line(x0, TY(lim), x0 + cw, TY(lim), strokeColor=RED, strokeWidth=0.8, strokeDashArray=[3, 2])); _t(d, x0 + cw + 3, TY(lim) - 2, f"{lab} {lim:g}", 6.2, RED)
    pts = [(x0 + (m + 0.5) * bw, TY(v)) for m, v in enumerate(temp) if v is not None]
    if len(pts) > 1: d.add(PolyLine([c for p in pts for c in p], strokeColor=colors.HexColor("#D9742B"), strokeWidth=1.8))
    for p in pts: d.add(Circle(p[0], p[1], 2.2, fillColor=colors.HexColor("#D9742B"), strokeColor=WHITE, strokeWidth=0.5))
    for m in range(12): _t(d, x0 + (m + 0.5) * bw, y0 - 10, MONTHS[m], 7, MUTED, "middle")
    for k in range(5):
        v = tlo + (thi - tlo) * k / 4; _t(d, x0 - 4, TY(v) - 2, f"{v:.0f}", 6.5, colors.HexColor("#D9742B"), "end")
        r = rmax * k / 4; _t(d, x0 + cw + 4, y0 + ch * k / 4 - 2, f"{r:.0f}", 6.5, colors.HexColor("#4A7FA8"))
    _t(d, x0 - 4, y0 + ch + 6, "deg C", 6.5, colors.HexColor("#D9742B"), "end"); _t(d, x0 + cw + 4, y0 + ch + 6, "rain, mm", 6.5, colors.HexColor("#4A7FA8"))
    lx = x0
    for col, lab in ((colors.HexColor("#D9742B"), "your monthly temperature"), (colors.HexColor("#9CC6E6"), "your monthly rainfall"), (colors.HexColor("#DCEFE0"), "crop's comfortable temperature"), (colors.HexColor("#FFF2CC"), "planting window")):
        d.add(Rect(lx, h - 14, 8, 6, fillColor=col, strokeColor=LINE, strokeWidth=0.4)); _t(d, lx + 11, h - 13, lab, 6.5, MUTED); lx += 22 + len(lab) * 3.1
    return d


DIAGRAMS = {"raised_bed": raised_bed, "drip_layout": drip_layout, "pit_planting": pit_planting, "mulch_ring": mulch_ring, "trellis_post": trellis_post, "ridge_planting": ridge_planting,
            "spacing_grid": spacing_grid, "windbreak": windbreak, "ipm_cycle": ipm_cycle, "scouting_path": scouting_path, "mushroom_room": mushroom_room, "controlled_room": controlled_room}
CAPTIONS = {"raised_bed": "Raised bed with drainage furrows", "drip_layout": "Drip irrigation layout (schematic)", "pit_planting": "Planting pit", "mulch_ring": "Mulch around a plant",
            "trellis_post": "Post and ring support for a climbing cactus", "ridge_planting": "Planting on ridges", "spacing_grid": "Plant spacing, top view", "windbreak": "Windbreak on the windward side",
            "ipm_cycle": "Pest and disease loop: prevent first, then look, fix, review", "scouting_path": "How to walk the field when scouting", "mushroom_room": "A simple growing room",
            "controlled_room": "An engineered indoor growing room"}
