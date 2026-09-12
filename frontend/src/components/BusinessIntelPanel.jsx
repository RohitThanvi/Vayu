/**
 * BusinessIntelPanel.jsx
 * Renders the newer business-intelligence endpoints in one compact
 * panel: the unified business risk score per chokepoint, dark-vessel
 * (AIS-gap) flags, OFAC sanctions screening hits, macro context
 * (FRED + World Bank), and GDELT regional tone hotspots. Slots into
 * the Maritime tab, below SupplyChainStatus.
 *
 * Each sub-section fetches independently and fails independently —
 * one endpoint being down (e.g. FRED_API_KEY unset) shouldn't blank
 * out the whole panel.
 */

import { useState, useEffect } from 'react';

const S = {
  surface2: '#0f1419', border: '#2a3040',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a', mono: "'JetBrains Mono','Courier New',monospace",
};

const BAND_COLOR = { critical: '#ff3b3b', elevated: '#f0b429', normal: '#2ecc71' };

const REFRESH_MS = 5 * 60 * 1000;

function useJson(apiUrl, path, refreshMs = REFRESH_MS) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const run = () => {
      fetch(`${apiUrl}${path}`)
        .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
        .then(d => { if (!cancelled) { setData(d); setError(null); } })
        .catch(() => { if (!cancelled) setError(true); });
    };
    run();
    const id = setInterval(run, refreshMs);
    return () => { cancelled = true; clearInterval(id); };
  }, [apiUrl, path, refreshMs]);
  return { data, error };
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, fontFamily: S.mono, color: S.text3, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6 }}>{title}</div>
      {children}
    </div>
  );
}

function Empty({ children }) {
  return <div style={{ fontSize: 12, color: S.text3, fontStyle: 'italic' }}>{children}</div>;
}

const CHOKEPOINTS = [
  ['strait_of_hormuz', 'Strait of Hormuz'], ['strait_of_malacca', 'Strait of Malacca'],
  ['bab_el_mandeb', 'Bab-el-Mandeb'], ['suez_canal', 'Suez Canal'],
  ['strait_of_gibraltar', 'Strait of Gibraltar'], ['panama_canal', 'Panama Canal'],
  ['english_channel', 'English Channel'],
];

