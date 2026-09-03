/**
 * CommodityTicker.jsx
 * A persistent bottom marquee bar showing global commodity prices — an
 * additional intelligence layer, separate from the map/analysis tabs,
 * always visible regardless of which sidebar tab is active.
 *
 * NOT MCX (India's commodity exchange) real-time data — MCX's live feed
 * is a paid exchange subscription with no free/legal alternative. This
 * shows real global futures prices (crude oil, natural gas, metals, agri
 * commodities) via Yahoo Finance's unofficial keyless chart API instead
 * (~15-20min delayed, refreshed every few hours server-side) — an
 * unofficial/reverse-engineered endpoint, not a documented API with an
 * SLA, but extremely widely used and has been stable for years. That's
 * a real, honestly-accepted tradeoff for something free — see backend
 * services/intel/commodity_prices.py for the full history (originally
 * used Alpha Vantage, whose 25-requests/day cap proved unworkable
 * against Render's free-tier cold-start pattern).
 *
 * Pure CSS marquee (no external ticker library) — a single row of items
 * duplicated once and animated with a linear translateX loop. Hover
 * detail cards are also pure CSS (:hover reveals an absolutely-positioned
 * sibling) rather than React state, so they work naturally against the
 * duplicated-track loop without extra re-render logic.
 */

import { useCommodityTicker } from '../hooks/useCommodityTicker';

const S = {
  bg: '#0d0f12',
  border: '#2a2f36',
  text2: '#9a9fa8',
  text3: '#6a6f78',
  mono: "'JetBrains Mono', 'SF Mono', Consolas, monospace",
  up: '#7ec88f',
  down: '#e8746b',
};

// Per-commodity color, keyed by category — used for both the icon stroke
// and the item's name text, so each family reads as visually related
// (energy amber, metals warm gold/silver, agri green) while each
// commodity still gets its own distinct icon shape below.
const CATEGORY_COLOR = { energy: '#e8a33d', metal: '#d4c48a', agri: '#8fc97e' };

function formatValue(v) {
  if (v == null) return '—';
  return v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(2);
}

