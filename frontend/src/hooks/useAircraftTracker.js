/**
 * useAircraftTracker.js
 * Polls the /api/v1/intel/aircraft REST endpoint on an interval and returns
 * the current snapshot of tracked aircraft.
 *
 * Same model as useVesselTracker.js: aircraft are continuously-updating
 * positions from a global snapshot poll (OpenSky), not discrete push events,
 * so simple polling is the right model — each poll just replaces the
 * previous snapshot.
 *
 * Usage:
 *   const { aircraft, stats, connected } = useAircraftTracker(apiUrl, enabled);
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 10000;

export function useAircraftTracker(apiUrl, enabled = true) {
  const [aircraft, setAircraft] = useState([]);
  const [stats, setStats] = useState({ active_aircraft: 0, airborne: 0, on_ground: 0 });
  const [connected, setConnected] = useState(false);

  const pollRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchAircraft = useCallback(async () => {
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/aircraft?limit=3000`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      setAircraft(data.aircraft || []);
      setConnected(true);
    } catch {
      if (mountedRef.current) setConnected(false);
    }

    try {
      const sres = await fetch(`${apiUrl}/api/v1/intel/aircraft/stats`);
      if (sres.ok) {
        const sdata = await sres.json();
        if (mountedRef.current) setStats(sdata);
      }
    } catch {}
  }, [apiUrl]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;

    fetchAircraft();
    pollRef.current = setInterval(fetchAircraft, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(pollRef.current);
    };
  }, [fetchAircraft, enabled]);

  return { aircraft, stats, connected };
}
