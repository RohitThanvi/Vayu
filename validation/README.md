# Vayu validation harness

Python 3.10+. `pip install -r requirements.txt`

0. **One command:** `python run_all.py --base-url https://YOUR-BACKEND.onrender.com` (self-test, then every validator over `cases.json`).
1. **Self-test first:** `python selftest.py` (checks the metrics against scikit-learn/scipy and runs the whole pipeline
   against a local mock server. Needs `pip install scikit-learn scipy` for the metric cross-check only.)
2. Copy `cases.example.json` to `cases.json`, put district/site polygons in `aoi/`, reference points in `ref_points/`.
3. `python run_validation.py --cases cases.json --base-url https://YOUR-BACKEND.onrender.com --dry-run`
   (runs ONE case, prints the raw outcome; fix path/field mismatches here)
4. `python run_validation.py --cases cases.json --base-url https://YOUR-BACKEND.onrender.com`

## Rules the harness enforces / you must keep
- `split`: tune cases may be used to adjust Vayu; **test cases are never to be looked at while tuning.** The default
  run uses `test` only. If you change model code after seeing test results, re-draw a fresh test set.
- Case AOI must be the same unit as the ground truth (district polygon vs district statistics).
- Results are cached per request in `validation_runs/_cache`; `--no-cache` forces fresh calls. Each run records the git
  hash (`--model-version` if the deployed commit differs from your local checkout) and a SHA-256 of the cases file.
- Throttled to `--delay` seconds per call (the RS endpoint is limited to 15 requests/minute; Render free tier is bandwidth-capped).

## Validator types
| type | endpoint(s) | metrics |
|---|---|---|
| crop_ranking | crop_suitability | top-1-in-truth, truth-top-1-in-model, top-k overlap, vs naive baseline, tie flag |
| scalar | any numeric output via `output_path` (terrain, LST, soil moisture, flood km2, snow %, NDVI mean, SAR, ...) | MAE, RMSE, bias, R2, Pearson, Spearman, slope/intercept, Lin CCC, skill vs baseline |
| class_areas | rs:lulc, rs:dynamic_world | MAE in percentage points, total variation distance, per-class bias |
| confusion | rs:accuracy_assessment (lulc, dynamic_world, burn_severity) | pooled confusion matrix, OA, kappa, balanced acc., macro-F1, producer's/user's accuracy; weighted kappa + within-one-class for burn severity |
| event | risk_score | ROC-AUC, POD, false-alarm rate, precision, F1 at threshold |
| phenology_dates | phenology | MAE / bias / RMSE in days, share within 15 days |
| categorical | irrigation_advisory (any label output) | confusion matrix + the classification metrics |

## Not covered (and why)
Intel feeds (vessels, aircraft, USGS, FIRMS, GDELT, AIS), mandi/CEDA prices, the LLM `/query` pipeline, WhatsApp,
groundwater-trend (needs well-level CGWB data in a form I haven't seen), crop-extent / supervised_classification
(the server only reports its own random-split accuracy, which is spatially autocorrelated and optimistic).
