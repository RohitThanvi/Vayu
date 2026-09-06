/**
 * SupplyChainStatus.jsx
 * Renders GET /api/v1/intel/supply-chain-correlation — for each of the
 * 7 monitored maritime chokepoints, its current vessel-traffic status
 * against a 14-day baseline (see backend timeseries_store.py), plus the
 * day-over-day move of commodities that route heavily through it.
 * Slots into the existing Maritime tab, replacing the previous static
 * "Monitored chokepoints" name list with live status.
 */

import { useState, useEffect } from 'react';

const S = {
  surface2: '#0f1419', border: '#2a3040', border2: '#3a4250',
  text: '#ffffff', text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', mono: "'JetBrains Mono','Courier New',monospace",
};

const STATUS_COLOR = { normal: '#2ecc71', elevated: '#f0b429', disrupted: '#ff3b3b', insufficient_history: 'rgba(255,255,255,0.4)' };
const STATUS_LABEL = { normal: 'NORMAL', elevated: 'ELEVATED', disrupted: 'DISRUPTED', insufficient_history: 'GATHERING DATA' };

const REFRESH_MS = 5 * 60 * 1000;   // matches the backend's 15-min snapshot cadence closely enough to stay current without over-polling

export default function SupplyChainStatus({ apiUrl }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const fetchData = () => {
      fetch(`${apiUrl}/api/v1/intel/supply-chain-correlation`)
        .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
        .then(d => { if (!cancelled) { setData(d); setError(null); } })
        .catch(() => { if (!cancelled) setError('Supply-chain status unavailable right now.'); });
    };
    fetchData();
    const id = setInterval(fetchData, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [apiUrl]);

  if (error) {
    return <div style={{ fontSize: 12, color: S.text3 }}>{error}</div>;
  }
  if (!data) {
    return <div style={{ fontSize: 12, color: S.text3 }}>Loading chokepoint status...</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {data.chokepoints.map(cp => {
        const color = STATUS_COLOR[cp.status] || S.text3;
        return (
          <div key={cp.key} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '9px 10px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 13, color: S.text2, fontFamily: S.mono }}>{cp.name}</span>
              <span style={{
                fontSize: 9.5, fontFamily: S.mono, letterSpacing: 1, fontWeight: 700, color,
                border: `1px solid ${color}66`, borderRadius: 3, padding: '2px 6px',
              }}>
                {STATUS_LABEL[cp.status] || cp.status}
              </span>
            </div>
            {cp.status !== 'insufficient_history' ? (
              <div style={{ fontSize: 11, color: S.text3, marginTop: 4 }}>
                {cp.current_count} vessels now vs. {cp.baseline_mean} baseline
                {cp.deviation_pct != null && (
                  <span style={{ color: cp.deviation_pct < 0 ? '#ff7a45' : '#2ecc71', fontWeight: 600 }}>
                    {' '}({cp.deviation_pct > 0 ? '+' : ''}{cp.deviation_pct}%)
                  </span>
                )}
              </div>
            ) : (
              <div style={{ fontSize: 11, color: S.text3, marginTop: 4 }}>
                Building baseline — {cp.sample_count} samples so far
              </div>
            )}
            {cp.related_commodities?.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                {cp.related_commodities.map(c => c.change_pct == null ? null : (
                  <span key={c.symbol} style={{ fontSize: 10.5, fontFamily: S.mono, color: c.change_pct > 0 ? '#2ecc71' : c.change_pct < 0 ? '#ff7a45' : S.text3, background: S.border, borderRadius: 3, padding: '2px 5px' }}>
                    {c.name} {c.change_pct > 0 ? '▲' : c.change_pct < 0 ? '▼' : ''} {Math.abs(c.change_pct).toFixed(1)}%
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
