import { useState, useEffect, useRef, useCallback } from 'react';
import IntelPanel from './components/IntelPanel';
import { useVesselTracker } from './hooks/useVesselTracker';

const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000';
const POLL_MS = 2500;

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
  // Generic dot fallback
  DOT: (fill, border) => `
    <svg viewBox="0 0 16 16" width="10" height="10">
      <circle cx="8" cy="8" r="5" fill="${fill}" stroke="${border}" stroke-width="1.2"/>
    </svg>`,
  // CARGO vessel — bow-forward hull silhouette
  SHIP_CARGO: (fill, border) => `
    <svg viewBox="0 0 16 16" width="15" height="15">
      <path d="M8 1.4 L11 9.4 L11 12.4 L5 12.4 L5 9.4 Z" fill="${fill}" stroke="${border}" stroke-width="0.6"/>
      <rect x="6.4" y="9.6" width="3.2" height="1.6" fill="${border}" opacity="0.5"/>
    </svg>`,
  // TANKER vessel — oil barrel
  BARREL: (fill, border) => `
    <svg viewBox="0 0 16 16" width="14" height="14">
      <ellipse cx="8" cy="3.4" rx="4" ry="1.4" fill="none" stroke="${border}" stroke-width="1"/>
      <rect x="4" y="3.4" width="8" height="8.6" fill="${fill}" opacity="0.9"/>
      <ellipse cx="8" cy="12" rx="4" ry="1.4" fill="none" stroke="${border}" stroke-width="1"/>
      <line x1="4" y1="6.4" x2="12" y2="6.4" stroke="${border}" stroke-width="0.6" opacity="0.7"/>
      <line x1="4" y1="9.4" x2="12" y2="9.4" stroke="${border}" stroke-width="0.6" opacity="0.7"/>
    </svg>`,
  // PASSENGER vessel — ferry with cabin
  FERRY: (fill, border) => `
    <svg viewBox="0 0 16 16" width="15" height="15">
      <path d="M3 11 L13 11 L11 14 L5 14 Z" fill="${fill}" stroke="${border}" stroke-width="0.5"/>
      <rect x="6" y="6.2" width="4" height="4.8" fill="${fill}" stroke="${border}" stroke-width="0.5"/>
      <rect x="7.1" y="2.8" width="1.8" height="3.4" fill="${border}"/>
    </svg>`,
  // FISHING vessel — small boat with pole
  FISHBOAT: (fill, border) => `
    <svg viewBox="0 0 16 16" width="14" height="14">
      <path d="M3 10.4 L13 10.4 L11 13.4 L5 13.4 Z" fill="${fill}" stroke="${border}" stroke-width="0.5"/>
      <line x1="8" y1="10.4" x2="8" y2="2.6" stroke="${border}" stroke-width="1"/>
      <line x1="8" y1="3.6" x2="12" y2="5.6" stroke="${border}" stroke-width="0.8"/>
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
  // Barrels have no directional bow — skip rotation for tankers to avoid implying false heading
  const rotation = vessel.category === 'TANKER' ? 0 : cog;
  const icon = L.divIcon({
    className: '',
    html: `<div style="
      transform: rotate(${rotation}deg);
      filter: drop-shadow(0 0 3px ${c.fill}99);
      display:flex; align-items:center; justify-content:center;
    ">${svg}</div>`,
    iconSize: [15, 15],
    iconAnchor: [7.5, 7.5],
  });
  return L.marker([vessel.lat, vessel.lon], { icon, zIndexOffset: 50 });
}

