/**
 * DroughtDashboard.jsx
 * The "dedicated graphs/charts dashboard" that replaces the Intel Feed
 * panel (right side) while the Agri sidebar tab is active — App.jsx
 * swaps the two purely by conditional render on `tab`, so leaving Agri
 * automatically restores Intel Feed with no extra cleanup needed.
 *
 * This is deliberately the VISUAL/analytical companion to AgriPanel's
 * existing "Risk Score" view in the left sidebar, not a duplicate of
 * it — AgriPanel stays the place for actions (run score, save to
 * watchlist, download report, view district rollup); this panel is
 * charts only, and fetches automatically whenever the AOI changes
 * rather than waiting for a button press, since that's the point of a
 * standing dashboard vs. an on-demand report.
 *
 * Backend: POST /api/v1/agri/drought-dashboard — a thin orchestration
 * endpoint that reuses the exact same compute_risk_score() the sidebar's
 * button already calls (same number, same explainability), plus a new
 * lightweight drought-stress trend series (see risk_scoring.
 * compute_drought_trend's docstring for why the trend uses a cheaper
 * single-source computation than the headline score, and why a failed
 * checkpoint appears as a genuine gap rather than being silently
 * dropped or defaulted).
 *
 * No new chart library dependency — custom SVG (matches this project's
 * existing pattern elsewhere: OrbitalGlobe, CommodityTicker's icons,
 * the Icon() component) rather than pulling in recharts/chart.js for
 * one panel's worth of charts.
 */

import { useState, useEffect, useCallback } from 'react';

const S = {
  bg: '#0a0c0f', surface: '#0d1117', surface2: '#0f1419',
  border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4',
  mono: "'JetBrains Mono','Courier New',monospace",
};

const BAND_COLOR = { low: '#4a7c59', moderate: '#c9933a', high: '#c96a3a', severe: '#8b2020' };
const BAND_LABEL = { low: 'Low Risk', moderate: 'Moderate Risk', high: 'High Risk', severe: 'Severe Risk' };
function bandColor(band) { return BAND_COLOR[band] || S.text3; }

