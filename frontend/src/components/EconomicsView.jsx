/**
 * EconomicsView.jsx
 * Economic bloc analysis — G7/G20/BRICS/ASEAN — as its own full-width
 * sub-tab in BusinessIntelBar rather than squeezed alongside Live/
 * Analysis, since a macro comparison table for 19 G20 countries plus
 * tone/commodities/EDGAR genuinely needs the room.
 *
 * Four panels per bloc, all built on data this project already has —
 * see backend services/intel/economic_blocs.py for the full reasoning
 * on each:
 *   - Macro rollup: World Bank GDP growth/inflation, one row per member
 *   - Regional tone: GDELT events filtered by bloc/member-country name
 *   - Commodity relevance: EDITORIAL judgment call, reasoning shown
 *     inline per commodity — not sourced from an API, flagged as such
 *   - SEC exposure: EDGAR full-text search for the bloc's own name
 */

import { useState, useEffect } from 'react';

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', gold: '#c9a86a',
};

const BLOC_LIST = [
  ['g7', 'G7'], ['g20', 'G20'], ['brics', 'BRICS'], ['asean', 'ASEAN'],
];

const TONE_COLOR = { deteriorating: '#ff3b3b', tense: '#f0b429', neutral: S.text3, improving: '#2ecc71' };

function useJson(url) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(d => { if (!cancelled) { setData(d); setError(null); } })
      .catch(() => { if (!cancelled) setError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [url]);
  return { data, error, loading };
}

function Panel({ title, children, span = 1 }) {
  return (
    <div style={{ gridColumn: `span ${span}`, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 6, padding: '12px 14px', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 10 }}>{title}</div>
      <div style={{ overflowY: 'auto', flex: 1 }}>{children}</div>
    </div>
  );
}

function Empty({ children }) {
  return <div style={{ fontSize: 11.5, color: S.text3, fontStyle: 'italic' }}>{children}</div>;
}

