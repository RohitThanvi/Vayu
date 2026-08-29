# Vayu Network Bridge (AIS + adsb.lol aircraft)

Holds two outbound connections that get blocked from Render Oregon's
datacenter IP range — AISStream.io's WebSocket and, previously, OpenSky's
REST snapshot — and re-exposes vessel and aircraft data as plain REST
endpoints the main Vayu backend polls.

This bridge is itself **a second Render web service, deployed in the Ohio
region** (not Fly.io — that was considered early on but never actually
used, since Fly's free tier now requires a card on file).

**Why this exists:** AISStream is WebSocket-only and allows exactly one
live connection per API key. When the main backend held that connection
directly on Render Oregon, every connection attempt got rejected with
HTTP 429 at the handshake — before AISStream even reads the API key —
and regenerating the key didn't help. That pointed at Render Oregon's
specific shared outbound IP pool getting blocked, not anything about the
key or the code. Moving the connection to this Render-Ohio service fixed
it.

Aircraft data originally came from OpenSky the same way, but OpenSky
ConnectTimeouts from Render-Ohio too (confirmed via this bridge's own
logs — even the OAuth token request itself never completes). Unlike AIS,
region-hopping within Render didn't fix OpenSky — it appears to disfavor
Render broadly, not just the Oregon range. So aircraft tracking now uses
**adsb.lol** instead: a free, fully keyless community ADS-B aggregation
API that doesn't have this problem. Its tradeoff is the opposite of
OpenSky's: no single global-snapshot endpoint, only bounded point/radius
queries (max 250nm) — so this bridge polls a curated list of ~55
aviation-dense regions spanning every populated continent
(`ADSBLOL_REGIONS` in `app.py`) and merges the results into one snapshot,
rather than one call covering the whole globe.

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
BRIDGE_API_KEY=some_long_random_string_you_invent
```
adsb.lol needs no key at all — nothing to add for aircraft tracking.
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

## Deploy (Render free tier, Ohio region)

From the Render dashboard: New → Web Service → point it at this repo,
root directory `ais-bridge/`, Docker runtime (uses the included
`Dockerfile`), region **Ohio**. Set environment variables:

- `AISSTREAM_API_KEY` = your aisstream.io key
- `BRIDGE_API_KEY` = a long random string you invent

No adsb.lol credentials needed — it's keyless.

Confirm it's working (replace with your actual Render URL):

```bash
curl https://<your-bridge>.onrender.com/health
curl -H "X-Bridge-Key: <BRIDGE_API_KEY>" https://<your-bridge>.onrender.com/vessels
curl -H "X-Bridge-Key: <BRIDGE_API_KEY>" https://<your-bridge>.onrender.com/aircraft
```

## Wiring it into the main backend

On the main Vayu backend (Render Oregon), set:

- `AIS_BRIDGE_URL` = `https://<your-bridge>.onrender.com`
- `AIS_BRIDGE_API_KEY` = the same `BRIDGE_API_KEY` value you set above

`AISSTREAM_API_KEY` is no longer needed on the main backend — only the
bridge talks to AISStream now. There's nothing to set for aircraft
tracking beyond the two vars above — the same bridge URL/key covers both
`/vessels` and `/aircraft`.

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
  already uses, now enriched with more of what adsb.lol provides:
  `icao24`, `callsign`, `registration`, `type_code`, `type_desc`, `lat`,
  `lon`, `baro_altitude_m`, `on_ground`, `velocity_ms`, `heading`,
  `vertical_rate_ms`, `squawk`, `category`, `emergency`, `military`,
  `interesting`, `last_update`. (`origin_country` is always `null` now —
  adsb.lol doesn't provide it the way OpenSky did.) `last_error`/
  `last_success_at` reflect the bridge's OWN last attempt against
  adsb.lol — the main backend surfaces this distinctly from a
  bridge-poll failure so a problem talking to adsb.lol (vs. a problem
  talking to the bridge) is still diagnosable from
  `/api/v1/intel/sources`.

## Notes

- Keep this on a single worker/instance — see the comment in `Dockerfile`.
  A second instance would trip the same "duplicate connection" 429 the
  AIS side of this bridge exists to avoid. (adsb.lol polling itself has
  no such constraint — only AISStream's one-connection-per-key
  requirement drives this.)
- If `BRIDGE_API_KEY` is left unset, `/vessels` and `/aircraft` are both
  unauthenticated. The app logs a warning on startup if you forget to set
  it — don't deploy publicly without it.
- `ADSBLOL_REGIONS` in `app.py` is a curated list, not literal
  wall-to-wall global tiling — tiling the entire globe in 250nm circles
  would mean hundreds of requests per poll cycle against a free,
  donation-funded, keyless community API, most of them over open ocean
  with no traffic. ~55 well-chosen aviation-dense region centers give
  broad worldwide coverage of where aircraft actually are without being
  an abusive request volume. Add or adjust regions freely.
