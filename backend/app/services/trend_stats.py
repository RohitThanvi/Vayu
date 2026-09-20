"""
trend_stats.py — Mann-Kendall trend test + Sen's slope estimator, the
standard non-parametric trend-significance pairing in the hydrology/
remote-sensing literature (Mann 1945; Kendall 1975; Sen 1968) for exactly
the kind of noisy, irregularly-sampled environmental time series this
project produces (NDVI, LST, precipitation, groundwater anomaly...).
Distinguishes "the chart looks like it's going up" from "this trend is
statistically significant at p<0.05" — nothing in this codebase did that
before this module existed; every time-series result up to now was a
chart with no significance test attached, just a slope a human eyeballs.

Non-parametric by design: makes no assumption about the data's
distribution (unlike a linear-regression trend test, which assumes normal
residuals) — appropriate here since none of these environmental series
are guaranteed normally distributed, and the test is robust to outliers
and irregular sampling (cloud-gap months, missing periods), both routine
in satellite time series.
"""

import logging
import math
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MIN_POINTS = 4  # below this, "trend" and "no trend" aren't meaningfully distinguishable


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def mann_kendall_test(values: List[float], dates: Optional[List[str]] = None, alpha: float = 0.05) -> Dict[str, Any]:
    """
    `values` must already be a time-ordered, gap-free series (the caller
    filters out nulls/gaps before calling this — Mann-Kendall assumes
    complete, ordered data; a gap isn't a value of zero). `dates`, if
    given as parseable YYYY-MM-DD strings matching `values` one-to-one, is
    used only to express Sen's slope in per-year units — the test
    statistic itself depends only on rank order, not elapsed time.

    Returns the S statistic, its tie-corrected variance, the normal-
    approximation Z score, a two-sided p-value, whether the trend is
    significant at `alpha`, the trend direction, and Sen's slope (the
    median of all pairwise slopes — robust to outliers, the standard
    companion estimator to Mann-Kendall for trend MAGNITUDE, since the
    test itself only establishes significance and direction, not size).
    """
    n = len(values)
    if n < MIN_POINTS:
        return {"status": "insufficient_data", "note": f"Mann-Kendall needs at least {MIN_POINTS} points, got {n}."}

    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = values[j] - values[i]
            s += (diff > 0) - (diff < 0)

    # Tie-corrected variance (Kendall 1975) — ties lower the variance
    # relative to the tie-free case, and satellite-derived indices
    # (hard classification thresholds, coarse sensor precision) can have
    # genuine repeated values, not just coincidence.
    tie_counts = Counter(values)
    tie_term = sum(t * (t - 1) * (2 * t + 5) for t in tie_counts.values() if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18

    if var_s <= 0:
        z = 0.0
    elif s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0

    p_value = 2 * (1 - _normal_cdf(abs(z)))
    significant = p_value < alpha
    trend = ("increasing" if s > 0 else "decreasing") if significant else "no significant trend"

    # Sen's slope: median of all pairwise slopes (x_j - x_i) / (j - i).
    slopes = sorted((values[j] - values[i]) / (j - i) for i in range(n - 1) for j in range(i + 1, n))
    m = len(slopes)
    sens_slope = slopes[m // 2] if m % 2 == 1 else (slopes[m // 2 - 1] + slopes[m // 2]) / 2

    sens_slope_per_year = None
    if dates and len(dates) == n:
        try:
            parsed = [datetime.strptime(d[:10], "%Y-%m-%d") for d in dates]
            total_years = (parsed[-1] - parsed[0]).days / 365.25
            avg_step_years = total_years / (n - 1) if n > 1 else 0
            if avg_step_years > 0:
                # sens_slope above is per SAMPLE STEP; rescale to per year
                # using the actual average spacing implied by the real
                # date range, since samples may not be evenly spaced.
                sens_slope_per_year = sens_slope / avg_step_years
        except (ValueError, IndexError, TypeError):
            pass

    return {
        "status": "ok",
        "n": n,
        "s_statistic": s,
        "z_score": round(z, 4),
        "p_value": round(p_value, 5),
        "significant": significant,
        "alpha": alpha,
        "trend": trend,
        "sens_slope_per_step": round(sens_slope, 6),
        "sens_slope_per_year": round(sens_slope_per_year, 6) if sens_slope_per_year is not None else None,
        "method": (
            f"Mann-Kendall trend test (Mann 1945; Kendall 1975), tie-corrected variance, normal approximation "
            f"for the Z statistic; Sen's slope (Sen 1968) for trend magnitude. Trend called significant at "
            f"p<{alpha}; 'no significant trend' means the null hypothesis (no monotonic trend) could not be "
            f"rejected at this AOI/period/sample size — not proof the value is unchanging."
        ),
    }
