/**
 * useStrategicSites.js
 * Fetches the curated ports/refineries/mines list + their live nearby
 * signals (earthquakes, dark vessels, news tone — see backend
 * services/intel/strategic_sites.py). Static list, so this polls far
 * less often than vessel tracking — the signals attached to each site
 * change over hours, not seconds.
 *
 * Usage:
 *   const { sites } = useStrategicSites(apiUrl, enabled);
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 min — signals are slow-moving

export function useStrategicSites(apiUrl, enabled = true) {
  const [sites, setSites] = useState([]);
  const pollRef = useRef(null);
  const mountedRef = useRef(true);

  const fetchSites = useCallback(async () => {
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/strategic-sites`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (mountedRef.current) setSites(data.sites || []);
    } catch {
      // keep whatever we last had rather than clearing the map on a blip
    }
  }, [apiUrl]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return undefined;
    fetchSites();
    pollRef.current = setInterval(fetchSites, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(pollRef.current);
    };
  }, [enabled, fetchSites]);

  return { sites };
}