// ── Map ───────────────────────────────────────────────────────────────────────
function VayuMap({ onAreaDrawn, mapRef, drawGroupRef, intelLayerRef, vesselLayerRef }) {
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
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
      subdomains:'abcd', maxZoom:20, noWrap:true, bounds:[[-85,-180],[85,180]],
    }).addTo(map);
    L.control.zoom({ position:'topright' }).addTo(map);

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
  }, []);
  return <div ref={divRef} style={{ width:'100%', height:'100%' }} />;
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
      <div style={{ fontSize:15, color:S.text3, fontFamily:S.mono, letterSpacing:2, marginBottom:8, textTransform:'uppercase' }}>Analysis Type</div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:4 }}>
        {Object.entries(METRICS_META).map(([id,m]) => (
          <button key={id} onClick={() => onChange(id===selected?null:id)} title={m.desc}
            style={{ padding:'7px 4px', fontSize:14, fontFamily:S.mono, letterSpacing:0.5,
              background: selected===id ? 'rgba(126,184,212,0.08)' : S.surface2,
              border: `1px solid ${selected===id ? S.accent : S.border}`,
              color: selected===id ? S.accent : S.text3, cursor:'pointer',
              textAlign:'center', lineHeight:1.4, textTransform:'uppercase' }}>
            {m.label.split(' ').slice(0,2).join(' ')}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Results ───────────────────────────────────────────────────────────────────
function ResultsPanel({ result }) {
  const m = METRICS_META[result.metric] || { label:result.metric, color:S.accent };
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
    </div>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar({ tab,setTab, queryText,setQueryText, selMetric,setSelMetric, drawnAOI,
  isLoading,error,result,jobStatus, onSubmit, history,onSelectHistory, vesselStats }) {
  const [eIdx, setEIdx] = useState(0);
  const cycleExample = () => { const n=(eIdx+1)%EXAMPLES.length; setEIdx(n); setQueryText(EXAMPLES[n]); };
  const TABS = ['Analyze','History','Maritime','Guide'];
  return (
    <div style={{ background:S.surface, borderRight:`1px solid ${S.border}`, display:'flex', flexDirection:'column', height:'100%', width:'100%' }}>
      <div style={{ padding:'12px 14px', borderBottom:`1px solid ${S.border}`, flexShrink:0 }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div>
            <div style={{ fontFamily:S.mono, fontSize:18, fontWeight:700, letterSpacing:3, color:S.text }}>VAYU</div>
            <div style={{ fontFamily:S.mono, fontSize:14, color:S.text3, letterSpacing:2 }}>GEOSPATIAL INTELLIGENCE</div>
          </div>
          <div style={{ fontSize:14, fontFamily:S.mono, color:'#4a7c59', border:'1px solid #4a7c59', padding:'2px 7px', letterSpacing:1.5 }}>v2.0</div>
        </div>
      </div>
      <div style={{ display:'flex', borderBottom:`1px solid ${S.border}`, flexShrink:0 }}>
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ flex:1, padding:'9px 0', fontSize:15, fontFamily:S.mono, letterSpacing:1.5, textTransform:'uppercase',
              background:'transparent', border:'none', borderBottom:`2px solid ${tab===t?S.accent:'transparent'}`,
              color:tab===t?S.accent:S.text3, cursor:'pointer' }}>
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
              {drawnAOI ? 'AOI DEFINED' : 'DRAW AOI ON MAP'}
            </div>
            <button onClick={onSubmit} disabled={isLoading||!queryText||!drawnAOI}
              style={{ padding:'10px', fontSize:15, fontFamily:S.mono, letterSpacing:2, textTransform:'uppercase',
                background: isLoading||!queryText||!drawnAOI ? S.surface2 : 'rgba(126,184,212,0.1)',
                border: `1px solid ${isLoading||!queryText||!drawnAOI ? S.border : S.accent}`,
                color: isLoading||!queryText||!drawnAOI ? S.text3 : S.accent,
                cursor: isLoading||!queryText||!drawnAOI ? 'not-allowed' : 'pointer' }}>
              {isLoading ? 'ANALYZING...' : 'RUN ANALYSIS'}
            </button>
            {isLoading && jobStatus && <ProgressBar pct={jobStatus.progress_pct||0} label={jobStatus.stage_label||jobStatus.stage||'Processing...'} />}
            {error && (
              <div style={{ background:'rgba(139,32,32,0.08)', border:'1px solid rgba(139,32,32,0.3)', padding:'9px 11px' }}>
                <div style={{ fontSize:14, fontFamily:S.mono, color:'#8b2020', letterSpacing:2, marginBottom:4 }}>ERROR</div>
                <div style={{ fontSize:15, color:S.text2 }}>{error}</div>
              </div>
            )}
            {result && <ResultsPanel result={result} />}
          </>
        )}
        {tab === 'History' && (
          <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
            <div style={{ fontSize:15, color:S.text3, fontFamily:S.mono, letterSpacing:2, marginBottom:4 }}>{history.length} ANALYSES</div>
            {history.length === 0 && <div style={{ textAlign:'center', padding:'24px 0', fontSize:15, color:S.text3, fontFamily:S.mono }}>NO ANALYSES YET</div>}
            {history.map((item,i) => {
              const hm = METRICS_META[item.metric]||{ label:item.metric, color:S.accent };
              return (
                <button key={i} onClick={() => { setTab('Analyze'); onSelectHistory(item); }}
                  style={{ textAlign:'left', padding:'9px 11px', background:S.surface2, border:`1px solid ${S.border}`, cursor:'pointer', width:'100%' }}>
                  <div style={{ fontSize:15, fontFamily:S.mono, color:hm.color, letterSpacing:1, marginBottom:4, textTransform:'uppercase' }}>{hm.label}</div>
                  <div style={{ fontSize:14, color:S.text3 }}>{item.summary?.slice(0,60)}...</div>
                </button>
              );
            })}
          </div>
        )}
        {tab === 'Maritime' && (
          <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
            <div>
              <div style={{ fontSize:15, color:S.text3, fontFamily:S.mono, letterSpacing:2, marginBottom:8, textTransform:'uppercase' }}>
                Active Vessels — {vesselStats?.active_vessels ?? 0}
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:6 }}>
                {Object.entries(VESSEL_COLORS).map(([cat, c]) => {
                  const count = vesselStats?.by_category?.[cat] || 0;
                  return (
                    <div key={cat} style={{ background:S.surface2, border:`1px solid ${S.border}`, padding:'8px 10px' }}>
                      <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:4 }}>
                        <div style={{ width:8, height:8, background:c.fill, borderRadius:2 }} />
                        <span style={{ fontSize:13, color:S.text3, letterSpacing:0.5 }}>{c.label}</span>
                      </div>
                      <div style={{ fontSize:18, fontFamily:S.mono, color:'#ffffff', fontWeight:700 }}>{count}</div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:12 }}>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.accent, letterSpacing:2, marginBottom:8, textTransform:'uppercase' }}>
                Monitored Chokepoints
              </div>
              {['Strait of Hormuz','Strait of Malacca','Bab-el-Mandeb','Suez Canal','Strait of Gibraltar','Panama Canal','English Channel'].map(name => (
                <div key={name} style={{ fontSize:14, color:S.text3, fontFamily:S.mono, marginBottom:5 }}>-- {name}</div>
              ))}
            </div>

            {(!vesselStats || vesselStats.active_vessels === 0) && (
              <div style={{ background:'rgba(201,147,58,0.06)', border:'1px solid rgba(201,147,58,0.25)', padding:'10px 12px', fontSize:13, color:S.text2, lineHeight:1.6 }}>
                No live vessel data. Maritime tracking requires an AISSTREAM_API_KEY
                (free at aisstream.io) configured on the backend.
              </div>
            )}
          </div>
        )}
        {tab === 'Guide' && (
          <div style={{ display:'flex', flexDirection:'column', gap:14, fontSize:15, color:S.text2 }}>
            <div>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.accent, letterSpacing:2, marginBottom:10, textTransform:'uppercase' }}>How to use</div>
              {[['1','Select an analysis type'],['2','Draw a polygon on the map'],['3','Enter a natural language query'],['4','Click Run Analysis']].map(([n,t]) => (
                <div key={n} style={{ display:'flex', gap:10, marginBottom:8, alignItems:'flex-start' }}>
                  <span style={{ fontSize:14, fontFamily:S.mono, color:S.accent, border:`1px solid ${S.border}`, padding:'2px 6px', flexShrink:0 }}>{n}</span>
                  <span style={{ fontSize:15, color:S.text2 }}>{t}</span>
                </div>
              ))}
            </div>
            <div style={{ borderTop:`1px solid ${S.border}`, paddingTop:12 }}>
              <div style={{ fontSize:15, fontFamily:S.mono, color:S.accent, letterSpacing:2, marginBottom:10, textTransform:'uppercase' }}>Live Intel Sources</div>
              {[
                ['USGS','Earthquakes M3.5+, global, every 5 min'],
                ['NASA FIRMS','Active fire hotspots, every 15 min'],
                ['GDELT','Geolocated news events, every 10 min'],
                ['ACLED','Armed conflict events (key required)'],
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
        <div style={{ display:'flex', justifyContent:'space-between', fontSize:14, fontFamily:S.mono, color:S.text3, letterSpacing:1 }}>
          <span>VAYU v2.0.0</span><span>GEE · GROQ · FASTAPI</span>
        </div>
      </div>
    </div>
  );
}

