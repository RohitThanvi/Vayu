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


class VayuClient:
    def __init__(self, base_url, cache_dir, timeout=120, retries=3, delay=5.0, poll_every=4, poll_max=900):
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
    def call_sync(self, endpoint, body, use_cache=True):
        f = self._key(endpoint, body)
        if use_cache and f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        resp = self._request("POST", SYNC_ENDPOINTS[endpoint], body)
        f.write_text(json.dumps(resp), encoding="utf-8")
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
