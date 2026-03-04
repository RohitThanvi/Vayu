import { useState, useEffect, useRef, useCallback } from 'react';

const API_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const POLL_MS = 2500;

const METRICS_META = {
  vegetation_change:        { label: 'Vegetation Change',       icon: '🌿', color: '#00ff9d', desc: 'NDVI green cover loss/gain' },
  builtup_change:           { label: 'Built-up Change',         icon: '🏙️', color: '#ff6b35', desc: 'Urban expansion analysis' },
  water_change:             { label: 'Water Body Change',       icon: '💧', color: '#00d4ff', desc: 'Surface water gain/loss' },
  flood_detection:          { label: 'Flood Detection',         icon: '🌊', color: '#4a9eff', desc: 'SAR flood mapping' },
  fire_detection:           { label: 'Fire & Burn Scars',       icon: '🔥', color: '#ff4136', desc: 'Active fire mapping' },
  drought_index:            { label: 'Drought Index',           icon: '🏜️', color: '#f5a623', desc: 'Drought severity' },
  land_surface_temperature: { label: 'Land Surface Temp',       icon: '🌡️', color: '#ff6b6b', desc: 'Heat & UHI analysis' },
  deforestation:            { label: 'Deforestation',           icon: '🌲', color: '#2ecc71', desc: 'Forest loss detection' },
  soil_moisture:            { label: 'Soil Moisture',           icon: '🌱', color: '#a0784a', desc: 'Soil & crop stress' },
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

function fmtKey(k) { return k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase()); }
function fmtVal(k, v) {
  if (typeof v !== 'number') return String(v);
  if (k.includes('pct')||k.includes('rate')) return `${v.toFixed(1)}%`;
  if (k.includes('km2')||k.includes('area')) return `${v.toFixed(2)} km²`;
  if (k.includes('_c')||k.includes('temp')) return `${v.toFixed(1)}°C`;
  if (k.includes('count')||k.includes('years')) return v.toFixed(0);
  return v.toFixed(3);
}

// ── Map ───────────────────────────────────────────────────────────────────────
function VayuMap({ onAreaDrawn, mapRef, drawGroupRef }) {
  const divRef = useRef(null);
  useEffect(() => {
    if (mapRef.current) return;
    const map = L.map(divRef.current, { zoomControl:false, attributionControl:false }).setView([26.91,75.78],10);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{ subdomains:'abcd', maxZoom:20 }).addTo(map);
    L.control.zoom({ position:'topright' }).addTo(map);
    const dg = new L.FeatureGroup(); map.addLayer(dg); drawGroupRef.current = dg;
    const dc = new L.Control.Draw({
      position:'topright',
      edit:{ featureGroup:dg, remove:true },
      draw:{
        polygon:{ shapeOptions:{ color:'#00d4ff', weight:2, fillOpacity:0.08 } },
        rectangle:{ shapeOptions:{ color:'#00d4ff', weight:2, fillOpacity:0.08 } },
        polyline:false, circle:false, marker:false, circlemarker:false,
      },
    });
    map.addControl(dc);
    map.on(L.Draw.Event.CREATED, e => { dg.clearLayers(); dg.addLayer(e.layer); onAreaDrawn(e.layer.toGeoJSON().geometry); });
    map.on(L.Draw.Event.EDITED, e => { e.layers.eachLayer(l => onAreaDrawn(l.toGeoJSON().geometry)); });
    map.on(L.Draw.Event.DELETED, () => { if(dg.getLayers().length===0) onAreaDrawn(null); });
    mapRef.current = map;
  }, []);
  return <div ref={divRef} className="w-full h-full" />;
}

// ── Progress ──────────────────────────────────────────────────────────────────
function ProgressBar({ pct, label }) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between">
        <span className="text-xs" style={{ color:'var(--text2)', fontFamily:'var(--mono)' }}>{label}</span>
        <span className="text-xs font-bold" style={{ color:'var(--accent)', fontFamily:'var(--mono)' }}>{pct}%</span>
      </div>
      <div className="h-0.5 rounded-full overflow-hidden" style={{ background:'var(--border)' }}>
        <div className="progress-bar rounded-full" style={{ width:`${pct}%` }} />
      </div>
    </div>
  );
}

