# Crop-range review: what was checked, what changed, what could not be settled

Rule: a crop range changes **only** when a citable agronomic source contradicts the generic EcoCrop value. Each change is recorded next to the crop in
`crop_requirements.py` (`*_override_note`) and can be reverted independently. Sources below are the ones found in this review; several are secondary
summaries (university packages, review papers) and should be checked against the primary ICAR document before being quoted in a paper.

| Crop / factor | Decision | Basis | Confidence in the source |
|---|---|---|---|
| Wheat, soil pH | optimum 6.0-7.0 -> **6.0-8.2**, absolute max 8.5 -> **9.3** | ICAR-CSSRI sodicity threshold (pHs 8.2); ICAR-CSSRI Farmer FIRST sodic-basin trials, *Sustainability* 2021 13(6):3378 (wheat grown at pH 8.25 to >9.25 with yield decline); ICAR-NBPGR wheat panel study (normal fertile soil pH ~7.84). The 9.3 limit is an ESTIMATE. | good for 8.2; medium for 9.3 |
| Groundnut, soil pH | optimum upper 6.5 -> **7.5** | Assam Agricultural University package of practices (desirable 5.5-7.5) | medium (one state-university source) |
| Rice, slope | cliff at 3-6% -> **soft terracing requirement to 30%** (score 0.5 at the limit, ESTIMATE) | FAO bench-terrace guidance (level terraces for rice on 12-47% slopes); WOCAT paddy terraces on 30-60% slopes; IPB Sukabumi GIS study (yield 5.16 -> 2.70 t/ha from 0-3% to 30-45%; >30% unsuitable) | good for "terraced paddy exists"; the 0.5 score is not measured |
| Rice, excess rain | **tolerant** class (standing-water crop) | Konkan / Kerala coasts grow paddy above 3000 mm/yr | agronomic common knowledge; no single citation recorded |
| Mango, rainfall | dry-window requirement (Nov-Mar) | see `rainfall_seasonality` in the mango entry (secondary sources) | medium |
| Maize, pH | **unchanged** (optimum 6-7, limit 8.5) | KAU gives 5.5-8.0 with optimum 6-7, which the existing trapezoid already represents | - |
| Soybean, pH | **unchanged** | ICAR-linked guidance gives optimum 6.0-7.5 and advises gypsum above 7.5; no source supports widening | - |
| Chickpea, temperature | **unchanged** | van der Maessen (1972): optimum 18-26 / 21-29 C, consistent with EcoCrop 15-29 | good |
| Chickpea, humidity | **unscored caveat** at >= 1500 mm/yr | ECHO + ICRISAT guidance (poor in warm humid conditions; sow in cool post-rainy season). van der Maessen found little RH effect on fruit-set, so the issue is disease, which is not measured here | medium |

## Not solvable with the evidence and tools available (so deliberately NOT changed)
1. **Rabi / zaid water supply - now modelled, but only roughly.** A monthly soil-water balance (see `AGRI_SUITABILITY_ENGINE.md`) replaces the
   annual-rainfall proxy. Still missing and flagged: measured soil depth (so shallow laterite soils are over-credited), crop-specific Kc / cycle length
   (one cycle-mean Kc of 0.75 is used), and a PET method better than Thornthwaite for hot-dry climates (ERA5-Land PET would need new, untested Earth Engine sampling).
2. **Humidity / disease pressure as a scored factor.** There is no sampled humidity layer in the pipeline and no crop-specific numeric limits were found.
   It is surfaced as a caveat with lower confidence instead of a made-up penalty.
3. **Summer (zaid) ground truth.** The statistics dataset's summer rows are too sparse for a validated ranking.
4. **Gram on humid coasts still scores high** (the caveat is shown). Fixing it needs (2).
5. **Crop pH / temperature ranges for onion, potato, mustard, barley, cotton** were not reviewed for lack of a source found in this pass.

## Slope and irrigation decisions made on the development split (2026-10)
Evidence: 78 validation AOIs sampled with the corrected DEM; tune split only (153 ranking cases per irrigation variant).
| Decision | Tried | Result on tune | Rationale |
|---|---|---|---|
| Slope minimum score (SLOPE_MIN_SCORE) 0.10 -> **0.5** | 0.5, and 1.0 (slope off) | slope off: AUC 0.639 -> 0.622 (slope carries signal). 0.5: AUC unchanged, recall 0.83 -> 0.86, hill-state recall 0.48 -> 0.61, false-positive rate 0.58 -> 0.64 | Farmers cultivate steep land by terracing (labels show Sikkim maize and Himalayan rice on cropland slopes up to ~58%); steepness is a cost / erosion factor, not an impossibility. ESTIMATE; only two values were tried. |
| Cropland fraction needed to use the cropland slope 1% -> **0.05%** | 0.05% | metric-neutral (AUC unchanged; top-3 overlap 0.614 -> 0.601, within noise) | The 1% threshold sent Uttarkashi (0.9% cropland) and Sikkim (0.1%) back to the mountain-wide mean slope (30 deg) although they hold hundreds of hectares of cropland (cropland slope 8-12 deg). Chosen for correctness, not for the metric. |
| UI irrigation: checkbox -> **Auto / Yes / No**, Auto default | irrigated, rainfed, auto variants | pooled AUC irrigated 0.64, **auto 0.61**, rainfed 0.53 | Auto (per-location irrigated-cropland share, GFSAD1000) beats the old unchecked-rainfed default. It does not beat assuming irrigation everywhere, so it is evidence, not proof that GFSAD1000 irrigation context is accurate. |
Confirm all three on untouched test states before quoting them.

### Water factor: documented, not changed
Within-crop AUC averages ~0.50 and is below 0.5 for chickpea (0.24) and maize (0.41): chickpea is major in water-short Gujarat / Karnataka and absent from wet Assam / Bihar,
so "more water = better" is wrong for a dry-season pulse that grows on residual moisture. That needs a humidity / disease-pressure term with sourced limits, not a re-tuned range.
