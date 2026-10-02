"""
engine_config.py - every tunable constant of the suitability engine, in one place.

Values marked ESTIMATE are documented defaults, not measurements. They may be tuned ONLY on the
development split of the validation data (never on the final test set) and every change should be
justified in the commit message.
"""

# ---- rainfall / seasonality evidence
DRY_MONTH_MM = 50.0              # ESTIMATE: a month with less rain than this counts as "dry" (rule of thumb; Koppen uses 60)

# A crop may declare a rain-sensitive 'dry window' (e.g. flowering). Window rain is judged against n_months * DRY_MONTH_MM (all months dry = full score),
# falling linearly to zero at DRY_WINDOW_ZERO_FACTOR times that.
DRY_WINDOW_ZERO_FACTOR = 3.0     # ESTIMATE

# ---- constraint system
HARD_FACTORS = ("temperature",)  # absolute survival limits. Everything else is a SOFT limitation.
SOFT_FLOOR = 0.10                # ESTIMATE: lowest score a soft (amendable / mitigable) limitation can impose

# ---- excess-water (waterlogging / disease) model
# Rainfall AMOUNT above a crop's ceiling is only a proxy for waterlogging risk; how much of the
# raw penalty applies depends on drainage evidence. strength 1.0 = full penalty (can reach the floor),
# strength 0.5 = at most half the penalty.
EXCESS_STRENGTH_BY_DRAINAGE = {   # ESTIMATE
    "good": 0.50, "moderate": 0.75, "unknown": 0.75, "poor": 1.00,
}
EXCESS_SENSITIVITY_ADJUST = {     # ESTIMATE: added to the strength above (clamped to [floor, 1.0])
    "tolerant": -0.50,            # crops grown under standing water (paddy): excess rain is not a hazard until it floods the field
    "low": -0.25, "medium": 0.0, "high": +0.25,
}
EXCESS_STRENGTH_FLOOR = {"tolerant": 0.0, "low": 0.25, "medium": 0.25, "high": 0.25}

# ---- drainage inference (no direct drainage layer is sampled yet; slope + texture are proxies)
DRAINAGE_SLOPE_GOOD_PCT = 3.0     # ESTIMATE: >= this slope sheds water
DRAINAGE_SLOPE_POOR_PCT = 1.0     # ESTIMATE: below this, water stands (if soil is also fine-textured)
FREE_DRAINING_TEXTURES = (9, 11, 12)     # USDA: sandy loam, loamy sand, sand
POOR_DRAINING_TEXTURES = (1, 2, 5)       # USDA: clay, silty clay, silty clay loam

# ---- categories (applied to the CALIBRATED score)
CATEGORY_HIGH = 0.75
CATEGORY_MODERATE = 0.50
CATEGORY_LOW = 0.25              # below this (without a hard constraint) -> "very_low", never "unsuitable"

# ---- confidence = completeness/quality of evidence, a heuristic index, NOT a statistical interval
CONF_PENALTY_UNKNOWN_FACTOR = 0.15
CONF_PENALTY_INFERRED_DRAINAGE = 0.10
CONF_PENALTY_UNVERIFIED_CROP = 0.10
CONF_MIN = 0.20
