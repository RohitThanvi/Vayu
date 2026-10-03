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
1. **Rabi / zaid water supply.** Annual rainfall stands in for stored soil moisture. The proper replacement is a soil water balance (available water
   capacity + reference evapotranspiration + crop coefficients and cycle length). It needs new Earth Engine sampling that cannot be tested here and
   per-crop ET parameters that this review did not find sources for; substituting numbers would be invention. The EcoCrop rainfall ranges are not
   comparable with a "rain + stored water" figure, so a swap would also undo the Bharatpur fix that was validated against ground truth.
2. **Humidity / disease pressure as a scored factor.** There is no sampled humidity layer in the pipeline and no crop-specific numeric limits were found.
   It is surfaced as a caveat with lower confidence instead of a made-up penalty.
3. **Summer (zaid) ground truth.** The statistics dataset's summer rows are too sparse for a validated ranking.
4. **Gram on humid coasts still scores high** (the caveat is shown). Fixing it needs (2).
5. **Crop pH / temperature ranges for onion, potato, mustard, barley, cotton** were not reviewed for lack of a source found in this pass.