function EdgarExposureLookup({ apiUrl }) {
  const [selected, setSelected] = useState(CHOKEPOINTS[0][0]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const lookup = () => {
    setLoading(true);
    setResult(null);
    fetch(`${apiUrl}/api/v1/intel/edgar-exposure/${selected}`)
      .then(r => r.json())
      .then(setResult)
      .catch(() => setResult({ filings: [], count: 0 }))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
        <select value={selected} onChange={e => setSelected(e.target.value)}
          style={{ flex: 1, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 12, fontFamily: S.mono, padding: '6px 8px', borderRadius: 3 }}>
          {CHOKEPOINTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <button onClick={lookup} disabled={loading}
          style={{ background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 11, fontFamily: S.mono, padding: '6px 12px', borderRadius: 3, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          {loading ? '...' : 'Check'}
        </button>
      </div>
      {result && result.filings.length === 0 && <Empty>No recent filings mentioning this chokepoint.</Empty>}
      {result && result.filings.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {result.filings.map((f, i) => (
            <a key={i} href={f.search_url} target="_blank" rel="noreferrer" style={{ display: 'block', fontSize: 11.5, color: S.text2, textDecoration: 'none', background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 10px' }}>
              <span style={{ color: S.gold }}>{f.company}</span> — {f.form_type}, filed {f.filed}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BusinessIntelPanel({ apiUrl }) {
  const risk = useJson(apiUrl, '/api/v1/intel/business-risk');
  const dark = useJson(apiUrl, '/api/v1/intel/dark-vessels');
  const sanc = useJson(apiUrl, '/api/v1/intel/sanctions-screen');
  const macro = useJson(apiUrl, '/api/v1/intel/macro', 30 * 60 * 1000);
  const tone = useJson(apiUrl, '/api/v1/intel/geo-tone');

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <Section title="Risk score by chokepoint">
        {risk.error && <Empty>Unavailable right now.</Empty>}
        {!risk.error && !risk.data && <Empty>Loading...</Empty>}
        {risk.data && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {risk.data.chokepoints.map(cp => (
              <div key={cp.chokepoint} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 10px' }}>
                <span style={{ fontSize: 12, color: S.text2 }}>{cp.chokepoint_display}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 10, fontFamily: S.mono, color: BAND_COLOR[cp.band], textTransform: 'uppercase', letterSpacing: 0.5 }}>{cp.band}</span>
                  <span style={{ fontSize: 14, fontFamily: S.mono, color: S.text, fontWeight: 700 }}>{cp.score}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Dark vessels (AIS gaps)">
        {dark.error && <Empty>Unavailable right now.</Empty>}
        {dark.data && dark.data.flags.length === 0 && <Empty>No dark-vessel events currently flagged.</Empty>}
        {dark.data && dark.data.flags.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {dark.data.flags.slice(0, 5).map((f, i) => (
              <div key={i} style={{ fontSize: 11.5, color: S.text2, lineHeight: 1.5, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 10px' }}>
                {f.note}
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Sanctions screen (OFAC)">
        {sanc.error && <Empty>Unavailable right now.</Empty>}
        {sanc.data && !sanc.data.list_status.loaded && <Empty>Sanctions list not loaded yet.</Empty>}
        {sanc.data && sanc.data.list_status.loaded && sanc.data.hits.length === 0 && <Empty>No tracked vessels match the SDN list.</Empty>}
        {sanc.data && sanc.data.hits.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sanc.data.hits.slice(0, 5).map((h, i) => (
              <div key={i} style={{ fontSize: 11.5, color: '#ff8080', background: 'rgba(255,59,59,0.08)', border: '1px solid rgba(255,59,59,0.3)', borderRadius: 3, padding: '7px 10px' }}>
                {h.name} (MMSI {h.mmsi}) — name match, verify before acting
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Macro context">
        {macro.error && <Empty>Unavailable right now.</Empty>}
        {macro.data && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
            {Object.entries(macro.data.us || {}).map(([k, v]) => (
              <div key={k} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 9px' }}>
                <div style={{ fontSize: 9.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{k.replace(/_/g, ' ')}</div>
                <div style={{ fontSize: 13, fontFamily: S.mono, color: S.gold }}>{v.value}{v.yoy_pct !== undefined ? ` (${v.yoy_pct > 0 ? '+' : ''}${v.yoy_pct}% YoY)` : ''}</div>
              </div>
            ))}
            {Object.entries(macro.data.global || {}).map(([k, v]) => (
              <div key={k} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 9px' }}>
                <div style={{ fontSize: 9.5, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{k.replace(/_/g, ' ')}</div>
                <div style={{ fontSize: 13, fontFamily: S.mono, color: S.accent }}>{v.value}%</div>
              </div>
            ))}
            {macro.data && !macro.data.fred_configured && (
              <div style={{ gridColumn: '1 / -1', fontSize: 10.5, color: S.text3, fontStyle: 'italic' }}>
                US figures need FRED_API_KEY configured (free) — showing global-only data.
              </div>
            )}
          </div>
        )}
      </Section>

      <Section title="SEC exposure (EDGAR)">
        <EdgarExposureLookup apiUrl={apiUrl} />
      </Section>

      <Section title="Regional tone (GDELT)">
        {tone.error && <Empty>Unavailable right now.</Empty>}
        {tone.data && tone.data.regions.length === 0 && <Empty>Not enough recent coverage cached yet.</Empty>}
        {tone.data && tone.data.regions.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {tone.data.regions.slice(0, 4).map((r, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: S.text2, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '7px 10px' }}>
                <span>({r.lat.toFixed(0)}, {r.lon.toFixed(0)}) — {r.label}</span>
                <span style={{ fontFamily: S.mono, color: r.avg_tone < 0 ? '#ff8080' : '#7fd48a' }}>{r.avg_tone}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
