/**
 * LiveGoesPanel.jsx — genuinely live (not archival, not composite)
 * satellite imagery for the Orbital tab: NOAA's GOES-East (GOES-19) and
 * GOES-West (GOES-18) full-disk GeoColor images, refreshed on the same
 * ~10-minute cadence the satellites themselves image on.
 *
 * Why GOES and not something with global coverage: geostationary
 * weather satellites are the only free, keyless source that's actually
 * LIVE in a meaningful sense (updated within minutes) rather than
 * "recent" (hours/days). The tradeoff is real and worth stating plainly
 * rather than glossing over: coverage is two fixed hemispheres (the
 * Americas/Atlantic via GOES-East, the Pacific via GOES-West — see
 * COVERAGE_NOTE below), not global, and resolution is weather-satellite
 * scale (cloud systems, storms), not anything you could pick out a
 * building in.
 *
 * Deliberately NOT including Himawari-8/9 (which would cover Asia-
 * Pacific, filling a real gap in GOES's coverage) — NICT's free
 * real-time feed for it explicitly prohibits commercial use in its own
 * terms of service, which isn't a safe fit for a product rather than a
 * personal/research script.
 */

import { useState, useEffect, useCallback } from 'react';

// Mirrors the shared shell theme variables (see App.jsx's S object) so
// this panel flips with the light/dark toggle automatically.
const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  border: 'var(--vayu-border)',
  surface2: 'var(--vayu-surface2)',
  text: 'var(--vayu-text)',
  text3: 'var(--vayu-text3)',
  accent: 'var(--vayu-accent)',
};

const SATELLITES = {
  east: {
    label: 'GOES-East (GOES-19)',
    coverage: 'Americas & Atlantic',
    url: 'https://cdn.star.nesdis.noaa.gov/GOES19/ABI/FD/GEOCOLOR/1808x1808.jpg',
  },
  west: {
    label: 'GOES-West (GOES-18)',
    coverage: 'Pacific',
    url: 'https://cdn.star.nesdis.noaa.gov/GOES18/ABI/FD/GEOCOLOR/1808x1808.jpg',
  },
};

const REFRESH_MS = 10 * 60 * 1000; // matches GOES's own ~10-min full-disk imaging cadence — refreshing faster than that would just re-fetch the same image

export default function LiveGoesPanel() {
  const [which, setWhich] = useState('east');
  const [cacheBust, setCacheBust] = useState(() => Date.now());
  const [lastRefreshed, setLastRefreshed] = useState(() => new Date());
  const [expanded, setExpanded] = useState(false);
  const [imgError, setImgError] = useState(false);

  const refresh = useCallback(() => {
    setCacheBust(Date.now());
    setLastRefreshed(new Date());
    setImgError(false);
  }, []);

  useEffect(() => {
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const sat = SATELLITES[which];
  const imgSrc = `${sat.url}?t=${cacheBust}`;

  return (
    <div style={{ margin: '0 14px 12px', border: `1px solid ${S.border}`, borderRadius: 3, overflow: 'hidden', background: S.surface2 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', borderBottom: `1px solid ${S.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff5c5c', boxShadow: '0 0 5px #ff5c5c', flexShrink: 0 }} />
          <span style={{ fontSize: 11.5, fontFamily: S.mono, letterSpacing: 1, color: S.text }}>LIVE — NOAA GOES</span>
        </div>
        <button onClick={refresh} title="Refresh now" style={{ background: 'transparent', border: 'none', color: S.text3, cursor: 'pointer', fontSize: 13, padding: 2 }}>⟳</button>
      </div>

      <div style={{ display: 'flex', gap: 4, padding: '6px 10px 0' }}>
        {Object.entries(SATELLITES).map(([id, s]) => (
          <button key={id} onClick={() => { setWhich(id); setImgError(false); }}
            style={{
              flex: 1, fontSize: 10.5, fontFamily: S.mono, padding: '4px 6px', borderRadius: 3, cursor: 'pointer',
              background: which === id ? 'rgba(126,184,212,0.14)' : 'transparent',
              border: `1px solid ${which === id ? S.accent : S.border}`,
              color: which === id ? S.accent : S.text3,
            }}>
            {id === 'east' ? 'East' : 'West'}
          </button>
        ))}
      </div>

      <button onClick={() => setExpanded(true)} style={{ display: 'block', width: '100%', padding: '8px 10px', background: 'transparent', border: 'none', cursor: 'pointer' }}>
        {imgError ? (
          <div style={{ fontSize: 10.5, color: S.text3, fontFamily: S.mono, padding: '20px 0', textAlign: 'center' }}>
            Image didn't load — NOAA's feed can be briefly unavailable during satellite eclipse/maintenance windows. Try refreshing.
          </div>
        ) : (
          <img src={imgSrc} alt={`${sat.label} full disk`} onError={() => setImgError(true)}
            style={{ width: '100%', borderRadius: 3, display: 'block', aspectRatio: '1/1', objectFit: 'cover' }} />
        )}
      </button>

      <div style={{ padding: '6px 10px 8px', fontSize: 10, color: S.text3, fontFamily: S.mono, lineHeight: 1.4 }}>
        {sat.coverage} · updates ~every 10 min · last refreshed {lastRefreshed.toLocaleTimeString()}
      </div>

      {expanded && (
        <div onClick={() => setExpanded(false)} style={{
          position: 'fixed', inset: 0, zIndex: 2100, background: 'rgba(0,0,0,0.75)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24, cursor: 'zoom-out',
        }}>
          <img src={imgSrc} alt={`${sat.label} full disk, expanded`} style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 4 }} />
        </div>
      )}
    </div>
  );
}
