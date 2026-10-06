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
 * Six sub-views:
 *   - Groundwater: GRACE trend for the AOI, chart + trend read.
 *   - Analysis: NDVI + groundwater trend charts side by side.
 *   - Crop Stage: phenology read (green-up/peak/senescence) from the
 *     AOI's own NDVI curve.
 *   - Irrigation: irrigate now/monitor/hold off advisory.
 *   - Crops: which crops suit a point/AOI (OpenLandMap soil + CHIRPS/ERA5
 *     climate vs FAO EcoCrop ranges), farmer soil-test overrides, and a
 *     mandi-based revenue estimate — via /agri/crop-suitability.
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

const CROP_RATING_COLOR = { high: '#2ecc71', moderate: '#c9a86a', low: '#ff9a45', very_low: '#ff7a45', unsuitable: '#ff5a5a' };
// Backward compatible: older API responses only have `rating`; newer ones add `category` (very_low) and an explanation built
// from the factors that actually reduced the score.
const SEASON_NAME = { kharif: 'Kharif', rabi: 'Rabi', zaid: 'Summer', perennial: 'Year-round' };
const cropCategory = (c) => c.category ?? c.rating;
const cropLimitText = (c) => {
  if (c.limiting_factors === undefined) return `limited by ${c.limiting_label.toLowerCase()}`;           // old API
  if (c.limiting_factor === 'none') return 'no significant limitation';
  return `${c.category === 'unsuitable' ? 'hard limit' : 'main limitation'}: ${c.limiting_label.toLowerCase()}`;
};
const CROP_TEXTURES = ['Clay', 'Silty clay', 'Sandy clay', 'Clay loam', 'Silty clay loam', 'Sandy clay loam', 'Loam', 'Silt loam', 'Sandy loam', 'Silt', 'Loamy sand', 'Sand'];
const inputStyle = { width: '100%', boxSizing: 'border-box', background: S.surface2, border: `1px solid ${S.border2}`, color: S.text, fontFamily: S.mono, fontSize: 11.5, padding: '5px 7px', borderRadius: 4 };

