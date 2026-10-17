/**
 * Event impact: how a stock moved around dated events, compared with the market and with its own ordinary days.
 * Events are dates you type or USGS earthquakes in a region. Descriptive only — never a claim of cause.
 */
import { useState } from 'react';
import { S, useJson, btn, input, friendlyError } from './theme';
import { Panel, Empty } from './ui';
import { Bars } from './Financials';

const WIN = { 0: 'Event day', 1: '+1 day', 3: '+3 days', 5: '+5 days' };
export const pct = (v) => (v == null || !Number.isFinite(Number(v)) ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(2)}%`);
const tone = (v) => (v == null ? S.text3 : v > 0 ? '#3fb27f' : v < 0 ? '#e05c5c' : S.text2);

export default function EventImpact({ apiUrl, symbol }) {
  const [mode, setMode] = useState('dates');
  const [dates, setDates] = useState('');
  const [region, setRegion] = useState('');
  const [mag, setMag] = useState('6.0');
  const [win, setWin] = useState('5');
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);
  const opts = useJson(apiUrl, '/api/v1/intel/company/event-study/options', 0).data;
  const regions = opts?.regions || [];

  const run = async () => {
    setBusy(true); setErr(null); setRes(null);
    const qs = mode === 'dates'
      ? `/api/v1/intel/company/event-study?symbol=${encodeURIComponent(symbol)}&dates=${encodeURIComponent(dates.trim())}`
      : `/api/v1/intel/company/event-study/quakes?symbol=${encodeURIComponent(symbol)}&region=${encodeURIComponent(region || regions[0]?.id || '')}&min_magnitude=${encodeURIComponent(mag)}`;
    try {
      const r = await fetch(`${apiUrl}${qs}`);
      if (!r.ok) { setErr(friendlyError(r.status, (await r.json().catch(() => ({}))).detail)); }
      else { setRes(await r.json()); }
    } catch (e) { setErr(friendlyError(0, e.message)); }
    setBusy(false);
  };
  const canRun = !busy && (mode === 'dates' ? dates.trim().length >= 8 : regions.length > 0);

  const events = res?.events || [];
  const summary = res?.summary || {};
  const points = events.map((e) => ({ end: e.aligned_date, value: e.ret?.[win]?.abnormal ?? 0, label: e.label, has: e.ret?.[win] != null })).filter((p) => p.has);

  return (
    <Panel title="Event impact — how it moved around events">
      <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
        {[['dates', 'My dates'], ['quakes', 'Earthquakes']].map(([k, l]) => (
          <button key={k} aria-pressed={mode === k} onClick={() => { setMode(k); setRes(null); setErr(null); }} style={{ ...btn, opacity: mode === k ? 1 : 0.55 }}>{l}</button>))}
      </div>
      {mode === 'dates' ? (
        <textarea aria-label="Event dates" rows={3} value={dates} onChange={(e) => setDates(e.target.value)} placeholder={'One per line or comma-separated, YYYY-MM-DD with an optional label:\n2024-03-01 Port strike\n2024-05-02'}
                  style={{ ...input, width: '100%', boxSizing: 'border-box', fontFamily: S.mono, fontSize: 11 }} />
      ) : (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <select aria-label="Earthquake region" value={region || regions[0]?.id || ''} onChange={(e) => setRegion(e.target.value)} style={input}>
            {regions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}</select>
          <select aria-label="Minimum magnitude" value={mag} onChange={(e) => setMag(e.target.value)} style={input}>
            {(opts?.magnitudes || [6.0]).map((m) => <option key={m} value={String(m.toFixed ? m.toFixed(1) : m)}>M ≥ {Number(m).toFixed(1)}</option>)}</select>
          {!regions.length && <span style={{ fontSize: 10.5, color: S.text3 }}>Region list unavailable right now.</span>}
        </div>
      )}
      <div style={{ marginTop: 6 }}><button onClick={run} disabled={!canRun} style={{ ...btn, opacity: canRun ? 1 : 0.5 }}>{busy ? 'Running…' : 'Run event study'}</button></div>
      {err && <div role="alert" style={{ fontSize: 11, color: '#e8746b', marginTop: 8, lineHeight: 1.4 }}>{err.text}</div>}

      {res && (
        <div style={{ marginTop: 10 }}>
          {res.source && <div style={{ fontSize: 10.5, color: S.text3, marginBottom: 4 }}>{res.source.name} · {res.source.region} · M ≥ {res.source.min_magnitude} · {res.source.events_found} qualifying days. {res.source.note}</div>}
          {!events.length && <Empty>{res.notes?.[0] || 'No events could be analysed.'}</Empty>}
          {events.length > 0 && (
            <>
              <div style={{ overflowX: 'auto' }}>
                <table data-testid="es-summary" style={{ borderCollapse: 'collapse', fontSize: 11, fontFamily: S.mono, color: S.text2, width: '100%' }}>
                  <thead><tr style={{ color: S.text3, textAlign: 'right' }}>
                    {['Window', 'n', 'Avg vs market', 'Median', '% up', '± s.e.', 'Rank vs random days'].map((h, i) => <th key={h} style={{ padding: '2px 6px', textAlign: i ? 'right' : 'left', fontWeight: 400 }}>{h}</th>)}</tr></thead>
                  <tbody>{(res.windows || []).map((k) => { const s = summary[String(k)]; if (!s) return null; return (
                    <tr key={k} style={{ borderTop: `1px solid ${S.border}`, textAlign: 'right' }}>
                      <td style={{ padding: '2px 6px', textAlign: 'left' }}>{WIN[k]}</td><td>{s.n}</td>
                      <td style={{ color: tone(s.mean_abnormal_pct) }}>{pct(s.mean_abnormal_pct)}</td><td style={{ color: tone(s.median_abnormal_pct) }}>{pct(s.median_abnormal_pct)}</td>
                      <td>{s.share_positive_pct}%</td><td>{s.std_error_pct != null ? `${s.std_error_pct}` : '—'}</td>
                      <td>{s.percentile_vs_random_windows != null ? `${s.percentile_vs_random_windows}th pct` : s.note ? 'n < 3' : '—'}</td></tr>); })}</tbody>
                </table>
              </div>
              <div style={{ fontSize: 10, color: S.text3, margin: '4px 0' }}>
                "Avg vs market" = stock move minus {res.benchmark} over the same days. "Rank vs random days": where this average sits among averages of the same number of randomly chosen windows from the stock's own last five years (near 0 or 100 = unusual; not a significance test, not proof of cause).
                {res.baseline?.[win] && <> Ordinary {WIN[win]} range for this stock (5th–95th pct): {pct(res.baseline[win].p05)} to {pct(res.baseline[win].p95)}.</>}
              </div>
              <div style={{ display: 'flex', gap: 4, alignItems: 'center', margin: '6px 0 2px' }}>
                <span style={{ fontSize: 10, color: S.text3 }}>Chart window:</span>
                {(res.windows || []).map((k) => <button key={k} aria-pressed={win === String(k)} onClick={() => setWin(String(k))} style={{ ...btn, padding: '1px 7px', opacity: win === String(k) ? 1 : 0.55 }}>{WIN[k]}</button>)}
              </div>
              <Bars points={points} fmt={pct} label={`Abnormal return by event, ${WIN[win]}`} tip={(p, v) => `${p.end}: ${v}${p.label ? ` — ${p.label}` : ''}`} />
              <div style={{ maxHeight: 220, overflowY: 'auto', marginTop: 6 }}>
                <table data-testid="es-events" style={{ borderCollapse: 'collapse', fontSize: 10.5, fontFamily: S.mono, color: S.text2, width: '100%' }}>
                  <tbody>{events.map((e) => (
                    <tr key={e.date} style={{ borderTop: `1px solid ${S.border}` }}>
                      <td style={{ padding: '2px 6px', whiteSpace: 'nowrap' }}>{e.aligned_date}</td>
                      <td style={{ color: S.text3, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={e.note || ''}>{e.label || ''}{e.note ? ' *' : ''}{e.overlaps_other_event ? ' ⧉' : ''}</td>
                      {(res.windows || []).map((k) => { const r = e.ret?.[String(k)]; return <td key={k} style={{ textAlign: 'right', padding: '2px 6px', color: tone(r?.abnormal) }}>{r ? `${pct(r.abnormal)}${r.unusual ? ' ★' : ''}` : '—'}</td>; })}
                    </tr>))}</tbody>
                </table>
              </div>
              <div style={{ fontSize: 9.5, color: S.text3, marginTop: 3 }}>★ outside the stock's ordinary 5–95% range for that window · ⧉ overlaps another event's window · * aligned to the next trading day. Columns: {(res.windows || []).map((k) => WIN[k]).join(', ')}.</div>
            </>
          )}
          {res.skipped?.length > 0 && <div style={{ fontSize: 10, color: S.text3, marginTop: 4 }}>Skipped: {res.skipped.map((s) => `${s.date} (${s.reason})`).join('; ')}.</div>}
          {res.input_problems?.length > 0 && <div style={{ fontSize: 10, color: '#e0c23c', marginTop: 4 }}>Ignored input: {res.input_problems.join('; ')}.</div>}
          <div style={{ fontSize: 9.5, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>{(res.notes || []).map((n, i) => <div key={i}>• {n}</div>)}</div>
        </div>
      )}
    </Panel>
  );
}
