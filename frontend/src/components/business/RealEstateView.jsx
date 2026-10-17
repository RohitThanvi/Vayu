/**
 * RealEstateView.jsx — Business tab › "Real Estate" (Location Intelligence).
 *
 * Score one place, or compare up to four, on liveability & access, growth momentum and hazard
 * resilience — using only free data (OpenStreetMap, VIIRS night lights, JRC water history, Copernicus DEM,
 * Landsat, USGS). It does not estimate prices: no free open price data exists for India, and the view says so.
 * Every number is expandable down to its value, threshold, weight and source.
 */

import { useState, useRef, useEffect } from 'react';
import { friendlyError } from './theme';
export { friendlyError };

const S = {
  mono: "'JetBrains Mono','Courier New',monospace",
  surface: 'var(--vayu-surface)', surface2: 'var(--vayu-surface2)', border: 'var(--vayu-border)',
  text: 'var(--vayu-text)', text2: 'var(--vayu-text2)', text3: 'var(--vayu-text3)', accent: 'var(--vayu-accent)', gold: 'var(--vayu-gold)',
};
const scoreColor = (s) => s == null ? '#8a8f98' : s >= 70 ? '#7ec88f' : s >= 45 ? '#e8c15c' : '#e8746b';
const GROUP_ORDER = ['liveability', 'growth', 'resilience'];

const COORD_RE = /^\s*-?\d+(\.\d+)?\s*[,;]\s*-?\d+(\.\d+)?\s*$/;
const _geoCache = new Map();

/** Place name -> {lat, lon} using the USER's browser (their own IP gets its own Nominatim allowance, instead of every
 *  user sharing the server's). Returns null on any failure so the caller falls back to letting the server try. */
export async function geocodeInBrowser(q) {
  const key = q.trim().toLowerCase();
  if (COORD_RE.test(q)) return null;                          // the server parses coordinates itself
  if (_geoCache.has(key)) return _geoCache.get(key);
  try {
    const r = await fetch('https://nominatim.openstreetmap.org/search?' + new URLSearchParams({ q, format: 'jsonv2', limit: '1' }));
    if (!r.ok) return null;
    const d = await r.json();
    const hit = Array.isArray(d) && d[0] ? { lat: Number(d[0].lat), lon: Number(d[0].lon) } : null;
    if (hit && Number.isFinite(hit.lat) && Number.isFinite(hit.lon)) { _geoCache.set(key, hit); return hit; }
  } catch { /* fall through */ }
  return null;
}

function Bar({ label, score, coverage }) {
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10.5, fontFamily: S.mono, color: S.text3 }}>
        <span>{label}</span>
        <span style={{ color: scoreColor(score) }}>{score == null ? 'not scored' : score} {coverage < 0.8 && score != null ? `· ${Math.round(coverage * 100)}% data` : ''}</span>
      </div>
      <div style={{ height: 6, background: S.surface2, borderRadius: 3, border: `1px solid ${S.border}` }}>
        <div style={{ width: `${score ?? 0}%`, height: '100%', background: scoreColor(score), borderRadius: 3 }} />
      </div>
    </div>
  );
}

export function toCsv(result) {
  const rows = [['location', 'rank', 'overall_score', 'group', 'group_score', 'metric', 'label', 'value', 'unit', 'utility', 'weight', 'source']];
  (result.locations || []).forEach(l => GROUP_ORDER.forEach(g => (l.groups[g]?.criteria || []).forEach(c =>
    rows.push([l.name, l.rank ?? '', l.overall_score ?? '', g, l.groups[g].score ?? '', c.metric, c.label, c.value ?? '', c.unit, c.utility ?? '', c.weight, c.source]))));
  return rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
}

