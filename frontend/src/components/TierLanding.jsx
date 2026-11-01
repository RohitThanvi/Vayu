/**
 * TierLanding.jsx — config-driven, 2D landing page for single-product (locked-tier) sites.
 *
 * Deliberately WebGL-free: the hero is an SVG "satellite vegetation tile" (a deterministic
 * pixel mosaic, like a coarse NDVI scene) instead of the 3D globe + satellite used on the
 * main landing page. Content and palette come from lib/tierLandingConfigs.js.
 */
import { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';

const MONO = "'JetBrains Mono','Courier New',monospace";
const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
const SECTIONS = [['home', 'Home'], ['capabilities', 'What it does'], ['seasons', 'Seasons'], ['trust', 'Trust'], ['developer', 'Developer']];

// Deterministic smooth-ish noise (no Math.random: the tile must render identically every time).
function hash(x, y) {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = ((h ^ (h >>> 13)) * 1274126177) | 0;
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}
function fieldValue(cx, cy) {
  // blend of coarse (parcel-scale) and fine noise, bilinear on the coarse lattice
  const gx = cx / 4, gy = cy / 4, x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
  const s = (t) => t * t * (3 - 2 * t);
  const a = hash(x0, y0), b = hash(x0 + 1, y0), c = hash(x0, y0 + 1), d = hash(x0 + 1, y0 + 1);
  const coarse = a + (b - a) * s(fx) + (c - a) * s(fy) + (a - b - c + d) * s(fx) * s(fy);
  return 0.72 * coarse + 0.28 * hash(cx + 91, cy + 17);
}
function ramp(v) {
  // low vegetation (dry soil) -> high vegetation (deep green)
  const lo = [122, 98, 62], mid = [118, 168, 84], hi = [28, 118, 62];
  const t = Math.min(1, Math.max(0, (v - 0.15) / 0.75));
  const [p, q, u] = t < 0.5 ? [lo, mid, t * 2] : [mid, hi, (t - 0.5) * 2];
  return `rgb(${Math.round(p[0] + (q[0] - p[0]) * u)},${Math.round(p[1] + (q[1] - p[1]) * u)},${Math.round(p[2] + (q[2] - p[2]) * u)})`;
}

export function VegetationTile({ cols = 64, rows = 40 }) {
  const cells = useMemo(() => {
    const out = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) out.push({ c, r, fill: ramp(fieldValue(c, r)) });
    return out;
  }, [cols, rows]);
  return (
    <svg viewBox={`0 0 ${cols} ${rows}`} preserveAspectRatio="xMidYMid slice" role="img" aria-label="Illustrative satellite vegetation tile"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }}>
      {cells.map((k) => <rect key={`${k.c}-${k.r}`} x={k.c} y={k.r} width="1.02" height="1.02" fill={k.fill} />)}
    </svg>
  );
}

