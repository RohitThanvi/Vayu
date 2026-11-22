import { useState, useEffect, useCallback } from 'react';
import RiskAssessmentPanel from './RiskAssessmentPanel.jsx';

// Matches the dark-terminal aesthetic used elsewhere in App.jsx (kept local
// to avoid importing the S/Icon internals across files).
const S = {
  bg: 'var(--vayu-bg)', surface: 'var(--vayu-surface)', surface2: 'var(--vayu-surface2)', border: 'var(--vayu-border)',
  text: 'var(--vayu-text)', text2: 'var(--vayu-text2)', text3: 'var(--vayu-text3)', accent: 'var(--vayu-accent)',
  mono: "'JetBrains Mono', 'Courier New', monospace",
};

const BAND_COLOR = { low: '#4a7c59', moderate: '#c9933a', high: '#c96a3a', severe: '#8b2020' };

function bandColor(band) { return BAND_COLOR[band] || S.text3; }

export default function AgriPanel({ drawnAOI, apiUrl, searchedRegionName }) {
  const [view, setView] = useState('score'); // 'score' | 'watchlist' | 'rollup'

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
      <div style={{ display: 'flex', gap: 6 }}>
        {[['score','Score'], ['watchlist','Watchlist'], ['rollup','Overview'], ['mandi','Mandi']].map(([v,label]) => (
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
            Draw an AOI on the map (or search a place) to get the agricultural risk assessment: drought stress from vegetation, rainfall, soil moisture and groundwater, combined with how vulnerable the land is, plus the original three indicators (drought extent, vegetation loss, soil moisture) in full, and one PDF report with every metric.
          </div>
          <div style={{
            fontSize: 15, fontFamily: S.mono, padding: '7px 10px', letterSpacing: 1,
            background: drawnAOI ? 'rgba(74,124,89,0.08)' : S.surface2,
            border: `1px solid ${drawnAOI ? '#4a7c59' : S.border}`,
            color: drawnAOI ? '#4a7c59' : S.text3,
          }}>
            {drawnAOI ? 'AOI DEFINED' : 'DRAW AOI ON MAP'}
          </div>
          <RiskAssessmentPanel drawnAOI={drawnAOI} apiUrl={apiUrl} regionName={searchedRegionName} />
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
