# Agricultural engine regression report
_generated 2026-10-03 by backend/tests/make_regression_report.py - offline, on APPROXIMATE regional fixtures; this is a behavioural check, NOT accuracy evidence._

## Test results

| suite | test | result |
|---|---|---|
| test_agri_engine | test_best_season_is_the_highest_scoring_eligible_season_and_seasonal_block_is_consistent | PASS  |
| test_agri_engine | test_calibration_is_identity_until_fitted_and_monotone_when_fitted | PASS  |
| test_agri_engine | test_caveat_does_not_change_the_score | PASS  |
| test_agri_engine | test_compatible_pairs_not_rejected_by_water_logic | PASS  |
| test_agri_engine | test_dry_window_penalises_year_round_wet_sites_by_the_same_rule | PASS  |
| test_agri_engine | test_every_crop_profile_is_schema_valid | PASS  |
| test_agri_engine | test_every_seasonal_crop_has_observed_seasons_and_a_primary | PASS  |
| test_agri_engine | test_explanation_is_generated_from_factors_that_reduced_the_score | PASS  |
| test_agri_engine | test_generic_mechanism_mango_not_zeroed_even_without_dry_window_attribute | PASS  |
| test_agri_engine | test_groundnut_ph_optimum_extends_to_7_5_only | PASS  |
| test_agri_engine | test_humid_site_adds_unscored_disease_caveat_and_lowers_confidence_for_chickpea_only | PASS  |
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
| test_agri_engine | test_rice_slope_is_a_soft_terracing_requirement_not_a_cliff | PASS  |
| test_agri_engine | test_season_filter_ranks_only_crops_grown_in_that_season_and_lists_the_rest | PASS  |
| test_agri_engine | test_summer_vs_winter_is_reported_when_a_crop_is_grown_in_both | PASS  |
| test_agri_engine | test_unknown_evidence_is_not_unsuitable_and_lowers_confidence | PASS  |
| test_agri_engine | test_unreviewed_crops_keep_their_original_ph_ranges | PASS  |
| test_agri_engine | test_water_message_distinguishes_excess_from_deficit | PASS  |
| test_agri_engine | test_wheat_ph_range_accepts_normal_indo_gangetic_soils_but_still_penalises_sodic | PASS  |
| test_agri_engine | test_zaid_depends_on_irrigation_but_kharif_in_humid_site_does_not | PASS  |
| test_crop_suitability_calibration | test_jowar_not_zeroed_at_monsoon_rainfall | PASS  |
| test_crop_suitability_calibration | test_kharif_and_perennial_water_logic_unchanged | PASS  |
| test_crop_suitability_calibration | test_output_schema_unchanged | PASS  |
| test_crop_suitability_calibration | test_rabi_deficit_side_still_uses_annual_rain | PASS  |
| test_crop_suitability_calibration | test_rabi_excess_still_fires_on_genuinely_wet_winter | PASS  |
| test_crop_suitability_calibration | test_tie_break_is_not_alphabetical | PASS  |
| test_crop_suitability_calibration | test_wheat_not_penalised_for_monsoon_rain | PASS  |
| test_water_balance | test_awc_table_covers_every_texture_class_and_is_physically_ordered | PASS  |
| test_water_balance | test_bucket_never_exceeds_capacity_and_more_rain_never_stores_less | PASS  |
| test_water_balance | test_irrigation_lifts_dry_season_scores_at_a_wet_coast | PASS  |
| test_water_balance | test_missing_texture_falls_back_to_the_envelope_and_never_scores_lower | PASS  |
| test_water_balance | test_rainfed_dry_season_crops_need_irrigation_at_a_wet_coast_but_not_at_a_gangetic_site | PASS  |
| test_water_balance | test_rice_leads_the_live_ratnagiri_profile_when_rainfed_and_no_dry_season_crop_is_high | PASS  |
| test_water_balance | test_semi_arid_rainfed_mustard_is_stressed_but_not_zeroed | PASS  |
| test_water_balance | test_soil_depth_caveat_is_shown_for_dry_window_estimates_and_lowers_confidence | PASS  |
| test_water_balance | test_summer_water_score_depends_on_irrigation_everywhere | PASS  |
| test_water_balance | test_thornthwaite_is_in_a_sane_range_and_monotone_in_temperature | PASS  |
| test_water_balance | test_window_wraps_the_year_end_and_uses_the_month_before_the_window | PASS  |

