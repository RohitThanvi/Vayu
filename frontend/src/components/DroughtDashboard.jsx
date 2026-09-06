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
 * lightweight drought-stress trend series. Both the headline score's 3
 * GEE calls and the trend's N GEE calls now run concurrently (see
 * risk_scoring.py) rather than sequentially — this used to take several
 * minutes even for a small AOI.
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

// Punchier, higher-saturation band colors than a first pass used —
// these need to read clearly at a glance against the dark surface, not
// just be "on brand." Each also gets a glow variant for the gauge/card.
const BAND_COLOR = { low: '#2ecc71', moderate: '#f0b429', high: '#ff7a45', severe: '#ff3b3b' };
const BAND_LABEL = { low: 'Low Risk', moderate: 'Moderate Risk', high: 'High Risk', severe: 'Severe Risk' };
function bandColor(band) { return BAND_COLOR[band] || S.text3; }
function bandGlow(band) { const c = bandColor(band); return `${c}33`; }   // ~20% alpha for glows/fills

// Small inline icons — kept local rather than importing App.jsx's Icon()
// (that component isn't exported), same "each file owns its tiny SVG
// icon set" pattern CommodityTicker.jsx already uses.
const ICONS = {
  drought: <path d="M12 2C8 7 5 11 5 15a7 7 0 0 0 14 0c0-4-3-8-7-13Z" />,
  vegetation: <path d="M12 22V12M12 12C12 7 8 4 4 4c0 5 3 9 8 9Zm0 0c0-5 4-8 8-8 0 5-3 9-8 9Z" />,
  moisture: <path d="M12 3s6 7 6 11a6 6 0 0 1-12 0c0-4 6-11 6-11Z" />,
};
function IndicatorIcon({ name, color, size = 15 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name]}
    </svg>
  );
}

// ── Hero gauge: 0-100 composite score as a glowing arc, band-colored ───────
function ScoreGauge({ score, band }) {
  const color = bandColor(band);
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const r = 50, cx = 70, cy = 70;
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
  const gradId = `gauge-grad-${band}`;

  return (
    <svg width={140} height={140} viewBox="0 0 140 140">
      <defs>
        <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={color} stopOpacity="0.55" />
          <stop offset="100%" stopColor={color} stopOpacity="1" />
        </linearGradient>
        <filter id={`glow-${band}`} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <path d={`M ${bgx1} ${bgy1} A ${r} ${r} 0 1 1 ${bgx2} ${bgy2}`} fill="none" stroke={S.surface2} strokeWidth={11} strokeLinecap="round" />
      {score != null && (
        <path d={`M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`} fill="none" stroke={`url(#${gradId})`}
          strokeWidth={11} strokeLinecap="round" filter={`url(#glow-${band})`} />
      )}
      <text x={cx} y={cy - 1} textAnchor="middle" fontSize={34} fontWeight={800} fontFamily={S.mono} fill={score != null ? S.text : S.text3}>
        {score != null ? Math.round(score) : '--'}
      </text>
      <text x={cx} y={cy + 20} textAnchor="middle" fontSize={10} letterSpacing={1.5} fontFamily={S.mono} fill={S.text3}>
        / 100
      </text>
    </svg>
  );
}

