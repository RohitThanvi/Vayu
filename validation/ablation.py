"""
ablation.py - component ablation, computed OFFLINE from the per-crop `ablation_inputs` the engine returns
(so it costs no extra API calls and uses exactly the same responses as the main run).

  1 climate only            temperature, rainfall AMOUNT (deficit/excess vs EcoCrop range, no drainage/seasonality/irrigation)
  2 + soil & terrain        + pH, texture, organic carbon, slope
  3 + water model           rainfall amount -> drainage-aware excess + dry-window seasonality (rainfed deficit)
  4 + irrigation            rainfed deficit -> irrigation-aware deficit (changes only irrigated cases)
  5 + remote sensing        NOT AVAILABLE: the suitability pipeline samples no remote-sensing evidence yet
  6 full VAYU               the production score
Aggregation is the production rule: minimum of soft-floored factors, 0 if a hard (temperature) limit is violated.
"""
SOFT_FLOOR = 0.10
STAGES = ["1_climate_only", "2_plus_soil_terrain", "3_plus_water_model", "4_plus_irrigation", "5_plus_remote_sensing", "6_full_vayu"]


def stage_scores(inp, irrigated):
    if inp.get("temperature_hard"):
        return {s: 0.0 for s in STAGES if s != "5_plus_remote_sensing"}
    fl = lambda v: None if v is None else max(v, SOFT_FLOOR)
    mn = lambda vs: min(v for v in vs if v is not None) if any(v is not None for v in vs) else None
    soil = [fl(inp.get(k)) for k in ("ph", "texture", "organic_carbon", "slope")]
    t = fl(inp.get("temperature"))
    out = {"1_climate_only": mn([t, fl(inp.get("rainfall_amount"))])}
    out["2_plus_soil_terrain"] = mn([t, fl(inp.get("rainfall_amount"))] + soil)
    out["3_plus_water_model"] = mn([t, fl(inp.get("water_rainfed"))] + soil)
    out["4_plus_irrigation"] = mn([t, fl(inp.get("water_irrigated") if irrigated else inp.get("water_rainfed"))] + soil)
    return out
