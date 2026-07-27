# VAYU V2 — Intel Feed Integration Guide

## Files Delivered

```
backend/app/
├── main.py                          ← REPLACE existing main.py
├── core/
│   └── config.py                    ← REPLACE existing config.py
├── services/intel/
│   ├── __init__.py                  ← CREATE (empty file)
│   ├── fetchers.py                  ← NEW — USGS, FIRMS, GDELT, ACLED
│   ├── store.py                     ← NEW — in-memory event store
│   └── scheduler.py                 ← NEW — background polling
└── api/
    └── intel_endpoints.py           ← NEW — REST + WebSocket

frontend/src/
├── hooks/
│   └── useIntelFeed.js              ← NEW — WebSocket hook
└── components/
    └── IntelPanel.jsx               ← NEW — live feed panel
```

---

## Step 1 — Backend Setup

### 1a. Create the __init__.py file
In `backend/app/services/intel/`, create an empty file named `__init__.py`.
This makes it a Python package.

### 1b. Add dependencies to requirements.txt
Add these lines:
```
httpx==0.27.2
websockets==13.1
```
httpx is likely already there. websockets is needed for FastAPI WebSocket support.

### 1c. Add to .env (optional — only for ACLED)
```
ACLED_EMAIL=your@email.com
ACLED_KEY=your_acled_key
```
USGS, FIRMS, and GDELT work immediately with no credentials.

### 1d. Replace files
- Replace `backend/app/main.py` with the new main.py
- Replace `backend/app/core/config.py` with the new config.py
- Copy the entire `backend/app/services/intel/` folder
- Copy `backend/app/api/intel_endpoints.py`

### 1e. Test locally
```bash
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload --port 8000
```

Check:
- http://localhost:8000/api/v1/intel/events      → should return events within 60s
- http://localhost:8000/api/v1/intel/stats       → store statistics
- http://localhost:8000/api/v1/intel/sources     → source status
- http://localhost:8000/health                   → intel_events count

---

## Step 2 — Frontend Integration

### 2a. Install no new packages
useIntelFeed.js uses only native browser WebSocket API.
IntelPanel.jsx uses only React.

### 2b. Create folders
```
frontend/src/hooks/
frontend/src/components/
```

### 2c. Copy files
- Copy `useIntelFeed.js` into `frontend/src/hooks/`
- Copy `IntelPanel.jsx` into `frontend/src/components/`

### 2d. Wire IntelPanel into App.jsx
Replace the static right panel in App.jsx with:

```jsx
import IntelPanel from "./components/IntelPanel";

// In your JSX, where the right panel currently is:
<IntelPanel
  apiUrl={import.meta.env.VITE_API_URL || "http://localhost:8000"}
  aoi={drawnAOI}
  onEventClick={(event) => {
    // Optional: fly map to event location
    if (mapRef.current) {
      mapRef.current.setView([event.lat, event.lon], 8);
    }
  }}
/>
```

---

## Step 3 — Deploy to Render

Push all changes:
```bash
git add .
git commit -m "feat: add real-time intel feed (USGS, FIRMS, GDELT)"
git push
```

Render auto-redeploys. No new environment variables needed for the
three free sources (USGS, FIRMS, GDELT).

Optional — add to Render env vars for ACLED:
```
ACLED_EMAIL = your@email.com
ACLED_KEY   = your_key
```

---

## Step 4 — Verify Live Feed

After deploy, test WebSocket connection:
```javascript
// In browser console on your Vercel frontend:
const ws = new WebSocket("wss://vayu-0zxv.onrender.com/api/v1/intel/ws");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```
You should see a snapshot message immediately, then live events as they arrive.

---

## What's Live After This

| Source      | Data                          | Update Freq | Auth Required |
|-------------|-------------------------------|-------------|---------------|
| USGS        | Earthquakes M3.5+, global     | 5 min       | No            |
| NASA FIRMS  | Active fire hotspots, global  | 15 min      | No            |
| GDELT       | Geolocated news events        | 10 min      | No            |
| ACLED       | Armed conflict events         | 60 min      | Free key      |

---

## Next Sources to Add (future sprints)

| Source    | What                    | Notes                              |
|-----------|-------------------------|------------------------------------|
| OpenSky   | Real-time aircraft      | No auth, add to fetchers.py        |
| AISHub    | Vessel positions        | Free registration needed           |
| GDACS     | Major disaster alerts   | RSS feed, very easy to add         |
| ISRO Bhuvan | Indian satellite data | Registration + API key needed      |