// ── Trend chart: drought-affected % over recent checkpoints, gradient fill ─
function TrendChart({ points }) {
  const valid = points.filter(p => p.drought_affected_pct != null);
  if (valid.length < 2) {
    return (
      <div style={{ fontSize: 12, color: S.text3, padding: '24px 0', textAlign: 'center', background: S.surface2, borderRadius: 6 }}>
        Not enough cloud-free imagery across this window to plot a trend yet.
      </div>
    );
  }
  const w = 260, h = 110, pad = 8, topPad = 14;
  // Fixed to a hardcoded 0-10 floor before — for real drought_affected_pct
  // values (often single-digit, sometimes under 1%), that crushed a
  // genuine 0.4-0.9% swing into ~5% of the chart's vertical range,
  // reading as a flat/static line even when the underlying data was
  // moving. Scale to the ACTUAL observed min/max instead (with headroom
  // so a flat real series still shows as a visible band, not a
  // degenerate zero-height line).
  const rawValues = valid.map(p => p.drought_affected_pct);
  const dataMin = Math.min(...rawValues);
  const dataMax = Math.max(...rawValues);
  const spread = Math.max(dataMax - dataMin, 0.4);
  const yMin = Math.max(0, dataMin - spread * 0.25);
  const yMax = dataMax + spread * 0.25;
  const xStep = (w - pad * 2) / (points.length - 1);
  const toY = (v) => h - pad - ((v - yMin) / (yMax - yMin)) * (h - pad - topPad);

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
  const deltaColor = delta > 0 ? '#ff7a45' : delta < 0 ? '#2ecc71' : S.text3;

  return (
    <div style={{ background: S.surface2, borderRadius: 6, padding: '12px 12px 10px' }}>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={S.accent} stopOpacity="0.45" />
            <stop offset="100%" stopColor={S.accent} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map(f => (
          <line key={f} x1={pad} y1={topPad + f * (h - pad - topPad)} x2={w - pad} y2={topPad + f * (h - pad - topPad)} stroke={S.border} strokeWidth={1} strokeDasharray="2,3" />
        ))}
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke={S.border2} strokeWidth={1} />
        <path d={areaPath.trim() ? `${areaPath} L ${pad + xStep * (points.length - 1)} ${h - pad} Z` : ''} fill="url(#trend-fill)" stroke="none" />
        <path d={path} fill="none" stroke={S.accent} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => p.drought_affected_pct == null ? null : (
          <circle key={i} cx={pad + i * xStep} cy={toY(p.drought_affected_pct)} r={3} fill={S.bg} stroke={S.accent} strokeWidth={2} />
        ))}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: S.text3, fontFamily: S.mono, marginTop: 4 }}>
        <span>{points[0]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
      <div style={{ fontSize: 12, color: S.text2, marginTop: 8 }}>
        Drought-affected area:{' '}
        <span style={{ fontWeight: 700, color: S.accent, fontSize: 14 }}>{latest.toFixed(1)}%</span>
        {' '}
        <span style={{ color: deltaColor, fontWeight: 600 }}>
          ({delta > 0 ? '+' : ''}{delta.toFixed(1)}pt vs {points[0]?.date})
        </span>
      </div>
    </div>
  );
}

