/**
 * useCommodityTicker.js
 * Polls /api/v1/intel/commodities on an interval. Data is refreshed
 * server-side every few hours (see backend commodity_prices.py — Yahoo
 * Finance's unofficial chart API has no hard daily cap, unlike the
 * Alpha Vantage source this originally used), so this hook still polls
 * less aggressively than the live-tracking hooks since there's nothing
 * new between server-side refreshes.
 *
 * Usage:
 *   const { commodities, lastError } = useCommodityTicker(apiUrl);
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const POLL_INTERVAL_MS = 15 * 60 * 1000;   // 15 min — matches the shorter server-side refresh cadence now

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
