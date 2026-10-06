"""client.py - HTTP client for the Vayu API: retries, 429/Retry-After, async job polling, on-disk response cache."""
import hashlib, json, time
from pathlib import Path
import requests

API = "/api/v1"
SYNC_ENDPOINTS = {                       # name -> path (all POST, JSON body with aoi_geojson)
    "crop_suitability":    f"{API}/agri/crop-suitability",
    "risk_score":          f"{API}/agri/risk-score",
    "baseline":            f"{API}/agri/baseline",
    "drought_dashboard":   f"{API}/agri/drought-dashboard",
    "groundwater_trend":   f"{API}/agri/groundwater-trend",
    "phenology":           f"{API}/agri/phenology",
    "ndvi_trend":          f"{API}/agri/ndvi-trend",
    "irrigation_advisory": f"{API}/agri/irrigation-advisory",
    "crop_extent":         f"{API}/agri/crop-extent",
}
RS_ANALYZE = f"{API}/remote-sensing/analyze"      # POST -> 202 {request_id}; GET /analyze/{id} to poll


class VayuError(RuntimeError):
    pass


def aoi_key(body):
    """Stable key for a location: the AOI geometry (or lat/lon) of a request."""
    aoi = body.get("aoi_geojson")
    if aoi is None:
        aoi = {"lat": body.get("lat"), "lon": body.get("lon")}
    return hashlib.sha256(json.dumps(aoi, sort_keys=True, default=str).encode()).hexdigest()[:20]


class ReplayClient:
    """Re-scores SAVED location profiles with the engine in THIS checkout - no network, no Earth Engine.
    Valid as long as the sampled evidence (soil, climate, slope) is unchanged; it tests the scoring logic, not the sampling."""
    def __init__(self, profiles_dir, backend_path=None):
        import sys
        from pathlib import Path as _P
        self.dir = _P(profiles_dir)
        sys.path.insert(0, str(backend_path or _P(__file__).resolve().parent.parent / "backend"))
        from app.services.agri.crop_suitability import score_crops, ENGINE_VERSION
        self._score, self.local_engine_version = score_crops, ENGINE_VERSION

    def call(self, endpoint, body, use_cache=True):
        if endpoint != "crop_suitability":
            raise VayuError(f"replay supports crop_suitability only (got {endpoint})")
        f = self.dir / f"{aoi_key(body)}.json"
        if not f.exists():
            raise VayuError("no saved profile for this AOI - run once against the live backend (profiles are saved automatically)")
        prof = json.loads(f.read_text(encoding="utf-8"))["profile"]
        if getattr(self, "profile_transform", None):
            prof = self.profile_transform(prof)
        irr = body["irrigation_available"] if "irrigation_available" in body else False        # null = infer from the saved irrigated-cropland share
        out = self._score(prof, None if irr is None else bool(irr), season=body.get("season"))
        out["engine_version"] = "replay"
        return out


class VayuClient:
    def __init__(self, base_url, cache_dir, timeout=120, retries=3, delay=5.0, poll_every=4, poll_max=900, profiles_dir=None):
        self.profiles_dir = Path(profiles_dir) if profiles_dir else None
        self.base = base_url.rstrip("/")
        self.cache = Path(cache_dir); self.cache.mkdir(parents=True, exist_ok=True)
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self.poll_every, self.poll_max = poll_every, poll_max
        self.s = requests.Session()
        self.last_call = 0.0

    # -- cache ---------------------------------------------------------
    def _key(self, tag, body):
        h = hashlib.sha256((tag + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()[:24]
        return self.cache / f"{tag.replace('/', '_').strip('_')}__{h}.json"

    def _throttle(self):                 # RS endpoint is limited to 15/min; stay well below
        wait = self.delay - (time.time() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.last_call = time.time()

    def _request(self, method, path, body=None):
        url = self.base + path
        for attempt in range(1, self.retries + 1):
            try:
                self._throttle()
                r = self.s.request(method, url, json=body, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("Retry-After", 30))); continue
                if r.status_code >= 500 and attempt < self.retries:
                    time.sleep(2 ** attempt * 5); continue            # also covers Render cold starts
                if r.status_code >= 400:
                    raise VayuError(f"HTTP {r.status_code} {path}: {r.text[:300]}")
                return r.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == self.retries:
                    raise VayuError(f"{type(e).__name__} on {path}: {e}")
                time.sleep(2 ** attempt * 5)
        raise VayuError(f"gave up on {path}")

    # -- public --------------------------------------------------------
    def _save_profile(self, endpoint, body, resp):
        """Keep the sampled evidence (monthly climate, soil, slope, latitude) so any engine version can be replayed offline later."""
        if endpoint != "crop_suitability" or not self.profiles_dir or not isinstance(resp, dict):
            return
        prof = resp.get("profile") or {}
        if not prof.get("monthly_rain_mm") or not prof.get("monthly_temp_c"):
            return                                              # older backend: profile too thin to replay
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        f = self.profiles_dir / f"{aoi_key(body)}.json"
        if not f.exists():
            f.write_text(json.dumps({"profile": prof, "engine_version": resp.get("engine_version"), "saved": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")

    def call_sync(self, endpoint, body, use_cache=True):
        f = self._key(endpoint, body)
        if use_cache and f.exists():
            resp = json.loads(f.read_text(encoding="utf-8")); self._save_profile(endpoint, body, resp); return resp
        resp = self._request("POST", SYNC_ENDPOINTS[endpoint], body)
        f.write_text(json.dumps(resp), encoding="utf-8")
        self._save_profile(endpoint, body, resp)
        return resp

    def call_rs(self, tool, body, use_cache=True):
        body = {**body, "tool": tool}
        f = self._key("rs_" + tool, body)
        if use_cache and f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        rid = self._request("POST", RS_ANALYZE, body)["request_id"]
        t0 = time.time()
        while True:
            r = self._request("GET", f"{RS_ANALYZE}/{rid}")
            if r.get("status") == "done":
                result = r["result"]; f.write_text(json.dumps(result), encoding="utf-8"); return result
            if time.time() - t0 > self.poll_max:
                raise VayuError(f"timeout polling {tool} job {rid}")
            time.sleep(self.poll_every)

    def call(self, endpoint, body, use_cache=True):
        if endpoint.startswith("rs:"):
            return self.call_rs(endpoint[3:], body, use_cache)
        if endpoint not in SYNC_ENDPOINTS:
            raise VayuError(f"unknown endpoint '{endpoint}'")
        return self.call_sync(endpoint, body, use_cache)
