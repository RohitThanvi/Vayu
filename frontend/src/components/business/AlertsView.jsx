/** Alerts: rules you set, evaluated in this browser. Honest about what that means and what it doesn't do. */
import { useState } from 'react';
import { RULE_TYPES, describeRule } from '../../lib/alertRules';
import { S, useJson, btn, input, CHOKEPOINTS } from './theme';
import { Panel, Empty } from './ui';

const NAMES = Object.fromEntries(CHOKEPOINTS);
const ago = (t) => { if (!t) return 'not yet'; const m = Math.round((Date.now() - t) / 60000); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };

export default function AlertsView({ apiUrl, alerts }) {
  const [type, setType] = useState('price_above');
  const [f, setF] = useState({ symbol: '', level: '', pct: '', chokepoint: CHOKEPOINTS[0][0], band: 'elevated', siteId: '' });
  const [err, setErr] = useState(null);
  const sites = useJson(apiUrl, type === 'site_signal' ? '/api/v1/intel/strategic-sites' : null, 0).data;
  const needs = RULE_TYPES[type].needs;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const submit = (e) => {
    e.preventDefault();
    const site = (sites?.sites || sites || []).find?.((x) => x.id === f.siteId);
    const draft = { type, ...(needs.includes('symbol') ? { symbol: f.symbol } : {}), ...(needs.includes('level') ? { level: f.level } : {}), ...(needs.includes('pct') ? { pct: f.pct } : {}),
      ...(needs.includes('chokepoint') ? { chokepoint: f.chokepoint, band: f.band } : {}), ...(needs.includes('siteId') ? { siteId: f.siteId, siteName: site?.name } : {}) };
    const result = alerts.addRule(draft);
    setErr(result);
    if (!result) setF({ ...f, symbol: '', level: '', pct: '' });
  };

  return (
    <div style={{ display: 'flex', height: '100%', overflowX: 'auto' }}>
      <div style={{ width: 400, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        <Panel title="New alert">
          <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <select value={type} onChange={(e) => { setType(e.target.value); setErr(null); }} aria-label="Alert type" style={input}>
              {Object.entries(RULE_TYPES).map(([id, t]) => <option key={id} value={id}>{t.label}</option>)}
            </select>
            {needs.includes('symbol') && <input value={f.symbol} onChange={set('symbol')} placeholder="Symbol, e.g. BZ=F or RELIANCE.NS" aria-label="Symbol" style={input} />}
            {needs.includes('level') && <input value={f.level} onChange={set('level')} placeholder="Price level" inputMode="decimal" aria-label="Price level" style={input} />}
            {needs.includes('pct') && <input value={f.pct} onChange={set('pct')} placeholder="Percent, e.g. 3" inputMode="decimal" aria-label="Percent" style={input} />}
            {needs.includes('chokepoint') && (
              <div style={{ display: 'flex', gap: 5 }}>
                <select value={f.chokepoint} onChange={set('chokepoint')} aria-label="Chokepoint" style={{ ...input, flex: 1 }}>{CHOKEPOINTS.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select>
                <select value={f.band} onChange={set('band')} aria-label="Risk band" style={input}><option value="elevated">elevated</option><option value="critical">critical</option></select>
              </div>
            )}
            {needs.includes('siteId') && (
              <select value={f.siteId} onChange={set('siteId')} aria-label="Site" style={input}>
                <option value="">Choose a site…</option>
                {(sites?.sites || sites || []).map?.((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            )}
            <button type="submit" style={btn}>Add alert</button>
            {err && <div style={{ fontSize: 10.5, color: '#e0c23c' }}>{err}</div>}
          </form>
        </Panel>
        <Panel title="How alerts work">
          <div style={{ fontSize: 10.5, color: S.text3, lineHeight: 1.55 }}>
            Alerts are checked every 5 minutes <b>in this browser</b>, so they work while a Vayu tab is open (any tab of the app). An alert fires when its
            condition <i>becomes</i> true, then stays quiet until it clears or 6 hours pass. It never fires on stale or missing data. Email and WhatsApp
            alerts are not offered yet: they need verified addresses so nobody can be signed up by someone else.
          </div>
          {alerts.permission === 'default' && <button onClick={alerts.requestPermission} style={{ ...btn, marginTop: 8 }}>Enable desktop notifications</button>}
          {alerts.permission === 'denied' && <div style={{ fontSize: 10.5, color: S.text3, marginTop: 6 }}>Desktop notifications are blocked in this browser; alerts still appear in the app.</div>}
        </Panel>
      </div>

      <div style={{ width: 430, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        <Panel title={`Your alerts (${alerts.rules.length})`} right={<span style={{ fontSize: 9.5, color: S.text3, fontFamily: S.mono }}>checked {ago(alerts.status.lastChecked)}</span>}>
          {!alerts.rules.length && <Empty>No alerts yet. Add one on the left.</Empty>}
          {alerts.rules.map((r) => (
            <div key={r.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0', borderBottom: `1px solid ${S.border}`, opacity: r.enabled === false ? 0.5 : 1 }}>
              <input type="checkbox" checked={r.enabled !== false} onChange={() => alerts.toggleRule(r.id)} aria-label={`Enable ${describeRule(r, NAMES)}`} />
              <div style={{ flex: 1, fontSize: 11.5, color: S.text2 }}>
                {describeRule(r, NAMES)}
                {alerts.status.waiting.includes(r.id) && <span style={{ color: '#e0c23c', fontSize: 10, marginLeft: 6 }}>waiting for fresh data</span>}
              </div>
              <button onClick={() => alerts.removeRule(r.id)} aria-label="Delete alert" style={{ background: 'none', border: 'none', color: S.text3, cursor: 'pointer', fontSize: 14 }}>×</button>
            </div>
          ))}
          {alerts.status.error && <div style={{ fontSize: 10.5, color: '#e0c23c', marginTop: 6 }}>{alerts.status.error}</div>}
        </Panel>
      </div>

      <div style={{ flex: 1, minWidth: 320, padding: '10px 14px', overflowY: 'auto', boxSizing: 'border-box' }}>
        <Panel title="Recent alerts" right={alerts.history.length ? <button onClick={alerts.clearHistory} style={{ ...btn, padding: '1px 7px' }}>Clear</button> : null}>
          {!alerts.history.length && <Empty>Nothing has fired yet.</Empty>}
          {alerts.history.map((h) => (
            <div key={h.id} style={{ padding: '5px 0', borderBottom: `1px solid ${S.border}` }}>
              <div style={{ fontSize: 11.5, color: S.text }}>{h.message}</div>
              <div style={{ fontSize: 9.5, color: S.text3 }}>{new Date(h.at).toLocaleString()}</div>
            </div>
          ))}
        </Panel>
      </div>
    </div>
  );
}
