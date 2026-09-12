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
 *
 * Y-axis tick labels, several evenly-spaced X-axis date labels (not
 * just first/last), and a hover crosshair + tooltip showing the exact
 * point under the cursor.
 */

import { useState, useRef, useCallback } from 'react';

const S = {
  surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text3: 'rgba(255,255,255,0.6)', accent: '#7eb8d4', mono: "'JetBrains Mono','Courier New',monospace",
};

const CHART_W = 640;
const Y_AXIS_W = 52;   // left gutter reserved for Y tick labels
const X_AXIS_H = 18;   // bottom gutter reserved for X tick labels
const N_X_TICKS = 6;
const N_Y_TICKS = 4;

export default function SparkChart({ points, color = S.accent, height = 150, formatX, formatY, emptyLabel = 'No data available for this range.' }) {
  const [hoverIdx, setHoverIdx] = useState(null);
  const svgRef = useRef(null);

  const valid = (points || []).filter(p => p.value != null && !Number.isNaN(p.value));

  const fx = formatX || ((d) => String(d));
  const fy = formatY || ((v) => v.toFixed(2));

  const onMove = useCallback((e) => {
    if (!svgRef.current || valid.length < 2) return;
    const rect = svgRef.current.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const plotX = relX - Y_AXIS_W;
    const plotW = CHART_W - Y_AXIS_W - 10;
    const frac = Math.min(1, Math.max(0, plotX / plotW));
    const idx = Math.round(frac * (valid.length - 1));
    setHoverIdx(Math.min(valid.length - 1, Math.max(0, idx)));
  }, [valid.length]);

  const onLeave = useCallback(() => setHoverIdx(null), []);

  if (valid.length < 2) {
    return (
      <div style={{ fontSize: 12, color: S.text3, padding: '20px 0', textAlign: 'center', background: S.surface2, borderRadius: 6, height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {emptyLabel}
      </div>
    );
  }

  const w = CHART_W, h = height, padR = 10, topPad = 12, botPad = X_AXIS_H;
  const plotLeft = Y_AXIS_W, plotRight = w - padR, plotW = plotRight - plotLeft;

  const values = valid.map(p => p.value);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  const spread = Math.max(dataMax - dataMin, Math.abs(dataMax) * 0.02, 0.01);
  const yMin = dataMin - spread * 0.12;
  const yMax = dataMax + spread * 0.12;
  const xStep = plotW / (valid.length - 1);
  const toX = (i) => plotLeft + i * xStep;
  const toY = (v) => h - botPad - ((v - yMin) / (yMax - yMin || 1)) * (h - botPad - topPad);

  let path = '';
  let areaPath = '';
  valid.forEach((p, i) => {
    const x = toX(i), y = toY(p.value);
    path += i === 0 ? `M ${x} ${y} ` : `L ${x} ${y} `;
    areaPath += i === 0 ? `M ${x} ${h - botPad} L ${x} ${y} ` : `L ${x} ${y} `;
  });
  areaPath += `L ${toX(valid.length - 1)} ${h - botPad} Z`;

  const first = valid[0].value;
  const last = valid[valid.length - 1].value;
  const delta = last - first;
  const deltaPct = first !== 0 ? (delta / Math.abs(first)) * 100 : 0;
  const deltaColor = delta > 0 ? '#2ecc71' : delta < 0 ? '#ff7a45' : S.text3;
  const gradId = `spark-fill-${Math.round(Math.random() * 1e9)}`;

  // Y-axis ticks — evenly spaced across the value range, formatted the
  // same way as the hover tooltip so the axis and the tooltip always agree.
  const yTicks = Array.from({ length: N_Y_TICKS + 1 }, (_, i) => yMin + (i / N_Y_TICKS) * (yMax - yMin));

  // X-axis ticks — evenly spaced by INDEX (not by date), so this works
  // uniformly whether points are daily (commodities), monthly (FRED),
  // or yearly (World Bank) without needing to know the cadence.
  const xTickCount = Math.min(N_X_TICKS, valid.length);
  const xTickIndices = Array.from({ length: xTickCount }, (_, i) =>
    Math.round((i / (xTickCount - 1)) * (valid.length - 1))
  );

  const hp = hoverIdx != null ? valid[hoverIdx] : null;
  const hx = hoverIdx != null ? toX(hoverIdx) : null;
  const hy = hoverIdx != null ? toY(hp.value) : null;
  // Flip the tooltip to the left side of the cursor once it'd otherwise
  // run past the right edge of the chart.
  const tooltipOnLeft = hx != null && hx > plotLeft + plotW * 0.68;

  return (
    <div style={{ background: S.surface2, borderRadius: 6, padding: '10px 6px 4px', position: 'relative' }}>
      <svg ref={svgRef} width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
        style={{ display: 'block', overflow: 'visible', cursor: 'crosshair' }}
        onMouseMove={onMove} onMouseLeave={onLeave}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.4" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Y gridlines + tick labels */}
        {yTicks.map((v, i) => {
          const y = toY(v);
          return (
            <g key={i}>
              <line x1={plotLeft} y1={y} x2={plotRight} y2={y} stroke={S.border} strokeWidth={1} strokeDasharray={i === 0 ? undefined : '2,3'} />
              <text x={plotLeft - 8} y={y} textAnchor="end" dominantBaseline="middle"
                fontFamily={S.mono} fontSize={9.5} fill={S.text3}>
                {fy(v)}
              </text>
            </g>
          );
        })}

        <path d={areaPath} fill={`url(#${gradId})`} stroke="none" />
        <path d={path} fill="none" stroke={color} strokeWidth={2.2} strokeLinejoin="round" strokeLinecap="round" />

        {valid.length <= 60 && valid.map((p, i) => (
          <circle key={i} cx={toX(i)} cy={toY(p.value)} r={2.3} fill="#0d1117" stroke={color} strokeWidth={1.6} />
        ))}

        {/* X-axis tick labels */}
        {xTickIndices.map((idx, i) => (
          <text key={i} x={toX(idx)} y={h - 4} textAnchor={i === 0 ? 'start' : i === xTickIndices.length - 1 ? 'end' : 'middle'}
            fontFamily={S.mono} fontSize={9.5} fill={S.text3}>
            {fx(valid[idx].date)}
          </text>
        ))}

        {/* Hover crosshair + highlighted point */}
        {hp && (
          <>
            <line x1={hx} y1={topPad} x2={hx} y2={h - botPad} stroke={S.border2} strokeWidth={1} />
            <circle cx={hx} cy={hy} r={4} fill={color} stroke="#0d1117" strokeWidth={1.5} />
          </>
        )}
      </svg>

      {/* HTML tooltip (crisper text than SVG at small sizes) — positioned
          from the hovered point's fraction of the chart width, flips to
          the point's left near the right edge so it never clips off. */}
      {hp && (
        <div style={{
          position: 'absolute', top: 6,
          left: tooltipOnLeft ? undefined : `calc(${(hx / w) * 100}% + 10px)`,
          right: tooltipOnLeft ? `calc(${100 - (hx / w) * 100}% + 10px)` : undefined,
          background: 'rgba(5,7,12,0.95)', border: `1px solid ${S.border2}`, borderRadius: 4,
          padding: '5px 8px', pointerEvents: 'none', whiteSpace: 'nowrap',
          fontFamily: S.mono, fontSize: 10.5, color: S.text, zIndex: 2,
          boxShadow: '0 4px 14px rgba(0,0,0,0.4)',
        }}>
          <div style={{ color: S.text3, marginBottom: 2 }}>{fx(hp.date)}</div>
          <div style={{ color, fontWeight: 700 }}>{fy(hp.value)}</div>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: S.text3, fontFamily: S.mono, marginTop: 2, paddingLeft: Y_AXIS_W - 6 }}>
        <span />
        <span style={{ color: deltaColor }}>
          {delta > 0 ? '▲' : delta < 0 ? '▼' : '–'} {fy(Math.abs(delta))} ({deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(1)}%)
        </span>
      </div>
    </div>
  );
}
