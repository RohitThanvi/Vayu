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
  // Debug info surfaced in the UI (see OrbitalGlobe's stats box) instead of
  // only console — makes "why is nothing showing" diagnosable without
  // needing devtools open.
  const [debug, setDebug] = useState({ fetchedCount: 0, parsedCount: 0, propagatedCount: 0, lastError: null });

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
      const rawList = data.satellites || [];
      const parsed = [];
      let parseErrors = 0;
      for (const s of rawList) {
        try {
          const satrec = satellite.twoline2satrec(s.line1, s.line2);
          parsed.push({ name: s.name, group: s.group, satrec });
        } catch {
          // Malformed/unpropagatable element set — skip rather than crash the whole layer
          parseErrors++;
        }
      }
      satrecsRef.current = parsed;
      setLoaded(parsed.length > 0);
      setDebug(prev => ({ ...prev, fetchedCount: rawList.length, parsedCount: parsed.length, lastError: parseErrors > 0 && parsed.length === 0 ? `all ${parseErrors} TLEs failed to parse` : null }));
    } catch (err) {
      // Keep whatever satrecs we already have; just skip this refresh
      setDebug(prev => ({ ...prev, lastError: `fetch failed: ${err.message}` }));
    }
  }, [apiUrl]);

  const propagateNow = useCallback(() => {
    if (satrecsRef.current.length === 0) return;
    const now = new Date();
    const gmst = satellite.gstime(now);
    const results = [];
    let propagateErrors = 0;
    for (const { name, group, satrec } of satrecsRef.current) {
      try {
        const pv = satellite.propagate(satrec, now);
        if (!pv || !pv.position) { propagateErrors++; continue; }
        const gd = satellite.eciToGeodetic(pv.position, gmst);
        const lat = satellite.degreesLat(gd.latitude);
        const lon = satellite.degreesLong(gd.longitude);
        if (Number.isNaN(lat) || Number.isNaN(lon)) { propagateErrors++; continue; }
        results.push({ name, group, lat, lon, alt_km: gd.height });
      } catch {
        // Decayed/invalid orbit for this element set at current time — skip just this one
        propagateErrors++;
      }
    }
    if (mountedRef.current) {
      setSatellites(results);
      setDebug(prev => ({ ...prev, propagatedCount: results.length, lastError: results.length === 0 && satrecsRef.current.length > 0 ? `${propagateErrors}/${satrecsRef.current.length} satrecs failed to propagate` : prev.lastError }));
    }
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

  return { satellites, loaded, debug };
}
