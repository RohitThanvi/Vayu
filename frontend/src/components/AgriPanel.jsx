import { useState, useEffect, useCallback } from 'react';
import SparkChart from './SparkChart';

// Matches the dark-terminal aesthetic used elsewhere in App.jsx (kept local
// to avoid importing the S/Icon internals across files).
const S = {
  bg: '#0a0c0f', surface: '#12151a', surface2: '#181c22', border: '#262b33',
  text: '#e4e7eb', text2: '#a8b0bb', text3: '#5c6673', accent: '#7eb8d4',
  mono: "'JetBrains Mono', 'Courier New', monospace",
};

const BAND_COLOR = { low: '#4a7c59', moderate: '#c9933a', high: '#c96a3a', severe: '#8b2020' };

function bandColor(band) { return BAND_COLOR[band] || S.text3; }

function ActionButton({ onClick, disabled, loading, label, loadingLabel }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '9px', fontSize: 13, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
      background: disabled ? S.surface2 : 'rgba(126,184,212,0.1)',
      border: `1px solid ${disabled ? S.border : S.accent}`,
      color: disabled ? S.text3 : S.accent,
      cursor: disabled ? 'not-allowed' : 'pointer',
    }}>
      {loading ? loadingLabel : label}
    </button>
  );
}

function ErrorBox({ children }) {
  return (
    <div style={{ background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '9px 11px', fontSize: 13, color: S.text2 }}>
      {children}
    </div>
  );
}

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '4px 0', borderBottom: `1px solid ${S.border}` }}>
      <span style={{ color: S.text3 }}>{label}</span>
      <span style={{ color: S.text2, fontFamily: S.mono }}>{value}</span>
    </div>
  );
}

