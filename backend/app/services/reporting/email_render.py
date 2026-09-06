"""
email_render.py — renders the daily executive summary as a single HTML
string for email delivery.

Deliberately NOT using SVG or <img> charts: most email clients either
strip external images by default (Gmail/Outlook require an explicit
"show images" click, meaning every chart would render as a blank box
on first open) or don't support inline SVG at all (Outlook's Word
rendering engine notoriously doesn't). The charts here are built from
plain HTML tables with colored, width-percentage <td> cells — a
long-established, genuinely email-safe technique used in real
corporate reporting emails, and it renders identically whether or not
the recipient's client allows images.

All styling is inline (style="..." on every element) rather than a
<style> block — email clients strip <style> blocks unpredictably
(Gmail web strips them from the <head> but keeps inline styles; some
webmail clients do the opposite), so inline is the only style approach
that reliably works everywhere.
"""

import html
from typing import Any, Dict, List

DARK_BG = "#0a0c0f"
CARD_BG = "#0d1117"
BORDER = "#2a3040"
TEXT = "#ffffff"
TEXT2 = "#c7d0da"
TEXT3 = "#8a97a6"
ACCENT = "#c9a86a"   # gold, matches the app's research-agent/premium accents
GREEN = "#2ecc71"
RED = "#ff5a5a"
AMBER = "#f0b429"

FONT = "'Courier New', Courier, monospace"


def _esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _bar_row(label: str, value: float, max_value: float, color: str, value_label: str) -> str:
    """One email-safe 'chart' row: a label, a colored bar (as a table
    cell width percentage), and a value — the core building block for
    every chart in this email."""
    pct = max(2, min(100, (abs(value) / max_value * 100) if max_value else 0))
    return f"""
    <tr>
      <td style="padding:4px 10px 4px 0; font-family:{FONT}; font-size:12px; color:{TEXT2}; white-space:nowrap; width:38%;">{_esc(label)}</td>
      <td style="padding:4px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BORDER}; border-radius:3px;">
          <tr><td style="width:{pct:.0f}%; height:10px; background:{color}; border-radius:3px; font-size:1px; line-height:1px;">&nbsp;</td>
              <td style="font-size:1px; line-height:1px;">&nbsp;</td></tr>
        </table>
      </td>
      <td style="padding:4px 0 4px 10px; font-family:{FONT}; font-size:12px; color:{color}; text-align:right; white-space:nowrap; width:18%; font-weight:bold;">{_esc(value_label)}</td>
    </tr>
    """


def _section_header(title: str) -> str:
    return f"""
    <tr><td style="padding:26px 0 10px;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="width:3px; height:13px; background:{ACCENT}; border-radius:2px;"></td>
        <td style="padding-left:8px; font-family:{FONT}; font-size:12px; letter-spacing:2px; color:{TEXT3}; text-transform:uppercase;">{_esc(title)}</td>
      </tr></table>
    </td></tr>
    """


def _commodity_chart(commodities: List[Dict[str, Any]]) -> str:
    movers = sorted([c for c in commodities if c.get("change_pct") is not None], key=lambda c: -abs(c["change_pct"]))[:8]
    if not movers:
        return f'<tr><td style="font-family:{FONT}; font-size:12px; color:{TEXT3};">No commodity data available today.</td></tr>'
    max_abs = max(abs(c["change_pct"]) for c in movers) or 1
    rows = ""
    for c in movers:
        color = GREEN if c["change_pct"] > 0 else RED if c["change_pct"] < 0 else TEXT3
        rows += _bar_row(c["name"], c["change_pct"], max_abs, color, f"{c['change_pct']:+.2f}%")
    return f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'


def _chokepoint_chart(chokepoints: List[Dict[str, Any]]) -> str:
    status_color = {"normal": GREEN, "elevated": AMBER, "disrupted": RED, "insufficient_history": TEXT3}
    rows = ""
    for cp in chokepoints:
        color = status_color.get(cp["status"], TEXT3)
        dev = cp.get("deviation_pct")
        value_label = f"{dev:+.0f}%" if dev is not None else "—"
        rows += _bar_row(cp["name"], abs(dev) if dev is not None else 2, max((abs(c.get("deviation_pct") or 0) for c in chokepoints), default=1) or 1, color, value_label)
    return f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'


def _status_pill(text: str, color: str) -> str:
    return f'<span style="font-family:{FONT}; font-size:10px; letter-spacing:1px; font-weight:bold; color:{color}; border:1px solid {color}; border-radius:3px; padding:2px 7px;">{_esc(text)}</span>'


