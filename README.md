<p align="center">
  <img src="frontend/public/logo.png" alt="Vayu" width="120" />
</p>

<h1 align="center">VAYU</h1>
<p align="center"><b>Geospatial &amp; Business Intelligence Terminal</b></p>

<p align="center">
  <em>Satellite earth observation · remote sensing · maritime &amp; economic intelligence · agricultural risk scoring — unified in one live terminal.</em>
</p>

---

> **Abstract**
> Vayu is a web-based earth-observation and open-source-intelligence (OSINT) terminal that fuses **on-demand satellite analysis** over user-defined areas of interest with a **continuously streaming intelligence layer** — seismic activity, thermal anomalies, geolocated news, maritime traffic, commodity markets, and macroeconomic indicators. A dedicated **remote sensing toolkit** exposes the underlying spectral, radar, and terrain methods directly for domain researchers, while a **business intelligence layer** correlates chokepoint traffic, sanctions exposure, SEC disclosures, and economic-bloc data into a single auditable risk signal. An **agricultural module** applies the same satellite pipeline to a composite risk-scoring engine with region monitoring and automated alerting, plus a cited, seasonal **crop-suitability engine** with a held-out validation harness.
>
> Every computed result states its source dataset, spatial resolution, and formula alongside the number — this is built to be checked, not just read. Where a method is only a proxy (for example the water-quality indices) or has not yet been validated against documented events, the result and this README say so.

---

## Table of Contents

