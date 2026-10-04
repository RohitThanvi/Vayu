#!/usr/bin/env python3
"""
resample_profiles.py - re-sample every validation AOI with the CURRENT sampler (projected DEM + cropland-weighted slope) and save the profiles,
so the effect of the sampling fix can be tested offline with --replay. Needs your local Earth Engine credentials.

  cd backend
  python ..\\validation\\diagnostics\\resample_profiles.py                      # all AOIs in ..\\validation\\cases.json
  python ..\\validation\\diagnostics\\resample_profiles.py --limit 3            # quick smoke test first
  python ..\\validation\\diagnostics\\resample_profiles.py --force              # redo existing files

Writes ..\\validation\\validation_runs\\_profiles_v2\\<aoi_key>.json (same format the harness saves; resumable). Then:
  cd ..\\validation
  python run_validation.py --cases cases.json --replay validation_runs\\_profiles_v2 --split tune
"""
import argparse, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
VAL = HERE.parent
sys.path.insert(0, str(VAL.parent / "backend")); sys.path.insert(0, str(VAL))
from app.services import gee_client                                   # noqa: E402
from app.services.agri.crop_suitability import sample_location_profile   # noqa: E402
from client import aoi_key                                            # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(VAL / "cases.json")); ap.add_argument("--out", default=str(VAL / "validation_runs" / "_profiles_v2"))
    ap.add_argument("--workers", type=int, default=2); ap.add_argument("--limit", type=int, default=0); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    gee_client._initialize_gee()
    if not gee_client.GEE_READY:
        sys.exit(f"Earth Engine not ready: {gee_client.GEE_INIT_ERROR}")
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))["cases"]
    aois = {}
    for c in cases:
        if c.get("type") in ("crop_ranking", "presence") and c.get("aoi"):
            aois.setdefault(c["aoi"], None)
    todo = []
    for rel in sorted(aois):
        aoi = json.loads((VAL / rel).read_text(encoding="utf-8")); key = aoi_key({"aoi_geojson": aoi})
        if a.force or not (out / f"{key}.json").exists():
            todo.append((rel, aoi, key))
    if a.limit: todo = todo[:a.limit]
    print(f"{len(aois)} AOIs in cases, {len(todo)} to sample -> {out}")

    def work(item):
        rel, aoi, key = item
        t0 = time.time(); prof = sample_location_profile(aoi=aoi)
        prof["value_source"] = {"ph": "modeled", "organic_carbon_gkg": "modeled", "texture": "modeled"}
        (out / f"{key}.json").write_text(json.dumps({"profile": prof, "engine_version": "resample", "source_aoi": rel,
                                                     "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
        return rel, prof, time.time() - t0

    ok = bad = 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(work, it): it for it in todo}
        for f in as_completed(futs):
            rel = futs[f][0]
            try:
                _, p, dt = f.result(); ok += 1
                print(f"[{ok + bad}/{len(todo)}] {Path(rel).stem:34s} slope {p['slope_basis']:9s} {p['slope_deg']!s:>5} deg "
                      f"(all land {p['slope_all_deg']!s:>5}, cropland {p['slope_cropland_deg']!s:>5}, cropland share {p['cropland_fraction']!s:>5})  {dt:4.0f}s")
            except Exception as e:                                    # noqa: BLE001
                bad += 1; print(f"[{ok + bad}/{len(todo)}] {Path(rel).stem}: FAILED {type(e).__name__}: {str(e)[:160]}")
    print(f"\ndone: {ok} sampled, {bad} failed. Replay with:  --replay {out}")


if __name__ == "__main__":
    main()
