"""Regenerates docs/AGRI_ENGINE_REGRESSION_REPORT.md: test results + the Ratnagiri inspection. Offline, fixtures only."""
import copy, io, contextlib, sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).parent))
import test_agri_engine as T, test_crop_suitability_calibration as T0
from agri_fixtures import LOCATIONS
from app.services.agri.crop_suitability import score_crops

lines = [f"# Agricultural engine regression report", f"_generated {datetime.date.today()} by backend/tests/make_regression_report.py - offline, on APPROXIMATE regional fixtures; this is a behavioural check, NOT accuracy evidence._", ""]
res = []
for mod in (T, T0):
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        try: getattr(mod, name)(); res.append((mod.__name__, name, "PASS", ""))
        except AssertionError as e: res.append((mod.__name__, name, "FAIL", str(e)[:120]))
lines += ["## Test results", "", "| suite | test | result |", "|---|---|---|"] + [f"| {m} | {n} | {r} {d} |" for m, n, r, d in res]
lines += ["", f"{sum(r == 'PASS' for *_, r, _ in res)}/{len(res)} passed", "", "## Ratnagiri-like fixture (rainfed): all crops, ranked", "",
          "| rank | crop | category | score | confidence | primary limitation |", "|---|---|---|---|---|---|"]
out = score_crops(copy.deepcopy(LOCATIONS["ratnagiri"]), False)
for r in out["crops"]:
    p = r["limiting_factors"]["primary"]
    lines.append(f"| {r['rank']} | {r['name']} | {r['category']} | {r['score']:.2f} | {r['confidence']:.2f} | {p['message'] if p else 'none'} |")
m = next(r for r in out["crops"] if r["crop_id"] == "mango")
lines += ["", "### Ratnagiri -> Mango, evidence summary", ""] + [f"- {l}" for l in m["evidence_summary"]]
lines += ["", f"drainage inferred: `{out['evidence']['drainage']}`; dry months: {out['evidence']['dry_months']}; calibration: {out['calibration_status']}"]
Path(__file__).resolve().parents[2].joinpath("docs/AGRI_ENGINE_REGRESSION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines[:6])); print("... written")
