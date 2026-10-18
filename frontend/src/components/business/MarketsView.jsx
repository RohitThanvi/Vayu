/**
 * Markets: a persistent watchlist for ANY symbol, a chart with range selector, the India sector heatmap, indices and FX.
 * Prices are ~15-20 min delayed reference prices from Yahoo's unofficial API; every block carries its data-quality stamp.
 */
import { useState, useEffect, useMemo } from 'react';
import SparkChart from '../SparkChart';
import HazardsPanel from './HazardsPanel';
import { S, useJson, num, exportUrl, btn, input, SYMBOL_RE, UP, DOWN } from './theme';
import { Change, Panel, DataStamp, Empty } from './ui';

const WL_KEY = 'vayu.watchlist.v1';
const DEFAULT_WATCHLIST = ['^NSEI', '^BSESN', 'INR=X', 'BZ=F', '^GSPC'];
const MAX_WATCHLIST = 12;   // the quotes endpoint accepts at most 12 symbols per request
const RANGES = [['1mo', '1M'], ['3mo', '3M'], ['6mo', '6M'], ['1y', '1Y'], ['5y', '5Y']];

function loadWatchlist() {
  try {
    const v = JSON.parse(localStorage.getItem(WL_KEY));
    if (Array.isArray(v) && v.every((s) => typeof s === 'string' && SYMBOL_RE.test(s))) return v.slice(0, MAX_WATCHLIST);
  } catch { /* fall through to the default */ }
  return DEFAULT_WATCHLIST;
}

