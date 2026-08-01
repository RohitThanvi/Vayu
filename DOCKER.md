# VAYU — Docker Deployment Guide

Two ways to containerize this project, depending on what you actually need.

---

## Option A — All-in-One Image (recommended for "share with a friend")

**One image. One `docker run`. Nothing else for the other person to configure.**

This bundles the frontend (built static files, served by nginx) and the
backend (FastAPI/uvicorn) into a single container. nginx reverse-proxies
`/api/*` and the WebSocket route to the backend running on `127.0.0.1:8000`
inside the same container. Both processes are supervised by `supervisord`.

### 1. Fill in your secrets

Create `backend/.env` (copy from `backend/.env.example`) with your real values:

```dotenv
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
GCP_PROJECT_ID=your-gcp-project-id
GCS_BUCKET_NAME=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS_JSON={"type":"service_account",...}
ACLED_EMAIL=you@example.com
ACLED_PASSWORD=your-acled-password
AIS_BRIDGE_URL=https://your-ais-bridge.fly.dev
AIS_BRIDGE_API_KEY=your-bridge-shared-secret
ALLOWED_ORIGINS_STR=http://localhost:8080
```

**Why `GOOGLE_APPLICATION_CREDENTIALS_JSON` and not `earthengine authenticate`?**
Docker containers have no browser and no persistent home directory across
rebuilds, so the only viable GEE auth method inside a container is a
service-account JSON pasted as a single-line env var. This is already how
`gee_client.py` is set up — see [`Step 3` below](#step-3) if you don't have one yet.

### 2. Build the image

```bash
chmod +x build-allinone.sh
./build-allinone.sh vayu:local
```

Or manually:
```bash
docker build -f Dockerfile.allinone -t vayu:local .
```

The build will **fail loudly** if `backend/.env` doesn't exist — this is
intentional, so you don't accidentally ship an image with no credentials.

### 3. Test it locally

```bash
docker run -d -p 8080:80 --name vayu vayu:local
```

Open **http://localhost:8080** — the full app, frontend + backend + live
intel feed + vessel tracking, all from one container, one port.

Check logs:
```bash
docker logs -f vayu
```

Stop it:
```bash
docker stop vayu && docker rm vayu
```

### 4. Share it with your friend

**Easiest — push to Docker Hub (free, public or private repo):**
```bash
docker login
docker tag vayu:local yourusername/vayu:latest
docker push yourusername/vayu:latest
```

Your friend then just runs:
```bash
docker pull yourusername/vayu:latest
docker run -d -p 8080:80 yourusername/vayu:latest
```
and opens `http://localhost:8080`. That's genuinely it — no `.env` file,
no `earthengine authenticate`, no npm install, nothing.

**Alternative — save/load as a file (no registry, no internet needed):**
```bash
docker save vayu:local | gzip > vayu.tar.gz
# send vayu.tar.gz to your friend however you like (USB, Drive, etc.)

# Friend runs:
gunzip -c vayu.tar.gz | docker load
docker run -d -p 8080:80 vayu:local
```

---

### ⚠️ Important security note

This approach **bakes your API keys and GCP service account credentials
directly into the image**. Anyone who receives the image (via pull, file
transfer, or a public registry) can extract them — e.g. `docker history`,
or just running the container and reading `/app/backend/.env` inside it.

This is fine for sharing with **one trusted friend** for a demo. It is
**not** appropriate for:
- Pushing to a public Docker Hub repo with valuable/production keys
- Sharing widely or posting publicly

**Mitigations if you're doing this:**
- Use a GCP service account scoped to **only** Earth Engine read access (no
  other project permissions)
- Set a spend/rate cap on your Groq API key
- Use a Docker Hub **private** repository (free tier allows 1 private repo)
- Rotate all keys after you're done sharing, if it matters

If you want to avoid baking secrets into the image entirely, use **Option B**
below instead — your friend would just need to also create their own
`.env` file with their own keys (slightly more setup, but no shared secrets).

---

## Option B — Separate Containers via docker-compose

More maintainable long-term, keeps secrets out of the image, but requires
the other person to have **both** the image(s) and a `.env` file — slightly
more setup than Option A.

```bash
docker compose up -d --build
```

This uses the existing `backend/Dockerfile`, `frontend/Dockerfile`, and
`docker-compose.yml` already in this repo — two containers (backend on
`:8000`, frontend on `:80`), with the backend reading `backend/.env` via
`env_file:` at **runtime** (not baked into the image).

To share this way: push both images to a registry, give your friend the
`docker-compose.yml`, and have them create their own `backend/.env` with
their own credentials (or you give them yours separately, e.g. over a
secure channel — not via the image itself).

---

## Quick comparison

| | Option A (all-in-one) | Option B (compose) |
|---|---|---|
| Friend's setup | `docker pull` + `docker run` | `docker compose up` + needs `.env` |
| Ports exposed | 1 (`:80`) | 2 (`:80`, `:8000`) |
| Secrets location | Baked into image | Injected at runtime via `.env` |
| Best for | Quick demo to a trusted friend | Longer-term / multi-person use |

---

## Files involved (Option A)

```
Dockerfile.allinone       — multi-stage build: frontend (node) -> combined runtime (python+nginx)
nginx.allinone.conf       — proxies /api and WebSocket to localhost:8000 inside the container
supervisord.conf          — runs both nginx and uvicorn as supervised processes
.dockerignore             — excludes node_modules/venv/etc, but explicitly KEEPS backend/.env
build-allinone.sh         — convenience build/tag/push script
```