// ── Hero gauge: 0-100 composite score as an arc, band-colored ──────────────
function ScoreGauge({ score, band }) {
  const color = bandColor(band);
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const r = 54, cx = 64, cy = 64;
  const startAngle = 135;
  const sweep = 270 * pct;
  const toXY = (deg) => {
    const rad = (deg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
  };
  const [x1, y1] = toXY(startAngle);
  const [x2, y2] = toXY(startAngle + sweep);
  const largeArc = sweep > 180 ? 1 : 0;
  const [bgx1, bgy1] = toXY(startAngle);
  const [bgx2, bgy2] = toXY(startAngle + 270);

  return (
    <svg width={128} height={128} viewBox="0 0 128 128">
      <path d={`M ${bgx1} ${bgy1} A ${r} ${r} 0 1 1 ${bgx2} ${bgy2}`} fill="none" stroke={S.border} strokeWidth={10} strokeLinecap="round" />
      {score != null && (
        <path d={`M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`} fill="none" stroke={color} strokeWidth={10} strokeLinecap="round" />
      )}
      <text x={cx} y={cy - 2} textAnchor="middle" fontSize={30} fontWeight={700} fontFamily={S.mono} fill={score != null ? color : S.text3}>
        {score != null ? Math.round(score) : '--'}
      </text>
      <text x={cx} y={cy + 18} textAnchor="middle" fontSize={10} letterSpacing={1} fontFamily={S.mono} fill={S.text3}>
        / 100
      </text>
    </svg>
  );
}

// ── Trend chart: drought-affected % over recent checkpoints ────────────────
function TrendChart({ points }) {
  const valid = points.filter(p => p.drought_affected_pct != null);
  if (valid.length < 2) {
    return (
      <div style={{ fontSize: 12, color: S.text3, padding: '20px 0', textAlign: 'center' }}>
        Not enough cloud-free imagery across this window to plot a trend yet.
      </div>
    );
  }
  const w = 260, h = 90, pad = 8;
  const maxVal = Math.max(10, ...valid.map(p => p.drought_affected_pct));
  const xStep = (w - pad * 2) / (points.length - 1);
  const toY = (v) => h - pad - (v / maxVal) * (h - pad * 2);

  // Build the path with gaps at missing (null) checkpoints, rather than
  // interpolating across them — an honest break in the line, not a
  // guessed value standing in for missing data.
  let path = '';
  let areaPath = '';
  let drawing = false;
  points.forEach((p, i) => {
    const x = pad + i * xStep;
    if (p.drought_affected_pct == null) { drawing = false; return; }
    const y = toY(p.drought_affected_pct);
    if (!drawing) {
      path += `M ${x} ${y} `;
      areaPath += `M ${x} ${h - pad} L ${x} ${y} `;
      drawing = true;
    } else {
      path += `L ${x} ${y} `;
      areaPath += `L ${x} ${y} `;
    }
  });

  const latest = valid[valid.length - 1].drought_affected_pct;
  const earliest = valid[0].drought_affected_pct;
  const delta = latest - earliest;

  return (
    <div>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block' }}>
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke={S.border} strokeWidth={1} />
        <path d={areaPath.trim() ? `${areaPath} L ${pad + xStep * (points.length - 1)} ${h - pad} Z` : ''} fill="rgba(126,184,212,0.12)" stroke="none" />
        <path d={path} fill="none" stroke={S.accent} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => p.drought_affected_pct == null ? null : (
          <circle key={i} cx={pad + i * xStep} cy={toY(p.drought_affected_pct)} r={2.5} fill={S.accent} />
        ))}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: S.text3, fontFamily: S.mono, marginTop: 2 }}>
        <span>{points[0]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
      <div style={{ fontSize: 12, color: S.text2, marginTop: 6 }}>
        Drought-affected area:{' '}
        <span style={{ fontWeight: 700, color: S.accent }}>{latest.toFixed(1)}%</span>
        {' '}
        <span style={{ color: delta > 0 ? '#c96a3a' : delta < 0 ? '#4a7c59' : S.text3 }}>
          ({delta > 0 ? '+' : ''}{delta.toFixed(1)}pt vs {points[0]?.date})
        </span>
      </div>
    </div>
  );
}

