/**
 * Pure helpers for the shared intel feed (no React, so they are unit-testable in plain Node).
 *
 * The server owns the one canonical feed; a device just mirrors it. These keep a device's list identical to everyone
 * else's: events are identified by their stable id, ordered by event time, and capped the same way the server caps them.
 */

export const MAX_LOCAL_EVENTS = 500;

/** Newest first by event time; id is the tie-break so two devices always order equal timestamps the same way. */
export function compareNewestFirst(a, b) {
  if (a.ts !== b.ts) return a.ts < b.ts ? 1 : -1;
  if (a.id !== b.id) return a.id < b.id ? 1 : -1;
  return 0;
}

/** Drop duplicate ids (the LAST occurrence wins), order newest-first, cap. */
export function normalizeEvents(events, max = MAX_LOCAL_EVENTS) {
  const byId = new Map();
  for (const e of events || []) {
    if (e && e.id != null) byId.set(e.id, e);
  }
  return [...byId.values()].sort(compareNewestFirst).slice(0, max);
}

/** Add incoming events to the current list without ever duplicating one. */
export function mergeEvents(prev, incoming, max = MAX_LOCAL_EVENTS) {
  return normalizeEvents([...(prev || []), ...(incoming || [])], max);
}

/** Server stats ({total, by_source, by_severity}) -> the shape the panel reads. */
export function toPanelStats(serverStats) {
  const s = serverStats || {};
  return { total: s.total || 0, bySource: s.by_source || {}, bySeverity: s.by_severity || {} };
}

/** Events in `list` whose ids are not in `knownIds` (used to raise a map marker once per event, never again on re-sync). */
export function unseen(list, knownIds) {
  return (list || []).filter((e) => e && e.id != null && !knownIds.has(e.id));
}
