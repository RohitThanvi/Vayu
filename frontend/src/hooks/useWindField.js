/**
 * useWindField.js
 * Polls the same /api/v1/intel/wind-field endpoint the 2D map's leaflet-
 * velocity "Wind" layer already uses (see App.jsx handleToggleWeather,
 * type 'velocity') — real global wind U/V components from Open-Meteo,
 * refreshed backend-side every 45 min (see scheduler.py INTERVAL_WIND).
 * Client polls less aggressively than that refresh cadence since the
 * data rarely changes between polls.
 *
 * Returns the raw [{header, data}, {header, data}] pair (U record, V
 * record) unchanged — OrbitalGlobe decodes it into per-point vectors
 * itself, same shape leaflet-velocity consumes on the 2D map.
 *
 * Usage:
 *   const { windField, loaded } = useWindField(apiUrl, enabled);
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 10 * 60 * 1000; // 10 min — well under the 45 min
                                          // backend refresh, so a poll only
                                          // occasionally actually gets new data

export function useWindField(apiUrl, enabled = true) {
  const [windField, setWindField] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const pollRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchWind = useCallback(async () => {
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/wind-field`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      setWindField(data);
      setLoaded(true);
    } catch {
      // Leave whatever windField we already have (if any) rather than
      // clearing it on a transient failure — a stale-but-real wind field
      // is still useful, an empty one isn't.
    }
  }, [apiUrl]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;

    fetchWind();
    pollRef.current = setInterval(fetchWind, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(pollRef.current);
    };
  }, [fetchWind, enabled]);

  return { windField, loaded };
}
