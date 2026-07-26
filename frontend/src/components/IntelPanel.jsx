/**
 * IntelPanel.jsx
 * Right-side live intelligence feed panel for VAYU terminal.
 * Drop-in replacement for the static feed section in App.jsx.
 *
 * Props:
 *   apiUrl      — backend base URL (e.g. "https://vayu.onrender.com")
 *   aoi         — current drawn AOI GeoJSON geometry (optional, for bbox filter)
 *   onEventClick — fn(event) called when user clicks a feed item
 */

import { useState, useMemo } from "react";
import { useIntelFeed } from "../hooks/useIntelFeed";

const SOURCE_COLORS = {
  "USGS":       "#8a6010",
  "NASA FIRMS": "#8b2020",
  "GDELT":      "#3a3a7a",
  "ACLED":      "#6b2020",
  "OpenSky":    "#1a4a5a",
  "AISHub":     "#1a3a6a",
};

const SEVERITY_COLORS = {
  critical: "#8b2020",
  warn:     "#7a5010",
  info:     "#2a3a4a",
};

const ALL_SOURCES = ["USGS", "NASA FIRMS", "GDELT", "ACLED"];
const ALL_SEVERITIES = ["critical", "warn", "info"];

function timeAgo(ts) {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 60)  return `${diff}S AGO`;
  if (diff < 3600) return `${Math.floor(diff / 60)}M AGO`;
  return `${Math.floor(diff / 3600)}H ${Math.floor((diff % 3600) / 60)}M AGO`;
}

function formatCoord(lat, lon) {
  const la = `${Math.abs(lat).toFixed(2)}${lat >= 0 ? "N" : "S"}`;
  const lo = `${Math.abs(lon).toFixed(2)}${lon >= 0 ? "E" : "W"}`;
  return `${la} ${lo}`;
}

