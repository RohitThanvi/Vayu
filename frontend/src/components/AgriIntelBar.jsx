/**
 * AgriIntelBar.jsx
 * A full-width, collapsible bottom panel for the Agriculture tier — the
 * same pattern BusinessIntelBar and SpectraIntelBar already established
 * (wide horizontal space for genuinely elaborate content instead of
 * squeezing it into the 330px sidebar AgriPanel occupies). AgriPanel
 * keeps the compact, decision-first core (Score / Watchlist / Overview /
 * Mandi); everything below is exploratory analysis that benefits from
 * room — same split Business made between its sidebar and its bar.
 *
 * Five sub-views:
 *   - Groundwater: GRACE trend for the AOI, chart + trend read.
 *   - Analysis: NDVI + groundwater trend charts side by side.
 *   - Crop Stage: phenology read (green-up/peak/senescence) from the
 *     AOI's own NDVI curve.
 *   - Irrigation: irrigate now/monitor/hold off advisory.
 *   - ML Extent: Random Forest crop-extent classification — same engine
 *     as Spectra's ML Classify tool, via the /agri/crop-extent wrapper.
 *     Training points can be added by clicking the map (pointPickActive/
 *     onSetPointPickHandler, threaded from App.jsx) instead of only
 *     typing lat/lon.
 */

import { useState, useCallback, useEffect } from 'react';
import SparkChart from './SparkChart';

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  surface: '#0d1117', surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a',
};

function RunButton({ onClick, disabled, loading, label, loadingLabel }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      width: '100%', background: disabled ? S.surface2 : 'rgba(126,184,212,0.12)',
      border: `1px solid ${disabled ? S.border2 : S.accent}`, color: disabled ? S.text3 : S.accent,
      fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase',
      padding: '8px 12px', borderRadius: 4, cursor: disabled ? 'not-allowed' : 'pointer',
    }}>
      {loading ? loadingLabel : label}
    </button>
  );
}

function ErrorNote({ children }) {
  return <div style={{ fontSize: 10.5, color: '#ff8080', marginTop: 8 }}>{children}</div>;
}

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, padding: '5px 0', borderBottom: `1px solid ${S.border}` }}>
      <span style={{ color: S.text3 }}>{label}</span>
      <span style={{ color: S.text2, fontFamily: S.mono }}>{value}</span>
    </div>
  );
}

function ControlsPanel({ children, width = 240 }) {
  return <div style={{ width, flexShrink: 0 }}>{children}</div>;
}

function useAgriFetch(apiUrl, path) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const run = useCallback(async (body) => {
    setLoading(true); setError(null); setResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail || `HTTP ${resp.status}`);
      setResult(await resp.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [apiUrl, path]);

  return { run, loading, error, result };
}

function GroundwaterTab({ apiUrl, drawnAOI }) {
  const { run, loading, error, result } = useAgriFetch(apiUrl, 'groundwater-trend');
  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          GRACE satellite groundwater trend — regional (~300km grid), layered against the drought/moisture read in the risk score.
        </div>
        <RunButton onClick={() => drawnAOI && run({ aoi_geojson: drawnAOI })} disabled={loading || !drawnAOI} loading={loading} label="CHECK TREND" loadingLabel="CHECKING..." />
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </ControlsPanel>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Draw an AOI and check trend.</div>}
        {result?.status === 'ok' && (
          <div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 12 }}>
              <span style={{ fontSize: 20, fontFamily: S.mono, fontWeight: 700, textTransform: 'uppercase', color: result.trend === 'declining' ? '#ff7a45' : result.trend === 'rising' ? '#2ecc71' : S.text }}>{result.trend}</span>
              <span style={{ fontSize: 12, color: S.text3 }}>{result.slope_cm_per_year > 0 ? '+' : ''}{result.slope_cm_per_year} cm/yr</span>
            </div>
            <SparkChart points={result.series} height={180} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(1)} emptyLabel="No GRACE series available." />
            <div style={{ fontSize: 10.5, color: S.text3, marginTop: 8 }}>{result.resolution_note}</div>
          </div>
        )}
        {result && result.status !== 'ok' && <div style={{ fontSize: 12, color: S.text3, marginTop: 20 }}>{result.note}</div>}
      </div>
    </div>
  );
}

