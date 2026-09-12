/**
 * BusinessIntelBar.jsx
 * A full-width, collapsible bottom panel for the Maritime tab — replaces
 * cramming the business-intel features (risk score, dark vessels,
 * sanctions, macro, GDELT tone, EDGAR lookup) into the narrow 330px
 * left sidebar alongside vessel controls, which made that sidebar too
 * dense to actually read. This gets its own wide horizontal strip
 * below the map instead, laid out in columns like a real terminal's
 * bottom ticker/dashboard, and collapses to a thin handle when not
 * needed so it doesn't permanently eat map height.
 *
 * Rendered only for the Maritime tab, desktop only (App.jsx already
 * hides/adapts a lot of chrome on mobile; a 6-column horizontal strip
 * isn't a good mobile pattern anyway — mobile keeps the compact
 * version, if any, in the existing panel flow).
 */

import { useState, useEffect } from 'react';

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  bg: '#0a0c0f', surface: '#0d1117', surface2: '#0f1419',
  border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a',
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

function Column({ title, width = 230, children }) {
  return (
    <div style={{ width, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  );
}

function Empty({ children }) {
  return <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic' }}>{children}</div>;
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
      <div style={{ display: 'flex', gap: 5, marginBottom: 8 }}>
        <select value={selected} onChange={e => setSelected(e.target.value)}
          style={{ flex: 1, minWidth: 0, background: S.surface2, border: `1px solid ${S.border}`, color: S.text2, fontSize: 11, fontFamily: S.mono, padding: '5px 6px', borderRadius: 3 }}>
          {CHOKEPOINTS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <button onClick={lookup} disabled={loading}
          style={{ background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5, fontFamily: S.mono, padding: '5px 10px', borderRadius: 3, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5, flexShrink: 0 }}>
          {loading ? '...' : 'Check'}
        </button>
      </div>
      {result && result.filings.length === 0 && <Empty>No recent filings.</Empty>}
      {result && result.filings.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          {result.filings.slice(0, 4).map((f, i) => (
            <a key={i} href={f.search_url} target="_blank" rel="noreferrer" style={{ display: 'block', fontSize: 11, color: S.text2, textDecoration: 'none', background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 8px' }}>
              <span style={{ color: S.gold }}>{f.company}</span> — {f.form_type}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BusinessIntelBar({ apiUrl }) {
  const [open, setOpen] = useState(true);
  const risk = useJson(apiUrl, '/api/v1/intel/business-risk');
  const dark = useJson(apiUrl, '/api/v1/intel/dark-vessels');
  const sanc = useJson(apiUrl, '/api/v1/intel/sanctions-screen');
  const macro = useJson(apiUrl, '/api/v1/intel/macro', 30 * 60 * 1000);
  const tone = useJson(apiUrl, '/api/v1/intel/geo-tone');

  const worstBand = risk.data?.chokepoints?.[0]?.band; // already sorted worst-first by the API

  return (
    <div style={{ flexShrink: 0, background: S.surface, borderTop: `1px solid ${S.border}`, display: 'flex', flexDirection: 'column' }}>
      <button onClick={() => setOpen(o => !o)} style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '7px 16px', background: 'none', border: 'none',
        borderBottom: open ? `1px solid ${S.border}` : 'none', cursor: 'pointer', width: '100%', textAlign: 'left',
      }}>
        <span style={{ fontSize: 11, fontFamily: S.mono, color: S.accent, letterSpacing: 1.5, textTransform: 'uppercase' }}>
          Business Intelligence
        </span>
        {worstBand && !open && (
          <span style={{ fontSize: 10, fontFamily: S.mono, color: BAND_COLOR[worstBand], letterSpacing: 0.5, textTransform: 'uppercase' }}>
            ● highest risk: {worstBand}
          </span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: S.text3, fontFamily: S.mono }}>{open ? '▾ collapse' : '▸ expand'}</span>
      </button>

      {open && (
        <div style={{ display: 'flex', height: 230, overflowX: 'auto' }}>
          <Column title="Risk score by chokepoint" width={230}>
            {risk.error && <Empty>Unavailable right now.</Empty>}
            {!risk.error && !risk.data && <Empty>Loading...</Empty>}
            {risk.data && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {risk.data.chokepoints.map(cp => (
                  <div key={cp.chokepoint} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 9px' }}>
                    <span style={{ fontSize: 11.5, color: S.text2 }}>{cp.chokepoint_display}</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: BAND_COLOR[cp.band] }} />
                      <span style={{ fontSize: 13, fontFamily: S.mono, color: S.text, fontWeight: 700 }}>{cp.score}</span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Column>

          <Column title="Dark vessels (AIS gaps)" width={240}>
            {dark.error && <Empty>Unavailable right now.</Empty>}
            {dark.data && dark.data.flags.length === 0 && <Empty>No dark-vessel events flagged.</Empty>}
            {dark.data && dark.data.flags.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {dark.data.flags.slice(0, 5).map((f, i) => (
                  <div key={i} style={{ fontSize: 11, color: S.text2, lineHeight: 1.45, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 9px' }}>
                    {f.note}
                  </div>
                ))}
              </div>
            )}
          </Column>

          <Column title="Sanctions screen (OFAC)" width={230}>
            {sanc.error && <Empty>Unavailable right now.</Empty>}
            {sanc.data && !sanc.data.list_status.loaded && <Empty>Sanctions list not loaded yet.</Empty>}
            {sanc.data && sanc.data.list_status.loaded && sanc.data.hits.length === 0 && <Empty>No tracked vessels match the SDN list.</Empty>}
            {sanc.data && sanc.data.hits.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {sanc.data.hits.slice(0, 5).map((h, i) => (
                  <div key={i} style={{ fontSize: 11, color: '#ff8080', background: 'rgba(255,59,59,0.08)', border: '1px solid rgba(255,59,59,0.3)', borderRadius: 3, padding: '6px 9px' }}>
                    {h.name} (MMSI {h.mmsi})
                  </div>
                ))}
              </div>
            )}
          </Column>

          <Column title="Macro context" width={220}>
            {macro.error && <Empty>Unavailable right now.</Empty>}
            {macro.data && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {Object.entries(macro.data.us || {}).map(([k, v]) => (
                  <div key={k} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 8px' }}>
                    <div style={{ fontSize: 9, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{k.replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 12.5, fontFamily: S.mono, color: S.gold }}>{v.value}{v.yoy_pct !== undefined ? ` (${v.yoy_pct > 0 ? '+' : ''}${v.yoy_pct}%)` : ''}</div>
                  </div>
                ))}
                {Object.entries(macro.data.global || {}).map(([k, v]) => (
                  <div key={k} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 8px' }}>
                    <div style={{ fontSize: 9, color: S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>{k.replace(/_/g, ' ')}</div>
                    <div style={{ fontSize: 12.5, fontFamily: S.mono, color: S.accent }}>{v.value}%</div>
                  </div>
                ))}
                {!macro.data.fred_configured && (
                  <div style={{ fontSize: 10, color: S.text3, fontStyle: 'italic' }}>US figures need FRED_API_KEY (free).</div>
                )}
              </div>
            )}
          </Column>

          <Column title="Regional tone (GDELT)" width={230}>
            {tone.error && <Empty>Unavailable right now.</Empty>}
            {tone.data && tone.data.regions.length === 0 && <Empty>Not enough recent coverage cached yet.</Empty>}
            {tone.data && tone.data.regions.length > 0 && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {tone.data.regions.slice(0, 5).map((r, i) => (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: S.text2, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 9px' }}>
                    <span>({r.lat.toFixed(0)}, {r.lon.toFixed(0)}) {r.label}</span>
                    <span style={{ fontFamily: S.mono, color: r.avg_tone < 0 ? '#ff8080' : '#7fd48a' }}>{r.avg_tone}</span>
                  </div>
                ))}
              </div>
            )}
          </Column>

          <Column title="SEC exposure (EDGAR)" width={250}>
            <EdgarExposureLookup apiUrl={apiUrl} />
          </Column>
        </div>
      )}
    </div>
  );
}