def render_report_html(summary: Dict[str, Any], snapshot: Dict[str, Any], unsubscribe_url: str, dashboard_url: str, report_date: str) -> str:
    commodities = snapshot.get("commodities", [])
    chokepoints = snapshot.get("supply_chain", {}).get("chokepoints", [])
    intel_stats = snapshot.get("intel_stats", {})
    aqi = snapshot.get("air_quality", {})
    aircraft = snapshot.get("aircraft_stats", {})
    vessels = snapshot.get("vessel_stats", {})
    agri = snapshot.get("agri_watchlist", [])

    watchpoints_html = "".join(
        f'<tr><td style="padding:5px 0; font-family:{FONT}; font-size:12.5px; color:{TEXT2}; line-height:1.6;">'
        f'<span style="color:{ACCENT};">&#9656;</span>&nbsp; {_esc(w)}</td></tr>'
        for w in summary.get("key_watchpoints", []) if w
    ) or f'<tr><td style="font-family:{FONT}; font-size:12px; color:{TEXT3};">Nothing flagged for tomorrow.</td></tr>'

    agri_html = f'<tr><td style="font-family:{FONT}; font-size:12px; color:{TEXT3};">No regions on the watchlist.</td></tr>'
    if agri:
        rows = ""
        for r in agri:
            score = r.get("latest_score")
            color = GREEN if (score is None or score < 30) else AMBER if score < 60 else RED
            score_label = f"{score:.0f}/100" if score is not None else "not yet scored"
            rows += f"""
            <tr><td style="padding:8px 0; border-top:1px solid {BORDER};">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
                <td style="font-family:{FONT}; font-size:12.5px; color:{TEXT2};">{_esc(r['name'])}{f" ({_esc(r['crop'])})" if r.get('crop') else ''}</td>
                <td style="text-align:right; font-family:{FONT}; font-size:12px; font-weight:bold; color:{color};">{score_label}</td>
              </tr></table>
            </td></tr>
            """
        agri_html = rows

    worst_aqi = aqi.get("worst_stations", [])
    aqi_html = f'<tr><td style="font-family:{FONT}; font-size:12px; color:{TEXT3};">No air quality data available today.</td></tr>'
    if worst_aqi:
        max_aqi = max(s["aqi"] for s in worst_aqi) or 1
        rows = ""
        for s in worst_aqi:
            color = GREEN if s["aqi"] <= 100 else AMBER if s["aqi"] <= 200 else RED
            rows += _bar_row(s.get("station_name", "Station"), s["aqi"], max_aqi, color, f"{s['aqi']:.0f}")
        aqi_html = f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'

    return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0; padding:0; background:{DARK_BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{DARK_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; overflow:hidden;">

  <tr><td style="padding:26px 28px 20px; border-bottom:1px solid {BORDER};">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{FONT}; font-size:15px; letter-spacing:3px; color:{TEXT}; font-weight:bold;">VAYU</td>
      <td style="text-align:right; font-family:{FONT}; font-size:11px; color:{TEXT3};">{_esc(report_date)}</td>
    </tr></table>
    <div style="font-family:{FONT}; font-size:10px; letter-spacing:2px; color:{TEXT3}; margin-top:2px;">EXECUTIVE INTELLIGENCE BRIEFING</div>
  </td></tr>

  <tr><td style="padding:22px 28px 6px;">
    <div style="font-family:Georgia,serif; font-size:19px; line-height:1.4; color:{TEXT}; font-weight:bold;">{_esc(summary.get('headline', ''))}</div>
  </td></tr>

  <tr><td style="padding:0 28px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">

    {_section_header('Global Markets')}
    <tr><td style="font-family:{FONT}; font-size:12.5px; color:{TEXT2}; line-height:1.6; padding-bottom:10px;">{_esc(summary.get('market_summary', ''))}</td></tr>
    {_commodity_chart(commodities)}

    {_section_header('Maritime & Supply Chain')}
    <tr><td style="font-family:{FONT}; font-size:12.5px; color:{TEXT2}; line-height:1.6; padding-bottom:10px;">{_esc(summary.get('supply_chain_summary', ''))}</td></tr>
    {_chokepoint_chart(chokepoints)}
    <tr><td style="padding-top:10px; font-family:{FONT}; font-size:11px; color:{TEXT3};">
      {vessels.get('active_vessels', 0)} vessels tracked &middot; {aircraft.get('active_aircraft', 0)} aircraft tracked
    </td></tr>

    {_section_header('Global Hazards & Events')}
    <tr><td style="font-family:{FONT}; font-size:12.5px; color:{TEXT2}; line-height:1.6; padding-bottom:6px;">{_esc(summary.get('hazard_summary', ''))}</td></tr>
    <tr><td style="font-family:{FONT}; font-size:11px; color:{TEXT3};">
      {intel_stats.get('current_events', 0)} active events tracked (USGS, NASA FIRMS, GDELT, ACLED)
    </td></tr>

    {_section_header('Air Quality (India, CPCB)')}
    <tr><td style="font-family:{FONT}; font-size:12px; color:{TEXT3}; padding-bottom:8px;">
      {f"National average AQI: {aqi.get('national_avg_aqi')}" if aqi.get('national_avg_aqi') is not None else 'Data unavailable today.'}
      &middot; Worst stations:
    </td></tr>
    {aqi_html}

    {_section_header('Agricultural Watchlist')}
    <tr><td style="font-family:{FONT}; font-size:12.5px; color:{TEXT2}; line-height:1.6; padding-bottom:6px;">{_esc(summary.get('agri_summary', ''))}</td></tr>
    <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{agri_html}</table></td></tr>

    {_section_header('Watch Tomorrow')}
    <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{watchpoints_html}</table></td></tr>

  </table></td></tr>

  <tr><td style="padding:28px;">
    <a href="{_esc(dashboard_url)}" style="display:block; text-align:center; background:linear-gradient(180deg,#f5d98a,{ACCENT}); color:#05070c; font-family:{FONT}; font-size:12px; font-weight:bold; letter-spacing:2px; text-decoration:none; padding:13px; border-radius:4px;">
      OPEN FULL DASHBOARD
    </a>
  </td></tr>

  <tr><td style="padding:0 28px 24px; border-top:1px solid {BORDER}; padding-top:16px;">
    <div style="font-family:{FONT}; font-size:10px; color:{TEXT3}; line-height:1.7;">
      You're receiving this because you subscribed to Vayu's daily executive briefing.
      <a href="{_esc(unsubscribe_url)}" style="color:{TEXT3}; text-decoration:underline;">Unsubscribe</a>
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body>
</html>
"""