function AnalysisTab({ apiUrl, drawnAOI }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [ndvi, setNdvi] = useState(null);
  const [gw, setGw] = useState(null);

  const run = useCallback(async () => {
    if (!drawnAOI) return;
    setLoading(true); setError(null); setNdvi(null); setGw(null);
    try {
      const [ndviResp, gwResp] = await Promise.all([
        fetch(`${apiUrl}/api/v1/agri/ndvi-trend`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ aoi_geojson: drawnAOI, months_back: 12 }),
        }).then(r => r.json()).catch(() => null),
        fetch(`${apiUrl}/api/v1/agri/groundwater-trend`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ aoi_geojson: drawnAOI }),
        }).then(r => r.json()).catch(() => null),
      ]);
      setNdvi(ndviResp); setGw(gwResp);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [apiUrl, drawnAOI]);

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          Historical trend charts for this AOI — NDVI over the last 12 months, and groundwater, side by side.
        </div>
        <RunButton onClick={run} disabled={loading || !drawnAOI} loading={loading} label="LOAD TRENDS" loadingLabel="LOADING..." />
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </ControlsPanel>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', gap: 20 }}>
        {!ndvi && !gw && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Draw an AOI and load trends.</div>}
        {(ndvi || gw) && (
          <>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>NDVI (monthly)</div>
              {ndvi?.points
                ? <SparkChart points={ndvi.points} height={180} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(2)} emptyLabel="No NDVI data for this AOI/period." />
                : <div style={{ fontSize: 11.5, color: S.text3 }}>NDVI trend unavailable.</div>}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Groundwater (GRACE)</div>
              {gw?.status === 'ok'
                ? <SparkChart points={gw.series} height={180} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(1)} emptyLabel="No groundwater series available." />
                : <div style={{ fontSize: 11.5, color: S.text3 }}>{gw?.note || 'Groundwater trend unavailable.'}</div>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function PhenologyTab({ apiUrl, drawnAOI }) {
  const { run, loading, error, result } = useAgriFetch(apiUrl, 'phenology');
  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          Where this AOI sits in its own growing season right now — read from the shape of its last 12 months of NDVI, not a fixed crop calendar.
        </div>
        <RunButton onClick={() => drawnAOI && run({ aoi_geojson: drawnAOI })} disabled={loading || !drawnAOI} loading={loading} label="READ CROP STAGE" loadingLabel="READING..." />
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
        {result?.status === 'ok' && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 2 }}>
            <StatRow label="Green-up" value={result.green_up_date} />
            <StatRow label="Peak greenness" value={result.peak_date} />
            <StatRow label="Senescence" value={result.senescence_date || 'not yet reached'} />
          </div>
        )}
      </ControlsPanel>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Draw an AOI and read its crop stage.</div>}
        {result?.status === 'ok' && (
          <div>
            <div style={{ padding: '10px 14px', background: S.surface2, border: `1px solid ${S.accent}`, borderRadius: 6, marginBottom: 12, display: 'inline-block' }}>
              <span style={{ fontSize: 18, fontFamily: S.mono, fontWeight: 700, color: S.accent, textTransform: 'uppercase' }}>{result.current_stage}</span>
              <span style={{ fontSize: 11, color: S.text3, marginLeft: 10 }}>as of {result.as_of}</span>
            </div>
            <SparkChart points={result.series} height={170} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(2)} emptyLabel="No NDVI series available." />
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8 }}>{result.method}</div>
          </div>
        )}
        {result && result.status !== 'ok' && <div style={{ fontSize: 12, color: S.text3, marginTop: 20 }}>{result.note}</div>}
      </div>
    </div>
  );
}

function IrrigationTab({ apiUrl, drawnAOI }) {
  const { run, loading, error, result } = useAgriFetch(apiUrl, 'irrigation-advisory');
  const rc = result?.recommendation === 'irrigate_now' ? '#ff7a45' : result?.recommendation === 'hold_off' ? '#2ecc71' : '#c9a86a';
  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          Irrigate now / monitor / hold off — SMAP soil moisture cross-checked against CHIRPS rainfall vs. seasonal-normal. A directional regional read, not a field-level prescription.
        </div>
        <RunButton onClick={() => drawnAOI && run({ aoi_geojson: drawnAOI })} disabled={loading || !drawnAOI} loading={loading} label="CHECK IRRIGATION NEED" loadingLabel="CHECKING..." />
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </ControlsPanel>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Draw an AOI and check irrigation need.</div>}
        {result && result.recommendation !== 'insufficient_data' && (
          <div>
            <div style={{ padding: '10px 14px', background: S.surface2, border: `1px solid ${rc}`, borderRadius: 6, marginBottom: 12, display: 'inline-block' }}>
              <span style={{ fontSize: 18, fontFamily: S.mono, fontWeight: 700, color: rc, textTransform: 'uppercase' }}>{result.recommendation.replace('_', ' ')}</span>
            </div>
            <div style={{ fontSize: 13, color: S.text2, lineHeight: 1.6, marginBottom: 12, maxWidth: 560 }}>{result.reason}</div>
            <div style={{ display: 'flex', gap: 24 }}>
              <StatRow label="Dry-stress share of AOI" value={result.soil_moisture?.dry_stress_pct != null ? `${result.soil_moisture.dry_stress_pct}%` : 'n/a'} />
              <StatRow label="Rainfall vs seasonal-normal" value={result.rainfall?.condition || 'n/a'} />
            </div>
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8 }}>{result.disclaimer}</div>
          </div>
        )}
        {result?.recommendation === 'insufficient_data' && <div style={{ fontSize: 12, color: S.text3, marginTop: 20 }}>{result.reason}</div>}
      </div>
    </div>
  );
}

