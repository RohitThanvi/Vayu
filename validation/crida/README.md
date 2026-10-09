# CRIDA DACP pilot (ground truth + irrigation + agro-climatic zone)
Source: ICAR-CRIDA District Agriculture Contingency Plans (~650 districts). Verified on one document (Agra). Not used by the engine yet.
1. `python fetch_dacp.py --urls urls.txt --out dacp_pdfs --limit 20` (own machine; ~20 districts across >= 5 states)
2. `pip install pypdf` then `python parse_dacp.py dacp_pdfs/*.pdf > pilot.json`
3. Measure: districts with empty `qa`, rows parsed vs rows in the PDF (spot-check 5 districts by eye), irrigated-share vs the GFSAD1000 `irrigated_cropland_share` already in the profiles.
4. Only if the parse is reliable: scale up, build ground truth from irrigated/rainfed area by season (adds perennials + summer), use the NARP zone as the stratifier, re-run validation on tune.
Tests: `python test_parse_dacp.py` (parser verified on two documents only: Agra 2014 = template A, Gulbarga 2011 = template B; QA flagged 2 of 5 Gulbarga crop rows as inconsistent IN THE SOURCE, so expect noisy hand-compiled numbers).
