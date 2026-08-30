/**
 * useCommodityTicker.js
 * Polls /api/v1/intel/commodities on an interval. Data is refreshed
 * server-side only once a day (see backend commodity_prices.py — free
 * Alpha Vantage tier has a low daily budget, and the data itself is
 * monthly-resolution for most symbols anyway), so this hook polls far
 * less aggressively than the live-tracking hooks — there's nothing new
 * to see on a tighter interval.
 *
 * Usage:
 *   const { commodities, lastError } = useCommodityTicker(apiUrl);
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const POLL_INTERVAL_MS = 30 * 60 * 1000;   // 30 min — plenty for daily-refreshed data

export function useCommodityTicker(apiUrl, enabled = true) {
  const [commodities, setCommodities] = useState([]);
  const [lastError, setLastError] = useState(null);
  const mountedRef = useRef(true);

  const fetchCommodities = useCallback(async () => {
    if (apiUrl === undefined || apiUrl === null) return;
    try {
      const res = await fetch(`${apiUrl}/api/v1/intel/commodities`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!mountedRef.current) return;
      setCommodities(data.commodities || []);
      setLastError(data.last_error || null);
    } catch (err) {
      if (mountedRef.current) setLastError(err.message);
    }
  }, [apiUrl]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) return;
    fetchCommodities();
    const id = setInterval(fetchCommodities, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [fetchCommodities, enabled]);

  return { commodities, lastError };
}
