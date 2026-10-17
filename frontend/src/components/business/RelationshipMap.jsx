/**
 * Supplier / customer map: suppliers on the left, the company in the centre, customers on the right. Click a node for its
 * evidence and source; "Open map" re-centres on that company (breadcrumbs go back). Edges come from companies' own SEC filings
 * (extracted automatically — every one links to the filing) plus relationships the analyst adds, which are marked as theirs.
 */
import { useState, useEffect } from 'react';
import { S, useJson, btn, input, SYMBOL_RE } from './theme';
import { Panel, Empty } from './ui';

const KEY = 'vayu.relationships.v1';
const MAX_PER_SIDE = 10;
const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);

export function loadAnalystEdges(center) {
  try {
    const all = JSON.parse(localStorage.getItem(KEY) || '{}');
    const rows = Array.isArray(all?.[center]) ? all[center] : [];
    // Normalise whatever is in storage: a stable string id (React key + select/remove), and only the fields we know how to render.
    return rows.filter((r) => r && typeof r.name === 'string' && (r.side === 'supplier' || r.side === 'customer')).slice(0, 40)
      .map((r, i) => ({ id: typeof r.id === 'string' && r.id ? r.id : `mine-loaded-${i}`, side: r.side, name: r.name.slice(0, 80),
                        symbol: typeof r.symbol === 'string' && SYMBOL_RE.test(r.symbol) ? r.symbol : null,
                        pct: Number.isFinite(r.pct) && r.pct > 0 && r.pct <= 100 ? r.pct : null, source_url: safeUrl(r.source_url),
                        evidence: typeof r.evidence === 'string' ? r.evidence.slice(0, 300) : null, confidence: 'analyst' }));
  } catch { return []; }
}
function saveAnalystEdges(center, rows) {
  try { const all = JSON.parse(localStorage.getItem(KEY) || '{}'); all[center] = rows; localStorage.setItem(KEY, JSON.stringify(all)); } catch { /* storage unavailable: edges last this session */ }
}

const short = (s, n = 24) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const ROW = 46, W = 900, NODE_W = 230, CX = 370, CW = 160;

function Side({ nodes, side, selectedId, onSelect, top }) {
  const x = side === 'supplier' ? 20 : W - 20 - NODE_W;
  return nodes.map((n, i) => {
    const y = top + i * ROW, sel = selectedId === n.id;
    const color = n.confidence === 'analyst' ? '#c9a86a' : S.accent;
    const x1 = side === 'supplier' ? x + NODE_W : CX + CW, x2 = side === 'supplier' ? CX : x;
    const my = (x1 + x2) / 2;
    return (
      <g key={n.id}>
        <path d={`M${x1},${y + 17} C${my},${y + 17} ${my},${n.cy} ${x2},${n.cy}`} fill="none" stroke={color} strokeOpacity={sel ? 0.95 : 0.4}
              strokeWidth={1 + Math.min(5, (n.pct || 4) / 8)} strokeDasharray={n.confidence === 'analyst' ? '5 4' : undefined} />
        <g role="button" tabIndex={0} aria-label={`${n.name}${n.pct != null ? `, ${n.pct}% of sales` : ''}`} data-testid={`node-${side}`} style={{ cursor: 'pointer' }}
           onClick={() => onSelect(n.id)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(n.id); } }}>
          <rect x={x} y={y} width={NODE_W} height={34} rx={4} fill={S.surface2} stroke={sel ? S.accent : S.border} strokeWidth={sel ? 1.6 : 1} />
          <text x={x + 8} y={y + 14} fontSize="11" fill={S.text} fontFamily={S.mono}>{short(n.name)}</text>
          <text x={x + 8} y={y + 27} fontSize="9" fill={S.text3} fontFamily={S.mono}>{[n.symbol, n.pct != null ? `${n.pct}% of sales` : null, n.confidence === 'analyst' ? 'yours' : null].filter(Boolean).join(' · ')}</text>
        </g>
      </g>
    );
  });
}

