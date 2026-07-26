/**
 * useIntelFeed.js
 * React hook that manages the WebSocket connection to the VAYU intel feed.
 *
 * Usage:
 *   const { events, connected, stats, setFilter } = useIntelFeed();
 *
 * Returns:
 *   events    — array of IntelEvent objects, newest first, max 500
 *   connected — boolean WebSocket status
 *   stats     — { total, bySource, bySeverity }
 *   setFilter — fn({ sources: [], severities: [] }) to narrow stream
 *   clear     — fn() to clear local event buffer
 */

import { useState, useEffect, useRef, useCallback } from "react";

const MAX_LOCAL_EVENTS = 500;
const RECONNECT_DELAY_MS = 3000;

export function useIntelFeed(apiUrl, onNewEvent) {
  const wsUrl = apiUrl
    ? apiUrl.replace(/^http/, "ws") + "/api/v1/intel/ws"
    : `ws://${window.location.hostname}:8000/api/v1/intel/ws`;

  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [stats, setStats] = useState({ total: 0, bySource: {}, bySeverity: {} });

  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const filterRef = useRef({ sources: null, severities: null });
  const mountedRef = useRef(true);

  const addEvents = useCallback((incoming) => {
    setEvents((prev) => {
      const merged = [...incoming, ...prev];
      return merged.slice(0, MAX_LOCAL_EVENTS);
    });

    setStats((prev) => {
      const bySource = { ...prev.bySource };
      const bySeverity = { ...prev.bySeverity };
      for (const e of incoming) {
        bySource[e.source] = (bySource[e.source] || 0) + 1;
        bySeverity[e.severity] = (bySeverity[e.severity] || 0) + 1;
      }
      return { total: prev.total + incoming.length, bySource, bySeverity };
    });

    if (onNewEvent) {
      incoming.forEach((e) => {
        if (typeof e.lat === "number" && typeof e.lon === "number") {
          onNewEvent(e);
        }
      });
    }
  }, [onNewEvent]);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      // Re-send active filter on reconnect
      if (filterRef.current.sources || filterRef.current.severities) {
        ws.send(JSON.stringify({ type: "filter", ...filterRef.current }));
      }
    };

    ws.onmessage = (ev) => {
      if (!mountedRef.current) return;
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot") {
          addEvents(msg.data || []);
        } else if (msg.type === "event") {
          addEvents([msg.data]);
        }
        // ping / filter_ack — no action needed
      } catch {}
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);
      // Reconnect after delay
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [wsUrl, addEvents]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const setFilter = useCallback((filter) => {
    filterRef.current = filter;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "filter", ...filter }));
    }
  }, []);

  const clear = useCallback(() => {
    setEvents([]);
    setStats({ total: 0, bySource: {}, bySeverity: {} });
  }, []);

  return { events, connected, stats, setFilter, clear };
}