function Heat({ q }) {
  const pct = q.change_pct;
  const a = pct == null ? 0.05 : Math.min(1, Math.abs(pct) / 3) * 0.75 + 0.1;
  const bg = pct == null ? S.surface2 : pct >= 0 ? `rgba(46,204,113,${a})` : `rgba(224,92,92,${a})`;
  return (
    <div title={`${q.symbol}${q.stale ? ' — last good value (refresh failed)' : ''}`} style={{ background: bg, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 8px', opacity: q.stale ? 0.6 : 1 }}>
      <div style={{ fontSize: 10.5, color: S.text, fontFamily: S.mono }}>{q.name.replace('Nifty ', '')}</div>
      <Change pct={pct} style={{ fontSize: 12, fontWeight: 700, color: S.text }} />
    </div>
  );
}

export default function MarketsView({ apiUrl, command, onOpenAsset }) {
  const [watchlist, setWatchlist] = useState(loadWatchlist);
  const [selected, setSelected] = useState(() => loadWatchlist()[0] || null);
  const [range, setRange] = useState('1y');
  const [draft, setDraft] = useState('');
  const [msg, setMsg] = useState(null);

  useEffect(() => { try { localStorage.setItem(WL_KEY, JSON.stringify(watchlist)); } catch { /* storage unavailable: list lasts this session */ } }, [watchlist]);
  useEffect(() => { if (command?.symbol && SYMBOL_RE.test(command.symbol)) setSelected(command.symbol.toUpperCase()); }, [command]);

  const markets = useJson(apiUrl, '/api/v1/intel/markets', 5 * 60 * 1000).data;
  const quotes = useJson(apiUrl, watchlist.length ? `/api/v1/intel/quotes?symbols=${encodeURIComponent(watchlist.join(','))}` : null, 60 * 1000);
  const hist = useJson(apiUrl, selected ? `/api/v1/intel/quote-history?symbol=${encodeURIComponent(selected)}&range=${range}` : null, 30 * 60 * 1000).data;

  const byQuote = useMemo(() => Object.fromEntries((quotes.data?.quotes || []).map((q) => [q.symbol, q])), [quotes.data]);
  const points = useMemo(() => (hist?.points || []).map((p) => ({ date: new Date(p.date * 1000).toISOString(), value: p.value })), [hist]);

  const addSymbol = (raw) => {
    const s = String(raw).trim().toUpperCase();
    if (!SYMBOL_RE.test(s)) return setMsg('Use a Yahoo symbol, e.g. RELIANCE.NS, TCS.NS, AAPL, INR=X.');
    if (watchlist.includes(s)) return setMsg('Already on your watchlist.');
    if (watchlist.length >= MAX_WATCHLIST) return setMsg(`Watchlist holds up to ${MAX_WATCHLIST} symbols.`);
    setWatchlist([...watchlist, s]); setSelected(s); setDraft(''); setMsg(null);
  };
  const add = (e) => { e.preventDefault(); addSymbol(draft); };

  // Name/symbol suggestions: S&P 500 + Vayu's lists + Yahoo search (server side), debounced so typing doesn't spam the API.
  const [q, setQ] = useState('');
  useEffect(() => { const t = setTimeout(() => setQ(draft.trim()), 250); return () => clearTimeout(t); }, [draft]);
  const sugg = useJson(apiUrl, q.length >= 2 ? `/api/v1/intel/symbol-search?q=${encodeURIComponent(q)}` : null, 0).data;
  const suggestions = (sugg?.results || []).filter((r) => !watchlist.includes(r.symbol)).slice(0, 8);
  const sectors = markets?.sectors || [];
  const strip = [...(markets?.indices || []), ...(markets?.fx || [])];

  return (
    <div style={{ display: 'flex', height: '100%', overflowX: 'auto' }}>
      <div style={{ width: 340, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        <Panel title="Watchlist" right={<span style={{ fontSize: 9.5, color: S.text3, fontFamily: S.mono }}>{watchlist.length}/{MAX_WATCHLIST}</span>}>
          <form onSubmit={add} style={{ display: 'flex', gap: 5, marginBottom: 6 }}>
            <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Search name or symbol (Tata, GM, RELIANCE.NS)" aria-label="Add symbol" style={{ ...input, flex: 1 }} />
            <button type="submit" style={btn}>Add</button>
          </form>
          {msg && <div style={{ fontSize: 10.5, color: '#e0c23c', marginBottom: 6 }}>{msg}</div>}
          {draft.trim().length >= 2 && suggestions.length > 0 && (
            <div role="listbox" aria-label="Symbol suggestions" data-testid="suggestions" style={{ border: `1px solid ${S.border}`, borderRadius: 3, marginBottom: 6 }}>
              {suggestions.map((r) => (
                <button key={r.symbol} role="option" onClick={() => addSymbol(r.symbol)}
                        style={{ display: 'flex', width: '100%', justifyContent: 'space-between', gap: 8, background: 'none', border: 'none', borderBottom: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '4px 7px', cursor: 'pointer', textAlign: 'left' }}>
                  <span style={{ color: S.accent }}>{r.symbol}</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: S.text3 }}>{r.name}{r.exchange ? ` · ${r.exchange}` : r.kind && r.kind !== 'equity' ? ` · ${r.kind}` : ''}</span>
                </button>
              ))}
            </div>
          )}
          {!watchlist.length && <Empty>Add a symbol to start your watchlist.</Empty>}
          {watchlist.map((sym) => {
            const q = byQuote[sym]; const err = quotes.data?.errors?.[sym];
            return (
              <div key={sym} onClick={() => setSelected(sym)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setSelected(sym)}
                style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: 8, alignItems: 'center', padding: '6px 4px', cursor: 'pointer', borderBottom: `1px solid ${S.border}`, background: selected === sym ? 'rgba(126,184,212,0.08)' : 'transparent' }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 11.5, color: S.text, fontFamily: S.mono }}>{sym}</div>
                  <div style={{ fontSize: 10, color: S.text3, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{q?.name || (err ? 'no quote available' : '…')}</div>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: 12, color: S.text, fontFamily: S.mono }}>{q ? num(q.value) : '—'}</div>
                  <Change pct={q?.change_pct} style={{ fontSize: 10.5 }} />
                </div>
                <button aria-label={`Remove ${sym}`} onClick={(e) => { e.stopPropagation(); setWatchlist(watchlist.filter((s) => s !== sym)); }}
                  style={{ background: 'none', border: 'none', color: S.text3, cursor: 'pointer', fontSize: 13 }}>×</button>
              </div>
            );
          })}
          <DataStamp quality={quotes.data?.data_quality} style={{ marginTop: 6 }} />
          {quotes.error && <div style={{ fontSize: 10.5, color: '#e0c23c', marginTop: 4 }}>Quotes unavailable right now — showing the last values received.</div>}
        </Panel>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <a href={exportUrl(apiUrl, 'markets')} style={btn} download>Markets CSV</a>
          <a href={exportUrl(apiUrl, 'commodities')} style={btn} download>Commodities CSV</a>
        </div>
      </div>

      <div style={{ width: 460, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        {!selected ? <Empty>Select a symbol to chart it.</Empty> : (
          <Panel title={selected} right={
            <div style={{ display: 'flex', gap: 4 }}>
              {RANGES.map(([id, label]) => (
                <button key={id} onClick={() => setRange(id)} style={{ ...btn, padding: '2px 7px', background: range === id ? 'rgba(126,184,212,0.28)' : 'transparent' }}>{label}</button>
              ))}
            </div>}>
            <div style={{ fontSize: 12, color: S.text2, marginBottom: 6 }}>{hist?.name || byQuote[selected]?.name || ''}</div>
            {hist?.error ? <Empty>{hist.error}</Empty> : (
              <SparkChart points={points} height={190}
                formatX={(d) => new Date(d).toLocaleDateString(undefined, { month: 'short', year: '2-digit' })}
                formatY={(v) => num(v, v < 10 ? 3 : 1)} emptyLabel="Loading chart…" />
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
              <button style={btn} onClick={() => onOpenAsset?.('ticker', selected)}>One-page profile</button>
              {!watchlist.includes(selected) && watchlist.length < MAX_WATCHLIST && <button style={btn} onClick={() => setWatchlist([...watchlist, selected])}>Add to watchlist</button>}
            </div>
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 8 }}>Daily closes. Reference prices, delayed ~15–20 min; not for trading decisions.</div>
          </Panel>
        )}
      </div>

      <div style={{ flex: 1, minWidth: 360, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        <HazardsPanel apiUrl={apiUrl} />
        <Panel title="India sectors (Nifty) — day change" right={<span style={{ fontSize: 9.5, color: S.text3, fontFamily: S.mono }}>{sectors.length} of 10 available</span>}>
          {sectors.length ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(112px, 1fr))', gap: 5 }}>
              {[...sectors].sort((a, b) => (b.change_pct ?? -99) - (a.change_pct ?? -99)).map((q) => <Heat key={q.symbol} q={q} />)}
            </div>
          ) : <Empty>Sector data is not available right now.</Empty>}
          <DataStamp quality={markets?.data_quality} style={{ marginTop: 6 }} />
        </Panel>
        <Panel title="Indices & currencies">
          {strip.length ? strip.map((q) => (
            <div key={q.symbol} onClick={() => setSelected(q.symbol)} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && setSelected(q.symbol)}
              style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: `1px solid ${S.border}`, cursor: 'pointer', opacity: q.stale ? 0.6 : 1 }}>
              <span style={{ fontSize: 11.5, color: S.text2 }}>{q.name}{q.stale && <span style={{ color: '#e0c23c', marginLeft: 5, fontSize: 9 }}>stale</span>}</span>
              <span><span style={{ fontFamily: S.mono, fontSize: 11.5, color: S.text, marginRight: 8 }}>{num(q.value)}</span><Change pct={q.change_pct} style={{ fontSize: 11 }} /></span>
            </div>
          )) : <Empty>Market data is not available right now.</Empty>}
        </Panel>
        <div style={{ fontSize: 9.5, color: S.text3, lineHeight: 1.5 }}>
          <span style={{ color: UP }}>▲</span> up / <span style={{ color: DOWN }}>▼</span> down are day changes versus the previous close. Sector and index symbols come from Yahoo and are omitted if Yahoo does not recognise them.
        </div>
      </div>
    </div>
  );
}
