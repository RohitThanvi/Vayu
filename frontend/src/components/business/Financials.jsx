/** Annual financials a company reported to the SEC (XBRL), exactly as tagged. Descriptive only: no estimates, no forecasts. */
import { S, useJson } from './theme';
import { Panel, Empty } from './ui';

export const money = (v) => {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v), a = Math.abs(n), sign = n < 0 ? '-' : '';
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
  return `${sign}$${a.toLocaleString()}`;
};

const LABELS = { revenue: 'Revenue', net_income: 'Net income', operating_income: 'Operating income', operating_cash_flow: 'Operating cash flow', assets: 'Total assets', eps_diluted: 'Diluted EPS' };
const ORDER = ['revenue', 'net_income', 'operating_income', 'operating_cash_flow', 'assets', 'eps_diluted'];

export function Bars({ points, fmt = money, color = S.accent, label }) {
  if (!points?.length) return null;
  const W = 300, H = 92, pad = 4, bw = Math.max(8, (W - pad * 2) / points.length - 6);
  const vals = points.map((p) => Number(p.value));
  const hi = Math.max(0, ...vals), lo = Math.min(0, ...vals), rng = hi - lo || 1;
  const y = (v) => pad + ((hi - v) / rng) * (H - 16 - pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label} style={{ display: 'block', maxWidth: 420 }}>
      <line x1={pad} x2={W - pad} y1={y(0)} y2={y(0)} stroke={S.border} />
      {points.map((p, i) => {
        const x = pad + i * ((W - pad * 2) / points.length) + 3, v = Number(p.value), top = Math.min(y(v), y(0)), h = Math.max(1, Math.abs(y(v) - y(0)));
        return (
          <g key={p.end}>
            <title>{`${p.end}: ${fmt(v)} (${p.form}, filed ${p.filed || '?'})`}</title>
            <rect x={x} y={top} width={bw} height={h} fill={v < 0 ? '#e05c5c' : color} opacity="0.85" />
            <text x={x + bw / 2} y={H - 3} textAnchor="middle" fontSize="8" fill={S.text3} fontFamily={S.mono}>{p.end.slice(0, 4)}</text>
          </g>
        );
      })}
    </svg>
  );
}

export default function Financials({ apiUrl, symbol }) {
  const { data: f, error } = useJson(apiUrl, `/api/v1/intel/company/financials?symbol=${encodeURIComponent(symbol)}`, 0);
  const series = f?.series || {};
  const have = ORDER.filter((m) => series[m]?.length);
  const last = (m) => series[m]?.[series[m].length - 1];
  const growth = f?.derived?.revenue_growth_pct || [], margin = f?.derived?.net_margin_pct || [];
  return (
    <Panel title="Business done — annual financials (SEC)" right={f?.source_url ? <a href={f.source_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 10, color: S.accent }}>filings ↗</a> : null}>
      {!f && !error && <Empty>Loading…</Empty>}
      {error && !f && <Empty>Could not load financials ({error}).</Empty>}
      {f && !have.length && <Empty>{(f.notes && f.notes[0]) || 'No annual financials available for this symbol.'}</Empty>}
      {have.length > 0 && (
        <>
          <div data-testid="fin-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
            {have.filter((m) => m !== 'eps_diluted').map((m) => (
              <div key={m}>
                <div style={{ fontSize: 10, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{LABELS[m]} · latest <span style={{ color: S.text, fontFamily: S.mono }}>{money(last(m).value)}</span></div>
                <Bars points={series[m]} label={`${LABELS[m]} by fiscal year`} />
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: S.text2, fontFamily: S.mono, marginTop: 8, lineHeight: 1.6 }}>
            {last('eps_diluted') && <div>Diluted EPS ({last('eps_diluted').end}): ${last('eps_diluted').value}</div>}
            {growth.length > 0 && <div>Revenue growth ({growth[growth.length - 1].end}): {growth[growth.length - 1].pct}%</div>}
            {margin.length > 0 && <div>Net margin ({margin[margin.length - 1].end}): {margin[margin.length - 1].pct}%</div>}
          </div>
          <div style={{ fontSize: 9.5, color: S.text3, marginTop: 6, lineHeight: 1.45 }}>
            {f.source}{f.as_of ? ` · latest filing ${f.as_of}` : ''}{f.entity ? ` · ${f.entity}` : ''}. {(f.notes || []).join(' ')}
          </div>
        </>
      )}
    </Panel>
  );
}