// ── Metric selector ───────────────────────────────────────────────────────────
function MetricSelector({ selected, onChange }) {
  return (
    <div>
      <label className="block text-xs font-semibold uppercase tracking-widest mb-2" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>Analysis Type</label>
      <div className="grid grid-cols-3 gap-1.5">
        {Object.entries(METRICS_META).map(([id,m]) => (
          <button key={id} onClick={() => onChange(id===selected?null:id)} title={m.desc}
            className="flex flex-col items-center gap-1 p-2 rounded-lg text-center transition-all duration-150"
            style={{ background:selected===id?'rgba(0,212,255,0.1)':'var(--surface2)', border:`1px solid ${selected===id?m.color:'var(--border)'}`, color:selected===id?m.color:'var(--text2)', cursor:'pointer' }}>
            <span className="text-lg leading-none">{m.icon}</span>
            <span className="text-[9px] leading-tight font-medium" style={{ fontFamily:'var(--mono)' }}>{m.label.split(' ').slice(0,2).join(' ')}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Results ───────────────────────────────────────────────────────────────────
function ResultsPanel({ result }) {
  const m = METRICS_META[result.metric] || { icon:'📊', label:result.metric, color:'var(--accent)' };
  return (
    <div className="space-y-3 animate-fade-up">
      <div className="flex items-center gap-2 pb-2" style={{ borderBottom:'1px solid var(--border)' }}>
        <span className="text-xl">{m.icon}</span>
        <div>
          <div className="text-xs font-semibold uppercase tracking-widest" style={{ color:m.color, fontFamily:'var(--mono)' }}>{m.label}</div>
          <div className="text-xs" style={{ color:'var(--text3)' }}>{result.start_date} → {result.end_date}{result.region&&` · ${result.region}`}</div>
        </div>
      </div>
      <div className="p-3 rounded-lg" style={{ background:'var(--surface2)', border:'1px solid var(--border)' }}>
        <div className="text-xs uppercase tracking-widest mb-1.5" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>Finding</div>
        <p className="text-sm leading-relaxed font-medium" style={{ color:'var(--text)' }}>{result.summary}</p>
      </div>
      {result.insight && (
        <div className="p-3 rounded-lg" style={{ background:'rgba(0,212,255,0.05)', border:'1px solid rgba(0,212,255,0.2)' }}>
          <div className="text-xs uppercase tracking-widest mb-1.5" style={{ color:'var(--accent)', fontFamily:'var(--mono)' }}>⚡ AI Insight</div>
          <p className="text-xs leading-relaxed" style={{ color:'var(--text2)' }}>{result.insight}</p>
        </div>
      )}
      <div>
        <div className="text-xs uppercase tracking-widest mb-2" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>Key Metrics</div>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(result.metrics||{}).map(([k,v]) => (
            <div key={k} className="metric-card p-2.5">
              <div className="text-xs leading-tight mb-1" style={{ color:'var(--text3)' }}>{fmtKey(k)}</div>
              <div className="text-sm font-bold" style={{ color:m.color, fontFamily:'var(--mono)' }}>{fmtVal(k,v)}</div>
            </div>
          ))}
        </div>
      </div>
      {result.geojson_url && (
        <a href={result.geojson_url} target="_blank" rel="noreferrer"
          className="block text-center text-xs py-2 rounded-lg transition-colors"
          style={{ background:'var(--surface2)', border:'1px solid var(--border)', color:'var(--text2)' }}>
          ↓ Download GeoJSON
        </a>
      )}
    </div>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar({ tab,setTab, queryText,setQueryText, selMetric,setSelMetric, drawnAOI, isLoading,error,result,jobStatus, onSubmit, history,onSelectHistory }) {
  const [eIdx, setEIdx] = useState(0);
  const cycleExample = () => { const n=(eIdx+1)%EXAMPLES.length; setEIdx(n); setQueryText(EXAMPLES[n]); };
  const TABS = ['Analyze','History','Guide'];

  return (
    <div className="glass-panel flex flex-col h-full w-full" style={{ minWidth:0 }}>
      {/* Logo */}
      <div className="px-5 pt-5 pb-4 flex-shrink-0" style={{ borderBottom:'1px solid var(--border)' }}>
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center text-lg" style={{ background:'rgba(0,212,255,0.12)', border:'1px solid rgba(0,212,255,0.25)' }}>🌍</div>
          <div>
            <h1 className="text-base font-bold tracking-tight" style={{ fontFamily:'var(--mono)', color:'var(--text)' }}>VAYU</h1>
            <p className="text-[10px]" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>GEOSPATIAL INTELLIGENCE</p>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-shrink-0 px-3 pt-3 gap-1">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)} className="flex-1 py-1.5 text-xs rounded-md transition-all"
            style={{ fontFamily:'var(--mono)', background:tab===t?'rgba(0,212,255,0.12)':'transparent', color:tab===t?'var(--accent)':'var(--text3)', border:`1px solid ${tab===t?'rgba(0,212,255,0.25)':'transparent'}`, cursor:'pointer' }}>
            {t}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {tab === 'Analyze' && (
          <>
            <MetricSelector selected={selMetric} onChange={setSelMetric} />
            <div>
              <div className="flex justify-between items-center mb-2">
                <label className="text-xs font-semibold uppercase tracking-widest" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>Query</label>
                <button onClick={cycleExample} className="text-[10px] transition-colors" style={{ color:'var(--text3)', cursor:'pointer', fontFamily:'var(--mono)' }}>example ↻</button>
              </div>
              <textarea rows={3} className="vayu-input w-full rounded-lg px-3 py-2.5 text-sm resize-none"
                placeholder="e.g. How much green cover did this area lose since 2020?"
                value={queryText} onChange={e => setQueryText(e.target.value)} />
            </div>
            <div className="flex items-center gap-2 text-xs rounded-lg px-3 py-2.5"
              style={{ background:drawnAOI?'rgba(0,255,157,0.05)':'var(--surface2)', border:`1px solid ${drawnAOI?'rgba(0,255,157,0.2)':'var(--border)'}` }}>
              <span style={{ color:drawnAOI?'var(--accent2)':'var(--text3)' }}>{drawnAOI?'✓':'○'}</span>
              <span style={{ color:drawnAOI?'var(--accent2)':'var(--text3)', fontFamily:'var(--mono)' }}>{drawnAOI?'Area of Interest defined':'Draw AOI on map →'}</span>
            </div>
            <button onClick={onSubmit} disabled={isLoading||!queryText||!drawnAOI}
              className="w-full py-3 rounded-lg text-sm font-semibold transition-all duration-200"
              style={{ fontFamily:'var(--mono)', background:isLoading||!queryText||!drawnAOI?'var(--surface2)':'linear-gradient(135deg,rgba(0,212,255,0.18),rgba(0,255,157,0.12))', border:`1px solid ${isLoading||!queryText||!drawnAOI?'var(--border)':'rgba(0,212,255,0.35)'}`, color:isLoading||!queryText||!drawnAOI?'var(--text3)':'var(--accent)', cursor:isLoading||!queryText||!drawnAOI?'not-allowed':'pointer' }}>
              {isLoading?'ANALYZING...':'RUN ANALYSIS'}
            </button>
            {isLoading && jobStatus && <ProgressBar pct={jobStatus.progress_pct||0} label={jobStatus.stage_label||jobStatus.stage||'Processing...'} />}
            {error && (
              <div className="p-3 rounded-lg text-xs animate-fade-up" style={{ background:'rgba(255,65,54,0.07)', border:'1px solid rgba(255,65,54,0.25)', color:'#ff6b6b' }}>
                <div className="font-bold mb-1" style={{ fontFamily:'var(--mono)' }}>⚠ ERROR</div>
                <div style={{ color:'var(--text2)' }}>{error}</div>
              </div>
            )}
            {result && <ResultsPanel result={result} />}
          </>
        )}

        {tab === 'History' && (
          <div className="space-y-2">
            <div className="text-xs uppercase tracking-widest mb-3" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>{history.length} Analyses</div>
            {history.length === 0 && (
              <div className="text-center py-8 text-xs" style={{ color:'var(--text3)' }}>No analyses yet.<br/>Run your first query in the Analyze tab.</div>
            )}
            {history.map((item,i) => {
              const hm = METRICS_META[item.metric]||{ icon:'📊', label:item.metric, color:'var(--accent)' };
              return (
                <button key={i} onClick={() => { setTab('Analyze'); onSelectHistory(item); }} className="w-full text-left p-2.5 rounded-lg transition-all"
                  style={{ background:'var(--surface2)', border:'1px solid var(--border)', cursor:'pointer' }}>
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{hm.icon}</span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium truncate" style={{ color:'var(--text)', fontFamily:'var(--mono)' }}>{hm.label}</div>
                      <div className="text-xs truncate" style={{ color:'var(--text3)' }}>{item.summary?.slice(0,55)}…</div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {tab === 'Guide' && (
          <div className="space-y-4 text-xs" style={{ color:'var(--text2)' }}>
            <div>
              <div className="font-bold mb-2 uppercase tracking-widest" style={{ color:'var(--accent)', fontFamily:'var(--mono)' }}>How to use</div>
              {[['1','Select an analysis type or just type your question'],['2','Draw a polygon or rectangle on the map'],['3','Click Run Analysis and wait for results'],['4','View metrics, AI insights, and map overlays']].map(([n,t]) => (
                <div key={n} className="flex gap-3 mb-2.5">
                  <span className="w-5 h-5 rounded flex-shrink-0 font-bold flex items-center justify-center" style={{ background:'rgba(0,212,255,0.12)', color:'var(--accent)', fontFamily:'var(--mono)', fontSize:'10px' }}>{n}</span>
                  <span>{t}</span>
                </div>
              ))}
            </div>
            <div style={{ borderTop:'1px solid var(--border)', paddingTop:'12px' }}>
              <div className="font-bold mb-2 uppercase tracking-widest" style={{ color:'var(--accent)', fontFamily:'var(--mono)' }}>Analysis Types</div>
              {Object.entries(METRICS_META).map(([id,m]) => (
                <div key={id} className="flex items-start gap-2 mb-2">
                  <span>{m.icon}</span>
                  <div><span className="font-medium" style={{ color:'var(--text)' }}>{m.label}</span><span className="ml-1" style={{ color:'var(--text3)' }}>— {m.desc}</span></div>
                </div>
              ))}
            </div>
            <div style={{ borderTop:'1px solid var(--border)', paddingTop:'12px' }}>
              <div className="font-bold mb-2 uppercase tracking-widest" style={{ color:'var(--accent)', fontFamily:'var(--mono)' }}>Data Sources</div>
              {['Sentinel-2 SR Harmonized (ESA)','Sentinel-1 SAR GRD (ESA)','Landsat 8/9 Collection 2 (USGS)','MODIS MCD64A1 / MOD14A1 (NASA)','Dynamic World V1 (Google/WRI)','Hansen GFC 2023 (UMD)','JRC GSW Monthly History (EC)','SMAP 10km Soil Moisture (NASA)'].map(d => (
                <div key={d} className="mb-1 flex gap-2"><span style={{ color:'var(--border2)' }}>▸</span><span>{d}</span></div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="flex-shrink-0 px-4 py-3" style={{ borderTop:'1px solid var(--border)' }}>
        <div className="text-[10px] flex items-center justify-between" style={{ color:'var(--text3)', fontFamily:'var(--mono)' }}>
          <span>VAYU v1.0.0</span>
          <span style={{ color:'var(--border2)' }}>GEE · GROQ · FASTAPI</span>
        </div>
      </div>
    </div>
  );
}

// ── Map overlay ───────────────────────────────────────────────────────────────
function MapOverlay({ result, isLoading, drawnAOI }) {
  if (!isLoading && !result && !drawnAOI) return (
    <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none">
      <div className="px-4 py-2 rounded-full text-xs" style={{ background:'rgba(13,24,33,0.88)', border:'1px solid var(--border2)', color:'var(--text2)', fontFamily:'var(--mono)' }}>
        Use the draw tools (top-right) to define your Area of Interest
      </div>
    </div>
  );
  if (isLoading) return (
    <div className="absolute top-4 left-1/2 -translate-x-1/2 z-[1000] pointer-events-none">
      <div className="px-4 py-2 rounded-full text-xs flex items-center gap-2 animate-glow" style={{ background:'rgba(0,212,255,0.1)', border:'1px solid rgba(0,212,255,0.35)', color:'var(--accent)', fontFamily:'var(--mono)' }}>
        <span className="animate-spin">⟳</span> ANALYZING SATELLITE DATA
      </div>
    </div>
  );
  if (result) {
    const m = METRICS_META[result.metric]||{ icon:'📊', label:result.metric };
    return (
      <div className="absolute top-4 right-4 z-[1000] pointer-events-none">
        <div className="px-3 py-1.5 rounded-lg text-xs" style={{ background:'rgba(13,24,33,0.88)', border:'1px solid var(--border2)', color:'var(--text2)', fontFamily:'var(--mono)' }}>
          {m.icon} {m.label} · {result.start_date?.slice(0,4)}–{result.end_date?.slice(0,4)}
        </div>
      </div>
    );
  }
  return null;
}

// ── Root App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [tab, setTab] = useState('Analyze');
  const [queryText, setQueryText] = useState('');
  const [selMetric, setSelMetric] = useState(null);
  const [drawnAOI, setDrawnAOI] = useState(null);
  const [result, setResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const [jobStatus, setJobStatus] = useState(null);
  const [history, setHistory] = useState([]);

  const mapRef = useRef(null);
  const drawGroupRef = useRef(null);
  const layersRef = useRef([]);
  const pollRef = useRef(null);
  const aoiBoundsRef = useRef(null);

  const clearLayers = useCallback(() => {
    layersRef.current.forEach(l => { if (mapRef.current?.hasLayer(l)) mapRef.current.removeLayer(l); });
    layersRef.current = [];
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
      // Zoom to AOI if no geojson available
      if (!result.geojson_url && aoiBoundsRef.current) {
        try { mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] }); } catch(e) {}
      }
    }
    if (result.geojson_url) {
      const gjUrl = result.geojson_url.startsWith('http') ? result.geojson_url : `${API_URL}${result.geojson_url}`;
      fetch(gjUrl).then(r=>r.json()).then(gj => {
        const layer = L.geoJSON(gj, { style:{ color:METRICS_META[result.metric]?.color||'#00d4ff', weight:2, opacity:0.9, fillOpacity:0.15 } }).addTo(mapRef.current);
        if (layer.getBounds().isValid()) mapRef.current.fitBounds(layer.getBounds(), { padding:[40,40] });
        else if (aoiBoundsRef.current) mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] });
        layersRef.current.push(layer);
      }).catch(()=>{
        if (aoiBoundsRef.current) mapRef.current.fitBounds(aoiBoundsRef.current, { padding:[40,40] });
      });
    }
  }, [result]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  return (
    <div className="w-screen h-screen flex overflow-hidden" style={{ background:'var(--bg)' }}>
      <div className="w-80 flex-shrink-0 h-full z-10">
        <Sidebar tab={tab} setTab={setTab} queryText={queryText} setQueryText={setQueryText}
          selMetric={selMetric} setSelMetric={setSelMetric} drawnAOI={drawnAOI}
          isLoading={isLoading} error={error} result={result} jobStatus={jobStatus}
          onSubmit={handleSubmit} history={history}
          onSelectHistory={r => { setResult(r); clearLayers(); }} />
      </div>
      <div className="flex-1 h-full relative scan-overlay">
        <VayuMap onAreaDrawn={setDrawnAOI} mapRef={mapRef} drawGroupRef={drawGroupRef} />
        <MapOverlay result={result} isLoading={isLoading} drawnAOI={drawnAOI} />
      </div>
    </div>
  );
}
