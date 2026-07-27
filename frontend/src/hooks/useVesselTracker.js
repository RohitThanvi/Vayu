/**
 * useVesselTracker.js
 * Polls the /api/v1/intel/vessels REST endpoint on an interval and returns
 * the current snapshot of tracked maritime vessels.
 *
 * Unlike intel events (WebSocket push of discrete events), vessels are
 * continuously-updating positions, so simple polling is the right model —
 * each poll just replaces the previous snapshot.
 *
 * Usage:
 *   const { vessels, stats, connected } = useVesselTracker(apiUrl);
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 8000;

export function useVesselTracker(apiUrl, enabled = true) {
  const [vessels, setVessels] = useState([]);
  const [stats, setStats] = useState({ active_vessels: 0, by_category: {} });
  const [connected, setConnected] = useState(false);

  const pollRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchVessels = useCallback(async () => {
    // Allow apiUrl === "" (same-origin relative paths, e.g. single-container
    // nginx-proxy deployments) — only skip if truly not provided.
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/vessels?limit=2000`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      setVessels(data.vessels || []);
      setConnected(true);
    } catch {
      if (mountedRef.current) setConnected(false);
    }

    try {
      const sres = await fetch(`${apiUrl}/api/v1/intel/vessels/stats`);
      if (sres.ok) {
        const sdata = await sres.json();
        if (mountedRef.current) setStats(sdata);
      }
    } catch {}
  }, [apiUrl]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;

    fetchVessels();
    pollRef.current = setInterval(fetchVessels, POLL_INTERVAL_MS);

    return () => {
      mountedRef.current = false;
      clearInterval(pollRef.current);
    };
  }, [fetchVessels, enabled]);

  return { vessels, stats, connected };
}