export default function RelationshipMap({ apiUrl, symbol, onOpenAsset }) {
  const [stack, setStack] = useState([symbol]);
  const cur = stack[stack.length - 1];
  useEffect(() => { setStack([symbol]); }, [symbol]);
  const { data: g, error } = useJson(apiUrl, `/api/v1/intel/company/relationships?symbol=${encodeURIComponent(cur)}`, 0);
  const ready = g && g.center && String(g.center.symbol).toUpperCase() === String(cur).toUpperCase();

  const [mine, setMine] = useState(() => loadAnalystEdges(cur));
  useEffect(() => { setMine(loadAnalystEdges(cur)); }, [cur]);
  const [selected, setSelected] = useState(null);
  useEffect(() => { setSelected(null); }, [cur]);
  const [form, setForm] = useState({ side: 'supplier', name: '', symbol: '', pct: '', url: '', note: '' });

  const addMine = (e) => {
    e.preventDefault();
    const name = form.name.trim();
    if (!name) return;
    const sym = form.symbol.trim().toUpperCase();
    const pct = Number(form.pct);
    const row = { id: `mine-${Date.now()}-${mine.length}`, side: form.side, name: name.slice(0, 80), symbol: SYMBOL_RE.test(sym) ? sym : null,
                  pct: Number.isFinite(pct) && pct > 0 && pct <= 100 ? pct : null, source_url: safeUrl(form.url.trim()), evidence: form.note.trim().slice(0, 300) || null, confidence: 'analyst' };
    const next = [...mine, row];
    setMine(next); saveAnalystEdges(cur, next); setForm({ ...form, name: '', symbol: '', pct: '', url: '', note: '' });
  };
  const removeMine = (id) => { const next = mine.filter((r) => r.id !== id); setMine(next); saveAnalystEdges(cur, next); setSelected(null); };

  const fromSec = (arr, side) => (arr || []).map((n, i) => ({ ...n, id: `${side}-${i}`, side }));
  const left = ready ? [...fromSec(g.suppliers, 'supplier'), ...mine.filter((m) => m.side === 'supplier')] : [];
  const right = ready ? [...fromSec(g.customers, 'customer'), ...mine.filter((m) => m.side === 'customer')] : [];
  const shownL = left.slice(0, MAX_PER_SIDE), shownR = right.slice(0, MAX_PER_SIDE);
  const rows = Math.max(shownL.length, shownR.length, 1);
  const H = 40 + rows * ROW + 20, centerY = 40 + (rows * ROW) / 2 - 10;
  const withCy = (arr) => arr.map((n) => ({ ...n, cy: centerY }));
  const node = [...left, ...right].find((n) => n.id === selected);

  const drill = (sym) => { if (sym && SYMBOL_RE.test(sym)) { setStack([...stack, sym]); } };
  const err = error ? (error === '429' ? 'Too many requests right now — wait a minute and try again.' : 'Could not load relationships right now. Try again shortly.') : null;

  return (
    <Panel title="Relationship map — suppliers ← company → customers">
      {stack.length > 1 && (
        <div data-testid="crumbs" style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8, fontSize: 11, fontFamily: S.mono }}>
          {stack.map((s, i) => (
            <span key={`${s}-${i}`}>{i > 0 && <span style={{ color: S.text3 }}> › </span>}
              {i < stack.length - 1 ? <button style={{ ...btn, padding: '1px 6px' }} onClick={() => setStack(stack.slice(0, i + 1))}>{s}</button> : <span style={{ color: S.text }}>{s}</span>}
            </span>
          ))}
        </div>
      )}
      {err && !ready && <Empty>{err}</Empty>}
      {!ready && !err && <Empty>Reading SEC filings… the first look at a company can take up to a minute.</Empty>}
      {ready && (
        <>
          {g.partial && <div role="alert" style={{ fontSize: 10.5, color: '#e0c23c', marginBottom: 6 }}>Some SEC documents could not be read, so this map may be incomplete.</div>}
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 9.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>
            <span>Suppliers · name it as a major customer</span><span>Customers · disclosed in its own 10-K</span>
          </div>
          <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="group" aria-label={`Supplier and customer map for ${g.center.name}`} style={{ display: 'block' }}>
            <Side nodes={withCy(shownL)} side="supplier" selectedId={selected} onSelect={setSelected} top={40} />
            <Side nodes={withCy(shownR)} side="customer" selectedId={selected} onSelect={setSelected} top={40} />
            <g>
              <rect x={CX} y={centerY - 24} width={CW} height={48} rx={6} fill={S.surface} stroke={S.accent} strokeWidth={1.6} />
              <text x={CX + CW / 2} y={centerY - 4} textAnchor="middle" fontSize="12" fill={S.text} fontFamily={S.mono}>{short(g.center.name, 20)}</text>
              <text x={CX + CW / 2} y={centerY + 12} textAnchor="middle" fontSize="10" fill={S.accent} fontFamily={S.mono}>{g.center.symbol}</text>
            </g>
          </svg>
          {(left.length > MAX_PER_SIDE || right.length > MAX_PER_SIDE) && <div style={{ fontSize: 10, color: S.text3 }}>Showing the {MAX_PER_SIDE} strongest per side ({left.length} suppliers, {right.length} customers found).</div>}
          {!left.length && !right.length && <Empty>No relationships were found in the filings checked. That means none were found — not that none exist.</Empty>}
          {g.exposures?.length > 0 && (
            <div style={{ fontSize: 11, color: S.text2, margin: '6px 0' }}>Exposed to:{' '}
              {g.exposures.map((x) => <span key={x.chokepoint} title={x.basis} style={{ border: `1px solid ${S.border}`, borderRadius: 3, padding: '1px 6px', marginRight: 5 }}>{x.display}</span>)}
              <span style={{ fontSize: 9.5, color: S.text3 }}>(Vayu's chokepoint mapping)</span></div>
          )}
          {node && (
            <div data-testid="edge-detail" style={{ border: `1px solid ${S.border}`, borderRadius: 4, padding: '8px 10px', margin: '8px 0', background: S.surface2 }}>
              <div style={{ fontSize: 12, color: S.text, fontFamily: S.mono }}>{node.name}{node.symbol ? ` (${node.symbol})` : ''}</div>
              <div style={{ fontSize: 11, color: S.text2, margin: '4px 0' }}>
                {node.confidence === 'analyst'
                  ? `Added by you: ${node.side === 'supplier' ? 'supplies' : 'buys from'} ${g.center.name}.`
                  : node.side === 'supplier'
                    ? `${node.name} reports ${g.center.name} as ${node.pct != null ? `${node.pct}% of` : 'a major part of'} its sales.`
                    : `${g.center.name} reports ${node.name} as ${node.pct != null ? `${node.pct}% of` : 'a major part of'} its sales.`}
              </div>
              {node.evidence && <blockquote style={{ margin: '4px 0', padding: '0 0 0 8px', borderLeft: `2px solid ${S.border}`, fontSize: 10.5, color: S.text3, lineHeight: 1.45 }}>…{node.evidence}…</blockquote>}
              <div style={{ fontSize: 10, color: S.text3 }}>
                {node.confidence === 'analyst' ? 'Not verified by Vayu.' : 'Extracted from filing text — verify in the filing.'}{node.filed ? ` · filed ${node.filed}` : ''}{' '}
                {safeUrl(node.source_url) && <a href={node.source_url} target="_blank" rel="noopener noreferrer" style={{ color: S.accent }}>Open source ↗</a>}
              </div>
              <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                {node.symbol && SYMBOL_RE.test(node.symbol) && <button style={btn} onClick={() => drill(node.symbol)}>Open map for {node.symbol}</button>}
                {node.symbol && onOpenAsset && <button style={btn} onClick={() => onOpenAsset('ticker', node.symbol)}>Profile</button>}
                {node.confidence === 'analyst' && <button style={btn} onClick={() => removeMine(node.id)}>Remove</button>}
              </div>
            </div>
          )}
          <details style={{ marginTop: 6 }}>
            <summary style={{ cursor: 'pointer', fontSize: 11, color: S.accent }}>Add a relationship you know of</summary>
            <form onSubmit={addMine} style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 6 }}>
              <select aria-label="Relationship side" value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })} style={input}>
                <option value="supplier">Supplies {g.center.name}</option><option value="customer">Buys from {g.center.name}</option></select>
              <input aria-label="Company name" placeholder="Company name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ ...input, width: 150 }} />
              <input aria-label="Ticker (optional)" placeholder="Ticker" value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} style={{ ...input, width: 80 }} />
              <input aria-label="Share of sales percent (optional)" placeholder="% of sales" inputMode="decimal" value={form.pct} onChange={(e) => setForm({ ...form, pct: e.target.value })} style={{ ...input, width: 80 }} />
              <input aria-label="Source link (optional)" placeholder="Source URL" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} style={{ ...input, width: 170 }} />
              <input aria-label="Note (optional)" placeholder="Note" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} style={{ ...input, width: 170 }} />
              <button type="submit" style={btn}>Add</button>
            </form>
            <div style={{ fontSize: 9.5, color: S.text3, marginTop: 3 }}>Saved in this browser only, shown with a dashed line, and never presented as verified.</div>
          </details>
          <div style={{ fontSize: 9.5, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>{(g.notes || []).map((n, i) => <div key={i}>• {n}</div>)}</div>
        </>
      )}
    </Panel>
  );
}
