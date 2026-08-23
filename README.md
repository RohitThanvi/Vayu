# VAYU — Geospatial Intelligence Terminal

---

> **Operational Notice**
> VAYU is an advanced web-based earth-observation and open-source intelligence (OSINT) terminal. It synthesizes **on-demand satellite analysis** over user-defined areas of interest (AOI) with a continuously streaming telemetry matrix tracking seismic events, thermal hotspots, news-derived geolocations, structural conflicts, and maritime logistics. A dedicated **agricultural risk module** adds a composite scoring engine, region watchlists with a farmer/officer feedback loop, and scientific PDF report generation on top of the same satellite pipeline.
> *This terminal is engineered for deep situational awareness and strategic exploratory analysis.*

---

## Capabilities & Core Intelligence

### Satellite Earth Observation

1. **Spatial Definition:** Define an arbitrary GeoJSON Polygon or MultiPolygon AOI directly on the interactive canvas, or search a place by name (results also become the AOI's display name in reports).
2. **Natural Language Translation:** Input unstructured analytical queries (e.g., `Show vegetation loss in this area from 2020-01-01 to 2024-01-01`).
3. **Automated Computation:** An LLM-orchestrated pipeline parses the request into discrete spatial metrics and temporal windows, triggers Google Earth Engine (GEE) computations, generates change masks, and yields structural metrics alongside localized map tiles.
4. **Toggleable satellite basemap layers** (independent of the query pipeline): true color, NDVI vegetation, SAR/microwave, and thermal/IR — each backed by a cached GEE tile endpoint.

| Analytical Metric | Primary Earth Observation Dataset | Strategic Application |
| --- | --- | --- |
| **Vegetation Dynamics** | Sentinel-2 SR Harmonized (NDVI) | Automated canopy and green-cover variance screening |
| **Urban Expansion** | Google Dynamic World V1 | Built-up growth and land-use change, with a gain/loss/unchanged change map |
| **Hydrological Variance** | JRC Global Surface Water | Long-term surface-water persistence and depletion tracking, with a gain/loss/unchanged change map |
| **Radar Flood Detection** | Sentinel-1 SAR GRD + JRC GSW + SRTM DEM | SAR flood-extent mapping through cloud cover, with permanent-water exclusion and steep-terrain exclusion |
| **Thermal & Burn Scarring** | MODIS Burned Area / Active Fire | Burned-footprint and thermal-event detection over the period |
| **Vegetative Stress** | Sentinel-2 NDDI | Drought-stress screening, area-normalized against the AOI's actual size |
| **Thermal Profiling** | Landsat 8/9 TIRS (Collection 2) | Surface temperature distribution and urban-heat-island detection |
| **Sylvatic Depletion** | Hansen Global Forest Change | Multi-year tree-cover loss tracking |
| **Subsurface Hydrology** | NASA SMAP L4 | Soil-moisture variance, area-normalized against the AOI's actual size |

Each of the 9 analysis types above can be exported as a source-cited, methodology-documented PDF report — see **Scientific PDF Reporting** below.

---

### Agricultural Risk Module

A composite risk-scoring layer built on top of the satellite pipeline, purpose-built for repeat monitoring of specific regions rather than one-off queries.

* **Composite 0–100 risk score** combining three satellite indicators — drought (NDDI), vegetation decline (NDVI threshold change), and soil moisture deficit (SMAP) — as a weighted average (40/35/25%), automatically renormalized if any indicator is unavailable for a given run.
* **Regional environmental context** — groundwater trend (GRACE), rainfall anomaly vs. a 10-year seasonal normal (CHIRPS), and land surface temperature (Landsat) — reported separately from the composite score, since each has a different spatial resolution/timescale than the three scored indicators.
* **5-year seasonal NDVI baseline** — flags whether current conditions are unusual for that specific time of year at that specific location, independent of the composite score.
* **Region watchlist** with a farmer/officer feedback loop on past alerts — confidence blends data completeness with the region's actual historical alert accuracy once enough feedback has accumulated.
* **Automated alert engine** — background scheduler scans watchlisted regions and pushes risk alerts.
* **WhatsApp bot integration** for last-mile alert delivery.
* **Mandi (market) price** and **groundwater trend** endpoints as additional overlays for a watchlisted region.
* **Rollup views** for aggregated multi-region reporting.

---

### Scientific PDF Reporting

Every analysis type — the 9 satellite analyses and the agricultural risk score — can be exported as a structured PDF report, not just raw numbers:

* Executive summary, study-area geometry, full methodology with a worked numeric example, indicator definitions and thresholds, results table, findings & interpretation, data-quality/confidence section, recommendations, glossary, full dataset citations, and stated limitations.
* **Satellite imagery panels** (true color, NDVI, NDDI, SAR, thermal, SMAP as applicable) with proper color legends, and — for vegetation, built-up, and water change reports — a dedicated **gain/loss/unchanged change map** with its own discrete legend, so the spatial pattern of change is visible directly rather than left for the reader to diff two images by eye.
* Imagery captions disclose what was actually achievable for that run (the cloud-free window actually used, and the % of the AOI with valid pixels) rather than a fixed claim, since coverage genuinely varies by season and AOI.
* An optional LLM-generated narrative synthesis, explicitly grounded in the same hedged, deterministic findings text shown elsewhere in the report — kept from asserting more certainty than the underlying satellite classification actually supports (e.g., a land-cover class change is disclosed as exactly that, not asserted as confirmed construction or demolition).
* Full source citations for every dataset used in a given report.

---

### Real-Time Intelligence Feeds

At system initialization, an asynchronous processing scheduler establishes persistent telemetry loops, fetching, deduplicating, and broadcasting events downstream to connected terminal displays via WebSockets.

| Data Pipeline | Integration Architecture | Polling Interval | Synthesized Intelligence |
| --- | --- | --- | --- |
| **USGS** | Native Telemetry Core | 5 Minutes | Global seismic event tracking and magnitude vectors |
| **NASA FIRMS** | Native Telemetry Core | 15 Minutes | High-confidence VIIRS active thermal hotspots |
| **GDELT GKG** | Native Telemetry Core | 10 Minutes | Multi-lingual, geolocated semantic news events |
| **ACLED** | Authenticated Integration | 60 Minutes | Political violence, protest matrices, and regional conflict |
| **AISStream.io** | Secure Stream Broker (via a dedicated `ais-bridge` service) | Persistent / Real-Time | Live vessel positions in globally critical maritime chokepoints |
| **Open-Meteo** | Native Telemetry Core | 45 Minutes | Global wind-vector field, rendered as an animated overlay |

> **Maritime Monitoring Specifications**
> The tracking sub-system isolates unique MMSI transponders to map logistical flows across global shipping routes. Vessels are categorized automatically into *Tanker, Cargo/Bulk, Passenger, Fishing,* or *Auxiliary* layers, with route trails and a dead-reckoning predicted-path forecast. The frontend caps live intel markers and only redraws a vessel's icon when its heading or category actually changes, so the map stays responsive over long sessions.

---

## Architectural Topology

```text
React + Vite Interface (web, and Android via Capacitor)
  ├─ Submits AOI & Natural Language Queries ──> FastAPI Core Gateway (/api/v1/query)
  │                                               ├─ Groq LLM: Intention extraction & insights
  │                                               ├─ Google Earth Engine: Distributed raster computation
  │                                               └─ GeoJSON Serialization + GCS Tile Delivery
  ├─ Agri risk scoring & PDF reports ──────────> FastAPI Agri + Report Gateways (/api/v1/agri, /api/v1/report)
  │                                               ├─ Composite risk scoring (GEE) + regional context (GRACE/CHIRPS/Landsat)
  │                                               └─ reportlab-based PDF generation, source-cited
  └─ Real-Time WebSocket Matrix <─────────────── FastAPI Telemetry Engine
                                                  ├─ Asynchronous background polling daemons
                                                  ├─ Persistent AISStream WebSocket connection (via ais-bridge)
                                                  └─ Agri alert-engine scheduler (watchlist scanning)

```

---

## Project Structure

```text
Vayu/
├── frontend/                  # React + Vite web UI, also packaged for Android via Capacitor
│   └── src/
│       ├── App.jsx             # State coordinator, spatial layers, map, and visual matrix
│       ├── components/         # Panels: query results, intel feed, agri risk, satellite/weather toggles
│       └── hooks/               # WebSocket / polling abstractions (intel feed, vessel tracker)
├── backend/
│   ├── app/api/                # REST + WebSocket routers: query, intel, layers, agri, report
│   ├── app/services/            # Compute connectors (GEE, Groq LLM, satellite imagery, PDF report generator)
│   │   └── agri/                 # Risk scoring, seasonal baseline, groundwater/precipitation context,
│   │                              #   region watchlist, alert engine, feedback, mandi price, WhatsApp bot
│   ├── app/core/                 # Configuration, logging, in-memory job/vessel storage
│   └── geojson_outputs/          # Local fallback storage cache
├── ais-bridge/                 # Standalone service holding the single AISStream.io WebSocket connection
└── docker-compose.yml

```

---

## Deployment & Configuration

### Prerequisites

* **Runtime Frameworks:** Node.js 20+ and Python 3.11+ for standard local setups.
* **Cloud Infrastructure:** Google Cloud Platform project with active Google Earth Engine API access.
* **Credentials:** Valid Groq API Token, alongside optional upstream data tokens (ACLED, AISStream, OpenWeatherMap).

### Environment Architecture

Instantiate an isolated environment configuration file at `backend/.env`. *Ensure this file remains out of version control systems.*

```dotenv
ENVIRONMENT=production
LOG_LEVEL=INFO
ALLOWED_ORIGINS_STR=https://your-secure-domain.internal

# Google Cloud Platform & Earth Engine Configuration
GCP_PROJECT_ID=your-gcp-enterprise-project-id
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type": "service_account", ...}

# Core Analytical & Intelligence API Access
GROQ_API_KEY=gsk_secure_production_llm_key
ACLED_EMAIL=intelligence@organization.org
ACLED_PASSWORD=secure_operational_credential
AIS_BRIDGE_URL=https://your-ais-bridge-service
AIS_BRIDGE_API_KEY=bridge_shared_secret_token

```

The frontend additionally uses `VITE_OWM_API_KEY` (OpenWeatherMap, free tier) for the Temperature/Air Pressure overlay tiles, set at the Vite build environment.

To configure local workstation context for Google Earth Engine run:

```bash
earthengine authenticate
earthengine set_project YOUR_PROJECT_ID

```

---

## Execution Manual

### Standard Local Deployment

**Phase 1: Spin up the API Gateway**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Or Windows equivalent: .\.venv\Scripts\Activate.ps1
pip install --upgrade -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

```

**Phase 2: Launch the Terminal Canvas**

```bash
cd frontend
npm ci
npm run dev

```

Navigate to the localized endpoint (typically `http://localhost:5173`). Full OpenAPI technical schema specifications are accessible via `http://localhost:8000/docs`.

**Optional: AIS bridge**, if live vessel tracking is needed — see `ais-bridge/README.md`. Deployed as a standalone service since AISStream.io only permits one WebSocket connection per API key, and needs to run from an IP range the provider hasn't rate-limited.

### Enterprise Orchestration via Docker Compose

To stand up the complete network layer in a sealed container topology:

```bash
docker compose up --build -d

```

---

## API Blueprint

All system interactions route through the `/api/v1` namespace.

| Verb | Path | Purpose |
| --- | --- | --- |
| `POST` | `/query` | Dispatches a non-blocking natural-language analytic job over an AOI; yields a tracking token |
| `GET` | `/query/{id}/status` | Checks a long-running compute job |
| `GET` | `/query/{id}` | Returns the completed analysis payload or failure summary |
| `GET` | `/intel/events` | Fetches paginated, multi-source historical intelligence entries |
| `WS` | `/intel/ws` | Persistent stream for real-time intel alerts |
| `GET` | `/intel/vessels` | Live global vessel positions |
| `GET` | `/layers/{layer_key}` | Cached tile URL for a toggleable satellite basemap layer |
| `POST` | `/report/analysis` | Generates a source-cited PDF for one of the 9 satellite analysis types |
| `POST` | `/report/agri-risk` | Generates the agricultural risk-assessment PDF for an AOI |
| `POST` | `/agri/risk-score` | Composite 0–100 agricultural risk score for an AOI |
| `POST` | `/agri/baseline` | 5-year seasonal NDVI baseline comparison for an AOI |
| `GET/POST` | `/agri/regions` | List/create watchlisted regions |
| `GET` | `/agri/regions/{region_id}/alerts` | Alert history for a watchlisted region |
| `GET` | `/agri/alerts` | All recent agri alerts |
| `POST` | `/agri/feedback` | Farmer/officer feedback on a past alert |
| `GET` | `/agri/feedback/accuracy` | A region's historical alert-accuracy track record |
| `GET` | `/agri/rollup` | Aggregated multi-region summary |
| `GET` | `/agri/mandi-price` | Nearby market (mandi) price overlay for a region |
| `GET` | `/agri/groundwater-trend` | GRACE-based groundwater trend for a region |
| `POST` | `/agri/whatsapp/webhook` | WhatsApp bot webhook for alert delivery |

---

## Strategic Operational Constraints

> [!IMPORTANT]
> * **State Volatility:** Current running states, background jobs, and maritime matrices operate entirely **in-memory**. Restarts wipe these caches. For distributed staging environments, state synchronization layers should be backed by Redis or PostgreSQL.
> * **Spatial Clustering:** The internal data broker caps storage at 2,000 global events inside a rolling 24-hour window. Spatiotemporal deduplication is computed using an 11 km grid boundary.
> * **Telemetry Variance:** Remote sensing layers depend strongly on clear orbital paths and cloud-cover conditions — coverage genuinely varies by season and AOI, and report imagery captions disclose the actual window/coverage achieved rather than assuming a fixed clean composite. Earth observation artifacts should be verified across multiple spectrums and with ground-truth methods.
> * **Resolution mismatch in the agri regional-context layer:** groundwater (GRACE, ~300 km grid) and rainfall (CHIRPS, ~5.5 km grid) operate at coarser resolution and slower timescales than the three scored risk indicators, which is why they're reported as separate context rather than folded into the composite score.
> * **Crop-agnostic scoring:** the agri risk model does not currently account for crop-specific growth stage or water requirements, and should be read as a general land-condition signal for the AOI as a whole, not a per-parcel or per-crop diagnosis.

---

## Hardening Recommendations

To scale this terminal architecture for operational deployment, implement the following infrastructure changes:

* **Persistent States:** Replace volatile in-memory storage arrays with a robust PostgreSQL instance fitted with the PostGIS spatial engine. Offload computational workflows to dedicated Celery worker nodes.
* **Edge Security:** Enforce Strict Transport Security (HSTS), implement mutual TLS (mTLS) for system integrations, and deploy Web Application Firewalls (WAF) backed by strict rate limits.
* **Data Lineage:** Integrate strict data-provenance logging to track intelligence inputs back to primary raw formats.

---

### Licensing

*Operational baseline architecture. Retained under private internal domain boundaries until explicit licensing assignment.*
