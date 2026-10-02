# Crop-suitability engine: how a score is produced

Modules live in `backend/app/services/agri/`. `crop_suitability.py` keeps the public functions (`sample_location_profile`,
`score_crops`, `add_profit_estimates`) and the API contract; the logic is layered:

| Layer | Module | What it does |
|---|---|---|
| Location / context | `crop_suitability.sample_location_profile`, request (`lat/lon` or AOI, `irrigation_available`, soil-test overrides) | Samples soil (OpenLandMap 250 m), slope (Copernicus DEM), 10-yr monthly rain (CHIRPS) and temperature (ERA5-Land). Season and irrigation regime come from the request / crop. |
| Environmental evidence | `evidence.py` | Annual, seasonal and monthly rainfall; **dry months**, **longest dry run**, **wet-season concentration**; temperature; soil; slope; **inferred drainage class** (slope + texture). Anything missing is listed in `missing`. Soil moisture and remote-sensing evidence are **not sampled yet** and are carried as explicit `None`. |
| Crop profile | `crop_requirements.py`, schema in `crop_profile.py` | EcoCrop-style trapezoids (temperature, rainfall, pH), texture classes, organic carbon, slope, plus optional `excess_sensitivity` and `rainfall_seasonality` (a window the crop needs dry; requires a cited source). `validate_crop()` is enforced in tests. |
| Factor scorers | `suitability_engine.py` | One 0-1 score per factor. **Water is three separate questions** (below). |
| Constraint engine | same | **Hard** = absolute temperature limit (only this can make a crop `unsuitable`). **Soft** = everything else: reduces the score, floored at `SOFT_FLOOR` (0.10). **Unknown** = factor omitted and confidence lowered; never counted against the crop. |
| Aggregation | same | Minimum over the soft-floored factors (FAO limiting-factor rule, a deliberate choice), 0 if a hard limit is violated -> `raw_score`. |
| Calibration | `calibration.py` | Isotonic map raw -> calibrated score. **Identity until fitted** (`calibration_status` says so). Fit only from a tune-split run: `validation/fit_calibration.py`. |
| Ranking | `suitability_engine.rank_crops` | Sorts on the calibrated score, then headroom (mean factor score), then name. Equal (score, headroom) share a `rank` and report `tied_with`. No crop-specific penalties anywhere. |
| Explanation | same | Built from the factors whose effective score is below 0.95. `limiting_factors.primary/secondary`, `evidence_summary` (✓ / ⚠ / ✗ / ?), and a `limiting_label` of *Rainfall deficit*, *Excess rainfall* or *Dry-season length*, or *No significant limitation*. The ambiguous label "Rainfall / water supply" is never emitted. |

## The water model

1. **Deficit.** Supply vs the crop's optimum minimum. Kharif uses in-season rain; rabi and perennials use annual rain as a proxy for stored
   moisture + irrigation recharge. With irrigation the shortfall is only partly penalised (down to 0.5); without it, in full.
2. **Excess.** Exposure vs the crop's ceiling: kharif and rabi use the rain in their own window (a Nov-Mar crop never sees the monsoon),
   perennials use annual rain. Rainfall amount is only a *proxy* for waterlogging risk, so the penalty is `1 - k * (1 - raw)` where
   `k` (0.25-1.0) comes from **drainage evidence** (good 0.5 / moderate or unknown 0.75 / poor 1.0) adjusted by the crop's `excess_sensitivity`
   (default high for drought-tolerant crops, low for paddy). A crop that declares a rain-sensitive window scales `k` by how wet that window is.
3. **Seasonality.** For crops that declare `rainfall_seasonality` (currently mango: Nov-Mar flowering), how dry the window is.

`water_score = min(deficit, excess, seasonality)`; `factors.water.driver` names which one set it.

## Response additions (all backward compatible; old fields unchanged)
`category` (high / moderate / low / very_low / unsuitable), `confidence` (completeness of evidence, **a heuristic, not a statistical interval**),
`raw_score`, `calibrated_score`, `rank`, `tied_with`, `constraints {hard, soft, unknown}`, `limiting_factors`, `evidence_summary`,
`components {temperature_score, rainfall_score, water_score, irrigation_score, season_score, soil_score, terrain_score, climate_score,
remote_sensing_score (null: not sampled), risk_penalty, raw_score, calibrated_score, final_rank}`, `ablation_inputs`; top-level `evidence`,
`calibration_status`. `rating` is kept (`very_low` is reported as `low` there); `limiting_factor` is now `none` when nothing reduced the score.

## What is deliberately NOT modelled yet
Soil moisture and remote-sensing evidence; a direct drainage layer (inferred from slope + texture); frost/heat-stress timing; crop duration and
sowing windows beyond the season window; per-crop rain-sensitive windows other than mango (needs sources). All `ESTIMATE` constants are in
`engine_config.py` and may be tuned on the development split only.

## Tests
`backend/tests/test_agri_engine.py` (sanity suite on approximate regional fixtures, paired irrigation tests, constraint / explanation invariants)
and `test_crop_suitability_calibration.py`. Fixtures are rounded climatologies for *qualitative* checks, not measurements.
