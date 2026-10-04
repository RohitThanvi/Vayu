# Vayu validation harness

Python 3.10+. `pip install -r requirements.txt`

0. **Ground truth is built for you** (`build_ground_truth.py`: real district boundaries + district crop statistics, ~60 districts / 22 states,
   whole-state train/test holdout). `cases.json` and `aoi/` are already generated; re-generate or widen with
   `python build_ground_truth.py --max-districts 120 --states "Rajasthan,Haryana"`. See `ground_truth/SOURCES.md` for sources and limitations.
   Other endpoints (terrain, LST, flood, risk score, phenology...) still need reference data you supply - see `cases.example.json`.

0b. **One command:** `python run_all.py --base-url https://YOUR-BACKEND.onrender.com` (self-test, then every validator over `cases.json`).
1. **Self-test first:** `python selftest.py` (checks the metrics against scikit-learn/scipy and runs the whole pipeline
   against a local mock server. Metric cross-check needs `python -m pip install -r requirements-dev.txt`.)
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

## Before / after a model change (the protocol)
1. Commit the baseline code, run `python run_validation.py --cases cases.json --base-url ... --split tune` -> keep `report.md`.
2. Change the model (cite sources for every range change). Commit it. Re-run the SAME tune command. Compare.
   Responses are cached per commit hash, so old answers are never reused; an uncommitted tree disables the cache.
3. Only after the change is final, run on a test set the model has never been tuned or diagnosed on. If the original
   test states were already looked at, draw fresh ones:
   `python build_ground_truth.py --exclude-states "haryana,madhyapradesh,maharashtra,mizoram,nagaland,rajasthan,uttarpradesh,westbengal" --max-districts 120`
4. `crop_ranking` now also reports suitability as classification (binary confusion matrix, recall/precision/AUC at score>=0.5
   vs observed major crops >=10% of area) and a top-1 confusion matrix per season (`confusion_*.csv`).

## What `crop_ranking` reports now
- **Tie-aware ranking**: top-1, top-3 recall, top-k overlap, NDCG@k, MRR, Kendall tau-b, Spearman (equal scores share positions; metrics are
  expectations under random tie-breaking) for Vayu AND three explicitly defined baselines on exactly the same cases
  (`state_prior`, `random`, `climate_only`), plus paired overlap gain vs each, with zone-bootstrap CIs.
- **Suitability as classification**: binary confusion matrix, precision / recall / specificity / FPR / F1, ROC-AUC, **PR-AUC**, recall by crop.
- **Calibration**: Brier + reliability curve + ECE, evaluated ONLY when scores are calibrated (otherwise it says why not).
- **Support**: cases, zones, crops, and cases per season / variant / zone; robustness by season and zone with CIs.
- **Ablation** (offline from the same responses): climate only -> + soil/terrain -> + water model -> + irrigation -> (remote sensing: not available) -> full.
- **`presence` cases** (`mango`, `sugarcane`): where an established major perennial crop exists, the engine must not reject it.
- `fit_calibration.py <tune run dir>` fits the calibrator (tune split only; refuses anything else).

## Always pull before a run, and check the backend version
The harness and the backend must be the same generation. A run made with an OLD harness against a NEW backend (no `season` sent) silently drops
every crop whose best season differs from the case's season (3-10 crops per case instead of 7-13) and its metrics are not valid. The manifest and
report now record `backend_engine_version` (from the response; Render supplies `RENDER_GIT_COMMIT`), a season-aware backend that does not echo the
requested season aborts the case, and a mid-run deploy prints a warning. Check `n_crops_ranked` in `results.csv` if numbers look odd.

## Offline replay and diagnosis (iterate on the model without calling the backend)
Every live run now saves each location's sampled evidence (monthly rain / temperature, soil, slope, latitude) to `validation_runs/_profiles/`
(needs a backend that returns the monthly arrays - the current one does).
* `python run_validation.py --cases cases.json --replay validation_runs/_profiles --split tune` re-scores those saved profiles with THIS checkout's
  engine: no network, no Earth Engine, seconds. It tests scoring logic only (the sampling is whatever was saved). The manifest says `replay@<commit>`.
* `python diagnose.py validation_runs/<run_id>` writes `diagnosis.md`: AUC of each factor against "observed major crop", per-crop over / under-rating,
  the limiting factor behind every widely grown crop rated < 0.5, false positives by crop, and results by season and state.
Use these on the **tune** split. Look at the test split once, at the end, on states the model has never been tuned or diagnosed on.

## Ground-truth correction (read this if you ran an earlier `cases.json`)
An earlier `build_ground_truth.py` kept only DES rows labelled Kharif or Rabi. The dataset also uses Autumn, Winter and Summer, so it dropped 42% of national
rice area and ALL rice in Bihar, West Bengal, Assam, Jharkhand, Kerala, Odisha and Meghalaya: districts where rice dominates were labelled "rice not major".
`season_map.py` is now the single mapping (Kharif/Autumn/Winter -> kharif, Rabi -> rabi, Summer -> zaid) used by both the builder and `derive_crop_seasons.py`.
Results from runs made with the old `cases.json` (sha 3d8427d5...) are not comparable with the corrected ones; saved profiles remain valid (same AOIs).

## Live diagnostics
`diagnostics/check_dem_slope.py` compares four ways of computing mean slope for an AOI (see its docstring). Profiles from the validation runs show implausibly
low slopes everywhere (max 2.3% even in the Himalaya), so the slope factor currently contributes nothing.

## Slope bug (found with the validation profiles, confirmed live) and the resample workflow
Profiles showed mean slopes <= 2.3% everywhere, even in the Himalaya. `diagnostics/check_dem_slope.py` confirmed the cause: `ee.ImageCollection(...).mosaic()` drops
the DEM's projection, so `ee.Terrain.slope` returned ~1 deg for Uttarkashi where the same DEM with its projection restored (and independent SRTM) gives ~30 deg.
Fixed through `gee_client.copernicus_dem()` in the Crops sampler and the Spectra terrain tool. The Crops slope is now the mean over ESA WorldCover cropland
pixels (falls back to all land if the AOI has <1% cropland or the cropland query fails); `slope_all_deg`, `slope_cropland_deg`, `cropland_fraction` and
`slope_basis` are returned. Mountain AOIs (mean slope >= 10 deg) get an unscored caveat and lower confidence.
To measure the effect: `diagnostics/resample_profiles.py` (live, writes `_profiles_v2`), then `run_validation.py --replay validation_runs/_profiles_v2 --split tune`
and compare with the same replay on the old `_profiles`.

## Comparing engine variants offline (tune split only)
`python experiment.py --profiles validation_runs/_profiles_v2 --split tune --variant slope50:engine_config.SLOPE_MIN_SCORE=0.5 --variant slope100:engine_config.SLOPE_MIN_SCORE=1.0`
re-scores saved profiles under named constant overrides (`engine_config.*` or `water_balance.*`) and prints pooled / within-crop AUC, recall, false-positive rate,
recall in hill vs plain states and top-3 overlap side by side. Keep the variant list short: many variants on one split overfit it. Choose a variant only with an
agronomic rationale, then confirm once on untouched test states.