const AMENITY_ROWS = [
  ['hospital', 'Hospital / clinic'], ['school', 'School / college'], ['market', 'Supermarket / market'],
  ['rail', 'Rail / metro'], ['bus', 'Bus stop'], ['park', 'Park'], ['road', 'Major road'],
];
const OSM_STATUS_TEXT = {
  unavailable: 'Amenity data unavailable — OpenStreetMap could not be read, so liveability is not scored. Try again in a minute.',
  sparse: 'OpenStreetMap has very little mapped around here, so liveability is not scored (a mapping gap is not the same as no amenities).',
};

function Amenities({ amenities, status }) {
  if (status && status !== 'ok') {
    return <div data-testid="osm-status" style={{ fontSize: 11, color: '#e8c15c', lineHeight: 1.45, margin: '6px 0' }}>{OSM_STATUS_TEXT[status] || OSM_STATUS_TEXT.unavailable}</div>;
  }
  const nearby = amenities?.nearby;
  if (!nearby) return null;
  return (
    <div data-testid="amenities" style={{ margin: '8px 0 4px' }}>
      <div style={{ fontSize: 10, color: S.accent, fontFamily: S.mono, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2 }}>Nearest amenities</div>
      {AMENITY_ROWS.map(([kind, label]) => {
        const items = nearby[kind] || [];
        const first = items[0];
        const reach = amenities.search_radius_km?.[kind];
        return (
          <details key={kind} style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3 }}>
            <summary style={{ cursor: items.length > 1 ? 'pointer' : 'default', display: 'flex', justifyContent: 'space-between', gap: 6 }}>
              <span>{label}</span>
              <span style={{ color: first ? S.text2 : S.text3 }}>
                {first ? `${first.name || first.type || 'unnamed'} · ${first.distance_km} km ${first.direction}` : `none mapped within ${reach} km`}
              </span>
            </summary>
            {items.slice(1).map((it, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', paddingLeft: 10 }}>
                <span>{it.name || it.type || 'unnamed'}</span><span>{it.distance_km} km {it.direction}</span>
              </div>
            ))}
          </details>
        );
      })}
      <div style={{ fontSize: 9.5, color: S.text3, marginTop: 2 }}>© OpenStreetMap contributors · distances are straight-line; "none mapped" can mean unmapped.</div>
    </div>
  );
}

export function explainSourceFailures(errors) {
  const e = (errors || []).join(' ').toLowerCase();
  const parts = [];
  if (/earthengine|earth engine|\bee\b|gcp_project|credential|not initiali[sz]ed|eeexception|permission|quota|asset/.test(e)) {
    parts.push('Earth Engine did not return data (not set up on this server, out of quota, or temporarily failing) — this blanks the satellite criteria: night lights, terrain, flood history and surface temperature.');
  }
  if (/timed ?out|timeout|asyncio|cancel/.test(e)) parts.push('Some satellite queries timed out; a second run is usually faster because results are cached.');
  return parts;
}

function NoDataBanner({ loc }) {
  const allEmpty = GROUP_ORDER.every(g => (loc.groups[g]?.criteria || []).every(c => c.value == null));
  if (!allEmpty) return null;
  const why = explainSourceFailures(loc.errors);
  return (
    <div role="alert" data-testid="no-data" style={{ fontSize: 11, color: '#e8746b', lineHeight: 1.45, margin: '6px 0' }}>
      <b>No measurements came back for this location.</b> {why.length ? why.join(' ') : 'Every data source failed for it; the reasons are listed below.'}
      {loc.errors?.length > 0 && (
        <details style={{ marginTop: 4, color: S.text3, fontFamily: S.mono, fontSize: 10 }}>
          <summary style={{ cursor: 'pointer' }}>Technical details ({loc.errors.length})</summary>
          {loc.errors.map((x, i) => <div key={i}>{x}</div>)}
        </details>
      )}
    </div>
  );
}

const inr = (n) => `₹${Math.round(n).toLocaleString('en-IN')}`;