47/47 passed

## Ratnagiri (live UI values, rainfed): all crops, ranked

| rank | crop | category | score | confidence | primary limitation |
|---|---|---|---|---|---|
| 1 | Rice (paddy) | high | 0.90 | 0.90 | Excess rainfall: 2834.0 mm vs ceiling 2000.0-4000.0 mm. Drainage looks moderate (neither clearly free-draining nor clearly waterlogged), so this is a soft limit (strength 0.25), not a hard cut-off. |
| 2 | Sugarcane | high | 0.77 | 0.90 | Excess rainfall: 2930.0 mm vs ceiling 2000.0-5000.0 mm. Drainage looks moderate (neither clearly free-draining nor clearly waterlogged), so this is a soft limit (strength 0.75), not a hard cut-off. |
| 3 | Guava | moderate | 0.60 | 1.00 | Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, .... |
| 3 | Mango | moderate | 0.60 | 0.90 | Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, .... |
| 5 | Amla (gooseberry) | moderate | 0.60 | 0.90 | Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, .... |
| 6 | Groundnut | moderate | 0.60 | 0.90 | Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, .... |
| 7 | Lemon | low | 0.40 | 0.90 | Soil pH 5.9 vs needed 6.5-7.0. |
| 8 | Jowar (sorghum) | low | 0.31 | 0.90 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 8 | Maize | low | 0.31 | 0.90 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 10 | Gram (chickpea) | low | 0.31 | 0.75 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 11 | Mustard | low | 0.31 | 0.90 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 12 | Potato | low | 0.31 | 0.90 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 13 | Onion | low | 0.31 | 0.90 | Water balance: 34 mm rain + ~177 mm stored soil water = ~210 mm vs ~460 mm crop demand (PET x Kc), moisture index 0.46; no irrigation, so a rainfed crop would be water-stressed. |
| 14 | Cotton | low | 0.25 | 0.90 | Excess rainfall: 2834.0 mm vs ceiling 1200.0-1500.0 mm. Drainage looks moderate (neither clearly free-draining nor clearly waterlogged), so this is a soft limit (strength 0.75), not a hard cut-off. |
| 15 | Soybean | low | 0.25 | 0.90 | Excess rainfall: 2834.0 mm vs ceiling 1500.0-1800.0 mm. Drainage looks moderate (neither clearly free-draining nor clearly waterlogged), so this is a soft limit (strength 0.75), not a hard cut-off. |
| 16 | Avocado | low | 0.25 | 0.90 | Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, .... |
| 17 | Bajra (pearl millet) | very_low | 0.10 | 0.90 | Excess rainfall: 2834.0 mm vs ceiling 900.0-1700.0 mm. Drainage looks moderate (neither clearly free-draining nor clearly waterlogged), so this is a soft limit (strength 1.00), not a hard cut-off. |
| 18 | Wheat | very_low | 0.10 | 0.90 | Temperature 26.6 °C (Rabi window mean) is above the optimum 15.0-23.0 °C. |
| 19 | Barley | very_low | 0.10 | 0.90 | Soil pH 5.9 vs needed 6.5-7.5. |

### Ratnagiri -> Mango, evidence summary

- ⚠ Soil texture Clay loam vs needed Sandy clay loam, Loam, Silt loam, ....
- ✓ Temperature compatible (27.5 °C; needs 24.0-30.0 °C)
- ✓ Water conditions compatible (water supply 2930.0 mm vs ~600.0 mm needed; rain above the generic 1500.0 mm ceiling is tolerated because the crop's rain-sensitive months are dry (34.0 mm))
- ✓ Soil pH compatible (5.9; needs 5.5-7.5)
- ✓ Organic carbon compatible (13.8 g/kg; needs >= 8.0 g/kg)
- ✓ Slope compatible (2 %; needs <= 20%)

drainage inferred: `{'class': 'moderate', 'basis': 'neither clearly free-draining nor clearly waterlogged', 'inferred': True}`; dry months: 6; calibration: identity (not fitted)