1. [Access Model](#access-model)
2. [Satellite Earth Observation](#satellite-earth-observation)
3. [Remote Sensing Toolkit — Spectra](#remote-sensing-toolkit--spectra)
4. [Business Intelligence](#business-intelligence)
5. [Agricultural Risk Module](#agricultural-risk-module)
6. [Scientific PDF Reporting](#scientific-pdf-reporting)
7. [Real-Time Intelligence Feeds](#real-time-intelligence-feeds)
8. [Weather &amp; Orbital Layers](#weather--orbital-layers)
9. [Platform: Theme, Mobile &amp; Landing Page](#platform-theme-mobile--landing-page)
10. [Validation &amp; Testing](#validation--testing)
11. [Architecture](#architecture)
12. [Project Structure](#project-structure)
13. [Deployment &amp; Configuration](#deployment--configuration)
14. [API Reference](#api-reference)
15. [Data Source Attribution](#data-source-attribution)
16. [Known Constraints](#known-constraints)
17. [Licensing](#licensing)

---

## Access Model

The landing page routes into one of four scoped terminal configurations (chosen from a tier picker or directly from the Tiers section) rather than a single undifferentiated view:

| Terminal | Tabs enabled | Intended for |
| --- | --- | --- |
| **Vayu Business** | Analyze, Business, Weather, Orbital | Markets, logistics, and macro/geopolitical analysis |
| **Vayu Agri** | Analyze, Weather, Agri | Agricultural risk monitoring and land condition |
| **Vayu Remote Sensing** | Analyze, Spectra, Weather | Domain remote sensing research (spectral/radar/terrain science) |
| **Vayu Full Terminal** | All six tabs | Unrestricted access |

There are no user accounts in the live build: the tier is a client-side choice (stored in `localStorage`). Account/session infrastructure (email+password signup, service-tier field, password reset) is implemented against a Postgres backend but disabled in favor of the tier picker above for the MVP — see the in-code banner comments in `frontend/src/components/AppGate.jsx` and `backend/app/api/auth_endpoints.py` for the exact restoration path.

<p align="center">
  <img src="frontend/public/screenshots/analyze-maritime.jpg" width="800" alt="Analyze tab with live vessel tracking and the Intel Feed" />
  <br /><sub>Analyze tab — AOI-defined satellite analysis, live vessel tracking, and the multi-source Intel Feed. (Earlier build; current tab set and layout have since expanded — see below.)</sub>
</p>

---

## Satellite Earth Observation

**Tab: Analyze**

1. **Spatial definition** — draw an arbitrary GeoJSON Polygon/MultiPolygon AOI on the interactive canvas, or search a place by name.
2. **Natural-language translation** — free-text analytical queries (e.g. *"Show vegetation loss in this area from 2020-01-01 to 2024-01-01"*) are parsed by an LLM-orchestrated pipeline into a discrete metric and time window.
3. **Computation** — Google Earth Engine executes the corresponding raster analysis; results return as structured metrics plus a rendered map layer.
4. **Toggleable satellite basemap layers**, independent of the query pipeline: true color, NDVI, SAR, thermal/IR.

| Analysis | Dataset | Application |
| --- | --- | --- |
| Vegetation Dynamics | Sentinel-2 SR Harmonized (NDVI) | Canopy/green-cover variance screening |
| Urban Expansion | Google Dynamic World V1 | Built-up growth, gain/loss/unchanged change map |
| Hydrological Variance | JRC Global Surface Water | Surface-water persistence/depletion, gain/loss/unchanged change map |
| Radar Flood Detection | Sentinel-1 SAR GRD + JRC GSW + SRTM DEM | SAR flood-extent mapping through cloud cover, with permanent-water and steep-terrain exclusion |
| Thermal &amp; Burn Scarring | MODIS Burned Area / Active Fire | Burned-footprint and thermal-event detection |
| Vegetative Stress | Sentinel-2 NDDI | Drought-stress screening, area-normalized |
| Thermal Profiling | Landsat 8/9 TIRS (Collection 2) | Surface temperature, urban heat island detection |
| Forest-Cover Loss | Hansen Global Forest Change | Multi-year tree-cover loss tracking |
| Subsurface Hydrology | NASA SMAP L4 | Soil-moisture variance, area-normalized |

Each of these 9 analyses can be exported as a source-cited PDF — see [Scientific PDF Reporting](#scientific-pdf-reporting).

---

## Remote Sensing Toolkit — Spectra

**Tab: Spectra** — 22 tools

A direct-access toolkit for domain researchers (IIRS/NRSC/ISRO-adjacent workflows) who need the underlying spectral/radar/terrain building blocks rather than a pre-packaged natural-language answer. Pick a tool, an AOI, and a date range; every result states its dataset, resolution, and formula alongside the numbers.

**What every tool returns** (the contract each one is built to):
* **Method + citation** — the formula or published method, and the dataset it ran on.
* **Uncertainty** — standard deviation and/or valid-pixel fraction, and the number of scenes or footprints behind the number.
* **Pixel-level map layer** where a map is meaningful (a Leaflet tile layer you can toggle on the map).
* **GeoTIFF download** of the underlying raster (raw values, not the stretched picture), and a client-side **CSV** export of any result.
* **Scene provenance** for composites — the exact scene IDs, dates and cloud % that fed the composite.
* A source-cited **PDF report** for any result (see [Scientific PDF Reporting](#scientific-pdf-reporting)).
* For tools that measure something over time, a one-click **"View as … timelapse"** that replays the layer over the same AOI and dates (see **Timelapse** below).

Results are cached (exact-match on tool + every parameter, 30-day TTL, Redis if `REDIS_URL` is set, in-memory otherwise), which is safe because a historical query over a fixed geometry and date range is deterministic.

### Optical, terrain and land cover

| Tool | Method | Dataset · Resolution |
| --- | --- | --- |
| **Spectral Indices** | NDVI (Rouse 1974), NDWI (McFeeters 1996), MNDWI (Xu 2006), NDBI (Zha et al. 2003), SAVI (Huete 1988), EVI (Huete et al. 2002), NDSI (Hall et al. 1995) | Sentinel-2 SR Harmonized, cloud-masked median composite · 10-20 m |
| **Spectral Composites ("X-ray" views)** | The same imagery through different band combinations: natural colour, colour infrared (vegetation vigour), SWIR B12-B8A-B4 (burn scars, haze), atmospheric penetration, agriculture/moisture, geology, plus a **Sentinel-1 SAR composite** (VV, VH, VV−VH) that sees through cloud. Published recipes (Sentinel Hub custom-scripts, Sentinel education band list, NASA Earth Observatory). Colours are a fixed display stretch, not a measurement — per-channel statistics, clipped-pixel shares and raw-band GeoTIFFs are provided for quantitative work | Sentinel-2 SR Harmonized + Sentinel-1 GRD · 10-20 m |
| **Terrain Analysis** | Elevation, slope, aspect. Aspect is circular data (0°≡360°) and is averaged via a unit-vector circular mean, not a naive arithmetic mean; hillshade layer | Copernicus DEM GLO-30 (TanDEM-X-derived) · 30 m |
| **Land Cover Classification** | 11-class area breakdown (tree cover, cropland, built-up, water, wetland, mangrove, etc.) with the dataset's official palette | ESA WorldCover v200, 76.7% validated overall accuracy · 10 m, 2021 |
| **Dynamic World (ML)** | Google/WRI's pretrained near-real-time 9-class land-cover model — no training needed; complements the custom classifier below | GOOGLE/DYNAMICWORLD/V1 · 10 m |
| **Snow Cover** | NDSI &gt; 0.4 classification threshold (Hall et al. 1995 — the same threshold MODIS's operational snow product uses) | Sentinel-2 SR · 20 m — resolves individual Himalayan valley glaciers far better than MODIS's native 500 m |
| **Change Detection** | A chosen spectral index differenced between two independent date ranges over the same AOI — per-period mean, delta and % change | Sentinel-2 SR · 20 m |
| **Burn Severity** | dNBR with the USGS FIREMON classes (Key &amp; Benson 2006) alongside **RBR** (Relativized Burn Ratio, Parks et al. 2014), which corrects for pre-fire vegetation density; the dNBR classes are applied to RBR only for side-by-side comparison, not as RBR's own calibrated cut-offs | Sentinel-2 SR · 20 m |
| **Timelapse** | A frame-by-frame timelapse of **natural colour, NDVI, night lights, NO₂, SAR backscatter or soil moisture** with **yearly, quarterly or monthly** frames (up to 60). Each frame is a composite of its period (Landsat/SAR: median; VIIRS/NO₂/SMAP: mean) drawn with one stretch shared by every frame, so frames are comparable. Played in-app with period labels and speed control; per-frame image count, valid-pixel fraction and AOI-mean value; a Mann-Kendall / Sen's slope trend for yearly frames only (a seasonal cycle would corrupt it otherwise). **Download as MP4 or GIF** (period label burned in; built server-side from the frames). The PDF report embeds **every frame** in a contact sheet. Periods with no usable data are skipped and listed. Also reachable with one click ("View as … timelapse") from the Night Lights, Spectral Indices, Spectral Composites, Atmosphere, SAR Backscatter and Soil Moisture results | Landsat 5/7/8/9 C2 L2 (30 m, 1984+), VIIRS DNB (2014+), Sentinel-5P NO₂ (mid-2018+), Sentinel-1 GRD (2014+), SMAP L4 (Apr 2015+) |
| **Forest Structure (GEDI)** | Canopy height (rh98) and aboveground biomass density (AGBD, with the model's own standard error) from spaceborne-lidar footprints, filtered with the Earth Engine catalog's own quality masks. Reports footprint statistics, footprint counts and density — **no area totals or carbon stock** are computed, because GEDI samples along orbit tracks and is not a probability sample of the AOI | GEDI L2A + L4A monthly rasters · 25 m footprints, 51.6°N–51.6°S, 2019 onward |
| **Water Quality (indicative)** | **Indicative only.** NDTI turbidity proxy (Lacaux et al. 2007) and NDCI chlorophyll proxy (Mishra &amp; Mishra 2012) over permanent water (JRC occurrence ≥ 90%). Uncalibrated relative indices for comparing places or dates within one waterbody — no NTU or chlorophyll-a concentrations are reported | Sentinel-2 SR + JRC GSW v1.4 · 20 m |

### Radar, water and atmosphere

| Tool | Method | Dataset · Resolution |
| --- | --- | --- |
| **SAR Backscatter** | VV/VH backscatter (dB) + Radar Vegetation Index. RVI is computed on *linear* backscatter power after converting from dB — a ratio of decibel values is not the same physical quantity as a ratio of the underlying power | Sentinel-1 GRD, dual-pol IW mode, all-weather day/night · 20 m |
| **Flood Mapping** | Before/after SAR change detection following UN-SPIDER's Recommended Practice: both periods from the same orbit pass, 50 m speckle smoothing, ratio on dB bands, permanent water (JRC occurrence ≥ 90%) masked out; VH default, VV for open-water delineation. The tool cannot verify a flood actually occurred in the window you give it | Sentinel-1 GRD · 10-20 m |
| **Surface Water Dynamics** | Water occurrence over 1984–2021, split into permanent (occurrence ≥ 90%) and seasonal | JRC Global Surface Water v1.4 · 30 m |
| **Soil Moisture** | Surface soil-moisture (`sm_surface`) mean snapshot over the period | NASA SMAP L4 · coarse (~10 km class) |
| **Land Surface Temperature** | Surface temperature from the Landsat thermal band — urban heat and drought stress | Landsat 8/9 Collection 2 Level-2 (ST_B10) |
| **Vegetation Drought Index** | Kogan's **VCI, TCI and VHI** (0–100): this window's NDVI and daytime land-surface temperature scaled against each pixel's own min/max for the same calendar window in 2001..(year−1); VHI = 0.5×VCI + 0.5×TCI, with drought classes. Relative to each pixel's own history, not an absolute drought measure; the number of baseline years actually used is reported. Window must sit inside one calendar year | MODIS MOD13Q1 NDVI (250 m) + MOD11A2 LST (1 km), analysed at 1 km |
| **Night Lights (VIIRS)** | Monthly night-time radiance (mean, σ, summed) with the cloud-free observation count behind each month, Mann-Kendall + Sen's slope. An economic-activity *proxy*, not a GDP measure; this product version is unfiltered (fires, boats, aurora included) and zero-observation months are shown as gaps, not darkness. Replayable as a timelapse | NOAA VIIRS DNB monthly, stray-light-corrected · ~464 m, 2014 onward |
| **Atmospheric Composition** | Tropospheric NO₂, SO₂, CO column densities and the absorbing aerosol index — column densities, not ground-level concentrations, so not comparable to a surface AQI monitor | Sentinel-5P / TROPOMI OFFL L3 · ~1 km grid, 2018 onward |

### Statistics, machine learning and validation

| Tool | Method | Dataset · Notes |
| --- | --- | --- |
| **Index Time Series** | A spectral index over time with a **Mann-Kendall** trend test and **Sen's slope** (`trend_stats.py`, unit-tested against known series) | Sentinel-2 SR |
| **ML Classify** | Random Forest (`ee.Classifier.smileRandomForest`, Breiman 2001) trained on points you click on the map, into whatever classes your analysis needs. Stratified held-out test split; reports **both** the (optimistic) training accuracy and the honest held-out test accuracy, clearly labelled | Sentinel-2 composite (6 bands + NDVI/NDWI/NDBI) |
| **Accuracy Assessment** | Confusion matrix, overall / producer's / user's accuracy and Cohen's kappa (Congalton 1991; `accuracy_stats.py`, unit-tested) of Land Cover, Dynamic World or Burn Severity against reference points you supply | Pure-Python statistics, GEE-independent |

> **Status of the newer tools.** The six most recently added tools (Spectral Composites, Vegetation Drought Index, Night Lights, Forest Structure, Timelapse, Water Quality) were built and checked against dataset catalogs and source papers, and their Python logic is unit/mocked-tested, but they have **not yet been run against live Earth Engine nor validated against documented real-world events**. See [Validation &amp; Testing](#validation--testing).

---

## Business Intelligence

**Tab: Business** — a wide, collapsible bottom panel with three sub-views (Live / Analysis / Economics), built specifically because cramming this much into a narrow sidebar made it unreadable.

### Live

* **Unified 0-100 business risk score** per monitored chokepoint (Strait of Hormuz, Strait of Malacca, Bab-el-Mandeb, Suez Canal, Strait of Gibraltar, Panama Canal, English Channel) — a deterministic, fully auditable weighted blend: 40% vessel-traffic anomaly (14-day rolling baseline z-score), 20% regional GDELT tone, 20% seismic proximity, 20% OFAC sanctions hits. No opaque model; every component and its weight is shown.
* **Dark-vessel (AIS-gap) detection** — flags vessels that stop transmitting AIS near a chokepoint and reappear elsewhere, further than plausible continuous transit would explain — a known sanctions-evasion/smuggling signature. Pure logic on the existing AIS stream, with a mass-disappearance guard so a bridge reconnect isn't mistaken for real gaps.
* **OFAC sanctions screening** — currently tracked vessels matched by name against the US Treasury SDN list (name-only match, disclosed as such — AIS provides MMSI+name, not IMO number).
* **Macro context** — US (FRED: fed funds rate, CPI, 10-year Treasury), Global, and India (World Bank: GDP growth, inflation) side by side.
* **Regional tone** — GDELT news coverage aggregated into geographic hotspots, most-negative-first, with sample headlines.
* **SEC exposure (EDGAR)** — on-demand full-text search of recent 8-K/10-K/10-Q filings mentioning a selected chokepoint.
* **Markets** — global stock indices (S&amp;P 500, Dow, Nasdaq, Nifty 50, Sensex, FTSE, Nikkei, Shanghai) and a *curated* set of bellwether equities tied to Vayu's own themes — energy majors (Reliance, ExxonMobil, Chevron), shipping/logistics (ZIM, FedEx) and defense (Lockheed, RTX) — rather than a generic ticker list.
* **Conflict forecast** — ACLED CAST: a monthly political-violence forecast per country, up to six months ahead, shown as observed-vs-forecast bars and explicitly labelled indicative. CAST access is granted by ACLED separately from the event feed, so the UI reports a distinct "no CAST access" status instead of failing silently.

### Analysis

Historical charting for everything above that was previously current-value-only — commodities (Yahoo Finance chart API, 1M-5Y), US macro (FRED), Global/India macro (World Bank), and chokepoint vessel-traffic history (accumulated automatically every 15 minutes), and market indices/equities. A **chokepoint–market correlation** view ranks a chokepoint's highest traffic-anomaly days (z-score against the window's own mean/σ) and reports how its bellwether stocks moved on the same and next day — descriptive, with no fabricated correlation coefficient. Rendered with a dependency-free SVG chart: real Y-axis, multiple X-axis date markers, and a hover crosshair/tooltip showing the exact value at any point.

### Economics

G7 / G20 / BRICS / ASEAN bloc analysis, each with:
* **Macro rollup** — GDP growth + inflation for every member country (World Bank, fetched in parallel).
* **Regional tone** — GDELT coverage filtered by bloc/member-country name.
* **Commodity relevance** — which tracked commodities this bloc's members are major producers/exporters of. Explicitly editorial (not API-sourced); every entry carries its reasoning inline so it is reviewable rather than presented as fact.
* **SEC exposure** — EDGAR full-text search for the bloc's own name.

### Strategic sites

64 major ports, oil refineries, and mines (curated, hand-maintained — see `strategic_sites.py`), plotted on the map with live proximity-based signals (nearby earthquakes, dark-vessel flags, regional tone) **and** actual recent news headlines within 150 km, shown on hover with source, timestamp, and a direct link.

<p align="center">
  <img src="frontend/public/screenshots/weather-layers.jpg" width="800" alt="Weather overlay layers" />
  <br /><sub>Weather tab — temperature, wind, air-quality overlays, with the live commodity ticker along the bottom.</sub>
</p>

---

## Agricultural Risk Module

**Tab: Agri** — a compact sidebar for the core risk score, plus a wide bottom panel (matching the Business and Spectra bars) for the deeper tools.

**Sidebar (Score · Watchlist · Overview · Mandi)**
* **Composite 0-100 risk score** combining drought (NDDI), vegetation decline (NDVI threshold change), and soil-moisture deficit (SMAP) as a weighted average (40/35/25%), automatically renormalized if any indicator is unavailable.
* **Regional environmental context** — groundwater trend (GRACE), rainfall anomaly vs. a 10-year seasonal normal (CHIRPS), and land surface temperature (Landsat), reported separately from the composite score due to differing spatial resolution/timescale.
* **5-year seasonal NDVI baseline** — flags whether current conditions are unusual for that time of year at that location.
* **Region watchlist** with a farmer/officer feedback loop — confidence blends data completeness with the region's actual historical alert accuracy; an **automated alert engine** and a **WhatsApp bot** for last-mile delivery.
* **Mandi (market) price** overlay (data.gov.in / Agmarknet), with historical price trend (avg/CAGR) from CEDA.

**Bottom panel**
* **Groundwater** — regional groundwater trend from GRACE mascons (coarse, ~300 km grid; read as a regional signal, not a well-level one).
* **Analysis** — NDVI and groundwater trend charts side by side.
* **Crop Stage** — green-up / peak / senescence read from the AOI's own NDVI seasonal curve (`phenology.py`), filling the gap that the risk score is crop-agnostic.
* **Irrigation** — an *irrigate now / monitor / hold off* advisory that cross-checks SMAP soil-moisture dry stress against CHIRPS rainfall-vs-normal. A directional regional read, not a field-level prescription.
* **Crops** — ranked crop suitability for a point or AOI (crop requirements and season windows are India-focused; see below).
* **ML Extent** — Random Forest crop-extent classification, a thin wrapper over Spectra's ML Classify.

### Crop-suitability engine

Ranks a set of Indian crops (13 are evaluated seasonally) for a location from modeled soil (OpenLandMap 250 m), slope (Copernicus DEM), 10-year monthly rainfall (CHIRPS) and temperature (ERA5-Land). Full write-up: [`docs/AGRI_SUITABILITY_ENGINE.md`](docs/AGRI_SUITABILITY_ENGINE.md).

* **Seasonal, by observed practice.** A crop is scored in each season it is actually grown — Kharif (Jun–Oct), Rabi (Nov–Mar), Zaid (Mar–Jun), perennials year-round — with eligibility taken from Directorate of Economics &amp; Statistics area-by-season data, and the best season highlighted in plain language (e.g. jowar is evaluated in both kharif and rabi; wheat only in rabi).
* **Cited, auditable crop requirements.** EcoCrop-style trapezoids for temperature, rainfall and pH, plus texture, organic carbon and slope, each schema-validated in tests.
* **A constraint engine, not a black box.** One 0–1 score per factor; only an absolute temperature limit can make a crop *unsuitable*; everything else reduces the score but is floored; missing evidence lowers confidence and is never counted against a crop. The final score is the minimum over factors (the FAO limiting-factor rule), with the limiting factor named (*rainfall deficit*, *excess rainfall*, *dry-season length*, …).
* **Water is three separate questions** — deficit, excess (scaled by inferred drainage) and rainfall seasonality — and irrigation availability is inferred from the surrounding irrigated-cropland share (GFSAD1000) or set explicitly.
* **Farmer overrides and profit layer** — a soil-test can override the modelled soil, and a mandi-price-based revenue estimate is added per crop.
* **Honest calibration status.** Scores pass through an isotonic calibration that is the *identity until fitted* on a tune-split run, and the response says so (`calibration_status`). `confidence` is a data-completeness heuristic, not a statistical interval.

<p align="center">
  <img src="frontend/public/screenshots/agri-dashboard.jpg" width="800" alt="Agricultural risk dashboard" />
  <br /><sub>Agri tab — composite risk score, contributing-indicator breakdown, and drought-trend chart for a drawn AOI.</sub>
</p>

---

## Scientific PDF Reporting

Every satellite analysis (the 9 Analyze-tab metrics, the agricultural risk score, and any Spectra result) exports as a structured PDF, not raw numbers. A **timelapse report embeds every frame** (a labelled contact sheet with each frame's value, image count and valid-pixel share), not a single image:

* Executive summary, study-area geometry, full methodology with a worked numeric example, indicator definitions and thresholds, results table, findings &amp; interpretation, data-quality/confidence section, recommendations, glossary, full dataset citations, and stated limitations.
* **Satellite imagery panels** (true color, NDVI, NDDI, SAR, thermal, SMAP as applicable) with proper legends, plus a dedicated gain/loss/unchanged **change map** for vegetation/built-up/water reports.
* Imagery captions disclose what was actually achievable for that run (cloud-free window used, % of AOI with valid pixels) rather than a fixed claim.
* An optional LLM-generated narrative, explicitly grounded in the same hedged, deterministic findings text shown elsewhere — never asserting more certainty than the underlying classification supports.

<p align="center">
  <img src="frontend/public/screenshots/report-imagery.jpg" width="480" alt="PDF report satellite imagery panel" />
  <br /><sub>Generated PDF report — true color, NDVI, NDDI, and SMAP panels with legends and methodology captions.</sub>
</p>

---

## Real-Time Intelligence Feeds

An asynchronous scheduler maintains persistent polling loops, deduplicating and broadcasting events to connected clients via WebSocket.

| Source | Interval | Feeds |
| --- | --- | --- |
| USGS | 5 min | Global seismic events, M3.5+ |
| NASA FIRMS | 15 min | VIIRS active thermal hotspots |
| GDELT GKG | 10 min | Multilingual geolocated news events, with per-article sentiment (tone) |
| ACLED | 60 min | Political violence / conflict events |
| AISStream.io | Real-time | Live vessel positions in monitored chokepoints (via a dedicated `ais-bridge` service) |
| adsb.lol | Real-time | Live global aircraft positions (via `ais-bridge`) |
| Open-Meteo | 45 min | Global wind-vector field |
| data.gov.in | 15 min | CPCB air-quality stations, Agmarknet mandi prices |
| SEC EDGAR / OFAC / World Bank / FRED | On-demand / cached | Business intelligence layer — see above |

Vessels are auto-categorized (Tanker, Cargo/Bulk, Passenger, Fishing, Auxiliary) with route trails and a dead-reckoning predicted-path forecast; markers only redraw when heading or category actually changes.

---

## Weather &amp; Orbital Layers

**Tab: Weather** — temperature, wind speed (animated, backend-rendered), air pressure, and CPCB air-quality overlays; zoomed-in NDVI/SAR/thermal satellite tiles alongside satellite basemaps: a static Sentinel-2 true-colour mosaic (EOX), a fresh **True Color (Live)** Sentinel-2 composite (Earth Engine), and a **High-Res Close-Up** (Esri World Imagery, sub-metre in populated areas — Esri's real coverage varies by location, so the app requests tiles with `blankTile=false` and clamps the maximum zoom when a tile genuinely does not exist). A **Street View** toggle uses Mapillary crowdsourced imagery (coverage is sparse outside actively-mapped areas).

**Tab: Orbital** — a 3D globe (Three.js) with toggleable layers:
* **Satellites** — space stations and the brightest visual-magnitude objects, via TLE propagation (CelesTrak).
* **Aircraft** — live global positions with per-aircraft telemetry (altitude, heading, squawk, signal strength).
* **Vessels** — the same live AIS snapshot as the 2D map, drawn with the same per-category icons and colours (cargo, tanker, ferry, fishing, …), with a detail panel (MMSI, category, speed, course, destination).
* **Atmosphere** — a real, current NASA GIBS true-colour cloud composite in place of the static cloud texture, and real wind vectors (direction, speed, colour) from the same wind-field data the 2D map uses. A snapshot, not animated particle flow; it falls back to the static clouds if the GIBS layer is unreachable.
* **Live GOES panel** — genuinely live NOAA GOES-East (GOES-19) and GOES-West (GOES-18) full-disk imagery, ~10-minute cadence (free, keyless, public-domain). Himawari was deliberately not added: its free real-time feed's terms prohibit commercial use.
* **Close-up handoff** — zooming the globe in far enough hands off to a real 2D map centred on the point you were looking at, and zooming out eases the 3D camera back.

<p align="center">
  <img src="frontend/public/screenshots/orbital-view.jpg" width="800" alt="Orbital globe view with live aircraft tracking" />
  <br /><sub>Orbital tab — live global aircraft positions on a 3D globe, with per-aircraft telemetry.</sub>
</p>

---

## Platform: Theme, Mobile &amp; Landing Page

* **Light/dark theme toggle** (persisted; applied through `--vayu-*` CSS custom properties across the app shell and every panel). The marketing landing page keeps its own dark palette by design.
* **Mobile** — the horizontal Business/Spectra/Agri tool bars are reachable on phones through a dedicated **Tools** tab in the bottom navigation (they are full-screen overlays there), their sub-tab rows scroll horizontally, and the tier picker scrolls on short viewports. A full mobile audit of every control has *not* been done.
* **Landing page** — a real-time WebGL Earth in the hero (day/night terminator, ocean specular, independently rotating clouds, atmosphere glow; static-image fallback on mobile or without WebGL), an interactive 3D model of NASA's Landsat 7, and Home / About / Tiers / Developer / Contact sections with scroll choreography.

---

## Validation &amp; Testing

Credibility is the stated priority, so this section separates what *is* tested from what is not.

**Crop-suitability validation harness (`validation/`).** A held-out-by-design harness: ground truth is built from real district boundaries (Census-2011, datameet) and district-by-season crop statistics (Directorate of Economics &amp; Statistics, 2005–2014, via data.gov.in), covering ~60 districts across 22 states, with **whole states held out** as the test split so spatial autocorrelation cannot leak between tune and test. One command (`python validation/run_all.py --base-url …`) runs a self-test (metrics cross-checked against scikit-learn/SciPy) and every validator. Validator types cover crop ranking (top-1/top-k overlap against a naive baseline), scalar outputs (MAE, RMSE, bias, R², Lin's CCC, …), class areas, confusion matrices (OA, kappa, producer's/user's accuracy), event detection (ROC-AUC, POD, false-alarm rate) and phenology date error. See `validation/README.md` and `validation/ground_truth/SOURCES.md` (which states each source's limitations).

**Engine tests.** `backend/tests/` holds a sanity and invariant suite for the crop-suitability engine (schema validity of every crop profile, paired irrigation tests, constraint/explanation invariants, endpoint fuzzing) and a regression report. These run on approximate regional fixtures and are a **behavioural check, not accuracy evidence**.

**Statistics modules** (`trend_stats.py`, `accuracy_stats.py`) are pure Python and unit-tested against known/hand-worked cases.

**Timelapse and video tests** (`tests/test_timelapse_media.py`, `tests/test_spectra_timelapse.py`) encode real GIF and MP4 files from synthetic frames, check the Earth-Engine-host-only URL guard, period/label maths and error paths, and include a regression guard for a bug that only appears on live Earth Engine (arithmetic on images drops `system:time_start`, which silently emptied the drought index's date filters).

**Not yet done — stated plainly:**
* Spectra tools have **not** been validated against documented real-world events (for example a recorded wildfire for dNBR, a documented drought year for VHI, or a known reservoir filling for the Timelapse). This is the main credibility gap for scientific use.
* The six most recently added Spectra tools have not yet been run against live Earth Engine; their dataset IDs, bands, scale factors and formulas were checked against the Earth Engine catalog and source papers, and their Python logic is mocked-tested.
* No accuracy figure for the crop-suitability engine is claimed in this README; run the harness to produce one.

---

## Architecture

```text
React + Vite Interface (web, and Android via Capacitor)
  |- Satellite queries (NL + AOI) --------------> FastAPI /api/v1/query
  |                                                  |- Groq LLM: intent extraction & narrative synthesis
  |                                                  |- Google Earth Engine: raster computation
  |                                                  `- GeoJSON serialization + tile delivery
  |- Remote sensing tools (direct) --------------> FastAPI /api/v1/remote-sensing
  |                                                  |- 22 Spectra tools -> Google Earth Engine (optical, SAR, lidar, thermal, night lights)
  |                                                  |- Async job queue + exact-match result cache (Redis or in-memory)
  |                                                  `- trend_stats / accuracy_stats (pure-Python statistics)
  |- Business intelligence -----------------------> FastAPI /api/v1/intel
  |                                                  |- SEC EDGAR, OFAC SDN, GDELT, USGS, World Bank, FRED
  |                                                  |- AIS/ADS-B bridge, commodity prices (Yahoo)
  |                                                  `- Economic bloc + strategic site analysis
  |- Agri risk, crop suitability & PDF reports ---> FastAPI /api/v1/agri, /api/v1/report
  |                                                  |- Composite risk scoring (GEE) + GRACE/CHIRPS/Landsat context
  |                                                  |- Crop-suitability engine (soil/climate/terrain, seasonal, calibrated)
  |                                                  |- Phenology, irrigation advisory, groundwater, ML crop extent
  |                                                  `- reportlab-based PDF generation, source-cited
  |- Contact form ---------------------------------> FastAPI /api/v1/auth/contact (SQLite-persisted)
  `- Real-time WebSocket matrix <------------------ FastAPI telemetry engine
                                                     |- Asynchronous background polling daemons
                                                     |- Persistent AISStream WebSocket (via ais-bridge)
                                                     `- Agri alert-engine scheduler (watchlist scanning)
```

---

## Project Structure

```text
Vayu/
├── frontend/                        # React + Vite web UI, packaged for Android via Capacitor
│   └── src/
│       ├── App.jsx                   # Tab/state coordinator, map, layers, tier gating
│       ├── components/               # LandingPage, HeroEarth, SpectraPanel + SpectraIntelBar,
│       │                             #   AgriPanel + AgriIntelBar, BusinessIntelBar, EconomicsView, IntelPanel,
│       │                             #   OrbitalGlobe, GlobeCloseUpMap, LiveGoesPanel, StreetViewPanel, SparkChart, ...
│       ├── hooks/                    # WebSocket/polling abstractions (vessels, aircraft, satellites, wind, intel feed)
│       └── lib/                      # esriZoomGuard (location-aware Esri zoom clamping)
├── backend/
│   ├── app/api/                      # REST + WebSocket routers: query, intel, layers, agri, report,
│   │                                 #   reporting, auth, remote_sensing
│   ├── app/services/
│   │   ├── gee_client.py              # 9 natural-language-driven satellite metrics
│   │   ├── gee_remote_sensing.py      # Spectra toolkit -- the 22 remote-sensing tools
│   │   ├── gee_cache.py               # exact-match result cache for Spectra (Redis or in-memory)
│   │   ├── timelapse_media.py         # timelapse video (GIF/MP4) assembly + frame fetching for PDFs (Earth Engine hosts only)
│   │   ├── trend_stats.py             # Mann-Kendall + Sen's slope
│   │   ├── accuracy_stats.py          # confusion matrix, OA/kappa, producer's/user's accuracy
│   │   ├── report_generator.py        # source-cited PDF reports (Analyze, Agri, Spectra)
│   │   ├── intel/                     # Vessel/aircraft tracking, GDELT/USGS/FIRMS/ACLED ingestion, ACLED CAST forecast,
│   │   │                             #   dark-vessel detection, sanctions, EDGAR exposure, macro, market indices,
│   │   │                             #   chokepoint-market correlation, economic blocs, strategic sites,
│   │   │                             #   supply-chain/business-risk scoring, commodity prices
│   │   ├── agri/                      # Risk scoring, seasonal baseline, region watchlist, alert engine, feedback,
│   │   │                             #   mandi price, WhatsApp bot, crop-suitability engine (crop_requirements,
│   │   │                             #   suitability_engine, evidence, calibration, crop_seasons), phenology,
│   │   │                             #   irrigation advisory, groundwater
│   │   ├── auth/                      # Postgres-backed accounts (built, currently disabled)
│   │   └── reporting/                 # PDF generation, daily executive summary emails
│   ├── app/core/                     # Configuration, logging, in-memory job/vessel storage
│   └── tests/                        # Crop-engine sanity/invariant suite + regression report
├── validation/                      # Held-out validation harness (ground truth, validators, metrics, self-test)
├── docs/                            # Crop-suitability engine write-up, regression and range-review reports
├── ais-bridge/                      # Standalone service: AISStream.io WebSocket + adsb.lol polling
└── docker-compose.yml
```

---

## Deployment &amp; Configuration

Create `backend/.env` (kept out of version control):

```dotenv
ENVIRONMENT=production
LOG_LEVEL=INFO
ALLOWED_ORIGINS_STR=https://your-domain

# Google Earth Engine -- required for Analyze and Spectra
GCP_PROJECT_ID=your-gcp-project-id
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type": "service_account", ...}

# LLM narrative / query parsing
GROQ_API_KEY=your-groq-key

# Conflict events
ACLED_EMAIL=you@example.com
ACLED_PASSWORD=your-acled-password

# AIS/ADS-B bridge
AIS_BRIDGE_URL=https://your-ais-bridge-service
AIS_BRIDGE_API_KEY=your-bridge-shared-secret

# India datasets (data.gov.in) -- powers CPCB AQI + Agmarknet mandi prices
AQI_API_KEY=your-data-gov-in-key

# US macro (FRED) -- optional; business intel degrades to World Bank-only without it
FRED_API_KEY=your-fred-key

# Contact-form/admin viewing endpoints
ADMIN_API_KEY=your-chosen-admin-key
ADMIN_EMAIL=you@example.com

# Spectra result cache -- optional; falls back to an in-memory cache (and silently falls back again if Redis is unreachable)
REDIS_URL=rediss://...

# Historical mandi price trend (CEDA) -- optional
CEDA_API_KEY=your-ceda-key

# Daily executive-summary email -- optional (Resend, or Gmail SMTP)
RESEND_API_KEY=...
SMTP_USER=...
SMTP_PASSWORD=...

# Accounts (built, currently disabled -- see Access Model above)
DATABASE_URL=postgresql://...
```

The frontend additionally uses build-time variables: `VITE_API_URL` (backend base URL), `VITE_OWM_API_KEY` (OpenWeatherMap, free tier, for temperature/pressure tiles) and `VITE_MAPILLARY_ACCESS_TOKEN` (free Mapillary token for Street View; without it the toggle shows a clear "token missing" message).

For local Earth Engine authentication:

```bash
earthengine authenticate
earthengine set_project YOUR_PROJECT_ID
```

### Local Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install --upgrade -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# Frontend (separate terminal)
cd frontend
npm ci
npm run dev
```

Navigate to `http://localhost:5173`. Full OpenAPI schema at `http://localhost:8000/docs`.

**AIS bridge** (optional, for live vessel/aircraft tracking) — see `ais-bridge/README.md`. Deployed as a standalone service since AISStream.io permits one WebSocket connection per API key and needs a non-rate-limited IP range.

### Docker Compose

```bash
docker compose up --build -d
```

---

## API Reference

All routes are under `/api/v1`. The full, authoritative OpenAPI schema is served at `/docs`.

**Analysis and Spectra**

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/query` | Natural-language satellite analysis job over an AOI |
| `GET` | `/query/{id}` &middot; `/query/{id}/status` | Poll/retrieve a query job |
| `GET` | `/metrics` | List the supported Analyze-tab metrics |
| `POST` | `/remote-sensing/analyze` | Run a Spectra tool directly (async job, `202`); `tool` is one of the 22 tools below |
| `GET` | `/remote-sensing/analyze/{id}` | Poll/retrieve a Spectra job |
| `GET` | `/remote-sensing/analyze/{id}/video?format=mp4\|gif&fps=4` | Download a finished **timelapse** as an MP4 or GIF with period labels burned in (assembled from the job's stored frame images; `404` if the result has expired, `422` if the Earth Engine frame links have expired) |
| `GET` | `/remote-sensing/tools` | List available Spectra tools |
| `POST` | `/report/analysis` &middot; `/report/agri-risk` &middot; `/report/remote-sensing` | Source-cited PDF generation (Analyze metrics / agri risk / any Spectra result) |
| `GET` | `/layers/{layer_key}` | Cached satellite basemap tile URL |

Spectra `tool` values: `spectral_indices`, `spectral_composites`, `terrain`, `lulc`, `dynamic_world`, `snow_cover`, `change_detection`, `burn_severity`, `timelapse` (`frequency`: `yearly` \| `quarterly` \| `monthly`; `layer`: `natural_color` \| `ndvi` \| `night_lights` \| `no2` \| `sar_vv` \| `soil_moisture`), `gedi_forest_structure`, `water_quality`, `sar_backscatter`, `flood_mapping`, `surface_water_dynamics`, `soil_moisture`, `land_surface_temperature`, `vegetation_drought_index`, `night_lights`, `atmospheric_composition`, `index_time_series`, `supervised_classification`, `accuracy_assessment`.

**Agriculture** (`/agri`)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/risk-score` &middot; `/baseline` &middot; `/drought-dashboard` | Composite risk score / seasonal NDVI baseline / drought dashboard |
| `POST` | `/crop-suitability` | Ranked, seasonal crop suitability for a point/AOI, with optional soil-test overrides and a mandi-based revenue estimate |
| `POST` | `/phenology` &middot; `/irrigation-advisory` &middot; `/groundwater-trend` &middot; `/ndvi-trend` &middot; `/crop-extent` | Crop stage / irrigation advisory / groundwater trend / NDVI trend / ML crop extent |
| `GET/POST` | `/regions` &middot; `DELETE /regions/{id}` | Watchlisted regions |
| `GET` | `/regions/{id}/alerts` &middot; `/alerts` &middot; `/rollup` | Alert history; officer/district role views |
| `POST` | `/feedback` &middot; `GET /feedback/accuracy` | Feedback loop |
| `GET` | `/mandi-price` | Market price overlay |
| `GET` | `/whatsapp/info` &middot; `POST /whatsapp/webhook` | WhatsApp bot |

**Intelligence** (`/intel`)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/events` &middot; `/events/aoi` &middot; `WS /ws` &middot; `/sources` &middot; `/stats` | Historical / live multi-source intel feed |
| `GET` | `/vessels` &middot; `/vessels/chokepoints` &middot; `/vessels/stats` &middot; `/aircraft` &middot; `/aircraft/stats` &middot; `/satellites/tle` | Live vessel / aircraft / satellite tracking |
| `GET` | `/business-risk` &middot; `/dark-vessels` &middot; `/sanctions-screen` &middot; `/seismic-disruption` | Chokepoint risk score and its components |
| `GET` | `/edgar-exposure/{chokepoint}` &middot; `/geo-tone` | SEC filings; GDELT regional sentiment |
| `GET` | `/macro` &middot; `/macro/history` &middot; `/macro/history/worldbank` | US/Global/India macro, current + historical |
| `GET` | `/commodities` &middot; `/commodities/history` &middot; `/markets` &middot; `/markets/history` | Commodity prices; stock indices and bellwether equities |
| `GET` | `/chokepoint-traffic-history` &middot; `/chokepoint-market-correlation` &middot; `/supply-chain-correlation` | Traffic history; traffic-anomaly vs market moves; chokepoint vs commodity moves |
| `GET` | `/conflict-forecast` | ACLED CAST monthly political-violence forecast per country |
| `GET` | `/strategic-sites` | Ports/refineries/mines with live signals + news |
| `GET` | `/economic-blocs` and `/{id}/macro` &#124; `/tone` &#124; `/commodities` &#124; `/exposure` | G7/G20/BRICS/ASEAN analysis |
| `GET` | `/wind-field` &middot; `/air-quality` &middot; `/air-quality/cpcb-stations` | Wind vector grid; air quality |

**Other:** `POST /auth/contact` (contact form, SQLite-persisted), `POST /subscribe` and `GET /unsubscribe` (daily executive summary), `GET /auth/contact/messages` and `/auth/admin/users` (admin, `X-Admin-Key` header). The `/auth/signup`, `/login`, `/logout`, `/forgot-password` and `/reset-password` routes belong to the disabled account system.

---

## Data Source Attribution

Every dataset used across the terminal, for reference:

**Satellite / Earth observation (via Google Earth Engine):** Sentinel-1 SAR GRD, Sentinel-2 SR Harmonized, Sentinel-5P TROPOMI, Landsat 5/7/8/9 Collection 2, MODIS (MOD13Q1 NDVI, MOD11A2 LST, Burned Area, Active Fire), VIIRS Day/Night Band monthly composites, GEDI L2A canopy height and L4A biomass, Hansen Global Forest Change, NASA SMAP L4, Google Dynamic World V1, JRC Global Surface Water v1.4, Copernicus DEM GLO-30, ESA WorldCover v200.

**Basemaps and live imagery:** EOX Sentinel-2 mosaic, Esri World Imagery, NASA GIBS (VIIRS true-colour clouds), NOAA GOES-East/West, Mapillary (crowdsourced street-level), NASA Blue Marble textures and NASA's public-domain Landsat 7 3D model.

**Weather/climate/agronomy:** Open-Meteo, GRACE (groundwater), CHIRPS (precipitation), ERA5-Land (temperature), OpenLandMap (soil), GFSAD1000 (irrigated cropland), FAO EcoCrop (crop requirement ranges), Directorate of Economics &amp; Statistics crop statistics and datameet Census-2011 district boundaries (validation ground truth).

**Business/markets:** Yahoo Finance (commodity prices), FRED (US macro), World Bank (global/country macro), SEC EDGAR (full-text filing search), US Treasury OFAC SDN list, AISStream.io (AIS), adsb.lol (ADS-B).

**News/events:** GDELT GKG, ACLED (event feed and CAST forecast), USGS, NASA FIRMS.

**India-specific:** data.gov.in (CPCB air quality, Agmarknet mandi prices), CEDA (historical mandi price trends).

Every non-satellite result in the app states its source dataset in a `method` field in the API response and, where surfaced, in the UI itself.

---

## Known Constraints

> [!IMPORTANT]
> * **State volatility:** background jobs and in-memory caches (vessel positions, intel events) reset on restart. Time-series data (chokepoint traffic, contact messages) is SQLite-persisted; account data is Postgres-backed when enabled.
> * **Telemetry variance:** satellite layers depend on cloud cover and orbital pass timing — coverage varies by season/AOI, and report captions disclose the actual achieved window/coverage rather than assuming a fixed composite.
> * **Commodity-relevance mapping is editorial**, not API-sourced — every entry states its reasoning so it can be reviewed, not treated as fact.
> * **Sanctions screening is name-only** (AIS provides MMSI+name, not IMO number) — a lead to verify, not a confirmed hit.
> * **No official free RBI data API exists** — India's repo rate is deliberately not included pending one; every third-party "RBI API" found is an unofficial paid scraper.
> * **Crop-agnostic agri risk score** — the composite risk score does not account for crop-specific growth stage or water requirements; read as a general land-condition signal for the AOI, not a per-parcel diagnosis. Crop-specific guidance comes from the separate crop-suitability, crop-stage and irrigation tools, each with its own stated limits.
* **Crop-suitability scores are not yet calibrated or accuracy-validated** — calibration is the identity until fitted, `confidence` is a completeness heuristic, and its validation is district-level against observed crop area (which reflects irrigation, prices and policy as well as suitability); see the harness's stated limitations.
* **Spectra validation is outstanding** — see [Validation &amp; Testing](#validation--testing). Proxy tools (Water Quality, Night Lights) are labelled as proxies; GEDI results are footprint statistics, not area totals.
* **Earth Engine dependence and quotas** — all satellite analysis runs on Google Earth Engine, so availability and quotas are Google's; very large AOIs at fine resolution can exceed GeoTIFF export limits (the download link is then omitted).
* **Timelapse videos are built on demand** from Earth Engine frame links that may expire, so a very old result may need re-running before its video or PDF contact sheet can be built; encoding runs on the backend (bundled ffmpeg for MP4) and large timelapses add memory and bandwidth load on a free-tier host.
* **Hosting limits** — the free-tier backend's monthly bandwidth cap can suspend the service; results are cached to reduce repeated Earth Engine work, but a cold start or suspension is visible to users.
> * **Resolution mismatch in the agri regional-context layer** — groundwater (GRACE, ~300 km grid) and rainfall (CHIRPS, ~5.5 km grid) are coarser/slower than the three scored risk indicators, hence reported separately from the composite score.

---

## Licensing

*Retained under private internal domain boundaries until explicit licensing assignment.*
