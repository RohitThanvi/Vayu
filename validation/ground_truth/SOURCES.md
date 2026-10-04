# Ground-truth sources (auto-generated)
- **Crop statistics:** Kaggle 'Crop Production in India' (Abhinand05), compiled from data.gov.in / Directorate of Economics & Statistics,
  Ministry of Agriculture; district x season x crop x area (ha), 1997-2015. Mirror used: https://raw.githubusercontent.com/ritveek19/EDA_CropProduction/master/crop_production.csv
- **District boundaries:** datameet/maps, Census-2011 districts. https://raw.githubusercontent.com/datameet/maps/master/Districts/Census_2011/2011_Dist.shp
- **Reference years:** 2005-2014 (>= 5 years required per district-season).
- Check each source's licence/terms before redistributing the raw data.

## Known limitations (state these when you present results)
1. Dataset ends in 2015 and has known reporting gaps/zeros; boundaries are Census-2011 while some districts were later split.
2. Truth ranks only the 13 seasonal crops Vayu models, and a crop counts in a season only if Vayu evaluates it there (observed-practice eligibility). Other crops are ignored.
3. Observed area reflects irrigation, prices, policy and tradition, not only biophysical suitability.
4. The dataset has no irrigation share, so every district is run twice (irrigated / rainfed) and reported separately;
   it is NOT chosen per district. Replace with real irrigated-area shares (e.g. ICRISAT) for a sharper test.
5. DES season labels are mapped Kharif/Autumn/Winter -> kharif, Rabi -> rabi, Summer -> zaid (season_map.py); 'Whole Year' is excluded.
   (An earlier version kept only Kharif/Rabi labels, which dropped 42% of national rice area and all rice in the eastern states; fixed.)
6. Name matching between sources is automatic; audit `matching_report.csv` (fuzzy matches are flagged).
7. Split: whole STATES are held out (spatial holdout): ['haryana', 'madhyapradesh', 'maharashtra', 'mizoram', 'nagaland', 'odisha', 'rajasthan', 'uttarpradesh', 'westbengal'].
8. `agro_zone` is the state, not an ICAR agro-climatic zone.
