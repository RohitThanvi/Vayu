#!/usr/bin/env python3
"""
fit_calibration.py - fit the isotonic score calibrator from a DEVELOPMENT (tune-split) validation run.

  python fit_calibration.py validation_runs/<run_id>            # writes backend/app/services/agri/calibration.json

Label = "crop was an observed major crop (>=10% of modelled-crop area) in that district-season".
Refuses to fit on anything but a tune-split run: calibrating on the final test set would leak it.
Calibration is monotone, so a global map never changes crop ORDER; it changes what the numbers mean.
"""
import argparse, json, sys
from pathlib import Path
import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "backend"))
from app.services.agri.calibration import fit_isotonic          # noqa: E402


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("run_dir"); ap.add_argument("--min-pairs", type=int, default=300)
    ap.add_argument("--out", default=str(HERE.parent / "backend/app/services/agri/calibration.json")); a = ap.parse_args()
    run = Path(a.run_dir); man = json.loads((run / "manifest.json").read_text())
    if man.get("split") != "tune":
        sys.exit(f"REFUSED: run split is '{man.get('split')}'. Calibrate on a tune-split run only.")
    df = pd.read_csv(run / "results.csv"); df = df[(df.type == "crop_ranking") & (df.status == "ok")]
    xs, ys, by = [], [], {}
    for _, r in df.iterrows():
        for cid, score, share in json.loads(r["pairs"]):
            xs.append(float(score)); ys.append(1.0 if share >= 0.10 else 0.0); by.setdefault(r["season"], ([], []))[0].append(float(score)); by[r["season"]][1].append(ys[-1])
    if len(xs) < a.min_pairs:
        sys.exit(f"Only {len(xs)} (case, crop) pairs; need >= {a.min_pairs} for a stable fit.")
    maps = {"global": fit_isotonic(xs, ys)}
    for season, (sx, sy) in by.items():
        if len(sx) >= a.min_pairs: maps[season] = fit_isotonic(sx, sy)
    note = f"isotonic on tune run {man['run_id']} (model {man['model_version']}, {len(xs)} pairs)"
    Path(a.out).write_text(json.dumps({"maps": maps, "note": note}, indent=1)); print("wrote", a.out, "-", note)


if __name__ == "__main__":
    main()
