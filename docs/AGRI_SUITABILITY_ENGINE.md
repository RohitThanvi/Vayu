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

## Seasonal suitability (what "grows well in summer, not in winter" means here)
A crop is scored **in each season it is actually grown**, and the best season is highlighted. Windows (DES convention): **Kharif Jun-Oct**,
**Rabi Nov-Mar**, **Zaid / summer Mar-Jun**; perennials use the whole year.

1. **Eligibility = observed practice.** `crop_seasons.py` is generated by `validation/derive_crop_seasons.py` from Directorate of Economics &
   Statistics area-by-season (2005-2014): a season is eligible if it holds >= 5% of the crop's assigned area (Autumn and Winter, state-specific names
   for monsoon-sown rice, count as kharif; Summer = zaid; "Whole Year" is unassigned). Example: jowar is 39% kharif / 61% rabi, so it is evaluated in both;
   wheat only in rabi. Each crop's shares are returned in `season_evidence`.
2. **Suitability = physical evidence in that window.** Temperature is the window mean (so a heat-loving crop can be fine in the Kharif/Zaid window and
   unsuitable in the Rabi window, and vice versa); water uses the season's own logic (kharif: in-season rain; rabi: annual-rain proxy for stored moisture;
   zaid: in-window rain, i.e. irrigation-dependent).
3. **Output.** `best_season`, `seasonal{season: score, category, window temperature / rain, limiting message, hard_limit, observed_area_share}`,
   `season_highlight` (plain-language summary generated from those numbers), `seasons_not_grown`, `season_evidence`. `season` in the request
   (`best` | `kharif` | `rabi` | `zaid`) ranks only crops grown in that season; the rest come back in `not_grown_in_season`.

**Known limits (deliberate, not hidden):** sowing dates, variety duration, photoperiod and vernalisation are not modelled (eligibility from practice stands in
for them); the rabi/zaid water supply uses annual / in-window rainfall as a proxy for stored moisture and irrigation, which over-credits sites with shallow
or fast-draining soils (a soil water balance - available water capacity + reference ET - is the proper replacement and needs new Earth Engine sampling);
no humidity / disease-pressure evidence; crop pH ranges are generic EcoCrop values that under-rate alkaline-tolerant crops on Indo-Gangetic soils.

## Dry-window water balance (rabi and zaid)  - replaces the annual-rainfall proxy
A Nov-Mar or Mar-May crop does not live on annual rainfall; a rainfed one lives on the moisture the soil **stored after the monsoon** plus the little rain
that falls in its window. `water_balance.py` estimates that and compares it with demand:

    supply = in-window rain + soil water stored at window start        demand = Kc x PET over the window        MAI = supply / demand

* **PET**: Thornthwaite (1948) from monthly mean temperature, Willmott et al. (1985) extension above 26.5 C, day-length correction from latitude
  (taken from the AOI / point). It under-estimates in hot-dry climates, so MAI is a *relative* index, not an irrigation-scheduling figure.
* **Storage**: plant-available water capacity by USDA texture (UC Cooperative Extension ANR 80243 ranges, midpoints; loamy sand from Alberta Agriculture)
  x a 1.0 m root zone. A monthly bucket (two passes of the climatology) gives the soil water at the end of every month; surplus is lost.
* **Demand**: one cycle-mean crop coefficient, 0.75, from the FAO-56 Table 12 wheat shape (Kc 0.3 -> 1.15 -> 0.25 over 30/30/40/30 days). A crop-specific
  Kc table is the obvious next refinement.
* **Score**: rainfed score = (MAI - 0.30) / (0.80 - 0.30) clipped to 0-1 (ESTIMATE thresholds). With irrigation the shortfall is only partly penalised
  (as before). For rabi the EcoCrop annual-rainfall envelope still applies and the **lower** of the two counts, so a crop's own water-intensity is kept
  (rice needs more than bajra) while *timing* now matters. Zaid is the Mar-May window (a ~100-day crop sown in March is harvested by early June; June rain belongs to kharif).
* **Honest limits** (reported in every affected result as a `soil_depth` caveat and a 0.10 confidence penalty): soil **depth** is not measured (OpenLandMap is 0-30 cm),
  so on shallow soils such as laterite the stored water is over-estimated and rainfed dry-season scores are optimistic; one Kc for all crops; Thornthwaite PET.
  The result: a high-rainfall coast (Ratnagiri) no longer rates rainfed jowar / maize / gram HIGH in rabi; the Indo-Gangetic plain, where post-monsoon storage
  plus winter rain cover demand, keeps its rabi ratings. These are behaviours on fixtures and on one live reading, not validated accuracy.
