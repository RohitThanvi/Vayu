# Water factor diagnosis (tune split; replayed profiles `_profiles_v2`)

## 1. Why water has no / negative signal for two rabi crops
Per-crop AUC of the water factor (rainfed variant): rabi jowar 0.03, rabi chickpea 0.24, rice 0.71, wheat 0.59, mustard 0.58 (kharif crops ~0.5-0.6).
Zone-level inspection: rabi jowar is a major crop only in Karnataka (Gulbarga, Bidar) and Gujarat, the semi-arid Deccan / Gujarat; it is absent from
the wet zones (Assam, Bihar, Chhattisgarh, Uttarakhand, Himachal) where the water balance scores it 1.0. Chickpea shows the same pattern with
Chhattisgarh as an exception. The water arithmetic is behaving as designed; the labels reflect where rabi pulses/sorghum are *adopted*
(post-rainy crops on stored moisture in deep Vertisols; competition from rice and disease/humidity pressure in humid zones), which the engine does not
sample (soil depth / Vertisol class, humidity, disease, competing crops). This is missing information, not a scorer bug, and no numeric crop limit
could be sourced for it, so no crop range was changed.
Sources read: ICRISAT Information Bulletin 11 "An Approach to Improved Productivity on Deep Vertisols" (oar.icrisat.org/id/eprint/798); Agricultural Reviews
R-2150 (arccjournals.com; deep Vertisol, 185 cm, 230-300 mm water holding capacity; rabi cropping on residual moisture).

## 2. BUG FIXED: ROOT_ZONE_M overrides were silently ignored
`awc_mm(texture, depth_m=ROOT_ZONE_M)` bound the default at import time, so changing `water_balance.ROOT_ZONE_M` (experiment.py variants, config) had no effect.
Now resolved at call time (default behaviour unchanged, ROOT_ZONE_M still 1.0). Regression test: `test_root_zone_override_reaches_awc_at_call_time`.

## 3. Root-zone depth sensitivity (tune only; effect only on rabi/zaid, 3 of 13 states change)
| rainfed | base 1.0 m | 1.5 m | 1.85 m |
|---|---|---|---|
| pooled AUC | 0.521 | 0.572 | 0.580 |
| within-crop AUC | 0.560 | 0.613 | 0.645 |
| recall@0.5 | 0.557 | 0.670 | 0.670 |
Irrigated pooled AUC 0.641 / 0.650 / 0.657. At 1.5 m: rabi maize +0.18, mustard +0.15, wheat +0.13, jowar +0.03; chickpea slightly worse in auto/irrigated (-0.07 / -0.02); no state worse.
**Not adopted.** 1.85 m is a Vertisol soil depth, not an effective root depth. Sources found: FAO (y5749e table, minimum of maximum rooting depth) sorghum 1.0, wheat 1.0,
maize 0.9, pulses 0.6, groundnut 0.5, rice 0.5 m; Martin et al. 1990 (via LibreTexts irrigation text) maximum effective depth maize and sorghum 1.0-2.0 m, wheat 1.0-1.5 m.
Root depth is crop-specific: a uniform 1.5 m over-credits pulses (consistent with the chickpea result). The existing code comment attributing "0.5-1.5 m" to FAO-56
was not confirmed (FAO-56 Table 22 itself was not retrieved); re-check it.
**Recommended next step:** per-crop root depth in the crop profile (bucket recomputed per crop), values inside the sourced ranges, labelled ESTIMATE, tested on tune. Expected to help cereals and mustard without helping pulses by construction.
