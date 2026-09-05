import { useState, useEffect, useRef, useCallback, Suspense, lazy } from 'react';
import { Analytics } from '@vercel/analytics/react';
import IntelPanel from './components/IntelPanel';
import CommodityTicker from './components/CommodityTicker';
import ErrorBoundary from './components/ErrorBoundary';
import AgriPanel from './components/AgriPanel';
// Lazy-loaded: three.js is a large dependency (pulls the main bundle from
// ~290KB to ~830KB) that only the Orbital tab needs — code-splitting it
// means everyone else's initial load stays fast, and it's only fetched
// the first time someone actually opens that tab.
const OrbitalGlobe = lazy(() => import('./components/OrbitalGlobe'));
import { useVesselTracker } from './hooks/useVesselTracker';
import { useSatelliteTracker } from './hooks/useSatelliteTracker';
import { useAircraftTracker } from './hooks/useAircraftTracker';

// Any string that ends up interpolated into a raw HTML string passed to
// Leaflet's bindPopup() (Leaflet has no JSX-style auto-escaping — it's
// literal innerHTML) needs escaping first. Several of these strings
// ultimately trace back to a user-typed search box query (the research
// agent's place_name/reasoning are LLM output grounded in live web
// search results, so an attacker-controlled page the LLM reads from, or
// a crafted query, could otherwise land a stored-XSS payload in a
// popup). Third-party data (AQI/vessel/aircraft feeds) gets the same
// treatment for defense-in-depth, even though those sources are lower-risk.
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const MOBILE_BREAKPOINT = 860;

/** Tracks whether the viewport is narrow enough to need the mobile layout. */
function useIsMobile() {
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' && window.innerWidth <= MOBILE_BREAKPOINT
  );
  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= MOBILE_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return isMobile;
}

const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000';
const POLL_MS = 2500;

// Free OpenWeatherMap key (openweathermap.org/api, no card required — the
// Weather Maps 1.0 tile endpoint is covered by their free Weather API tier).
// Tiles are fetched straight from the browser, so this only needs a
// build-time frontend env var, no backend involvement.
const OWM_API_KEY = import.meta.env.VITE_OWM_API_KEY || '';

const WEATHER_LAYERS = {
  temp:     { type:'tile',     owmLayer: 'temp_new',     label: 'Temperature', icon: 'thermo', opacity: 0.55 },
  wind:     { type:'velocity',                            label: 'Wind Speed',  icon: 'wave',   opacity: 0.9 },
  pressure: { type:'tile',     owmLayer: 'pressure_new', label: 'Air Pressure', icon: 'target', opacity: 0.55 },
};

// Bhuvan/GEE-Explorer-style toggleable satellite imagery layers — each
// backed by a cached GEE tile URL fetched from /api/v1/layers/{key}.
// GEE's per-tile compute cost for these four layers is structurally too
// expensive at low/world zoom (huge geographic area per tile -> huge
// intersecting image count) — not just slow, it hangs for 2+ minutes and
// then effectively never resolves. Gate the toggle + the tile layer itself
// at a zoom where a tile covers a small enough area to compute promptly.
const SATELLITE_MIN_ZOOM = 6;

const SATELLITE_LAYERS = {
  // True Color is NOT a GEE live-compute layer like the other three — it's
  // a pre-rendered, pre-tiled global mosaic (EOX Sentinel-2 cloudless,
  // s2maps.eu), fetched directly from EOX's own tile server with no
  // backend round-trip at all. That's *why* it has no minZoom restriction
  // below (EOX_TRUE_COLOR_URL) while the other three genuinely need one —
  // it's not live per-tile compute, so there's no world-zoom cost to gate.
  // Free for non-commercial use; requires attribution, shown in the UI
  // note wherever this layer is toggled (see EOX_ATTRIBUTION).
  true_color: { label: 'True Color',      icon: 'map',    opacity: 0.9,  desc: 'Sentinel-2 cloudless global mosaic (EOX), any zoom' },
  worldview:  { label: 'Daily Worldview', icon: 'globe',  opacity: 0.9,  desc: "NASA GIBS daily satellite view (MODIS Terra), any zoom" },
  ndvi:       { label: 'NDVI Vegetation', icon: 'leaf',   opacity: 0.75, desc: 'Vegetation health index' },
  sar:        { label: 'SAR / Microwave', icon: 'radio',  opacity: 0.75, desc: 'Sentinel-1, sees through cloud cover' },
  thermal:    { label: 'Thermal / IR',    icon: 'thermo', opacity: 0.75, desc: 'Landsat surface temperature' },
};

// Pre-rendered, pre-tiled global Sentinel-2 cloudless mosaic — free,
// keyless, works at every zoom level with no per-tile compute (unlike the
// other three GEE-backed layers). See SATELLITE_LAYERS.true_color comment.
const EOX_TRUE_COLOR_URL = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg';
const EOX_ATTRIBUTION = 'Sentinel-2 cloudless \u2013 s2maps.eu by EOX IT Services GmbH';

// NASA GIBS ("Global Imagery Browse Services") — the same free, keyless,
// pre-tiled WMTS service that actually powers nasa.gov's own Worldview
// tool. Requested by name ("something like NASA's Worldview") as a data
// source to add if feasible — this is that exact source, not a lookalike.
// Same architectural category as True Color: pre-tiled, no GEE compute,
// no minZoom gate. Imagery has ~1 day processing latency, so this
// requests YESTERDAY's date, not "today" (GIBS has no "latest" alias in
// its plain XYZ URL scheme — a real date must be supplied).
function gibsWorldviewUrl() {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - 1);
  const dateStr = d.toISOString().slice(0, 10);
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${dateStr}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
}
const GIBS_ATTRIBUTION = 'Imagery courtesy NASA GIBS/Worldview';

// Only these three genuinely need the low-zoom gate (live GEE compute,
// see global_layers.py) — True Color and Worldview are both pre-tiled and exempt.
const GEE_GATED_LAYERS = new Set(['ndvi', 'sar', 'thermal']);


// Official OpenWeatherMap Weather Maps 1.0 color stops (openweathermap.org/map_legend),
// so the legend shown in the app matches exactly what the tiles are actually
// drawing rather than an approximation. Pressure converted Pa -> hPa.
// Note purple/magenta on the temperature scale is the COLD end (below -40°C),
// not hot -- that's why Antarctica renders in that color, correctly.
const WEATHER_LEGENDS = {
  temp: {
    unit: '°C',
    stops: [
      { v:-40, c:'rgb(130,22,146)' },
      { v:-20, c:'rgb(32,140,236)' },
      { v:0,   c:'rgb(35,221,221)' },
      { v:10,  c:'rgb(194,255,40)' },
      { v:20,  c:'rgb(255,240,40)' },
      { v:30,  c:'rgb(252,128,20)' },
    ],
  },
  wind: {
    unit: 'm/s',
    stops: [
      { v:1,   c:'rgb(255,255,255)' },
      { v:15,  c:'rgb(179,100,188)' },
      { v:25,  c:'rgb(63,33,59)' },
      { v:50,  c:'rgb(116,76,172)' },
      { v:100, c:'rgb(70,0,175)' },
      { v:200, c:'rgb(13,17,38)' },
    ],
  },
  pressure: {
    unit: 'hPa',
    stops: [
      { v:940,  c:'rgb(0,115,255)' },
      { v:980,  c:'rgb(75,208,214)' },
      { v:1010, c:'rgb(176,247,32)' },
      { v:1040, c:'rgb(251,85,21)' },
      { v:1080, c:'rgb(198,0,0)' },
    ],
  },
};

/** Build a CSS linear-gradient string whose stop *positions* are proportional
 * to the real values, not evenly spaced, so the gradient's visual shape
 * matches OWM's actual (non-linear) color ramp. */
function legendGradient(stops) {
  const min = stops[0].v, max = stops[stops.length - 1].v;
  const parts = stops.map(s => `${s.c} ${((s.v - min) / (max - min) * 100).toFixed(1)}%`);
  return `linear-gradient(to right, ${parts.join(', ')})`;
}

const METRICS_META = {
  vegetation_change:        { label: 'Vegetation Change',       color: '#4a7c59', desc: 'NDVI green cover loss/gain' },
  builtup_change:           { label: 'Built-up Change',         color: '#c9933a', desc: 'Urban expansion analysis' },
  water_change:             { label: 'Water Body Change',       color: '#2a6abd', desc: 'Surface water gain/loss' },
  flood_detection:          { label: 'Flood Detection',         color: '#1a5a9a', desc: 'SAR flood mapping' },
  fire_detection:           { label: 'Fire Detection',          color: '#8b2020', desc: 'Active fire mapping' },
  drought_index:            { label: 'Drought Index',           color: '#8a6010', desc: 'Drought severity' },
  land_surface_temperature: { label: 'Land Surface Temp',       color: '#7a3020', desc: 'Heat & UHI analysis' },
  deforestation:            { label: 'Deforestation',           color: '#4a7c59', desc: 'Forest loss detection' },
  soil_moisture:            { label: 'Soil Moisture',           color: '#6a5a30', desc: 'Soil & crop stress' },
};

// ── Icon library — minimal line-art SVGs, one per intel/vessel type ──────────
const ICONS = {
  // USGS — seismic epicenter rings
  QUAKE: (fill, border) => `
    <svg viewBox="0 0 16 16" width="16" height="16">
      <circle cx="8" cy="8" r="1.6" fill="${fill}"/>
      <circle cx="8" cy="8" r="4.2" fill="none" stroke="${border}" stroke-width="0.9" opacity="0.75"/>
      <circle cx="8" cy="8" r="6.6" fill="none" stroke="${border}" stroke-width="0.7" opacity="0.4"/>
    </svg>`,
  // NASA FIRMS — flame
  FLAME: (fill, border) => `
    <svg viewBox="0 0 16 16" width="16" height="16">
      <path d="M8 1.2C5.3 4 4.2 6.8 5 9.6 5.5 11.4 6.8 12.6 8 12.6 9.2 12.6 10.5 11.4 11 9.6 11.8 6.8 10.7 4 8 1.2Z"
        fill="${fill}" stroke="${border}" stroke-width="0.6"/>
      <path d="M8 5.2C7 6.8 6.6 8.1 7 9.4 7.2 10.1 7.7 10.6 8 10.6 8.3 10.6 8.8 10.1 9 9.4 9.4 8.1 9 6.8 8 5.2Z"
        fill="${border}" opacity="0.6"/>
    </svg>`,
  // GDELT — news / document
  NEWS: (fill, border) => `
    <svg viewBox="0 0 16 16" width="16" height="16">
      <rect x="3" y="2" width="10" height="12" rx="1" fill="${fill}" opacity="0.92" stroke="${border}" stroke-width="0.6"/>
      <line x1="5" y1="5"  x2="11" y2="5"  stroke="${border}" stroke-width="0.9"/>
      <line x1="5" y1="8"  x2="11" y2="8"  stroke="${border}" stroke-width="0.9"/>
      <line x1="5" y1="11" x2="9"  y2="11" stroke="${border}" stroke-width="0.9"/>
    </svg>`,
  // ACLED — conflict / clash marker
  CONFLICT: (fill, border) => `
    <svg viewBox="0 0 16 16" width="16" height="16">
      <circle cx="8" cy="8" r="6.2" fill="${fill}" opacity="0.18" stroke="${border}" stroke-width="0.8"/>
      <line x1="5" y1="5" x2="11" y2="11" stroke="${border}" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="11" y1="5" x2="5" y2="11" stroke="${border}" stroke-width="1.5" stroke-linecap="round"/>
    </svg>`,
  // Predicted-path tip — small open chevron pointing along the forecast bearing
  FORECAST_TIP: (fill, border) => `
    <svg viewBox="0 0 16 16" width="11" height="11">
      <path d="M8 1.5 L13.5 12 L8 9.3 L2.5 12 Z" fill="${fill}" opacity="0.85" stroke="${border}" stroke-width="0.6"/>
    </svg>`,
  // Generic dot fallback
  DOT: (fill, border) => `
    <svg viewBox="0 0 16 16" width="10" height="10">
      <circle cx="8" cy="8" r="5" fill="${fill}" stroke="${border}" stroke-width="1.2"/>
    </svg>`,
  // CARGO / OTHER vessel — bold filled hull silhouette with a bright white
  // outline (so it stays crisp against the dark basemap at small sizes) and
  // a light deckhouse block near the stern, so it reads as "boat" at a
  // glance instead of a thin triangle sliver.
  SHIP_CARGO: (fill, border) => `
    <svg viewBox="0 0 18 18" width="22" height="22">
      <path d="M9 1C11.4 4.6 12.6 7.8 12.6 10.8L12.6 13.6C12.6 15 11.4 16.1 10 16.1L8 16.1C6.6 16.1 5.4 15 5.4 13.6L5.4 10.8C5.4 7.8 6.6 4.6 9 1Z"
        fill="${fill}" stroke="#ffffff" stroke-width="1.1"/>
      <rect x="7" y="11.4" width="4" height="3.2" rx="0.5" fill="#ffffff" opacity="0.85"/>
    </svg>`,
  // TANKER vessel — a literal oil barrel/drum, bold and filled with a white
  // outline + banding so it's unmistakably "not a ship" at a glance.
  // Non-directional on purpose (see createVesselMarker) — a barrel has no bow.
  BARREL: (fill, border) => `
    <svg viewBox="0 0 18 18" width="22" height="22">
      <rect x="4.6" y="1.8" width="8.8" height="14.4" rx="2.6" fill="${fill}" stroke="#ffffff" stroke-width="1.2"/>
      <line x1="4.6" y1="6" x2="13.4" y2="6" stroke="#ffffff" stroke-width="1.2" opacity="0.9"/>
      <line x1="4.6" y1="12" x2="13.4" y2="12" stroke="#ffffff" stroke-width="1.2" opacity="0.9"/>
    </svg>`,
  // PASSENGER vessel — ferry with cabin
  FERRY: (fill, border) => `
    <svg viewBox="0 0 18 18" width="22" height="22">
      <path d="M3.4 12.4 L14.6 12.4 L12.2 16 L5.8 16 Z" fill="${fill}" stroke="#ffffff" stroke-width="1"/>
      <rect x="6.6" y="6.8" width="4.8" height="5.6" rx="0.6" fill="${fill}" stroke="#ffffff" stroke-width="1"/>
      <rect x="8" y="2.6" width="2" height="4.2" fill="#ffffff" opacity="0.9"/>
    </svg>`,
  // FISHING vessel — small boat with pole
  FISHBOAT: (fill, border) => `
    <svg viewBox="0 0 18 18" width="20" height="20">
      <path d="M3.4 11.6 L14.6 11.6 L12.2 15.2 L5.8 15.2 Z" fill="${fill}" stroke="#ffffff" stroke-width="1"/>
      <line x1="9" y1="11.6" x2="9" y2="2.4" stroke="#ffffff" stroke-width="1.3"/>
      <line x1="9" y1="3.6" x2="13.4" y2="5.8" stroke="#ffffff" stroke-width="1"/>
    </svg>`,
  // AIRCRAFT — minimal top-down plane silhouette, rotated to heading in
  // createAircraftMarker the same way vessel icons rotate to course.
  PLANE: (fill, border) => `
    <svg viewBox="0 0 18 18" width="18" height="18">
      <path d="M9 1 L10 6.5 L16.5 10 L16.5 11.6 L10 9.6 L10 13.6 L12.6 15.4 L12.6 16.6 L9 15.6 L5.4 16.6 L5.4 15.4 L8 13.6 L8 9.6 L1.5 11.6 L1.5 10 L8 6.5 Z"
        fill="${fill}" stroke="#ffffff" stroke-width="0.9"/>
    </svg>`,
  // SATELLITE — small body with two solar-panel wings, non-directional (an
  // orbital position marker, not a heading-indicator like the plane/ships)
  SATELLITE: (fill, border) => `
    <svg viewBox="0 0 18 18" width="16" height="16">
      <rect x="7" y="7" width="4" height="4" rx="0.6" fill="${fill}" stroke="#ffffff" stroke-width="0.9"/>
      <rect x="0.8" y="7.4" width="4.6" height="3.2" fill="${fill}" opacity="0.85" stroke="#ffffff" stroke-width="0.6"/>
      <rect x="12.6" y="7.4" width="4.6" height="3.2" fill="${fill}" opacity="0.85" stroke="#ffffff" stroke-width="0.6"/>
      <line x1="5.4" y1="9" x2="7" y2="9" stroke="#ffffff" stroke-width="0.7"/>
      <line x1="11" y1="9" x2="12.6" y2="9" stroke="#ffffff" stroke-width="0.7"/>
    </svg>`,
};

