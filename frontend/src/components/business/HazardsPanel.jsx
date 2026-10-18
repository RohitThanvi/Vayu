/** Live natural-hazard alerts (GDACS Orange/Red + NOAA tsunami messages) with Vayu's market context. Context, never a forecast. */
import { useState } from 'react';
import { S, useJson, btn, friendlyError } from './theme';
import { Panel, Empty } from './ui';
import { pct } from './EventImpact';

const HAZ = { earthquake: 'Earthquake', cyclone: 'Cyclone', flood: 'Flood', volcano: 'Volcano', tsunami: 'Tsunami message' };
const BADGE = { critical: '#e05c5c', warn: '#e0a23c', info: '#6b7a8d' };
const WIN = { 0: 'event day', 1: '+1 day', 3: '+3 days', 5: '+5 days' };
const https = (u) => (typeof u === 'string' && u.startsWith('https://') ? u : null);

export const ago = (ts, now = Date.now()) => {
  const t = Date.parse(ts);
  if (!Number.isFinite(t)) return '';
  const m = Math.max(0, Math.round((now - t) / 60000));
  return m < 60 ? `${m} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};

function Reaction({ apiUrl, ev }) {
  const idx = (ev.exposure?.country_indices || []).find((c) => c.symbol !== '^GSPC');
  const day = typeof ev.from === 'string' ? ev.from.slice(0, 10) : null;
  const [st, setSt] = useState(null);
  if (!idx || !day) return null;
  const run = async () => {
    setSt({ busy: true });
    try {
      const r = await fetch(`${apiUrl}/api/v1/intel/company/event-study?symbol=${encodeURIComponent(idx.symbol)}&dates=${encodeURIComponent(day)}`);
      if (!r.ok) return setSt({ err: friendlyError(r.status, (await r.json().catch(() => ({}))).detail).text });
      setSt({ res: await r.json() });
    } catch (e) { setSt({ err: friendlyError(0, e.message).text }); }
  };
  const e0 = st?.res?.events?.[0];
  const rows = e0 ? (st.res.windows || []).filter((k) => e0.ret?.[String(k)]) : [];
  return (
    <div style={{ marginTop: 4 }}>
      {!st && <button style={{ ...btn, padding: '1px 8px' }} onClick={run}>Market reaction so far ({idx.label})</button>}
      {st?.busy && <span style={{ fontSize: 10.5, color: S.text3 }}>Checking…</span>}
      {st?.err && <span role="alert" style={{ fontSize: 10.5, color: '#e8746b' }}>{st.err}</span>}
      {st?.res && (
        <div data-testid="reaction" style={{ fontSize: 10.5, color: S.text2, fontFamily: S.mono, lineHeight: 1.5 }}>
          {rows.length
            ? <>{idx.label} vs {st.res.benchmark} since {day}: {rows.map((k) => `${WIN[k]} ${pct(e0.ret[String(k)].abnormal)}`).join(' · ')}</>
            : 'Too recent: no closing prices after the event yet.'}
          <div style={{ fontSize: 9.5, color: S.text3 }}>Descriptive only: other news moves markets too, and one event is one data point.</div>
        </div>
      )}
    </div>
  );
}

export default function HazardsPanel({ apiUrl }) {
  const { data, error } = useJson(apiUrl, '/api/v1/intel/hazards?limit=40', 5 * 60 * 1000);
  const [all, setAll] = useState(false);
  const events = data?.events || [];
  const shown = all ? events : events.slice(0, 6);
  return (
    <Panel title="Natural hazards — live" right={<span style={{ fontSize: 9.5, color: S.text3, fontFamily: S.mono }}>GDACS + NOAA tsunami</span>}>
      {!data && !error && <Empty>Loading…</Empty>}
      {error && !data && <Empty>Hazard alerts are unavailable right now.</Empty>}
      {data && !events.length && <Empty>No Orange/Red alerts are currently held. (They appear here within about 30 minutes of GDACS publishing them.)</Empty>}
      {shown.map((e) => {
        const ex = e.exposure || {};
        const label = e.hazard === 'tsunami' ? 'MSG' : (e.alert || e.severity || '').toUpperCase();
        return (
          <div key={e.id} data-testid="hazard" style={{ borderBottom: `1px solid ${S.border}`, padding: '6px 0' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
              <span style={{ fontSize: 9, fontFamily: S.mono, color: '#fff', background: BADGE[e.severity] || BADGE.info, borderRadius: 2, padding: '0 5px', whiteSpace: 'nowrap' }}>{label}</span>
              <span style={{ fontSize: 11.5, color: S.text }}>{e.title}</span>
            </div>
            <div style={{ fontSize: 10, color: S.text3, fontFamily: S.mono, marginTop: 2 }}>
              {[HAZ[e.hazard] || e.hazard, e.country, e.severity_text, ago(e.ts)].filter(Boolean).join(' · ')}
              {https(e.url) && <> · <a href={e.url} target="_blank" rel="noopener noreferrer" style={{ color: S.accent }}>{e.source === 'GDACS' ? 'GDACS report' : 'official bulletin'} ↗</a></>}
            </div>
            {(ex.chokepoints?.length > 0 || ex.country_indices?.length > 0) && (
              <div style={{ marginTop: 3, display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {ex.chokepoints.map((c) => (
                  <span key={c.chokepoint} data-testid="chip-choke" title={`Watch: ${(c.watch_commodities || []).join(', ') || 'n/a'}${c.bellwethers?.length ? ` · bellwethers: ${c.bellwethers.join(', ')}` : ''}`}
                        style={{ fontSize: 10, border: `1px solid ${S.border}`, borderRadius: 3, padding: '0 6px', color: S.text2 }}>{c.display} · {c.distance_km} km</span>))}
                {ex.country_indices.map((c) => <span key={c.symbol} data-testid="chip-index" style={{ fontSize: 10, border: `1px solid ${S.border}`, borderRadius: 3, padding: '0 6px', color: S.text2 }}>{c.label}</span>)}
              </div>
            )}
            <Reaction apiUrl={apiUrl} ev={e} />
          </div>
        );
      })}
      {events.length > 6 && <button style={{ ...btn, marginTop: 6 }} onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${events.length}`}</button>}
      <div style={{ fontSize: 9.5, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>
        {(data?.notes || []).map((n, i) => <div key={i}>• {n}</div>)}
        {(data?.sources || []).map((s) => <div key={s.id}>• {s.name}: {s.note}</div>)}
      </div>
    </Panel>
  );
}
