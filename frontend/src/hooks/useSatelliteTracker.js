/**
 * useSatelliteTracker.js
 * Fetches cached TLE orbital elements from /api/v1/intel/satellites/tle
 * (refetched periodically to pick up the server's ~6h CelesTrak refresh)
 * and propagates each satellite's live position client-side via SGP4
 * (satellite.js). Position math happens entirely in the browser — the
 * server never computes per-satellite positions, it only caches raw TLEs.
 *
 * Usage:
 *   const { satellites, loaded } = useSatelliteTracker(apiUrl, enabled);
 *   // satellites: [{ name, group, lat, lon, alt_km }, ...]
 */

import { useState, useEffect, useRef, useCallback } from "react";
import * as satellite from "satellite.js";

const TLE_REFETCH_MS = 30 * 60 * 1000;   // 30min — server cache itself refreshes ~6h, this just picks it up eventually
const PROPAGATE_INTERVAL_MS = 3000;       // recompute positions every 3s — smooth enough for a live feel, cheap on CPU

export function useSatelliteTracker(apiUrl, enabled = true) {
  const [satellites, setSatellites] = useState([]);
  const [loaded, setLoaded] = useState(false);

  const satrecsRef = useRef([]);   // [{ name, group, satrec }]
  const tleFetchRef = useRef(null);
  const propagateRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchTLEs = useCallback(async () => {
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/satellites/tle`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      const parsed = [];
      for (const s of (data.satellites || [])) {
        try {
          const satrec = satellite.twoline2satrec(s.line1, s.line2);
          parsed.push({ name: s.name, group: s.group, satrec });
        } catch {
          // Malformed/unpropagatable element set — skip rather than crash the whole layer
        }
      }
      satrecsRef.current = parsed;
      setLoaded(parsed.length > 0);
    } catch {
      // Keep whatever satrecs we already have; just skip this refresh
    }
  }, [apiUrl]);

  const propagateNow = useCallback(() => {
    if (satrecsRef.current.length === 0) return;
    const now = new Date();
    const gmst = satellite.gstime(now);
    const results = [];
    for (const { name, group, satrec } of satrecsRef.current) {
      try {
        const pv = satellite.propagate(satrec, now);
        if (!pv || !pv.position) continue;
        const gd = satellite.eciToGeodetic(pv.position, gmst);
        const lat = satellite.degreesLat(gd.latitude);
        const lon = satellite.degreesLong(gd.longitude);
        if (Number.isNaN(lat) || Number.isNaN(lon)) continue;
        results.push({ name, group, lat, lon, alt_km: gd.height });
      } catch {
        // Decayed/invalid orbit for this element set at current time — skip just this one
      }
    }
    if (mountedRef.current) setSatellites(results);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;

    fetchTLEs().then(propagateNow);
    tleFetchRef.current = setInterval(fetchTLEs, TLE_REFETCH_MS);
    propagateRef.current = setInterval(propagateNow, PROPAGATE_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(tleFetchRef.current);
      clearInterval(propagateRef.current);
    };
  }, [fetchTLEs, propagateNow, enabled]);

  return { satellites, loaded };
}