export default function EconomicsView({ apiUrl }) {
  const [blocId, setBlocId] = useState('g7');

  const info = useJson(`${apiUrl}/api/v1/intel/economic-blocs`);
  const macro = useJson(`${apiUrl}/api/v1/intel/economic-blocs/${blocId}/macro`);
  const tone = useJson(`${apiUrl}/api/v1/intel/economic-blocs/${blocId}/tone`);
  const commodities = useJson(`${apiUrl}/api/v1/intel/economic-blocs/${blocId}/commodities`);
  const [exposure, setExposure] = useState(null);
  const [exposureLoading, setExposureLoading] = useState(false);

  const blocMeta = info.data?.blocs?.find(b => b.id === blocId);

  const loadExposure = () => {
    setExposureLoading(true);
    setExposure(null);
    fetch(`${apiUrl}/api/v1/intel/economic-blocs/${blocId}/exposure`)
      .then(r => r.json())
      .then(setExposure)
      .catch(() => setExposure({ filings: [] }))
      .finally(() => setExposureLoading(false));
  };

  return (
    <div style={{ padding: '12px 16px 16px', display: 'flex', flexDirection: 'column', gap: 12, height: 340 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {BLOC_LIST.map(([id, label]) => (
          <button key={id} onClick={() => { setBlocId(id); setExposure(null); }} style={{
            background: blocId === id ? 'rgba(126,184,212,0.12)' : 'none',
            border: `1px solid ${blocId === id ? S.accent : S.border}`,
            color: blocId === id ? S.accent : S.text3, fontFamily: S.mono, fontSize: 11.5, letterSpacing: 1, textTransform: 'uppercase',
            padding: '6px 14px', borderRadius: 3, cursor: 'pointer',
          }}>
            {label}
          </button>
        ))}
        {blocMeta && (
          <span style={{ fontSize: 11, color: S.text3, marginLeft: 6 }}>{blocMeta.description}</span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, flex: 1, minHeight: 0 }}>

        <Panel title="Macro rollup (World Bank)" span={1}>
          {macro.error && <Empty>Unavailable right now.</Empty>}
          {!macro.error && !macro.data && <Empty>Loading...</Empty>}
          {macro.data && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ color: S.text3, textAlign: 'left' }}>
                  <th style={{ fontWeight: 400, paddingBottom: 6 }}>Country</th>
                  <th style={{ fontWeight: 400, paddingBottom: 6, textAlign: 'right' }}>GDP</th>
                  <th style={{ fontWeight: 400, paddingBottom: 6, textAlign: 'right' }}>Infl.</th>
                </tr>
              </thead>
              <tbody>
                {macro.data.countries.map(c => (
                  <tr key={c.iso3} style={{ borderTop: `1px solid ${S.border}` }}>
                    <td style={{ padding: '4px 0', color: S.text2 }}>{c.country}</td>
                    <td style={{ padding: '4px 0', textAlign: 'right', fontFamily: S.mono, color: c.gdp_growth == null ? S.text3 : c.gdp_growth < 0 ? '#ff7a45' : '#2ecc71' }}>
                      {c.gdp_growth != null ? `${c.gdp_growth}%` : '—'}
                    </td>
                    <td style={{ padding: '4px 0', textAlign: 'right', fontFamily: S.mono, color: S.gold }}>
                      {c.inflation != null ? `${c.inflation}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Regional tone (GDELT)" span={1}>
          {tone.error && <Empty>Unavailable right now.</Empty>}
          {tone.data && tone.data.event_count === 0 && <Empty>No recent coverage matched to this bloc's name/members yet.</Empty>}
          {tone.data && tone.data.event_count > 0 && (
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10 }}>
                <span style={{ fontSize: 22, fontFamily: S.mono, fontWeight: 700, color: TONE_COLOR[tone.data.label] || S.text }}>
                  {tone.data.avg_tone}
                </span>
                <span style={{ fontSize: 10.5, color: TONE_COLOR[tone.data.label] || S.text3, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  {tone.data.label}
                </span>
                <span style={{ fontSize: 10.5, color: S.text3, marginLeft: 'auto' }}>{tone.data.event_count} articles</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                {tone.data.sample_headlines.map((h, i) => (
                  <div key={i} style={{ fontSize: 10.5, color: S.text2, lineHeight: 1.4, borderTop: i === 0 ? 'none' : `1px solid ${S.border}`, paddingTop: i === 0 ? 0 : 5 }}>
                    {h}
                  </div>
                ))}
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Commodity relevance (editorial)" span={1}>
          {commodities.error && <Empty>Unavailable right now.</Empty>}
          {commodities.data && commodities.data.commodities.length === 0 && <Empty>No specific relevance mapped for this bloc.</Empty>}
          {commodities.data && commodities.data.commodities.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {commodities.data.commodities.map((c) => (
                <div key={c.symbol} style={{ borderBottom: `1px solid ${S.border}`, paddingBottom: 7 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                    <span style={{ fontSize: 11.5, color: S.text2 }}>{c.name || c.symbol}</span>
                    {c.price != null && (
                      <span style={{ fontFamily: S.mono, fontSize: 11, color: S.gold }}>
                        {c.price} {c.unit}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 10, color: S.text3, lineHeight: 1.4 }}>{c.reason}</div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel title="SEC exposure (EDGAR)" span={1}>
          <button onClick={loadExposure} disabled={exposureLoading} style={{
            background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, color: S.accent, fontSize: 10.5,
            fontFamily: S.mono, padding: '6px 14px', borderRadius: 3, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10,
          }}>
            {exposureLoading ? 'Searching...' : `Search filings mentioning "${blocMeta?.name || blocId.toUpperCase()}"`}
          </button>
          {exposure && exposure.filings.length === 0 && <Empty>No recent filings found.</Empty>}
          {exposure && exposure.filings.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              {exposure.filings.map((f, i) => (
                <a key={i} href={f.search_url} target="_blank" rel="noreferrer" style={{ display: 'block', fontSize: 11, color: S.text2, textDecoration: 'none', background: 'rgba(255,255,255,0.03)', border: `1px solid ${S.border}`, borderRadius: 3, padding: '6px 8px' }}>
                  <span style={{ color: S.gold }}>{f.company}</span> — {f.form_type}, {f.filed}
                </a>
              ))}
            </div>
          )}
        </Panel>

      </div>
    </div>
  );
}
