/** Latest news mentions (GDELT, free, no quota) and official SEC filings for a company. Headlines are the publishers'; Vayu does not summarise or score them. */
import { useState } from 'react';
import { S, useJson, btn } from './theme';
import { Panel, Empty } from './ui';
import { ago } from './HazardsPanel';

const https = (u) => (typeof u === 'string' && u.startsWith('https://') ? u : null);
const SPANS = [['3d', '3 days'], ['7d', '7 days'], ['30d', '30 days']];

export default function CompanyNews({ apiUrl, symbol, name }) {
  const [span, setSpan] = useState('7d');
  const nameQ = name ? `&name=${encodeURIComponent(name)}` : '';
  const { data: news, error: newsErr } = useJson(apiUrl, `/api/v1/intel/company/news?symbol=${encodeURIComponent(symbol)}${nameQ}&timespan=${span}`, 30 * 60 * 1000);
  const { data: fil, error: filErr } = useJson(apiUrl, `/api/v1/intel/company/filings?symbol=${encodeURIComponent(symbol)}`, 30 * 60 * 1000);
  const newsReady = news && news.timespan === span;
  const articles = newsReady ? news.articles || [] : [];
  const filings = fil?.filings || [];
  return (
    <Panel title="Latest news & filings" right={<span style={{ display: 'flex', gap: 4 }}>{SPANS.map(([k, l]) => (
      <button key={k} aria-pressed={span === k} onClick={() => setSpan(k)} style={{ ...btn, padding: '1px 7px', opacity: span === k ? 1 : 0.55 }}>{l}</button>))}</span>}>
      <div style={{ fontSize: 10, color: S.accent, fontFamily: S.mono, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 3 }}>News mentions</div>
      {!newsReady && !newsErr && <Empty>Loading news…</Empty>}
      {newsErr && !newsReady && <Empty>News is unavailable right now.</Empty>}
      {newsReady && news.stale && <div role="status" data-testid="news-stale" style={{ fontSize: 10.5, color: '#e0c23c', marginBottom: 4 }}>{(news.notes || [])[0]}</div>}
      {newsReady && !news.stale && !articles.length && <Empty>{(news.notes || [])[0] || 'No recent articles found.'}</Empty>}
      <div data-testid="news-list">
        {articles.map((a, i) => (
          <div key={`${i}-${a.url}`} style={{ padding: '4px 0', borderBottom: `1px solid ${S.border}` }}>
            {https(a.url)
              ? <a href={a.url} target="_blank" rel="noopener noreferrer" style={{ color: S.text, fontSize: 12, textDecoration: 'none' }}>{a.title}</a>
              : <span style={{ color: S.text2, fontSize: 12 }}>{a.title}</span>}
            <div style={{ fontSize: 10, color: S.text3, fontFamily: S.mono }}>{[a.domain, a.country, ago(a.seen)].filter(Boolean).join(' · ')}</div>
          </div>
        ))}
      </div>

      <div style={{ fontSize: 10, color: S.accent, fontFamily: S.mono, textTransform: 'uppercase', letterSpacing: 1, margin: '10px 0 3px' }}>Official filings (SEC)</div>
      {!fil && !filErr && <Empty>Loading filings…</Empty>}
      {filErr && !fil && <Empty>Filings are unavailable right now.</Empty>}
      {fil && !filings.length && <Empty>{(fil.notes || [])[0] || 'No recent filings.'}</Empty>}
      <div data-testid="filings-list">
        {filings.map((f) => (
          <div key={f.accession} style={{ padding: '4px 0', borderBottom: `1px solid ${S.border}` }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', fontSize: 11.5 }}>
              <span style={{ fontSize: 9.5, fontFamily: S.mono, border: `1px solid ${S.border}`, borderRadius: 2, padding: '0 5px', color: S.accent }}>{f.form}</span>
              <span style={{ color: S.text }}>{f.label}</span>
              <span style={{ color: S.text3, fontFamily: S.mono, fontSize: 10 }}>{f.filed}</span>
              {https(f.url) && <a href={f.url} target="_blank" rel="noopener noreferrer" style={{ color: S.accent, fontSize: 10.5 }}>open ↗</a>}
            </div>
            {f.items?.length > 0 && <div style={{ fontSize: 10.5, color: S.text2, marginTop: 2 }}>{f.items.map((i) => `${i.code} ${i.label}`).join(' · ')}</div>}
          </div>
        ))}
      </div>
      <div style={{ fontSize: 9.5, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>
        {newsReady && news.source && <div>• {news.source.name}: {news.source.note}</div>}
        {fil?.source && <div>• {fil.source.name}: {fil.source.note}</div>}
      </div>
    </Panel>
  );
}