export default function IntelPanel({ apiUrl, aoi, onEventClick, onNewEvent }) {
  const { events, connected, stats, setFilter, clear } = useIntelFeed(apiUrl, onNewEvent);

  const [activeSources, setActiveSources]     = useState(new Set(ALL_SOURCES));
  const [activeSeverities, setActiveSeverities] = useState(new Set(ALL_SEVERITIES));
  const [search, setSearch] = useState("");
  const [newCount, setNewCount] = useState(0);

  // Filter events client-side
  const filtered = useMemo(() => {
    return events.filter((e) => {
      if (!activeSources.has(e.source)) return false;
      if (!activeSeverities.has(e.severity)) return false;
      if (search && !e.title.toLowerCase().includes(search.toLowerCase()) &&
          !e.detail.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [events, activeSources, activeSeverities, search]);

  const toggleSource = (src) => {
    setActiveSources((prev) => {
      const next = new Set(prev);
      next.has(src) ? next.delete(src) : next.add(src);
      return next;
    });
  };

  const toggleSeverity = (sev) => {
    setActiveSeverities((prev) => {
      const next = new Set(prev);
      next.has(sev) ? next.delete(sev) : next.add(sev);
      return next;
    });
  };

  return (
    <div style={styles.panel}>
      {/* Header */}
      <div style={styles.header}>
        <div style={styles.headerLeft}>
          <span style={styles.title}>INTEL FEED</span>
          <span style={{
            ...styles.badge,
            color: connected ? "#6fcf73" : "#ff6b6b",
            fontWeight: 700,
          }}>
            {connected ? "LIVE" : "RECONNECTING"}
          </span>
        </div>
        <div style={styles.headerRight}>
          <span style={styles.countBadge}>{filtered.length} EVENTS</span>
          <button style={styles.clearBtn} onClick={clear}>CLR</button>
        </div>
      </div>

      {/* Source filter row */}
      <div style={styles.filterRow}>
        {ALL_SOURCES.map((src) => (
          <button
            key={src}
            style={{
              ...styles.filterChip,
              color: "#ffffff",
              opacity: activeSources.has(src) ? 1 : 0.35,
              borderColor: activeSources.has(src) ? (SOURCE_COLORS[src] || "#6b7a8d") : "#2a3040",
            }}
            onClick={() => toggleSource(src)}
          >
            {src}
          </button>
        ))}
      </div>

      {/* Severity filter row */}
      <div style={styles.filterRow}>
        {ALL_SEVERITIES.map((sev) => (
          <button
            key={sev}
            style={{
              ...styles.filterChip,
              color: "#ffffff",
              opacity: activeSeverities.has(sev) ? 1 : 0.35,
              borderColor: activeSeverities.has(sev) ? SEVERITY_COLORS[sev] : "#2a3040",
            }}
            onClick={() => toggleSeverity(sev)}
          >
            {sev.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Search */}
      <div style={styles.searchRow}>
        <span style={styles.searchPrompt}>FILTER&gt;</span>
        <input
          style={styles.searchInput}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="keyword search..."
        />
      </div>

      {/* Metrics summary */}
      <div style={styles.metricsRow}>
        {Object.entries(stats.bySource).map(([src, count]) => (
          <div key={src} style={styles.metricCell}>
            <span style={styles.metricLabel}>{src}</span>
            <span style={styles.metricVal}>
              {count}
            </span>
          </div>
        ))}
      </div>

      {/* Event list */}
      <div style={styles.eventList}>
        {filtered.length === 0 && (
          <div style={styles.emptyState}>
            {connected ? "AWAITING INTELLIGENCE DATA..." : "CONNECTING TO FEED..."}
          </div>
        )}
        {filtered.map((event) => (
          <EventCard
            key={event.id}
            event={event}
            onClick={() => onEventClick && onEventClick(event)}
          />
        ))}
      </div>
    </div>
  );
}

function EventCard({ event, onClick }) {
  const [expanded, setExpanded] = useState(false);
  const srcColor = SOURCE_COLORS[event.source] || "#4a5568";
  const sevColor = SEVERITY_COLORS[event.severity] || "#2a3a4a";

  return (
    <div
      style={{
        ...styles.card,
        borderLeft: `2px solid ${sevColor}`,
      }}
      onClick={() => { setExpanded((v) => !v); onClick(); }}
    >
      <div style={styles.cardTag}>
        <span style={{ color: srcColor, fontSize: 13, letterSpacing: 1.5, fontWeight: 700 }}>
          {event.tag}
        </span>
        <span style={{ color: "#ffffff", fontSize: 12, opacity: 0.5 }}>
          {timeAgo(event.ts)}
        </span>
      </div>
      <div style={styles.cardTitle}>{event.title}</div>
      {expanded && (
        <>
          <div style={styles.cardDetail}>{event.detail}</div>
          <div style={styles.cardCoord}>
            {formatCoord(event.lat, event.lon)}
          </div>
        </>
      )}
      {!expanded && (
        <div style={styles.cardCoord}>
          {formatCoord(event.lat, event.lon)}
        </div>
      )}
    </div>
  );
}

const styles = {
  panel: {
    background: "#0a0c0f",
    borderLeft: "1px solid #1e2530",
    display: "flex",
    flexDirection: "column",
    height: "100%",
    fontFamily: "'JetBrains Mono', 'Courier New', monospace",
    fontSize: 14,
    color: "#ffffff",
    minWidth: 260,
    maxWidth: 320,
  },
  header: {
    background: "#0d1117",
    borderBottom: "1px solid #1e2530",
    padding: "8px 12px",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexShrink: 0,
  },
  headerLeft: { display: "flex", alignItems: "center", gap: 10 },
  headerRight: { display: "flex", alignItems: "center", gap: 8 },
  title: { fontSize: 14, letterSpacing: 2, color: "#ffffff", textTransform: "uppercase", fontWeight: 700 },
  badge: { fontSize: 13, letterSpacing: 1.5, color: "#ffffff" },
  countBadge: { fontSize: 13, color: "#ffffff", letterSpacing: 1 },
  clearBtn: {
    fontSize: 13, color: "#ffffff", letterSpacing: 1,
    background: "transparent", border: "1px solid #3a4250",
    padding: "2px 8px", cursor: "pointer",
    fontFamily: "inherit",
  },
  filterRow: {
    display: "flex", flexWrap: "wrap", gap: 5,
    padding: "6px 10px", borderBottom: "1px solid #111519",
    flexShrink: 0,
  },
  filterChip: {
    fontSize: 13, letterSpacing: 1, padding: "2px 7px",
    background: "transparent", border: "1px solid",
    cursor: "pointer", fontFamily: "inherit",
    textTransform: "uppercase", color: "#ffffff",
  },
  searchRow: {
    display: "flex", alignItems: "center", gap: 6,
    padding: "5px 10px", borderBottom: "1px solid #111519",
    flexShrink: 0,
  },
  searchPrompt: { fontSize: 13, color: "#ffffff", letterSpacing: 1 },
  searchInput: {
    flex: 1, background: "transparent", border: "none",
    color: "#ffffff", fontFamily: "inherit", fontSize: 13,
    outline: "none", letterSpacing: 0.5,
  },
  metricsRow: {
    display: "flex", flexWrap: "wrap", gap: 0,
    borderBottom: "1px solid #111519", flexShrink: 0,
  },
  metricCell: {
    display: "flex", flexDirection: "column",
    padding: "5px 10px", borderRight: "1px solid #111519",
    minWidth: 70,
  },
  metricLabel: { fontSize: 12, color: "#ffffff", letterSpacing: 1, opacity: 0.7 },
  metricVal: { fontSize: 14, fontWeight: 700, letterSpacing: 0.5, color: "#ffffff" },
  eventList: {
    overflowY: "auto", flex: 1,
  },
  emptyState: {
    padding: "24px 12px", textAlign: "center",
    fontSize: 13, color: "#ffffff", letterSpacing: 1.5, opacity: 0.5,
  },
  card: {
    padding: "8px 12px 8px 10px",
    borderBottom: "1px solid #0f1419",
    cursor: "pointer",
  },
  cardTag: {
    display: "flex", justifyContent: "space-between",
    marginBottom: 4,
  },
  cardTitle: {
    fontSize: 14, color: "#ffffff", lineHeight: 1.5, marginBottom: 3, fontWeight: 500,
  },
  cardDetail: {
    fontSize: 13, color: "#ffffff", lineHeight: 1.6, marginBottom: 4, opacity: 0.85,
  },
  cardCoord: { fontSize: 12, color: "#ffffff", letterSpacing: 0.5, opacity: 0.55 },
};