// ── Sub-score breakdown: horizontal bars, one per contributing indicator ───
function SubScoreBars({ subScores, inputsFailed }) {
  const rows = [
    { key: 'drought', label: 'Drought Stress' },
    { key: 'vegetation_loss', label: 'Vegetation Loss' },
    { key: 'moisture_deficit', label: 'Moisture Deficit' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {rows.map(({ key, label }) => {
        const val = subScores?.[key];
        const failed = inputsFailed?.includes(key);
        const barColor = val == null ? S.border2 : val >= 55 ? '#c96a3a' : val >= 30 ? '#c9933a' : '#4a7c59';
        return (
          <div key={key}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: S.text3, marginBottom: 3 }}>
              <span>{label}</span>
              <span>{failed ? 'no data' : val != null ? `${Math.round(val)}` : '—'}</span>
            </div>
            <div style={{ height: 6, background: S.surface2, borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${val ?? 0}%`, background: barColor, transition: 'width 0.3s ease' }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function DroughtDashboard({ drawnAOI, apiUrl, searchedRegionName, onClose, isMobile }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchDashboard = useCallback(async () => {
    if (!drawnAOI) return;
    setLoading(true); setError(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/agri/drought-dashboard`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aoi_geojson: drawnAOI }),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || 'Drought dashboard failed');
      setData(await resp.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [drawnAOI, apiUrl]);

  // Auto-fetch whenever the AOI changes — this panel is a standing
  // dashboard, not an on-demand report (that's what the sidebar's "Run
  // Risk Score" button is for), so it shouldn't need a manual trigger.
  useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  const current = data?.current;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: S.bg, borderLeft: `1px solid ${S.border}`, overflowY: 'auto' }}>
      <div style={{ padding: '14px 16px', borderBottom: `1px solid ${S.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 13, fontFamily: S.mono, letterSpacing: 2, textTransform: 'uppercase', color: S.text }}>Drought Monitoring</div>
          <div style={{ fontSize: 11, color: S.text3, marginTop: 2 }}>{searchedRegionName || (drawnAOI ? 'Selected AOI' : 'No AOI selected')}</div>
        </div>
        {isMobile && onClose && (
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: S.text3, fontSize: 20, cursor: 'pointer', padding: 4 }}>×</button>
        )}
      </div>

      <div style={{ flex: 1, padding: '16px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {!drawnAOI && (
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.6 }}>
            Draw an AOI on the map, or search a place, to see drought severity, trend, and contributing indicators here — auto-updates whenever the AOI changes.
          </div>
        )}

        {drawnAOI && loading && !data && (
          <div style={{ fontSize: 13, color: S.text3, textAlign: 'center', padding: '30px 0' }}>Analyzing satellite data...</div>
        )}

        {error && (
          <div style={{ background: 'rgba(139,32,32,0.08)', border: '1px solid rgba(139,32,32,0.3)', padding: '9px 11px', fontSize: 13, color: S.text2 }}>
            {error}
          </div>
        )}

        {current && (
          <>
            {/* Hero: gauge + band + confidence */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, background: S.surface, border: `1px solid ${bandColor(current.band)}`, borderRadius: 4, padding: '14px' }}>
              <ScoreGauge score={current.risk_score} band={current.band} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase', color: bandColor(current.band), fontWeight: 700 }}>
                  {BAND_LABEL[current.band] || current.band}
                </div>
                <div style={{ fontSize: 11, color: S.text3, marginTop: 4 }}>Confidence: {current.confidence}%</div>
                <div style={{ fontSize: 12, color: S.text2, marginTop: 8, lineHeight: 1.5 }}>{current.reason}</div>
              </div>
            </div>

            {/* Trend chart */}
            <div>
              <div style={{ fontSize: 11, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase', color: S.text3, marginBottom: 8 }}>
                Drought Stress Trend
              </div>
              {data.trend_error && !data.trend?.length && (
                <div style={{ fontSize: 12, color: S.text3 }}>Trend unavailable this time — {data.trend_error}</div>
              )}
              {data.trend?.length > 0 && <TrendChart points={data.trend} />}
            </div>

            {/* Component breakdown */}
            <div>
              <div style={{ fontSize: 11, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase', color: S.text3, marginBottom: 8 }}>
                Contributing Indicators
              </div>
              <SubScoreBars subScores={current.sub_scores} inputsFailed={current.inputs_failed} />
            </div>

            {/* Provenance / transparency footer */}
            <div style={{ fontSize: 10, color: S.text3, lineHeight: 1.6, borderTop: `1px solid ${S.border}`, paddingTop: 10 }}>
              Sources: {current.provenance?.vegetation_source}; {current.provenance?.drought_source}; {current.provenance?.moisture_source}.
              {current.inputs_failed?.length > 0 && (
                <> No coverage this period: {current.inputs_failed.join(', ')}.</>
              )}
            </div>

            <button onClick={fetchDashboard} disabled={loading}
              style={{
                padding: '9px', fontSize: 12, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
                background: loading ? S.surface2 : 'rgba(126,184,212,0.1)',
                border: `1px solid ${loading ? S.border : S.accent}`,
                color: loading ? S.text3 : S.accent,
                cursor: loading ? 'not-allowed' : 'pointer',
              }}>
              {loading ? 'REFRESHING...' : 'REFRESH'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
