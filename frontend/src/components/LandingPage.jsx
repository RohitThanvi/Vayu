/**
 * LandingPage.jsx
 * Single-page landing/login experience shown by AppGate.jsx before the
 * main app. Sections (Home/About/Contact/Account) live on one page and
 * are navigated by smooth-scrolling to anchors — no router library.
 * Nav-link highlighting on scroll uses IntersectionObserver (vanilla,
 * no scroll-animation library). The hero background is a real
 * satellite/Earth photo with a classic vanilla parallax (drifts+scales
 * slower than scroll, headline fades as you scroll past it) — no
 * Three.js here at all, so this page loads instantly with zero WebGL
 * weight.
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const S = {
  bg: '#05070c', surface: 'rgba(13,17,23,0.82)', surface2: '#0d1117', border: '#2a3040',
  text: '#ffffff', text2: 'rgba(255,255,255,0.75)', text3: 'rgba(255,255,255,0.5)',
  gold: '#c9a86a', goldBright: '#f5d98a', accent: '#7eb8d4',
  mono: "'JetBrains Mono','Courier New',monospace",
};

const SECTIONS = [['home', 'Home'], ['about', 'About'], ['contact', 'Contact']];

const FEATURES = [
  { title: 'Satellite Analysis', desc: 'Vegetation, drought, flood, fire, deforestation & more — 9 fixed metrics plus open-ended AI research on any AOI.', img: '/screenshots/analyze-maritime.jpg' },
  { title: 'Weather & Air Quality', desc: 'Live temperature, wind, pressure overlays, and real-time CPCB air quality across India.', img: '/screenshots/weather-layers.jpg' },
  { title: 'Agricultural Intelligence', desc: 'A composite 0-100 risk score fusing vegetation stress, drought, and soil moisture into one plain-language read.', img: '/screenshots/agri-dashboard.jpg' },
  { title: 'Orbital Tracking', desc: 'Live aircraft and satellite positions rendered on an interactive 3D globe.', img: '/screenshots/orbital-view.jpg' },
];

function Starfield() {
  const stars = Array.from({ length: 110 }, (_, i) => {
    const seed = i * 137.5;
    return { x: (seed * 3.7) % 100, y: (seed * 5.3) % 100, size: 1 + (i % 3), opacity: 0.25 + ((i * 0.61) % 0.55) };
  });
  return (
    <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      {stars.map((s, i) => <circle key={i} cx={`${s.x}%`} cy={`${s.y}%`} r={s.size / 2} fill="#ffffff" opacity={s.opacity} />)}
    </svg>
  );
}

function PasswordField({ label, value, onChange, onEnter }) {
  const [visible, setVisible] = useState(false);
  return (
    <div>
      <div style={{ fontFamily: S.mono, fontSize: 10, letterSpacing: 1, color: S.text3, marginBottom: 5, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ position: 'relative' }}>
        <input type={visible ? 'text' : 'password'} value={value} onChange={e => onChange(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && onEnter && onEnter()}
          style={{ width: '100%', boxSizing: 'border-box', background: 'rgba(0,0,0,0.3)', border: `1px solid ${S.border}`, borderRadius: 3, color: S.text, fontFamily: S.mono, fontSize: 13, padding: '10px 40px 10px 11px' }} />
        <button type="button" onClick={() => setVisible(v => !v)} tabIndex={-1}
          style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', color: S.text3, fontFamily: S.mono, fontSize: 10, letterSpacing: 0.5, cursor: 'pointer', padding: '4px 6px', textTransform: 'uppercase' }}>
          {visible ? 'HIDE' : 'SHOW'}
        </button>
      </div>
    </div>
  );
}

function AuthCard({ apiUrl, onAuthenticated }) {
  const [mode, setMode] = useState('login');   // 'login' | 'signup' | 'forgot'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);

  const submit = async () => {
    setError(null); setInfo(null);
    if (mode === 'forgot') {
      if (!email.trim()) return;
      setStatus('loading');
      try {
        const resp = await fetch(`${apiUrl}/api/v1/auth/forgot-password`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: email.trim() }),
        });
        const data = await resp.json();
        setInfo(data.message || 'If that email is registered, a reset link has been sent.');
      } catch {
        setError('Something went wrong — please try again.');
      } finally {
        setStatus('idle');
      }
      return;
    }
    if (!email.trim() || !password) return;
    setStatus('loading');
    try {
      const resp = await fetch(`${apiUrl}/api/v1/auth/${mode}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Something went wrong.');
      onAuthenticated(data.token, data.email);
    } catch (e) {
      setError(e.message);
      setStatus('idle');
    }
  };

  return (
    <div style={{
      flex: '0 0 340px', background: S.surface, border: `1px solid ${S.border}`,
      borderRadius: 8, padding: '30px 28px', backdropFilter: 'blur(6px)',
    }}>
      {mode !== 'forgot' && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 22 }}>
          {['login', 'signup'].map(m => (
            <button key={m} onClick={() => { setMode(m); setError(null); setInfo(null); }}
              style={{
                flex: 1, padding: '8px', fontFamily: S.mono, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase',
                cursor: 'pointer', background: mode === m ? 'rgba(201,168,106,0.12)' : 'transparent',
                border: `1px solid ${mode === m ? S.gold : S.border}`, color: mode === m ? S.gold : S.text3,
              }}>
              {m === 'login' ? 'Log In' : 'Sign Up'}
            </button>
          ))}
        </div>
      )}
      {mode === 'forgot' && (
        <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 1, color: S.gold, marginBottom: 18, textTransform: 'uppercase' }}>Reset Password</div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <div style={{ fontFamily: S.mono, fontSize: 10, letterSpacing: 1, color: S.text3, marginBottom: 5, textTransform: 'uppercase' }}>Email</div>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            style={{ width: '100%', boxSizing: 'border-box', background: 'rgba(0,0,0,0.3)', border: `1px solid ${S.border}`, borderRadius: 3, color: S.text, fontFamily: S.mono, fontSize: 13, padding: '10px 11px' }} />
        </div>

        {mode !== 'forgot' && (
          <PasswordField label="Password" value={password} onChange={setPassword} onEnter={submit} />
        )}

        {mode === 'login' && (
          <button type="button" onClick={() => { setMode('forgot'); setError(null); setInfo(null); }}
            style={{ alignSelf: 'flex-end', background: 'none', border: 'none', color: S.text3, fontFamily: S.mono, fontSize: 10.5, letterSpacing: 0.5, cursor: 'pointer', padding: 0, textDecoration: 'underline' }}>
            Forgot password?
          </button>
        )}

        {error && <div style={{ fontFamily: S.mono, fontSize: 11.5, color: '#ff7a45' }}>{error}</div>}
        {info && <div style={{ fontFamily: S.mono, fontSize: 11.5, color: '#4a7c59' }}>{info}</div>}

        <button onClick={submit} disabled={status === 'loading'}
          style={{
            marginTop: 6, padding: '11px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase',
            background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4,
            color: '#05070c', fontWeight: 700, cursor: status === 'loading' ? 'wait' : 'pointer',
            opacity: status === 'loading' ? 0.7 : 1,
          }}>
          {status === 'loading' ? '...' : mode === 'login' ? 'ENTER TERMINAL' : mode === 'signup' ? 'CREATE ACCOUNT' : 'SEND RESET LINK'}
        </button>

        {mode === 'forgot' && (
          <button type="button" onClick={() => { setMode('login'); setError(null); setInfo(null); }}
            style={{ background: 'none', border: 'none', color: S.text3, fontFamily: S.mono, fontSize: 10.5, letterSpacing: 0.5, cursor: 'pointer', padding: 0 }}>
            &larr; Back to login
          </button>
        )}
      </div>
    </div>
  );
}

function ResetPasswordCard({ apiUrl, token }) {
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState('idle');
  const [message, setMessage] = useState(null);
  const [ok, setOk] = useState(false);

  const submit = async () => {
    if (password.length < 8) { setMessage('Password must be at least 8 characters.'); return; }
    setStatus('loading'); setMessage(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/auth/reset-password`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Reset failed.');
      setOk(true);
      setMessage('Password updated — you can log in now.');
    } catch (e) {
      setMessage(e.message);
    } finally {
      setStatus('idle');
    }
  };

  return (
    <div style={{ flex: '0 0 340px', background: S.surface, border: `1px solid ${S.border}`, borderRadius: 8, padding: '30px 28px', backdropFilter: 'blur(6px)' }}>
      <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 1, color: S.gold, marginBottom: 18, textTransform: 'uppercase' }}>Set New Password</div>
      {!ok ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <PasswordField label="New Password" value={password} onChange={setPassword} onEnter={submit} />
          {message && <div style={{ fontFamily: S.mono, fontSize: 11.5, color: '#ff7a45' }}>{message}</div>}
          <button onClick={submit} disabled={status === 'loading'}
            style={{ marginTop: 6, padding: '11px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4, color: '#05070c', fontWeight: 700, cursor: status === 'loading' ? 'wait' : 'pointer' }}>
            {status === 'loading' ? '...' : 'UPDATE PASSWORD'}
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontFamily: S.mono, fontSize: 12.5, color: '#4a7c59' }}>{message}</div>
          <button onClick={() => { window.history.replaceState({}, '', '/'); window.location.reload(); }}
            style={{ padding: '11px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4, color: '#05070c', fontWeight: 700, cursor: 'pointer' }}>
            GO TO LOGIN
          </button>
        </div>
      )}
    </div>
  );
}

function ContactForm({ apiUrl }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [status, setStatus] = useState('idle');
  const [result, setResult] = useState(null);

  const submit = async () => {
    if (!name.trim() || !email.trim() || !message.trim()) return;
    setStatus('loading'); setResult(null);
    try {
      const resp = await fetch(`${apiUrl}/api/v1/auth/contact`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), email: email.trim(), message: message.trim() }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Could not send message.');
      setResult({ ok: true, text: "Message sent — we'll get back to you." });
      setName(''); setEmail(''); setMessage('');
    } catch (e) {
      setResult({ ok: false, text: e.message });
    } finally {
      setStatus('idle');
    }
  };

  const inputStyle = { width: '100%', boxSizing: 'border-box', background: 'rgba(0,0,0,0.3)', border: `1px solid ${S.border}`, borderRadius: 3, color: S.text, fontFamily: S.mono, fontSize: 13, padding: '10px 11px' };

  return (
    <div style={{ maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <input placeholder="Name" value={name} onChange={e => setName(e.target.value)} style={inputStyle} />
      <input placeholder="Email" type="email" value={email} onChange={e => setEmail(e.target.value)} style={inputStyle} />
      <textarea placeholder="Message" value={message} onChange={e => setMessage(e.target.value)} rows={4} style={{ ...inputStyle, resize: 'vertical', fontFamily: S.mono }} />
      {result && <div style={{ fontFamily: S.mono, fontSize: 12, color: result.ok ? '#4a7c59' : '#ff7a45' }}>{result.text}</div>}
      <button onClick={submit} disabled={status === 'loading'}
        style={{ alignSelf: 'flex-start', padding: '10px 24px', fontFamily: S.mono, fontSize: 11, letterSpacing: 2, textTransform: 'uppercase', background: 'rgba(126,184,212,0.12)', border: `1px solid ${S.accent}`, borderRadius: 4, color: S.accent, cursor: status === 'loading' ? 'wait' : 'pointer' }}>
        {status === 'loading' ? '...' : 'SEND MESSAGE'}
      </button>
    </div>
  );
}

export default function LandingPage({ apiUrl, onAuthenticated }) {
  const [activeSection, setActiveSection] = useState('home');
  const [parallaxY, setParallaxY] = useState(0);
  const sectionRefs = useRef({});
  const scrollContainerRef = useRef(null);

  // Reset-password mode: a link from the reset email lands here with
  // ?reset_token=... in the URL.
  const resetToken = new URLSearchParams(window.location.search).get('reset_token');

  const scrollTo = useCallback((id) => {
    sectionRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  useEffect(() => {
    // Active nav-link highlighting on scroll — vanilla IntersectionObserver,
    // no scroll-animation library. `root` must be the actual scrolling
    // container (see below — this page manages its own scroll, since
    // body itself is position:fixed for the main app and can't scroll
    // at all), not the default viewport, or intersection detection
    // would silently never fire.
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) setActiveSection(entry.target.dataset.section);
        });
      },
      { root: scrollContainerRef.current, threshold: 0.4 }
    );
    Object.values(sectionRefs.current).forEach(el => el && observer.observe(el));
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    // Lightweight parallax: hero background scene drifts slower than
    // the page scroll — a plain scroll listener throttled via rAF, not
    // a library. Listens on the scroll container itself (see above),
    // not window — window/body never scrolls here.
    const container = scrollContainerRef.current;
    if (!container) return;
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        setParallaxY(container.scrollTop * 0.25);
        ticking = false;
      });
    };
    container.addEventListener('scroll', onScroll, { passive: true });
    return () => container.removeEventListener('scroll', onScroll);
  }, []);

  if (resetToken) {
    return (
      <div style={{ position: 'fixed', inset: 0, background: S.bg, overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Starfield />
        <div style={{ position: 'relative', zIndex: 1 }}>
          <ResetPasswordCard apiUrl={apiUrl} token={resetToken} />
        </div>
      </div>
    );
  }

  return (
    <div ref={scrollContainerRef} style={{ position: 'fixed', inset: 0, overflowY: 'auto', overflowX: 'hidden', background: S.bg, color: S.text, scrollBehavior: 'smooth' }}>
      {/* Navbar */}
      <div style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100, background: 'rgba(5,7,12,0.85)', backdropFilter: 'blur(8px)', borderBottom: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '14px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontFamily: S.mono, fontSize: 14, letterSpacing: 3, color: S.gold, fontWeight: 700 }}>VAYU</div>
          <div style={{ display: 'flex', gap: 28, alignItems: 'center' }}>
            {SECTIONS.map(([id, label]) => (
              <button key={id} onClick={() => scrollTo(id)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: S.mono, fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase', color: activeSection === id ? S.gold : S.text3, padding: 0 }}>
                {label}
              </button>
            ))}
            <button onClick={() => scrollTo('account')}
              style={{ padding: '8px 18px', fontFamily: S.mono, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(201,168,106,0.12)', border: `1px solid ${S.gold}`, borderRadius: 4, color: S.gold, cursor: 'pointer' }}>
              Sign In / Sign Up
            </button>
          </div>
        </div>
      </div>

      {/* Home / Hero — real satellite/Earth photography as a full-bleed
          cinematic backdrop, with a classic vanilla parallax (background
          drifts+scales slower than scroll, headline fades as you scroll
          past it) and a legibility gradient behind the overlaid copy. */}
      <div ref={el => sectionRefs.current.home = el} data-section="home" style={{ position: 'relative', height: '100vh', overflow: 'hidden' }}>
        <div style={{
          position: 'absolute', inset: 0,
          backgroundImage: 'url(/hero-satellite.jpg)', backgroundSize: 'cover', backgroundPosition: 'center',
          transform: `scale(${1.08 + parallaxY * 0.0003}) translateY(${parallaxY * 0.3}px)`,
        }} />
        {/* Legibility gradient — darkest at bottom-left where the copy
            sits, fading out toward the upper-right where the image
            should read clearly */}
        <div style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'linear-gradient(115deg, rgba(5,7,12,0.92) 0%, rgba(5,7,12,0.72) 32%, rgba(5,7,12,0.2) 62%, rgba(5,7,12,0.05) 100%)',
        }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(0deg, rgba(5,7,12,1) 0%, rgba(5,7,12,0) 18%)' }} />

        <div style={{
          position: 'relative', zIndex: 1, height: '100%', display: 'flex', alignItems: 'center', maxWidth: 1200, margin: '0 auto', padding: '0 28px',
          opacity: Math.max(0, 1 - parallaxY / 260), transform: `translateY(${parallaxY * 0.15}px)`,
        }}>
          <div style={{ maxWidth: 560 }}>
            <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 3, color: S.text3, textTransform: 'uppercase', marginBottom: 16 }}>
              Geospatial &amp; Business Intelligence
            </div>
            <div style={{ fontFamily: 'Georgia, serif', fontSize: 50, lineHeight: 1.15, color: S.text, marginBottom: 22, textShadow: '0 2px 24px rgba(0,0,0,0.6)' }}>
              One terminal for <span style={{ color: S.gold }}>everything above and around you.</span>
            </div>
            <div style={{ fontFamily: S.mono, fontSize: 13.5, color: S.text2, lineHeight: 1.8, marginBottom: 34, maxWidth: 480 }}>
              Live satellite analysis, maritime &amp; aviation tracking, commodity
              markets, drought &amp; agricultural risk scoring, and global hazard
              intel — unified, live, and actionable.
            </div>
            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
              <button onClick={() => scrollTo('account')}
                style={{ padding: '14px 32px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4, color: '#05070c', fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 24px rgba(201,168,106,0.3)' }}>
                Enter Terminal
              </button>
              <button onClick={() => scrollTo('about')}
                style={{ padding: '14px 28px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: 'transparent', border: `1px solid ${S.border}`, borderRadius: 4, color: S.text2, cursor: 'pointer' }}>
                See What It Does
              </button>
            </div>
          </div>
        </div>

        <div style={{
          position: 'absolute', bottom: 26, left: '50%', transform: 'translateX(-50%)', zIndex: 1,
          fontFamily: S.mono, fontSize: 10, letterSpacing: 2, color: S.text3, textTransform: 'uppercase', textAlign: 'center',
          opacity: Math.max(0, 1 - parallaxY / 120),
        }}>
          <div>Scroll to explore</div>
          <div style={{ marginTop: 6, fontSize: 14 }}>&#8595;</div>
        </div>

        <style>{'@keyframes vayu-landing-spin { to { transform: rotate(360deg); } }'}</style>
      </div>

      {/* About */}
      <div ref={el => sectionRefs.current.about = el} data-section="about" style={{ position: 'relative', padding: '100px 28px', borderTop: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 3, color: S.gold, textTransform: 'uppercase', marginBottom: 8 }}>About</div>
          <div style={{ fontFamily: 'Georgia, serif', fontSize: 30, color: S.text, marginBottom: 40 }}>What Vayu actually does</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 28 }}>
            {FEATURES.map(f => (
              <div key={f.title} style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 8, overflow: 'hidden' }}>
                <img src={f.img} alt={f.title} style={{ width: '100%', height: 160, objectFit: 'cover', display: 'block', borderBottom: `1px solid ${S.border}` }} />
                <div style={{ padding: '16px 18px' }}>
                  <div style={{ fontFamily: S.mono, fontSize: 13, color: S.gold, marginBottom: 8, letterSpacing: 0.5 }}>{f.title}</div>
                  <div style={{ fontFamily: S.mono, fontSize: 12, color: S.text3, lineHeight: 1.6 }}>{f.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Contact */}
      <div ref={el => sectionRefs.current.contact = el} data-section="contact" style={{ padding: '100px 28px', borderTop: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 3, color: S.gold, textTransform: 'uppercase', marginBottom: 8 }}>Contact</div>
          <div style={{ fontFamily: 'Georgia, serif', fontSize: 30, color: S.text, marginBottom: 30 }}>Get in touch</div>
          <ContactForm apiUrl={apiUrl} />
        </div>
      </div>

      {/* Account / Auth */}
      <div ref={el => sectionRefs.current.account = el} data-section="account" style={{ position: 'relative', minHeight: '80vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '100px 28px', borderTop: `1px solid ${S.border}`, overflow: 'hidden' }}>
        <Starfield />
        <div style={{ position: 'relative', zIndex: 1 }}>
          <AuthCard apiUrl={apiUrl} onAuthenticated={onAuthenticated} />
        </div>
      </div>
    </div>
  );
}