const CROP_MIN_SAMPLES_PER_CLASS = 4;
const CROP_MIN_CLASSES = 2;

function CropExtentTab({ apiUrl, drawnAOI, onSetPointPickHandler }) {
  const [trainingSamples, setTrainingSamples] = useState([]);
  const [ptLat, setPtLat] = useState('');
  const [ptLon, setPtLon] = useState('');
  const [ptLabel, setPtLabel] = useState('');
  const [picking, setPicking] = useState(false);
  const { run, loading, error, result } = useAgriFetch(apiUrl, 'crop-extent');

  const addPoint = () => {
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
  const removePoint = (idx) => setTrainingSamples(prev => prev.filter((_, i) => i !== idx));

  // Click-on-map point picking, same pattern as Spectra's ML Classify —
  // recreated whenever ptLabel changes so the registered handler never
  // reads a stale class name.
  const addPointFromMapClick = useCallback((lat, lon) => {
    const label = ptLabel.trim();
    if (!label) return;
    setTrainingSamples(prev => {
      const existing = prev.find(p => p.class_label.toLowerCase() === label.toLowerCase());
      const class_id = existing ? existing.class_id : (prev.reduce((max, p) => Math.max(max, p.class_id), 0) + 1);
      return [...prev, { lat, lon, class_id, class_label: existing ? existing.class_label : label }];
    });
  }, [ptLabel]);
  useEffect(() => {
    if (!picking || !onSetPointPickHandler) return;
    onSetPointPickHandler(() => addPointFromMapClick);
    return () => onSetPointPickHandler(null);
  }, [picking, addPointFromMapClick, onSetPointPickHandler]);

  const byClass = trainingSamples.reduce((acc, p, idx) => {
    (acc[p.class_id] ||= { label: p.class_label, points: [] }).points.push({ ...p, idx });
    return acc;
  }, {});
  const classCount = Object.keys(byClass).length;
  const ready = classCount >= CROP_MIN_CLASSES && Object.values(byClass).every(c => c.points.length >= CROP_MIN_SAMPLES_PER_CLASS);

  const runClassification = () => {
    if (!drawnAOI || !ready) return;
    const end = new Date().toISOString().slice(0, 10);
    const start = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString().slice(0, 10);
    run({ aoi_geojson: drawnAOI, start_date: start, end_date: end, training_samples: trainingSamples });
  };

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel width={300}>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          How much of this AOI is actually cropped — Random Forest classification (same engine as Spectra's ML Classify). Type a class name, then click points on the map for it, or type coordinates directly. Needs {CROP_MIN_CLASSES}+ classes, {CROP_MIN_SAMPLES_PER_CLASS}+ points each.
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input type="text" placeholder="Class (e.g. cropped)" value={ptLabel} onChange={e => setPtLabel(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addPoint()}
            style={{ flex: 1, minWidth: 0, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
          <button onClick={() => setPicking(p => !p)} disabled={!ptLabel.trim() && !picking} style={{
            background: picking ? 'rgba(126,184,212,0.25)' : 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`,
            color: S.accent, fontSize: 10, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3, cursor: 'pointer', flexShrink: 0, whiteSpace: 'nowrap',
          }}>
            {picking ? '● click map…' : '⊕ pick'}
          </button>
        </div>
        {picking && <div style={{ fontSize: 10, color: S.accent, marginBottom: 8 }}>Click the map to add a point labeled "{ptLabel.trim() || '…'}".</div>}
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input type="number" step="any" placeholder="Lat" value={ptLat} onChange={e => setPtLat(e.target.value)}
            style={{ width: 66, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px' }} />
          <input type="number" step="any" placeholder="Lon" value={ptLon} onChange={e => setPtLon(e.target.value)}
            style={{ width: 66, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px' }} />
          <button onClick={addPoint} disabled={!ptLat || !ptLon || !ptLabel.trim()}
            style={{ flex: 1, background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5, fontFamily: S.mono, padding: '6px', borderRadius: 3, cursor: 'pointer' }}>
            + Add
          </button>
        </div>
        {classCount > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10, maxHeight: 130, overflowY: 'auto' }}>
            {Object.entries(byClass).map(([cid, c]) => (
              <div key={cid} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '5px 7px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, marginBottom: 3 }}>
                  <span style={{ color: c.points.length >= CROP_MIN_SAMPLES_PER_CLASS ? S.accent : '#c9a86a' }}>{c.label}</span>
                  <span style={{ color: S.text3, fontFamily: S.mono }}>{c.points.length}{c.points.length < CROP_MIN_SAMPLES_PER_CLASS ? ` (need ${CROP_MIN_SAMPLES_PER_CLASS}+)` : ''}</span>
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
                  {c.points.map(p => (
                    <span key={p.idx} onClick={() => removePoint(p.idx)} title="Click to remove"
                      style={{ fontSize: 9, fontFamily: S.mono, color: S.text3, background: S.surface, border: `1px solid ${S.border}`, borderRadius: 3, padding: '1px 4px', cursor: 'pointer' }}>
                      {p.lat.toFixed(3)},{p.lon.toFixed(3)} ✕
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <RunButton onClick={runClassification} disabled={!drawnAOI || !ready || loading} loading={loading} label="RUN CLASSIFICATION" loadingLabel="CLASSIFYING..." />
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </ControlsPanel>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Label training points, then run classification.</div>}
        {result && (
          <div>
            {result.classes.map(c => (
              <div key={c.class_id} style={{ marginBottom: 8, maxWidth: 420 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 2 }}>
                  <span style={{ color: S.text2 }}>{c.label}</span>
                  <span style={{ fontFamily: S.mono, color: S.gold }}>{c.pct_of_aoi}% ({c.area_km2} km²)</span>
                </div>
                <div style={{ height: 5, background: S.surface2, borderRadius: 2, overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${c.pct_of_aoi}%`, background: S.accent }} />
                </div>
              </div>
            ))}
            <div style={{ marginTop: 12, maxWidth: 420 }}>
              <StatRow label="Test accuracy (held-out points)" value={result.accuracy?.test_accuracy != null ? `${Math.round(result.accuracy.test_accuracy * 100)}%` : 'n/a'} />
              <StatRow label="Training / test points used" value={`${result.training.train_points} / ${result.training.test_points}`} />
            </div>
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8, maxWidth: 560 }}>{result.accuracy?.note}</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgriIntelBar({ apiUrl, drawnAOI, pointPickActive, onSetPointPickHandler }) {
  const [open, setOpen] = useState(true);
  const [view, setView] = useState('groundwater'); // 'groundwater' | 'analysis' | 'phenology' | 'irrigation' | 'cropextent'

  // Stop any active point-picking if the user collapses the bar or
  // switches away from the ML Extent tab — no reason to keep the map in
  // crosshair mode for a tab that's no longer visible.
  useEffect(() => {
    if ((!open || view !== 'cropextent') && pointPickActive) onSetPointPickHandler?.(null);
  }, [open, view]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ flexShrink: 0, background: S.surface, borderTop: `1px solid ${S.border}`, display: 'flex', flexDirection: 'column' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '7px 16px', background: 'none', border: 'none',
        borderBottom: open ? `1px solid ${S.border}` : 'none', cursor: 'pointer', width: '100%', textAlign: 'left',
      }}>
        <span style={{ fontSize: 11, fontFamily: S.mono, color: S.accent, letterSpacing: 1.5, textTransform: 'uppercase' }}>
          Agri Analysis
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: S.text3, fontFamily: S.mono }}>{open ? '▾ collapse' : '▸ expand'}</span>
      </button>

      {open && (
        <div style={{ display: 'flex', gap: 2, padding: '6px 16px 0', borderBottom: `1px solid ${S.border}` }}>
          {[['groundwater', 'Groundwater'], ['analysis', 'Analysis'], ['phenology', 'Crop Stage'], ['irrigation', 'Irrigation'], ['cropextent', 'ML Extent']].map(([id, label]) => (
            <button key={id} onClick={() => setView(id)} style={{
              background: 'none', border: 'none', borderBottom: view === id ? `2px solid ${S.accent}` : '2px solid transparent',
              color: view === id ? S.accent : S.text3, fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase',
              padding: '6px 12px', cursor: 'pointer', marginBottom: -1,
            }}>
              {label}
            </button>
          ))}
        </div>
      )}

      {open && (
        <div style={{ height: 320, overflowY: 'auto', padding: '14px 16px' }}>
          {view === 'groundwater' && <GroundwaterTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'analysis' && <AnalysisTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'phenology' && <PhenologyTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'irrigation' && <IrrigationTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'cropextent' && <CropExtentTab apiUrl={apiUrl} drawnAOI={drawnAOI} pointPickActive={pointPickActive} onSetPointPickHandler={onSetPointPickHandler} />}
        </div>
      )}
    </div>
  );
}