function formatTime(unixSeconds) {
  if (!unixSeconds) return null;
  try {
    return new Date(unixSeconds * 1000).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch { return null; }
}

// ── Hand-drawn per-commodity icons (inline SVG, no external assets) ────────
function CommodityIcon({ symbol, color, size = 18 }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round' };
  switch (symbol) {
    case 'CL=F':
    case 'BZ=F':   // crude oil — droplet
      return <svg {...p}><path d="M12 2.5C12 2.5 5.5 11 5.5 15.5a6.5 6.5 0 0 0 13 0C18.5 11 12 2.5 12 2.5Z" fill={`${color}33`} /></svg>;
    case 'NG=F':    // natural gas — flame
      return <svg {...p}><path d="M12 2s5 5 5 10a5 5 0 0 1-10 0c0-1.5.8-2.6 1.5-3.5-.3 1.3.2 2 1 2.3C8.8 9 9 6.5 11 5c-.3 1.5.3 2 1 2 1.2 0 2-2 0-5Z" fill={`${color}33`} /></svg>;
    case 'HG=F':    // copper — ingot
      return <svg {...p}><path d="M4 15 6 9h12l2 6-2 4H6l-2-4Z" fill={`${color}33`} /><path d="M6 9 8 4h8l2 5" /></svg>;
    case 'GC=F':    // gold — bar with a shine line
      return <svg {...p}><path d="M5 16 7 8h10l2 8-2 4H7l-2-4Z" fill={`${color}55`} /><path d="M9 10 15 13" strokeWidth={1.2} /></svg>;
    case 'ZW=F':    // wheat — stalk with grain heads
      return <svg {...p}><path d="M12 21V6" /><path d="M12 6c-2-1-3 0-3 1.5S10 9 12 9M12 6c2-1 3 0 3 1.5S14 9 12 9" /><path d="M12 10c-1.7-1-2.6 0-2.6 1.2S10.3 13 12 13M12 10c1.7-1 2.6 0 2.6 1.2S13.7 13 12 13" /><path d="M12 14c-1.5-.8-2.2 0-2.2 1S10.5 16.8 12 16" /></svg>;
    case 'ZC=F':    // corn — cob with kernel dots
      return <svg {...p}><path d="M9 4c6 0 8 5 6 12-1 3.5-5 5-8 3-2-4-1-11 2-15Z" fill={`${color}33`} /><circle cx="10.5" cy="8" r="0.6" fill={color} stroke="none" /><circle cx="13" cy="9" r="0.6" fill={color} stroke="none" /><circle cx="10" cy="11.5" r="0.6" fill={color} stroke="none" /><circle cx="12.5" cy="12.5" r="0.6" fill={color} stroke="none" /><circle cx="10.5" cy="15" r="0.6" fill={color} stroke="none" /></svg>;
    case 'CT=F':    // cotton — fluffy boll
      return <svg {...p}><circle cx="9" cy="9" r="3.2" fill={`${color}44`} /><circle cx="15" cy="9" r="3.2" fill={`${color}44`} /><circle cx="12" cy="12.5" r="3.6" fill={`${color}55`} /><path d="M12 15v6" /></svg>;
    case 'SB=F':    // sugar — crystal cube
      return <svg {...p}><path d="M12 3 20 8v8l-8 5-8-5V8Z" fill={`${color}33`} /><path d="M12 3v18M4 8l8 5 8-5" /></svg>;
    case 'KC=F':    // coffee — bean
      return <svg {...p}><ellipse cx="12" cy="12" rx="6.5" ry="9" transform="rotate(20 12 12)" fill={`${color}33`} /><path d="M12 4c-1 3 1 5 0 8s1 5 0 8" /></svg>;
    default:
      return <svg {...p}><circle cx="12" cy="12" r="7" /></svg>;
  }
}

function TickerItem({ item }) {
  const changePositive = item.change_pct != null && item.change_pct >= 0;
  const changeColor = item.change_pct == null ? S.text3 : (changePositive ? S.up : S.down);
  const color = CATEGORY_COLOR[item.category] || S.text2;
  const lastUpdated = formatTime(item.market_time);

  return (
    <span className="vayu-ticker-item" style={{ display:'inline-flex', alignItems:'center', gap:8, padding:'0 24px', fontFamily:S.mono, fontSize:13, whiteSpace:'nowrap', position:'relative', cursor:'default' }}>
      <CommodityIcon symbol={item.symbol} color={color} />
      <span style={{ color, letterSpacing:0.5, fontWeight:600 }}>{item.name}</span>
      <span style={{ color:'#f0f0f0', fontWeight:600 }}>${formatValue(item.value)}</span>
      <span style={{ color:S.text3, fontSize:11 }}>{item.unit}</span>
      {item.change_pct != null && (
        <span style={{ color: changeColor }}>{changePositive ? '▲' : '▼'} {Math.abs(item.change_pct).toFixed(2)}%</span>
      )}

      {/* Hover detail card — pure CSS reveal, no JS state. display/flexDirection
          live in the <style> block below (not here) so the .vayu-ticker-item:hover
          rule can actually override them — an inline style here would always
          win over a stylesheet rule regardless of :hover, silently making the
          hover reveal never work. */}
      <span className="vayu-ticker-tooltip" style={{
        position:'absolute', bottom:'calc(100% + 10px)', left:'50%', transform:'translateX(-50%)',
        background:'#15181d', border:`1px solid ${S.border}`, borderRadius:6, padding:'12px 14px',
        minWidth:220, boxShadow:'0 8px 24px rgba(0,0,0,0.5)', zIndex:100,
        gap:5, textAlign:'left', whiteSpace:'normal',
      }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:2 }}>
          <CommodityIcon symbol={item.symbol} color={color} size={20} />
          <span style={{ color, fontWeight:700, fontSize:13 }}>{item.name}</span>
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'auto auto', gap:'3px 12px', fontSize:11, color:S.text2 }}>
          <span style={{ color:S.text3 }}>Symbol</span><span>{item.symbol}</span>
          {item.exchange && <><span style={{ color:S.text3 }}>Exchange</span><span>{item.exchange}</span></>}
          {item.prev_close != null && <><span style={{ color:S.text3 }}>Prev. close</span><span>${formatValue(item.prev_close)}</span></>}
          {(item.day_low != null && item.day_high != null) && <><span style={{ color:S.text3 }}>Day range</span><span>${formatValue(item.day_low)} – ${formatValue(item.day_high)}</span></>}
          {(item.week52_low != null && item.week52_high != null) && <><span style={{ color:S.text3 }}>52-week range</span><span>${formatValue(item.week52_low)} – ${formatValue(item.week52_high)}</span></>}
          {lastUpdated && <><span style={{ color:S.text3 }}>Last quote</span><span>{lastUpdated}</span></>}
        </div>
        <div style={{ fontSize:9, color:S.text3, marginTop:4, lineHeight:1.4 }}>
          Yahoo Finance, ~15–20min delayed. Not MCX real-time data.
        </div>
      </span>
    </span>
  );
}

export default function CommodityTicker({ apiUrl }) {
  const { commodities, lastError } = useCommodityTicker(apiUrl);

  if (commodities.length === 0) {
    // Nothing to show yet (still loading, or the backend hasn't
    // completed its first refresh) — stay invisible rather than show
    // an empty bar or a scary error strip for an optional extra layer.
    return null;
  }

  const items = [...commodities, ...commodities];   // duplicated once for the seamless loop

  return (
    <div style={{
      flexShrink:0, height:44, width:'100%',
      background:S.bg, borderTop:`1px solid ${S.border}`, overflow:'visible',
      display:'flex', alignItems:'center', position:'relative',
    }}>
      <div style={{
        flexShrink:0, padding:'0 16px', height:'100%', display:'flex', alignItems:'center',
        background:S.bg, borderRight:`1px solid ${S.border}`, zIndex:1,
        fontFamily:S.mono, fontSize:11, letterSpacing:1.5, color:S.text3, textTransform:'uppercase',
      }}>
        Global Markets
      </div>
      <div style={{ flex:1, overflowX:'hidden', overflowY:'visible', position:'relative', height:'100%' }}>
        <div className="vayu-ticker-track" style={{ display:'flex', alignItems:'center', height:'100%', width:'max-content' }}>
          {items.map((item, i) => <TickerItem key={`${item.symbol}-${i}`} item={item} />)}
        </div>
      </div>
      {lastError && (
        <div style={{ flexShrink:0, padding:'0 12px', fontFamily:S.mono, fontSize:10, color:S.text3 }} title={lastError}>
          ⚠
        </div>
      )}
      <style>{`
        @keyframes vayu-ticker-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
        .vayu-ticker-track {
          animation: vayu-ticker-scroll 70s linear infinite;
        }
        .vayu-ticker-track:hover {
          animation-play-state: paused;
        }
        .vayu-ticker-tooltip {
          display: none;
        }
        .vayu-ticker-item:hover .vayu-ticker-tooltip {
          display: flex;
        }
      `}</style>
    </div>
  );
}
