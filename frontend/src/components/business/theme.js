/** Theme constants, formatters and the polling hook shared by the Business tab's views (components live in ui.jsx). */
import { useState, useEffect } from 'react';

export const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  bg: 'var(--vayu-bg)', surface: 'var(--vayu-surface)', surface2: 'var(--vayu-surface2)',
  border: 'var(--vayu-border)', border2: 'var(--vayu-border2)',
  text: 'var(--vayu-text)', text2: 'var(--vayu-text2)', text3: 'var(--vayu-text3)',
  accent: 'var(--vayu-accent)', gold: 'var(--vayu-gold)',
};
export const BAND_COLOR = { critical: '#ff3b3b', elevated: '#f0b429', normal: '#2ecc71' };
export const UP = '#2ecc71';
export const DOWN = '#e05c5c';
export const SYMBOL_RE = /^[A-Za-z0-9.^=-]{1,15}$/;
export const CHOKEPOINTS = [
  ['strait_of_hormuz', 'Strait of Hormuz'], ['strait_of_malacca', 'Strait of Malacca'], ['bab_el_mandeb', 'Bab-el-Mandeb'],
  ['suez_canal', 'Suez Canal'], ['strait_of_gibraltar', 'Strait of Gibraltar'], ['panama_canal', 'Panama Canal'], ['english_channel', 'English Channel'],
];

/** Fetch `path` now and every `refreshMs`. `path` null/'' disables it. Keeps the previous data while a refresh is in flight. */
export function useJson(apiUrl, path, refreshMs = 5 * 60 * 1000) {
  const [state, setState] = useState({ data: null, error: null });
  useEffect(() => {
    if (!path) { setState({ data: null, error: null }); return undefined; }
    let cancelled = false;
    const run = () => {
      fetch(`${apiUrl}${path}`)
        .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
        .then((d) => { if (!cancelled) setState({ data: d, error: null }); })
        .catch((e) => { if (!cancelled) setState((s) => ({ data: s.data, error: String(e.message || e) })); });
    };
    run();
    const id = refreshMs ? setInterval(run, refreshMs) : null;
    return () => { cancelled = true; if (id) clearInterval(id); };
  }, [apiUrl, path, refreshMs]);
  return state;
}

export const num = (v, nd = 2) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString(undefined, { minimumFractionDigits: nd, maximumFractionDigits: nd }));
export const exportUrl = (apiUrl, dataset) => `${apiUrl}/api/v1/intel/export/${dataset}.csv`;

export const btn = { background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5, fontFamily: S.mono, padding: '4px 10px', borderRadius: 3, cursor: 'pointer', textDecoration: 'none' };
export const input = { background: S.surface2, border: `1px solid ${S.border}`, color: S.text, fontSize: 11.5, fontFamily: S.mono, padding: '5px 7px', borderRadius: 3, minWidth: 0 };
