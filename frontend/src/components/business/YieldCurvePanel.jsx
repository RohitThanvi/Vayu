/** US Treasury yield curve: today vs one month and one year ago, with the 10Y-2Y / 10Y-3M spreads. Descriptive only. */
import SparkChart from '../SparkChart';
import { S, useJson, num, exportUrl, btn } from './theme';
import { Panel, DataStamp, Empty } from './ui';

const LINES = [['latest', 'Latest', 'var(--vayu-accent)'], ['one_month_ago', '1 month ago', 'var(--vayu-gold)'], ['one_year_ago', '1 year ago', 'var(--vayu-text3)']];

function CurveChart({ tenors, curves }) {
  const W = 420, H = 170, P = { l: 34, r: 10, t: 10, b: 22 };
  const all = LINES.flatMap(([k]) => tenors.map((t) => curves[k]?.[t])).filter((v) => v != null);
  if (!all.length) return <Empty>No curve data.</Empty>;
  const lo = Math.floor(Math.min(...all) * 4) / 4, hi = Math.ceil(Math.max(...all) * 4) / 4, rng = hi - lo || 1;
  const x = (i) => P.l + (i / Math.max(1, tenors.length - 1)) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - (v - lo) / rng) * (H - P.t - P.b);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: 520 }} role="img" aria-label="Yield curve">
      {[lo, (lo + hi) / 2, hi].map((g) => (
        <g key={g}><line x1={P.l} x2={W - P.r} y1={y(g)} y2={y(g)} stroke="var(--vayu-border)" strokeDasharray="2 3" /><text x={P.l - 4} y={y(g) + 3} fontSize="9" textAnchor="end" fill="var(--vayu-text3)">{g.toFixed(2)}</text></g>
      ))}
      {tenors.map((t, i) => <text key={t} x={x(i)} y={H - 6} fontSize="9" textAnchor="middle" fill="var(--vayu-text3)">{t}</text>)}
      {LINES.map(([k, , color]) => {
        let d = '', pen = false;
        tenors.forEach((t, i) => { const v = curves[k]?.[t]; if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)} `; pen = true; });
        return <path key={k} d={d} fill="none" stroke={color} strokeWidth={k === 'latest' ? 2 : 1.3} strokeDasharray={k === 'one_year_ago' ? '4 3' : undefined} />;
      })}
    </svg>
  );
}

export default function YieldCurvePanel({ apiUrl }) {
  const { data, error } = useJson(apiUrl, '/api/v1/intel/yield-curve', 30 * 60 * 1000);
  const hist = (data?.spread_10y_2y_history || []).map((p) => ({ date: p.date, value: p.value }));
  const sp = data?.spreads || {};
  const badge = (s) => (s?.value == null ? '—' : `${num(s.value)} pp${s.inverted ? ' · inverted' : ''}`);
  return (
    <Panel title="US Treasury yield curve" right={<DataStamp quality={data?.data_quality} />} style={{ margin: '10px 14px' }}>
      {!data && !error && <Empty>Loading…</Empty>}
      {(error && !data) || data?.error ? <Empty>Yield curve is unavailable right now.</Empty> : null}
      {data?.curves && (
        <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 420px' }}>
            <CurveChart tenors={data.tenors} curves={data.curves} />
            <div style={{ display: 'flex', gap: 12, fontSize: 10, color: S.text3 }}>{LINES.map(([k, label, c]) => <span key={k}><span style={{ color: c }}>━</span> {label}</span>)}</div>
          </div>
          <div style={{ flex: '1 1 260px' }}>
            {[['10Y − 2Y', sp['10y_minus_2y']], ['10Y − 3M', sp['10y_minus_3m']]].map(([label, s]) => (
              <div key={label} style={{ display: 'flex', justifyContent: 'space-between', padding: '5px 0', borderBottom: `1px solid ${S.border}` }}>
                <span style={{ fontSize: 11.5, color: S.text2 }}>{label}</span>
                <span style={{ fontFamily: S.mono, fontSize: 12, color: s?.inverted ? '#e0c23c' : S.text }}>{badge(s)}</span>
              </div>
            ))}
            <div style={{ fontSize: 10, color: S.text3, margin: '8px 0 2px' }}>10Y − 2Y spread, last ~13 months</div>
            <SparkChart points={hist} height={90} formatX={(d) => new Date(d).toLocaleDateString(undefined, { month: 'short', year: '2-digit' })} formatY={(v) => num(v)} emptyLabel="—" />
            <div style={{ marginTop: 8 }}><a href={exportUrl(apiUrl, 'yield-curve')} style={btn} download>Yield curve CSV</a></div>
          </div>
        </div>
      )}
      {data?.curves && <div style={{ fontSize: 9.5, color: S.text3, marginTop: 8, lineHeight: 1.5 }}>As of {data.as_of}. “Inverted” means the longer-dated yield is below the shorter-dated one. This is a description of the curve — no recession probability or other forecast is computed.</div>}
    </Panel>
  );
}
