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
  burn_severity: { label: 'Burn Severity', needsDates: false, needsPrePost: true, icon: '▲' },
  atmospheric_composition: { label: 'Atmosphere', needsDates: true, icon: '☁' },
  land_surface_temperature: { label: 'Surface Temp', needsDates: true, icon: '◉' },
  surface_water_dynamics: { label: 'Surface Water', needsDates: false, icon: '≋' },
  supervised_classification: { label: 'ML Classify', needsDates: true, needsTrainingPoints: true, icon: '⊛' },
};

// Matches backend gee_remote_sensing.py's MIN_SAMPLES_PER_CLASS/MIN_CLASSES —
// surfaced here too so the Run button and an inline hint can catch an
// under-specified training set before spending a request on it, rather
// than only finding out from the backend's error message.
const MIN_SAMPLES_PER_CLASS = 4;
const MIN_CLASSES = 2;

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
  if (tool === 'land_surface_temperature') {
    const lst = result.lst_celsius;
    if (!lst || lst.mean == null) {
      return (
        <div>
          <div style={{ fontSize: 12, color: S.text3, padding: '10px 0' }}>
            No cloud-free Landsat 8/9 scene found for this AOI/date range — Landsat's 16-day revisit means a short window can easily miss every pass. Try widening the dates.
          </div>
          <MethodNote text={result.method} />
        </div>
      );
    }
    return (
      <div>
        <StatRow label="Surface temperature (mean)" value={`${lst.mean}°C`} />
        <StatRow label="Min / Max" value={`${lst.min}°C / ${lst.max}°C`} />
        <StatRow label="σ (spatial spread)" value={`${lst.std_dev}°C`} />
        <StatRow label="Source" value={result.source} />
        <StatRow label="Scenes used" value={result.scene_count} />
        {result.valid_pixel_fraction != null && (
          <StatRow label="Valid pixel coverage" value={`${Math.round(result.valid_pixel_fraction * 100)}%`} />
        )}
        <div style={{ fontSize: 10.5, color: S.text3, marginTop: 6, lineHeight: 1.4 }}>
          This is surface (skin/canopy-top) temperature, not 2m air temperature — don't compare directly to a weather station reading.
        </div>
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="surface temperature" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'land_surface_temperature'} onActivate={() => setActiveLayerId?.('land_surface_temperature')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'surface_water_dynamics') {
    return (
      <div>
        <StatRow label="Permanent water" value={`${result.permanent_water_km2} km²`} />
        <StatRow label="Seasonal water" value={`${result.seasonal_water_km2} km²`} />
        <StatRow label="Max water ever observed (1984–2021)" value={`${result.max_ever_water_km2} km²`} />
        <StatRow label="Occurrence (mean / σ, ever-wet area)" value={`${result.occurrence_pct.mean}% / ${result.occurrence_pct.std_dev}%`} />
        <StatRow label="Avg. months/year water present" value={result.avg_months_per_year_water_present} />
        <StatRow label="Permanent threshold" value={result.permanent_threshold_used} />
        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="water occurrence" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'surface_water_dynamics'} onActivate={() => setActiveLayerId?.('surface_water_dynamics')} />
        <MethodNote text={result.method} />
      </div>
    );
  }
  if (tool === 'supervised_classification') {
    const acc = result.accuracy;
    return (
      <div>
        {result.classes.map(c => (
          <div key={c.class_id} style={{ marginBottom: 6 }}>
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

        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Accuracy</div>
          <StatRow label="Test accuracy (held-out points)" value={acc.test_accuracy != null ? `${Math.round(acc.test_accuracy * 100)}% (κ ${acc.test_kappa})` : 'n/a — no points held out'} />
          <StatRow label="Training (resubstitution) accuracy" value={`${Math.round(acc.training_resubstitution_accuracy * 100)}% (κ ${acc.training_resubstitution_kappa})`} />
          <StatRow label="Training / test points used" value={`${result.training.train_points} / ${result.training.test_points}`} />
          {result.training.points_dropped_cloud_masked > 0 && (
            <StatRow label="Points dropped (cloud-masked pixel)" value={result.training.points_dropped_cloud_masked} />
          )}
        </div>
        <div style={{ fontSize: 10.5, color: S.text3, marginTop: 6, lineHeight: 1.4 }}>
          Test accuracy is the honest number — scored on points the model never trained on. Training accuracy is always optimistic; it's shown for transparency, not as the headline figure.
        </div>

        <RasterControls mapLayer={result.map_layer} downloadUrl={result.download_url} label="classification" onShowOverlay={onShowOverlay}
          active={activeLayerId === 'supervised_classification'} onActivate={() => setActiveLayerId?.('supervised_classification')} />
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

export default function SpectraPanel({ apiUrl, drawnAOI, onShowOverlay, onClearOverlay, onSetPointPickHandler }) {
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
  const [trainingSamples, setTrainingSamples] = useState([]); // supervised_classification: [{lat, lon, class_id, class_label}]
  const [ptLat, setPtLat] = useState('');
  const [ptLon, setPtLon] = useState('');
  const [ptLabel, setPtLabel] = useState('');
  const [numTrees, setNumTrees] = useState(50);
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

  // Class ids are assigned automatically from the typed label — the same
  // label (trimmed, case-insensitive) reuses its existing class_id rather
  // than making the user track numeric ids themselves.
  const addTrainingPoint = () => {
    const lat = parseFloat(ptLat), lon = parseFloat(ptLon);
    const label = ptLabel.trim();
    if (Number.isNaN(lat) || Number.isNaN(lon) || !label) return;
    setTrainingSamples(prev => {
      const existing = prev.find(p => p.class_label.toLowerCase() === label.toLowerCase());
      const class_id = existing ? existing.class_id : (prev.reduce((max, p) => Math.max(max, p.class_id), 0) + 1);
      return [...prev, { lat, lon, class_id, class_label: existing ? existing.class_label : label }];
    });
    setPtLat(''); setPtLon(''); setPtLabel('');
  };

  // Click-on-map alternative to typing lat/lon: the class label field
  // stays as-is (still typed once), but each map click adds a point at
  // the clicked coordinates under whatever label is currently typed —
  // type a class name, click several points for it, change the name,
  // click more. addPointFromMapClick is recreated whenever ptLabel
  // changes so the registered handler (below) is never reading a stale
  // label, without needing a ref.
  const addPointFromMapClick = useCallback((lat, lon) => {
    const label = ptLabel.trim();
    if (!label) return; // no class typed yet — nothing to label the point with
    setTrainingSamples(prev => {
      const existing = prev.find(p => p.class_label.toLowerCase() === label.toLowerCase());
      const class_id = existing ? existing.class_id : (prev.reduce((max, p) => Math.max(max, p.class_id), 0) + 1);
      return [...prev, { lat, lon, class_id, class_label: existing ? existing.class_label : label }];
    });
  }, [ptLabel]);

  const [pickingPoints, setPickingPoints] = useState(false);
  // Registers/re-registers addPointFromMapClick with App.jsx's shared
  // point-pick slot whenever picking is on (so a ptLabel change is
  // reflected immediately), and always clears it on toggle-off or
  // unmount (switching tools/tabs) — an orphaned handler would keep
  // adding points to a tool the user has since left.
  useEffect(() => {
    if (!pickingPoints || !onSetPointPickHandler) return;
    onSetPointPickHandler(() => addPointFromMapClick);
    return () => onSetPointPickHandler(null);
  }, [pickingPoints, addPointFromMapClick, onSetPointPickHandler]);
  // Also stop picking if the user switches away from the ML tool — no
  // reason to keep the map in crosshair mode for an unrelated tool.
  useEffect(() => {
    if (tool !== 'supervised_classification' && pickingPoints) setPickingPoints(false);
  }, [tool, pickingPoints]);

  const removeTrainingPoint = (idx) => {
    setTrainingSamples(prev => prev.filter((_, i) => i !== idx));
  };

  const trainingByClass = trainingSamples.reduce((acc, p, idx) => {
    (acc[p.class_id] ||= { label: p.class_label, points: [] }).points.push({ ...p, idx });
    return acc;
  }, {});
  const trainingClassCount = Object.keys(trainingByClass).length;
  const trainingReady = trainingClassCount >= MIN_CLASSES && Object.values(trainingByClass).every(c => c.points.length >= MIN_SAMPLES_PER_CLASS);

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
    if (meta.needsTrainingPoints) {
      body.training_samples = trainingSamples;
      body.num_trees = numTrees;
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
  }, [apiUrl, drawnAOI, tool, startDate, endDate, selectedIndices, changeIndex, period1Start, period1End, period2Start, period2End, preStart, preEnd, postStart, postEnd, trainingSamples, numTrees]);

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

      {meta.needsTrainingPoints && (
        <>
          <Field label="Training points">
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.4, marginBottom: 8 }}>
              Type a class name below, then click points on the map for it — or type lat/lon directly. Needs at least {MIN_CLASSES} classes with {MIN_SAMPLES_PER_CLASS}+ points each — a share of each class's points is held out to score real accuracy, not just resubstitution.
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 8, alignItems: 'center' }}>
              <input type="text" placeholder="Class label (e.g. water)" value={ptLabel} onChange={e => setPtLabel(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addTrainingPoint()}
                style={{ flex: 1, minWidth: 0, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
              <button onClick={() => setPickingPoints(p => !p)} disabled={!ptLabel.trim() && !pickingPoints} style={{
                background: pickingPoints ? 'rgba(126,184,212,0.25)' : 'rgba(126,184,212,0.12)',
                border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5, fontFamily: S.mono,
                padding: '6px 10px', borderRadius: 3, cursor: 'pointer', flexShrink: 0, whiteSpace: 'nowrap',
              }}>
                {pickingPoints ? '● click map…' : '⊕ click map to add'}
              </button>
            </div>
            {pickingPoints && (
              <div style={{ fontSize: 10.5, color: S.accent, marginBottom: 8 }}>
                Click anywhere on the map to add a point labeled "{ptLabel.trim() || '…'}". Change the class name above to switch classes, or click the button again to stop.
              </div>
            )}
            <div style={{ fontSize: 10, color: S.text3, marginBottom: 4 }}>...or type coordinates directly:</div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <input type="number" step="any" placeholder="Lat" value={ptLat} onChange={e => setPtLat(e.target.value)}
                style={{ width: 70, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 6px', borderRadius: 3 }} />
              <input type="number" step="any" placeholder="Lon" value={ptLon} onChange={e => setPtLon(e.target.value)}
                style={{ width: 70, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 6px', borderRadius: 3 }} />
              <button onClick={addTrainingPoint} disabled={!ptLat || !ptLon || !ptLabel.trim()} style={{
                background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5,
                fontFamily: S.mono, padding: '6px 10px', borderRadius: 3, cursor: 'pointer', flexShrink: 0,
              }}>
                + Add
              </button>
            </div>

            {trainingClassCount > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {Object.entries(trainingByClass).map(([cid, c]) => (
                  <div key={cid} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '6px 8px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                      <span style={{ color: c.points.length >= MIN_SAMPLES_PER_CLASS ? S.gold : '#e0c23c' }}>{c.label}</span>
                      <span style={{ color: S.text3, fontFamily: S.mono }}>{c.points.length} pt{c.points.length !== 1 ? 's' : ''} {c.points.length < MIN_SAMPLES_PER_CLASS ? `(need ${MIN_SAMPLES_PER_CLASS}+)` : ''}</span>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {c.points.map(p => (
                        <span key={p.idx} onClick={() => removeTrainingPoint(p.idx)} title="Click to remove" style={{
                          fontSize: 9.5, fontFamily: S.mono, color: S.text3, background: S.surface, border: `1px solid ${S.border}`,
                          borderRadius: 3, padding: '2px 5px', cursor: 'pointer',
                        }}>
                          {p.lat.toFixed(3)},{p.lon.toFixed(3)} ✕
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {trainingClassCount > 0 && trainingClassCount < MIN_CLASSES && (
              <div style={{ fontSize: 10.5, color: '#e0c23c', marginTop: 6 }}>Add at least {MIN_CLASSES} distinct classes.</div>
            )}
          </Field>
          <Field label="Random Forest trees">
            <input type="number" min="10" max="500" value={numTrees} onChange={e => setNumTrees(Number(e.target.value) || 50)}
              style={{ width: 100, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
          </Field>
        </>
      )}

      <button onClick={run} disabled={loading || (tool === 'spectral_indices' && selectedIndices.length === 0) || (meta.needsTrainingPoints && !trainingReady)} style={{
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
