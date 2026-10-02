"""
Representative location profiles for the agricultural sanity suite.

IMPORTANT: these are APPROXIMATE, rounded climatologies written from general knowledge of IMD-style normals and
typical regional soils. They are for QUALITATIVE sanity checks ("is an obviously compatible crop rejected?"), not
measurements. Responses from the live endpoint now include the monthly rain / temperature arrays (`profile.monthly_*`), so real profiles can be lifted from any validation cache.
Monthly arrays are Jan..Dec.
"""
def _p(rain, temp, ph, tex, tex_name, oc, slope):
    return {"monthly_rain_mm": rain, "monthly_temp_c": temp, "annual_rain_mm": float(sum(rain)),
            "annual_mean_temp_c": sum(temp) / 12, "ph": ph, "texture_class": tex, "texture_name": tex_name,
            "organic_carbon_gkg": oc, "slope_pct": slope, "mode": "aoi", "climate_years": "approx. normals",
            "value_source": {}, "sources": {}}

LOCATIONS = {
    # Konkan coast: ~3100 mm, 6 dry months, laterite
    "ratnagiri": _p([0, 1, 1, 12, 55, 880, 1010, 640, 360, 150, 30, 4], [25, 26, 28, 30, 30, 28, 27, 27, 27, 28, 28, 26], 5.8, 6, "Sandy clay loam", 12, 6),
    # Eastern Rajasthan semi-arid: ~570 mm
    "rajasthan_semiarid": _p([8, 9, 6, 4, 12, 60, 190, 185, 75, 12, 3, 4], [15, 18, 24, 30, 34, 34, 30, 28, 29, 26, 20, 16], 8.0, 9, "Sandy loam", 4, 1),
    # Punjab plains: ~750 mm, canal irrigated
    "punjab": _p([25, 25, 22, 12, 15, 75, 235, 215, 100, 15, 5, 12], [12, 15, 21, 28, 33, 34, 31, 30, 29, 25, 18, 13], 8.0, 7, "Loam", 5, 0.5),
    # Malwa plateau, black cotton (vertisol) soil
    "madhya_pradesh": _p([10, 8, 5, 4, 12, 140, 300, 260, 170, 40, 10, 6], [18, 21, 26, 31, 34, 30, 26, 25, 26, 26, 22, 18], 7.8, 2, "Silty clay", 6, 1.5),
    # Central UP Gangetic plain
    "uttar_pradesh": _p([18, 20, 12, 6, 12, 95, 310, 330, 200, 45, 5, 5], [15, 18, 24, 31, 34, 34, 30, 29, 29, 26, 21, 16], 8.0, 7, "Loam", 5, 0.3),
}
