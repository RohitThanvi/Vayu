/**
 * SpectraIntelBar.jsx
 * A full-width, collapsible bottom panel for the Remote Sensing tier —
 * the same pattern BusinessIntelBar established for the Business tier
 * (wide horizontal space for genuinely elaborate content instead of
 * squeezing it into the 330px sidebar SpectraPanel already occupies).
 *
 * Three sub-views:
 *   - Time Series: charts a spectral index across monthly/quarterly
 *     sub-periods for the drawn AOI (SpectraPanel's tools each give one
 *     value for a date range; this shows the trend across many).
 *   - Change Detection: a roomier, side-by-side version of the same
 *     tool available in the sidebar — full period labels and a visual
 *     bar comparison instead of a cramped two-line summary.
 *   - Reference: a dataset/method catalog for every tool in Spectra —
 *     citations, resolutions, formulas, all in one place, since a
 *     research-grade tool should make its methodology easy to audit
 *     without having to run a query first.
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import SparkChart from './SparkChart';

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  surface: '#0d1117', surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a',
};

const POLL_MS = 2000;

const INDEX_CHOICES = [
  ['ndvi', 'NDVI — Vegetation'], ['ndwi', 'NDWI — Open water'], ['mndwi', 'MNDWI — Water (urban-safe)'],
  ['ndbi', 'NDBI — Built-up'], ['savi', 'SAVI — Soil-adjusted vegetation'], ['evi', 'EVI — Enhanced vegetation'],
  ['ndsi', 'NDSI — Snow/ice'],
];

const REFERENCE_DATA = [
  { tool: 'Spectral Indices', dataset: 'Sentinel-2 SR Harmonized', resolution: '10-20m', note: 'NDVI, NDWI, MNDWI, NDBI, SAVI, EVI, NDSI — cloud-masked median composite. Citations: Rouse 1974, McFeeters 1996, Xu 2006, Zha et al. 2003, Huete 1988/2002, Hall et al. 1995.' },
  { tool: 'Terrain Analysis', dataset: 'Copernicus DEM GLO-30', resolution: '30m', note: 'TanDEM-X-derived DSM (includes buildings/vegetation, not bare-earth). EGM2008 vertical datum. Aspect averaged circularly.' },
  { tool: 'Land Cover', dataset: 'ESA WorldCover v200', resolution: '10m, 2021', note: '11-class classification, 76.7% overall validated accuracy. Zanaga et al. 2022.' },
  { tool: 'Snow Cover', dataset: 'Sentinel-2 SR (NDSI)', resolution: '20m', note: 'NDSI > 0.4 threshold (Hall et al. 1995), same threshold as MODIS\'s operational snow product, at far finer resolution.' },
  { tool: 'SAR Backscatter', dataset: 'Sentinel-1 GRD (IW, dual-pol)', resolution: '20m', note: 'All-weather, day/night. RVI computed on linear backscatter power, not raw dB values.' },
  { tool: 'Change Detection', dataset: 'Sentinel-2 SR', resolution: '20m', note: 'Any of the 7 indices, diffed between two independently-composited periods.' },
  { tool: 'Burn Severity', dataset: 'Sentinel-2 SR (dNBR)', resolution: '20m', note: 'USGS FIREMON standard (Key & Benson 2006). NBR = (B8-B12)/(B8+B12).' },
  { tool: 'Atmospheric Composition', dataset: 'Sentinel-5P / TROPOMI OFFL L3', resolution: '~1.1km', note: 'NO2, SO2, CO column density + aerosol index. Column densities, not ground-level concentrations.' },
];

function useJobPoll(apiUrl) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const pollRef = useRef(null);
  useEffect(() => () => clearInterval(pollRef.current), []);

  const submit = useCallback(async (body) => {
    setLoading(true); setError(null); setResult(null);
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
          if (d.status === 'done') { clearInterval(pollRef.current); setResult(d.result); setLoading(false); }
        } catch (e) {
          clearInterval(pollRef.current); setError(`Polling error: ${e.message}`); setLoading(false);
        }
      }, POLL_MS);
    } catch (e) {
      setError(`Failed to submit: ${e.message}`); setLoading(false);
    }
  }, [apiUrl]);

  return { submit, loading, error, result };
}

function todayMinus(days) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function TimeSeriesTab({ apiUrl, drawnAOI }) {
  const [index, setIndex] = useState('ndvi');
  const [interval, setIntervalVal] = useState('month');
  const [startDate, setStartDate] = useState(todayMinus(365));
  const [endDate, setEndDate] = useState(todayMinus(0));
  const { submit, loading, error, result } = useJobPoll(apiUrl);

  const run = () => {
    if (!drawnAOI) return;
    submit({ tool: 'index_time_series', aoi_geojson: drawnAOI, index, start_date: startDate, end_date: endDate, interval });
  };

  const points = (result?.points || []).map(p => ({ date: p.date, value: p.value }));

  return (
    <div style={{ display: 'flex', gap: 16, height: '100%' }}>
      <div style={{ width: 220, flexShrink: 0 }}>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Index</div>
        <select value={index} onChange={e => setIndex(e.target.value)}
          style={{ width: '100%', background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3, marginBottom: 10 }}>
          {INDEX_CHOICES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Interval</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>
          {[['month', 'Monthly'], ['quarter', 'Quarterly']].map(([id, label]) => (
            <button key={id} onClick={() => setIntervalVal(id)} style={{
              flex: 1, background: interval === id ? 'rgba(126,184,212,0.12)' : S.surface2,
              border: `1px solid ${interval === id ? S.accent : S.border}`, color: interval === id ? S.accent : S.text3,
              fontFamily: S.mono, fontSize: 10.5, padding: '5px 6px', borderRadius: 3, cursor: 'pointer',
            }}>{label}</button>
          ))}
        </div>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Range</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
            style={{ background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
            style={{ background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
        </div>
        <button onClick={run} disabled={loading || !drawnAOI} style={{
          width: '100%', background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent,
          fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase', padding: '8px 12px', borderRadius: 4, cursor: 'pointer',
        }}>
          {loading ? 'Computing...' : 'Plot'}
        </button>
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <div style={{ fontSize: 10.5, color: '#ff8080', marginTop: 8 }}>{error}</div>}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Pick an index and range, then Plot.</div>}
        {result && (
          <>
            <SparkChart
              points={points} height={200}
              formatX={(d) => new Date(d).toLocaleDateString(undefined, { month: 'short', year: '2-digit' })}
              formatY={(v) => v.toFixed(3)}
              emptyLabel="No cloud-free scenes across this range — try widening it."
            />
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8 }}>{result.method}</div>
          </>
        )}
      </div>
    </div>
  );
}

function ChangeDetectionTab({ apiUrl, drawnAOI }) {
  const [index, setIndex] = useState('ndvi');
  const [p1s, setP1s] = useState(todayMinus(395));
  const [p1e, setP1e] = useState(todayMinus(365));
  const [p2s, setP2s] = useState(todayMinus(30));
  const [p2e, setP2e] = useState(todayMinus(0));
  const { submit, loading, error, result } = useJobPoll(apiUrl);

  const run = () => {
    if (!drawnAOI) return;
    submit({ tool: 'change_detection', aoi_geojson: drawnAOI, index, period1_start: p1s, period1_end: p1e, period2_start: p2s, period2_end: p2e });
  };

  const dateInput = (val, setVal) => (
    <input type="date" value={val} onChange={e => setVal(e.target.value)}
      style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }} />
  );

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <div style={{ width: 260, flexShrink: 0 }}>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Index</div>
        <select value={index} onChange={e => setIndex(e.target.value)}
          style={{ width: '100%', background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3, marginBottom: 10 }}>
          {INDEX_CHOICES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Period 1 (baseline)</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>{dateInput(p1s, setP1s)}{dateInput(p1e, setP1e)}</div>
        <div style={{ fontSize: 10.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Period 2 (comparison)</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 10 }}>{dateInput(p2s, setP2s)}{dateInput(p2e, setP2e)}</div>
        <button onClick={run} disabled={loading || !drawnAOI} style={{
          width: '100%', background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent,
          fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase', padding: '8px 12px', borderRadius: 4, cursor: 'pointer',
        }}>
          {loading ? 'Computing...' : 'Compare'}
        </button>
        {!drawnAOI && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Draw an AOI on the map first.</div>}
        {error && <div style={{ fontSize: 10.5, color: '#ff8080', marginTop: 8 }}>{error}</div>}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Pick two periods and Compare.</div>}
        {result && (
          <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
              {[result.period1, result.period2].map((p, i) => (
                <div key={i} style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 6, padding: '12px 14px' }}>
                  <div style={{ fontSize: 10, color: S.text3, marginBottom: 4 }}>{i === 0 ? 'Period 1' : 'Period 2'}: {p.start} to {p.end}</div>
                  <div style={{ fontFamily: S.mono, fontSize: 24, color: S.text, fontWeight: 700 }}>{p.mean}</div>
                  <div style={{ fontSize: 10, color: S.text3, marginTop: 2 }}>{p.scene_count} scene(s)</div>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 20, marginBottom: 10 }}>
              <div>
                <span style={{ fontSize: 10.5, color: S.text3 }}>Delta: </span>
                <span style={{ fontFamily: S.mono, fontSize: 14, color: result.delta > 0 ? '#2ecc71' : result.delta < 0 ? '#ff7a45' : S.text3 }}>
                  {result.delta > 0 ? '+' : ''}{result.delta}
                </span>
              </div>
              {result.pct_change != null && (
                <div>
                  <span style={{ fontSize: 10.5, color: S.text3 }}>% change: </span>
                  <span style={{ fontFamily: S.mono, fontSize: 14, color: S.gold }}>{result.pct_change > 0 ? '+' : ''}{result.pct_change}%</span>
                </div>
              )}
            </div>
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5 }}>{result.method}</div>
          </div>
        )}
      </div>
    </div>
  );
}

function ReferenceTab() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
      {REFERENCE_DATA.map(r => (
        <div key={r.tool} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 6, padding: '10px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 3 }}>
            <span style={{ fontSize: 12, color: S.gold }}>{r.tool}</span>
            <span style={{ fontSize: 10, fontFamily: S.mono, color: S.accent }}>{r.resolution}</span>
          </div>
          <div style={{ fontSize: 10.5, color: S.text2, marginBottom: 4 }}>{r.dataset}</div>
          <div style={{ fontSize: 10, color: S.text3, lineHeight: 1.4 }}>{r.note}</div>
        </div>
      ))}
    </div>
  );
}

export default function SpectraIntelBar({ apiUrl, drawnAOI }) {
  const [open, setOpen] = useState(true);
  const [view, setView] = useState('timeseries'); // 'timeseries' | 'change' | 'reference'

  return (
    <div style={{ flexShrink: 0, background: S.surface, borderTop: `1px solid ${S.border}`, display: 'flex', flexDirection: 'column' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '7px 16px', background: 'none', border: 'none',
        borderBottom: open ? `1px solid ${S.border}` : 'none', cursor: 'pointer', width: '100%', textAlign: 'left',
      }}>
        <span style={{ fontSize: 11, fontFamily: S.mono, color: S.accent, letterSpacing: 1.5, textTransform: 'uppercase' }}>
          Spectra Analysis
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: S.text3, fontFamily: S.mono }}>{open ? '▾ collapse' : '▸ expand'}</span>
      </button>

      {open && (
        <div style={{ display: 'flex', gap: 2, padding: '6px 16px 0', borderBottom: `1px solid ${S.border}` }}>
          {[['timeseries', 'Time Series'], ['change', 'Change Detection'], ['reference', 'Reference']].map(([id, label]) => (
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
        <div style={{ height: 300, overflowY: 'auto', padding: '14px 16px' }}>
          {view === 'timeseries' && <TimeSeriesTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'change' && <ChangeDetectionTab apiUrl={apiUrl} drawnAOI={drawnAOI} />}
          {view === 'reference' && <ReferenceTab />}
        </div>
      )}
    </div>
  );
}
