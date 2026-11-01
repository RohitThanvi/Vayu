/**
 * TierLanding.jsx — config-driven, 2D landing page for single-product (locked-tier) sites.
 *
 * Deliberately WebGL-free: the hero is a blurred real-imagery backdrop (config.hero.background)
 * plus a real screenshot of the product (config.hero.image), instead of the 3D globe + satellite
 * on the main landing page. Content and palette come from lib/tierLandingConfigs.js.
 *
 * Effects (ported from LandingPage.jsx, all vanilla): Ken Burns drift + scroll parallax on the
 * hero backdrop, hero text fading/drifting with scroll and counter-drifting with the mouse, a
 * page-wide cursor glow, a fixed ambient backdrop, per-section enter/exit choreography
 * (SectionFX), one-shot item reveals, tilt cards, nav active-section highlight and hover states.
 * Every IntersectionObserver uses the page's own scroll container as `root` (the app body is
 * position:fixed, so the viewport never scrolls).
 */
import { useEffect, useRef, useState, useCallback } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';

const MONO = "'JetBrains Mono','Courier New',monospace";
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SECTIONS = [['home', 'Home'], ['capabilities', 'What it does'], ['seasons', 'Seasons'], ['developer', 'Developer']];
const HAS_IO = () => typeof IntersectionObserver !== 'undefined';

/** Month indices (0 = Jan) covered by a season, handling windows that wrap the year end (e.g. Nov-Mar). */
export function seasonMonths(from, to) {
  const out = [];
  for (let m = from; ; m = (m + 1) % 12) { out.push(m); if (m === to) break; }
  return out;
}

// One-shot, per-item entrance (stays visible once triggered).
function Reveal({ rootRef, children, delay = 0, fill = false, variant = 'rise' }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(!HAS_IO());
  useEffect(() => {
    const el = ref.current;
    if (visible || !el || !HAS_IO()) return undefined;
    const io = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); io.disconnect(); } }, { root: rootRef.current, threshold: 0.15 });
    io.observe(el);
    return () => io.disconnect();
  }, [visible, rootRef]);
  const hidden = variant === 'scale' ? 'translateY(34px) scale(0.94)' : 'translateY(28px)';
  return (
    <div ref={ref} data-reveal={visible ? 'shown' : 'hidden'} style={{
      opacity: visible ? 1 : 0, transform: visible ? 'translateY(0) scale(1)' : hidden,
      filter: variant === 'scale' ? (visible ? 'blur(0px)' : 'blur(6px)') : 'none',
      transition: `opacity 0.7s cubic-bezier(0.16,1,0.3,1) ${delay}s, transform 0.7s cubic-bezier(0.16,1,0.3,1) ${delay}s, filter 0.7s ease ${delay}s`,
      willChange: 'opacity, transform', ...(fill ? { height: '100%' } : null),
    }}>{children}</div>
  );
}

// Section-level enter/exit choreography: keeps observing, so a section also settles back toward
// hidden as it leaves the viewport. Slow, eased, no overshoot, <= ~30px of movement.
export const SECTION_VARIANTS = {
  curtain: { hidden: { opacity: 0, transform: 'translateY(30px) scale(0.99)' }, shown: { opacity: 1, transform: 'translateY(0) scale(1)' }, duration: '1.1s', easing: 'cubic-bezier(0.22, 1, 0.36, 1)' },
  unfurl: { hidden: { opacity: 0, transform: 'scale(0.965)' }, shown: { opacity: 1, transform: 'scale(1)' }, duration: '1.15s', easing: 'cubic-bezier(0.19, 1, 0.22, 1)' },
  drift: { hidden: { opacity: 0, transform: 'translateX(-24px)' }, shown: { opacity: 1, transform: 'translateX(0)' }, duration: '1s', easing: 'cubic-bezier(0.25, 1, 0.5, 1)' },
  hairline: { hidden: { opacity: 0, transform: 'translateY(10px)' }, shown: { opacity: 1, transform: 'translateY(0)' }, duration: '0.9s', easing: 'ease-out' },
};