function CropSuitabilityTab({ apiUrl, drawnAOI, onSetPointPickHandler }) {
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [picking, setPicking] = useState(false);
  const [irrigation, setIrrigation] = useState('auto');   // auto = infer from the area's irrigated-cropland share (GFSAD1000) | yes | no
  const [season, setSeason] = useState('best');   // best | kharif | rabi | zaid
  const [ph, setPh] = useState('');
  const [oc, setOc] = useState('');
  const [texture, setTexture] = useState('');
  const [state, setState] = useState('');
  const [open, setOpen] = useState(null);
  const { run, loading, error, result } = useAgriFetch(apiUrl, 'crop-suitability');

  const onMapClick = useCallback((la, lo) => { setLat(la.toFixed(5)); setLon(lo.toFixed(5)); setPicking(false); }, []);
  useEffect(() => {
    if (!picking || !onSetPointPickHandler) return;
    onSetPointPickHandler(() => onMapClick);
    return () => onSetPointPickHandler(null);
  }, [picking, onMapClick, onSetPointPickHandler]);

  const hasPoint = lat !== '' && lon !== '' && !Number.isNaN(parseFloat(lat)) && !Number.isNaN(parseFloat(lon));
  const canRun = hasPoint || !!drawnAOI;

  const submit = () => {
    const overrides = {};
    if (ph !== '' && !Number.isNaN(parseFloat(ph))) overrides.ph = parseFloat(ph);
    if (oc !== '' && !Number.isNaN(parseFloat(oc))) overrides.organic_carbon_gkg = parseFloat(oc);
    if (texture) overrides.texture = texture;
    const body = { irrigation_available: irrigation === 'auto' ? null : irrigation === 'yes', season, state: state.trim() || undefined, soil_overrides: Object.keys(overrides).length ? overrides : undefined };
    if (hasPoint) { body.lat = parseFloat(lat); body.lon = parseFloat(lon); } else { body.aoi_geojson = drawnAOI; }
    run(body);
  };

  const p = result?.profile;
  const fmtRs = v => `₹${Number(v).toLocaleString('en-IN')}`;
  const notGrown = result?.not_grown_in_season || [];

  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <ControlsPanel width={260}>
        <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginBottom: 10 }}>
          Which crops suit a location — modeled soil (250m) + climate scored against FAO EcoCrop ranges. Tap the map, type a location, or draw an AOI.
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <input style={inputStyle} placeholder="lat" value={lat} onChange={e => setLat(e.target.value)} />
          <input style={inputStyle} placeholder="lon" value={lon} onChange={e => setLon(e.target.value)} />
        </div>
        <button onClick={() => setPicking(v => !v)} style={{ width: '100%', marginBottom: 8, background: picking ? 'rgba(126,184,212,0.2)' : 'none', border: `1px solid ${S.border2}`, color: picking ? S.accent : S.text3, fontFamily: S.mono, fontSize: 10.5, padding: '5px 8px', borderRadius: 4, cursor: 'pointer' }}>
          {picking ? 'CLICK THE MAP…' : 'PICK POINT ON MAP'}
        </button>
        <div style={{ margin: '8px 0 4px' }}>
          <div style={{ fontSize: 10.5, color: S.text3, marginBottom: 4 }}>Irrigation</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {[['auto', 'Auto'], ['yes', 'Yes'], ['no', 'No']].map(([id, label]) => (
              <button key={id} onClick={() => setIrrigation(id)} title={id === 'auto' ? 'Inferred from how much cropland around here is irrigated' : id === 'yes' ? 'Assume irrigation is available' : 'Rainfed only'}
                style={{ padding: '3px 10px', fontSize: 10.5, cursor: 'pointer', borderRadius: 3, border: `1px solid ${irrigation === id ? S.accent : S.border}`, background: irrigation === id ? 'rgba(201,168,106,0.15)' : 'transparent', color: irrigation === id ? S.accent : S.text2 }}>{label}</button>
            ))}
          </div>
        </div>
        <div style={{ margin: '8px 0 4px' }}>
          <div style={{ fontSize: 10.5, color: S.text3, marginBottom: 4 }}>Season</div>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {[['best', 'Best'], ['kharif', 'Kharif'], ['rabi', 'Rabi'], ['zaid', 'Summer']].map(([id, label]) => (
              <button key={id} onClick={() => setSeason(id)} title={id === 'best' ? 'Each crop shown in its best season' : id === 'kharif' ? 'Jun-Oct (monsoon)' : id === 'rabi' ? 'Nov-Mar (winter)' : 'Mar-May (summer, irrigated)'}
                style={{ padding: '3px 8px', fontSize: 10.5, cursor: 'pointer', borderRadius: 3, border: `1px solid ${season === id ? S.accent : S.border}`, background: season === id ? 'rgba(201,168,106,0.15)' : 'transparent', color: season === id ? S.accent : S.text2 }}>{label}</button>
            ))}
          </div>
        </div>
        <div style={{ fontSize: 10, color: S.text3, marginBottom: 4 }}>Have a soil test? Enter it (replaces modeled values):</div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <input style={inputStyle} placeholder="pH" value={ph} onChange={e => setPh(e.target.value)} />
          <input style={inputStyle} placeholder="OC g/kg" value={oc} onChange={e => setOc(e.target.value)} />
        </div>
        <select style={{ ...inputStyle, marginBottom: 6 }} value={texture} onChange={e => setTexture(e.target.value)}>
          <option value="">Texture (modeled)</option>
          {CROP_TEXTURES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <input style={{ ...inputStyle, marginBottom: 8 }} placeholder="State for mandi prices (optional)" value={state} onChange={e => setState(e.target.value)} />
        <RunButton onClick={submit} disabled={loading || !canRun} loading={loading} label="FIND SUITABLE CROPS" loadingLabel="ANALYSING..." />
        {!canRun && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 8 }}>Enter a location, pick a point, or draw an AOI.</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
      </ControlsPanel>

      <div style={{ flex: 1, minWidth: 0 }}>
        {!result && !loading && <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic', marginTop: 20 }}>Choose a location and find suitable crops.</div>}
        {result && (
          <div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 24px', marginBottom: 8 }}>
              <StatRow label="Soil pH" value={`${p.ph ?? 'n/a'} (${p.value_source.ph})`} />
              <StatRow label="Organic carbon" value={`${p.organic_carbon_gkg ?? 'n/a'} g/kg (${p.value_source.organic_carbon_gkg})`} />
              <StatRow label="Texture" value={`${p.texture_name ?? 'n/a'} (${p.value_source.texture})`} />
              <StatRow label="Rain / yr" value={p.annual_rain_mm != null ? `${p.annual_rain_mm} mm` : 'n/a'} />
              <StatRow label="Mean temp" value={p.annual_mean_temp_c != null ? `${p.annual_mean_temp_c} °C` : 'n/a'} />
            </div>
            {result.crops.map(c => {
              const rc = CROP_RATING_COLOR[cropCategory(c)];
              const isOpen = open === c.crop_id;
              return (
                <div key={c.crop_id} style={{ borderBottom: `1px solid ${S.border}`, padding: '6px 0' }}>
                  <div onClick={() => setOpen(isOpen ? null : c.crop_id)} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
                    <span style={{ width: 8, height: 8, borderRadius: 4, background: rc, flexShrink: 0 }} />
                    <span style={{ fontSize: 12.5, color: S.text, minWidth: 150 }}>{c.name} <span style={{ color: S.text3 }}>{c.name_hi}</span></span>
                    <span style={{ fontFamily: S.mono, fontSize: 11.5, color: rc, minWidth: 90, textTransform: 'uppercase' }}>{cropCategory(c).replace('_', ' ')} {c.score}{c.confidence != null && <span style={{ color: S.text3 }}> · conf {c.confidence}</span>}{c.caveats && c.caveats.length > 0 && <span title={c.caveats.map(v => v.message).join(' ')} style={{ color: '#ff9a45' }}> · ⚠ risk not modelled</span>}</span>
                    <span style={{ fontSize: 11, color: S.text3, flex: 1 }}>{c.best_season && <b style={{ color: S.accent, marginRight: 8, letterSpacing: 0.5 }}>{SEASON_NAME[c.best_season] || c.best_season}</b>}{cropLimitText(c)}</span>
                    {c.revenue?.status === 'ok' && <span style={{ fontSize: 11, fontFamily: S.mono, color: S.gold }}>~{fmtRs(c.revenue.revenue_rs_per_ha.modal)}/ha</span>}
                    <span style={{ color: S.text3, fontSize: 11 }}>{isOpen ? '▾' : '▸'}</span>
                  </div>
                  {isOpen && (
                    <div style={{ padding: '8px 0 4px 18px' }}>
                      {c.season_highlight && <div style={{ fontSize: 11.5, lineHeight: 1.5, color: S.text1 ?? S.text2, marginBottom: 6, padding: '4px 8px', borderLeft: `2px solid ${S.accent}` }}>{c.season_highlight}</div>}
                      {c.seasonal && Object.keys(c.seasonal).length > 1 && (
                        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                          {Object.entries(c.seasonal).map(([sn, e]) => (
                            <div key={sn} style={{ fontSize: 10.5, padding: '2px 8px', borderRadius: 3, border: `1px solid ${sn === c.best_season ? S.accent : S.border}`, color: CROP_RATING_COLOR[e.hard_limit ? 'unsuitable' : e.category] || S.text2 }}
                              title={e.message}>{SEASON_NAME[sn]}: {e.hard_limit ? 'unsuitable' : e.category.replace('_', ' ')} {e.score}{e.window_temp_c != null ? ` · ${e.window_temp_c}°C` : ''}</div>
                          ))}
                        </div>
                      )}
                      {c.evidence_summary && c.evidence_summary.map((line, i) => (
                        <div key={`e${i}`} style={{ fontSize: 11, lineHeight: 1.5, color: line.startsWith('✓') ? '#2ecc71' : line.startsWith('?') ? S.text3 : '#ff9a45', marginBottom: 2 }}>{line}</div>
                      ))}
                      {Object.entries(c.factors).map(([k, f]) => (
                        <StatRow key={k} label={`${k.replace('_', ' ')} (needs ${f.need})`} value={`${f.value ?? 'n/a'}${f.unit ? ' ' + f.unit : ''} → ${f.score ?? 'n/a'}${f.effective_score != null && f.effective_score !== f.score ? ` (counted as ${f.effective_score})` : ''}`} />
                      ))}
                      {c.amendments.map((a, i) => <div key={i} style={{ fontSize: 11, color: S.text2, marginTop: 5, lineHeight: 1.5 }}>• {a}</div>)}
                      {c.notes && <div style={{ fontSize: 10.5, color: S.text3, marginTop: 5 }}>{c.notes}</div>}
                      {c.revenue?.status === 'ok' && <div style={{ fontSize: 10.5, color: S.text3, marginTop: 5, lineHeight: 1.5 }}>Revenue ₹{c.revenue.revenue_rs_per_ha.low.toLocaleString('en-IN')}–₹{c.revenue.revenue_rs_per_ha.high.toLocaleString('en-IN')}/ha at ~₹{c.revenue.price_rs_per_q_modal}/quintal ({c.revenue.markets_used} mandi records), yield ~{c.revenue.yield_q_per_ha} q/ha. {c.revenue.note}</div>}
                      {c.price_trend && <div style={{ fontSize: 10.5, color: S.text3, marginTop: 3 }}>Historical: ~₹{c.price_trend.avg_price_rs_per_q}/quintal avg{c.price_trend.cagr_pct != null ? `, ${c.price_trend.cagr_pct}%/yr` : ''} ({c.price_trend.period}, CEDA, as of {c.price_trend.as_of})</div>}
                      {c.revenue && c.revenue.status !== 'ok' && <div style={{ fontSize: 10.5, color: S.text3, marginTop: 5 }}>{c.revenue.note}</div>}
                    </div>
                  )}
                </div>
              );
            })}
            {result.irrigation_evidence && result.irrigation_evidence.irrigated_cropland_share != null && (
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8 }}>
                Irrigation context: about {Math.round(result.irrigation_evidence.irrigated_cropland_share * 100)}% of cropland around here is irrigated (GFSAD1000, ~2010, 1 km).
                {result.irrigation_auto ? ` Auto mode assumed irrigation ${result.irrigation_available ? 'is' : 'is not'} available.` : ''}
                {result.irrigation_evidence.suggested_irrigation && !result.irrigation_available ? ' Consider setting Irrigation to Yes.' : ''}
              </div>
            )}
            {notGrown.length > 0 && (
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 10 }}>
                Not grown in {SEASON_NAME[result.season_requested] || result.season_requested} in practice (so not scored): {notGrown.map(n => `${n.name} (${Math.round((n.observed_area_share || 0) * 100)}% of national area)`).join(', ')}.
              </div>
            )}
            {result.seasonal_basis && <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 8 }}>{result.seasonal_basis}</div>}
            <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.5, marginTop: 10 }}>{result.disclaimer}</div>
          </div>
        )}
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

