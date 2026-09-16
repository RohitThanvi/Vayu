/**
 * SpectraPanel.jsx
 * The Spectra tab — direct-access remote sensing toolkit (replaces the
 * old static "Guide" tab). Separate from the Analyze tab's natural-
 * language 9-metric flow: this is for picking a tool + AOI + dates
 * directly, the way an actual remote sensing researcher would rather
 * than typing a question in English. See backend
 * services/gee_remote_sensing.py for the full methodology on each tool
 * — every result here surfaces its "method" field prominently, on
 * purpose, so the numbers are never presented without the dataset/
 * technique that produced them.
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  surface: '#0d1117', surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a',
};

const POLL_MS = 2000;

// Flattens a (possibly nested) result object into flat key/value rows
// for CSV export — the AOI-summary numbers (means, std_devs, areas,
// etc.), not per-pixel data (that's what the GeoTIFF download is for).
// Skips map_layer/download_url since those are URLs, not data.
function _flattenForCsv(obj, prefix = '', rows = []) {
  for (const [k, v] of Object.entries(obj || {})) {
    if (k === 'map_layer' || k === 'download_url') continue;
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      _flattenForCsv(v, key, rows);
    } else if (Array.isArray(v)) {
      v.forEach((item, i) => {
        if (item && typeof item === 'object') _flattenForCsv(item, `${key}[${i}]`, rows);
        else rows.push([`${key}[${i}]`, item]);
      });
    } else {
      rows.push([key, v]);
    }
  }
  return rows;
}

function downloadResultCsv(tool, result) {
  const rows = _flattenForCsv(result);
  const csv = ['field,value', ...rows.map(([k, v]) => {
    const val = String(v ?? '').replace(/"/g, '""');
    return `"${k}","${val}"`;
  })].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `vayu_spectra_${tool}_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

const TOOL_META = {
  spectral_indices: { label: 'Spectral Indices', needsDates: true, icon: '≈' },
  terrain: { label: 'Terrain Analysis', needsDates: false, icon: '△' },
  lulc: { label: 'Land Cover', needsDates: false, icon: '▦' },
  snow_cover: { label: 'Snow Cover', needsDates: true, icon: '❄' },
  sar_backscatter: { label: 'SAR Backscatter', needsDates: true, icon: '∿' },
  change_detection: { label: 'Change Detection', needsDates: false, needsTwoPeriods: true, icon: '⇄' },
  burn_severity: { label: 'Burn Severity', needsDates: false, needsPrePost: true, icon: '🔥' },
  atmospheric_composition: { label: 'Atmosphere', needsDates: true, icon: '☁' },
};

const INDEX_CHOICES = [
  ['ndvi', 'NDVI'], ['ndwi', 'NDWI'], ['mndwi', 'MNDWI'], ['ndbi', 'NDBI'],
  ['savi', 'SAVI'], ['evi', 'EVI'], ['ndsi', 'NDSI'],
];

function todayMinus(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}

function MethodNote({ text }) {
  if (!text) return null;
  return (
    <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, background: 'rgba(126,184,212,0.06)', border: `1px solid ${S.border}`, borderRadius: 4, padding: '8px 10px', marginTop: 10 }}>
      <span style={{ color: S.accent, textTransform: 'uppercase', letterSpacing: 0.5, marginRight: 6 }}>Method</span>{text}
    </div>
  );
}

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '3px 0', borderBottom: `1px solid ${S.border}` }}>
      <span style={{ color: S.text3 }}>{label}</span>
      <span style={{ fontFamily: S.mono, color: S.text2 }}>{value}</span>
    </div>
  );
}

function RasterControls({ mapLayer, downloadUrl, label, onShowOverlay, active, onActivate }) {
  const hasMap = mapLayer?.tile_url && onShowOverlay;
  const hasDownload = !!downloadUrl;
  if (!hasMap && !hasDownload) return null;
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
      {hasMap && (
        <button
          onClick={() => { onShowOverlay(mapLayer.tile_url); onActivate?.(); }}
          style={{
            display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontFamily: S.mono,
            background: active ? 'rgba(201,168,106,0.14)' : 'rgba(126,184,212,0.08)',
            border: `1px solid ${active ? S.gold : S.accent}`, color: active ? S.gold : S.accent,
            padding: '3px 8px', borderRadius: 3, cursor: 'pointer',
          }}
        >
          ▦ {active ? 'Shown on map' : `Show ${label || 'map'} on map`}
        </button>
      )}
      {hasDownload && (
        <a
          href={downloadUrl} target="_blank" rel="noopener noreferrer"
          style={{
            display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontFamily: S.mono,
            background: 'rgba(126,184,212,0.08)', border: `1px solid ${S.border2}`, color: S.text2,
            padding: '3px 8px', borderRadius: 3, textDecoration: 'none',
          }}
          title="Downloads the raw raster as a GeoTIFF — open in QGIS, SNAP, or any GIS/remote-sensing tool to verify independently."
        >
          ⬇ GeoTIFF
        </a>
      )}
    </div>
  );
}

function ResultView({ tool, result, onShowOverlay, activeLayerId, setActiveLayerId }) {
  if (tool === 'spectral_indices') {
    return (
      <div>
        {Object.entries(result.indices).map(([id, d]) => (
          <div key={id} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '8px 10px', marginBottom: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
              <span style={{ fontSize: 12, color: S.gold }}>{d.label}</span>
              <span style={{ fontFamily: S.mono, fontSize: 15, color: S.text, fontWeight: 700 }}>{d.mean}</span>
            </div>
            <div style={{ fontSize: 10, color: S.text3, marginBottom: 3 }}>{d.formula} &middot; {d.citation}</div>
            <div style={{ fontSize: 10.5, color: S.text2, lineHeight: 1.4, marginBottom: 4 }}>{d.interpretation}</div>
            <div style={{ display: 'flex', gap: 12, fontSize: 10, color: S.text3, fontFamily: S.mono }}>
              <span>min {d.min}</span><span>max {d.max}</span><span>σ {d.std_dev}</span>
            </div>
            <RasterControls mapLayer={d.map_layer} downloadUrl={d.download_url} label={id.toUpperCase()} onShowOverlay={onShowOverlay}
              active={activeLayerId === id} onActivate={() => setActiveLayerId?.(id)} />
          </div>
        ))}
        {result.valid_pixel_fraction != null && (
          <div style={{ fontSize: 10, color: S.text3, marginBottom: 6 }}>
            Valid (cloud-free) pixel coverage: {Math.round(result.valid_pixel_fraction * 100)}% of AOI
          </div>
        )}
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'terrain') {
    return (
      <div>
        <StatRow label="Elevation (mean)" value={`${result.elevation_m.mean} m`} />
        <StatRow label="Elevation (min / max)" value={`${result.elevation_m.min} / ${result.elevation_m.max} m`} />
        <StatRow label="Elevation (σ)" value={`${result.elevation_m.std_dev} m`} />
        <StatRow label="Slope (mean)" value={`${result.slope_degrees.mean}°`} />
        <StatRow label="Slope (min / max)" value={`${result.slope_degrees.min}° / ${result.slope_degrees.max}°`} />
        <StatRow label="Dominant aspect" value={`${result.dominant_aspect.compass} (${result.dominant_aspect.degrees}°)`} />
        <div style={{ fontSize: 10, color: S.text3, marginTop: 8 }}>{result.vertical_datum}</div>
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'lulc') {
    return (
      <div>
        {result.classes.map(c => (
          <div key={c.code} style={{ marginBottom: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 2 }}>
              <span style={{ color: S.text2 }}>{c.label}</span>
              <span style={{ fontFamily: S.mono, color: S.gold }}>{c.pct_of_aoi}%</span>
            </div>
            <div style={{ height: 4, background: S.surface2, borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${c.pct_of_aoi}%`, background: S.accent }} />
            </div>
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 1 }}>{c.area_km2} km²</div>
          </div>
        ))}
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="classification" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'lulc'} onActivate={() => setActiveLayerId?.('lulc')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'snow_cover') {
    return (
      <div>
        <StatRow label="Snow-covered area" value={`${result.snow_covered_km2} km²`} />
        <StatRow label="% of AOI" value={`${result.snow_cover_pct}%`} />
        <StatRow label="NDSI (mean / max / σ)" value={`${result.ndsi_mean} / ${result.ndsi_max} / ${result.ndsi_std_dev}`} />
        <StatRow label="Classification threshold" value={`NDSI > ${result.threshold_used}`} />
        <StatRow label="Scenes used" value={result.scene_count} />
        {result.valid_pixel_fraction != null && (
          <StatRow label="Valid pixel coverage" value={`${Math.round(result.valid_pixel_fraction * 100)}%`} />
        )}
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="snow mask" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'snow_cover'} onActivate={() => setActiveLayerId?.('snow_cover')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'sar_backscatter') {
    return (
      <div>
        <StatRow label="VV backscatter (mean)" value={`${result.vv_db.mean} dB (σ ${result.vv_db.std_dev})`} />
        <StatRow label="VH backscatter (mean)" value={`${result.vh_db.mean} dB (σ ${result.vh_db.std_dev})`} />
        <StatRow label="Radar Vegetation Index" value={result.radar_vegetation_index} />
        <StatRow label="Scenes used" value={result.scene_count} />
        {result.valid_pixel_fraction != null && (
          <StatRow label="Valid pixel coverage" value={`${Math.round(result.valid_pixel_fraction * 100)}%`} />
        )}
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="RVI" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'sar_backscatter'} onActivate={() => setActiveLayerId?.('sar_backscatter')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'change_detection') {
    const upColor = result.delta > 0 ? '#2ecc71' : result.delta < 0 ? '#ff7a45' : S.text3;
    return (
      <div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
          <div style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '8px 10px' }}>
            <div style={{ fontSize: 9.5, color: S.text3, marginBottom: 3 }}>Period 1 ({result.period1.start} → {result.period1.end})</div>
            <div style={{ fontFamily: S.mono, fontSize: 16, color: S.text }}>{result.period1.mean}</div>
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 2 }}>σ {result.period1.std_dev} &middot; {result.period1.scene_count} scene(s)</div>
          </div>
          <div style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '8px 10px' }}>
            <div style={{ fontSize: 9.5, color: S.text3, marginBottom: 3 }}>Period 2 ({result.period2.start} → {result.period2.end})</div>
            <div style={{ fontFamily: S.mono, fontSize: 16, color: S.text }}>{result.period2.mean}</div>
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 2 }}>σ {result.period2.std_dev} &middot; {result.period2.scene_count} scene(s)</div>
          </div>
        </div>
        <StatRow label="Delta (P2 - P1)" value={<span style={{ color: upColor }}>{result.delta > 0 ? '+' : ''}{result.delta}</span>} />
        {result.pct_change != null && <StatRow label="% change" value={`${result.pct_change > 0 ? '+' : ''}${result.pct_change}%`} />}
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="delta" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'change_detection'} onActivate={() => setActiveLayerId?.('change_detection')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'burn_severity') {
    return (
      <div>
        <StatRow label="dNBR (mean)" value={result.dnbr_mean} />
        <StatRow label="dNBR (min / max)" value={`${result.dnbr_min} / ${result.dnbr_max}`} />
        <StatRow label="dNBR (σ)" value={result.dnbr_std_dev} />
        <StatRow label="Overall classification" value={result.overall_classification} />
        <StatRow label="Pre / post fire scenes" value={`${result.pre_fire_scenes} / ${result.post_fire_scenes}`} />
        {Object.keys(result.area_by_severity_km2).length > 0 && (
          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Area by severity class</div>
            {Object.entries(result.area_by_severity_km2).map(([label, km2]) => (
              <StatRow key={label} label={label} value={`${km2} km²`} />
            ))}
          </div>
        )}
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="dNBR severity" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'burn_severity'} onActivate={() => setActiveLayerId?.('burn_severity')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'atmospheric_composition') {
    return (
      <div>
        <StatRow label="Tropospheric NO2" value={result.no2.mean != null ? `${result.no2.mean} ${result.no2.unit} (σ ${result.no2.std_dev})` : 'No data'} />
        <StatRow label="SO2" value={result.so2.mean != null ? `${result.so2.mean} ${result.so2.unit} (σ ${result.so2.std_dev})` : 'No data'} />
        <StatRow label="CO" value={result.co.mean != null ? `${result.co.mean} ${result.co.unit} (σ ${result.co.std_dev})` : 'No data'} />
        <StatRow label="Aerosol Index" value={result.aerosol_index.mean != null ? `${result.aerosol_index.mean} (σ ${result.aerosol_index.std_dev})` : 'No data'} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  return null;
}

export default function SpectraPanel({ apiUrl, drawnAOI, onShowOverlay, onClearOverlay }) {
  const [tool, setTool] = useState('spectral_indices');
  const [startDate, setStartDate] = useState(todayMinus(90));
  const [endDate, setEndDate] = useState(todayMinus(0));
  const [selectedIndices, setSelectedIndices] = useState(INDEX_CHOICES.map(([id]) => id));
  const [changeIndex, setChangeIndex] = useState('ndvi');
  const [period1Start, setPeriod1Start] = useState(todayMinus(395));
  const [period1End, setPeriod1End] = useState(todayMinus(365));
  const [period2Start, setPeriod2Start] = useState(todayMinus(30));
  const [period2End, setPeriod2End] = useState(todayMinus(0));
  const [preStart, setPreStart] = useState(todayMinus(120));
  const [preEnd, setPreEnd] = useState(todayMinus(90));
  const [postStart, setPostStart] = useState(todayMinus(30));
  const [postEnd, setPostEnd] = useState(todayMinus(0));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [activeLayerId, setActiveLayerId] = useState(null);
  const [lastParams, setLastParams] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const pollRef = useRef(null);

  // Clear any map overlay left over from a previous result when the whole
  // panel unmounts (i.e. the user leaves the Spectra tab) — otherwise a
  // classified raster would keep sitting on the map after switching to
  // Analyze/Business/Agri, which has nothing to do with what's showing there.
  useEffect(() => () => { clearInterval(pollRef.current); onClearOverlay?.(); }, []);

  const toggleIndex = (id) => {
    setSelectedIndices(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]);
  };

  const generateReport = useCallback(async () => {
    if (!result || !lastParams) return;
    setReportLoading(true); setError(null);
    try {
      const res = await fetch(`${apiUrl}/api/v1/report/remote-sensing`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...lastParams, result }),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vayu_spectra_${tool}_report.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(`Report generation failed: ${e.message}`);
    } finally {
      setReportLoading(false);
    }
  }, [apiUrl, tool, result, lastParams]);

  const run = useCallback(async () => {
    if (!drawnAOI) { setError('Draw an Area of Interest on the map first (top-right draw tools).'); return; }
    setLoading(true); setError(null); setResult(null);
    const meta = TOOL_META[tool];
    const body = { tool, aoi_geojson: drawnAOI };
    if (meta.needsDates) { body.start_date = startDate; body.end_date = endDate; }
    if (tool === 'spectral_indices') body.indices = selectedIndices;
    if (meta.needsTwoPeriods) {
      body.index = changeIndex;
      body.period1_start = period1Start; body.period1_end = period1End;
      body.period2_start = period2Start; body.period2_end = period2End;
    }
    if (meta.needsPrePost) {
      body.pre_start = preStart; body.pre_end = preEnd;
      body.post_start = postStart; body.post_end = postEnd;
    }
    setLastParams(body);

    try {
      const res = await fetch(`${apiUrl}/api/v1/remote-sensing/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
      const data = await res.json();
      clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const r = await fetch(`${apiUrl}/api/v1/remote-sensing/analyze/${data.request_id}`);
          if (r.status === 422) {
            clearInterval(pollRef.current);
            const e = await r.json().catch(() => ({}));
            setError(e.detail || 'Computation failed.'); setLoading(false);
            return;
          }
          const d = await r.json();
          if (d.status === 'done') {
            clearInterval(pollRef.current);
            setResult(d.result); setLoading(false); setActiveLayerId(null);
          }
        } catch (e) {
          clearInterval(pollRef.current); setError(`Polling error: ${e.message}`); setLoading(false);
        }
      }, POLL_MS);
    } catch (e) {
      setError(`Failed to submit: ${e.message}`); setLoading(false);
    }
  }, [apiUrl, drawnAOI, tool, startDate, endDate, selectedIndices, changeIndex, period1Start, period1End, period2Start, period2End, preStart, preEnd, postStart, postEnd]);

  const meta = TOOL_META[tool];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ fontSize: 13, fontFamily: S.mono, color: S.accent, letterSpacing: 1.5, marginBottom: 4, textTransform: 'uppercase' }}>
        Spectra — Remote Sensing
      </div>
      <div style={{ fontSize: 11, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
        Direct access to spectral indices, terrain, land cover, snow cover, SAR, change detection, burn severity, and atmospheric composition — each result states its dataset, resolution, and formula.
      </div>

      <Field label="Tool">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {Object.entries(TOOL_META).map(([id, m]) => (
            <button key={id} onClick={() => { setTool(id); setResult(null); setError(null); setActiveLayerId(null); onClearOverlay?.(); }} style={{
              background: tool === id ? 'rgba(126,184,212,0.12)' : S.surface2,
              border: `1px solid ${tool === id ? S.accent : S.border}`,
              color: tool === id ? S.accent : S.text2, fontFamily: S.mono, fontSize: 11,
              padding: '6px 10px', borderRadius: 4, cursor: 'pointer',
            }}>
              {m.icon} {m.label}
            </button>
          ))}
        </div>
      </Field>

      {!drawnAOI && (
        <div style={{ fontSize: 11, color: '#e0c23c', background: 'rgba(224,194,60,0.08)', border: '1px solid rgba(224,194,60,0.3)', borderRadius: 4, padding: '8px 10px', marginBottom: 10 }}>
          Draw an Area of Interest on the map (top-right draw tools) before running a tool.
        </div>
      )}

      {tool === 'spectral_indices' && (
        <Field label="Indices to compute">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {INDEX_CHOICES.map(([id, label]) => (
              <label key={id} style={{
                display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, fontFamily: S.mono, cursor: 'pointer',
                background: selectedIndices.includes(id) ? 'rgba(201,168,106,0.1)' : S.surface2,
                border: `1px solid ${selectedIndices.includes(id) ? S.gold : S.border}`, borderRadius: 3, padding: '4px 8px',
                color: selectedIndices.includes(id) ? S.gold : S.text3,
              }}>
                <input type="checkbox" checked={selectedIndices.includes(id)} onChange={() => toggleIndex(id)} style={{ accentColor: S.gold, margin: 0 }} />
                {label}
              </label>
            ))}
          </div>
        </Field>
      )}

      {meta.needsDates && (
        <Field label="Date range">
          <div style={{ display: 'flex', gap: 8 }}>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
          </div>
        </Field>
      )}

      {meta.needsTwoPeriods && (
        <>
          <Field label="Index to compare">
            <select value={changeIndex} onChange={e => setChangeIndex(e.target.value)}
              style={{ width: '100%', background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }}>
              {INDEX_CHOICES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select>
          </Field>
          <Field label="Period 1 (baseline)">
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="date" value={period1Start} onChange={e => setPeriod1Start(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
              <input type="date" value={period1End} onChange={e => setPeriod1End(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
            </div>
          </Field>
          <Field label="Period 2 (comparison)">
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="date" value={period2Start} onChange={e => setPeriod2Start(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
              <input type="date" value={period2End} onChange={e => setPeriod2End(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
            </div>
          </Field>
        </>
      )}

      {meta.needsPrePost && (
        <>
          <Field label="Pre-fire period">
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="date" value={preStart} onChange={e => setPreStart(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
              <input type="date" value={preEnd} onChange={e => setPreEnd(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
            </div>
          </Field>
          <Field label="Post-fire period">
            <div style={{ display: 'flex', gap: 8 }}>
              <input type="date" value={postStart} onChange={e => setPostStart(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
              <input type="date" value={postEnd} onChange={e => setPostEnd(e.target.value)}
                style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
            </div>
          </Field>
        </>
      )}

      <button onClick={run} disabled={loading || (tool === 'spectral_indices' && selectedIndices.length === 0)} style={{
        background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontFamily: S.mono,
        fontSize: 12, letterSpacing: 1, textTransform: 'uppercase', padding: '9px 16px', borderRadius: 4, cursor: 'pointer', marginTop: 4,
      }}>
        {loading ? 'Computing...' : 'Run'}
      </button>

      {error && (
        <div style={{ fontSize: 11.5, color: '#ff8080', background: 'rgba(255,59,59,0.08)', border: '1px solid rgba(255,59,59,0.3)', borderRadius: 4, padding: '8px 10px', marginTop: 10 }}>
          {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 14, borderTop: `1px solid ${S.border}`, paddingTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, marginBottom: 8 }}>
            <button
              onClick={generateReport}
              disabled={reportLoading}
              title="Generates a full scientific PDF report: cover page, study area, imagery, results table, methodology, glossary, and limitations."
              style={{
                display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontFamily: S.mono,
                background: reportLoading ? 'rgba(201,168,106,0.08)' : 'rgba(201,168,106,0.14)',
                border: `1px solid ${S.gold}`, color: S.gold,
                padding: '3px 8px', borderRadius: 3, cursor: reportLoading ? 'default' : 'pointer',
                opacity: reportLoading ? 0.6 : 1,
              }}
            >
              {reportLoading ? '⟳ Generating…' : '▤ Generate Report'}
            </button>
            <button
              onClick={() => downloadResultCsv(tool, result)}
              title="Downloads the AOI-summary numbers (means, std devs, areas, etc.) as CSV — for per-pixel raw data, use the GeoTIFF download on the map layer below."
              style={{
                display: 'flex', alignItems: 'center', gap: 5, fontSize: 10.5, fontFamily: S.mono,
                background: 'rgba(126,184,212,0.08)', border: `1px solid ${S.border2}`, color: S.text2,
                padding: '3px 8px', borderRadius: 3, cursor: 'pointer',
              }}
            >
              ⬇ Download CSV
            </button>
          </div>
          <ResultView tool={tool} result={result} onShowOverlay={onShowOverlay} activeLayerId={activeLayerId} setActiveLayerId={setActiveLayerId} />
        </div>
      )}
    </div>
  );
}
