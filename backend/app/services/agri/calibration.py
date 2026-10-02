"""
calibration.py - monotone (isotonic) calibration of raw suitability scores.

Raw scores are limiting-factor indices, not probabilities. A calibrator maps raw score -> an empirical
rate (e.g. P(crop is a major crop in this district)) learned from DEVELOPMENT data. Until one is fitted
the calibrator is the IDENTITY, and the response says so (calibration_status) - nothing is invented.

NOTE: a single global monotone map cannot change the order of crops, only the meaning/spread of the
numbers and the category cut-offs. Season-specific maps can re-order across seasons only. Per-crop maps
(not provided) would change order but overfit easily with the available support.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence

DEFAULT_PATH = Path(os.environ.get("VAYU_CALIBRATION", Path(__file__).with_name("calibration.json")))


def fit_isotonic(x: Sequence[float], y: Sequence[float]) -> List[List[float]]:
    """Pool-adjacent-violators. Returns knots [[x0, y0], ...] with y non-decreasing in x."""
    pts = sorted(zip(x, y))
    blocks = []                                     # [sum_y, count, x_min, x_max]
    for xi, yi in pts:
        blocks.append([float(yi), 1, xi, xi])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, n, lo, hi = blocks.pop()
            blocks[-1][0] += s; blocks[-1][1] += n; blocks[-1][3] = hi
    knots = []
    for s, n, lo, hi in blocks:
        v = s / n
        knots.append([lo, v]); 
        if hi != lo:
            knots.append([hi, v])
    return knots


class Calibrator:
    def __init__(self, maps: Optional[Dict[str, List[List[float]]]] = None, status: str = "identity (not fitted)"):
        self.maps = maps or {}
        self.status = status

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Calibrator":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(data["maps"], status=f"fitted: {data.get('note', 'see calibration.json')}")
        except (OSError, ValueError, KeyError):
            return cls()

    def apply(self, raw: float, season: Optional[str] = None) -> float:
        knots = self.maps.get(season) or self.maps.get("global")
        if not knots:
            return raw
        if raw <= knots[0][0]:
            return float(knots[0][1])
        for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
            if x0 <= raw <= x1:
                if x1 == x0:
                    return float(y0)
                v = y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
                return float(min(max(v, min(y0, y1)), max(y0, y1)))      # clamp: float noise must never break monotonicity
        return float(knots[-1][1])
