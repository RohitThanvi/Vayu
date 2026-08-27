# Vayu Network Bridge (AIS + OpenSky)

Holds two outbound connections that get blocked from Render's datacenter
IP range — AISStream.io's WebSocket and OpenSky's REST snapshot — and
re-exposes both as plain REST endpoints the main Vayu backend polls.

**Why this exists:** AISStream is WebSocket-only and allows exactly one
live connection per API key. When the main backend held that connection
directly on Render, every connection attempt got rejected with HTTP 429
at the handshake — before AISStream even reads the API key — and
regenerating the key didn't help. That points at Render's shared
outbound IP pool getting blocked, not anything about the key or the
code. This bridge runs somewhere with its own clean IP instead.

OpenSky hit the same class of problem later: `/states/all` consistently
ConnectTimeouts from Render even with valid OAuth2 credentials
configured — ruling out auth/rate-limiting as the cause, since that
would come back as a clean 4xx, not a connection-level timeout. Since
this bridge already solves exactly that shape of problem for AIS, it
now polls OpenSky too instead of standing up a second service.

## Local development

```bash
cd ais-bridge
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` and fill in:
```
AISSTREAM_API_KEY=your_aisstream_key
OPENSKY_CLIENT_ID=your_opensky_client_id       # optional — falls back to anonymous if unset
OPENSKY_CLIENT_SECRET=your_opensky_client_secret
BRIDGE_API_KEY=some_long_random_string_you_invent
```
`.env` is already covered by the repo's `.gitignore` — it won't get committed.
Then run it:
```bash
uvicorn app:app --reload --port 8080
```
Check it's working:
```bash
curl http://127.0.0.1:8080/health
curl -H "X-Bridge-Key: some_long_random_string_you_invent" http://127.0.0.1:8080/vessels
curl -H "X-Bridge-Key: some_long_random_string_you_invent" http://127.0.0.1:8080/aircraft
```

## Deploy (Fly.io free tier)

```bash
cd ais-bridge
fly launch --no-deploy     # picks up fly.toml; say no to a Postgres/Redis prompt if asked
fly secrets set AISSTREAM_API_KEY=your_aisstream_key
fly secrets set OPENSKY_CLIENT_ID=your_opensky_client_id
fly secrets set OPENSKY_CLIENT_SECRET=your_opensky_client_secret
fly secrets set BRIDGE_API_KEY=some_long_random_string_you_invent
fly deploy
```

Confirm it's working:

```bash
curl https://vayu-ais-bridge.fly.dev/health
curl -H "X-Bridge-Key: some_long_random_string_you_invent" https://vayu-ais-bridge.fly.dev/vessels
curl -H "X-Bridge-Key: some_long_random_string_you_invent" https://vayu-ais-bridge.fly.dev/aircraft
```

## Wiring it into the main backend

On the main Vayu backend (Render), set (unchanged from before — the same
bridge URL/key now covers both feeds):

- `AIS_BRIDGE_URL` = `https://vayu-ais-bridge.fly.dev`
- `AIS_BRIDGE_API_KEY` = the same `BRIDGE_API_KEY` value you set above

`AISSTREAM_API_KEY` and `OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` are no
longer needed on the main backend — only the bridge talks to AISStream
and OpenSky now.

## Endpoints

- `GET /health` — `{"status": "ok", "vessel_count": <int>, "aircraft_count": <int>}`, unauthenticated.
- `GET /vessels` — requires header `X-Bridge-Key: <BRIDGE_API_KEY>`.
  Returns `{"vessels": [...], "count": <int>, "generated_at": "<iso timestamp>"}`.
  Each vessel matches the shape the main backend's `VesselStore` already
  uses (`mmsi`, `lat`, `lon`, `sog`, `cog`, `heading`, `last_update`,
  `name`, `ship_type_code`, `category`, `category_label`, `destination`,
  `callsign`).
- `GET /aircraft` — requires header `X-Bridge-Key: <BRIDGE_API_KEY>`.
  Returns `{"aircraft": [...], "count": <int>, "generated_at": "<iso timestamp>", "last_error": <string|null>, "last_success_at": <string|null>}`.
  Each aircraft matches the shape the main backend's `AircraftStore`
  already uses (`icao24`, `callsign`, `origin_country`, `lat`, `lon`,
  `baro_altitude_m`, `on_ground`, `velocity_ms`, `heading`,
  `vertical_rate_ms`, `last_update`). `last_error`/`last_success_at`
  reflect the bridge's OWN last attempt against OpenSky — the main
  backend surfaces this distinctly from a bridge-poll failure so a
  problem talking to OpenSky (vs. a problem talking to the bridge) is
  still diagnosable from `/api/v1/intel/sources`.

## Notes

- Keep this on a single worker/instance — see the comment in `Dockerfile`.
  A second instance would trip the same "duplicate connection" 429 the
  AIS side of this bridge exists to avoid. (OpenSky polling itself has no
  such constraint — only AISStream's one-connection-per-key requirement
  drives this.)
- If `BRIDGE_API_KEY` is left unset, `/vessels` and `/aircraft` are both
  unauthenticated. The app logs a warning on startup if you forget to set
  it — don't deploy publicly without it.
- If `OPENSKY_CLIENT_ID`/`OPENSKY_CLIENT_SECRET` are left unset, `/aircraft`
  falls back to anonymous OpenSky access — works, but less reliable even
  from this bridge's IP than authenticated access would be.