const INTEL_ICON_FOR_SOURCE = {
  'USGS':       'QUAKE',
  'NASA FIRMS': 'FLAME',
  'GDELT':      'NEWS',
  'ACLED':      'CONFLICT',
};

const VESSEL_ICON_FOR_CATEGORY = {
  TANKER:    'BARREL',
  CARGO:     'SHIP_CARGO',
  PASSENGER: 'FERRY',
  FISHING:   'FISHBOAT',
  OTHER:     'SHIP_CARGO',
};

const INTEL_COLORS = {
  'USGS':       { fill: '#c9603a', border: '#e08050' },
  'NASA FIRMS': { fill: '#d4622a', border: '#f0803a' },
  'GDELT':      { fill: '#3a6ab0', border: '#5a8ad0' },
  'ACLED':      { fill: '#a03a4a', border: '#c05a6a' },
};

const VESSEL_COLORS = {
  TANKER:    { fill: '#c9933a', border: '#e0aa50', label: 'Tanker' },
  CARGO:     { fill: '#2a6abd', border: '#4a8add', label: 'Cargo / Bulk' },
  PASSENGER: { fill: '#3a8a6a', border: '#50aa80', label: 'Passenger' },
  FISHING:   { fill: '#6a5a8a', border: '#8a7aaa', label: 'Fishing' },
  OTHER:     { fill: '#5a6470', border: '#7a8490', label: 'Other' },
};

const EXAMPLES = [
  'How much green cover did this area lose since 2020?',
  'Show urban expansion between 2018 and 2023',
  'Detect flood events in the last 2 years',
  'Analyze deforestation from 2015 to 2024',
  'What is the drought severity in this region since 2021?',
  'Map burn scars from wildfires in 2023',
  'Show land surface temperature change since 2019',
  'Has soil moisture decreased in this area since 2020?',
];

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  bg: '#0a0c0f',
  surface: '#0d1117',
  surface2: '#0f1419',
  border: '#2a3040',
  border2: '#3a4250',
  text: '#ffffff',
  text2: 'rgba(255,255,255,0.8)',
  text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4',
};

function fmtKey(k) { return k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()); }
function fmtVal(k, v) {
  if (typeof v !== 'number') return String(v);
  if (k.includes('pct')||k.includes('rate')) return `${v.toFixed(1)}%`;
  if (k.includes('km2')||k.includes('area')) return `${v.toFixed(2)} km2`;
  if (k.includes('_c')||k.includes('temp')) return `${v.toFixed(1)}C`;
  if (k.includes('count')||k.includes('years')) return v.toFixed(0);
  return v.toFixed(3);
}

// ── Dead-reckoning projection — where a vessel is headed, not just where it's been ──
const EARTH_R_M = 6371000;
const KNOTS_TO_MS = 0.514444;

/** Great-circle destination point given a start coord, bearing (deg), and distance (m). */
function destinationPoint(lat, lon, bearingDeg, distanceM) {
  const rad = Math.PI / 180;
  const dR = distanceM / EARTH_R_M;
  const brng = bearingDeg * rad;
  const lat1 = lat * rad, lon1 = lon * rad;
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(dR) + Math.cos(lat1) * Math.sin(dR) * Math.cos(brng));
  const lon2 = lon1 + Math.atan2(
    Math.sin(brng) * Math.sin(dR) * Math.cos(lat1),
    Math.cos(dR) - Math.sin(lat1) * Math.sin(lat2)
  );
  return [lat2 / rad, ((lon2 / rad) + 540) % 360 - 180];
}

/** Great-circle distance between two points, in nautical miles. */
function haversineNm(lat1, lon1, lat2, lon2) {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return (EARTH_R_M * c) / 1852; // meters → nautical miles
}

const PREDICT_HOURS = [1, 2, 4, 8];        // forecast checkpoints
const MIN_SOG_FOR_PREDICTION = 0.8;        // knots — skip stationary/anchored vessels

/** Build a forecast track (dead reckoning) from a vessel's current course + speed. */
function buildPredictedPath(vessel) {
  const sog = typeof vessel.sog === 'number' ? vessel.sog : 0;
  const cog = typeof vessel.cog === 'number' ? vessel.cog : null;
  if (cog === null || sog < MIN_SOG_FOR_PREDICTION) return null;
  const speedMs = sog * KNOTS_TO_MS;
  const points = [[vessel.lat, vessel.lon]];
  for (const hrs of PREDICT_HOURS) {
    points.push(destinationPoint(vessel.lat, vessel.lon, cog, speedMs * hrs * 3600));
  }
  return points;
}

// ── UI line-icon set — metric types, nav tabs, and chrome controls ───────────
function Icon({ name, size = 16, style }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', style };
  switch (name) {
    case 'leaf':       return <svg {...p}><path d="M5 20C5 11 11 4 20 4c0 9-7 15-15 16Z"/><path d="M6.5 17.5 15 9"/></svg>;
    case 'building':   return <svg {...p}><rect x="5" y="3" width="9" height="18"/><rect x="14" y="9" width="5" height="12"/><path d="M8 7h2M8 11h2M8 15h2"/></svg>;
    case 'droplet':    return <svg {...p}><path d="M12 3s6.5 7.2 6.5 11.5A6.5 6.5 0 1 1 5.5 14.5C5.5 10.2 12 3 12 3Z"/></svg>;
    case 'wave':       return <svg {...p}><path d="M2 17c1.5 1.6 3 1.6 4.5 0s3-1.6 4.5 0 3 1.6 4.5 0 3-1.6 4.5 0"/><path d="M2 11c1.5 1.6 3 1.6 4.5 0s3-1.6 4.5 0 3 1.6 4.5 0 3-1.6 4.5 0"/></svg>;
    case 'flame':      return <svg {...p}><path d="M12 2c1 4-4 5-4 10a4 4 0 0 0 8 0c0-1.6-1-2.6-1-2.6 1.5 1 2 3 2 4.6a5 5 0 0 1-10 0C7 8 12 6 12 2Z"/></svg>;
    case 'drought':    return <svg {...p}><circle cx="12" cy="7" r="3"/><path d="M4 20l4-5M10 20l3-6M16 20l4-5"/></svg>;
    case 'thermo':      return <svg {...p}><path d="M12 3a2 2 0 0 0-2 2v9.5a4 4 0 1 0 4 0V5a2 2 0 0 0-2-2Z"/><path d="M12 8v6"/></svg>;
    case 'tree':       return <svg {...p}><path d="M12 2 6 12h3l-4 8h14l-4-8h3z"/><path d="M12 20v2"/></svg>;
    case 'sprout':     return <svg {...p}><path d="M12 21v-8"/><path d="M12 13C7 13 5 9 5 5c4 0 7 2 7 8Z"/><path d="M12 13c5 0 7-4 7-8-4 0-7 2-7 8Z"/></svg>;
    case 'target':     return <svg {...p}><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.5"/></svg>;
    case 'clock':      return <svg {...p}><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>;
    case 'anchor':     return <svg {...p}><circle cx="12" cy="5" r="2"/><path d="M12 7v14M6 13a6 6 0 0 0 12 0M4 13h4M16 13h4"/></svg>;
    case 'book':       return <svg {...p}><path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H12v18H5.5A1.5 1.5 0 0 1 4 19.5Z"/><path d="M20 4.5A1.5 1.5 0 0 0 18.5 3H12v18h6.5a1.5 1.5 0 0 0 1.5-1.5Z"/></svg>;
    case 'map':        return <svg {...p}><path d="M9 4 4 6v14l5-2 6 2 5-2V4l-5 2-6-2Z"/><path d="M9 4v14M15 6v14"/></svg>;
    case 'globe':      return <svg {...p}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg>;
    case 'sliders':    return <svg {...p}><path d="M4 6h9M17 6h3M4 18h3M11 18h9"/><circle cx="14" cy="6" r="2.2"/><circle cx="8" cy="18" r="2.2"/></svg>;
    case 'radio':      return <svg {...p}><circle cx="12" cy="12" r="2.2"/><path d="M8.3 15.7a5.5 5.5 0 0 1 0-7.4M15.7 8.3a5.5 5.5 0 0 1 0 7.4M5.5 18.5a10 10 0 0 1 0-13M18.5 5.5a10 10 0 0 1 0 13"/></svg>;
    case 'plane':      return <svg {...p}><path d="M2.5 12.5 21 6.5v3.6L13 14v5.3l3 2.2v1.4l-4-1.3-4 1.3v-1.4l3-2.2V14L2.5 16.1Z"/></svg>;
    case 'satellite-dish': return <svg {...p}><path d="M4 14a8 8 0 0 1 8-8"/><path d="M4 14a8 8 0 0 0 8 8"/><circle cx="12" cy="12" r="1.7"/><path d="M12 12 20 5M17 4l3 3-3 3"/></svg>;
    case 'close':      return <svg {...p}><path d="M6 6l12 12M18 6 6 18"/></svg>;
    case 'ship':       return <svg {...p}><path d="M4 15h16l-2 4H6Z"/><path d="M6 15V8h8l3 7M9 8V4h2v4"/></svg>;
    default:           return null;
  }
}

const METRIC_ICON_FOR = {
  vegetation_change: 'leaf', builtup_change: 'building', water_change: 'droplet',
  flood_detection: 'wave', fire_detection: 'flame', drought_index: 'drought',
  land_surface_temperature: 'thermo', deforestation: 'tree', soil_moisture: 'sprout',
};

