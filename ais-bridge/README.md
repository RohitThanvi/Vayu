# Vayu AIS Bridge

Holds the one persistent AISStream.io WebSocket connection and re-exposes
it as a plain REST endpoint (`GET /vessels`), so the main Vayu backend
(on Render) never has to hold that connection itself.

**Why this exists:** AISStream is WebSocket-only and allows exactly one
live connection per API key. When the main backend held that connection
directly on Render, every connection attempt got rejected with HTTP 429
at the handshake — before AISStream even reads the API key — and
regenerating the key didn't help. That points at Render's shared
outbound IP pool getting blocked, not anything about the key or the
code. This bridge runs somewhere with its own clean IP instead.

## Local development

```bash
cd ais-bridge
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```
Edit `.env` and fill in both values:
```
AISSTREAM_API_KEY=your_aisstream_key
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
```

## Deploy (Fly.io free tier)

```bash
cd ais-bridge
fly launch --no-deploy     # picks up fly.toml; say no to a Postgres/Redis prompt if asked
fly secrets set AISSTREAM_API_KEY=your_aisstream_key
fly secrets set BRIDGE_API_KEY=some_long_random_string_you_invent
fly deploy
```

Confirm it's working:

```bash
curl https://vayu-ais-bridge.fly.dev/health
curl -H "X-Bridge-Key: some_long_random_string_you_invent" https://vayu-ais-bridge.fly.dev/vessels
```

## Wiring it into the main backend

On the main Vayu backend (Render), set:

- `AIS_BRIDGE_URL` = `https://vayu-ais-bridge.fly.dev`
- `AIS_BRIDGE_API_KEY` = the same `BRIDGE_API_KEY` value you set above

`AISSTREAM_API_KEY` is no longer needed on the main backend — only the
bridge talks to AISStream now.

## Endpoints

- `GET /health` — `{"status": "ok", "vessel_count": <int>}`, unauthenticated.
- `GET /vessels` — requires header `X-Bridge-Key: <BRIDGE_API_KEY>`.
  Returns `{"vessels": [...], "count": <int>, "generated_at": "<iso timestamp>"}`.
  Each vessel matches the shape the main backend's `VesselStore` already
  uses (`mmsi`, `lat`, `lon`, `sog`, `cog`, `heading`, `last_update`,
  `name`, `ship_type_code`, `category`, `category_label`, `destination`,
  `callsign`).

## Notes

- Keep this on a single worker/instance — see the comment in `Dockerfile`.
  A second instance would trip the same "duplicate connection" 429 this
  bridge exists to avoid.
- If `BRIDGE_API_KEY` is left unset, `/vessels` is unauthenticated. The
  app logs a warning on startup if you forget to set it — don't deploy
  publicly without it.