// ── Map overlay ───────────────────────────────────────────────────────────────
function MapOverlay({ result, isLoading, drawnAOI }) {
  if (!isLoading && !result && !drawnAOI) return (
    <div style={{ position:'absolute', bottom:16, left:'50%', transform:'translateX(-50%)', zIndex:1000, pointerEvents:'none' }}>
      <div style={{ padding:'7px 16px', fontSize:15, fontFamily:"'Courier New',monospace", letterSpacing:1, background:'rgba(13,17,23,0.92)', border:'1px solid #3a4250', color:'#ffffff' }}>
        USE DRAW TOOLS (TOP-RIGHT) TO DEFINE AREA OF INTEREST
      </div>
    </div>
  );
  if (isLoading) return (
    <div style={{ position:'absolute', top:12, left:'50%', transform:'translateX(-50%)', zIndex:1000, pointerEvents:'none' }}>
      <div style={{ padding:'6px 16px', fontSize:15, fontFamily:"'Courier New',monospace", letterSpacing:1.5, background:'rgba(13,17,23,0.92)', border:'1px solid #7eb8d4', color:'#7eb8d4' }}>
        ANALYZING SATELLITE DATA
      </div>
    </div>
  );
  if (result) {
    const m = METRICS_META[result.metric]||{ label:result.metric };
    return (
      <div style={{ position:'absolute', top:12, right:12, zIndex:1000, pointerEvents:'none' }}>
        <div style={{ padding:'6px 12px', fontSize:15, fontFamily:"'Courier New',monospace", letterSpacing:1, background:'rgba(13,17,23,0.92)', border:'1px solid #3a4250', color:'#ffffff' }}>
          {m.label.toUpperCase()} · {result.start_date?.slice(0,4)}--{result.end_date?.slice(0,4)}
        </div>
      </div>
    );
  }
  return null;
}