// ── Intel marker — type-specific icon (flame, quake rings, news, conflict) ───
function createIntelMarker(event) {
  const c = INTEL_COLORS[event.source] || { fill: '#4a5568', border: '#6b7a8d' };
  const iconKey = INTEL_ICON_FOR_SOURCE[event.source] || 'DOT';
  const svg = ICONS[iconKey](c.fill, c.border);
  const sizeBoost = event.severity === 'critical' ? 1.25 : event.severity === 'warn' ? 1.05 : 1;
  const icon = L.divIcon({
    className: '',
    html: `<div style="
      transform: scale(${sizeBoost});
      filter: drop-shadow(0 0 4px ${c.fill}aa);
      cursor:pointer;
    ">${svg}</div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
  return L.marker([event.lat, event.lon], { icon, zIndexOffset: 100 });
}

// ── Vessel marker — type-specific hull/barrel/ferry icon, rotated to course ──
function createVesselMarker(vessel) {
  const c = VESSEL_COLORS[vessel.category] || VESSEL_COLORS.OTHER;
  const iconKey = VESSEL_ICON_FOR_CATEGORY[vessel.category] || 'SHIP_CARGO';
  const svg = ICONS[iconKey](c.fill, c.border);
  const cog = typeof vessel.cog === 'number' ? vessel.cog : 0;
  // A barrel has no bow — rotating it with course would be misleading, not
  // informative, so only heading-shaped hull icons rotate.
  const rotation = iconKey === 'BARREL' ? 0 : cog;
  const icon = L.divIcon({
    className: '',
    html: `<div style="
      transform: rotate(${rotation}deg);
      filter: drop-shadow(0 0 4px ${c.fill}cc) drop-shadow(0 0 1px #000000aa);
      display:flex; align-items:center; justify-content:center;
    ">${svg}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
  return L.marker([vessel.lat, vessel.lon], { icon, zIndexOffset: 50 });
}

// ── Weather overlay toggles — click a button, that layer switches on/off ────
function WeatherLayerToggles({ active, onToggle }) {
  return (
    <div>
      <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, marginBottom:9, textTransform:'uppercase' }}>Overlay layers</div>
      <div style={{ display:'flex', flexDirection:'column', gap:7 }}>
        {Object.entries(WEATHER_LAYERS).map(([key, meta]) => {
          const on = !!active[key];
          const legend = WEATHER_LEGENDS[key];
          return (
            <div key={key}>
              <button onClick={() => onToggle(key)}
                style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', minHeight:44, width:'100%',
                  fontFamily:S.mono, letterSpacing:0.3,
                  background: on ? 'rgba(126,184,212,0.10)' : S.surface2,
                  border: `1px solid ${on ? S.accent : S.border}`,
                  borderRadius: on ? '3px 3px 0 0' : 3,
                  color: on ? S.accent : S.text2, cursor:'pointer',
                  textAlign:'left', transition:'border-color 0.15s, background 0.15s' }}>
                <Icon name={meta.icon} size={18} style={{ flexShrink:0, opacity: on ? 1 : 0.75 }} />
                <span style={{ fontSize:14, flex:1 }}>{meta.label}</span>
                <span style={{ fontSize:11, letterSpacing:1, opacity:0.7 }}>{on ? 'ON' : 'OFF'}</span>
              </button>
              {on && legend && (
                <div style={{ border:`1px solid ${S.accent}`, borderTop:'none', borderRadius:'0 0 3px 3px', padding:'8px 12px 9px', background:'rgba(126,184,212,0.04)' }}>
                  <div style={{ height:8, borderRadius:2, background: legendGradient(legend.stops) }} />
                  <div style={{ display:'flex', justifyContent:'space-between', marginTop:4 }}>
                    <span style={{ fontSize:11, color:S.text3, fontFamily:S.mono }}>{legend.stops[0].v}{legend.unit}</span>
                    <span style={{ fontSize:11, color:S.text3, fontFamily:S.mono }}>{legend.stops[legend.stops.length-1].v}{legend.unit}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SatelliteLayerToggles({ active, onToggle, loadingKey, currentZoom }) {
  const zoomBlocked = currentZoom != null && currentZoom < SATELLITE_MIN_ZOOM;
  return (
    <div>
      <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, marginBottom:9, textTransform:'uppercase' }}>Satellite Layers</div>
      {zoomBlocked && (
        <div style={{ fontSize:11, color:S.text3, fontFamily:S.mono, marginBottom:9, padding:'8px 10px',
          border:`1px solid ${S.border}`, borderRadius:3, background:S.surface2, lineHeight:1.4 }}>
          Zoom in to load NDVI / SAR / Thermal — these render per-tile from live imagery and world view is too large an area to compute. True Color is a pre-rendered mosaic and works at any zoom.
        </div>
      )}
      <div style={{ display:'flex', flexDirection:'column', gap:7 }}>
        {Object.entries(SATELLITE_LAYERS).map(([key, meta]) => {
          const on = !!active[key];
          const loading = loadingKey === key;
          const gated = GEE_GATED_LAYERS.has(key);
          const disabled = loading || (gated && zoomBlocked && !on);
          return (
            <button key={key} onClick={() => onToggle(key)} disabled={disabled}
              style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', minHeight:44, width:'100%',
                fontFamily:S.mono, letterSpacing:0.3,
                background: on ? 'rgba(126,184,212,0.10)' : S.surface2,
                border: `1px solid ${on ? S.accent : S.border}`, borderRadius:3,
                color: on ? S.accent : (disabled ? S.text3 : S.text2), cursor: disabled ? (loading ? 'wait' : 'not-allowed') : 'pointer',
                opacity: disabled && !loading ? 0.55 : 1,
                textAlign:'left', transition:'border-color 0.15s, background 0.15s' }}>
              <Icon name={meta.icon} size={18} style={{ flexShrink:0, opacity: on ? 1 : 0.75 }} />
              <span style={{ flex:1 }}>
                <span style={{ fontSize:14, display:'block' }}>{meta.label}</span>
                <span style={{ fontSize:11, color:S.text3, display:'block', marginTop:1 }}>{meta.desc}</span>
              </span>
              <span style={{ fontSize:11, letterSpacing:1, opacity:0.7 }}>{loading ? '...' : (on ? 'ON' : 'OFF')}</span>
            </button>
          );

        })}
      </div>
    </div>
  );
}

// ── Orbital tab sidebar content: toggles, search, list, and the detail
// readout for whatever satellite/aircraft is selected — lives here (the
// actual left sidebar) instead of floating over the 3D canvas, matching
// how every other tab's controls/details live in the sidebar. ─────────────
const ORBITAL_COLORS = { station: '#ff6b6b', satellite: '#9b8ce8', aircraft: '#e8c15c' };
const ORBITAL_KIND_LABEL = { station: 'Space Station', satellite: 'Tracked Satellite', aircraft: 'Aircraft' };

function OrbitalSidebarPanel({
  showSatellites, onToggleSatellites, showAircraft, onToggleAircraft,
  satelliteCount, aircraftCount, satLoaded, satDebug,
  search, onSearchChange, filteredList, selected, onSelect,
}) {
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>
      <div style={{ padding:'16px 14px 10px' }}>
        <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, textTransform:'uppercase', marginBottom:9 }}>Orbital View</div>
        <div style={{ display:'flex', flexDirection:'column', gap:7 }}>
          <button onClick={onToggleSatellites}
            style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', minHeight:44, width:'100%',
              fontFamily:S.mono, letterSpacing:0.3,
              background: showSatellites ? 'rgba(155,140,232,0.10)' : S.surface2,
              border: `1px solid ${showSatellites ? '#9b8ce8' : S.border}`, borderRadius:3,
              color: showSatellites ? '#c3b8f5' : S.text2, cursor:'pointer', textAlign:'left' }}>
            <span style={{ flex:1 }}>
              <span style={{ fontSize:14, display:'block' }}>Satellites</span>
              <span style={{ fontSize:11, color:S.text3, display:'block', marginTop:1 }}>Space stations + brightest visual-magnitude satellites</span>
            </span>
            <span style={{ fontSize:11, letterSpacing:1, opacity:0.7 }}>{showSatellites ? satelliteCount : 'OFF'}</span>
          </button>
          <button onClick={onToggleAircraft}
            style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', minHeight:44, width:'100%',
              fontFamily:S.mono, letterSpacing:0.3,
              background: showAircraft ? 'rgba(232,193,92,0.10)' : S.surface2,
              border: `1px solid ${showAircraft ? '#e8c15c' : S.border}`, borderRadius:3,
              color: showAircraft ? '#f0d488' : S.text2, cursor:'pointer', textAlign:'left' }}>
            <span style={{ flex:1 }}>
              <span style={{ fontSize:14, display:'block' }}>Aircraft</span>
              <span style={{ fontSize:11, color:S.text3, display:'block', marginTop:1 }}>Live global flight positions (adsb.lol)</span>
            </span>
            <span style={{ fontSize:11, letterSpacing:1, opacity:0.7 }}>{showAircraft ? aircraftCount : 'OFF'}</span>
          </button>
        </div>
        <div style={{ fontSize:11, color:S.text3, lineHeight:1.5, marginTop:9 }}>
          {showSatellites && !satLoaded && 'Loading orbital elements… '}
          Drag the globe to rotate, scroll to zoom in.
        </div>
        {showSatellites && (satDebug?.lastError || (satLoaded && satDebug?.propagatedCount === 0)) && (
          <div style={{ fontSize:10, color:'#ff9d9d', marginTop:6, lineHeight:1.4 }}>
            Satellite debug: fetched {satDebug.fetchedCount}, parsed {satDebug.parsedCount}, showing {satDebug.propagatedCount}
            {satDebug.lastError ? ` — ${satDebug.lastError}` : ''}
          </div>
        )}
      </div>

      {selected && (
        <div style={{ margin:'0 14px 10px', padding:'10px 12px', background:S.surface2, border:`1px solid ${S.border}`, borderRadius:3 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:10 }}>
            <div style={{ fontSize:14, fontWeight:700, color: ORBITAL_COLORS[selected.kind], fontFamily:S.mono }}>
              {selected.kind === 'aircraft' ? (selected.callsign || selected.icao24) : selected.name}
            </div>
            <button onClick={() => onSelect(null)}
              style={{ background:'none', border:'none', color:S.text3, cursor:'pointer', fontSize:14, lineHeight:1, padding:0 }}>✕</button>
          </div>
          <div style={{ marginTop:4, opacity:0.7, textTransform:'uppercase', fontSize:11, letterSpacing:1, color:S.text3, fontFamily:S.mono }}>
            {ORBITAL_KIND_LABEL[selected.kind]}
          </div>
          {selected.kind !== 'aircraft' ? (
            <div style={{ marginTop:8, display:'grid', gridTemplateColumns:'auto auto', gap:'2px 12px', fontSize:12, fontFamily:S.mono, color:S.text2 }}>
              <span style={{ opacity:0.6 }}>Latitude</span><span>{selected.lat.toFixed(2)}°</span>
              <span style={{ opacity:0.6 }}>Longitude</span><span>{selected.lon.toFixed(2)}°</span>
              <span style={{ opacity:0.6 }}>Altitude</span><span>{Math.round(selected.alt_km).toLocaleString()} km</span>
            </div>
          ) : (
            <div style={{ marginTop:8, display:'grid', gridTemplateColumns:'auto auto', gap:'2px 12px', fontSize:12, fontFamily:S.mono, color:S.text2, maxHeight:260, overflowY:'auto' }}>
              {selected.registration && <><span style={{ opacity:0.6 }}>Registration</span><span>{selected.registration}</span></>}
              {selected.type_desc && <><span style={{ opacity:0.6 }}>Type</span><span>{selected.type_desc}</span></>}
              <span style={{ opacity:0.6 }}>Status</span><span>{selected.on_ground ? 'On ground' : 'Airborne'}</span>
              {!selected.on_ground && selected.baro_altitude_m != null && <><span style={{ opacity:0.6 }}>Altitude (baro)</span><span>{Math.round(selected.baro_altitude_m).toLocaleString()} m</span></>}
              {!selected.on_ground && selected.geom_altitude_m != null && <><span style={{ opacity:0.6 }}>Altitude (GPS)</span><span>{Math.round(selected.geom_altitude_m).toLocaleString()} m</span></>}
              {selected.velocity_ms != null && <><span style={{ opacity:0.6 }}>Ground speed</span><span>{Math.round(selected.velocity_ms * 3.6)} km/h</span></>}
              {selected.tas_ms != null && <><span style={{ opacity:0.6 }}>True airspeed</span><span>{Math.round(selected.tas_ms * 3.6)} km/h</span></>}
              {selected.mach != null && <><span style={{ opacity:0.6 }}>Mach</span><span>{selected.mach.toFixed(2)}</span></>}
              {selected.heading != null && <><span style={{ opacity:0.6 }}>Heading</span><span>{Math.round(selected.heading)}°</span></>}
              {selected.vertical_rate_ms != null && Math.abs(selected.vertical_rate_ms) > 0.3 && <><span style={{ opacity:0.6 }}>Vertical rate</span><span>{selected.vertical_rate_ms > 0 ? '↑' : '↓'} {Math.abs(Math.round(selected.vertical_rate_ms * 196.85))} ft/min</span></>}
              {selected.nav_heading != null && <><span style={{ opacity:0.6 }}>Autopilot heading</span><span>{Math.round(selected.nav_heading)}°</span></>}
              {selected.nav_altitude_mcp_m != null && <><span style={{ opacity:0.6 }}>Autopilot altitude</span><span>{Math.round(selected.nav_altitude_mcp_m).toLocaleString()} m</span></>}
              {selected.squawk && <><span style={{ opacity:0.6 }}>Squawk</span><span>{selected.squawk}</span></>}
              {selected.category && <><span style={{ opacity:0.6 }}>Category</span><span>{selected.category}</span></>}
              {selected.rssi != null && <><span style={{ opacity:0.6 }}>Signal</span><span>{selected.rssi.toFixed(1)} dBFS</span></>}
              {selected.seen_pos_s != null && <><span style={{ opacity:0.6 }}>Position age</span><span>{selected.seen_pos_s.toFixed(0)}s</span></>}
              {selected.military && <><span style={{ opacity:0.6 }}>Flag</span><span style={{ color:'#ff9d9d' }}>Military</span></>}
              {selected.pia && <><span style={{ opacity:0.6 }}>Flag</span><span style={{ color:'#ff9d9d' }}>Privacy (PIA)</span></>}
              {selected.ladd && <><span style={{ opacity:0.6 }}>Flag</span><span style={{ color:'#ff9d9d' }}>Limited disclosure</span></>}
              {selected.emergency && selected.emergency !== 'none' && <><span style={{ opacity:0.6 }}>Emergency</span><span style={{ color:'#ff6b6b' }}>{selected.emergency}</span></>}
            </div>
          )}
        </div>
      )}

      <div style={{ padding:'0 14px 10px' }}>
        <input
          type="text" placeholder="Search…" value={search}
          onChange={e => onSearchChange(e.target.value)}
          style={{
            width:'100%', padding:'7px 9px', background:S.surface2, border:`1px solid ${S.border}`,
            borderRadius:3, color:S.text1, fontFamily:S.mono, fontSize:12, outline:'none', boxSizing:'border-box',
          }}
        />
      </div>
      <div style={{ flex:1, overflowY:'auto', borderTop:`1px solid ${S.border}` }}>
        {filteredList.map(item => (
          <button key={item.key} onClick={() => onSelect({ kind: item.kind, ...item.data })}
            style={{
              display:'block', width:'100%', textAlign:'left', padding:'8px 14px',
              background: selected && ((selected.kind === 'aircraft' && item.kind === 'aircraft' && selected.icao24 === item.data.icao24) || (selected.kind !== 'aircraft' && item.kind !== 'aircraft' && selected.name === item.data.name)) ? 'rgba(155,140,232,0.10)' : 'transparent',
              border:'none', borderBottom:`1px solid ${S.border}`, cursor:'pointer',
              color: ORBITAL_COLORS[item.kind], fontFamily:S.mono, fontSize:12,
            }}>
            <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{item.label}</div>
            <div style={{ fontSize:10, opacity:0.55, marginTop:2, color:S.text3 }}>{item.sub}</div>
          </button>
        ))}
        {filteredList.length === 0 && (
          <div style={{ padding:'16px 14px', fontSize:12, color:S.text3, fontFamily:S.mono }}>
            {(showSatellites || showAircraft) ? 'No matches' : 'Toggle a layer above to see data'}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Map ───────────────────────────────────────────────────────────────────────
function VayuMap({ onAreaDrawn, mapRef, drawGroupRef, intelLayerRef, vesselLayerRef, onZoomChange }) {
  const divRef = useRef(null);
  useEffect(() => {
    if (mapRef.current) return;
    const map = L.map(divRef.current, {
      zoomControl:false,
      attributionControl:false,
      worldCopyJump:false,
      maxBounds: [[-85, -180], [85, 180]],
      maxBoundsViscosity: 1.0,
      minZoom: 2,
    }).setView([26.91,75.78],5);
    // CARTO basemaps started requiring an API key on their tile endpoints
    // as of ~August 2026 (a policy change on their side, applying to
    // basemaps.cartocdn.com broadly — unrelated to anything in this repo).
    // Still free (5M tile requests/month, non-commercial) — request one at
    // https://carto.com/basemaps/apikey/ (no approval queue, key emailed
    // back immediately) and set CARTO_API_KEY. Falls back to
    // unauthenticated tiles if unset, which now show a visible
    // "API KEY REQUIRED" watermark instead of failing silently.
    const cartoKey = import.meta.env.VITE_CARTO_API_KEY;
    const cartoUrl ='https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png' + (cartoKey ? `?key=${cartoKey}` : '');
    // Register the attribution control BEFORE adding the tile layer —
    // Leaflet's Control.Attribution only picks up a layer's `attribution`
    // string via the map's layeradd event, so a layer added before the
    // control exists is missed.
    L.control.attribution({ position: 'bottomright', prefix: false }).addTo(map);
    L.tileLayer(cartoUrl, {
      subdomains:'abcd', maxZoom:20, noWrap:true, bounds:[[-85,-180],[85,180]],
      // CARTO's free tier is conditioned on attribution staying visible on
      // the map (see their basemap terms) — attributionControl is off
      // above for a cleaner UI, so wire the attribution string through to
      // the manually-placed control instead of dropping it entirely.
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions" target="_blank" rel="noreferrer">CARTO</a>',
    }).addTo(map);
    L.control.zoom({ position:'topright' }).addTo(map);

    // Track zoom for the satellite-layer minZoom gate — GEE's own per-tile
    // compute cost is structurally too expensive at low/world zoom for
    // these four layers (huge geographic area per tile -> huge intersecting
    // image count), so the toggle panel needs to know current zoom to warn
    // the user instead of silently doing nothing for 2+ minutes.
    if (onZoomChange) {
      onZoomChange(map.getZoom());
      map.on('zoomend', () => onZoomChange(map.getZoom()));
    }

    // Intel markers layer group
    const ig = L.layerGroup().addTo(map);
    intelLayerRef.current = ig;

    // Vessel markers layer group (maritime/logistics tracking)
    const vg = L.layerGroup().addTo(map);
    vesselLayerRef.current = vg;

    const dg = new L.FeatureGroup(); map.addLayer(dg); drawGroupRef.current = dg;
    const dc = new L.Control.Draw({
      position:'topright',
      edit:{ featureGroup:dg, remove:true },
      draw:{
        polygon:{ shapeOptions:{ color:'#2a6abd', weight:1.5, fillOpacity:0.06 } },
        rectangle:{ shapeOptions:{ color:'#2a6abd', weight:1.5, fillOpacity:0.06 } },
        polyline:false, circle:false, marker:false, circlemarker:false,
      },
    });
    map.addControl(dc);
    map.on(L.Draw.Event.CREATED, e => { dg.clearLayers(); dg.addLayer(e.layer); onAreaDrawn(e.layer.toGeoJSON().geometry); });
    map.on(L.Draw.Event.EDITED, e => { e.layers.eachLayer(l => onAreaDrawn(l.toGeoJSON().geometry)); });
    map.on(L.Draw.Event.DELETED, () => { if(dg.getLayers().length===0) onAreaDrawn(null); });
    mapRef.current = map;

    // Keep Leaflet's internal size in sync — container width changes when
    // switching between the desktop 3-column layout and the mobile full-screen
    // layout, which Leaflet won't detect on its own.
    const ro = new ResizeObserver(() => map.invalidateSize());
    ro.observe(divRef.current);
    return () => ro.disconnect();
  }, []);
  return <div ref={divRef} style={{ width:'100%', height:'100%' }} />;
}

// ── Place search bar — geocode a place name straight to its boundary,
// alongside (not replacing) manual polygon drawing. Uses Nominatim
// (OpenStreetMap) which returns an actual boundary polygon for named
// places (districts, cities, blocks) when one exists in OSM, not just a
// point — falls back to a small box around the point if OSM only has a
// point for that place. ─────────────────────────────────────────────────────
// ── Air quality check — queries the current map-center coordinates against
// Open-Meteo's free Air Quality API (same no-key provider as the wind
// layer) and shows PM2.5/PM10/US AQI with a color-coded category. ─────────
function AirQualityCheck({ mapRef, apiUrl }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const check = async () => {
    const map = mapRef.current;
    if (!map) return;
    const center = map.getCenter();
    setLoading(true); setError(null); setResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/intel/air-quality?lat=${center.lat}&lon=${center.lng}`);
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Air quality lookup failed');
      setResult(await resp.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
      <div style={{ fontSize:14, color:S.text3, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase' }}>Air Quality</div>
      <div style={{ fontSize:13, color:S.text3, lineHeight:1.6 }}>
        Checks current air quality at the center of the visible map — pan the map to a location first, then check.
      </div>
      <button onClick={check} disabled={loading}
        style={{
          padding:'8px', fontSize:13, fontFamily:S.mono, letterSpacing:1.5, textTransform:'uppercase',
          background: loading ? S.surface2 : 'rgba(126,184,212,0.1)',
          border:`1px solid ${loading ? S.border : S.accent}`, color: loading ? S.text3 : S.accent,
          cursor: loading ? 'not-allowed' : 'pointer',
        }}>
        {loading ? 'CHECKING...' : 'CHECK AIR QUALITY HERE'}
      </button>
      {error && (
        <div style={{ fontSize:13, color:S.text2, background:'rgba(139,32,32,0.08)', border:'1px solid rgba(139,32,32,0.3)', padding:'7px 9px' }}>
          {error}
        </div>
      )}
      {result && (
        <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
          <div style={{
            display:'flex', alignItems:'baseline', gap:10, padding:'12px',
            background:S.surface2, border:`1px solid ${result.color}`,
          }}>
            <div style={{ fontSize:28, fontFamily:S.mono, fontWeight:700, color:result.color }}>
              {result.us_aqi ?? 'N/A'}
            </div>
            <div>
              <div style={{ fontSize:13, fontFamily:S.mono, letterSpacing:1, textTransform:'uppercase', color:result.color }}>
                {result.category}
              </div>
              <div style={{ fontSize:12, color:S.text3 }}>US AQI</div>
            </div>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:4 }}>
            <div style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'7px 8px' }}>
              <div style={{ fontSize:12, color:S.text3, marginBottom:3 }}>PM2.5</div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.text }}>{result.pm2_5 ?? 'N/A'} μg/m³</div>
            </div>
            <div style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'7px 8px' }}>
              <div style={{ fontSize:12, color:S.text3, marginBottom:3 }}>PM10</div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.text }}>{result.pm10 ?? 'N/A'} μg/m³</div>
            </div>
            <div style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'7px 8px' }}>
              <div style={{ fontSize:12, color:S.text3, marginBottom:3 }}>NO₂</div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.text }}>{result.nitrogen_dioxide ?? 'N/A'} μg/m³</div>
            </div>
            <div style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'7px 8px' }}>
              <div style={{ fontSize:12, color:S.text3, marginBottom:3 }}>O₃</div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.text }}>{result.ozone ?? 'N/A'} μg/m³</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PlaceSearchBar({ mapRef, drawGroupRef, aoiBoundsRef, onAreaDrawn, isMobile }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fallbackWarning, setFallbackWarning] = useState(null);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.trim().length < 3) { setResults([]); return; }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await fetch(
          `https://nominatim.openstreetmap.org/search?` + new URLSearchParams({
            q: query, format: 'geojson', polygon_geojson: '1', limit: '6', addressdetails: '1',
          }),
          { headers: { 'Accept-Language': 'en' } },
        );
        const data = await resp.json();
        // Prefer actual boundary polygons over bare point matches — Nominatim
        // often returns a place's point/label node ranked above its real
        // administrative boundary relation, and silently using the point
        // (via the small-box fallback below) produces a tiny, misleading
        // AOI with no indication anything's off. Surface real boundaries
        // first so picking the top result is usually the right one.
        const features = (data.features || []).slice().sort((a, b) => {
          const aPoint = a.geometry?.type === 'Point' ? 1 : 0;
          const bPoint = b.geometry?.type === 'Point' ? 1 : 0;
          return aPoint - bPoint;
        });
        setResults(features);
        setOpen(true);
      } catch (e) {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 400);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query]);

  const selectPlace = (feature) => {
    const map = mapRef.current;
    const dg = drawGroupRef.current;
    if (!map || !dg || !feature.geometry) return;

    dg.clearLayers();
    let geometry = feature.geometry;
    const isPointFallback = geometry.type === 'Point';

    // Nominatim sometimes only has a Point for smaller places — turn that
    // into a small usable box rather than leaving no AOI at all, so the
    // search bar still works for places without a mapped boundary. This is
    // NOT the real place boundary though — it's an arbitrary ~11km box
    // around a label point, so anything computed over it (e.g. built-up
    // area) reflects that small box, not the actual city/district. Warn
    // visibly rather than silently substituting it.
    if (isPointFallback) {
      const [lon, lat] = geometry.coordinates;
      const d = 0.05; // ~5km box
      geometry = {
        type: 'Polygon',
        coordinates: [[[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]]],
      };
    }

    const layer = L.geoJSON(geometry, { style: { color: '#2a6abd', weight: 1.5, fillOpacity: 0.06 } });
    layer.eachLayer(l => dg.addLayer(l));

    try {
      const bounds = dg.getBounds();
      aoiBoundsRef.current = bounds;
      map.fitBounds(bounds, { padding: [40, 40] });
    } catch (e) {}

    onAreaDrawn(geometry, feature.properties?.display_name
      ? feature.properties.display_name.split(',').slice(0, 2).join(',').trim()
      : null);
    setOpen(false);
    setQuery(feature.properties?.display_name?.split(',')[0] || query);
    setFallbackWarning(isPointFallback
      ? 'No mapped boundary for this place — using an approximate ~11km box around its center point, not the actual city/district boundary. Search a more specific result, or draw the AOI manually for an accurate area.'
      : null);
  };

  return (
    <div style={{
      position: 'absolute', top: isMobile ? 10 : 14, left: '50%', transform: 'translateX(-50%)',
      zIndex: 1000, width: isMobile ? '88%' : 380, maxWidth: '92vw',
    }}>
      <div style={{ position: 'relative' }}>
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); setFallbackWarning(null); }}
          onFocus={() => { if (results.length) setOpen(true); }}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Search a place..."
          style={{
            width: '100%', boxSizing: 'border-box', padding: '9px 12px',
            background: 'rgba(18,21,26,0.95)', border: '1px solid #262b33', borderRadius: 4,
            color: '#e4e7eb', fontFamily: "'JetBrains Mono', 'Courier New', monospace",
            fontSize: 13, letterSpacing: 0.4, outline: 'none',
            boxShadow: '0 2px 10px rgba(0,0,0,0.4)',
          }}
        />
        {loading && (
          <div style={{ position: 'absolute', right: 10, top: 9, fontSize: 11, color: '#5c6673', fontFamily: 'monospace' }}>...</div>
        )}
        {open && results.length > 0 && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, marginTop: 4,
            background: 'rgba(18,21,26,0.98)', border: '1px solid #262b33', borderRadius: 4,
            boxShadow: '0 4px 14px rgba(0,0,0,0.5)', maxHeight: 240, overflowY: 'auto',
          }}>
            {results.map((f, i) => {
              const isPoint = f.geometry?.type === 'Point';
              return (
                <button key={i}
                  onMouseDown={e => { e.preventDefault(); selectPlace(f); }}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left', padding: '8px 12px',
                    background: 'transparent', border: 'none', borderBottom: i < results.length - 1 ? '1px solid #1c2027' : 'none',
                    color: '#a8b0bb', fontSize: 12.5, fontFamily: "'JetBrains Mono', monospace",
                    cursor: 'pointer', lineHeight: 1.4,
                  }}>
                  <span style={{ color: isPoint ? '#c9933a' : '#4a7c59', fontSize: 10, marginRight: 6 }}>
                    {isPoint ? '[NO BOUNDARY]' : '[BOUNDARY]'}
                  </span>
                  {f.properties?.display_name}
                </button>
              );
            })}
          </div>
        )}
        {fallbackWarning && (
          <div style={{
            marginTop: 6, padding: '7px 10px', fontSize: 11.5, lineHeight: 1.5,
            background: 'rgba(201,147,58,0.1)', border: '1px solid rgba(201,147,58,0.4)',
            borderRadius: 4, color: '#c9933a', fontFamily: "'JetBrains Mono', monospace",
          }}>
            ⚠ {fallbackWarning}
          </div>
        )}
      </div>
    </div>
  );
}


