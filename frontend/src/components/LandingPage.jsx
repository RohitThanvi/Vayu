/**
 * LandingPage.jsx
 * Single-page landing experience shown by AppGate.jsx before the main
 * app. Home/About/Contact sections live on one page, navigated by
 * smooth-scrolling to anchors (no router library). Sign In/Sign Up is
 * a modal overlay (glass-blur backdrop) triggered by the navbar/hero
 * buttons — not a permanent section — closed by clicking outside,
 * Escape, or the close button.
 *
 * Animation/interactivity, all vanilla (no animation library):
 * - IntersectionObserver-driven nav-link highlighting AND scroll-reveal
 *   (fade+rise) on About/Contact content as it enters view
 * - A scroll-position parallax on the hero background (plain scroll
 *   listener + rAF throttling) layered with an independent, always-
 *   running CSS Ken Burns zoom/pan for a "living" backdrop even before
 *   the user scrolls
 * - CSS-only star twinkle, card hover lift/glow, button hover states
 *
 * `root` for every IntersectionObserver here must be the actual
 * scrolling container (see scrollContainerRef below), not the default
 * viewport — this page runs its own internal scroll because the main
 * app's body is position:fixed (a fixed-viewport map tool) and can't
 * scroll at all. Getting this wrong previously made scroll-linked
 * features silently never fire.
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const S = {
  bg: '#05070c', surface: 'rgba(13,17,23,0.88)', surface2: '#0d1117', surface3: '#111826', border: '#2a3040', borderLight: '#3a4257',
  // Bumped from 0.5/0.75 — the old text3 sat around ~5:1 contrast on this
  // background, which reads as "washed out" for small mono body copy even
  // though it technically cleared AA. text2/text3 raised so descriptive
  // copy stays legible without going full white (keeps the muted terminal
  // mood); text4 is for genuinely decorative/tertiary labels only.
  text: '#ffffff', text2: 'rgba(255,255,255,0.86)', text3: 'rgba(255,255,255,0.68)', text4: 'rgba(255,255,255,0.5)',
  gold: '#c9a86a', goldBright: '#f5d98a', goldDim: 'rgba(201,168,106,0.55)', accent: '#7eb8d4',
  mono: "'JetBrains Mono','Courier New',monospace",
};

const SECTIONS = [['home', 'Home'], ['about', 'About'], ['contact', 'Contact']];

const STATS = [
  { n: '9+', label: 'Fixed satellite analysis metrics', icon: 'M4 17l5-5 3 3 6-8M4 17V7M4 17h16' },
  { n: '10+', label: 'Live data sources fused into one feed', icon: 'M4 6a8 3 0 0016 0 8 3 0 00-16 0zM4 6v6a8 3 0 0016 0V6M4 12v6a8 3 0 0016 0v-6' },
  { n: '24/7', label: 'Automated monitoring & daily reports', icon: 'M12 7v5l3 3M12 21a9 9 0 100-18 9 9 0 000 18z' },
  { n: '1', label: 'Terminal for every domain — sea, sky, soil, markets', icon: 'M3 12h4l2-7 4 14 2-7h6' },
];

const FLOW = [
  { title: 'Live Sources', desc: 'Satellite imagery, AIS/ADS-B feeds, weather & AQI stations, commodity markets, gov. agri data', icon: 'M12 2l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z' },
  { title: 'Fusion & Scoring', desc: 'Composite risk scores, trend detection, correlation & anomaly flags, LLM narrative summaries', icon: 'M12 2a10 10 0 100 20 10 10 0 000-20zM12 6v6l4 2' },
  { title: 'One Terminal', desc: 'Unified map, dashboards, alerts and scheduled email reports — no context switching', icon: 'M3 5h18v13a2 2 0 01-2 2H5a2 2 0 01-2-2V5zM3 9h18' },
];

const FEATURES = [
  { title: 'Satellite Analysis', desc: 'Vegetation, drought, flood, fire, deforestation & more — 9 fixed metrics plus open-ended AI research on any AOI.', img: '/screenshots/analyze-maritime.jpg' },
  { title: 'Weather & Air Quality', desc: 'Live temperature, wind, pressure overlays, and real-time CPCB air quality across India.', img: '/screenshots/weather-layers.jpg' },
  { title: 'Agricultural Intelligence', desc: 'A composite 0-100 risk score fusing vegetation stress, drought, and soil moisture into one plain-language read.', img: '/screenshots/agri-dashboard.jpg' },
  { title: 'Orbital Tracking', desc: 'Live aircraft and satellite positions rendered on an interactive 3D globe.', img: '/screenshots/orbital-view.jpg' },
];

// One shared stylesheet for everything that needs a real CSS animation
// or :hover state — inline style objects can't express either, so
// this is the one place this file breaks from inline styles.
const GlobalStyle = () => (
  <style>{`
    @keyframes vayu-spin { to { transform: rotate(360deg); } }
    @keyframes vayu-kenburns {
      0%   { transform: scale(1.06) translate(0%, 0%); }
      50%  { transform: scale(1.16) translate(-1.2%, -1%); }
      100% { transform: scale(1.06) translate(0%, 0%); }
    }
    @keyframes vayu-modal-fade-in { from { opacity: 0; } to { opacity: 1; } }
    @keyframes vayu-modal-pop-in { from { opacity: 0; transform: scale(0.94) translateY(8px); } to { opacity: 1; transform: scale(1) translateY(0); } }
    .vayu-hero-bg { animation: vayu-kenburns 34s ease-in-out infinite; }
    .vayu-feature-card { transition: transform 0.25s ease, box-shadow 0.25s ease, border-color 0.25s ease; transform-style: preserve-3d; }
    .vayu-feature-card:hover { border-color: #c9a86a66; box-shadow: 0 22px 50px rgba(0,0,0,0.55), 0 4px 16px rgba(201,168,106,0.12); }
    .vayu-feature-card:hover .vayu-feature-img { transform: scale(1.06); }
    .vayu-feature-img { transition: transform 0.4s ease; }
    .vayu-nav-btn { transition: color 0.2s ease; position: relative; }
    .vayu-nav-btn::after {
      content: ''; position: absolute; left: 0; right: 0; bottom: -6px; height: 2px;
      background: #c9a86a; transform: scaleX(0); transform-origin: center;
      transition: transform 0.25s ease;
    }
    .vayu-nav-btn:hover { color: #f5d98a !important; }
    .vayu-nav-btn:hover::after { transform: scaleX(1); }
    .vayu-cta-primary { transition: transform 0.2s ease, box-shadow 0.2s ease; }
    .vayu-cta-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(201,168,106,0.45); }
    .vayu-cta-secondary { transition: border-color 0.2s ease, color 0.2s ease; }
    .vayu-cta-secondary:hover { border-color: #c9a86a; color: #f5d98a; }
    .vayu-stat-card { transition: border-color 0.25s ease, transform 0.25s ease; }
    .vayu-stat-card:hover { border-color: #c9a86a55; transform: translateY(-3px); }
    .vayu-flow-node { transition: opacity 0.4s ease; }
    input::placeholder, textarea::placeholder { color: rgba(255,255,255,0.4); }
  `}</style>
);

// Subtle mouse-tracked 3D tilt, shared by feature cards and the auth
// card. Kept small (max ~5-6deg) and spring-eased back to flat on
// leave — enough to read as "real" depth on hover, not a gimmick.
function useTilt(maxDeg = 5) {
  const ref = useRef(null);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const onMouseMove = useCallback((e) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    setTilt({ x: py * -maxDeg, y: px * maxDeg });
  }, [maxDeg]);
  const onMouseLeave = useCallback(() => setTilt({ x: 0, y: 0 }), []);
  return { ref, tilt, onMouseMove, onMouseLeave };
}

function TiltCard({ children, style, className }) {
  const { ref, tilt, onMouseMove, onMouseLeave } = useTilt(5);
  return (
    <div style={{ perspective: 900 }}>
      <div ref={ref} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} className={className}
        style={{ ...style, transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg) translateZ(0)` }}>
        {children}
      </div>
    </div>
  );
}

function Icon({ path, size = 20, color = S.gold }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d={path} />
    </svg>
  );
}

// Scroll-reveal wrapper: fades + rises into place the first time it
// enters the viewport, then stays (disconnects its own observer —
// this is a one-time entrance, not a repeat-on-every-scroll effect).
function Reveal({ children, delay = 0, root }) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) { setVisible(true); observer.disconnect(); }
      },
      { root, threshold: 0.15 }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [root]);

  return (
    <div ref={ref} style={{
      opacity: visible ? 1 : 0,
      transform: visible ? 'translateY(0)' : 'translateY(28px)',
      transition: `opacity 0.7s ease ${delay}s, transform 0.7s ease ${delay}s`,
    }}>
      {children}
    </div>
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
    <div>
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

// Shared card shell for the auth modal / reset-password view — real
// satellite imagery (reusing the hero photo) instead of the starfield,
// with a subtle mouse-tracked 3D tilt for some depth without being
// gimmicky. `onClose` button is explicitly stacked ABOVE the content
// wrapper (zIndex 2 vs 1) — previously they were both zIndex:1, so the
// content div (later in DOM, same z-index) silently ate every click on
// the × since equal z-index falls back to DOM order.
function SpaceCard({ children, onClose }) {
  const { ref: cardRef, tilt, onMouseMove, onMouseLeave } = useTilt(4);

  return (
    <div style={{ perspective: 1200 }}>
      <div ref={cardRef} onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} style={{
        position: 'relative', width: 380, maxWidth: '100%', borderRadius: 10, overflow: 'hidden',
        border: `1px solid ${S.gold}55`, boxShadow: '0 0 60px rgba(201,168,106,0.15), 0 30px 70px rgba(0,0,0,0.65)',
        transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)`, transformStyle: 'preserve-3d',
        transition: 'transform 0.3s ease',
      }}>
        <div style={{
          position: 'absolute', inset: 0, backgroundImage: 'url(/hero-satellite.jpg)', backgroundSize: 'cover',
          backgroundPosition: 'center', transform: 'translateZ(-1px) scale(1.05)',
        }} />
        <div style={{
          position: 'absolute', inset: 0,
          background: `linear-gradient(160deg, rgba(5,7,12,0.94) 0%, rgba(5,7,12,0.9) 45%, rgba(13,17,23,0.93) 100%)`,
        }} />
        {onClose && (
          <button onClick={onClose} aria-label="Close" type="button"
            style={{ position: 'absolute', top: 10, right: 12, background: 'rgba(5,7,12,0.4)', border: 'none', borderRadius: '50%', width: 30, height: 30, color: S.text2, fontSize: 20, lineHeight: 1, cursor: 'pointer', zIndex: 2 }}>
            &times;
          </button>
        )}
        <div style={{ position: 'relative', zIndex: 1, padding: '32px 28px' }}>
          {children}
        </div>
      </div>
    </div>
  );
}

function AuthModal({ apiUrl, onAuthenticated, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
      background: 'rgba(5,7,12,0.55)', backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)',
      animation: 'vayu-modal-fade-in 0.2s ease',
    }}>
      <div onClick={e => e.stopPropagation()} style={{ animation: 'vayu-modal-pop-in 0.25s ease' }}>
        <SpaceCard onClose={onClose}>
          <AuthCard apiUrl={apiUrl} onAuthenticated={onAuthenticated} />
        </SpaceCard>
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
    <SpaceCard>
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
    </SpaceCard>
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
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const sectionRefs = useRef({});
  const scrollContainerRef = useRef(null);

  // Reset-password mode: a link from the reset email lands here with
  // ?reset_token=... in the URL.
  const resetToken = new URLSearchParams(window.location.search).get('reset_token');

  const scrollTo = useCallback((id) => {
    sectionRefs.current[id]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  useEffect(() => {
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
        <GlobalStyle />
        <ResetPasswordCard apiUrl={apiUrl} token={resetToken} />
      </div>
    );
  }

  return (
    <div ref={scrollContainerRef} style={{ position: 'fixed', inset: 0, overflowY: 'auto', overflowX: 'hidden', background: S.bg, color: S.text, scrollBehavior: 'smooth' }}>
      <GlobalStyle />

      {authModalOpen && (
        <AuthModal apiUrl={apiUrl} onAuthenticated={onAuthenticated} onClose={() => setAuthModalOpen(false)} />
      )}

      {/* Navbar */}
      <div style={{ position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100, background: 'rgba(5,7,12,0.85)', backdropFilter: 'blur(8px)', borderBottom: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', padding: '14px 28px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <img src="/logo.png" alt="Vayu" width="20" height="20" style={{ display: 'block', filter: 'drop-shadow(0 0 4px rgba(201,168,106,0.4))' }} />
            <div style={{ fontFamily: S.mono, fontSize: 14, letterSpacing: 3, color: S.gold, fontWeight: 700 }}>VAYU</div>
          </div>
          <div style={{ display: 'flex', gap: 32, alignItems: 'center' }}>
            {SECTIONS.map(([id, label]) => (
              <button key={id} className="vayu-nav-btn" onClick={() => scrollTo(id)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontFamily: S.mono, fontSize: 13.5, letterSpacing: 1.5, textTransform: 'uppercase', color: activeSection === id ? S.gold : S.text3, padding: '4px 2px' }}>
                {label}
              </button>
            ))}
            <button onClick={() => setAuthModalOpen(true)}
              style={{ padding: '8px 18px', fontFamily: S.mono, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(201,168,106,0.12)', border: `1px solid ${S.gold}`, borderRadius: 4, color: S.gold, cursor: 'pointer' }}>
              Sign In / Sign Up
            </button>
          </div>
        </div>
      </div>

      {/* Home / Hero */}
      <div ref={el => sectionRefs.current.home = el} data-section="home" style={{ position: 'relative', height: '100vh', overflow: 'hidden' }}>
        {/* Scroll-driven layer (translateY only) wraps an independently
            always-animating Ken Burns layer (CSS keyframes) — two
            separate transform sources on two separate elements, so
            they don't fight over the same inline style. */}
        <div style={{ position: 'absolute', inset: 0, transform: `translateY(${parallaxY * 0.3}px)` }}>
          <div className="vayu-hero-bg" style={{
            position: 'absolute', inset: -24,
            backgroundImage: 'url(/hero-satellite.jpg)', backgroundSize: 'cover', backgroundPosition: 'center',
          }} />
        </div>

        <div style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'linear-gradient(115deg, rgba(5,7,12,0.92) 0%, rgba(5,7,12,0.72) 32%, rgba(5,7,12,0.2) 62%, rgba(5,7,12,0.05) 100%)',
        }} />
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', background: 'linear-gradient(0deg, rgba(5,7,12,1) 0%, rgba(5,7,12,0) 18%)' }} />
        <img src="/logo.png" alt="" aria-hidden style={{
          position: 'absolute', right: '6%', bottom: '10%', width: 220, opacity: 0.07,
          filter: 'grayscale(0.4)', pointerEvents: 'none',
        }} />

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
              <button className="vayu-cta-primary" onClick={() => setAuthModalOpen(true)}
                style={{ padding: '14px 32px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4, color: '#05070c', fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 24px rgba(201,168,106,0.3)' }}>
                Enter Terminal
              </button>
              <button className="vayu-cta-secondary" onClick={() => scrollTo('about')}
                style={{ padding: '14px 28px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase', background: 'rgba(13,17,23,0.7)', backdropFilter: 'blur(4px)', border: `1px solid ${S.borderLight}`, borderRadius: 4, color: S.text, cursor: 'pointer' }}>
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
      </div>

      {/* About */}
      <div ref={el => sectionRefs.current.about = el} data-section="about" style={{ position: 'relative', padding: '100px 28px', borderTop: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal root={scrollContainerRef.current}>
            <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 3, color: S.gold, textTransform: 'uppercase', marginBottom: 8 }}>About</div>
            <div style={{ fontFamily: 'Georgia, serif', fontSize: 30, color: S.text, marginBottom: 18 }}>What Vayu actually does</div>
            <div style={{ fontFamily: S.mono, fontSize: 13.5, color: S.text2, lineHeight: 1.85, maxWidth: 760, marginBottom: 56 }}>
              Vayu pulls together satellite imagery, maritime &amp; flight traffic, weather and
              air quality, commodity prices, and government agricultural data into one
              continuously-updating picture — instead of a dozen dashboards, tabs, and stale
              PDFs. Point it at any area on Earth and it runs the analysis, scores the risk,
              and explains what changed in plain language, then keeps watching so you don't
              have to check back manually.
            </div>
          </Reveal>

          {/* Stat strip — quiet infographic, same palette as the rest of
              the page (gold hairline icons on dark cards) so it reads as
              part of the design rather than a bolted-on dashboard widget. */}
          <Reveal root={scrollContainerRef.current}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 64 }}>
              {STATS.map((s) => (
                <div key={s.label} className="vayu-stat-card" style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 8, padding: '20px 20px' }}>
                  <div style={{ marginBottom: 14 }}><Icon path={s.icon} /></div>
                  <div style={{ fontFamily: 'Georgia, serif', fontSize: 26, color: S.goldBright, marginBottom: 6 }}>{s.n}</div>
                  <div style={{ fontFamily: S.mono, fontSize: 11.5, color: S.text3, lineHeight: 1.5 }}>{s.label}</div>
                </div>
              ))}
            </div>
          </Reveal>

          {/* How it works — a simple three-step flow, connected with a
              single hairline instead of arrows/animation, to stay calm
              rather than "dashboard-y". */}
          <Reveal root={scrollContainerRef.current}>
            <div style={{ fontFamily: S.mono, fontSize: 11, letterSpacing: 2.5, color: S.text3, textTransform: 'uppercase', marginBottom: 24 }}>How it works</div>
            <div style={{ position: 'relative', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 28, marginBottom: 64 }}>
              {FLOW.map((f, i) => (
                <div key={f.title} className="vayu-flow-node" style={{ position: 'relative' }}>
                  <div style={{ width: 38, height: 38, borderRadius: '50%', background: S.surface2, border: `1px solid ${S.goldDim}`, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 16 }}>
                    <Icon path={f.icon} size={18} />
                  </div>
                  <div style={{ fontFamily: S.mono, fontSize: 13, color: S.text, marginBottom: 8, letterSpacing: 0.5 }}>
                    <span style={{ color: S.text4, marginRight: 8 }}>{String(i + 1).padStart(2, '0')}</span>{f.title}
                  </div>
                  <div style={{ fontFamily: S.mono, fontSize: 12, color: S.text3, lineHeight: 1.6, maxWidth: 320 }}>{f.desc}</div>
                </div>
              ))}
            </div>
          </Reveal>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 28 }}>
            {FEATURES.map((f, i) => (
              <Reveal key={f.title} delay={i * 0.1} root={scrollContainerRef.current}>
                <TiltCard className="vayu-feature-card" style={{ background: S.surface2, border: `1px solid ${S.border}`, borderRadius: 8, overflow: 'hidden', height: '100%' }}>
                  <div style={{ overflow: 'hidden' }}>
                    <img className="vayu-feature-img" src={f.img} alt={f.title} style={{ width: '100%', height: 160, objectFit: 'cover', display: 'block', borderBottom: `1px solid ${S.border}` }} />
                  </div>
                  <div style={{ padding: '16px 18px' }}>
                    <div style={{ fontFamily: S.mono, fontSize: 13, color: S.gold, marginBottom: 8, letterSpacing: 0.5 }}>{f.title}</div>
                    <div style={{ fontFamily: S.mono, fontSize: 12, color: S.text2, lineHeight: 1.6 }}>{f.desc}</div>
                  </div>
                </TiltCard>
              </Reveal>
            ))}
          </div>
        </div>
      </div>

      {/* Contact */}
      <div ref={el => sectionRefs.current.contact = el} data-section="contact" style={{ padding: '100px 28px', borderTop: `1px solid ${S.border}` }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <Reveal root={scrollContainerRef.current}>
            <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 3, color: S.gold, textTransform: 'uppercase', marginBottom: 8 }}>Contact</div>
            <div style={{ fontFamily: 'Georgia, serif', fontSize: 30, color: S.text, marginBottom: 30 }}>Get in touch</div>
            <ContactForm apiUrl={apiUrl} />
          </Reveal>
        </div>
      </div>

      {/* Footer */}
      <div style={{ borderTop: `1px solid ${S.border}`, padding: '28px 28px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <img src="/logo.png" alt="" width="15" height="15" style={{ display: 'block', opacity: 0.7 }} />
            <div style={{ fontFamily: S.mono, fontSize: 11, letterSpacing: 1, color: S.text3 }}>
              &copy; {new Date().getFullYear()} Rosoft. All rights reserved.
            </div>
          </div>
          <div style={{ fontFamily: S.mono, fontSize: 11, letterSpacing: 1, color: S.text4 }}>VAYU — Geospatial &amp; Business Intelligence</div>
        </div>
      </div>
    </div>
  );
}