function SectionFX({ variant, rootRef, children, style }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(!HAS_IO());
  useEffect(() => {
    const el = ref.current;
    if (!el || !HAS_IO()) return undefined;
    const io = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), { root: rootRef.current, threshold: 0.12 });
    io.observe(el);
    return () => io.disconnect();
  }, [rootRef]);
  const v = SECTION_VARIANTS[variant];
  const st = visible ? v.shown : v.hidden;
  return (
    <div ref={ref} data-fx={variant} data-fx-state={visible ? 'shown' : 'hidden'} style={{
      ...style, opacity: st.opacity, transform: st.transform, filter: st.filter || 'none',
      transition: `opacity ${v.duration} ${v.easing}, transform ${v.duration} ${v.easing}`, willChange: 'opacity, transform',
    }}>{children}</div>
  );
}

// Subtle mouse-tracked 3D tilt for the capability cards (max ~5deg, eased flat on leave).
function TiltCard({ children, style, className, maxDeg = 5 }) {
  const ref = useRef(null);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const onMouseMove = useCallback((e) => {
    const el = ref.current; if (!el) return;
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    setTilt({ x: ((e.clientY - r.top) / r.height - 0.5) * -maxDeg, y: ((e.clientX - r.left) / r.width - 0.5) * maxDeg });
  }, [maxDeg]);
  const onMouseLeave = useCallback(() => setTilt({ x: 0, y: 0 }), []);
  return (
    <div style={{ perspective: 900, height: '100%' }}>
      <div ref={ref} data-tilt={`${tilt.x.toFixed(2)},${tilt.y.toFixed(2)}`} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} className={className}
        style={{ ...style, transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg) translateZ(0)` }}>{children}</div>
    </div>
  );
}

export function SeasonCard({ row, P }) {
  const active = new Set(seasonMonths(row.from, row.to));
  return (
    <div data-testid={`season-${row.name}`} className="tl-card" style={{ background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10, padding: '20px 20px 18px', height: '100%', boxSizing: 'border-box', backdropFilter: 'blur(10px)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <div style={{ fontFamily: 'Georgia, serif', fontSize: 22, color: P.text }}>{row.name}</div>
        <div style={{ fontSize: 10.5, letterSpacing: 2, textTransform: 'uppercase', color: row.color }}>{row.tag}</div>
      </div>
      <div style={{ fontSize: 13, color: P.text2, marginBottom: 16 }}>{row.window}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 3 }}>
        {MONTHS.map((m, i) => (
          <div key={m} style={{ textAlign: 'center' }}>
            <div data-season-month={active.has(i) ? `${row.name}-${i}` : undefined} style={{ height: 26, borderRadius: 3, background: active.has(i) ? row.color : 'rgba(255,255,255,0.06)', opacity: active.has(i) ? 0.9 : 1 }} />
            <div style={{ fontSize: 8.5, marginTop: 5, whiteSpace: 'nowrap', overflow: 'hidden', color: active.has(i) ? P.text2 : P.text4 }}>{m}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function TierLanding({ config, onEnter }) {
  const P = config.palette;
  const isMobile = useIsMobile(760);
  const scrollRef = useRef(null);
  const sectionRefs = useRef({});
  const heroTextRef = useRef(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState('home');
  const [parallaxY, setParallaxY] = useState(0);
  const scrollTo = useCallback((id) => { sectionRefs.current[id]?.scrollIntoView?.({ behavior: 'smooth', block: 'start' }); setMenuOpen(false); }, []);

  // Nav active-section highlight.
  useEffect(() => {
    if (!HAS_IO()) return undefined;
    const io = new IntersectionObserver((entries) => { entries.forEach((e) => { if (e.isIntersecting) setActiveSection(e.target.dataset.section); }); }, { root: scrollRef.current, threshold: 0.4 });
    Object.values(sectionRefs.current).forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, []);

  // Scroll parallax (rAF-throttled).
  useEffect(() => {
    const c = scrollRef.current; if (!c) return undefined;
    let ticking = false;
    const onScroll = () => { if (ticking) return; ticking = true; requestAnimationFrame(() => { setParallaxY(c.scrollTop * 0.25); ticking = false; }); };
    c.addEventListener('scroll', onScroll, { passive: true });
    return () => c.removeEventListener('scroll', onScroll);
  }, []);

  // Mouse ambiance: page-wide cursor glow + hero text counter-drift, by direct DOM mutation (no re-render).
  useEffect(() => {
    const c = scrollRef.current; if (!c || isMobile) return undefined;
    let raf = null;
    const onMove = (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        const nx = e.clientX / window.innerWidth, ny = e.clientY / window.innerHeight;
        c.style.setProperty('--mx', `${(nx * 100).toFixed(1)}%`); c.style.setProperty('--my', `${(ny * 100).toFixed(1)}%`);
        if (heroTextRef.current) heroTextRef.current.style.transform = `translate(${((nx - 0.5) * -14).toFixed(1)}px, ${((ny - 0.5) * -8).toFixed(1)}px)`;
        raf = null;
      });
    };
    c.addEventListener('mousemove', onMove, { passive: true });
    return () => { c.removeEventListener('mousemove', onMove); if (raf) cancelAnimationFrame(raf); };
  }, [isMobile]);

  const pad = isMobile ? '60px 20px' : '96px 28px';
  const eyebrow = { fontFamily: MONO, fontSize: 12, letterSpacing: 3, color: P.accent, textTransform: 'uppercase', marginBottom: 8 };
  const h2 = { fontFamily: 'Georgia, serif', fontSize: isMobile ? 24 : 30, color: P.text, marginBottom: 18 };
  const body = { fontFamily: MONO, fontSize: 13.5, color: P.text2, lineHeight: 1.85 };
  const card = { background: P.surface, border: `1px solid ${P.border}`, borderRadius: 10, padding: '20px 20px 22px', backdropFilter: 'blur(10px)' };
  const cta = { padding: isMobile ? '13px 26px' : '14px 32px', fontFamily: MONO, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${P.accentBright}, ${P.accent})`, border: 'none', borderRadius: 4, color: P.onAccent, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 24px rgba(134,201,138,0.28)' };
  const ghost = { padding: isMobile ? '13px 22px' : '14px 28px', fontFamily: MONO, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: 'rgba(16,36,26,0.7)', border: `1px solid ${P.borderLight}`, borderRadius: 4, color: P.text, cursor: 'pointer' };
  const setRef = (id) => (el) => { sectionRefs.current[id] = el; };
  const openApp = { padding: '8px 18px', fontFamily: MONO, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(134,201,138,0.12)', border: `1px solid ${P.accent}`, borderRadius: 4, color: P.accent, cursor: 'pointer' };
  const heroFade = Math.max(0, 1 - parallaxY / 260);

  return (
    <div ref={scrollRef} data-testid="tier-landing" data-tier={config.id} style={{ position: 'fixed', inset: 0, overflowY: 'auto', overflowX: 'hidden', background: P.bg, color: P.text, fontFamily: MONO, scrollBehavior: 'smooth' }}>
      <style>{`
        @keyframes tl-kenburns{0%{transform:scale(1.06) translate(0%,0%)}50%{transform:scale(1.16) translate(-1.2%,-1%)}100%{transform:scale(1.06) translate(0%,0%)}}
        @keyframes tl-pfp-spin{from{transform:scale(1) rotate(0deg)}to{transform:scale(1) rotate(360deg)}}
        .tl-hero-bg{animation:tl-kenburns 34s ease-in-out infinite}
        .tl-nav-btn{transition:color .2s ease;position:relative}
        .tl-nav-btn::after{content:'';position:absolute;left:0;right:0;bottom:-6px;height:2px;background:${P.accent};transform:scaleX(0);transform-origin:center;transition:transform .25s ease}
        .tl-nav-btn:hover{color:${P.accentBright}!important}.tl-nav-btn:hover::after{transform:scaleX(1)}
        .tl-card{transition:border-color .25s ease,transform .25s ease,box-shadow .25s ease}
        .tl-card:hover{border-color:${P.accentDim};box-shadow:0 22px 50px rgba(0,0,0,0.5),0 4px 16px rgba(134,201,138,0.12)}
        .tl-feature{transition:border-color .25s ease,box-shadow .25s ease;transform-style:preserve-3d}
        .tl-feature:hover{border-color:${P.accentDim}!important;box-shadow:0 22px 50px rgba(0,0,0,0.5),0 4px 16px rgba(134,201,138,0.12)}
        .tl-cta{transition:transform .2s ease,box-shadow .2s ease}.tl-cta:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(134,201,138,0.45)!important}
        .tl-ghost{transition:border-color .2s ease,color .2s ease}.tl-ghost:hover{border-color:${P.accent}!important;color:${P.accentBright}!important}
        .tl-links a{transition:border-color .2s ease,color .2s ease}.tl-links a:hover{border-color:${P.accent}!important;color:${P.accentBright}!important}
        .tl-pfp-img{transition:transform .4s cubic-bezier(.16,1,.3,1),box-shadow .4s ease,filter .4s ease}
        .tl-pfp-wrap{position:relative;flex-shrink:0;width:96px;height:96px}
        .tl-pfp-wrap::before{content:'';position:absolute;inset:-5px;border-radius:50%;background:conic-gradient(from 0deg,${P.accent},${P.accentBright},${P.wheat},${P.accent});opacity:0;transform:scale(.82);filter:blur(5px);transition:opacity .4s ease,transform .6s ease;z-index:0;animation:tl-pfp-spin 6s linear infinite paused}
        .tl-pfp-wrap:hover::before{opacity:.9;transform:scale(1);animation-play-state:running}
        .tl-pfp-wrap:hover .tl-pfp-img{transform:scale(1.07);filter:saturate(1.2) brightness(1.06);box-shadow:0 0 26px rgba(134,201,138,0.5)}
        .tl-ambient{position:fixed;inset:0;z-index:-1;pointer-events:none;overflow:hidden;
          background:radial-gradient(ellipse 900px 700px at 84% 6%,rgba(134,201,138,0.09),transparent 60%),radial-gradient(ellipse 800px 900px at 6% 72%,rgba(216,194,122,0.06),transparent 65%),
            radial-gradient(1.3px 1.3px at 12% 18%,rgba(255,255,255,0.45),transparent 60%),radial-gradient(1px 1px at 68% 42%,rgba(255,255,255,0.35),transparent 60%),radial-gradient(1.5px 1.5px at 40% 78%,rgba(255,255,255,0.38),transparent 60%),radial-gradient(1px 1px at 85% 88%,rgba(255,255,255,0.3),transparent 60%);
          background-repeat:no-repeat,no-repeat,repeat,repeat,repeat,repeat;background-size:100% 100%,100% 100%,260px 260px,320px 320px,240px 240px,300px 300px}
        .tl-cursor-glow{position:fixed;inset:0;pointer-events:none;z-index:2;background:radial-gradient(560px circle at var(--mx,50%) var(--my,40%),rgba(134,201,138,0.07),transparent 68%);mix-blend-mode:screen}
        @media (prefers-reduced-motion: reduce){.tl-hero-bg{animation:none}.tl-card,.tl-feature,.tl-cta,.tl-ghost{transition:none}.tl-pfp-wrap::before{animation:none}}
      `}</style>

      <div className="tl-ambient" data-testid="ambient">
        {/* Concentric rings: the one deliberate motif in the backdrop, echoing a centre-pivot field seen from orbit. Hairline, ~5% opacity. */}
        <svg width="900" height="900" viewBox="0 0 900 900" style={{ position: 'absolute', top: -220, right: -260, opacity: 0.05 }}>
          {[420, 330, 240, 150].map((r) => <circle key={r} cx="450" cy="450" r={r} fill="none" stroke={P.accent} strokeWidth="1" />)}
        </svg>
      </div>
      <div className="tl-cursor-glow" data-testid="cursor-glow" />

      <div style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100, background: 'rgba(8,20,14,0.86)', backdropFilter: 'blur(8px)', borderBottom: `1px solid ${P.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: isMobile ? '12px 18px' : '14px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <img src="/logo.png" alt="" width="20" height="20" style={{ display: 'block', filter: 'drop-shadow(0 0 4px rgba(134,201,138,0.4))' }} />
            <div style={{ fontSize: 14, letterSpacing: 3, color: P.accent, fontWeight: 700, textTransform: 'uppercase' }}>{config.product}</div>
          </div>
          {!isMobile ? (
            <div style={{ display: 'flex', gap: 32, alignItems: 'center' }}>
              {SECTIONS.map(([id, label]) => (
                <button key={id} className="tl-nav-btn" data-active={activeSection === id ? 'true' : 'false'} onClick={() => scrollTo(id)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: MONO, fontSize: 13, letterSpacing: 1.5, textTransform: 'uppercase', color: activeSection === id ? P.accent : P.text3, padding: '4px 2px' }}>{label}</button>
              ))}
              <button onClick={onEnter} style={openApp}>Open app</button>
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
              <button key={id} onClick={() => scrollTo(id)} style={{ background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', padding: '10px 2px', fontFamily: MONO, fontSize: 15, letterSpacing: 1, textTransform: 'uppercase', color: activeSection === id ? P.accent : P.text2 }}>{label}</button>
            ))}
            <button onClick={onEnter} style={{ ...openApp, marginTop: 8, padding: '12px 18px', fontSize: 12 }}>Open app</button>
          </div>
        )}
      </div>

      {/* Home / hero: blurred real-imagery backdrop (Ken Burns + parallax) behind the text and the product screenshot */}
      <div ref={setRef('home')} data-section="home" style={{ position: 'relative', minHeight: '100vh', overflow: 'hidden', display: 'flex', alignItems: 'center' }}>
        <div data-testid="hero-parallax" style={{ position: 'absolute', inset: 0, transform: `translateY(${parallaxY * 0.3}px)` }}>
          <div className="tl-hero-bg" data-testid="hero-bg" style={{ position: 'absolute', inset: '-4%', backgroundImage: `url(${config.hero.background})`, backgroundSize: 'cover', backgroundPosition: 'center', backgroundColor: P.bg }} />
        </div>
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(115deg, rgba(8,20,14,0.82) 0%, rgba(8,20,14,0.55) 36%, rgba(8,20,14,0.18) 66%, rgba(8,20,14,0.06) 100%)' }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: `linear-gradient(0deg, ${P.bg} 0%, rgba(8,20,14,0) 20%)` }} />
        <div data-testid="hero-content" style={{ position: 'relative', zIndex: 1, width: '100%', maxWidth: 1200, margin: '0 auto', padding: isMobile ? '96px 20px 56px' : '96px 28px 48px', display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '0.85fr 1.15fr', gap: isMobile ? 36 : 52, alignItems: 'center', boxSizing: 'border-box', opacity: heroFade, transform: `translateY(${parallaxY * 0.15}px)` }}>
          <div ref={heroTextRef}>
            <div style={{ fontSize: isMobile ? 10.5 : 12, letterSpacing: isMobile ? 2 : 3, color: P.text3, textTransform: 'uppercase', marginBottom: 16 }}>{config.hero.overline}</div>
            <h1 style={{ fontFamily: 'Georgia, serif', fontWeight: 400, fontSize: isMobile ? 32 : 44, lineHeight: 1.18, color: P.text, margin: '0 0 22px', textShadow: '0 2px 24px rgba(0,0,0,0.5)' }}>
              {config.hero.headline}<span style={{ color: P.accent }}>{config.hero.highlight}</span>
            </h1>
            <p style={{ ...body, fontSize: isMobile ? 12.5 : 13.5, margin: '0 0 34px', maxWidth: 480 }}>{config.hero.body}</p>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              <button className="tl-cta" onClick={onEnter} style={cta}>{config.hero.cta}</button>
              <button className="tl-ghost" onClick={() => scrollTo('capabilities')} style={ghost}>{config.hero.secondary}</button>
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
      <div ref={setRef('capabilities')} data-section="capabilities" style={{ borderTop: `1px solid ${P.border}` }}>
        <SectionFX variant="curtain" rootRef={scrollRef} style={{ padding: pad }}>
          <div style={{ maxWidth: 1200, margin: '0 auto' }}>
            <Reveal rootRef={scrollRef}><div style={eyebrow}>What it does</div><div style={h2}>From satellite pixels to a farm-level read</div></Reveal>
            <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: 16 }}>
              {config.capabilities.map((cap, i) => (
                <Reveal key={cap.title} rootRef={scrollRef} delay={(i % 3) * 0.09} variant="scale" fill>
                  <TiltCard className="tl-feature" style={{ ...card, height: '100%', boxSizing: 'border-box' }}>
                    <div style={{ fontSize: 14, color: P.text, marginBottom: 10, fontWeight: 600 }}>{cap.title}</div>
                    <div style={{ ...body, fontSize: 12.5, lineHeight: 1.75, color: P.text3 }}>{cap.desc}</div>
                  </TiltCard>
                </Reveal>
              ))}
            </div>
            <Reveal rootRef={scrollRef}>
              <div style={{ marginTop: 28, fontSize: 11.5, color: P.text3, letterSpacing: 0.5, lineHeight: 1.8 }}>
                <span style={{ color: P.accent, letterSpacing: 2, textTransform: 'uppercase', marginRight: 10 }}>Data</span>{config.sources.join(' \u00b7 ')}
              </div>
            </Reveal>
          </div>
        </SectionFX>
      </div>

      {/* Seasons */}
      <div ref={setRef('seasons')} data-section="seasons" style={{ borderTop: `1px solid ${P.border}` }}>
        <SectionFX variant="unfurl" rootRef={scrollRef} style={{ padding: pad }}>
          <div style={{ maxWidth: 1200, margin: '0 auto' }}>
            <Reveal rootRef={scrollRef}>
              <div style={eyebrow}>Seasons</div><div style={h2}>{config.seasons.title}</div>
              <div style={{ ...body, maxWidth: 760 }}>{config.seasons.intro}</div>
            </Reveal>
            <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(3, 1fr)', gap: 16, marginTop: 28 }}>
              {config.seasons.rows.map((row, i) => (
                <Reveal key={row.name} rootRef={scrollRef} delay={i * 0.09} variant="scale" fill><SeasonCard row={row} P={P} /></Reveal>
              ))}
            </div>
            <Reveal rootRef={scrollRef}><div style={{ ...body, marginTop: 22, color: P.text3, fontSize: 12.5, maxWidth: 760 }}>{config.seasons.note}</div></Reveal>
          </div>
        </SectionFX>
      </div>

      {/* Developer */}
      <div ref={setRef('developer')} data-section="developer" style={{ borderTop: `1px solid ${P.border}` }}>
        <SectionFX variant="drift" rootRef={scrollRef} style={{ padding: pad }}>
          <div style={{ maxWidth: 1200, margin: '0 auto' }}>
            <div style={eyebrow}>Developer</div><div style={{ ...h2, marginBottom: 30 }}>Who&rsquo;s building this</div>
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 20 : 40, alignItems: isMobile ? 'flex-start' : 'center' }}>
              <div className="tl-pfp-wrap">
                <img className="tl-pfp-img" src="/images/founder.jpg" alt="Rohit Thanvi" width="96" height="96" style={{ position: 'relative', zIndex: 1, width: 96, height: 96, borderRadius: '50%', objectFit: 'cover', border: `1px solid ${P.accentDim}`, display: 'block' }} />
              </div>
              <div style={{ maxWidth: 640 }}>
                <div style={{ fontSize: 16, color: P.text, marginBottom: 6 }}>Rohit Thanvi</div>
                <div style={{ fontSize: 11, letterSpacing: 1.5, color: P.accent, textTransform: 'uppercase', marginBottom: 16 }}>Developer, Vayu</div>
                <div style={{ ...body, fontSize: 13, marginBottom: 20 }}>A former ISRO intern who never quite stopped looking skyward &mdash; Vayu is what came of it.</div>
                <div className="tl-links" style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  {[['LinkedIn', 'https://linkedin.com/in/rohitthanvi'], ['GitHub', 'https://github.com/RohitThanvi'], ['Portfolio', 'https://rohitthanvi.vercel.app']].map(([label, href]) => (
                    <a key={label} href={href} target="_blank" rel="noreferrer" style={{ fontSize: 11, letterSpacing: 1, textTransform: 'uppercase', padding: '8px 14px', border: `1px solid ${P.borderLight}`, borderRadius: 4, color: P.text2, textDecoration: 'none', background: 'rgba(16,36,26,0.7)' }}>{label}</a>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </SectionFX>
      </div>

      <div style={{ borderTop: `1px solid ${P.border}` }}>
        <SectionFX variant="hairline" rootRef={scrollRef} style={{ padding: isMobile ? '22px 20px' : '28px 28px' }}>
          <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: isMobile ? 'center' : 'space-between', textAlign: isMobile ? 'center' : 'left' }}>
            <div style={{ fontSize: 11, letterSpacing: 1, color: P.text3 }}>&copy; {new Date().getFullYear()} Rosoft. All rights reserved.</div>
            <div style={{ fontSize: 11, letterSpacing: 1, color: P.text4 }}>{config.product} &mdash; part of the Vayu platform</div>
          </div>
        </SectionFX>
      </div>
    </div>
  );
}