export default function AgriPanel({ drawnAOI, apiUrl, searchedRegionName }) {
  const [view, setView] = useState('score'); // 'score' | 'watchlist' | 'rollup' | 'mandi' | 'groundwater' | 'analysis' | 'phenology' | 'irrigation' | 'crop_extent'
  const [scoreResult, setScoreResult] = useState(null);
  const [scoreLoading, setScoreLoading] = useState(false);
  const [scoreError, setScoreError] = useState(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);

  const [regions, setRegions] = useState([]);
  const [regionName, setRegionName] = useState('');
  const [regionCrop, setRegionCrop] = useState('');
  const [regionThreshold, setRegionThreshold] = useState(60);
  const [creatingRegion, setCreatingRegion] = useState(false);

  const [rollup, setRollup] = useState(null);
  const [rollupRole, setRollupRole] = useState('officer');

  const [whatsappInfo, setWhatsappInfo] = useState(null);
  useEffect(() => {
    fetch(`${apiUrl}/api/v1/agri/whatsapp/info`).then(r => r.json()).then(setWhatsappInfo).catch(() => {});
  }, [apiUrl]);

  const [mandiRecords, setMandiRecords] = useState(null);
  const [mandiLoading, setMandiLoading] = useState(false);
  const [mandiError, setMandiError] = useState(null);
  const [mandiCommodity, setMandiCommodity] = useState('');
  const [mandiState, setMandiState] = useState('');

  // Groundwater trend
  const [gwResult, setGwResult] = useState(null);
  const [gwLoading, setGwLoading] = useState(false);
  const [gwError, setGwError] = useState(null);

  // Analysis (trend charting) — NDVI seasonal curve + groundwater series,
  // side by side, mirroring BusinessIntelBar's Analysis tab pattern.
  const [analysisResult, setAnalysisResult] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState(null);

  // Phenology (crop-stage read)
  const [phenoResult, setPhenoResult] = useState(null);
  const [phenoLoading, setPhenoLoading] = useState(false);
  const [phenoError, setPhenoError] = useState(null);

  // Irrigation advisory
  const [irrResult, setIrrResult] = useState(null);
  const [irrLoading, setIrrLoading] = useState(false);
  const [irrError, setIrrError] = useState(null);

  // Crop-extent ML classification (Agri wrapper around Spectra's RF tool)
  const [cropTrainingSamples, setCropTrainingSamples] = useState([]);
  const [cropPtLat, setCropPtLat] = useState('');
  const [cropPtLon, setCropPtLon] = useState('');
  const [cropPtLabel, setCropPtLabel] = useState('');
  const [cropResult, setCropResult] = useState(null);
  const [cropLoading, setCropLoading] = useState(false);
  const [cropError, setCropError] = useState(null);
  const CROP_MIN_SAMPLES_PER_CLASS = 4;
  const CROP_MIN_CLASSES = 2;

  const runRiskScore = useCallback(async () => {
    if (!drawnAOI) return;
    setScoreLoading(true); setScoreError(null); setScoreResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/risk-score`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Risk scoring failed');
      setScoreResult(await resp.json());
    } catch (e) {
      setScoreError(e.message);
    } finally {
      setScoreLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  const downloadAgriReport = async () => {
    if (!drawnAOI) return;
    setReportLoading(true); setReportError(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/report/agri-risk`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI, region_name: searchedRegionName || undefined }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Report generation failed');
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = 'vayu_agri_risk_report.pdf';
      document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      setReportError(e.message);
    } finally {
      setReportLoading(false);
    }
  };

  const loadRegions = useCallback(async () => {
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/regions`);
      const data = await resp.json();
      setRegions(data.regions || []);
    } catch (e) { /* non-fatal */ }
  }, [apiUrl]);

  const loadRollup = useCallback(async (role) => {
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/rollup?role=${role}`);
      setRollup(await resp.json());
    } catch (e) { /* non-fatal */ }
  }, [apiUrl]);

  const loadMandi = useCallback(async () => {
    setMandiLoading(true); setMandiError(null);
    try {
      const params = new URLSearchParams();
      if (mandiCommodity.trim()) params.set('commodity', mandiCommodity.trim());
      if (mandiState.trim()) params.set('state', mandiState.trim());
      const resp = await fetch(`${apiUrl}/api/v1/agri/mandi-price?${params}`);
      const data = await resp.json();
      if (data.error) { setMandiError(data.error); setMandiRecords([]); }
      else setMandiRecords(data.records || []);
    } catch (e) {
      setMandiError(e.message);
    } finally {
      setMandiLoading(false);
    }
  }, [apiUrl, mandiCommodity, mandiState]);

  useEffect(() => { if (view === 'watchlist') loadRegions(); }, [view, loadRegions]);
  useEffect(() => { if (view === 'rollup') loadRollup(rollupRole); }, [view, rollupRole, loadRollup]);
  useEffect(() => { if (view === 'mandi' && mandiRecords === null) loadMandi(); }, [view, mandiRecords, loadMandi]);

  const runGroundwater = useCallback(async () => {
    if (!drawnAOI) return;
    setGwLoading(true); setGwError(null); setGwResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/groundwater-trend`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Groundwater trend failed');
      setGwResult(await resp.json());
    } catch (e) {
      setGwError(e.message);
    } finally {
      setGwLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  const runAnalysis = useCallback(async () => {
    if (!drawnAOI) return;
    setAnalysisLoading(true); setAnalysisError(null); setAnalysisResult(null);
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
      setAnalysisResult({ ndvi: ndviResp, groundwater: gwResp });
    } catch (e) {
      setAnalysisError(e.message);
    } finally {
      setAnalysisLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  const runPhenology = useCallback(async () => {
    if (!drawnAOI) return;
    setPhenoLoading(true); setPhenoError(null); setPhenoResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/phenology`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Phenology computation failed');
      setPhenoResult(await resp.json());
    } catch (e) {
      setPhenoError(e.message);
    } finally {
      setPhenoLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  const runIrrigation = useCallback(async () => {
    if (!drawnAOI) return;
    setIrrLoading(true); setIrrError(null); setIrrResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/irrigation-advisory`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Irrigation advisory failed');
      setIrrResult(await resp.json());
    } catch (e) {
      setIrrError(e.message);
    } finally {
      setIrrLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  const addCropPoint = () => {
    const lat = parseFloat(cropPtLat), lon = parseFloat(cropPtLon);
    const label = cropPtLabel.trim();
    if (Number.isNaN(lat) || Number.isNaN(lon) || !label) return;
    setCropTrainingSamples(prev => {
      const existing = prev.find(p => p.class_label.toLowerCase() === label.toLowerCase());
      const class_id = existing ? existing.class_id : (prev.reduce((max, p) => Math.max(max, p.class_id), 0) + 1);
      return [...prev, { lat, lon, class_id, class_label: existing ? existing.class_label : label }];
    });
    setCropPtLat(''); setCropPtLon(''); setCropPtLabel('');
  };
  const removeCropPoint = (idx) => setCropTrainingSamples(prev => prev.filter((_, i) => i !== idx));
  const cropByClass = cropTrainingSamples.reduce((acc, p, idx) => {
    (acc[p.class_id] ||= { label: p.class_label, points: [] }).points.push({ ...p, idx });
    return acc;
  }, {});
  const cropClassCount = Object.keys(cropByClass).length;
  const cropReady = cropClassCount >= CROP_MIN_CLASSES && Object.values(cropByClass).every(c => c.points.length >= CROP_MIN_SAMPLES_PER_CLASS);

  const runCropExtent = useCallback(async () => {
    if (!drawnAOI || !cropReady) return;
    setCropLoading(true); setCropError(null); setCropResult(null);
    try {
      const end = new Date().toISOString().slice(0, 10);
      const start = new Date(Date.now() - 90 * 24 * 3600 * 1000).toISOString().slice(0, 10);
      const resp = await fetch(`${apiUrl}/api/v1/agri/crop-extent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI, start_date: start, end_date: end, training_samples: cropTrainingSamples }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Crop-extent classification failed');
      setCropResult(await resp.json());
    } catch (e) {
      setCropError(e.message);
    } finally {
      setCropLoading(false);
    }
  }, [drawnAOI, apiUrl, cropTrainingSamples, cropReady]);

  // Pre-fill the watchlist name field from a searched place, so the user
  // doesn't have to retype "Jodhpur, Rajasthan" after already searching it
  // — only when they haven't started typing their own name for this AOI.
  useEffect(() => {
    if (searchedRegionName && !regionName) setRegionName(searchedRegionName);
  }, [searchedRegionName]); // eslint-disable-line react-hooks/exhaustive-deps

  const createRegion = async () => {
    if (!drawnAOI || !regionName.trim()) return;
    setCreatingRegion(true);
    try {
      await fetch(`${apiUrl}/api/v1/agri/regions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: regionName.trim(), aoi_geojson: drawnAOI,
          crop: regionCrop.trim() || null, risk_threshold: Number(regionThreshold) || 60,
        }),
      });
      setRegionName(''); setRegionCrop('');
      await loadRegions();
    } finally {
      setCreatingRegion(false);
    }
  };

  const deleteRegion = async (id) => {
    await fetch(`${apiUrl}/api/v1/agri/regions/${id}`, { method: 'DELETE' });
    loadRegions();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {[['score','Score'], ['watchlist','Watchlist'], ['rollup','Overview'], ['mandi','Mandi'],
          ['groundwater','Groundwater'], ['analysis','Analysis'], ['phenology','Stage'], ['irrigation','Irrigate'], ['crop_extent','ML Extent']].map(([v,label]) => (
          <button key={v} onClick={() => setView(v)}
            style={{
              flex: 1, padding: '6px 4px', fontSize: 12, fontFamily: S.mono, letterSpacing: 1,
              textTransform: 'uppercase', cursor: 'pointer',
              background: view === v ? 'rgba(126,184,212,0.1)' : S.surface2,
              border: `1px solid ${view === v ? S.accent : S.border}`,
              color: view === v ? S.accent : S.text3,
            }}>
            {label}
          </button>
        ))}
      </div>

      {view === 'score' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Draw an AOI on the map (or search a place) to get a composite 0-100 agricultural risk score — combines vegetation stress, drought, and soil moisture into one number with a plain-language reason, instead of raw indices.
          </div>
          <div style={{
            fontSize: 15, fontFamily: S.mono, padding: '7px 10px', letterSpacing: 1,
            background: drawnAOI ? 'rgba(74,124,89,0.08)' : S.surface2,
            border: `1px solid ${drawnAOI ? '#4a7c59' : S.border}`,
            color: drawnAOI ? '#4a7c59' : S.text3,
          }}>
            {drawnAOI ? 'AOI DEFINED' : 'DRAW AOI ON MAP'}
          </div>
          <button onClick={runRiskScore} disabled={!drawnAOI || scoreLoading}
            style={{
              padding: '10px', fontSize: 15, fontFamily: S.mono, letterSpacing: 2, textTransform: 'uppercase',
              background: !drawnAOI || scoreLoading ? S.surface2 : 'rgba(126,184,212,0.1)',
              border: `1px solid ${!drawnAOI || scoreLoading ? S.border : S.accent}`,
              color: !drawnAOI || scoreLoading ? S.text3 : S.accent,
              cursor: !drawnAOI || scoreLoading ? 'not-allowed' : 'pointer',
            }}>
            {scoreLoading ? 'SCORING...' : 'RUN RISK SCORE'}
          </button>
          {scoreError && (
            <div style={{ background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '9px 11px', fontSize: 14, color: S.text2 }}>
              {scoreError}
            </div>
          )}
          {scoreResult && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{
                display: 'flex', alignItems: 'baseline', gap: 10, padding: '12px',
                background: S.surface2, border: `1px solid ${bandColor(scoreResult.band)}`,
              }}>
                <div style={{ fontSize: 32, fontFamily: S.mono, fontWeight: 700, color: bandColor(scoreResult.band) }}>
                  {scoreResult.risk_score}
                </div>
                <div>
                  <div style={{ fontSize: 13, fontFamily: S.mono, letterSpacing: 2, textTransform: 'uppercase', color: bandColor(scoreResult.band) }}>
                    {scoreResult.band}
                  </div>
                  <div style={{ fontSize: 12, color: S.text3 }}>confidence {scoreResult.confidence}%</div>
                </div>
              </div>
              <div style={{ fontSize: 13, color: S.text2, lineHeight: 1.6 }}>{scoreResult.reason}</div>
              <div style={{ fontSize: 11, color: S.text3, fontFamily: S.mono }}>
                inputs used: {scoreResult.inputs_used?.join(', ') || 'none'}
                {scoreResult.inputs_failed?.length > 0 && ` · failed: ${scoreResult.inputs_failed.join(', ')}`}
              </div>
              <button onClick={downloadAgriReport} disabled={reportLoading}
                style={{
                  padding: '9px', fontSize: 13, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
                  background: reportLoading ? S.surface2 : 'rgba(126,184,212,0.1)',
                  border: `1px solid ${reportLoading ? S.border : S.accent}`,
                  color: reportLoading ? S.text3 : S.accent,
                  cursor: reportLoading ? 'not-allowed' : 'pointer',
                }}>
                {reportLoading ? 'GENERATING...' : 'DOWNLOAD REPORT (PDF)'}
              </button>
              {reportError && (
                <div style={{ fontSize: 12, color: S.text2, background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '6px 9px' }}>
                  {reportError}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {view === 'watchlist' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Save the current AOI to be automatically re-scored on a schedule — an alert fires (and is logged here) when its risk score crosses the threshold you set.
          </div>
          <input placeholder="Region name (e.g. Block 4 wheat fields)" value={regionName}
            onChange={e => setRegionName(e.target.value)}
            style={{ background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontFamily: S.mono, fontSize: 13, padding: '7px 9px' }} />
          <input placeholder="Crop (optional)" value={regionCrop}
            onChange={e => setRegionCrop(e.target.value)}
            style={{ background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontFamily: S.mono, fontSize: 13, padding: '7px 9px' }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: S.text3, fontFamily: S.mono }}>ALERT THRESHOLD</span>
            <input type="number" min={0} max={100} value={regionThreshold}
              onChange={e => setRegionThreshold(e.target.value)}
              style={{ width: 60, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontFamily: S.mono, fontSize: 13, padding: '5px 7px' }} />
          </div>
          <button onClick={createRegion} disabled={!drawnAOI || !regionName.trim() || creatingRegion}
            style={{
              padding: '9px', fontSize: 13, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
              background: !drawnAOI || !regionName.trim() ? S.surface2 : 'rgba(126,184,212,0.1)',
              border: `1px solid ${!drawnAOI || !regionName.trim() ? S.border : S.accent}`,
              color: !drawnAOI || !regionName.trim() ? S.text3 : S.accent,
              cursor: !drawnAOI || !regionName.trim() ? 'not-allowed' : 'pointer',
            }}>
            {creatingRegion ? 'SAVING...' : 'ADD TO WATCHLIST'}
          </button>

          <div style={{ borderTop: `1px solid ${S.border}`, paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 12, color: S.text3, fontFamily: S.mono, letterSpacing: 1.5 }}>{regions.length} WATCHED REGIONS</div>
            {regions.map(r => (
              <RegionCard key={r.id} region={r} apiUrl={apiUrl} onDelete={() => deleteRegion(r.id)} />
            ))}
          </div>
        </div>
      )}

      {view === 'rollup' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            {[['officer','My Regions'], ['district','District View']].map(([r,label]) => (
              <button key={r} onClick={() => setRollupRole(r)}
                style={{
                  flex: 1, padding: '5px', fontSize: 11, fontFamily: S.mono, letterSpacing: 1, textTransform: 'uppercase',
                  cursor: 'pointer', background: rollupRole === r ? 'rgba(126,184,212,0.1)' : S.surface2,
                  border: `1px solid ${rollupRole === r ? S.accent : S.border}`, color: rollupRole === r ? S.accent : S.text3,
                }}>
                {label}
              </button>
            ))}
          </div>
          {!rollup && <div style={{ fontSize: 13, color: S.text3, textAlign: 'center', padding: '20px 0' }}>Loading…</div>}
          {rollup && rollup.role === 'officer' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {rollup.regions?.length === 0 && <div style={{ fontSize: 13, color: S.text3 }}>No watched regions yet — add one from the Watchlist tab.</div>}
              {rollup.regions?.map(({ region, latest_alert }) => {
                const score = latest_alert?.risk_score;
                const band = score == null ? null : score >= 75 ? 'severe' : score >= 55 ? 'high' : score >= 30 ? 'moderate' : 'low';
                const color = bandColor(band);
                return (
                  <div key={region.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', background: S.surface2, borderLeft: `3px solid ${color}`, borderRadius: 3 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, color: S.text, fontFamily: S.mono }}>{region.name}</div>
                      <div style={{ fontSize: 11, color: S.text3, marginTop: 2 }}>{score == null ? 'Not scored yet' : band.charAt(0).toUpperCase()+band.slice(1)+' risk'}</div>
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 800, fontFamily: S.mono, color }}>{score != null ? Math.round(score) : '—'}</div>
                  </div>
                );
              })}
            </div>
          )}
          {rollup && rollup.role === 'district' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'flex', gap: 16, padding: '10px 12px', background: S.surface2, borderRadius: 3 }}>
                <div>
                  <div style={{ fontSize: 22, fontWeight: 800, fontFamily: S.mono, color: S.text }}>{rollup.total_regions}</div>
                  <div style={{ fontSize: 10, color: S.text3, letterSpacing: 1, textTransform: 'uppercase' }}>Regions</div>
                </div>
                <div>
                  <div style={{ fontSize: 22, fontWeight: 800, fontFamily: S.mono, color: S.accent }}>{rollup.regions_with_data}</div>
                  <div style={{ fontSize: 10, color: S.text3, letterSpacing: 1, textTransform: 'uppercase' }}>Scored</div>
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: S.text3, fontFamily: S.mono, letterSpacing: 1.5, marginBottom: 8, textTransform: 'uppercase' }}>By Risk Band</div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {Object.entries(rollup.band_counts || {}).map(([band, count]) => (
                    <div key={band} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', fontSize: 12, fontFamily: S.mono, background: S.surface2, borderRadius: 3 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: bandColor(band), display: 'inline-block' }} />
                      <span style={{ color: S.text2 }}>{count}</span>
                      <span style={{ color: S.text3, textTransform: 'capitalize' }}>{band}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: S.text3, fontFamily: S.mono, letterSpacing: 1.5, marginBottom: 8, textTransform: 'uppercase' }}>Highest Risk</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {(rollup.top_risk_regions || []).map((r, i) => (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, padding: '6px 2px', borderBottom: i < rollup.top_risk_regions.length-1 ? `1px solid ${S.border}` : 'none' }}>
                      <span style={{ color: S.text2 }}>{r.name}</span>
                      <span style={{ fontFamily: S.mono, fontWeight: 700, color: bandColor(r.risk_score >= 75 ? 'severe' : r.risk_score >= 55 ? 'high' : r.risk_score >= 30 ? 'moderate' : 'low') }}>{r.risk_score}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {view === 'mandi' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input placeholder="Commodity (e.g. Wheat)" value={mandiCommodity}
              onChange={e => setMandiCommodity(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && loadMandi()}
              style={{ flex: 1, minWidth: 0, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontFamily: S.mono, fontSize: 12, padding: '7px 9px' }} />
            <input placeholder="State (optional)" value={mandiState}
              onChange={e => setMandiState(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && loadMandi()}
              style={{ flex: 1, minWidth: 0, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontFamily: S.mono, fontSize: 12, padding: '7px 9px' }} />
            <button onClick={loadMandi} disabled={mandiLoading}
              style={{ background: 'rgba(126,184,212,0.1)', border: `1px solid ${S.accent}`, color: S.accent, fontFamily: S.mono, fontSize: 11, letterSpacing: 1, padding: '0 12px', cursor: mandiLoading ? 'wait' : 'pointer', flexShrink: 0 }}>
              {mandiLoading ? '...' : 'GO'}
            </button>
          </div>
          {mandiError && <div style={{ fontSize: 12, color: '#c96a3a' }}>{mandiError}</div>}
          {mandiLoading && <div style={{ fontSize: 13, color: S.text3, textAlign: 'center', padding: '16px 0' }}>Loading…</div>}
          {mandiRecords && !mandiLoading && mandiRecords.length === 0 && !mandiError && (
            <div style={{ fontSize: 13, color: S.text3 }}>No mandi prices found — try a different commodity or state.</div>
          )}
          {mandiRecords && mandiRecords.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {mandiRecords.map((r, i) => (
                <div key={i} style={{ padding: '9px 11px', background: S.surface2, borderRadius: 3 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span style={{ fontSize: 13, color: S.text, fontFamily: S.mono }}>{r.commodity}{r.variety ? ` (${r.variety})` : ''}</span>
                    <span style={{ fontSize: 16, fontWeight: 800, fontFamily: S.mono, color: S.accent }}>₹{r.modal_price}</span>
                  </div>
                  <div style={{ fontSize: 11, color: S.text3, marginTop: 3 }}>
                    {r.market}{r.district ? `, ${r.district}` : ''}{r.state ? `, ${r.state}` : ''} · Range ₹{r.min_price}–₹{r.max_price} · {r.arrival_date}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {view === 'groundwater' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Groundwater depletion/rising trend from GRACE satellite data — regional-scale (~300km grid), layered against the drought/moisture signal in the risk score above.
          </div>
          <ActionButton onClick={runGroundwater} disabled={!drawnAOI || gwLoading} loading={gwLoading} label="CHECK GROUNDWATER TREND" loadingLabel="CHECKING..." />
          {gwError && <ErrorBox>{gwError}</ErrorBox>}
          {gwResult?.status === 'ok' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: '12px', background: S.surface2, border: `1px solid ${gwResult.trend === 'declining' ? '#c96a3a' : gwResult.trend === 'rising' ? '#4a7c59' : S.border}` }}>
                <div style={{ fontSize: 22, fontFamily: S.mono, fontWeight: 700, color: gwResult.trend === 'declining' ? '#c96a3a' : gwResult.trend === 'rising' ? '#4a7c59' : S.text2, textTransform: 'uppercase' }}>{gwResult.trend}</div>
                <div style={{ fontSize: 12, color: S.text3 }}>{gwResult.slope_cm_per_year > 0 ? '+' : ''}{gwResult.slope_cm_per_year} cm/yr</div>
              </div>
              <SparkChart points={gwResult.series} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(1)} emptyLabel="No GRACE series available." />
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.4 }}>{gwResult.resolution_note}</div>
            </div>
          )}
          {gwResult && gwResult.status !== 'ok' && <div style={{ fontSize: 13, color: S.text3 }}>{gwResult.note}</div>}
        </div>
      )}

      {view === 'analysis' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Historical trend charts for this AOI — NDVI over the last 12 months, and the same groundwater trend as above, side by side.
          </div>
          <ActionButton onClick={runAnalysis} disabled={!drawnAOI || analysisLoading} loading={analysisLoading} label="LOAD TRENDS" loadingLabel="LOADING..." />
          {analysisError && <ErrorBox>{analysisError}</ErrorBox>}
          {analysisResult && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <div style={{ fontSize: 11, color: S.text3, fontFamily: S.mono, letterSpacing: 1.5, marginBottom: 6, textTransform: 'uppercase' }}>NDVI (monthly)</div>
                {analysisResult.ndvi?.points
                  ? <SparkChart points={analysisResult.ndvi.points} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(2)} emptyLabel="No NDVI data for this AOI/period." />
                  : <div style={{ fontSize: 12, color: S.text3 }}>NDVI trend unavailable.</div>}
              </div>
              <div>
                <div style={{ fontSize: 11, color: S.text3, fontFamily: S.mono, letterSpacing: 1.5, marginBottom: 6, textTransform: 'uppercase' }}>Groundwater (GRACE)</div>
                {analysisResult.groundwater?.status === 'ok'
                  ? <SparkChart points={analysisResult.groundwater.series} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(1)} emptyLabel="No groundwater series available." />
                  : <div style={{ fontSize: 12, color: S.text3 }}>{analysisResult.groundwater?.note || 'Groundwater trend unavailable.'}</div>}
              </div>
            </div>
          )}
        </div>
      )}

      {view === 'phenology' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Where this AOI sits in its own growing season right now — read from the shape of its last 12 months of NDVI, not a fixed crop calendar.
          </div>
          <ActionButton onClick={runPhenology} disabled={!drawnAOI || phenoLoading} loading={phenoLoading} label="READ CROP STAGE" loadingLabel="READING..." />
          {phenoError && <ErrorBox>{phenoError}</ErrorBox>}
          {phenoResult?.status === 'ok' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ padding: '12px', background: S.surface2, border: `1px solid ${S.accent}` }}>
                <div style={{ fontSize: 16, fontFamily: S.mono, fontWeight: 700, color: S.accent, textTransform: 'uppercase' }}>{phenoResult.current_stage}</div>
                <div style={{ fontSize: 11, color: S.text3, marginTop: 4 }}>as of {phenoResult.as_of}</div>
              </div>
              <StatRow label="Green-up" value={phenoResult.green_up_date} />
              <StatRow label="Peak greenness" value={phenoResult.peak_date} />
              <StatRow label="Senescence" value={phenoResult.senescence_date || 'not yet reached in this window'} />
              <SparkChart points={phenoResult.series} formatX={d => d.slice(0, 7)} formatY={v => v.toFixed(2)} emptyLabel="No NDVI series available." />
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.4 }}>{phenoResult.method}</div>
            </div>
          )}
          {phenoResult && phenoResult.status !== 'ok' && <div style={{ fontSize: 13, color: S.text3 }}>{phenoResult.note}</div>}
        </div>
      )}

      {view === 'irrigation' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Irrigate now / monitor / hold off — soil moisture (SMAP) cross-checked against recent rainfall (CHIRPS) vs. seasonal-normal. A directional regional read, not a field-level prescription.
          </div>
          <ActionButton onClick={runIrrigation} disabled={!drawnAOI || irrLoading} loading={irrLoading} label="CHECK IRRIGATION NEED" loadingLabel="CHECKING..." />
          {irrError && <ErrorBox>{irrError}</ErrorBox>}
          {irrResult && irrResult.recommendation !== 'insufficient_data' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(() => {
                const rc = irrResult.recommendation === 'irrigate_now' ? '#c96a3a' : irrResult.recommendation === 'hold_off' ? '#4a7c59' : '#c9933a';
                return (
                  <div style={{ padding: '12px', background: S.surface2, border: `1px solid ${rc}` }}>
                    <div style={{ fontSize: 16, fontFamily: S.mono, fontWeight: 700, color: rc, textTransform: 'uppercase' }}>{irrResult.recommendation.replace('_', ' ')}</div>
                  </div>
                );
              })()}
              <div style={{ fontSize: 13, color: S.text2, lineHeight: 1.6 }}>{irrResult.reason}</div>
              <StatRow label="Dry-stress share of AOI" value={irrResult.soil_moisture?.dry_stress_pct != null ? `${irrResult.soil_moisture.dry_stress_pct}%` : 'n/a'} />
              <StatRow label="Rainfall vs seasonal-normal" value={irrResult.rainfall?.condition || 'n/a'} />
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.4 }}>{irrResult.disclaimer}</div>
            </div>
          )}
          {irrResult?.recommendation === 'insufficient_data' && <div style={{ fontSize: 13, color: S.text3 }}>{irrResult.reason}</div>}
        </div>
      )}

      {view === 'crop_extent' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            How much of this AOI is actually cropped — Random Forest classification (same engine as Spectra's ML Classify) trained on points you label. Add at least {CROP_MIN_CLASSES} classes with {CROP_MIN_SAMPLES_PER_CLASS}+ points each (e.g. "cropped" vs "non-cropped").
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input type="number" step="any" placeholder="Lat" value={cropPtLat} onChange={e => setCropPtLat(e.target.value)}
              style={{ width: 66, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px' }} />
            <input type="number" step="any" placeholder="Lon" value={cropPtLon} onChange={e => setCropPtLon(e.target.value)}
              style={{ width: 66, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px' }} />
            <input type="text" placeholder="Class (e.g. cropped)" value={cropPtLabel} onChange={e => setCropPtLabel(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && addCropPoint()}
              style={{ flex: 1, minWidth: 0, background: S.bg, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '6px 8px' }} />
            <button onClick={addCropPoint} disabled={!cropPtLat || !cropPtLon || !cropPtLabel.trim()}
              style={{ background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5, fontFamily: S.mono, padding: '6px 10px', cursor: 'pointer', flexShrink: 0 }}>
              + Add
            </button>
          </div>
          {cropClassCount > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {Object.entries(cropByClass).map(([cid, c]) => (
                <div key={cid} style={{ background: S.surface2, border: `1px solid ${S.border}`, padding: '6px 8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                    <span style={{ color: c.points.length >= CROP_MIN_SAMPLES_PER_CLASS ? S.accent : '#c9933a' }}>{c.label}</span>
                    <span style={{ color: S.text3, fontFamily: S.mono }}>{c.points.length} pt{c.points.length !== 1 ? 's' : ''} {c.points.length < CROP_MIN_SAMPLES_PER_CLASS ? `(need ${CROP_MIN_SAMPLES_PER_CLASS}+)` : ''}</span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {c.points.map(p => (
                      <span key={p.idx} onClick={() => removeCropPoint(p.idx)} title="Click to remove"
                        style={{ fontSize: 9.5, fontFamily: S.mono, color: S.text3, background: S.bg, border: `1px solid ${S.border}`, padding: '2px 5px', cursor: 'pointer' }}>
                        {p.lat.toFixed(3)},{p.lon.toFixed(3)} ✕
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          <ActionButton onClick={runCropExtent} disabled={!drawnAOI || !cropReady || cropLoading} loading={cropLoading} label="RUN CROP-EXTENT CLASSIFICATION" loadingLabel="CLASSIFYING..." />
          {cropError && <ErrorBox>{cropError}</ErrorBox>}
          {cropResult && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {cropResult.classes.map(c => (
                <div key={c.class_id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, padding: '6px 0', borderBottom: `1px solid ${S.border}` }}>
                  <span style={{ color: S.text2 }}>{c.label}</span>
                  <span style={{ fontFamily: S.mono, color: S.accent }}>{c.pct_of_aoi}% ({c.area_km2} km²)</span>
                </div>
              ))}
              <StatRow label="Test accuracy (held-out points)" value={cropResult.accuracy?.test_accuracy != null ? `${Math.round(cropResult.accuracy.test_accuracy * 100)}%` : 'n/a'} />
              <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.4 }}>{cropResult.accuracy?.note}</div>
            </div>
          )}
        </div>
      )}

      {whatsappInfo?.configured && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', background: S.surface2, border: `1px solid ${S.border}`, fontSize: 12, color: S.text3, fontFamily: S.mono }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#4a7c59', flexShrink: 0 }} />
          WhatsApp a place name to <span style={{ color: S.text2 }}>{whatsappInfo.number}</span> for its risk score
        </div>
      )}
    </div>
  );
}

function RegionCard({ region, apiUrl, onDelete }) {
  const [alerts, setAlerts] = useState(null);
  const [expanded, setExpanded] = useState(false);

  const loadAlerts = async () => {
    const resp = await fetch(`${apiUrl}/api/v1/agri/regions/${region.id}/alerts?limit=5`);
    const data = await resp.json();
    setAlerts(data.alerts || []);
  };

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && alerts === null) loadAlerts();
  };

  const sendFeedback = async (alertId, accurate) => {
    await fetch(`${apiUrl}/api/v1/agri/feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alert_id: alertId, accurate }),
    });
    loadAlerts();
  };

  return (
    <div style={{ padding: '8px 10px', background: S.surface2, border: `1px solid ${S.border}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <button onClick={toggle} style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', flex: 1 }}>
          <div style={{ fontSize: 13, color: S.text, fontFamily: S.mono }}>{region.name}</div>
          <div style={{ fontSize: 11, color: S.text3 }}>{region.crop || 'no crop label'} · threshold {region.risk_threshold}</div>
        </button>
        <button onClick={onDelete} style={{ background: 'none', border: 'none', color: S.text3, cursor: 'pointer', fontSize: 16 }}>×</button>
      </div>
      {expanded && (
        <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {alerts === null && <div style={{ fontSize: 12, color: S.text3 }}>Loading alerts…</div>}
          {alerts?.length === 0 && <div style={{ fontSize: 12, color: S.text3 }}>No alerts fired yet.</div>}
          {alerts?.map(a => (
            <div key={a.id} style={{ fontSize: 12, color: S.text2, borderTop: `1px solid ${S.border}`, paddingTop: 6 }}>
              <div>Risk {a.risk_score}/100 — {a.reason}</div>
              <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                <button onClick={() => sendFeedback(a.id, true)} style={{ fontSize: 11, background: 'none', border: `1px solid ${S.border}`, color: S.text3, cursor: 'pointer', padding: '2px 6px' }}>accurate</button>
                <button onClick={() => sendFeedback(a.id, false)} style={{ fontSize: 11, background: 'none', border: `1px solid ${S.border}`, color: S.text3, cursor: 'pointer', padding: '2px 6px' }}>false alarm</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
