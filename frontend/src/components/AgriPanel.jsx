import { useState, useEffect, useCallback } from 'react';

// Matches the dark-terminal aesthetic used elsewhere in App.jsx (kept local
// to avoid importing the S/Icon internals across files).
const S = {
  bg: '#0a0c0f', surface: '#12151a', surface2: '#181c22', border: '#262b33',
  text: '#e4e7eb', text2: '#a8b0bb', text3: '#5c6673', accent: '#7eb8d4',
  mono: "'JetBrains Mono', 'Courier New', monospace",
};

const BAND_COLOR = { low: '#4a7c59', moderate: '#c9933a', high: '#c96a3a', severe: '#8b2020' };

function bandColor(band) { return BAND_COLOR[band] || S.text3; }

export default function AgriPanel({ drawnAOI, apiUrl }) {
  const [view, setView] = useState('score'); // 'score' | 'watchlist' | 'rollup'
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
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
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

  useEffect(() => { if (view === 'watchlist') loadRegions(); }, [view, loadRegions]);
  useEffect(() => { if (view === 'rollup') loadRollup(rollupRole); }, [view, rollupRole, loadRollup]);

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
      <div style={{ display: 'flex', gap: 6 }}>
        {['score', 'watchlist', 'rollup'].map(v => (
          <button key={v} onClick={() => setView(v)}
            style={{
              flex: 1, padding: '6px 4px', fontSize: 12, fontFamily: S.mono, letterSpacing: 1,
              textTransform: 'uppercase', cursor: 'pointer',
              background: view === v ? 'rgba(126,184,212,0.1)' : S.surface2,
              border: `1px solid ${view === v ? S.accent : S.border}`,
              color: view === v ? S.accent : S.text3,
            }}>
            {v}
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
            {['officer', 'district'].map(r => (
              <button key={r} onClick={() => setRollupRole(r)}
                style={{
                  flex: 1, padding: '5px', fontSize: 11, fontFamily: S.mono, letterSpacing: 1, textTransform: 'uppercase',
                  cursor: 'pointer', background: rollupRole === r ? 'rgba(126,184,212,0.1)' : S.surface2,
                  border: `1px solid ${rollupRole === r ? S.accent : S.border}`, color: rollupRole === r ? S.accent : S.text3,
                }}>
                {r} view
              </button>
            ))}
          </div>
          {!rollup && <div style={{ fontSize: 13, color: S.text3, textAlign: 'center', padding: '20px 0' }}>Loading…</div>}
          {rollup && rollup.role === 'officer' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {rollup.regions?.length === 0 && <div style={{ fontSize: 13, color: S.text3 }}>No watched regions yet.</div>}
              {rollup.regions?.map(({ region, latest_alert }) => (
                <div key={region.id} style={{ padding: '8px 10px', background: S.surface2, border: `1px solid ${S.border}` }}>
                  <div style={{ fontSize: 13, color: S.text, fontFamily: S.mono }}>{region.name}</div>
                  <div style={{ fontSize: 12, color: latest_alert ? bandColor(latest_alert.risk_score >= 75 ? 'severe' : latest_alert.risk_score >= 55 ? 'high' : latest_alert.risk_score >= 30 ? 'moderate' : 'low') : S.text3 }}>
                    {latest_alert ? `Risk ${latest_alert.risk_score}/100` : 'No alert yet'}
                  </div>
                </div>
              ))}
            </div>
          )}
          {rollup && rollup.role === 'district' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontSize: 12, color: S.text3, fontFamily: S.mono }}>
                {rollup.total_regions} regions · {rollup.regions_with_data} scored
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {Object.entries(rollup.band_counts || {}).map(([band, count]) => (
                  <div key={band} style={{ padding: '4px 8px', fontSize: 12, fontFamily: S.mono, border: `1px solid ${bandColor(band)}`, color: bandColor(band) }}>
                    {band}: {count}
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 12, color: S.text3, fontFamily: S.mono, letterSpacing: 1 }}>TOP RISK REGIONS</div>
              {(rollup.top_risk_regions || []).map((r, i) => (
                <div key={i} style={{ fontSize: 13, color: S.text2, display: 'flex', justifyContent: 'space-between' }}>
                  <span>{r.name}</span><span>{r.risk_score}</span>
                </div>
              ))}
            </div>
          )}
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
