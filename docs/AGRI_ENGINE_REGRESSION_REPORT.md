# Agricultural engine regression report
_generated 2026-10-02 by backend/tests/make_regression_report.py - offline, on APPROXIMATE regional fixtures; this is a behavioural check, NOT accuracy evidence._

## Test results

| suite | test | result |
|---|---|---|
| test_agri_engine | test_best_season_is_the_highest_scoring_eligible_season_and_seasonal_block_is_consistent | PASS  |
| test_agri_engine | test_calibration_is_identity_until_fitted_and_monotone_when_fitted | PASS  |
| test_agri_engine | test_compatible_pairs_not_rejected_by_water_logic | PASS  |
| test_agri_engine | test_dry_window_penalises_year_round_wet_sites_by_the_same_rule | PASS  |
| test_agri_engine | test_every_crop_profile_is_schema_valid | PASS  |
| test_agri_engine | test_every_seasonal_crop_has_observed_seasons_and_a_primary | PASS  |
| test_agri_engine | test_explanation_is_generated_from_factors_that_reduced_the_score | PASS  |
| test_agri_engine | test_generic_mechanism_mango_not_zeroed_even_without_dry_window_attribute | PASS  |
| test_agri_engine | test_invalid_season_is_rejected | PASS  |
| test_agri_engine | test_irrigation_barely_matters_for_drought_adapted_crops_and_for_humid_sites | PASS  |
| test_agri_engine | test_irrigation_helps_water_sensitive_crops_in_dry_site | PASS  |
| test_agri_engine | test_irrigation_never_lowers_a_score | PASS  |
| test_agri_engine | test_only_a_hard_constraint_can_make_a_crop_unsuitable | PASS  |
| test_agri_engine | test_paddy_is_not_penalised_for_monsoon_rain_at_a_high_rainfall_coast | PASS  |
| test_agri_engine | test_ranking_is_deterministic_and_tie_aware | PASS  |
| test_agri_engine | test_ranking_uses_calibrated_scores | PASS  |
| test_agri_engine | test_ratnagiri_mango_not_unsuitable_and_not_water_limited | PASS  |
| test_agri_engine | test_response_exposes_the_requested_components | PASS  |
| test_agri_engine | test_season_filter_ranks_only_crops_grown_in_that_season_and_lists_the_rest | PASS  |
| test_agri_engine | test_summer_vs_winter_is_reported_when_a_crop_is_grown_in_both | PASS  |
| test_agri_engine | test_unknown_evidence_is_not_unsuitable_and_lowers_confidence | PASS  |
| test_agri_engine | test_water_message_distinguishes_excess_from_deficit | PASS  |
| test_agri_engine | test_zaid_depends_on_irrigation_but_kharif_in_humid_site_does_not | PASS  |
| test_crop_suitability_calibration | test_jowar_not_zeroed_at_monsoon_rainfall | PASS  |
| test_crop_suitability_calibration | test_kharif_and_perennial_water_logic_unchanged | PASS  |
| test_crop_suitability_calibration | test_output_schema_unchanged | PASS  |
| test_crop_suitability_calibration | test_rabi_deficit_side_still_uses_annual_rain | PASS  |
| test_crop_suitability_calibration | test_rabi_excess_still_fires_on_genuinely_wet_winter | PASS  |
| test_crop_suitability_calibration | test_tie_break_is_not_alphabetical | PASS  |
| test_crop_suitability_calibration | test_wheat_not_penalised_for_monsoon_rain | PASS  |

30/30 passed

## Ratnagiri-like fixture (rainfed): all crops, ranked

| rank | crop | category | score | confidence | primary limitation |
|---|---|---|---|---|---|
| 1 | Groundnut | high | 1.00 | 1.00 | none |
| 1 | Jowar (sorghum) | high | 1.00 | 1.00 | none |
| 1 | Maize | high | 1.00 | 1.00 | none |
| 1 | Mango | high | 1.00 | 0.90 | none |
| 1 | Mustard | high | 1.00 | 1.00 | none |
| 6 | Guava | high | 0.96 | 0.90 | none |
| 7 | Gram (chickpea) | high | 0.85 | 1.00 | Soil pH 5.8 vs needed 6.0-8.5. |
| 8 | Amla (gooseberry) | moderate | 0.72 | 0.90 | Excess rainfall: 3143.0 mm vs ceiling 2500.0-4200.0 mm. Drainage looks good (slope 6.0% sheds water), so this is a soft limit (strength 0.75), not a hard cut-off. |
| 9 | Potato | moderate | 0.68 | 1.00 | Temperature 26.6 °C (Rabi window mean) is above the optimum 15.0-25.0 °C. |
| 10 | Onion | moderate | 0.68 | 1.00 | Temperature 26.6 °C (Rabi window mean) is above the optimum 12.0-25.0 °C. |
| 11 | Avocado | moderate | 0.50 | 0.90 | Excess rainfall: 3143.0 mm vs ceiling 2000.0-2500.0 mm. Drainage looks good (slope 6.0% sheds water), so this is a soft limit (strength 0.50), not a hard cut-off. |
| 11 | Soybean | moderate | 0.50 | 0.90 | Excess rainfall: 3040.0 mm vs ceiling 1500.0-1800.0 mm. Drainage looks good (slope 6.0% sheds water), so this is a soft limit (strength 0.50), not a hard cut-off. |
| 13 | Sugarcane | moderate | 0.50 | 0.90 | Slope 6 % vs needed <= 4%. |
| 14 | Cotton | moderate | 0.50 | 0.90 | Excess rainfall: 3040.0 mm vs ceiling 1200.0-1500.0 mm. Drainage looks good (slope 6.0% sheds water), so this is a soft limit (strength 0.50), not a hard cut-off. |
| 15 | Lemon | low | 0.30 | 0.90 | Soil pH 5.8 vs needed 6.5-7.0. |
| 16 | Bajra (pearl millet) | low | 0.25 | 0.90 | Excess rainfall: 3040.0 mm vs ceiling 900.0-1700.0 mm. Drainage looks good (slope 6.0% sheds water), so this is a soft limit (strength 0.75), not a hard cut-off. |
| 17 | Rice (paddy) | very_low | 0.10 | 0.90 | Slope 6 % vs needed <= 3%. |
| 18 | Barley | very_low | 0.10 | 1.00 | Soil pH 5.8 vs needed 6.5-7.5. |
| 19 | Wheat | very_low | 0.10 | 1.00 | Temperature 26.6 °C (Rabi window mean) is above the optimum 15.0-23.0 °C. |

### Ratnagiri -> Mango, evidence summary

- ✓ Temperature compatible (27.5 °C; needs 24.0-30.0 °C)
- ✓ Water conditions compatible (water supply 3143.0 mm vs ~600.0 mm needed; rain above the generic 1500.0 mm ceiling is tolerated because the crop's rain-sensitive months are dry (36.0 mm))
- ✓ Soil pH compatible (5.8; needs 5.5-7.5)
- ✓ Soil texture compatible (Sandy clay loam; needs Sandy clay loam, Loam, Silt loam, ...)
- ✓ Organic carbon compatible (12 g/kg; needs >= 8.0 g/kg)
- ✓ Slope compatible (6 %; needs <= 20%)

drainage inferred: `{'class': 'good', 'basis': 'slope 6.0% sheds water', 'inferred': True}`; dry months: 6; calibration: identity (not fitted)
