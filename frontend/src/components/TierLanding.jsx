/**
 * TierLanding.jsx — config-driven, 2D landing page for single-product (locked-tier) sites.
 *
 * Deliberately WebGL-free: the hero shows a real screenshot of the product (config.hero.image)
 * instead of the 3D globe + satellite used on the main landing page. Content and palette come
 * from lib/tierLandingConfigs.js.
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';

const MONO = "'JetBrains Mono','Courier New',monospace";
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SECTIONS = [['home', 'Home'], ['capabilities', 'What it does'], ['seasons', 'Seasons'], ['developer', 'Developer']];

/** Month indices (0 = Jan) covered by a season, handling windows that wrap the year end (e.g. Nov-Mar). */
export function seasonMonths(from, to) {
  const out = [];
  for (let m = from; ; m = (m + 1) % 12) { out.push(m); if (m === to) break; }
  return out;
}

export function SeasonCard({ row, P }) {
  const active = new Set(seasonMonths(row.from, row.to));
  return (
    <div data-testid={`season-${row.name}`} className="tl-card" style={{ background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10, padding: '20px 20px 18px', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <div style={{ fontFamily: 'Georgia, serif', fontSize: 22, color: P.text }}>{row.name}</div>
        <div style={{ fontSize: 10.5, letterSpacing: 2, textTransform: 'uppercase', color: row.color }}>{row.tag}</div>
      </div>
      <div style={{ fontSize: 13, color: P.text2, marginBottom: 16 }}>{row.window}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 3 }}>
        {MONTHS.map((m, i) => (
          <div key={m} style={{ textAlign: 'center' }}>
            <div data-season-month={active.has(i) ? `${row.name}-${i}` : undefined} style={{ height: 26, borderRadius: 3, background: active.has(i) ? row.color : 'rgba(255,255,255,0.06)', opacity: active.has(i) ? 0.9 : 1 }} />
            <div style={{ fontSize: 8.5, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', color: active.has(i) ? P.text2 : P.text4, letterSpacing: 0 }}>{m}</div>
          </div>
        ))}
      </div>
    </div>
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
        @media (prefers-reduced-motion: reduce){.tl-reveal{transition:none}.tl-card{transition:none}}
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
      <div ref={setRef('home')} style={{ position: 'relative', minHeight: '100vh', overflow: 'hidden', display: 'flex', alignItems: 'center', background: `radial-gradient(ellipse at 80% 42%, rgba(46,110,70,0.38), transparent 62%), radial-gradient(ellipse at 8% 95%, rgba(216,194,122,0.07), transparent 50%), ${P.bg}` }}>
        <div style={{ width: '100%', maxWidth: 1200, margin: '0 auto', padding: isMobile ? '96px 20px 56px' : '96px 28px 48px', display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '0.85fr 1.15fr', gap: isMobile ? 36 : 52, alignItems: 'center', boxSizing: 'border-box' }}>
          <div>
            <div style={{ fontSize: isMobile ? 10.5 : 12, letterSpacing: isMobile ? 2 : 3, color: P.text3, textTransform: 'uppercase', marginBottom: 16 }}>{config.hero.overline}</div>
            <h1 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: isMobile ? 32 : 44, lineHeight: 1.18, color: P.text, margin: '0 0 22px' }}>
              {config.hero.headline}<span style={{ color: P.accent }}>{config.hero.highlight}</span>
            </h1>
            <p style={{ ...body, fontSize: isMobile ? 12.5 : 13.5, margin: '0 0 34px', maxWidth: 480 }}>{config.hero.body}</p>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              <button onClick={onEnter} style={cta}>{config.hero.cta}</button>
              <button onClick={() => scrollTo('capabilities')} style={ghost}>{config.hero.secondary}</button>
            </div>
          </div>
          <figure style={{ margin: 0 }}>
            <div style={{ borderRadius: 12, overflow: 'hidden', border: `1px solid ${P.borderLight}`, boxShadow: '0 24px 70px rgba(0,0,0,0.55)' }}>
              <img src={config.hero.image.src} alt={config.hero.image.alt} width="1256" height="743" style={{ display: 'block', width: '100%', height: 'auto' }} />
            </div>
            <figcaption style={{ marginTop: 12, fontSize: 11, color: P.text3, letterSpacing: 0.5, lineHeight: 1.7 }}>{config.hero.image.caption}</figcaption>
          </figure>
        </div>
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
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal rootRef={scrollRef}>
            <div style={eyebrow}>Seasons</div><div style={h2}>{config.seasons.title}</div>
            <div style={{ ...body, maxWidth: 760 }}>{config.seasons.intro}</div>
          </Reveal>
          <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: 16, marginTop: 28 }}>
            {config.seasons.rows.map((row, i) => (
              <Reveal key={row.name} rootRef={scrollRef} delay={i * 90} fill><SeasonCard row={row} P={P} /></Reveal>
            ))}
          </div>
          <Reveal rootRef={scrollRef}><div style={{ ...body, marginTop: 22, color: P.text3, fontSize: 12.5, maxWidth: 760 }}>{config.seasons.note}</div></Reveal>
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
