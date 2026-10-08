# Factor-combination experiment (tune split only, 35 zones / 13 states)

Question: does a *learned monotone combination* of the six factor scores beat the current min (Liebig) rule?
Script: `validation/learn_combination.py --profiles <_profiles_v2>`. Leave-one-state-out CV; features are the engine's effective factor
scores only (no crop identity, no popularity); hard temperature limit kept outside the learned part. Test states NOT touched.

| variant | model | pooled AUC | within-crop AUC | top-3 overlap |
|---|---|---|---|---|
| irrigated | min (current) | 0.641 | 0.618 | 0.603 |
| irrigated | geometric mean (equal weights, nothing fitted) | 0.668 | 0.632 | 0.613 |
| irrigated | sqrt(min x geomean) | 0.652 | 0.629 | 0.636 |
| irrigated | learned, w>=0 (LOSO) | 0.672 | 0.554 | 0.613 |
| auto | min / geomean / hybrid / learned | 0.607 / 0.640 / 0.619 / 0.672 | 0.612 / 0.633 / 0.624 / 0.554 | 0.574 / 0.594 / 0.610 / 0.613 |
| rainfed | min / geomean / hybrid / learned | 0.521 / 0.565 / 0.534 / 0.672 | 0.560 / 0.585 / 0.569 / 0.554 | 0.490 / 0.535 / 0.551 / 0.613 |

(rainfed/auto "learned" equals irrigated because the fit gave the water factor weight 0.)

## Findings
1. **Learned weights rejected.** Weights: pH 1.0, texture 1.2, slope 0.35, temperature 0.3, water 0.0, organic carbon 0.0. Within-crop AUC
   falls to 0.554 (zone-bootstrap CI of the difference vs min entirely below 0 for irrigated/auto). The pooled gain comes from between-crop
   differences (crops tolerant of many soils get high scores), i.e. a popularity proxy, not site suitability. Water getting weight 0 is
   consistent with the earlier diagnosis (water AUC ~0.50, chickpea inverted) and points at the water scorer / labels, not at the combination.
2. **Equal-weight geometric mean** ranks better (+0.03 pooled AUC, 10/13 states, ties 98% -> 65%) but its CI touches 0 for irrigated, the within-crop
   gain is not significant, and it is *not a valid score*: one severe limit is averaged away (95% of crops score >= 0.5). It would remove the
   limiting-factor meaning of the score. Not adopted.
3. **sqrt(min x geomean)**: small but consistent AUC gain (+0.01, CI above 0), top-3 +0.03..+0.06, still needs recalibration. Not adopted without
   a fresh test split.
4. Harness note: tied scores are treated as ties by the metrics, although the API breaks ties by headroom, so tie-heavy min-rule rankings are
   under-credited in `overlap_top3`.

## Conclusion
The aggregation rule is not where the large gap is: all reasonable rules stay within ~0.03-0.04 AUC. Remaining levers are information (NBSS&LUP
soil-site limits, soil depth/drainage, humidity, elevation-aware temperature), ground truth (CRIDA DACPs), and the product claim (screen vs recommender).
No engine change was made. Do not quote these numbers externally.
