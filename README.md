# VAYU — Geospatial Intelligence Terminal

---

> **Operational Notice**
> VAYU is an advanced web-based earth-observation and open-source intelligence (OSINT) terminal. It synthesizes **on-demand satellite analysis** over user-defined areas of interest (AOI) with a continuously streaming telemetry matrix tracking seismic events, thermal hotspots, news-derived geolocations, structural conflicts, and maritime logistics.
> *This terminal is engineered for deep situational awareness and strategic exploratory analysis.*

---

## Capabilities & Core Intelligence

### Satellite Earth Observation

1. **Spatial Definition:** Define an arbitrary GeoJSON Polygon or MultiPolygon AOI directly onto the interactive canvas.
2. **Natural Language Translation:** Input unstructured analytical queries (e.g., `Show vegetation loss in this area from 2020-01-01 to 2024-01-01`).
3. **Automated Computation:** An LLM-orchestrated pipeline parses the request into discrete spatial metrics and temporal windows, triggers Google Earth Engine (GEE) computations, generates change masks, and yields structural metrics alongside localized map tiles.

| Analytical Metric | Primary Earth Observation Dataset | Strategic Application |
| --- | --- | --- |
| **Vegetation Dynamics** | Sentinel-2 NDVI | Automated canopy and green-cover variance screening |
| **Urban Expansion** | Dynamic World | High-resolution built-up growth and land-use mapping |
| **Hydrological Variance** | JRC Global Surface Water | Long-term surface-water persistence and depletion tracking |
| **Radar Flood Detection** | Sentinel-1 SAR | Synthetic Aperture Radar flood extent mapping through heavy cloud cover |
| **Thermal & Burn Scarring** | MODIS Burned Area / Active Fire | Macro-scale historical burn footprint and thermal event analysis |
| **Vegetative Stress** | Sentinel-2 NDDI / VCI | Multi-spectral crop health and severe drought screening |
| **Thermal Profiling** | Landsat 8/9 TIRS | Surface kinetic temperature distribution and heat-island metrics |
| **Sylvatic Depletion** | Hansen Global Forest Change | Micro-granular multi-year tree-cover loss tracking |
| **Subsurface Hydrology** | SMAP L3 | Radiometer-derived surface-soil-moisture variance |

---

### Real-Time Intelligence Feeds

At system initialization, an asynchronous processing scheduler establishes persistent telemetry loops, fetching, deduplicating, and broadcasting events downstream to connected terminal displays via robust WebSockets.

| Data Pipeline | Integration Architecture | Polling Interval | Synthesized Intelligence |
| --- | --- | --- | --- |
| **USGS** | Native Telemetry Core | 5 Minutes | Global seismic event tracking and magnitude vectors |
| **NASA FIRMS** | Native Telemetry Core | 15 Minutes | High-confidence VIIRS active thermal hotspots |
| **GDELT GKG** | Native Telemetry Core | 10 Minutes | Multi-lingual, geolocated semantic news events |
| **ACLED** | Authenticated Integration | 60 Minutes | Political violence, protest matrices, and regional conflict |
| **AISStream.io** | Secure Stream Broker | Persistent / Real-Time | Live vessel positions in globally critical maritime chokepoints |

> **Maritime Monitoring Specifications**
> The tracking sub-system isolates unique MMSI transponders to map logistical flows across global shipping routes. Vessels are categorized automatically into *Tanker, Cargo/Bulk, Passenger, Fishing,* or *Auxiliary* layers. This system tracks global energy and commodity flows through narrow maritime corridors.

---

## Architectural Topology

```text
React + Vite Enterprise Interface
  ├─ Submits AOI & Natural Language Queries ──> FastAPI Core Gateway (/api/v1/query)
  │                                               ├─ Groq LLM: Intention extraction & insights
  │                                               ├─ Google Earth Engine: Distributed raster computation
  │                                               └─ GeoJSON Serialization + GCS Tile Delivery
  └─ Real-Time WebSocket Matrix <─────────────── FastAPI Telemetry Engine
                                                  ├─ Asynchronous background polling daemons
                                                  └─ Persistent AISStream WebSocket connection

```

