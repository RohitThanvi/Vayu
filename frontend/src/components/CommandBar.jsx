/**
 * Command bar (Ctrl/Cmd+K): keyboard-first navigation, the way a terminal feels. Type to filter; Up/Down + Enter to run; Esc closes.
 * Typing a symbol (e.g. RELIANCE.NS) offers to chart it or open its one-page profile.
 */
import { useState, useEffect, useRef, useMemo } from 'react';
import { SYMBOL_RE } from '../lib/alertRules';

const BUSINESS_VIEWS = [['live', 'Live'], ['markets', 'Markets'], ['analysis', 'Analysis'], ['economics', 'Economics — India food & fuel, yield curve'], ['realestate', 'Real Estate — location intelligence (free data)'], ['alerts', 'Alerts']];

export default function CommandBar({ open, onClose, tabs, apiUrl, onCommand }) {
  const [q, setQ] = useState('');
  const [idx, setIdx] = useState(0);
  const inputRef = useRef(null);

  useEffect(() => { if (open) { setQ(''); setIdx(0); setTimeout(() => inputRef.current?.focus(), 0); } }, [open]);

  const items = useMemo(() => {
    const all = [];
    tabs.forEach((t) => all.push({ id: `tab:${t}`, group: 'Navigate', label: `Go to ${t}`, run: () => onCommand({ type: 'tab', tab: t }) }));
    if (tabs.includes('Business')) {
      BUSINESS_VIEWS.forEach(([v, label]) => all.push({ id: `bv:${v}`, group: 'Business', label: `Business · ${label}`, run: () => onCommand({ type: 'business', view: v }) }));
      all.push({ id: 'dl:brief', group: 'Download', label: 'Download daily brief (PDF)', run: () => onCommand({ type: 'download', url: `${apiUrl}/api/v1/report/business-brief` }) });
      ['markets', 'commodities', 'events', 'chokepoint-risk', 'yield-curve'].forEach((d) =>
        all.push({ id: `dl:${d}`, group: 'Download', label: `Download ${d.replace('-', ' ')} (CSV)`, run: () => onCommand({ type: 'download', url: `${apiUrl}/api/v1/intel/export/${d}.csv` }) }));
    }
    const sym = q.trim().toUpperCase();
    const dyn = [];
    if (tabs.includes('Business') && sym && SYMBOL_RE.test(sym)) {
      dyn.push({ id: `chart:${sym}`, group: 'Symbol', label: `Chart ${sym}`, run: () => onCommand({ type: 'business', view: 'markets', symbol: sym }) });
      dyn.push({ id: `prof:${sym}`, group: 'Symbol', label: `One-page profile: ${sym}`, run: () => onCommand({ type: 'business', view: 'markets', symbol: sym, asset: { kind: 'ticker', id: sym } }) });
    }
    const tokens = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const filtered = all.filter((it) => tokens.every((t) => it.label.toLowerCase().includes(t)));
    // A word like "markets" or "brief" is also a valid-looking ticker, so real commands must win; symbol suggestions follow them
    // (and come first only when nothing else matches, e.g. "reliance.ns").
    return [...filtered, ...dyn].slice(0, 14);
  }, [q, tabs, apiUrl, onCommand]);

  useEffect(() => { setIdx(0); }, [q]);
  if (!open) return null;

  const run = (it) => { if (it) { onClose(); it.run(); } };
  const onKey = (e) => {
    if (e.key === 'Escape') onClose();
    else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); run(items[idx]); }
  };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 6000, background: 'rgba(0,0,0,0.45)', display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: '14vh' }}>
      <div role="dialog" aria-label="Command bar" onClick={(e) => e.stopPropagation()} style={{ width: 'min(560px, 92vw)', background: 'var(--vayu-surface)', border: '1px solid var(--vayu-border2)', borderRadius: 6, boxShadow: '0 12px 40px rgba(0,0,0,0.5)', overflow: 'hidden' }}>
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} aria-label="Type a command or a symbol" aria-controls="vayu-cmd-list" aria-activedescendant={items[idx] ? `cmd-${items[idx].id}` : undefined}
          placeholder="Type a command or a symbol (e.g. markets, alerts, RELIANCE.NS)…"
          style={{ width: '100%', boxSizing: 'border-box', padding: '13px 16px', background: 'transparent', border: 'none', borderBottom: '1px solid var(--vayu-border)', color: 'var(--vayu-text)', fontSize: 14, fontFamily: "'JetBrains Mono',monospace", outline: 'none' }} />
        <div id="vayu-cmd-list" role="listbox" style={{ maxHeight: 340, overflowY: 'auto' }}>
          {!items.length && <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--vayu-text3)' }}>No matching command.</div>}
          {items.map((it, i) => (
            <div key={it.id} id={`cmd-${it.id}`} role="option" aria-selected={i === idx} onMouseEnter={() => setIdx(i)} onClick={() => run(it)}
              style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 16px', cursor: 'pointer', fontSize: 12.5, color: 'var(--vayu-text)', background: i === idx ? 'rgba(126,184,212,0.14)' : 'transparent' }}>
              <span>{it.label}</span><span style={{ fontSize: 10, color: 'var(--vayu-text3)', textTransform: 'uppercase', letterSpacing: 0.8 }}>{it.group}</span>
            </div>
          ))}
        </div>
        <div style={{ padding: '6px 16px', fontSize: 10, color: 'var(--vayu-text3)', borderTop: '1px solid var(--vayu-border)' }}>↑↓ choose · Enter run · Esc close · Ctrl/Cmd+K opens this anywhere</div>
      </div>
    </div>
  );
}
