/** Small presentational components shared by the Business tab's views. */
import { S, num, UP, DOWN } from './theme';

export function Empty({ children }) {
  return <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic' }}>{children}</div>;
}

export function Change({ pct, style }) {
  if (pct == null) return <span style={{ color: S.text3, ...style }}>—</span>;
  return <span style={{ color: pct > 0 ? UP : pct < 0 ? DOWN : S.text2, fontFamily: S.mono, ...style }}>{pct > 0 ? '+' : ''}{num(pct, 2)}%</span>;
}

export function Panel({ title, right, children, style }) {
  return (
    <div style={{ border: `1px solid ${S.border}`, background: S.surface, borderRadius: 4, padding: '10px 12px', marginBottom: 10, ...style }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8, gap: 8 }}>
        <div style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3, letterSpacing: 1, textTransform: 'uppercase' }}>{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

/** "Yahoo Finance · 9 min old" with a STALE badge — the honest label every figure in this tab sits under. */
export function DataStamp({ quality, style }) {
  if (!quality) return null;
  const age = quality.age_seconds;
  const ageTxt = age == null ? 'never fetched' : age < 3600 ? `${Math.max(1, Math.round(age / 60))} min old` : `${Math.round(age / 3600)} h old`;
  return (
    <div title={quality.delay_note || ''} style={{ fontSize: 9.5, fontFamily: S.mono, color: S.text3, ...style }}>
      {quality.source} · {ageTxt}
      {quality.stale && <span style={{ color: '#e0c23c', border: '1px solid #e0c23c', borderRadius: 3, padding: '0 4px', marginLeft: 6 }}>STALE</span>}
      {quality.last_error && !quality.stale && <span style={{ color: '#e0c23c', marginLeft: 6 }} title={quality.last_error}>partial</span>}
    </div>
  );
}

export function Spark({ values, w = 90, h = 24, color }) {
  const v = (values || []).filter((x) => Number.isFinite(x));
  if (v.length < 2) return <svg width={w} height={h} />;
  const lo = Math.min(...v), hi = Math.max(...v), rng = hi - lo || 1;
  const d = v.map((x, i) => `${i ? 'L' : 'M'}${((i / (v.length - 1)) * (w - 2) + 1).toFixed(1)},${(h - 2 - ((x - lo) / rng) * (h - 4)).toFixed(1)}`).join(' ');
  return <svg width={w} height={h} style={{ display: 'block' }}><path d={d} fill="none" stroke={color || S.accent} strokeWidth="1.4" /></svg>;
}
