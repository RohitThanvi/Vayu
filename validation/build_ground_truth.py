#!/usr/bin/env python3
"""
build_ground_truth.py - builds REAL crop-suitability validation cases automatically (no manual data entry).

  Ground truth : district x season crop AREA statistics (Kaggle "Crop Production in India", compiled from
                 data.gov.in / Directorate of Economics & Statistics, 1997-2015), mirrored on GitHub.
  AOIs         : Census-2011 district boundaries (datameet/maps), so the AOI is the SAME unit as the statistics.
  Output       : cases.json, aoi/*.geojson, ground_truth/matching_report.csv, ground_truth/SOURCES.md

  python build_ground_truth.py                       # ~60 districts, stratified across states
  python build_ground_truth.py --max-districts 120 --years 2008-2014 --states "Rajasthan,Haryana,Punjab"

What "truth" means here: for each district and season (Kharif / Rabi), the Vayu-modelled crops of that season ranked
by mean annual sown area over the chosen years. Vayu ranks BIOPHYSICAL suitability, observed area also reflects
irrigation, prices, policy and habit - so agreement will never be perfect. The state-level (leave-district-out)
majority ranking is included as the naive baseline the model has to beat.
"""
import argparse, difflib, hashlib, io, json, re, sys
from pathlib import Path
import numpy as np, pandas as pd, requests, shapefile
from shapely.geometry import shape, mapping
from season_map import SEASON_MAP

HERE = Path(__file__).parent
CROP_URL = "https://raw.githubusercontent.com/ritveek19/EDA_CropProduction/master/crop_production.csv"
SHP_BASE = "https://raw.githubusercontent.com/datameet/maps/master/Districts/Census_2011/2011_Dist"

# dataset crop name -> Vayu crop_id, and Vayu's own season tag (from backend crop_requirements.py)
CROP_MAP = {"wheat": "wheat", "rice": "rice_paddy", "bajra": "bajra_pearl_millet", "jowar": "jowar_sorghum",
            "maize": "maize", "barley": "barley", "gram": "gram_chickpea", "rapeseed &mustard": "mustard",
            "groundnut": "groundnut", "soyabean": "soybean", "cotton(lint)": "cotton", "potato": "potato",
            "onion": "onion"}
# Seasons each crop is evaluated in = where it is grown in practice (backend crop_seasons.py, derived by derive_crop_seasons.py).
sys.path.insert(0, str(HERE.parent / "backend"))
from app.services.agri.crop_seasons import GROWING_SEASONS          # noqa: E402
VAYU_SEASONS = {cid: set(v["seasons"]) for cid, v in GROWING_SEASONS.items()}
STATE_ALIAS = {"orissa": "odisha", "uttaranchal": "uttarakhand", "nctofdelhi": "delhi", "andamanandnicobarisland": "andamanandnicobarislands",
               "jammuandkashmir": "jammuandkashmir", "pondicherry": "puducherry"}


def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s).lower()).replace("&", "and")
    return re.sub(r"[^a-z]", "", s.replace("district", ""))


def nstate(s):
    n = norm(s); return STATE_ALIAS.get(n, n)


def download(url, dest):
    if dest.exists() and dest.stat().st_size > 0: return
    dest.parent.mkdir(parents=True, exist_ok=True)
    print("downloading", url)
    r = requests.get(url, timeout=180); r.raise_for_status(); dest.write_bytes(r.content)