// ── Progress ──────────────────────────────────────────────────────────────────
function ProgressBar({ pct, label }) {
  return (
    <div style={{ marginTop:8 }}>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
        <span style={{ fontSize:15, color:S.text3, fontFamily:S.mono, letterSpacing:1 }}>{label}</span>
        <span style={{ fontSize:15, color:S.accent, fontFamily:S.mono }}>{pct}%</span>
      </div>
      <div style={{ height:2, background:S.border, borderRadius:1, overflow:'hidden' }}>
        <div style={{ width:`${pct}%`, height:'100%', background:S.accent, transition:'width 0.4s ease' }} />
      </div>
    </div>
  );
}

// ── Metric selector ───────────────────────────────────────────────────────────
function MetricSelector({ selected, onChange }) {
  return (
    <div>
      <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, marginBottom:9, textTransform:'uppercase' }}>Analysis type</div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:7 }}>
        {Object.entries(METRICS_META).map(([id,m]) => {
          const active = id === selected;
          return (
            <button key={id} onClick={() => onChange(active ? null : id)} title={m.desc}
              style={{ display:'flex', alignItems:'center', gap:9, padding:'10px 10px', minHeight:52,
                fontFamily:S.mono, letterSpacing:0.3,
                background: active ? 'rgba(126,184,212,0.10)' : S.surface2,
                border: `1px solid ${active ? S.accent : S.border}`,
                borderRadius:3,
                color: active ? S.accent : S.text2, cursor:'pointer',
                textAlign:'left', transition:'border-color 0.15s, background 0.15s' }}>
              <Icon name={METRIC_ICON_FOR[id]} size={18} style={{ flexShrink:0, opacity: active ? 1 : 0.75 }} />
              <span style={{ fontSize:13, lineHeight:1.3 }}>{m.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Research agent result — a suggested place + reasoning + sources,
// genuinely different shape from a measured-metric result, so it gets
// its own small panel rather than being squeezed into ResultsPanel's
// metrics-table layout. ─────────────────────────────────────────────────────
function ResearchResultPanel({ result }) {
  const confColor = { high:'#7ec88f', medium:'#e8c15c', low:'#e8746b' }[result.confidence] || S.text3;
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
      <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, textTransform:'uppercase' }}>
        Research Agent
      </div>
      {result.place_name ? (
        <>
          <div style={{ fontSize:18, fontWeight:700, color:'#c9a86a' }}>{result.place_name}</div>
          <div style={{ fontSize:13, color:S.text2, lineHeight:1.6 }}>{result.reasoning}</div>
          <div style={{ display:'flex', gap:14, fontSize:12, fontFamily:S.mono, color:S.text3 }}>
            <span>Confidence: <span style={{ color:confColor }}>{result.confidence || 'low'}</span></span>
            {result.radius_km != null && <span>Marked radius: {result.radius_km} km</span>}
          </div>
        </>
      ) : (
        <div style={{ fontSize:13, color:S.text2, lineHeight:1.6 }}>
          {result.reasoning || "Couldn't find a specific answer grounded in web search results for this question."}
        </div>
      )}
      {result.source_urls && result.source_urls.length > 0 && (
        <div>
          <div style={{ fontSize:11, color:S.text3, fontFamily:S.mono, letterSpacing:1, textTransform:'uppercase', marginBottom:6 }}>Sources</div>
          <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
            {result.source_urls.map((u, i) => (
              <a key={i} href={u} target="_blank" rel="noreferrer"
                style={{ fontSize:11, color:S.accent, wordBreak:'break-all', textDecoration:'none' }}>
                {u}
              </a>
            ))}
          </div>
        </div>
      )}
      <div style={{ fontSize:11, color:S.text3, lineHeight:1.5, borderTop:`1px solid ${S.border}`, paddingTop:10 }}>
        Web search via a public search index — a suggestion grounded in current search results, not a verified survey. Confirm on the ground before acting on it.
      </div>
    </div>
  );
}

// ── Results ───────────────────────────────────────────────────────────────────
function ResultsPanel({ result, drawnAOI, apiUrl }) {
  const m = METRICS_META[result.metric] || { label:result.metric, color:S.accent };
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);

  const downloadReport = async () => {
    if (!drawnAOI) { setReportError('AOI unavailable — re-run the analysis first.'); return; }
    setReportLoading(true); setReportError(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/report/analysis`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          analysis_type: result.metric, aoi_geojson: drawnAOI,
          start_date: result.start_date, end_date: result.end_date,
          metrics: result.metrics,
        }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Report generation failed');
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `vayu_${result.metric}_report.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setReportError(e.message);
    } finally {
      setReportLoading(false);
    }
  };

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
      <div style={{ borderBottom:`1px solid ${S.border}`, paddingBottom:8 }}>
        <div style={{ fontSize:15, fontFamily:S.mono, letterSpacing:2, color:m.color, textTransform:'uppercase', marginBottom:3 }}>{m.label}</div>
        <div style={{ fontSize:14, color:S.text3, fontFamily:S.mono }}>{result.start_date} -- {result.end_date}{result.region&&` · ${result.region}`}</div>
      </div>
      <div style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'8px 10px' }}>
        <div style={{ fontSize:14, color:S.text3, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase', marginBottom:5 }}>Finding</div>
        <p style={{ fontSize:16, color:S.text, lineHeight:1.6, fontFamily:S.mono }}>{result.summary}</p>
      </div>
      {result.insight && (
        <div style={{ background:'rgba(126,184,212,0.04)', border:'1px solid rgba(126,184,212,0.15)', padding:'8px 10px' }}>
          <div style={{ fontSize:14, color:S.accent, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase', marginBottom:5 }}>AI Analysis</div>
          <p style={{ fontSize:15, color:S.text2, lineHeight:1.6 }}>{result.insight}</p>
        </div>
      )}
      <div>
        <div style={{ fontSize:14, color:S.text3, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase', marginBottom:6 }}>Key Metrics</div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:4 }}>
          {Object.entries(result.metrics||{}).map(([k,v]) => (
            <div key={k} style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'7px 8px' }}>
              <div style={{ fontSize:14, color:S.text3, marginBottom:3 }}>{fmtKey(k)}</div>
              <div style={{ fontSize:17, fontFamily:S.mono, color:m.color, fontWeight:600 }}>{fmtVal(k,v)}</div>
            </div>
          ))}
        </div>
      </div>
      {result.geojson_url && (
        <a href={result.geojson_url.startsWith('http') ? result.geojson_url : `${API_URL}${result.geojson_url}`}
          target="_blank" rel="noreferrer"
          style={{ display:'block', textAlign:'center', fontSize:15, padding:'7px', fontFamily:S.mono,
            background:S.surface2, border:`1px solid ${S.border}`, color:S.text2,
            letterSpacing:1, textDecoration:'none', textTransform:'uppercase' }}>
          Download GeoJSON
        </a>
      )}
      <button onClick={downloadReport} disabled={reportLoading}
        style={{ display:'block', width:'100%', textAlign:'center', fontSize:15, padding:'7px', fontFamily:S.mono,
          background: reportLoading ? S.surface2 : 'rgba(126,184,212,0.1)',
          border:`1px solid ${reportLoading ? S.border : S.accent}`, color: reportLoading ? S.text3 : S.accent,
          letterSpacing:1, textTransform:'uppercase', cursor: reportLoading ? 'not-allowed' : 'pointer' }}>
        {reportLoading ? 'Generating Report...' : 'Download Report (PDF)'}
      </button>
      {reportError && (
        <div style={{ fontSize:13, color:S.text2, background:'rgba(139,32,32,0.08)', border:'1px solid rgba(139,32,32,0.3)', padding:'6px 9px' }}>
          {reportError}
        </div>
      )}
    </div>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar({ tab,setTab, queryText,setQueryText, selMetric,setSelMetric, drawnAOI, aoiRegionName,
  isLoading,error,result,jobStatus, onSubmit, vesselStats, onClose, isMobile,
  weatherLayers, onToggleWeather, apiUrl, mapRef, satelliteLayers, onToggleSatelliteLayer, satelliteLoadingKey, mapZoom,
  aqiOn, aqiLoading, onToggleAqi,
  orbitalShowSatellites, setOrbitalShowSatellites, orbitalShowAircraft, setOrbitalShowAircraft,
  orbitalSatellites, orbitalSatLoaded, orbitalSatDebug, orbitalAircraftStats, orbitalAircraftValid,
  orbitalSearch, setOrbitalSearch, orbitalFilteredList, orbitalSelected, setOrbitalSelected }) {
  const [eIdx, setEIdx] = useState(0);
  const cycleExample = () => { const n=(eIdx+1)%EXAMPLES.length; setEIdx(n); setQueryText(EXAMPLES[n]); };
  const TABS = [
    { id:'Analyze',  icon:'target' },
    { id:'Maritime', icon:'anchor' },
    { id:'Weather',  icon:'thermo' },
    { id:'Agri',     icon:'leaf' },
    { id:'Orbital',  icon:'satellite-dish' },
    { id:'Guide',    icon:'book' },
  ];
  return (
    <div style={{ background:S.surface, borderRight:`1px solid ${S.border}`, display:'flex', flexDirection:'column', height:'100%', width:'100%' }}>
      <div style={{ padding:'12px 14px', borderBottom:`1px solid ${S.border}`, flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div style={{ display:'flex', alignItems:'center', gap:9 }}>
            <img src="/logo.png" alt="" width="26" height="26" style={{ flexShrink:0, filter:'drop-shadow(0 0 3px rgba(201,147,58,0.35))' }} />
            <div>
              <div style={{ fontFamily:S.mono, fontSize:18, fontWeight:700, letterSpacing:3, color:S.text }}>VAYU</div>
              <div style={{ fontFamily:S.mono, fontSize:12, color:S.text3, letterSpacing:1.5 }}>GEOSPATIAL INTELLIGENCE</div>
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8 }}>
            <div style={{ fontSize:12, fontFamily:S.mono, color:'#4a7c59', border:'1px solid #4a7c59', padding:'2px 7px', letterSpacing:1 }}>v2.0</div>
            {isMobile && (
              <button onClick={onClose} aria-label="Close panel"
                style={{ display:'flex', padding:6, background:S.surface2, border:`1px solid ${S.border}`, borderRadius:4, color:S.text2, cursor:'pointer' }}>
                <Icon name="close" size={16} />
              </button>
            )}
          </div>
        </div>
      </div>
      <div style={{ display:'flex', borderBottom:`1px solid ${S.border}`, flexShrink:0, padding:'6px 6px 0' }}>
        {TABS.map(({ id:t, icon }) => (
          <button key={t} onClick={() => setTab(t)}
            style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', gap:3, padding:'7px 2px 8px',
              fontSize:11, fontFamily:S.mono, letterSpacing:0.8, textTransform:'uppercase',
              background: tab===t ? 'rgba(126,184,212,0.08)' : 'transparent',
              border:'none', borderRadius:'4px 4px 0 0',
              color:tab===t?S.accent:S.text3, cursor:'pointer' }}>
            <Icon name={icon} size={17} />
            {t}
          </button>
        ))}
      </div>
      <div style={{ flex:1, overflowY:'auto', padding:'14px', display:'flex', flexDirection:'column', gap:14 }}>
        {tab === 'Analyze' && (
          <>
            <MetricSelector selected={selMetric} onChange={setSelMetric} />
            <div>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:7 }}>
                <span style={{ fontSize:15, color:S.text3, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase' }}>Query</span>
                <button onClick={cycleExample} style={{ fontSize:14, color:S.text3, fontFamily:S.mono, background:'transparent', border:'none', cursor:'pointer', letterSpacing:1 }}>EXAMPLE</button>
              </div>
              <textarea rows={3}
                style={{ width:'100%', background:S.bg, border:`1px solid ${S.border}`, color:S.text2,
                  fontFamily:S.mono, fontSize:15, padding:'9px', resize:'none', outline:'none',
                  letterSpacing:0.5, lineHeight:1.6, boxSizing:'border-box' }}
                placeholder="e.g. how much deforestation happened here since 2016..."
                value={queryText} onChange={e => setQueryText(e.target.value)} />
            </div>
            <div style={{ fontSize:15, fontFamily:S.mono, padding:'7px 10px', letterSpacing:1,
              background: drawnAOI ? 'rgba(74,124,89,0.08)' : S.surface2,
              border: `1px solid ${drawnAOI ? '#4a7c59' : S.border}`,
              color: drawnAOI ? '#4a7c59' : S.text3 }}>
              {drawnAOI ? 'AOI DEFINED' : 'NO AOI DRAWN (OPTIONAL)'}
            </div>
            <button onClick={onSubmit} disabled={isLoading||!queryText}
              style={{ padding:'10px', fontSize:15, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase',
                background: isLoading||!queryText ? S.surface2 : 'rgba(126,184,212,0.1)',
                border: `1px solid ${isLoading||!queryText ? S.border : S.accent}`,
                color: isLoading||!queryText ? S.text3 : S.accent,
                cursor: isLoading||!queryText ? 'not-allowed' : 'pointer' }}>
              {isLoading ? 'ANALYZING...' : 'RUN ANALYSIS'}
            </button>
            {isLoading && jobStatus && <ProgressBar pct={jobStatus.progress_pct||0} label={jobStatus.stage_label||jobStatus.stage||'Processing...'} />}
            {error && (
              <div style={{ background:'rgba(139,32,32,0.08)', border:'1px solid rgba(139,32,32,0.3)', padding:'9px 11px' }}>
                <div style={{ fontSize:14, fontFamily:S.mono, color:'#8b2020', letterSpacing:2, marginBottom:4 }}>ERROR</div>
                <div style={{ fontSize:15, color:S.text2 }}>{error}</div>
              </div>
            )}
            {result && (result.result_type === 'research'
              ? <ResearchResultPanel result={result} />
              : <ResultsPanel result={result} drawnAOI={drawnAOI} apiUrl={apiUrl} />)}
          </>
        )}
        {tab === 'Maritime' && (
          <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
            <div>
              <div style={{ fontSize:13, color:S.text3, fontFamily:S.mono, letterSpacing:1.5, marginBottom:9, textTransform:'uppercase' }}>
                Active vessels — {vesselStats?.active_vessels ?? 0}
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:7 }}>
                {Object.entries(VESSEL_COLORS).map(([cat, c]) => {
                  const count = vesselStats?.by_category?.[cat] || 0;
                  const iconKey = VESSEL_ICON_FOR_CATEGORY[cat] || 'SHIP_CARGO';
                  return (
                    <div key={cat} style={{ display:'flex', alignItems:'center', gap:9, background:S.surface2, border:`1px solid ${S.border}`, borderRadius:3, padding:'9px 10px' }}>
                      <span style={{ flexShrink:0, filter:`drop-shadow(0 0 2px ${c.fill}88)` }}
                        dangerouslySetInnerHTML={{ __html: ICONS[iconKey](c.fill, c.border) }} />
                      <div style={{ minWidth:0 }}>
                        <div style={{ fontSize:12, color:S.text3, letterSpacing:0.3, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{c.label}</div>
                        <div style={{ fontSize:17, fontFamily:S.mono, color:'#ffffff', fontWeight:700 }}>{count}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:12 }}>
              <div style={{ fontSize:13, fontFamily:S.mono, color:S.accent, letterSpacing:1.5, marginBottom:8, textTransform:'uppercase' }}>
                Map legend
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6 }}>
                <svg width="28" height="10"><line x1="0" y1="5" x2="28" y2="5" stroke={S.accent} strokeWidth="2" strokeDasharray="4,4"/></svg>
                <span style={{ fontSize:13, color:S.text2 }}>Track sailed (recent positions)</span>
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                <svg width="28" height="10"><line x1="0" y1="5" x2="28" y2="5" stroke={S.accent} strokeWidth="1.5" strokeDasharray="1,4"/></svg>
                <span style={{ fontSize:13, color:S.text2 }}>Forecast track (course + speed projection)</span>
              </div>
            </div>

            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:12 }}>
              <div style={{ fontSize:13, fontFamily:S.mono, color:S.accent, letterSpacing:1.5, marginBottom:8, textTransform:'uppercase' }}>
                Monitored chokepoints
              </div>
              <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                {['Strait of Hormuz','Strait of Malacca','Bab-el-Mandeb','Suez Canal','Strait of Gibraltar','Panama Canal','English Channel'].map(name => (
                  <div key={name} style={{ display:'flex', alignItems:'center', gap:7, fontSize:13, color:S.text3, fontFamily:S.mono }}>
                    <Icon name="anchor" size={13} style={{ flexShrink:0, opacity:0.6 }} />{name}
                  </div>
                ))}
              </div>
            </div>

            {(!vesselStats || vesselStats.active_vessels === 0) && (
              <div style={{ background:'rgba(201,147,58,0.06)', border:'1px solid rgba(201,147,58,0.25)', padding:'10px 12px', fontSize:13, color:S.text2, lineHeight:1.6 }}>
                No live vessel data. Maritime tracking requires an AISSTREAM_API_KEY
                (free at aisstream.io) configured on the backend.
              </div>
            )}
          </div>
        )}
        {tab === 'Weather' && (
          <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
            <WeatherLayerToggles active={weatherLayers} onToggle={onToggleWeather} />
            <div style={{ marginTop:14 }}>
              <button onClick={onToggleAqi} disabled={aqiLoading}
                style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 12px', minHeight:44, width:'100%',
                  fontFamily:S.mono, letterSpacing:0.3,
                  background: aqiOn ? 'rgba(126,184,212,0.10)' : S.surface2,
                  border: `1px solid ${aqiOn ? S.accent : S.border}`, borderRadius:3,
                  color: aqiOn ? S.accent : S.text2, cursor: aqiLoading ? 'wait' : 'pointer',
                  textAlign:'left', opacity: aqiLoading ? 0.6 : 1 }}>
                <Icon name="thermo" size={18} style={{ flexShrink:0, opacity: aqiOn ? 1 : 0.75 }} />
                <span style={{ fontSize:14, flex:1 }}>Air Quality (CPCB, India)</span>
                <span style={{ fontSize:11, letterSpacing:1, opacity:0.7 }}>{aqiLoading ? '…' : (aqiOn ? 'ON' : 'OFF')}</span>
              </button>
            </div>
            <div style={{ fontSize:13, color:S.text3, lineHeight:1.6 }}>
            </div>
            {!OWM_API_KEY && (
              <div style={{ background:'rgba(201,147,58,0.06)', border:'1px solid rgba(201,147,58,0.25)', padding:'10px 12px', fontSize:13, color:S.text2, lineHeight:1.6 }}>
                Temperature and Air Pressure need a free OpenWeatherMap key (openweathermap.org, no card required) set as VITE_OWM_API_KEY. Wind Speed doesn't need it — it's animated live from our own backend.
              </div>
            )}
            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:14 }}>
              <SatelliteLayerToggles active={satelliteLayers} onToggle={onToggleSatelliteLayer} loadingKey={satelliteLoadingKey} currentZoom={mapZoom} />
              <div style={{ fontSize:13, color:S.text3, lineHeight:1.6, marginTop:9 }}>
              </div>
            </div>
            <div style={{ fontSize:12, color:S.text3, lineHeight:1.6, padding:'8px 2px' }}>
              Aircraft and satellite tracking moved to the Orbital tab.
            </div>
            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:14 }}>
              <AirQualityCheck mapRef={mapRef} apiUrl={apiUrl} />
            </div>
          </div>
        )}
        {tab === 'Agri' && <AgriPanel drawnAOI={drawnAOI} apiUrl={apiUrl} searchedRegionName={aoiRegionName} />}
        {tab === 'Orbital' && (
          <OrbitalSidebarPanel
            showSatellites={orbitalShowSatellites} onToggleSatellites={() => setOrbitalShowSatellites(v => !v)}
            showAircraft={orbitalShowAircraft} onToggleAircraft={() => setOrbitalShowAircraft(v => !v)}
            satelliteCount={orbitalSatellites.length} aircraftCount={orbitalAircraftStats.active_aircraft || orbitalAircraftValid.length}
            satLoaded={orbitalSatLoaded} satDebug={orbitalSatDebug}
            search={orbitalSearch} onSearchChange={setOrbitalSearch}
            filteredList={orbitalFilteredList}
            selected={orbitalSelected} onSelect={setOrbitalSelected}
          />
        )}        {tab === 'Guide' && (
          <div style={{ display:'flex', flexDirection:'column', gap:14, fontSize:15, color:S.text2 }}>
            <div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.accent, letterSpacing:2, marginBottom:10, textTransform:'uppercase' }}>How to use</div>
              {[['1','Select an analysis type (optional — Vayu can infer it from your query)'],['2','Draw a polygon on the map, or just name a place in your query — the 9 fixed metrics will auto-locate it'],['3','Enter a natural language query'],['4','Click Run Analysis']].map(([n,t]) => (
                <div key={n} style={{ display:'flex', gap:10, marginBottom:8, alignItems:'flex-start' }}>
                  <span style={{ fontSize:14, fontFamily:S.mono, color:S.accent, border:`1px solid ${S.border}`, padding:'2px 6px', flexShrink:0 }}>{n}</span>
                  <span style={{ fontSize:15, color:S.text2 }}>{t}</span>
                </div>
              ))}
            </div>
            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:12 }}>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.accent, letterSpacing:2, marginBottom:10, textTransform:'uppercase' }}>Live Intel Sources</div>
              {[
                ['USGS','Earthquakes M3.5+, global'],
                ['NASA FIRMS','Active fire hotspots'],
                ['GDELT','Geolocated news events'],
                ['ACLED','Armed conflict events'],
              ].map(([src,desc]) => (
                <div key={src} style={{ marginBottom:8 }}>
                  <div style={{ fontSize:15, fontFamily:S.mono, color:INTEL_COLORS[src]?.border||S.accent, marginBottom:2 }}>{src}</div>
                  <div style={{ fontSize:14, color:S.text3 }}>{desc}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <div style={{ flexShrink:0, padding:'7px 14px', borderTop:`1px solid ${S.border}` }}>
        <div style={{ fontSize:14, fontFamily:S.mono, color:S.text3, letterSpacing:1 }}>
          VAYU v2.0.0
        </div>
      </div>
    </div>
  );
}

// ── Map overlay ───────────────────────────────────────────────────────────────
function MapOverlay({ result, isLoading, drawnAOI, isMobile }) {
  if (!isLoading && !result && !drawnAOI) return (
    <div style={{ position:'absolute', bottom: isMobile ? 72 : 16, left:'50%', transform:'translateX(-50%)', zIndex:1000, pointerEvents:'none', width: isMobile ? '90%' : 'auto', textAlign:'center' }}>
      <div style={{ padding:'7px 14px', fontSize: isMobile ? 12 : 15, fontFamily:"'Courier New',monospace", letterSpacing:0.5, background:'rgba(13,17,23,0.92)', border:'1px solid #3a4250', color:'#ffffff' }}>
        {isMobile ? 'TAP THE DRAW TOOL (TOP-RIGHT) TO DEFINE AN AREA' : 'USE DRAW TOOLS (TOP-RIGHT) TO DEFINE AREA OF INTEREST'}
      </div>
    </div>
  );
  // The "analyzing" state used to show a top-center banner here, at the
  // exact same position (top:12, centered) as PlaceSearchBar — on mobile,
  // where the search bar spans ~88% of the width, this banner sat right
  // on top of it, blocking the input while a query ran. Progress is
  // already shown via ProgressBar in the sidebar, so this was a fully
  // redundant, position-colliding duplicate — removed rather than
  // repositioned.
  if (isLoading) return null;
  if (result) {
    if (result.result_type === 'research') {
      return (
        <div style={{ position:'absolute', top: isMobile ? 56 : 12, right:12, left: isMobile ? 12 : 'auto', zIndex:900, pointerEvents:'none', textAlign: isMobile ? 'center' : 'right' }}>
          <div style={{ padding:'6px 12px', fontSize: isMobile ? 11 : 15, fontFamily:"'Courier New',monospace", letterSpacing:1, background:'rgba(13,17,23,0.92)', border:'1px solid #c9a86a', color:'#c9a86a', display:'inline-block' }}>
            RESEARCH AGENT{result.place_name ? ` · ${result.place_name.toUpperCase()}` : ''}
          </div>
        </div>
      );
    }
    const m = METRICS_META[result.metric]||{ label:result.metric };
    return (
      <div style={{ position:'absolute', top: isMobile ? 56 : 12, right:12, left: isMobile ? 12 : 'auto', zIndex:900, pointerEvents:'none', textAlign: isMobile ? 'center' : 'right' }}>
        <div style={{ padding:'6px 12px', fontSize: isMobile ? 11 : 15, fontFamily:"'Courier New',monospace", letterSpacing:1, background:'rgba(13,17,23,0.92)', border:'1px solid #3a4250', color:'#ffffff', display:'inline-block' }}>
          {(m.label || '').toUpperCase()} · {result.start_date?.slice(0,4)}--{result.end_date?.slice(0,4)}
        </div>
      </div>
    );
  }
  return null;
}

// ── Root App ──────────────────────────────────────────────────────────────────
export default function App() {
  const isMobile = useIsMobile();
  const [mobilePanel, setMobilePanel] = useState('map'); // 'map' | 'analyze' | 'intel'
  const [tab, setTab]             = useState('Analyze');
  const [queryText, setQueryText] = useState('');
  const [selMetric, setSelMetric] = useState(null);
  const [drawnAOI, setDrawnAOI]   = useState(null);
  const [selectedIntelEvent, setSelectedIntelEvent] = useState(null); // opens the intel-detail split panel
  const [aoiRegionName, setAoiRegionName] = useState(null); // set only via place search; cleared on manual draw/edit/delete

  const handleManualAreaDrawn = useCallback((geom) => {
    setDrawnAOI(geom);
    setAoiRegionName(null); // manual draw/edit/delete — no searched name to attach
  }, []);
  const handlePlaceSelected = useCallback((geom, name) => {
    setDrawnAOI(geom);
    setAoiRegionName(name || null);
  }, []);
  const [result, setResult]       = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError]         = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [, setHistory]     = useState([]);   // history tab removed from UI for now — still tracked in case it comes back
  const [weatherLayers, setWeatherLayers] = useState({ temp:false, wind:false, pressure:false });
  const [satelliteLayers, setSatelliteLayers] = useState({ true_color:false, ndvi:false, sar:false, thermal:false, worldview:false });
  const [satelliteLoadingKey, setSatelliteLoadingKey] = useState(null);
  const [mapZoom, setMapZoom] = useState(null);
  const [aqiOn, setAqiOn] = useState(false);
  const [aqiLoading, setAqiLoading] = useState(false);

  const mapRef          = useRef(null);
  const drawGroupRef    = useRef(null);
  const layersRef       = useRef([]);
  const weatherTileRefs = useRef({});     // 'temp'|'wind'|'pressure' -> L.tileLayer instance
  const satelliteTileRefs = useRef({});   // 'true_color'|'ndvi'|'sar'|'thermal'|'worldview' -> L.tileLayer instance
  const aqiLayerRef     = useRef(null);   // LayerGroup for CPCB AQI station markers
  const pollRef         = useRef(null);
  const aoiBoundsRef    = useRef(null);
  const intelLayerRef   = useRef(null);   // LayerGroup for intel markers
  const intelMarkersRef = useRef({});     // id -> marker, for dedup
  const intelOrderRef   = useRef([]);     // ids in insertion order, oldest first — for eviction
  const vesselLayerRef  = useRef(null);   // LayerGroup for vessel markers
  const vesselMarkersRef = useRef({});    // mmsi -> marker
  const vesselTrailsRef = useRef({});     // mmsi -> { points:[[lat,lon],...], polyline }
  const vesselPredictedRef = useRef({});  // mmsi -> { polyline, tipMarker } — forecast dead-reckoning track

  // Live maritime vessel tracking (AIS via aisstream.io)
  const { vessels, stats: vesselStats } = useVesselTracker(API_URL, true);

  // Aircraft and satellites are NOT on the 2D map at all — they only live
  // in the Orbital tab (3D globe + left sidebar list/detail view), since a
  // flat dot on a 2D map was a poor representation of a plane's heading/
  // altitude or an object in orbit; the 3D view is a genuinely better fit
  // for both. Data-fetching lives here (not inside OrbitalGlobe) so the
  // sidebar's Orbital tab and the 3D view share one source of truth —
  // OrbitalGlobe.jsx is a pure renderer, the info card/list/toggles all
  // live in the actual left sidebar instead of floating over the canvas.
  const [orbitalShowSatellites, setOrbitalShowSatellites] = useState(true);
  const [orbitalShowAircraft, setOrbitalShowAircraft] = useState(false);
  const [orbitalSelected, setOrbitalSelected] = useState(null);
  const [orbitalSearch, setOrbitalSearch] = useState('');
  const { satellites: orbitalSatellites, loaded: orbitalSatLoaded, debug: orbitalSatDebug } = useSatelliteTracker(API_URL, orbitalShowSatellites);
  const { aircraft: orbitalAircraft, stats: orbitalAircraftStats } = useAircraftTracker(API_URL, orbitalShowAircraft);
  const orbitalStations = orbitalSatellites.filter(s => s.group === 'stations');
  const orbitalOtherSats = orbitalSatellites.filter(s => s.group !== 'stations');
  const orbitalAircraftValid = orbitalAircraft.filter(a => typeof a.lat === 'number' && typeof a.lon === 'number');

  const orbitalFilteredList = (() => {
    const items = [];
    if (orbitalShowSatellites) {
      orbitalStations.forEach(s => items.push({ kind: 'station', key: `sat:${s.name}`, label: s.name, sub: `${Math.round(s.alt_km).toLocaleString()} km`, data: s }));
      orbitalOtherSats.forEach(s => items.push({ kind: 'satellite', key: `sat:${s.name}`, label: s.name, sub: `${Math.round(s.alt_km).toLocaleString()} km`, data: s }));
    }
    if (orbitalShowAircraft) {
      orbitalAircraftValid.forEach(a => items.push({
        kind: 'aircraft', key: `ac:${a.icao24}`,
        label: a.callsign || a.icao24,
        sub: a.on_ground ? 'on ground' : (a.baro_altitude_m != null ? `${Math.round(a.baro_altitude_m).toLocaleString()} m` : '—'),
        data: a,
      }));
    }
    items.sort((a, b) => a.label.localeCompare(b.label));
    if (!orbitalSearch.trim()) return items;
    const q = orbitalSearch.trim().toLowerCase();
    return items.filter(it => it.label.toLowerCase().includes(q));
  })();

  // Keep the selected item's numbers live rather than freezing at the
  // moment it was clicked/picked.
  useEffect(() => {
    setOrbitalSelected(prev => {
      if (!prev) return prev;
      if (prev.kind === 'aircraft') {
        const fresh = orbitalAircraftValid.find(a => a.icao24 === prev.icao24);
        return fresh ? { kind: 'aircraft', ...fresh } : prev;
      }
      const fresh = orbitalSatellites.find(s => s.name === prev.name);
      return fresh ? { kind: prev.kind, ...fresh } : prev;
    });
  }, [orbitalSatellites, orbitalAircraftValid]);

  const clearLayers = useCallback(() => {
    layersRef.current.forEach(l => { if (mapRef.current?.hasLayer(l)) mapRef.current.removeLayer(l); });
    layersRef.current = [];
  }, []);

  // ── Add intel event as a map marker ────────────────────────────────────────
  const addIntelMarker = useCallback((event) => {
    if (!intelLayerRef.current) return;
    if (intelMarkersRef.current[event.id]) return; // already on map

    try {
      const marker = createIntelMarker(event);
      const c = INTEL_COLORS[event.source] || { border: '#6b7a8d' };
      marker.bindTooltip(
        `<div style="font-family:monospace;font-size:14px;background:#0d1117;border:1px solid ${c.border};padding:8px 10px;color:#ffffff;max-width:240px;line-height:1.6">
          <div style="color:${c.border};font-size:13px;letter-spacing:1px;margin-bottom:4px;font-weight:700">${event.tag}</div>
          <div>${event.title}</div>
          <div style="color:#ffffff;font-size:13px;margin-top:4px;opacity:0.6">${event.lat.toFixed(2)}N ${event.lon.toFixed(2)}E</div>
        </div>`,
        { permanent:false, direction:'top', opacity:1, className:'vayu-tooltip' }
      );
      intelLayerRef.current.addLayer(marker);
      intelMarkersRef.current[event.id] = marker;
      intelOrderRef.current.push(event.id);
      marker.on('click', () => setSelectedIntelEvent(event));

      // Bug fix: this callback fires for every event the WebSocket has ever
      // pushed for the life of the tab — with no bound, that's an unbounded
      // number of permanent Leaflet markers (earthquakes/fires/GDELT/ACLED
      // are a genuinely continuous live feed), even though useIntelFeed's
      // own event LIST already correctly caps at MAX_LOCAL_EVENTS (500) —
      // the map markers were never subject to that same cap. This is very
      // likely the main cause of the tab getting slower the longer it's
      // left open: strictly-growing DOM node count that never shrinks.
      // Mirror the same 500 cap here, evicting the oldest marker first.
      const MAX_INTEL_MARKERS = 500;
      while (intelOrderRef.current.length > MAX_INTEL_MARKERS) {
        const oldestId = intelOrderRef.current.shift();
        const oldMarker = intelMarkersRef.current[oldestId];
        if (oldMarker) {
          try { intelLayerRef.current.removeLayer(oldMarker); } catch (e) {}
          delete intelMarkersRef.current[oldestId];
        }
      }
    } catch(e) {}
  }, []);

  // ── Render/update vessel markers + route trails whenever snapshot changes ──
  const MAX_TRAIL_POINTS = 18;
  const TRAIL_MIN_DELTA = 0.01; // degrees — skip near-duplicate points
  // If the implied speed between two consecutive fixes exceeds this, the fix
  // is almost certainly bad data (MMSI reuse/spoofing, GPS glitch, a stale
  // record jumping to a fresh one) rather than a real ship — even a fast
  // naval vessel tops out well under this. Break the trail instead of
  // drawing a straight "teleport" line across land/ocean between them.
  const MAX_PLAUSIBLE_KN = 60;

  useEffect(() => {
    if (!vesselLayerRef.current) return;
    const seen = new Set();

    vessels.forEach(v => {
      if (typeof v.lat !== 'number' || typeof v.lon !== 'number') return;
      seen.add(v.mmsi);

      // ── Marker: update position, and rotation/icon only if it actually
      //    changed (rebuilding the icon unconditionally every tick — up to
      //    2000 vessels every 8s — forces Leaflet to tear down and rebuild
      //    that marker's DOM element from scratch even when nothing visibly
      //    changed; over a long session that's the single biggest source of
      //    the tab getting slower the longer it stays open) ────────────────
      const existing = vesselMarkersRef.current[v.mmsi];
      if (existing) {
        existing.setLatLng([v.lat, v.lon]);
        const iconKey = VESSEL_ICON_FOR_CATEGORY[v.category] || 'SHIP_CARGO';
        const cog = typeof v.cog === 'number' ? v.cog : 0;
        const rotation = iconKey === 'BARREL' ? 0 : cog;
        if (existing._vayuIconKey !== iconKey || existing._vayuRotation !== rotation) {
          try {
            existing.setIcon(createVesselMarker(v).options.icon);
            existing._vayuIconKey = iconKey;
            existing._vayuRotation = rotation;
          } catch (e) {}
        }
      } else {
        try {
          const marker = createVesselMarker(v);
          const c = VESSEL_COLORS[v.category] || VESSEL_COLORS.OTHER;
          marker.bindTooltip(
            `<div style="font-family:monospace;font-size:14px;background:#0d1117;border:1px solid ${c.border};padding:8px 10px;color:#ffffff;max-width:240px;line-height:1.6">
              <div style="color:${c.border};font-size:13px;letter-spacing:1px;margin-bottom:4px;font-weight:700">${c.label.toUpperCase()}</div>
              <div>${v.name || ('MMSI ' + v.mmsi)}</div>
              ${v.destination ? `<div style="opacity:0.7;margin-top:2px">→ ${v.destination}</div>` : ''}
              <div style="color:#ffffff;font-size:13px;margin-top:4px;opacity:0.6">${v.sog?.toFixed(1) || 0} kn · ${v.lat.toFixed(2)}N ${v.lon.toFixed(2)}E</div>
              <div style="color:${c.border};font-size:11px;margin-top:5px;opacity:0.75">— track sailed &nbsp; ⋯ forecast (dead reckoning)</div>
            </div>`,
            { permanent:false, direction:'top', opacity:1 }
          );
          vesselLayerRef.current.addLayer(marker);
          const iconKeyInit = VESSEL_ICON_FOR_CATEGORY[v.category] || 'SHIP_CARGO';
          marker._vayuIconKey = iconKeyInit;
          marker._vayuRotation = iconKeyInit === 'BARREL' ? 0 : (typeof v.cog === 'number' ? v.cog : 0);
          vesselMarkersRef.current[v.mmsi] = marker;
        } catch(e) {}
      }

      // ── Route trail: append point (or break on an implausible jump) ───────
      try {
        let trail = vesselTrailsRef.current[v.mmsi];
        if (!trail) {
          trail = { points: [], times: [], polyline: null };
          vesselTrailsRef.current[v.mmsi] = trail;
        }
        const last = trail.points[trail.points.length - 1];
        const lastT = trail.times[trail.times.length - 1];
        const moved = !last || Math.abs(last[0]-v.lat) > TRAIL_MIN_DELTA || Math.abs(last[1]-v.lon) > TRAIL_MIN_DELTA;
        if (moved) {
          const nowT = v.last_update ? Date.parse(v.last_update) : Date.now();
          let implausible = false;
          if (last && lastT && Number.isFinite(nowT) && nowT > lastT) {
            const distNm = haversineNm(last[0], last[1], v.lat, v.lon);
            const hours = (nowT - lastT) / 3600000;
            if (distNm / hours > MAX_PLAUSIBLE_KN) implausible = true;
          }
          if (implausible) {
            // Discontinuity, not a real transit — start a fresh segment
            // rather than connecting across the impossible gap.
            trail.points = [[v.lat, v.lon]];
            trail.times = [nowT];
          } else {
            trail.points.push([v.lat, v.lon]);
            trail.times.push(nowT);
            if (trail.points.length > MAX_TRAIL_POINTS) { trail.points.shift(); trail.times.shift(); }
          }
        }
        const c = VESSEL_COLORS[v.category] || VESSEL_COLORS.OTHER;
        if (trail.points.length >= 2) {
          if (trail.polyline) {
            trail.polyline.setLatLngs(trail.points);
          } else {
            trail.polyline = L.polyline(trail.points, {
              color: c.border,     // use border color (brighter than fill)
              weight: 2,           // thicker than before
              opacity: 0.75,       // more visible
              dashArray: '4,6',    // longer dashes, easier to see
              lineJoin: 'round',
            }).addTo(vesselLayerRef.current);
          }
        }
      } catch(e) {}

      // ── Predicted path: where the vessel is headed, based on course + speed ──
      try {
        const forecast = buildPredictedPath(v);
        const existingForecast = vesselPredictedRef.current[v.mmsi];
        if (forecast) {
          const c = VESSEL_COLORS[v.category] || VESSEL_COLORS.OTHER;
          if (existingForecast) {
            existingForecast.polyline.setLatLngs(forecast);
            existingForecast.tipMarker.setLatLng(forecast[forecast.length - 1]);
          } else {
            const polyline = L.polyline(forecast, {
              color: c.fill,
              weight: 1.6,
              opacity: 0.55,
              dashArray: '1,6',       // fine dotted — visually distinct from the solid-dashed past trail
              lineCap: 'round',
            }).addTo(vesselLayerRef.current);
            const bearingIcon = L.divIcon({
              className: '',
              html: `<div style="transform:rotate(${v.cog}deg); opacity:0.8;">${ICONS.FORECAST_TIP(c.fill, c.border)}</div>`,
              iconSize: [11, 11],
              iconAnchor: [5.5, 5.5],
            });
            const tipMarker = L.marker(forecast[forecast.length - 1], {
              icon: bearingIcon, interactive: false, zIndexOffset: 40,
            }).addTo(vesselLayerRef.current);
            vesselPredictedRef.current[v.mmsi] = { polyline, tipMarker };
          }
        } else if (existingForecast) {
          // Vessel stopped/slowed below threshold — drop its forecast track
          try { vesselLayerRef.current.removeLayer(existingForecast.polyline); } catch(e) {}
          try { vesselLayerRef.current.removeLayer(existingForecast.tipMarker); } catch(e) {}
          delete vesselPredictedRef.current[v.mmsi];
        }
      } catch(e) {}
    });

    // ── Remove markers + trails for vessels no longer in the snapshot ───────
    Object.keys(vesselMarkersRef.current).forEach(mmsi => {
      if (!seen.has(Number(mmsi))) {
        try { vesselLayerRef.current.removeLayer(vesselMarkersRef.current[mmsi]); } catch(e) {}
        delete vesselMarkersRef.current[mmsi];
        const trail = vesselTrailsRef.current[mmsi];
        if (trail?.polyline) {
          try { vesselLayerRef.current.removeLayer(trail.polyline); } catch(e) {}
        }
        delete vesselTrailsRef.current[mmsi];
        const forecast = vesselPredictedRef.current[mmsi];
        if (forecast) {
          try { vesselLayerRef.current.removeLayer(forecast.polyline); } catch(e) {}
          try { vesselLayerRef.current.removeLayer(forecast.tipMarker); } catch(e) {}
          delete vesselPredictedRef.current[mmsi];
        }
      }
    });
  }, [vessels]);

  // ── Handle click on feed item: fly map + highlight marker ──────────────────
  // ── Toggle a weather overlay on/off — each layer is independent ────────────
  const handleToggleAqi = useCallback(() => {
    if (!mapRef.current) return;

    // Turning off: synchronous, no fetch needed.
    if (aqiOn) {
      if (aqiLayerRef.current) { try { mapRef.current.removeLayer(aqiLayerRef.current); } catch(e) {} }
      aqiLayerRef.current = null;
      setAqiOn(false);
      return;
    }

    setAqiLoading(true);
    fetch(`${API_URL}/api/v1/intel/air-quality/cpcb-stations`)
      .then(r => { if (!r.ok) throw new Error(`air-quality ${r.status}`); return r.json(); })
      .then(data => {
        setAqiLoading(false);
        if (!mapRef.current) return;
        const stations = data.stations || [];
        const group = L.layerGroup();
        stations.forEach(st => {
          if (typeof st.lat !== 'number' || typeof st.lon !== 'number') return;
          const color = st.category?.color || '#9a9fa8';
          const marker = L.circleMarker([st.lat, st.lon], {
            radius: 7, color: '#0d0f12', weight: 1.5, fillColor: color, fillOpacity: 0.9,
          });
          const pollutantRows = Object.entries(st.pollutants || {})
            .map(([k, v]) => `<div style="display:flex;justify-content:space-between;gap:10px;"><span style="opacity:0.6">${escapeHtml(k)}</span><span>${escapeHtml(v)}</span></div>`)
            .join('');
          marker.bindPopup(
            `<div style="font-family:'JetBrains Mono',monospace;font-size:12px;min-width:180px;">` +
            `<div style="font-weight:700;color:${color};margin-bottom:4px;">${escapeHtml(st.station_name || 'Station')}</div>` +
            `<div style="opacity:0.7;margin-bottom:6px;">${escapeHtml(st.city || '')}${st.city && st.state ? ', ' : ''}${escapeHtml(st.state || '')}</div>` +
            `<div style="display:flex;justify-content:space-between;font-weight:700;margin-bottom:4px;"><span>AQI</span><span style="color:${color}">${escapeHtml(st.aqi)} \u00b7 ${escapeHtml(st.category?.label || '')}</span></div>` +
            pollutantRows +
            `</div>`
          );
          group.addLayer(marker);
        });
        group.addTo(mapRef.current);
        aqiLayerRef.current = group;
        setAqiOn(true);
      })
      .catch(() => { setAqiLoading(false); });
  }, [aqiOn]);

  const handleToggleWeather = useCallback((key) => {
    if (!mapRef.current) return;
    const meta = WEATHER_LAYERS[key];

    setWeatherLayers(prev => {
      const turningOn = !prev[key];

      if (!turningOn) {
        const layer = weatherTileRefs.current[key];
        if (layer) { try { mapRef.current.removeLayer(layer); } catch(e) {} }
        delete weatherTileRefs.current[key];
        return { ...prev, [key]: false };
      }

      if (meta.type === 'tile') {
        if (!OWM_API_KEY) return prev; // can't turn on without a key — no-op
        const tl = L.tileLayer(
          `https://tile.openweathermap.org/map/${meta.owmLayer}/{z}/{x}/{y}.png?appid=${OWM_API_KEY}`,
          { opacity: meta.opacity, noWrap:true, bounds:[[-85,-180],[85,180]], zIndex: 5 }
        ).addTo(mapRef.current);
        weatherTileRefs.current[key] = tl;
        return { ...prev, [key]: true };
      }

      if (meta.type === 'velocity') {
        // Animated layer needs real vector data first — fetch, then add once
        // it arrives. Optimistically flip the toggle on now; if the fetch
        // fails, flip it back off rather than leaving a dead "ON" state.
        fetch(`${API_URL}/api/v1/intel/wind-field`)
          .then(r => { if (!r.ok) throw new Error(`wind-field ${r.status}`); return r.json(); })
          .then(data => {
            if (!mapRef.current) return;
            const legend = WEATHER_LEGENDS.wind;
            const vl = L.velocityLayer({
              displayValues: false,
              data,
              velocityScale: 0.01,
              particleAge: 90,
              lineWidth: 1.5,
              particleMultiplier: 1 / 300,
              frameRate: 20,
              minVelocity: legend.stops[0].v,
              maxVelocity: legend.stops[legend.stops.length - 1].v,
              colorScale: legend.stops.map(s => s.c),
              opacity: meta.opacity,
            });
            vl.addTo(mapRef.current);
            weatherTileRefs.current[key] = vl;
          })
          .catch(err => {
            console.error('wind field load failed:', err);
            setWeatherLayers(p => ({ ...p, [key]: false }));
          });
        return { ...prev, [key]: true };
      }

      return prev;
    });
  }, []);

  const handleToggleSatelliteLayer = useCallback((key) => {
    if (!mapRef.current) return;

    // Turning off: synchronous, no fetch needed.
    if (satelliteLayers[key]) {
      const layer = satelliteTileRefs.current[key];
      if (layer) { try { mapRef.current.removeLayer(layer); } catch(e) {} }
      delete satelliteTileRefs.current[key];
      setSatelliteLayers(prev => ({ ...prev, [key]: false }));
      return;
    }

    // True Color is a pre-tiled global mosaic (EOX), not live GEE compute —
    // no backend round-trip, no minZoom, no loading state needed. See the
    // comment on SATELLITE_LAYERS.true_color for why this is architecturally
    // different from the other three.
    if (key === 'true_color') {
      const meta = SATELLITE_LAYERS.true_color;
      const tl = L.tileLayer(EOX_TRUE_COLOR_URL, {
        opacity: meta.opacity, zIndex: 4, maxZoom: 18,
        attribution: EOX_ATTRIBUTION,
      }).addTo(mapRef.current);
      satelliteTileRefs.current.true_color = tl;
      setSatelliteLayers(prev => ({ ...prev, true_color: true }));
      return;
    }

    // Worldview (NASA GIBS) — same deal as True Color: pre-tiled, keyless,
    // no backend round-trip, no minZoom, no loading state.
    if (key === 'worldview') {
      const meta = SATELLITE_LAYERS.worldview;
      const tl = L.tileLayer(gibsWorldviewUrl(), {
        opacity: meta.opacity, zIndex: 4, maxZoom: 9,
        attribution: GIBS_ATTRIBUTION,
      }).addTo(mapRef.current);
      satelliteTileRefs.current.worldview = tl;
      setSatelliteLayers(prev => ({ ...prev, worldview: true }));
      return;
    }

    // Turning on NDVI/SAR/Thermal: below SATELLITE_MIN_ZOOM, GEE's per-tile
    // compute for a world-view area effectively never resolves (see
    // global_layers.py) — refuse to even start the fetch rather than
    // leaving the user staring at a loading state for 2+ minutes. The
    // toggle button itself is already disabled at this zoom
    // (SatelliteLayerToggles), this is the belt-and-suspenders guard
    // against any other call path.
    if (mapRef.current.getZoom() < SATELLITE_MIN_ZOOM) return;

    // Fetch the cached tile URL first, then add the layer — the
    // composite/tile-URL build can take a few seconds server-side the
    // first time (cached for 12h after), so show a loading state rather
    // than optimistically flipping on like the weather tiles do.
    setSatelliteLoadingKey(key);
    fetch(`${API_URL}/api/v1/layers/${key}`)
      .then(r => { if (!r.ok) throw new Error(`layers/${key} ${r.status}`); return r.json(); })
      .then(data => {
        if (!mapRef.current) return;
        const meta = SATELLITE_LAYERS[key];
        // minZoom on the Leaflet layer itself: if the user zooms back out
        // to world view while this layer is on, Leaflet simply stops
        // requesting tiles below this zoom instead of re-triggering the
        // same expensive-at-low-zoom GEE compute.
        const tl = L.tileLayer(data.tile_url, { opacity: meta.opacity, zIndex: 4, minZoom: SATELLITE_MIN_ZOOM }).addTo(mapRef.current);
        satelliteTileRefs.current[key] = tl;
        setSatelliteLayers(prev => ({ ...prev, [key]: true }));
      })
      .catch(err => {
        console.error(`satellite layer '${key}' load failed:`, err);
      })
      .finally(() => setSatelliteLoadingKey(null));
  }, [satelliteLayers]);

  const handleEventClick = useCallback((event) => {
    setSelectedIntelEvent(event);
    if (!mapRef.current) return;
    setMobilePanel('map');
    mapRef.current.flyTo([event.lat, event.lon], 7, { duration: 1.2 });

    // Flash the marker — target the icon wrapper div (contains the SVG)
    const marker = intelMarkersRef.current[event.id];
    if (marker) {
      const el = marker.getElement();
      const wrapper = el?.querySelector('div');
      if (wrapper) {
        const origFilter = wrapper.style.filter;
        const origScale = wrapper.style.transform;
        wrapper.style.filter = 'drop-shadow(0 0 10px #ffffff) brightness(1.6)';
        wrapper.style.transform = (origScale || '') + ' scale(1.8)';
        wrapper.style.transition = 'all 0.25s ease';
        setTimeout(() => {
          wrapper.style.filter = origFilter;
          wrapper.style.transform = origScale;
        }, 700);
      }
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    if (!queryText.trim()) { setError('Please enter a query.'); return; }
    // No hard AOI block here anymore — an AOI is only actually required
    // for the 9 fixed GEE metrics (vegetation change, flood detection,
    // etc.), not for open-ended research-agent questions ("where should
    // we place a mobile tower"), which don't need one at all. The
    // backend now classifies the query first and only asks for an AOI
    // if it turns out to need one (see the "aoi_required" error check
    // below) — it can also often resolve one itself just from a place
    // name mentioned in the query text, via server-side geocoding.
    clearLayers();
    if (drawGroupRef.current?.getLayers().length > 0) {
      try { aoiBoundsRef.current = drawGroupRef.current.getBounds(); } catch(e) {}
    }
    // Clear the *editable* draw-tool layer (the rectangle/polygon handles),
    // but keep drawnAOI itself set — it's re-drawn as a static outline once
    // the result comes in (see the [result] effect below), and several
    // features (Download Report, the Agri tab) need it to stay available
    // after a query completes rather than forcing a manual re-draw.
    drawGroupRef.current?.clearLayers();
    setIsLoading(true); setError(null); setResult(null); setJobStatus(null);
    const savedAOI = drawnAOI;
    const text = selMetric ? `[Metric: ${selMetric}] ${queryText}` : queryText;
    try {
      const res = await fetch(`${API_URL}/api/v1/query`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ text, aoi_geojson: savedAOI }),
      });
      if (!res.ok) { const e = await res.json().catch(()=>({})); throw new Error(e.detail||`HTTP ${res.status}`); }
      const data = await res.json();
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch(`${API_URL}/api/v1/query/${data.request_id}`);
          if (r.status === 202) { const d = await r.json(); setJobStatus(d); return; }
          if (r.status === 200) {
            clearInterval(pollRef.current);
            const d = await r.json();
            setResult(d); setHistory(p => [d,...p.slice(0,19)]); setIsLoading(false); setJobStatus(null);
            return;
          }
          clearInterval(pollRef.current);
          const e = await r.json().catch(()=>({}));
          setError(e.detail||'Processing failed.'); setIsLoading(false);
        } catch(e) { clearInterval(pollRef.current); setError(`Polling error: ${e.message}`); setIsLoading(false); }
      }, POLL_MS);
    } catch(e) { setError(`Failed to submit: ${e.message}`); setIsLoading(false); }
  }, [queryText, drawnAOI, selMetric, clearLayers]);

  useEffect(() => {
    if (!result || !mapRef.current) return;
    clearLayers();
    // If the AOI was auto-resolved server-side (the query named a place,
    // no manual draw — see geocode_util.py) rather than drawn by hand,
    // the frontend never learned its coordinates until now. Adopt it so
    // the outline below actually has something to draw, and so
    // Download Report (which needs drawnAOI to resend for regeneration)
    // works for this run too, not just manually-drawn ones.
    const effectiveResultAOI = drawnAOI || result.aoi_geojson;
    if (!drawnAOI && result.aoi_geojson) setDrawnAOI(result.aoi_geojson);
    // Re-draw the AOI as a static (non-editable) outline — the editable
    // draw-tool layer was cleared when the query submitted, but the
    // boundary itself should stay visible rather than vanishing once
    // results come in.
    if (effectiveResultAOI) {
      try {
        const outline = L.geoJSON(effectiveResultAOI, { style:{ color:'#2a6abd', weight:1.5, fillOpacity:0, dashArray:'4,3' } }).addTo(mapRef.current);
        layersRef.current.push(outline);
        // Auto-geocoded AOIs never went through handleSubmit's manual-draw
        // path, so aoiBoundsRef was never populated for them — the
        // fitBounds fallbacks below (tile/geojson load failure) need it
        // regardless of how this AOI was obtained.
        if (!aoiBoundsRef.current && outline.getBounds().isValid()) aoiBoundsRef.current = outline.getBounds();
      } catch(e) {}
    }
    if (result.tile_url) {
      const tl = L.tileLayer(result.tile_url, { opacity:0.75 }).addTo(mapRef.current);
      layersRef.current.push(tl);
      if (!result.geojson_url && aoiBoundsRef.current) {
        try { mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] }); } catch(e) {}
      }
    }
    if (result.geojson_url) {
      const gjUrl = result.geojson_url.startsWith('http') ? result.geojson_url : `${API_URL}${result.geojson_url}`;
      fetch(gjUrl).then(r=>r.json()).then(gj => {
        const layer = L.geoJSON(gj, { style:{ color:METRICS_META[result.metric]?.color||'#7eb8d4', weight:2, opacity:0.9, fillOpacity:0.12 } }).addTo(mapRef.current);
        if (layer.getBounds().isValid()) mapRef.current.fitBounds(layer.getBounds(), { padding:[40,40] });
        else if (aoiBoundsRef.current) mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] });
        layersRef.current.push(layer);
      }).catch(()=>{ if (aoiBoundsRef.current) mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] }); });
    }
    // Research-agent result: geocode EACH suggested place (client-side,
    // same Nominatim source PlaceSearchBar already uses) and mark each
    // with its own circle instead of the usual metric tile/geojson
    // overlay — a genuinely different result shape for a genuinely
    // different kind of answer (suggested spots, not a measured area).
    // The backend can suggest several distinct candidates for a question
    // like "where could we place a mobile tower in Jaipur" (e.g.
    // Jagatpura, Sindhi Camp) rather than forcing one — every one of
    // them gets its own circle. `places` is the current field; falling
    // back to the older single place_name/reasoning/radius_km fields
    // keeps this working against any cached/older job result shape too.
    const candidatePlaces = (result.result_type === 'research')
      ? (Array.isArray(result.places) && result.places.length > 0
          ? result.places
          : (result.place_name ? [{ place_name: result.place_name, reasoning: result.reasoning, radius_km: result.radius_km }] : []))
      : [];

    if (candidatePlaces.length > 0) {
      const allBounds = [];
      let remaining = candidatePlaces.length;
      candidatePlaces.forEach(place => {
        const geocodeQuery = result.region ? `${place.place_name}, ${result.region}` : place.place_name;
        fetch(`https://nominatim.openstreetmap.org/search?` + new URLSearchParams({
          q: geocodeQuery, format: 'jsonv2', limit: '1',
        })).then(r => r.json()).then(matches => {
          remaining -= 1;
          if (!matches || matches.length === 0 || !mapRef.current) return;
          const { lat, lon } = matches[0];
          const latNum = parseFloat(lat), lonNum = parseFloat(lon);
          const radiusM = (place.radius_km || 2) * 1000;
          const circle = L.circle([latNum, lonNum], {
            radius: radiusM, color:'#c9a86a', weight:2, fillColor:'#c9a86a', fillOpacity:0.15,
          }).addTo(mapRef.current);
          circle.bindPopup(`<b>${escapeHtml(place.place_name)}</b><br/>${escapeHtml(place.reasoning)}`);
          layersRef.current.push(circle);
          // Center-point marker — the circle alone doesn't mark the exact
          // suggested spot, only the general radius around it. A small
          // target-style pin at the true center makes the actual point
          // unambiguous, especially once there are several circles on
          // screen at once.
          const centerIcon = L.divIcon({
            className: 'vayu-place-marker',
            html: `<svg width="26" height="26" viewBox="0 0 26 26" xmlns="http://www.w3.org/2000/svg">
                     <circle cx="13" cy="13" r="4.5" fill="#c9a86a" stroke="#0d0f12" stroke-width="1.5"/>
                     <circle cx="13" cy="13" r="10" fill="none" stroke="#c9a86a" stroke-width="1.5" opacity="0.6"/>
                   </svg>`,
            iconSize: [26, 26], iconAnchor: [13, 13],
          });
          const centerMarker = L.marker([latNum, lonNum], { icon: centerIcon }).addTo(mapRef.current);
          centerMarker.bindPopup(`<b>${escapeHtml(place.place_name)}</b><br/>${escapeHtml(place.reasoning)}`);
          layersRef.current.push(centerMarker);
          allBounds.push(circle.getBounds());
          // Fit to every successfully-geocoded circle once all requests
          // have settled, not just the first one to resolve — otherwise
          // whichever geocode call happens to come back last silently
          // wins the viewport regardless of arrival order.
          if (remaining === 0 && allBounds.length > 0 && mapRef.current) {
            let combined = allBounds[0];
            for (const b of allBounds.slice(1)) combined = combined.extend(b);
            mapRef.current.fitBounds(combined, { padding:[60,60] });
          }
        }).catch(() => { remaining -= 1; });
      });
    }
  }, [result]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const sidebarEl = (
    <Sidebar tab={tab} setTab={setTab} queryText={queryText} setQueryText={setQueryText}
      selMetric={selMetric} setSelMetric={setSelMetric} drawnAOI={drawnAOI} aoiRegionName={aoiRegionName}
      isLoading={isLoading} error={error} result={result} jobStatus={jobStatus}
      onSubmit={handleSubmit}
      vesselStats={vesselStats}
      weatherLayers={weatherLayers} onToggleWeather={handleToggleWeather}
      aqiOn={aqiOn} aqiLoading={aqiLoading} onToggleAqi={handleToggleAqi}
      satelliteLayers={satelliteLayers} onToggleSatelliteLayer={handleToggleSatelliteLayer}
      satelliteLoadingKey={satelliteLoadingKey} mapZoom={mapZoom}
      orbitalShowSatellites={orbitalShowSatellites} setOrbitalShowSatellites={setOrbitalShowSatellites}
      orbitalShowAircraft={orbitalShowAircraft} setOrbitalShowAircraft={setOrbitalShowAircraft}
      orbitalSatellites={orbitalSatellites} orbitalSatLoaded={orbitalSatLoaded} orbitalSatDebug={orbitalSatDebug}
      orbitalAircraftStats={orbitalAircraftStats} orbitalAircraftValid={orbitalAircraftValid}
      orbitalSearch={orbitalSearch} setOrbitalSearch={setOrbitalSearch}
      orbitalFilteredList={orbitalFilteredList} orbitalSelected={orbitalSelected} setOrbitalSelected={setOrbitalSelected}
      isMobile={isMobile} onClose={() => setMobilePanel('map')} apiUrl={API_URL} mapRef={mapRef} />
  );

  const intelPanelEl = (
    <IntelPanel
      apiUrl={API_URL}
      aoi={drawnAOI}
      onEventClick={handleEventClick}
      onNewEvent={addIntelMarker}
      selectedEvent={selectedIntelEvent}
      onCloseDetail={() => setSelectedIntelEvent(null)}
      isMobile={isMobile}
      onClose={() => setMobilePanel('map')}
    />
  );

  const mapEl = (
    <VayuMap onAreaDrawn={handleManualAreaDrawn} mapRef={mapRef} drawGroupRef={drawGroupRef} intelLayerRef={intelLayerRef} vesselLayerRef={vesselLayerRef} onZoomChange={setMapZoom} />
  );

  // Single tree for both layouts — the map element's position/type never changes
  // between mobile and desktop, so resizing across the breakpoint never remounts
  // (and thus never destroys) the underlying Leaflet map instance.
  return (
    <div style={{ width:'100vw', height:'100vh', position:'relative', display:'flex', flexDirection:'column', overflow:'hidden', background:'#0a0c0f' }}>
      <div style={{ flex:1, minHeight:0, position:'relative', display: isMobile ? 'block' : 'flex', overflow:'hidden' }}>
        {!isMobile && <div style={{ width:330, flexShrink:0, height:'100%', zIndex:10 }}>{sidebarEl}</div>}

        <div style={ isMobile
          ? { position:'absolute', top:0, left:0, right:0, bottom:0, zIndex:1, overflow:'hidden' }
          : { flex:1, height:'100%', position:'relative' } }>
          {/* mapEl stays mounted even while the Orbital tab is active — just
              hidden — so switching tabs never remounts (and thus never
              destroys) the underlying Leaflet map instance, same reasoning
              as the mobile/desktop layout comment above. */}
          <div style={{ display: tab === 'Orbital' ? 'none' : 'contents' }}>
            {mapEl}
            <PlaceSearchBar mapRef={mapRef} drawGroupRef={drawGroupRef} aoiBoundsRef={aoiBoundsRef} onAreaDrawn={handlePlaceSelected} isMobile={isMobile} />
            <MapOverlay result={result} isLoading={isLoading} drawnAOI={drawnAOI} isMobile={isMobile} />
          </div>
          {tab === 'Orbital' && (
            <div style={{ position:'absolute', top:0, left:0, right:0, bottom:0, zIndex:1 }}>
              <Suspense fallback={
                <div style={{ width:'100%', height:'100%', display:'flex', alignItems:'center', justifyContent:'center',
                  fontFamily:'monospace', fontSize:13, color:'#7a8088', background:'#05070a' }}>
                  Loading orbital view…
                </div>
              }>
                <OrbitalGlobe
                  stations={orbitalStations} otherSats={orbitalOtherSats} aircraft={orbitalAircraftValid}
                  showSatellites={orbitalShowSatellites} showAircraft={orbitalShowAircraft}
                  onSelect={setOrbitalSelected}
                />
              </Suspense>
            </div>
          )}
        </div>

        {!isMobile && tab !== 'Orbital' && <div style={{ width:290, flexShrink:0, height:'100%', zIndex:10 }}>{intelPanelEl}</div>}

        {isMobile && mobilePanel === 'analyze' && (
          <div style={{ position:'absolute', top:0, left:0, right:0, bottom:56, zIndex:2000, background:S.surface, overflow:'hidden' }}>
            {sidebarEl}
          </div>
        )}
        {isMobile && mobilePanel === 'intel' && (
          <div style={{ position:'absolute', top:0, left:0, right:0, bottom:56, zIndex:2000, background:'#0a0c0f', overflow:'hidden' }}>
            {intelPanelEl}
          </div>
        )}

        {isMobile && <MobileBottomNav active={mobilePanel} onChange={setMobilePanel} />}
      </div>

      {/* Commodity ticker: desktop only, as a real flex sibling (not a
          fixed overlay) so it never covers the mobile bottom nav or
          anything else — it just claims its own 28px row at the very
          bottom of the viewport, same as the sidebar/map/intel panel
          claim their own columns above it. */}
      {!isMobile && <ErrorBoundary fallback={null}><CommodityTicker apiUrl={API_URL} /></ErrorBoundary>}

      <Analytics />
    </div>
  );
}

// ── Mobile bottom navigation — switches between map / analyze / intel feed ──
function MobileBottomNav({ active, onChange }) {
  const ITEMS = [
    { id:'map',     label:'Map',     icon:'map' },
    { id:'analyze', label:'Analyze', icon:'sliders' },
    { id:'intel',   label:'Intel',   icon:'radio' },
  ];
  return (
    <div style={{
      position:'absolute', left:0, right:0, bottom:0, height:56, zIndex:2100,
      display:'flex', background:S.surface, borderTop:`1px solid ${S.border}`,
      paddingBottom:'env(safe-area-inset-bottom)',
    }}>
      {ITEMS.map(({ id, label, icon }) => (
        <button key={id} onClick={() => onChange(id)}
          style={{ flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap:2,
            background:'transparent', border:'none', color: active===id ? S.accent : S.text3,
            fontFamily:S.mono, fontSize:11, letterSpacing:0.8, textTransform:'uppercase', cursor:'pointer' }}>
          <Icon name={icon} size={19} />
          {label}
        </button>
      ))}
    </div>
  );
}
