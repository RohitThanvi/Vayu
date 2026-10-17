/** One page on a ticker or a strategic site: what Vayu knows about it in one place. Descriptive only — no forecasts, no scores. */
import { useState } from 'react';
import SparkChart from '../SparkChart';
import Financials from './Financials';
import RelationshipMap from './RelationshipMap';
import EventImpact from './EventImpact';
import { S, useJson, num, BAND_COLOR, btn } from './theme';
import { Change, Panel, Empty } from './ui';

function Stat({ label, value, suffix = '' }) {
  return (
    <div style={{ minWidth: 92 }}>
      <div style={{ fontSize: 9.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontFamily: S.mono, fontSize: 13, color: S.text }}>{value == null ? '—' : `${num(value)}${suffix}`}</div>
    </div>
  );
}

function RangeBar({ pct, low, high }) {
  if (pct == null) return null;
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ position: 'relative', height: 6, background: S.surface2, borderRadius: 3 }}>
        <div style={{ position: 'absolute', left: `${Math.min(100, Math.max(0, pct))}%`, top: -3, width: 3, height: 12, background: S.accent, borderRadius: 1 }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9.5, color: S.text3, fontFamily: S.mono, marginTop: 2 }}><span>{num(low)}</span><span>52-week range</span><span>{num(high)}</span></div>
    </div>
  );
}

function Links({ items }) {
  if (!items?.length) return null;
  return (
    <Panel title="Linked chokepoints">
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {items.map((c) => (
          <span key={c.chokepoint} style={{ fontSize: 11, border: `1px solid ${BAND_COLOR[c.band] || S.border}`, color: BAND_COLOR[c.band] || S.text2, borderRadius: 3, padding: '2px 7px' }}>
            {c.display}{c.band ? ` · ${c.band}` : ''}{c.score != null ? ` (${num(c.score, 0)})` : ''}
          </span>
        ))}
      </div>
    </Panel>
  );
}

function PriceChart({ apiUrl, symbol }) {
  const [range, setRange] = useState('1y');
  const hist = useJson(apiUrl, `/api/v1/intel/quote-history?symbol=${encodeURIComponent(symbol)}&range=${range}`, 30 * 60 * 1000).data;
  const points = (hist?.points || []).map((p) => ({ date: new Date(p.date * 1000).toISOString(), value: p.value }));
  return (
    <Panel title="Price" right={<span style={{ display: 'flex', gap: 4 }}>{[['3mo', '3M'], ['1y', '1Y'], ['5y', '5Y']].map(([r, l]) => (
      <button key={r} aria-pressed={range === r} onClick={() => setRange(r)} style={{ ...btn, padding: '1px 7px', opacity: range === r ? 1 : 0.55 }}>{l}</button>))}</span>}>
      {points.length > 1 ? <SparkChart points={points} height={170} /> : <Empty>No price history available.</Empty>}
    </Panel>
  );
}

export default function AssetProfile({ apiUrl, kind, id, onClose, onOpenAsset }) {
  const { data: p, error } = useJson(apiUrl, `/api/v1/intel/asset-profile?kind=${kind}&id=${encodeURIComponent(id)}`, 5 * 60 * 1000);
  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 20, background: S.bg, overflowY: 'auto', padding: '12px 16px' }} role="dialog" aria-label={`Profile of ${id}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 15, color: S.text, fontFamily: S.mono }}>{p?.name || id}</div>
          <div style={{ fontSize: 10, color: S.text3, textTransform: 'uppercase', letterSpacing: 1 }}>{kind === 'site' ? `Strategic site · ${p?.site_type || ''}` : `Ticker · ${id}`}</div>
        </div>
        <button onClick={onClose} style={btn} aria-label="Close profile">Close ✕</button>
      </div>
      {error && !p && <Empty>Could not load this profile ({error}).</Empty>}
      {!p && !error && <Empty>Loading…</Empty>}
      {p?.kind === 'ticker' && (
        <>
          <Panel title="Quote">
            {p.quote ? (
              <div style={{ display: 'flex', gap: 22, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 22, fontFamily: S.mono, color: S.text }}>{num(p.quote.value)} <span style={{ fontSize: 11, color: S.text3 }}>{p.quote.currency || ''}</span></span>
                <Change pct={p.quote.change_pct} style={{ fontSize: 14 }} />
                <span style={{ fontSize: 10.5, color: S.text3 }}>{p.quote.exchange || ''}</span>
              </div>
            ) : <Empty>No quote available right now.</Empty>}
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 4 }}>Yahoo Finance (unofficial) · typically 15–20 min delayed · reference only</div>
          </Panel>
          <Panel title="Price statistics (daily closes)">
            {p.stats?.volatility_annualised_pct == null ? <Empty>Not enough price history for statistics.</Empty> : (
              <>
                <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                  <Stat label="1 month" value={p.stats.return_1m_pct} suffix="%" /><Stat label="3 months" value={p.stats.return_3m_pct} suffix="%" /><Stat label="1 year" value={p.stats.return_1y_pct} suffix="%" />
                  <Stat label="Volatility (annualised)" value={p.stats.volatility_annualised_pct} suffix="%" /><Stat label="Max drawdown" value={p.stats.max_drawdown_pct} suffix="%" />
                </div>
                <RangeBar pct={p.stats.position_in_52w_range_pct} low={p.stats.low_52w} high={p.stats.high_52w} />
              </>
            )}
          </Panel>
          <PriceChart apiUrl={apiUrl} symbol={id} />
          <Financials apiUrl={apiUrl} symbol={id} />
          <RelationshipMap apiUrl={apiUrl} symbol={id} onOpenAsset={onOpenAsset} />
          <EventImpact apiUrl={apiUrl} symbol={id} />
          <Links items={p.linked_chokepoints} />
        </>
      )}
      {p?.kind === 'site' && (
        <>
          <Panel title="About"><div style={{ fontSize: 12, color: S.text2, lineHeight: 1.5 }}>{p.note}</div></Panel>
          <Panel title="Signals detected now">
            {p.signals?.length ? p.signals.map((s, i) => (
              <div key={i} style={{ fontSize: 11.5, color: s.severity === 'critical' ? '#e05c5c' : '#e0c23c', marginBottom: 3 }}>{s.note}</div>
            )) : <Empty>None detected near this site. (That means none detected, not that nothing is happening.)</Empty>}
          </Panel>
          <Links items={p.linked_chokepoints} />
          {p.related_tickers?.length > 0 && (
            <Panel title="Related bellwether tickers">
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {p.related_tickers.map((t) => <button key={t} style={btn} onClick={() => onOpenAsset?.('ticker', t)}>{t}</button>)}
              </div>
            </Panel>
          )}
          <Panel title="Latest updates (news)">
            {p.recent_news?.length ? p.recent_news.map((n, i) => (
              <div key={i} style={{ marginBottom: 6 }}>
                <a href={n.url} target="_blank" rel="noreferrer" style={{ fontSize: 11.5, color: S.text, textDecoration: 'none' }}>{n.title}</a>
                <div style={{ fontSize: 9.5, color: S.text3 }}>{n.domain}</div>
              </div>
            )) : <Empty>No recent matching headlines.</Empty>}
          </Panel>
        </>
      )}
      {p?.notes?.map((n, i) => <div key={i} style={{ fontSize: 10, color: S.text3, marginBottom: 3 }}>• {n}</div>)}
    </div>
  );
}