export function SeasonBars({ rows, P, isMobile }) {
  const W = 480, H = 40 + rows.length * 34, left = isMobile ? 52 : 64, cw = (W - left) / 12;
  const spans = (row) => (row.from <= row.to ? [[row.from, row.to]] : [[row.from, 11], [0, row.to]]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Growing seasons: ${rows.map((r) => `${r.name} ${r.window}`).join(', ')}`} style={{ width: '100%', maxWidth: 560, display: 'block' }}>
      {MONTHS.map((m, i) => <text key={i} x={left + i * cw + cw / 2} y={16} textAnchor="middle" fontSize="11" fill={P.text3} fontFamily={MONO}>{m}</text>)}
      {rows.map((row, ri) => (
        <g key={row.name}>
          <text x={0} y={52 + ri * 34} fontSize="11.5" fill={P.text2} fontFamily={MONO}>{row.name}</text>
          {spans(row).map(([a, b], si) => (
            <rect key={si} data-season={row.name} x={left + a * cw + 1} y={36 + ri * 34} width={(b - a + 1) * cw - 2} height={22} rx={4} fill={row.color} opacity={0.88} />
          ))}
        </g>
      ))}
    </svg>
  );
}

function useReveal(rootRef) {
  const ref = useRef(null);
  const [shown, setShown] = useState(typeof IntersectionObserver === 'undefined');
  useEffect(() => {
    if (shown || !ref.current || typeof IntersectionObserver === 'undefined') return undefined;
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setShown(true); io.disconnect(); } }, { root: rootRef.current, threshold: 0.12 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [shown, rootRef]);
  return [ref, shown];
}

function Reveal({ rootRef, children, delay = 0, fill = false }) {
  const [ref, shown] = useReveal(rootRef);
  return <div ref={ref} className="tl-reveal" style={{ opacity: shown ? 1 : 0, transform: shown ? 'none' : 'translateY(18px)', transitionDelay: `${delay}ms`, ...(fill ? { height: '100%' } : null) }}>{children}</div>;
}

export default function TierLanding({ config, onEnter }) {
  const P = config.palette;
  const isMobile = useIsMobile(760);
  const scrollRef = useRef(null);
  const sectionRefs = useRef({});
  const [menuOpen, setMenuOpen] = useState(false);
  const scrollTo = useCallback((id) => { sectionRefs.current[id]?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }); setMenuOpen(false); }, []);
  const pad = isMobile ? '60px 20px' : '96px 28px';
  const eyebrow = { fontFamily: MONO, fontSize: 12, letterSpacing: 3, color: P.accent, textTransform: 'uppercase', marginBottom: 8 };
  const h2 = { fontFamily: 'Georgia, serif', fontSize: isMobile ? 24 : 30, color: P.text, marginBottom: 18 };
  const body = { fontFamily: MONO, fontSize: 13.5, color: P.text2, lineHeight: 1.85 };
  const card = { background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10, padding: '20px 20px 22px', backdropFilter: 'blur(10px)' };
  const cta = { padding: isMobile ? '13px 26px' : '14px 32px', fontFamily: MONO, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${P.accentBright}, ${P.accent})`, border: 'none', borderRadius: 4, color: P.onAccent, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 24px rgba(134,201,138,0.28)' };
  const ghost = { padding: isMobile ? '13px 22px' : '14px 28px', fontFamily: MONO, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: 'rgba(16,36,26,0.7)', border: `1px solid ${P.borderLight}`, borderRadius: 4, color: P.text, cursor: 'pointer' };
  const setRef = (id) => (el) => { sectionRefs.current[id] = el; };

  return (
    <div ref={scrollRef} data-testid="tier-landing" data-tier={config.id} style={{ position: 'fixed', inset: 0, overflowY: 'auto', overflowX: 'hidden', background: P.bg, color: P.text, fontFamily: MONO }}>
      <style>{`
        .tl-reveal{transition:opacity .9s ease,transform .9s ease}
        .tl-nav-btn:hover{color:${P.accent}!important}
        .tl-card{transition:border-color .25s ease,transform .25s ease}.tl-card:hover{border-color:${P.accentDim};transform:translateY(-2px)}
        .tl-list li::marker{color:${P.accent}}
        .tl-scan{position:absolute;top:0;bottom:0;width:22%;background:linear-gradient(90deg,transparent,rgba(185,236,176,0.16),transparent);animation:tl-scan 9s linear infinite;pointer-events:none}
        @keyframes tl-scan{from{left:-25%}to{left:105%}}
        @media (prefers-reduced-motion: reduce){.tl-scan{animation:none;display:none}.tl-reveal{transition:none}.tl-card{transition:none}}
      `}</style>

      <div style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100, background: 'rgba(8,20,14,0.86)', backdropFilter: 'blur(8px)', borderBottom: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: isMobile ? '12px 18px' : '14px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <img src="/logo.png" alt="" width="20" height="20" style={{ display: 'block' }} />
            <div style={{ fontSize: 14, letterSpacing: 3, color: P.accent, fontWeight: 700, textTransform: 'uppercase' }}>{config.product}</div>
          </div>
          {!isMobile ? (
            <div style={{ display: 'flex', gap: 28, alignItems: 'center' }}>
              {SECTIONS.map(([id, label]) => (
                <button key={id} className="tl-nav-btn" onClick={() => scrollTo(id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: MONO, fontSize: 13, letterSpacing: 1.5, textTransform: 'uppercase', color: P.text3, padding: '4px 2px' }}>{label}</button>
              ))}
              <button onClick={onEnter} style={{ padding: '8px 18px', fontFamily: MONO, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(134,201,138,0.12)', border: `1px solid ${P.accent}`, borderRadius: 4, color: P.accent, cursor: 'pointer' }}>Open app</button>
            </div>
          ) : (
            <button aria-label={menuOpen ? 'Close menu' : 'Open menu'} onClick={() => setMenuOpen((o) => !o)} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 6 }}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke={P.text} strokeWidth="1.8" strokeLinecap="round">{menuOpen ? <path d="M5 5l14 14M19 5L5 19" /> : <path d="M4 6h16M4 12h16M4 18h16" />}</svg>
            </button>
          )}
        </div>
        {isMobile && menuOpen && (
          <div style={{ borderTop: `1px solid ${P.border}`, background: 'rgba(8,20,14,0.97)', padding: '14px 18px 18px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {SECTIONS.map(([id, label]) => (
              <button key={id} onClick={() => scrollTo(id)} style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: '10px 2px', fontFamily: MONO, fontSize: 15, letterSpacing: 1, textTransform: 'uppercase', color: P.text2 }}>{label}</button>
            ))}
            <button onClick={onEnter} style={{ marginTop: 8, padding: '12px 18px', fontFamily: MONO, fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(134,201,138,0.12)', border: `1px solid ${P.accent}`, borderRadius: 4, color: P.accent, cursor: 'pointer' }}>Open app</button>
          </div>
        )}
      </div>

      {/* Home / hero */}
      <div ref={setRef('home')} style={{ position: 'relative', minHeight: '100vh', overflow: 'hidden', display: 'flex', alignItems: 'center' }}>
        <div style={{ position: 'absolute', inset: 0 }}><VegetationTile /><div className="tl-scan" /></div>
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(115deg, rgba(8,20,14,0.94) 0%, rgba(8,20,14,0.78) 38%, rgba(8,20,14,0.3) 70%, rgba(8,20,14,0.1) 100%)' }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: `linear-gradient(0deg, ${P.bg} 0%, rgba(8,20,14,0) 20%)` }} />
        <div style={{ position: 'relative', zIndex: 1, width: '100%', maxWidth: 1200, margin: '0 auto', padding: isMobile ? '90px 20px 60px' : '0 28px' }}>
          <div style={{ maxWidth: 580 }}>
            <div style={{ fontSize: isMobile ? 10.5 : 12, letterSpacing: isMobile ? 2 : 3, color: P.text3, textTransform: 'uppercase', marginBottom: 16 }}>{config.hero.overline}</div>
            <h1 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: isMobile ? 32 : 50, lineHeight: 1.18, color: P.text, margin: '0 0 22px', textShadow: '0 2px 24px rgba(0,0,0,0.6)' }}>
              {config.hero.headline}<span style={{ color: P.accent }}>{config.hero.highlight}</span>
            </h1>
            <p style={{ ...body, fontSize: isMobile ? 12.5 : 13.5, margin: '0 0 34px', maxWidth: 500 }}>{config.hero.body}</p>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              <button onClick={onEnter} style={cta}>{config.hero.cta}</button>
              <button onClick={() => scrollTo('capabilities')} style={ghost}>{config.hero.secondary}</button>
            </div>
          </div>
        </div>
        <div style={{ position: 'absolute', right: 14, bottom: 12, zIndex: 1, fontSize: 10, color: P.text4, letterSpacing: 1 }}>Illustrative tile, not live data</div>
      </div>

      {/* Capabilities */}
      <div ref={setRef('capabilities')} style={{ padding: pad, borderTop: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal rootRef={scrollRef}><div style={eyebrow}>What it does</div><div style={h2}>From satellite pixels to a farm-level read</div></Reveal>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: 16 }}>
            {config.capabilities.map((cap, i) => (
              <Reveal key={cap.title} rootRef={scrollRef} delay={(i % 3) * 90} fill>
                <div className="tl-card" style={{ ...card, height: '100%', boxSizing: 'border-box' }}>
                  <div style={{ fontSize: 14, color: P.text, marginBottom: 10, fontWeight: 600 }}>{cap.title}</div>
                  <div style={{ ...body, fontSize: 12.5, lineHeight: 1.75, color: P.text3 }}>{cap.desc}</div>
                </div>
              </Reveal>
            ))}
          </div>
          <Reveal rootRef={scrollRef}>
            <div style={{ marginTop: 28, fontSize: 11.5, color: P.text3, letterSpacing: 0.5, lineHeight: 1.8 }}>
              <span style={{ color: P.accent, letterSpacing: 2, textTransform: 'uppercase', marginRight: 10 }}>Data</span>{config.sources.join(' \u00b7 ')}
            </div>
          </Reveal>
        </div>
      </div>

      {/* Seasons */}
      <div ref={setRef('seasons')} style={{ padding: pad, borderTop: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: isMobile ? 28 : 56, alignItems: 'center' }}>
          <Reveal rootRef={scrollRef}>
            <div style={eyebrow}>Seasons</div><div style={h2}>{config.seasons.title}</div>
            <div style={body}>{config.seasons.intro}</div>
            <div style={{ ...body, marginTop: 14, color: P.text3, fontSize: 12.5 }}>{config.seasons.note}</div>
          </Reveal>
          <Reveal rootRef={scrollRef} delay={120}>
            <div style={{ ...card, padding: '22px 22px 14px' }}><SeasonBars rows={config.seasons.rows} P={P} isMobile={isMobile} /></div>
          </Reveal>
        </div>
      </div>

      {/* Trust */}
      <div ref={setRef('trust')} style={{ padding: pad, borderTop: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal rootRef={scrollRef}><div style={eyebrow}>Trust</div><div style={h2}>{config.trust.title}</div><div style={{ ...body, marginBottom: 28, maxWidth: 720 }}>{config.trust.intro}</div></Reveal>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: 16 }}>
            <Reveal rootRef={scrollRef} fill>
              <div style={{ ...card, height: '100%', boxSizing: 'border-box' }} data-testid="trust-done">
                <div style={{ fontSize: 11, letterSpacing: 2, textTransform: 'uppercase', color: P.accent, marginBottom: 12 }}>In place</div>
                <ul className="tl-list" style={{ margin: 0, paddingLeft: 18 }}>{config.trust.done.map((t) => <li key={t} style={{ ...body, fontSize: 12.5, marginBottom: 8, color: P.text2 }}>{t}</li>)}</ul>
              </div>
            </Reveal>
            <Reveal rootRef={scrollRef} delay={90} fill>
              <div style={{ ...card, height: '100%', boxSizing: 'border-box', borderColor: 'rgba(216,194,122,0.4)' }} data-testid="trust-not-yet">
                <div style={{ fontSize: 11, letterSpacing: 2, textTransform: 'uppercase', color: P.wheat, marginBottom: 12 }}>Not yet</div>
                <ul className="tl-list" style={{ margin: 0, paddingLeft: 18 }}>{config.trust.notYet.map((t) => <li key={t} style={{ ...body, fontSize: 12.5, marginBottom: 8, color: P.text2 }}>{t}</li>)}</ul>
              </div>
            </Reveal>
          </div>
        </div>
      </div>

      {/* Developer */}
      <div ref={setRef('developer')} style={{ padding: pad, borderTop: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal rootRef={scrollRef}>
            <div style={eyebrow}>Developer</div><div style={{ ...h2, marginBottom: 30 }}>Who&rsquo;s building this</div>
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 20 : 40, alignItems: isMobile ? 'flex-start' : 'center' }}>
              <img src="/images/founder.jpg" alt="Rohit Thanvi" width="96" height="96" style={{ width: 96, height: 96, borderRadius: '50%', objectFit: 'cover', border: `1px solid ${P.accentDim}`, flexShrink: 0 }} />
              <div style={{ maxWidth: 640 }}>
                <div style={{ fontSize: 16, color: P.text, marginBottom: 6 }}>Rohit Thanvi</div>
                <div style={{ fontSize: 11, letterSpacing: 1.5, color: P.accent, textTransform: 'uppercase', marginBottom: 16 }}>Developer, Vayu</div>
                <div style={{ ...body, fontSize: 13, marginBottom: 20 }}>A former ISRO intern who never quite stopped looking skyward &mdash; Vayu is what came of it.</div>
                <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {[['LinkedIn', 'https://linkedin.com/in/rohitthanvi'], ['GitHub', 'https://github.com/RohitThanvi'], ['Portfolio', 'https://rohitthanvi.vercel.app']].map(([label, href]) => (
                    <a key={label} href={href} target="_blank" rel="noreferrer" style={{ fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', padding: '8px 14px', border: `1px solid ${P.borderLight}`, borderRadius: 4, color: P.text2, textDecoration: 'none', background: 'rgba(16,36,26,0.7)' }}>{label}</a>
                  ))}
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </div>

      <div style={{ borderTop: `1px solid ${P.border}`, padding: isMobile ? '22px 20px' : '28px 28px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: isMobile ? 'center' : 'space-between', textAlign: isMobile ? 'center' : 'left' }}>
          <div style={{ fontSize: 11, letterSpacing: 1, color: P.text3 }}>&copy; {new Date().getFullYear()} Rosoft. All rights reserved.</div>
          <div style={{ fontSize: 11, letterSpacing: 1, color: P.text4 }}>{config.product} &mdash; part of the Vayu platform</div>
        </div>
      </div>
    </div>
  );
}