export default function AgriIntelBar({ apiUrl, drawnAOI, pointPickActive, onSetPointPickHandler, isMobile }) {
  const [open, setOpen] = useState(true);
  const [view, setView] = useState('groundwater'); // 'groundwater' | 'analysis' | 'phenology' | 'irrigation' | 'cropextent' | 'cropsuit'

  // Stop any active point-picking if the user collapses the bar or
  // switches away from the ML Extent tab — no reason to keep the map in
  // crosshair mode for a tab that's no longer visible.
  useEffect(() => {
    if ((!open || (view !== 'cropextent' && view !== 'cropsuit')) && pointPickActive) onSetPointPickHandler?.(null);
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
        <div style={{ display: 'flex', gap: 2, padding: '6px 16px 0', borderBottom: `1px solid ${S.border}`, overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {[['groundwater', 'Groundwater'], ['analysis', 'Analysis'], ['phenology', 'Crop Stage'], ['irrigation', 'Irrigation'], ['cropextent', 'ML Extent'], ['cropsuit', 'Crops']].map(([id, label]) => (
            <button key={id} onClick={() => setView(id)} style={{
              background: 'none', border: 'none', borderBottom: view === id ? `2px solid ${S.accent}` : '2px solid transparent',
              color: view === id ? S.accent : S.text3, fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase',
              padding: '6px 12px', cursor: 'pointer', marginBottom: -1, flexShrink: 0, whiteSpace: 'nowrap',
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
          {view === 'cropsuit' && <CropSuitabilityTab apiUrl={apiUrl} drawnAOI={drawnAOI} onSetPointPickHandler={onSetPointPickHandler} />}
          {view === 'cropextent' && <CropExtentTab apiUrl={apiUrl} drawnAOI={drawnAOI} pointPickActive={pointPickActive} onSetPointPickHandler={onSetPointPickHandler} />}
        </div>
      )}
    </div>
  );
}