### Enterprise Technology Stack

---

## Project Structure

```text
Vayu/
├── frontend/                 # High-performance analytical UI and mapping canvas
│   └── src/
│       ├── App.jsx            # State coordinator, spatial layers, and visual matrix
│       ├── components/        # Real-time intelligence and monitoring readouts
│       └── hooks/             # WebSocket abstractions and telemetry sync hooks
├── backend/
│   ├── app/api/               # RESTful interfaces and secure stream gateways
│   ├── app/services/          # Compute connectors (GEE, Groq, ACLED, AIS)
│   ├── app/core/              # Immutable configurations, logging, and job storage
│   └── geojson_outputs/       # Local fallback air-gapped storage cache
└── docker-compose.yml

```

---

## Deployment & Configuration

### Prerequisites

* **Runtime Frameworks:** Node.js 20+ and Python 3.11+ for standard local setups.
* **Cloud Infrastructure:** Google Cloud Platform project with active Google Earth Engine API access.
* **Credentials:** Valid Groq API Token, alongside optional upstream data tokens (ACLED, AISStream).

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
AISSTREAM_API_KEY=ais_stream_production_token

```

To configure local workstation context for Google Earth Engine run:

```bash
earthengine authenticate
earthengine set_project YOUR_PROJECT_ID

```

---

## Execution Manual

### Standard Local Deployment

**Phase 1: Spin up the High-Performance API Gateway**

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

### Enterprise Orchestration via Docker Compose

To stand up the complete network layer in a sealed container topology:

```bash
docker compose up --build -d

```

---

## API Blueprint

All system interactions route through the `/api/v1` namespace.

| Verb | Path | Expected Payload / Response | Protocol Purpose |
| --- | --- | --- | --- |
| `POST` | `/query` | `{"text": string, "aoi_geojson": object}` | Dispatches non-blocking analytic job; yields tracking token |
| `GET` | `/query/{id}/status` | Status Metadata | Checks long-running compute jobs |
| `GET` | `/query/{id}` | Computed Analysis Matrix | Returns the completed data payload or failure summary |
| `GET` | `/intel/events` | Event Vector Set | Fetches paginated, multi-source historical intelligence entries |
| `WS` | `/intel/ws` | Event Broadcast Pipe | Persistent stream for real-time edge alerts |
| `GET` | `/intel/vessels` | Maritime Spatial State | State-of-the-art live global shipping positions |

---

## Strategic Operational Constraints

> [!IMPORTANT]
> * **State Volatility:** Current running states, background jobs, and maritime matrices operate entirely **in-memory**. Restarts wipe these caches. For distributed staging environments, state synchronization layers should be backed by Redis or PostgreSQL.
> * **Spatial Clustering:** The internal data broker caps storage at 2,000 global events inside a rolling 24-hour window. Spatiotemporal deduplication is computed using an 11 km grid boundary.
> * **Telemetry Variance:** Remote sensing layers depend strongly on clear orbital paths and cloud-cover conditions. Earth observation artifacts should be verified across multiple spectrums and verified using ground-truth methods.
> 
> 

---

## Hardening Recommendations

To scale this terminal architecture for operational deployment, implement the following infrastructure changes:

* **Persistent States:** Replace volatile in-memory storage arrays with a robust PostgreSQL instance fitted with the PostGIS spatial engine. Offload computational workflows to dedicated Celery worker nodes.
* **Edge Security:** Enforce Strict Transport Security (HSTS), implement mutual TLS (mTLS) for system integrations, and deploy Web Application Firewalls (WAF) backed by strict rate limits.
* **Data Lineage:** Integrate strict data-provenance logging to track intelligence inputs back to primary raw formats.

---

### Licensing

*Operational baseline architecture. Retained under private internal domain boundaries until explicit licensing assignment.*