// ── Composition donut: each indicator's share of total risk contribution ──
function CompositionDonut({ subScores }) {
  const rows = [
    { key: 'drought', label: 'Drought', color: '#3aa0ff' },
    { key: 'vegetation_loss', label: 'Vegetation', color: '#2ecc71' },
    { key: 'moisture_deficit', label: 'Moisture', color: '#f0b429' },
  ].map(r => ({ ...r, val: subScores?.[r.key] ?? 0 }));
  const total = rows.reduce((s, r) => s + r.val, 0);

  if (total <= 0) {
    return (
      <div style={{ fontSize: 12, color: S.text3, padding: '16px 0', textAlign: 'center' }}>
        No contributing signal to break down yet.
      </div>
    );
  }

  const cx = 46, cy = 46, r = 34, strokeW = 16;
  const circumference = 2 * Math.PI * r;
  let cumulative = 0;
  const segments = rows.filter(row => row.val > 0).map(row => {
    const frac = row.val / total;
    const dash = frac * circumference;
    const seg = { ...row, frac, dashArray: `${dash} ${circumference - dash}`, dashOffset: -cumulative };
    cumulative += dash;
    return seg;
  });

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
      <svg width={92} height={92} viewBox="0 0 92 92" style={{ flexShrink: 0, transform: 'rotate(-90deg)' }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={S.surface2} strokeWidth={strokeW} />
        {segments.map(seg => (
          <circle key={seg.key} cx={cx} cy={cy} r={r} fill="none" stroke={seg.color} strokeWidth={strokeW}
            strokeDasharray={seg.dashArray} strokeDashoffset={seg.dashOffset} strokeLinecap="butt" />
        ))}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7, flex: 1 }}>
        {rows.map(row => (
          <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11.5, color: S.text2 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: row.color, display: 'inline-block', flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{row.label}</span>
            <span style={{ fontFamily: S.mono, fontWeight: 700, color: S.text }}>{total > 0 ? Math.round((row.val / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Sub-score breakdown: icon + label + gradient bar, one per indicator ────
function SubScoreBars({ subScores, inputsFailed }) {
  const rows = [
    { key: 'drought', label: 'Drought Stress', icon: 'drought' },
    { key: 'vegetation_loss', label: 'Vegetation Loss', icon: 'vegetation' },
    { key: 'moisture_deficit', label: 'Moisture Deficit', icon: 'moisture' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {rows.map(({ key, label, icon }) => {
        const val = subScores?.[key];
        const failed = inputsFailed?.includes(key);
        const barColor = val == null ? S.border2 : val >= 55 ? '#ff7a45' : val >= 30 ? '#f0b429' : '#2ecc71';
        return (
          <div key={key}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 12, color: S.text2, marginBottom: 5 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <IndicatorIcon name={icon} color={val == null ? S.text3 : barColor} />
                {label}
              </span>
              <span style={{ fontFamily: S.mono, fontWeight: 700, color: failed ? S.text3 : barColor }}>
                {failed ? 'NO DATA' : val != null ? Math.round(val) : '—'}
              </span>
            </div>
            <div style={{ height: 7, background: S.surface2, borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${val ?? 0}%`, background: barColor, borderRadius: 4, transition: 'width 0.4s ease', boxShadow: val ? `0 0 8px ${barColor}88` : 'none' }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SectionHeader({ children }) {
  return (
    <div style={{ fontSize: 11, fontFamily: S.mono, letterSpacing: 1.8, textTransform: 'uppercase', color: S.text3, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ width: 3, height: 12, background: S.accent, borderRadius: 2, display: 'inline-block' }} />
      {children}
    </div>
  );
}

export default function DroughtDashboard({ drawnAOI, apiUrl, searchedRegionName, onClose, isMobile }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [fetchedAt, setFetchedAt] = useState(null);

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
      setFetchedAt(new Date());
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
  const glow = current ? bandGlow(current.band) : null;

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: `linear-gradient(180deg, ${S.bg} 0%, #0b0e13 100%)`, borderLeft: `1px solid ${S.border}`, overflowY: 'auto' }}>
      <div style={{ padding: '16px 18px', borderBottom: `1px solid ${S.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 13, fontFamily: S.mono, letterSpacing: 2.5, textTransform: 'uppercase', color: S.text, fontWeight: 700 }}>Drought Monitoring</div>
          <div style={{ fontSize: 11, color: S.text3, marginTop: 3 }}>{searchedRegionName || (drawnAOI ? 'Selected AOI' : 'No AOI selected')}</div>
        </div>
        {isMobile && onClose && (
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: S.text3, fontSize: 22, cursor: 'pointer', padding: 4, lineHeight: 1 }}>×</button>
        )}
      </div>

      <div style={{ flex: 1, padding: '18px', display: 'flex', flexDirection: 'column', gap: 22 }}>
        {!drawnAOI && (
          <div style={{ fontSize: 13, color: S.text3, lineHeight: 1.7, padding: '8px 2px' }}>
            Draw an AOI on the map, or search a place, to see drought severity, trend, and contributing indicators here — auto-updates whenever the AOI changes.
          </div>
        )}

        {drawnAOI && loading && !data && (
          <div style={{ fontSize: 13, color: S.accent, textAlign: 'center', padding: '40px 0', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 28, height: 28, border: `3px solid ${S.border}`, borderTopColor: S.accent, borderRadius: '50%', animation: 'vayu-spin 0.8s linear infinite' }} />
            Analyzing satellite data...
            <style>{'@keyframes vayu-spin { to { transform: rotate(360deg); } }'}</style>
          </div>
        )}

        {error && (
          <div style={{ background: 'rgba(255,59,59,0.08)', border: '1px solid rgba(255,59,59,0.35)', borderRadius: 6, padding: '10px 12px', fontSize: 13, color: S.text2 }}>
            {error}
          </div>
        )}

        {current && (
          <>
            {/* Hero: glowing gauge + band + confidence, band-tinted card.
                Stacked vertically (not side-by-side) — a 290px sidebar
                minus gauge width left so little room for the reason
                paragraph that it was wrapping one word per line; a
                narrow panel needs a vertical card layout here, not a
                horizontal one. */}
            <div style={{
              background: `linear-gradient(160deg, ${glow} 0%, ${S.surface} 65%)`,
              border: `1px solid ${bandColor(current.band)}55`, borderRadius: 8, padding: '20px 18px',
              boxShadow: `0 0 24px ${glow}`,
              display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center',
            }}>
              <ScoreGauge score={current.risk_score} band={current.band} />
              <div style={{
                fontSize: 16, fontFamily: S.mono, letterSpacing: 1.5, textTransform: 'uppercase',
                color: bandColor(current.band), fontWeight: 800, marginTop: 6,
              }}>
                {BAND_LABEL[current.band] || current.band}
              </div>
              <div style={{ fontSize: 11, color: S.text3, marginTop: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: current.confidence >= 70 ? '#2ecc71' : current.confidence >= 40 ? '#f0b429' : '#ff7a45', display: 'inline-block' }} />
                Confidence: {current.confidence}%
              </div>
              <div style={{ fontSize: 12.5, color: S.text2, marginTop: 12, lineHeight: 1.6, textAlign: 'left', width: '100%', borderTop: `1px solid ${S.border}`, paddingTop: 12 }}>{current.reason}</div>
            </div>

            {/* Trend chart */}
            <div>
              <SectionHeader>Drought Stress Trend</SectionHeader>
              {data.trend_error && !data.trend?.length && (
                <div style={{ fontSize: 12, color: S.text3 }}>Trend unavailable this time — {data.trend_error}</div>
              )}
              {data.trend?.length > 0 && <TrendChart points={data.trend} />}
            </div>

            {/* Component breakdown */}
            <div>
              <SectionHeader>Contributing Indicators</SectionHeader>
              <div style={{ background: S.surface2, borderRadius: 6, padding: '16px 14px' }}>
                <CompositionDonut subScores={current.sub_scores} />
                <div style={{ height: 1, background: S.border, margin: '16px 0 14px' }} />
                <SubScoreBars subScores={current.sub_scores} inputsFailed={current.inputs_failed} />
              </div>
            </div>

            {/* Provenance / transparency footer */}
            <div style={{ fontSize: 10, color: S.text3, lineHeight: 1.7, borderTop: `1px solid ${S.border}`, paddingTop: 12 }}>
              Sources: {current.provenance?.vegetation_source}; {current.provenance?.drought_source}; {current.provenance?.moisture_source}.
              {current.inputs_failed?.length > 0 && (
                <> No coverage this period: {current.inputs_failed.join(', ')}.</>
              )}
              {fetchedAt && <div style={{ marginTop: 4 }}>Last updated {fetchedAt.toLocaleTimeString()}</div>}
            </div>

            <button onClick={fetchDashboard} disabled={loading}
              style={{
                padding: '11px', fontSize: 12, fontFamily: S.mono, letterSpacing: 1.8, textTransform: 'uppercase',
                background: loading ? S.surface2 : 'rgba(126,184,212,0.12)',
                border: `1px solid ${loading ? S.border : S.accent}`, borderRadius: 4,
                color: loading ? S.text3 : S.accent,
                cursor: loading ? 'not-allowed' : 'pointer',
                fontWeight: 700,
              }}>
              {loading ? 'REFRESHING...' : 'REFRESH'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