function PriceBlock({ price }) {
  if (!price) return null;
  const { price_per_sqft: p, circle_rate_per_sqft: cr, premium_to_circle_pct: prem, score_per_1000: spk, value_rank: vr, value_rank_of: vo } = price;
  return (
    <div data-testid="price" style={{ margin: '8px 0 4px' }}>
      <div style={{ fontSize: 10, color: S.accent, fontFamily: S.mono, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2 }}>Price (entered by you)</div>
      <div style={{ fontSize: 11, fontFamily: S.mono, color: S.text2, lineHeight: 1.5 }}>
        {p != null && <div>Market: {inr(p)} / sq ft</div>}
        {cr != null && <div>Circle rate: {inr(cr)} / sq ft</div>}
        {prem != null && <div>{prem >= 0 ? `${prem}% above` : `${Math.abs(prem)}% below`} the circle rate</div>}
        {spk != null && <div>Score per ₹1,000/sq ft: {spk}{vr != null ? ` · value rank #${vr} of ${vo}` : ''}</div>}
      </div>
      <div style={{ fontSize: 9.5, color: S.text3, marginTop: 2 }}>Not verified by Vayu. The value rank only compares the places you entered; it is not a valuation.</div>
    </div>
  );
}

function LocationCard({ loc }) {
  const c = loc.context || {};
  const chips = [
    c.air_quality && `AQI ${Math.round(c.air_quality.us_aqi)} · ${c.air_quality.category}`,
    c.terrain?.elevation_m != null && `Elev ${Math.round(c.terrain.elevation_m)} m`,
    c.seismic?.quake_max_mag != null && `Max quake M${c.seismic.quake_max_mag}`,
    c.land_cover?.builtup != null && `Built-up ${c.land_cover.builtup}%`,
  ].filter(Boolean);
  return (
    <div style={{ flex: '1 1 300px', minWidth: 290, background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 4, padding: '10px 12px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 13, color: S.text, fontWeight: 600 }}>{loc.rank ? `#${loc.rank} ` : ''}{loc.name}</span>
        <span style={{ fontSize: 22, fontFamily: S.mono, fontWeight: 800, color: scoreColor(loc.overall_score) }}>{loc.overall_score ?? '—'}</span>
      </div>
      <div style={{ fontSize: 10, color: S.text3, fontFamily: S.mono, marginBottom: 8 }}>{loc.lat}, {loc.lon} · {loc.radius_km} km radius · {Math.round(loc.overall_coverage * 100)}% evidence</div>
      {GROUP_ORDER.map(g => <Bar key={g} label={loc.groups[g].label} score={loc.groups[g].score} coverage={loc.groups[g].coverage} />)}
      <NoDataBanner loc={loc} />
      <PriceBlock price={loc.price} />
      <Amenities amenities={c.amenities} status={c.osm_status} />
      {loc.flags?.length > 0 && <div style={{ fontSize: 11, color: '#e8746b', lineHeight: 1.45, margin: '6px 0' }}>⚠ {loc.flags.join(' ')}</div>}
      {(loc.strengths?.length > 0 || loc.weaknesses?.length > 0) && (
        <div style={{ fontSize: 11, color: S.text2, lineHeight: 1.5 }}>
          {loc.strengths?.length > 0 && <div><b>Strengths:</b> {loc.strengths.join('; ')}</div>}
          {loc.weaknesses?.length > 0 && <div><b>Weak spots:</b> {loc.weaknesses.join('; ')}</div>}
        </div>
      )}
      {chips.length > 0 && <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
        {chips.map((t, i) => <span key={i} style={{ fontSize: 10, fontFamily: S.mono, color: S.text3, border: `1px solid ${S.border}`, borderRadius: 10, padding: '1px 7px' }}>{t}</span>)}
      </div>}
      <details style={{ marginTop: 8 }}>
        <summary style={{ cursor: 'pointer', fontSize: 10.5, color: S.text3, fontFamily: S.mono }}>Criteria (value · utility · weight)</summary>
        {GROUP_ORDER.map(g => (
          <div key={g} style={{ marginTop: 6 }}>
            <div style={{ fontSize: 10, color: S.accent, fontFamily: S.mono, textTransform: 'uppercase', letterSpacing: 1 }}>{loc.groups[g].label}</div>
            {loc.groups[g].criteria.map((k, i) => (
              <div key={i} title={k.note || `${k.source} · good at ${k.good_at}, bad at ${k.bad_at}`}
                   style={{ display: 'flex', justifyContent: 'space-between', gap: 6, fontSize: 10.5, fontFamily: S.mono, color: S.text3 }}>
                <span>{k.label}</span>
                <span>{k.value == null ? 'no data' : `${k.value} ${k.unit}`}{k.utility != null ? ` · ${Math.round(k.utility * 100)}%` : ''} · w{Math.round(k.weight * 100)}{k.note ? ' *' : ''}</span>
              </div>
            ))}
          </div>
        ))}
      </details>
    </div>
  );
}

export default function RealEstateView({ apiUrl, pollMs = 3000, geoGapMs = 1100 }) {
  const [rows, setRows] = useState(['', '']);
  const [prices, setPrices] = useState([['', ''], ['', '']]);   // per row: [market ₹/sq ft, circle rate ₹/sq ft]
  const [radius, setRadius] = useState(2);
  const [profiles, setProfiles] = useState([{ id: 'balanced', label: 'Balanced' }]);
  const [profile, setProfile] = useState('balanced');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ pct: 0, label: '' });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const timer = useRef(null);

  useEffect(() => {
    fetch(`${apiUrl}/api/v1/realestate/profiles`).then(r => r.json()).then(d => d.profiles && setProfiles(d.profiles)).catch(() => {});
    return () => timer.current && clearInterval(timer.current);
  }, [apiUrl]);

  const setRow = (i, v) => setRows(rs => rs.map((r, j) => (j === i ? v : r)));
  const setPrice = (i, k, v) => setPrices(ps => { const n = rows.map((_, j) => ps[j] || ['', '']); n[i] = n[i].map((x, kk) => (kk === k ? v : x)); return n; });
  const priceNum = (v) => { const n = Number(String(v).replace(/[,₹\s]/g, '')); return Number.isFinite(n) && n > 0 ? n : undefined; };

  const run = async () => {
    const typedIdx = rows.map((r, i) => [r.trim(), i]).filter(([r]) => r);
    const typed = typedIdx.map(([r]) => r);
    if (!typed.length) { setError({ text: 'Enter at least one place or "lat, lon".', retry: false }); return; }
    setBusy(true); setError(null); setResult(null); setProgress({ pct: 2, label: 'Looking up places' });
    const fail = (status, detail) => { if (timer.current) clearInterval(timer.current); setError(friendlyError(status, detail)); setBusy(false); };
    try {
      const locations = [];
      let lookedUp = 0;
      for (const [q, ri] of typedIdx) {
        let hit = null;
        if (!COORD_RE.test(q) && !_geoCache.has(q.toLowerCase()) && lookedUp++ > 0) await new Promise(r => setTimeout(r, geoGapMs));   // Nominatim: ~1 request/second
        hit = await geocodeInBrowser(q);
        const pr = prices[ri] || ['', ''];
        const priced = { price_per_sqft: priceNum(pr[0]), circle_rate_per_sqft: priceNum(pr[1]) };   // undefined values are dropped by JSON
        locations.push(hit ? { name: q, lat: hit.lat, lon: hit.lon, ...priced } : { query: q, ...priced });   // fall back to the server's own lookup
      }
      setProgress({ pct: 4, label: 'Queued' });
      const res = await fetch(`${apiUrl}/api/v1/realestate/assess`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locations, radius_km: Number(radius), profile }),
      });
      if (!res.ok) return fail(res.status, (await res.json().catch(() => ({}))).detail);
      const { request_id } = await res.json();
      timer.current = setInterval(async () => {
        try {
          const r = await fetch(`${apiUrl}/api/v1/realestate/assess/${request_id}`);
          if (!r.ok) return fail(r.status, (await r.json().catch(() => ({}))).detail);
          const d = await r.json();
          if (d.status === 'done') { clearInterval(timer.current); setResult(d.result); setBusy(false); }
          else setProgress({ pct: d.progress_pct || 0, label: d.stage_label || 'Working' });
        } catch (e) { fail(0, e.message); }
      }, pollMs);
    } catch (e) { fail(0, e.message); }
  };

  const download = () => {
    const blob = new Blob([toCsv(result)], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'vayu-location-intelligence.csv'; a.click();
  };

  return (
    <div style={{ display: 'flex', height: 330, overflow: 'hidden' }}>
      <div style={{ width: 300, flexShrink: 0, borderRight: `1px solid ${S.border}`, padding: '10px 14px', overflowY: 'auto' }}>
        <div style={{ fontSize: 10.5, fontFamily: S.mono, color: S.text3, letterSpacing: 1, textTransform: 'uppercase', marginBottom: 6 }}>Locations (1-4)</div>
        {rows.map((r, i) => (
          <input key={i} value={r} onChange={e => setRow(i, e.target.value)} placeholder={i === 0 ? 'e.g. Mansarovar, Jaipur' : 'Add another to compare, or "26.85, 75.76"'}
                 style={{ width: '100%', boxSizing: 'border-box', marginBottom: 5, background: S.surface2, border: `1px solid ${S.border}`, color: S.text, borderRadius: 3, padding: '6px 8px', fontSize: 12 }} />
        ))}
        {rows.length < 4 && <button onClick={() => { setRows(rs => [...rs, '']); setPrices(ps => [...ps, ['', '']]); }} style={{ background: 'none', border: 'none', color: S.accent, fontSize: 11, cursor: 'pointer', padding: 0, marginBottom: 8 }}>+ add location</button>}
        <details style={{ marginBottom: 8 }}>
          <summary style={{ cursor: 'pointer', fontSize: 11, color: S.accent }}>Add prices you have seen (optional)</summary>
          <div style={{ fontSize: 10, color: S.text3, lineHeight: 1.4, margin: '4px 0' }}>₹ per sq ft. Vayu does not look up prices; whatever you type is shown back and compared.</div>
          {rows.map((r, i) => (
            <div key={i} style={{ marginBottom: 4 }}>
              <div style={{ fontSize: 10, color: S.text3, fontFamily: S.mono, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.trim() || `Location ${i + 1}`}</div>
              <div style={{ display: 'flex', gap: 4 }}>
                <input aria-label={`Market price per sq ft, location ${i + 1}`} inputMode="numeric" placeholder="Market ₹/sq ft" value={(prices[i] || ['', ''])[0]} onChange={e => setPrice(i, 0, e.target.value)}
                       style={{ flex: 1, minWidth: 0, background: S.surface2, border: `1px solid ${S.border}`, color: S.text, borderRadius: 3, padding: '4px 6px', fontSize: 11 }} />
                <input aria-label={`Circle rate per sq ft, location ${i + 1}`} inputMode="numeric" placeholder="Circle rate ₹/sq ft" value={(prices[i] || ['', ''])[1]} onChange={e => setPrice(i, 1, e.target.value)}
                       style={{ flex: 1, minWidth: 0, background: S.surface2, border: `1px solid ${S.border}`, color: S.text, borderRadius: 3, padding: '4px 6px', fontSize: 11 }} />
              </div>
            </div>
          ))}
        </details>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <select value={radius} onChange={e => setRadius(e.target.value)} style={{ flex: 1, background: S.surface2, color: S.text, border: `1px solid ${S.border}`, borderRadius: 3, padding: 5, fontSize: 11 }}>
            {[1, 2, 3, 4].map(r => <option key={r} value={r}>{r} km radius</option>)}
          </select>
          <select value={profile} onChange={e => setProfile(e.target.value)} style={{ flex: 1.4, background: S.surface2, color: S.text, border: `1px solid ${S.border}`, borderRadius: 3, padding: 5, fontSize: 11 }}>
            {profiles.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </div>
        <button disabled={busy} onClick={run} style={{ width: '100%', background: busy ? S.surface2 : S.accent, color: busy ? S.text3 : '#0b0d10', border: 'none', borderRadius: 3, padding: '8px 0', fontWeight: 700, fontSize: 12, cursor: busy ? 'default' : 'pointer' }}>
          {busy ? `${progress.label} (${progress.pct}%)` : 'Analyse'}
        </button>
        {error && (
          <div role="alert" style={{ fontSize: 11, color: '#e8746b', marginTop: 8, lineHeight: 1.4 }}>
            {error.text}
            {error.retry && <div><button onClick={run} style={{ marginTop: 6, background: 'none', border: `1px solid ${S.border}`, color: S.accent, borderRadius: 3, fontSize: 11, padding: '3px 9px', cursor: 'pointer' }}>Try again</button></div>}
          </div>
        )}
        <div style={{ fontSize: 10, color: S.text3, marginTop: 10, lineHeight: 1.45 }}>
          Free data only. No prices or returns are estimated — no free open price data exists for India; you can add prices you have seen and Vayu will compare them. Takes 1-3 minutes.
        </div>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '10px 14px' }}>
        {!result && !busy && <div style={{ fontSize: 12, color: S.text3, lineHeight: 1.6, maxWidth: 560 }}>
          Score a location on <b>liveability &amp; access</b>, <b>growth momentum</b> and <b>hazard resilience</b>, or compare up to four.
          Choose a profile (family buyer, growth investor, lender/insurer) to change how the three are weighted. Every score expands down to its source data.
        </div>}
        {busy && <div style={{ fontSize: 12, color: S.text3 }}>{progress.label}… this runs several satellite and map queries per location.</div>}
        {result && (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11, fontFamily: S.mono, color: S.text3 }}>Profile: {result.profile_label} · weights {GROUP_ORDER.map(g => `${g} ${Math.round(result.weights[g] * 100)}%`).join(' / ')}</span>
              <button onClick={download} style={{ marginLeft: 'auto', background: 'none', border: `1px solid ${S.border}`, color: S.accent, borderRadius: 3, fontSize: 11, padding: '3px 9px', cursor: 'pointer' }}>Export CSV</button>
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-start' }}>
              {result.locations.map((l, i) => <LocationCard key={i} loc={l} />)}
            </div>
            {result.caveats?.length > 0 && <div style={{ fontSize: 11, color: S.text3, marginTop: 10, lineHeight: 1.5 }}><b>Caveats:</b> {result.caveats.join(' ')}</div>}
            {result.price_resources?.length > 0 && (
              <details data-testid="price-resources" style={{ fontSize: 10.5, color: S.text3, marginTop: 8, lineHeight: 1.5 }}>
                <summary style={{ cursor: 'pointer', color: S.accent }}>Where to find real prices and circle rates (official sources)</summary>
                {result.price_resources.map((r, i) => (
                  <div key={i} style={{ marginTop: 3 }}><a href={r.url} target="_blank" rel="noopener noreferrer" style={{ color: S.accent }}>{r.label}</a> — {r.note}</div>
                ))}
              </details>
            )}
            <div style={{ fontSize: 10.5, color: S.text3, marginTop: 8, lineHeight: 1.5 }}><b>Not included:</b> {result.not_included.join('; ')}.</div>
            <div style={{ fontSize: 10.5, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>{result.methodology}</div>
            <div style={{ fontSize: 10, color: S.text3, marginTop: 6, lineHeight: 1.5 }}>Sources: {result.sources.join(' · ')}</div>
          </>
        )}
      </div>
    </div>
  );
}
