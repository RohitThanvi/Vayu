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
 * duplicated once and animated with a linear translateX loop, which is
 * the standard trick for a seamless infinite scroll without measuring
 * content width in JS.
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

function formatValue(v) {
  if (v == null) return '—';
  return v >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(2);
}

function TickerItem({ item }) {
  const changePositive = item.change_pct != null && item.change_pct >= 0;
  const changeColor = item.change_pct == null ? S.text3 : (changePositive ? S.up : S.down);
  return (
    <span style={{ display:'inline-flex', alignItems:'baseline', gap:6, padding:'0 20px', fontFamily:S.mono, fontSize:12, whiteSpace:'nowrap' }}>
      <span style={{ color:S.text2, letterSpacing:0.5 }}>{item.name}</span>
      <span style={{ color:'#e8e8e8' }}>${formatValue(item.value)}</span>
      <span style={{ color:S.text3, fontSize:10 }}>{item.unit}</span>
      {item.change_pct != null && (
        <span style={{ color: changeColor }}>{changePositive ? '▲' : '▼'} {Math.abs(item.change_pct).toFixed(2)}%</span>
      )}
    </span>
  );
}

export default function CommodityTicker({ apiUrl }) {
  const { commodities, lastError } = useCommodityTicker(apiUrl);

  if (commodities.length === 0) {
    // Nothing to show yet (still loading, or ALPHAVANTAGE_API_KEY isn't
    // configured) — stay invisible rather than show an empty bar or a
    // scary error strip for something that's an optional extra layer.
    return null;
  }

  const items = [...commodities, ...commodities];   // duplicated once for the seamless loop

  return (
    <div style={{
      flexShrink:0, height:28, width:'100%',
      background:S.bg, borderTop:`1px solid ${S.border}`, overflow:'hidden',
      display:'flex', alignItems:'center',
    }}>
      <div style={{
        flexShrink:0, padding:'0 14px', height:'100%', display:'flex', alignItems:'center',
        background:S.bg, borderRight:`1px solid ${S.border}`, zIndex:1,
        fontFamily:S.mono, fontSize:10, letterSpacing:1.5, color:S.text3, textTransform:'uppercase',
      }}>
        Global Markets
      </div>
      <div style={{ flex:1, overflow:'hidden', position:'relative', height:'100%' }}>
        <div className="vayu-ticker-track" style={{ display:'flex', alignItems:'center', height:'100%', width:'max-content' }}>
          {items.map((item, i) => <TickerItem key={`${item.symbol}-${i}`} item={item} />)}
        </div>
      </div>
      {lastError && (
        <div style={{ flexShrink:0, padding:'0 10px', fontFamily:S.mono, fontSize:9, color:S.text3 }} title={lastError}>
          ⚠
        </div>
      )}
      <style>{`
        @keyframes vayu-ticker-scroll {
          from { transform: translateX(0); }
          to { transform: translateX(-50%); }
        }
        .vayu-ticker-track {
          animation: vayu-ticker-scroll 60s linear infinite;
        }
        .vayu-ticker-track:hover {
          animation-play-state: paused;
        }
      `}</style>
    </div>
  );
}