def load_districts(cache):
    for ext in ("shp", "shx", "dbf"): download(f"{SHP_BASE}.{ext}", cache / f"2011_Dist.{ext}")
    rd = shapefile.Reader(str(cache / "2011_Dist"))
    out = []
    for sr in rd.iterShapeRecords():
        out.append({"district": sr.record["DISTRICT"], "state": sr.record["ST_NM"], "geom": shape(sr.shape.__geo_interface__)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2005-2014", help="reference years (dataset ends 2015; 2015 is incomplete)")
    ap.add_argument("--min-years", type=int, default=5)
    ap.add_argument("--min-area-ha", type=float, default=5000, help="min mean annual modelled-crop area in the season")
    ap.add_argument("--min-share", type=float, default=0.03, help="drop crops below this share of modelled area (noise)")
    ap.add_argument("--max-districts", type=int, default=60)
    ap.add_argument("--states", default="", help="comma-separated state filter (default: all)")
    ap.add_argument("--exclude-states", default="", help="comma-separated states to leave out entirely (e.g. states already used "
                    "for diagnosis/tuning) - use this to draw a FRESH, untouched test set after changing the model")
    ap.add_argument("--presence-min-years", type=int, default=2, help="perennial area is stable, and the dataset reports mango for few years")
    ap.add_argument("--max-presence", type=int, default=24, help="perennial presence districts (mango / sugarcane)")
    ap.add_argument("--irrigation", default="both", choices=["both", "true", "false"])
    ap.add_argument("--test-fraction", type=float, default=0.4, help="share of STATES held out whole (spatial holdout)")
    ap.add_argument("--simplify-deg", type=float, default=0.003, help="boundary simplification (~330 m)")
    a = ap.parse_args()
    y0, y1 = (int(x) for x in a.years.split("-"))
    cache = HERE / "ground_truth" / "_download_cache"

    # ---- statistics ------------------------------------------------------------------
    download(CROP_URL, cache / "crop_production.csv")
    df = pd.read_csv(cache / "crop_production.csv"); df.columns = [c.strip() for c in df.columns]
    for c in ("State_Name", "District_Name", "Season", "Crop"): df[c] = df[c].astype(str).str.strip()
    df["Season"] = df["Season"].str.lower()
    raw_all = df.copy()                                         # unfiltered, for perennial presence cases
    df["Season"] = df.Season.map(SEASON_MAP)                    # Autumn / Winter -> kharif, Summer -> zaid (same mapping as derive_crop_seasons.py)
    df = df[df.Season.notna() & df.Crop_Year.between(y0, y1)]
    df["crop_id"] = df.Crop.str.lower().map(CROP_MAP)
    df = df[df.crop_id.notna() & (df.Area > 0)]
    df = df[df.apply(lambda r: r.Season in VAYU_SEASONS[r.crop_id], axis=1)]       # keep a crop only in seasons Vayu evaluates it in
    df["sk"] = df.State_Name.map(nstate); df["dk"] = df.District_Name.map(norm)
    df = df.groupby(["sk", "dk", "State_Name", "District_Name", "Season", "Crop_Year", "crop_id"], as_index=False).Area.sum()

    stats = {}
    for (sk, dk, season), g in df.groupby(["sk", "dk", "Season"]):
        n_years = g.Crop_Year.nunique()
        if n_years < a.min_years: continue
        m = (g.groupby("crop_id").Area.sum() / n_years).sort_values(ascending=False)
        stats[(sk, dk, season)] = {"mean_area": m, "n_years": n_years,
                                   "state": g.State_Name.iloc[0], "district": g.District_Name.iloc[0]}

    # ---- match to boundaries ---------------------------------------------------------
    shp = load_districts(cache)
    by_state = {}
    for d in shp: by_state.setdefault(nstate(d["state"]), []).append(d)
    match, report = {}, []
    for (sk, dk) in sorted({(k[0], k[1]) for k in stats}):
        cands = {norm(d["district"]): d for d in by_state.get(sk, [])}
        if not cands: report.append((sk, dk, "", 0.0, "state_not_in_boundaries")); continue
        if dk in cands: match[(sk, dk)] = cands[dk]; report.append((sk, dk, cands[dk]["district"], 1.0, "exact")); continue
        best = difflib.get_close_matches(dk, list(cands), n=1, cutoff=0.9)
        if best:
            ratio = difflib.SequenceMatcher(None, dk, best[0]).ratio()
            match[(sk, dk)] = cands[best[0]]; report.append((sk, dk, cands[best[0]]["district"], round(ratio, 3), "fuzzy>=0.9"))
        else:
            report.append((sk, dk, "", 0.0, "unmatched"))
    gt_dir = HERE / "ground_truth"; gt_dir.mkdir(exist_ok=True)
    pd.DataFrame(report, columns=["state_key", "dataset_district", "boundary_district", "name_similarity", "status"]).to_csv(
        gt_dir / "matching_report.csv", index=False)

    # ---- state baselines (leave-district-out) ------------------------------------------
    state_tot = {}
    for (sk, dk, season), v in stats.items():
        t = state_tot.setdefault((sk, season), pd.Series(dtype=float)); state_tot[(sk, season)] = t.add(v["mean_area"], fill_value=0)

    # ---- candidate districts ------------------------------------------------------------
    wanted = {nstate(s) for s in a.states.split(",") if s.strip()}
    excluded = {nstate(s) for s in a.exclude_states.split(",") if s.strip()}
    cand = []
    for (sk, dk, season), v in stats.items():
        if (sk, dk) not in match or (wanted and sk not in wanted) or sk in excluded: continue
        m = v["mean_area"]
        if m.sum() < a.min_area_ha: continue
        m = m[m / m.sum() >= a.min_share]
        if len(m) < 2: continue                                  # a 1-crop truth cannot test a ranking
        base = state_tot[(sk, season)].sub(v["mean_area"], fill_value=0).clip(lower=0).sort_values(ascending=False)
        cand.append({"sk": sk, "dk": dk, "season": season, "truth": list(m.index), "shares": (m / m.sum()).round(3).to_dict(),
                     "baseline": list(base.index[:5]), "info": v})
    # districts that have >=1 usable season; stratified round-robin over states for diversity
    dist = {}
    for c in cand: dist.setdefault((c["sk"], c["dk"]), []).append(c)
    per_state = {}
    for (sk, dk) in sorted(dist, key=lambda k: hashlib.sha1("|".join(k).encode()).hexdigest()): per_state.setdefault(sk, []).append((sk, dk))
    chosen, i = [], 0
    while len(chosen) < a.max_districts and any(per_state.values()):
        for sk in sorted(per_state):
            if per_state[sk] and len(chosen) < a.max_districts: chosen.append(per_state[sk].pop(0))
    states = sorted({k[0] for k in chosen})
    test_states = {s for s in states if int(hashlib.sha1(("vayu-split|" + s).encode()).hexdigest(), 16) % 1000 < a.test_fraction * 1000}
    if not test_states and states: test_states = {states[0]}

    # ---- write AOIs + cases ---------------------------------------------------------------
    (HERE / "aoi").mkdir(exist_ok=True); cases = []
    variants = [("irrigated", True), ("rainfed", False)] if a.irrigation == "both" else [(("irrigated" if a.irrigation == "true" else "rainfed"), a.irrigation == "true")]
    for sk, dk in chosen:
        b = match[(sk, dk)]; zid = f"{re.sub(r'[^a-z0-9]+', '_', nstate(b['state']))}__{re.sub(r'[^a-z0-9]+', '_', norm(b['district']))}"
        g = b["geom"].simplify(a.simplify_deg, preserve_topology=True)
        (HERE / "aoi" / f"{zid}.geojson").write_text(json.dumps(mapping(g)), encoding="utf-8")
        for c in dist[(sk, dk)]:
            for vname, irr in variants:
                cases.append({
                    "case_id": f"{zid}__{c['season']}__{vname}", "zone_id": zid, "agro_zone": b["state"], "variant": vname,
                    "split": "test" if sk in test_states else "tune", "type": "crop_ranking", "endpoint": "crop_suitability",
                    "aoi": f"aoi/{zid}.geojson", "request": {"irrigation_available": irr},
                    "truth": {"season": c["season"], "k": 3, "gt_crops": c["truth"], "baseline_crops": c["baseline"],
                              "mean_area_share": c["shares"]},
                    "source": f"Kaggle 'Crop Production in India' (data.gov.in/DES), mean annual area {y0}-{y1}, "
                              f"{c['info']['n_years']} yrs; boundary: datameet Census 2011"})
    # ---- perennial PRESENCE cases ------------------------------------------------------------
    # The dataset has too few perennial crops for a ranking (mango, sugarcane, a little lemon), but it supports a different,
    # important check: where a Vayu perennial crop is a MAJOR established crop, the engine must not reject it.
    PERENNIAL = {"mango": ("mango", 3000.0), "sugarcane": ("sugarcane", 10000.0)}          # dataset name -> (Vayu id, min mean ha)
    # NOTE: the dataset has NO mango rows for Maharashtra/Konkan (Ratnagiri) and only a few states overall, so Konkan mango cannot be
    # checked from this source. That case lives in the offline sanity suite (backend/tests) and needs a horticulture-statistics source.
    pr = raw_all[raw_all.Crop.str.lower().isin(PERENNIAL) & raw_all.Crop_Year.between(y0, y1) & (raw_all.Area > 0)].copy()
    pr["sk"] = pr.State_Name.map(nstate); pr["dk"] = pr.District_Name.map(norm)
    yr = pr.groupby(["sk", "dk", "Crop", "Crop_Year"], as_index=False).Area.sum()
    pm = yr.groupby(["sk", "dk", "Crop"]).agg(area=("Area", "mean"), yrs=("Crop_Year", "nunique")).reset_index()
    pm["crop_id"] = pm.Crop.str.lower().map(lambda c: PERENNIAL[c][0]); pm["min_ha"] = pm.Crop.str.lower().map(lambda c: PERENNIAL[c][1])
    pm = pm[(pm.yrs >= a.presence_min_years) & (pm.area >= pm.min_ha)]
    pm = pm[[((r.sk, r.dk) in match) and (r.sk not in excluded) and (not wanted or r.sk in wanted) for r in pm.itertuples()]]
    sel = []
    for cid, g in pm.groupby("crop_id"):
        g = g.sort_values("area", ascending=False); per_st = {}
        for r in g.itertuples(): per_st.setdefault(r.sk, []).append(r)
        picked = []
        while len(picked) < a.max_presence // 2 and any(per_st.values()):
            for st in sorted(per_st):
                if per_st[st] and len(picked) < a.max_presence // 2: picked.append(per_st[st].pop(0))
        sel += picked
    for r in sel:
        b = match[(r.sk, r.dk)]; zid = f"{re.sub(r'[^a-z0-9]+', '_', nstate(b['state']))}__{re.sub(r'[^a-z0-9]+', '_', norm(b['district']))}"
        aoi_file = HERE / "aoi" / f"{zid}.geojson"
        if not aoi_file.exists():
            aoi_file.write_text(json.dumps(mapping(b["geom"].simplify(a.simplify_deg, preserve_topology=True))), encoding="utf-8")
        is_test = int(hashlib.sha1(("vayu-split|" + r.sk).encode()).hexdigest(), 16) % 1000 < a.test_fraction * 1000
        for vname, irr in variants:
            cases.append({"case_id": f"{zid}__presence_{r.crop_id}__{vname}", "zone_id": zid, "agro_zone": b["state"], "variant": vname,
                          "split": "test" if is_test else "tune", "type": "presence", "endpoint": "crop_suitability",
                          "aoi": f"aoi/{zid}.geojson", "request": {"irrigation_available": irr},
                          "truth": {"crop_id": r.crop_id, "mean_area_ha": round(float(r.area), 0)},
                          "source": f"Kaggle 'Crop Production in India' mean annual area {y0}-{y1} ({int(r.yrs)} yrs); boundary: datameet Census 2011"})

    meta = {"description": "AUTO-GENERATED by build_ground_truth.py. agro_zone = state (a proxy, NOT ICAR agro-climatic zones).",
            "years": a.years, "test_states_held_out_whole": sorted(test_states), "excluded_states": sorted(excluded)}
    (HERE / "cases.json").write_text(json.dumps({"meta": meta, "cases": cases}, indent=1), encoding="utf-8")
    (gt_dir / "SOURCES.md").write_text(f"""# Ground-truth sources (auto-generated)
- **Crop statistics:** Kaggle 'Crop Production in India' (Abhinand05), compiled from data.gov.in / Directorate of Economics & Statistics,
  Ministry of Agriculture; district x season x crop x area (ha), 1997-2015. Mirror used: {CROP_URL}
- **District boundaries:** datameet/maps, Census-2011 districts. {SHP_BASE}.shp
- **Reference years:** {a.years} (>= {a.min_years} years required per district-season).
- Check each source's licence/terms before redistributing the raw data.

## Known limitations (state these when you present results)
1. Dataset ends in 2015 and has known reporting gaps/zeros; boundaries are Census-2011 while some districts were later split.
2. Truth ranks only the {len(VAYU_SEASONS)} seasonal crops Vayu models, and a crop counts in a season only if Vayu evaluates it there (observed-practice eligibility). Other crops are ignored.
3. Observed area reflects irrigation, prices, policy and tradition, not only biophysical suitability.
4. The dataset has no irrigation share, so every district is run twice (irrigated / rainfed) and reported separately;
   it is NOT chosen per district. Replace with real irrigated-area shares (e.g. ICRISAT) for a sharper test.
5. DES season labels are mapped Kharif/Autumn/Winter -> kharif, Rabi -> rabi, Summer -> zaid (season_map.py); 'Whole Year' is excluded.
   (An earlier version kept only Kharif/Rabi labels, which dropped 42% of national rice area and all rice in the eastern states; fixed.)
6. Name matching between sources is automatic; audit `matching_report.csv` (fuzzy matches are flagged).
7. Split: whole STATES are held out (spatial holdout): {sorted(test_states)}.
8. `agro_zone` is the state, not an ICAR agro-climatic zone.
""", encoding="utf-8")
    n_d = len(chosen)
    print(f"\n{n_d} districts, {len(cases)} cases ({sum(c['split']=='test' for c in cases)} test / {sum(c['split']=='tune' for c in cases)} tune); "
          f"{len(states)} states, held-out states: {sorted(test_states)}")
    print(f"matching: {pd.DataFrame(report, columns=['k','d','b','r','status']).status.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
