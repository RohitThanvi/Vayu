/**
 * SparkChart.jsx
 * Dependency-free SVG line chart — this project has no chart library
 * installed (recharts/chart.js/etc.) and DroughtDashboard.jsx already
 * hand-rolled one for its own trend line, so this generalizes that
 * same visual pattern (gradient fill, dashed gridlines, accent line +
 * dots) into a reusable component instead of copy-pasting a second
 * bespoke one for the Business Intelligence Analysis tab.
 *
 * Takes generic {date, value} points — `date` can be an ISO string, a
 * unix-seconds number (Yahoo's chart API), or a "YYYY" year string
 * (World Bank) — formatting is handled by the caller via `formatX`.
 */

const S = {
  surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text3: 'rgba(255,255,255,0.6)', accent: '#7eb8d4', mono: "'JetBrains Mono','Courier New',monospace",
};

export default function SparkChart({ points, color = S.accent, height = 140, formatX, formatY, emptyLabel = 'No data available for this range.' }) {
  const valid = (points || []).filter(p => p.value != null && !Number.isNaN(p.value));
  if (valid.length < 2) {
    return (
      <div style={{ fontSize: 12, color: S.text3, padding: '20px 0', textAlign: 'center', background: S.surface2, borderRadius: 6, height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {emptyLabel}
      </div>
    );
  }

  const w = 600, h = height, pad = 10, topPad = 12;
  const values = valid.map(p => p.value);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const spread = Math.max(dataMax - dataMin, Math.abs(dataMax) * 0.02, 0.01);
  const yMin = dataMin - spread * 0.15;
  const yMax = dataMax + spread * 0.15;
  const xStep = (w - pad * 2) / (valid.length - 1);
  const toY = (v) => h - pad - ((v - yMin) / (yMax - yMin || 1)) * (h - pad - topPad);

  let path = '';
  let areaPath = '';
  valid.forEach((p, i) => {
    const x = pad + i * xStep;
    const y = toY(p.value);
    path += i === 0 ? `M ${x} ${y} ` : `L ${x} ${y} `;
    areaPath += i === 0 ? `M ${x} ${h - pad} L ${x} ${y} ` : `L ${x} ${y} `;
  });
  areaPath += `L ${pad + xStep * (valid.length - 1)} ${h - pad} Z`;

  const first = valid[0].value;
  const last = valid[valid.length - 1].value;
  const delta = last - first;
  const deltaPct = first !== 0 ? (delta / Math.abs(first)) * 100 : 0;
  const deltaColor = delta > 0 ? '#2ecc71' : delta < 0 ? '#ff7a45' : S.text3;
  const gradId = `spark-fill-${Math.round(Math.random() * 1e9)}`;

  return (
    <div style={{ background: S.surface2, borderRadius: 6, padding: '10px 10px 8px' }}>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.4" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map(f => (
          <line key={f} x1={pad} y1={topPad + f * (h - pad - topPad)} x2={w - pad} y2={topPad + f * (h - pad - topPad)} stroke={S.border} strokeWidth={1} strokeDasharray="2,3" />
        ))}
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke={S.border2} strokeWidth={1} />
        <path d={areaPath} fill={`url(#${gradId})`} stroke="none" />
        <path d={path} fill="none" stroke={color} strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />
        {valid.length <= 60 && valid.map((p, i) => (
          <circle key={i} cx={pad + i * xStep} cy={toY(p.value)} r={2.3} fill="#0d1117" stroke={color} strokeWidth={1.6} />
        ))}
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: S.text3, fontFamily: S.mono, marginTop: 4 }}>
        <span>{formatX ? formatX(valid[0].date) : valid[0].date}</span>
        <span style={{ color: deltaColor }}>
          {delta > 0 ? '▲' : delta < 0 ? '▼' : '–'} {formatY ? formatY(Math.abs(delta)) : Math.abs(delta).toFixed(2)} ({deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(1)}%)
        </span>
        <span>{formatX ? formatX(valid[valid.length - 1].date) : valid[valid.length - 1].date}</span>
      </div>
    </div>
  );
}
