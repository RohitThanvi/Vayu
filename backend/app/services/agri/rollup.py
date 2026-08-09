"""
rollup.py — role-based views over the alert history.

A block officer needs individual region alerts; a district-level rollup
needs counts and a ranked list, not the same raw feed. This aggregates
db.alerts by region for whichever role is asking, without re-running any
satellite computation (cheap, reads persisted alert history only).
"""

from collections import defaultdict
from typing import Any, Dict

from . import db


def get_rollup(role: str = "officer") -> Dict[str, Any]:
    regions = db.list_regions()
    latest_by_region = {}
    for region in regions:
        alerts = db.list_alerts(region_id=region["id"], limit=1)
        latest_by_region[region["id"]] = alerts[0] if alerts else None

    if role == "officer":
        # full detail per region, sorted by most recent risk first
        items = []
        for region in regions:
            latest = latest_by_region.get(region["id"])
            items.append({
                "region": {k: region[k] for k in ("id", "name", "crop", "risk_threshold")},
                "latest_alert": latest,
            })
        items.sort(key=lambda x: (x["latest_alert"] or {}).get("risk_score", -1), reverse=True)
        return {"role": "officer", "regions": items, "total_regions": len(regions)}

    # district-level rollup: aggregate counts, no per-region granularity
    band_counts = defaultdict(int)
    scored = 0
    for region in regions:
        latest = latest_by_region.get(region["id"])
        if not latest:
            continue
        scored += 1
        score = latest["risk_score"]
        band = "severe" if score >= 75 else "high" if score >= 55 else "moderate" if score >= 30 else "low"
        band_counts[band] += 1

    top_risk_regions = sorted(
        [r for r in regions if latest_by_region.get(r["id"])],
        key=lambda r: latest_by_region[r["id"]]["risk_score"],
        reverse=True,
    )[:10]

    return {
        "role": "district",
        "total_regions": len(regions),
        "regions_with_data": scored,
        "band_counts": dict(band_counts),
        "top_risk_regions": [
            {"name": r["name"], "crop": r["crop"], "risk_score": latest_by_region[r["id"]]["risk_score"]}
            for r in top_risk_regions
        ],
    }