// ── Root App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [tab, setTab]             = useState('Analyze');
  const [queryText, setQueryText] = useState('');
  const [selMetric, setSelMetric] = useState(null);
  const [drawnAOI, setDrawnAOI]   = useState(null);
  const [result, setResult]       = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError]         = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [history, setHistory]     = useState([]);

  const mapRef          = useRef(null);
  const drawGroupRef    = useRef(null);
  const layersRef       = useRef([]);
  const pollRef         = useRef(null);
  const aoiBoundsRef    = useRef(null);
  const intelLayerRef   = useRef(null);   // LayerGroup for intel markers
  const intelMarkersRef = useRef({});     // id -> marker, for dedup
  const vesselLayerRef  = useRef(null);   // LayerGroup for vessel markers
  const vesselMarkersRef = useRef({});    // mmsi -> marker
  const vesselTrailsRef = useRef({});     // mmsi -> { points:[[lat,lon],...], polyline }

  // Live maritime vessel tracking (AIS via aisstream.io)
  const { vessels, stats: vesselStats } = useVesselTracker(API_URL, true);

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
    } catch(e) {}
  }, []);

  // ── Render/update vessel markers + route trails whenever snapshot changes ──
  const MAX_TRAIL_POINTS = 18;
  const TRAIL_MIN_DELTA = 0.01; // degrees — skip near-duplicate points

  useEffect(() => {
    if (!vesselLayerRef.current) return;
    const seen = new Set();

    vessels.forEach(v => {
      if (typeof v.lat !== 'number' || typeof v.lon !== 'number') return;
      seen.add(v.mmsi);

      // ── Marker: update position+rotation, or create new ──────────────────
      const existing = vesselMarkersRef.current[v.mmsi];
      if (existing) {
        existing.setLatLng([v.lat, v.lon]);
        try { existing.setIcon(createVesselMarker(v).options.icon); } catch(e) {}
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
            </div>`,
            { permanent:false, direction:'top', opacity:1 }
          );
          vesselLayerRef.current.addLayer(marker);
          vesselMarkersRef.current[v.mmsi] = marker;
        } catch(e) {}
      }

      // ── Route trail: append point, redraw polyline ────────────────────────
      try {
        let trail = vesselTrailsRef.current[v.mmsi];
        if (!trail) {
          trail = { points: [], polyline: null };
          vesselTrailsRef.current[v.mmsi] = trail;
        }
        const last = trail.points[trail.points.length - 1];
        const moved = !last || Math.abs(last[0]-v.lat) > TRAIL_MIN_DELTA || Math.abs(last[1]-v.lon) > TRAIL_MIN_DELTA;
        if (moved) {
          trail.points.push([v.lat, v.lon]);
          if (trail.points.length > MAX_TRAIL_POINTS) trail.points.shift();
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
      }
    });
  }, [vessels]);

  // ── Handle click on feed item: fly map + highlight marker ──────────────────
  const handleEventClick = useCallback((event) => {
    if (!mapRef.current) return;
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
    if (!drawnAOI) { setError('Please draw an Area of Interest on the map.'); return; }
    clearLayers();
    if (drawGroupRef.current?.getLayers().length > 0) {
      try { aoiBoundsRef.current = drawGroupRef.current.getBounds(); } catch(e) {}
    }
    drawGroupRef.current?.clearLayers();
    setIsLoading(true); setError(null); setResult(null); setJobStatus(null);
    const savedAOI = drawnAOI;
    setDrawnAOI(null);
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
  }, [result]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  return (
    <div style={{ width:'100vw', height:'100vh', display:'flex', overflow:'hidden', background:'#0a0c0f' }}>
      {/* Left sidebar */}
      <div style={{ width:270, flexShrink:0, height:'100%', zIndex:10 }}>
        <Sidebar tab={tab} setTab={setTab} queryText={queryText} setQueryText={setQueryText}
          selMetric={selMetric} setSelMetric={setSelMetric} drawnAOI={drawnAOI}
          isLoading={isLoading} error={error} result={result} jobStatus={jobStatus}
          onSubmit={handleSubmit} history={history}
          onSelectHistory={r => { setResult(r); clearLayers(); }}
          vesselStats={vesselStats} />
      </div>

      {/* Map */}
      <div style={{ flex:1, height:'100%', position:'relative' }}>
        <VayuMap onAreaDrawn={setDrawnAOI} mapRef={mapRef} drawGroupRef={drawGroupRef} intelLayerRef={intelLayerRef} vesselLayerRef={vesselLayerRef} />
        <MapOverlay result={result} isLoading={isLoading} drawnAOI={drawnAOI} />
      </div>

      {/* Right intel panel */}
      <div style={{ width:290, flexShrink:0, height:'100%', zIndex:10 }}>
        <IntelPanel
          apiUrl={API_URL}
          aoi={drawnAOI}
          onEventClick={handleEventClick}
          onNewEvent={addIntelMarker}
        />
      </div>
    </div>
  );
}
