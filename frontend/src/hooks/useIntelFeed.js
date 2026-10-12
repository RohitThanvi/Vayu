/**
 * useIntelFeed.js
 * React hook that mirrors the ONE shared VAYU intel feed.
 *
 * The server holds a single canonical feed (same list for everyone, versioned by `feed_id`). A device does not build its
 * own feed out of "whatever arrived since I connected" any more — that is what made two visitors see different feeds:
 *
 *   - On connect the server sends the full canonical feed (REPLACING whatever is shown, never prepended onto it).
 *   - New events stream in over the WebSocket and are de-duplicated by id.
 *   - Counters come from the server, so every device shows the same numbers.
 *   - The device re-syncs from GET /intel/feed when the server says it fell behind, when the tab becomes visible again,
 *     and on a slow safety timer. Those requests carry an ETag, so while nothing has changed the answer is a tiny 304.
 *
 * Usage:
 *   const { events, connected, stats, setFilter, clear } = useIntelFeed(apiUrl, onNewEvent);
 *     events    — canonical feed, newest first, max 500
 *     connected — boolean WebSocket status
 *     stats     — { total, bySource, bySeverity } straight from the server
 *     setFilter — fn({ sources: [], severities: [] }) to narrow the LIVE stream
 *     clear     — fn() clears this device's local view only
 *     onNewEvent is called once per event the first time this device sees it (map markers), never again on re-sync.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { MAX_LOCAL_EVENTS, normalizeEvents, mergeEvents, toPanelStats, unseen } from "../lib/intelFeedMerge";

const RECONNECT_DELAY_MS = 3000;
const SAFETY_RESYNC_MS = 5 * 60 * 1000;    // connected: cheap conditional check that nothing drifted
const OFFLINE_RESYNC_MS = 60 * 1000;       // WebSocket down: keep the feed fresh over REST meanwhile
const MAX_KNOWN_IDS = 5000;

export function useIntelFeed(apiUrl, onNewEvent) {
  // apiUrl truthy  -> absolute backend URL (multi-container/Render setups)
  // apiUrl ""/falsy -> same-origin relative (single-container nginx-proxy setups)
  const base = apiUrl || "";
  const wsUrl = apiUrl
    ? apiUrl.replace(/^http/, "ws") + "/api/v1/intel/ws"
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/api/v1/intel/ws`;
  const feedUrl = `${base}/api/v1/intel/feed`;

  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const [stats, setStats] = useState({ total: 0, bySource: {}, bySeverity: {} });

  const wsRef = useRef(null);
  const reconnectTimer = useRef(null);
  const filterRef = useRef({ sources: null, severities: null });
  const mountedRef = useRef(true);
  const feedIdRef = useRef(null);           // feed identity this device believes it has
  const etagRef = useRef(null);             // ETag of the last full copy fetched over REST
  const knownIdsRef = useRef(new Set());    // every event id this device has already seen
  const onNewEventRef = useRef(onNewEvent); // latest callback without re-opening the socket when it changes
  onNewEventRef.current = onNewEvent;

  const announce = useCallback((fresh) => {
    const cb = onNewEventRef.current;
    if (!cb) return;
    fresh.forEach((e) => {
      if (typeof e.lat === "number" && typeof e.lon === "number") cb(e);
    });
  }, []);

  const remember = useCallback((list) => {
    const known = knownIdsRef.current;
    if (known.size > MAX_KNOWN_IDS) known.clear();
    list.forEach((e) => known.add(e.id));
  }, []);

  /** Make this device's feed EXACTLY the server's copy. */
  const applyCanonical = useCallback((serverEvents, feedId, serverStats) => {
    if (!mountedRef.current) return;
    const next = normalizeEvents(serverEvents, MAX_LOCAL_EVENTS);
    const fresh = unseen(next, knownIdsRef.current);
    remember(next);
    setEvents(next);
    if (serverStats) setStats(toPanelStats(serverStats));
    if (feedId) feedIdRef.current = feedId;
    announce(fresh);
  }, [announce, remember]);

  /** One live event: add it unless it is already here. */
  const applyLive = useCallback((event, feedId, serverStats) => {
    if (!mountedRef.current || !event) return;
    if (!knownIdsRef.current.has(event.id)) {
      remember([event]);
      setEvents((prev) => mergeEvents(prev, [event], MAX_LOCAL_EVENTS));
      announce([event]);
    }
    if (serverStats) setStats(toPanelStats(serverStats));
    if (feedId) feedIdRef.current = feedId;
  }, [announce, remember]);

  /**
   * Re-sync from REST. `conditional`: ask "has anything changed since the feed I think I have?" — answered with a 304
   * (no body) when not, so the periodic check costs next to nothing.
   */
  const resync = useCallback(async (conditional = false) => {
    try {
      const headers = {};
      const tag = conditional ? (feedIdRef.current ? `W/"${feedIdRef.current}-${MAX_LOCAL_EVENTS}"` : etagRef.current) : null;
      if (tag) headers["If-None-Match"] = tag;
      const res = await fetch(feedUrl, { headers, cache: "no-store" });
      if (res.status === 304 || !res.ok) return;
      const data = await res.json();
      etagRef.current = res.headers.get("ETag");
      applyCanonical(data.events || [], data.feed_id, data.stats);
    } catch {
      /* offline / backend asleep: the next trigger tries again */
    }
  }, [feedUrl, applyCanonical]);

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
          applyCanonical(msg.data || [], msg.feed_id, msg.stats);
        } else if (msg.type === "event") {
          applyLive(msg.data, msg.feed_id, msg.stats);
        } else if (msg.type === "resync") {
          resync(false);                                    // server: "you fell behind" -> take the canonical copy
        } else if (msg.type === "ping" && msg.stats) {
          setStats(toPanelStats(msg.stats));                // keep counters equal to the server's
        }
        // filter_ack / pong — no action needed
      } catch {
        /* malformed message: ignore it, the next snapshot/resync restores state */
      }
    };

    ws.onclose = () => {
      if (!mountedRef.current) return;
      setConnected(false);
      // Reconnect after delay (the next snapshot replaces the list, so a reconnect can no longer duplicate anything)
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [wsUrl, applyCanonical, applyLive, resync]);

  useEffect(() => {
    mountedRef.current = true;
    resync(false);   // paint the canonical feed right away over REST; the socket's snapshot is the same list, so nothing flickers
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect, resync]);

  // A tab that slept or was in the background may have missed messages: check as soon as it is visible again.
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") resync(true); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [resync]);

  // Safety net: cheap conditional re-check while connected, fresher polling while the socket is down.
  useEffect(() => {
    const t = setInterval(() => resync(true), connected ? SAFETY_RESYNC_MS : OFFLINE_RESYNC_MS);
    return () => clearInterval(t);
  }, [connected, resync]);

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
