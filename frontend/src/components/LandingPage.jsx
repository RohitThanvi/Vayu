/**
 * LandingPage.jsx
 * Shown by AppGate.jsx before the main app when there's no valid
 * session. Lightweight CSS/SVG visuals (starfield + orbit rings +
 * satellite glyph) rather than the Three.js scene used on the 404
 * page — this is the very first thing every visitor sees, so fast
 * paint matters more here than on an error page nobody plans to land
 * on; pulling in Three.js for this would slow down first load for
 * every single visitor.
 */

import { useState } from 'react';

const S = {
  bg: '#05070c', surface: 'rgba(13,17,23,0.75)', border: '#2a3040',
  text: '#ffffff', text2: 'rgba(255,255,255,0.75)', text3: 'rgba(255,255,255,0.5)',
  gold: '#c9a86a', goldBright: '#f5d98a',
  mono: "'JetBrains Mono','Courier New',monospace",
};

function Starfield() {
  // A fixed set of pre-computed star positions (deterministic, no
  // per-render randomness causing layout thrash) as a lightweight CSS
  // radial-gradient field rather than hundreds of DOM nodes.
  const stars = Array.from({ length: 90 }, (_, i) => {
    const seed = i * 137.5;
    const x = (seed * 3.7) % 100;
    const y = (seed * 5.3) % 100;
    const size = 1 + (i % 3);
    const opacity = 0.3 + ((i * 0.61) % 0.6);
    return { x, y, size, opacity };
  });
  return (
    <svg width="100%" height="100%" style={{ position: 'absolute', inset: 0 }}>
      {stars.map((s, i) => (
        <circle key={i} cx={`${s.x}%`} cy={`${s.y}%`} r={s.size / 2} fill="#ffffff" opacity={s.opacity} />
      ))}
    </svg>
  );
}

function OrbitGraphic() {
  return (
    <svg width="440" height="440" viewBox="0 0 440 440" style={{ opacity: 0.9 }}>
      <defs>
        <radialGradient id="earthGlow" cx="50%" cy="50%" r="50%">
          <stop offset="60%" stopColor="#0d1e33" />
          <stop offset="100%" stopColor="#0d1e33" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="earthBody" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#1a3a5c" />
          <stop offset="100%" stopColor="#0a1826" />
        </linearGradient>
      </defs>
      <circle cx="220" cy="360" r="140" fill="url(#earthGlow)" />
      <circle cx="220" cy="360" r="95" fill="url(#earthBody)" stroke="#2a4a6c" strokeWidth="1" />
      {[210, 170, 130].map((r, i) => (
        <ellipse key={i} cx="220" cy="200" rx={r} ry={r * 0.32} fill="none" stroke={S.gold} strokeOpacity={0.25 - i * 0.05} strokeWidth="1" />
      ))}
      {/* satellite glyph riding the outer orbit ring */}
      <g transform="translate(220 200) rotate(-25)">
        <g transform="translate(210 0)">
          <rect x="-9" y="-9" width="18" height="18" fill="none" stroke={S.goldBright} strokeWidth="1.4" />
          <rect x="-24" y="-4" width="13" height="8" fill="none" stroke={S.goldBright} strokeWidth="1.2" />
          <rect x="11" y="-4" width="13" height="8" fill="none" stroke={S.goldBright} strokeWidth="1.2" />
          <line x1="0" y1="-9" x2="0" y2="-17" stroke={S.goldBright} strokeWidth="1.2" />
        </g>
      </g>
    </svg>
  );
}

export default function LandingPage({ apiUrl, onAuthenticated }) {
  const [mode, setMode] = useState('login');   // 'login' | 'signup'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState(null);

  const submit = async () => {
    if (!email.trim() || !password) return;
    setStatus('loading'); setError(null);
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
    <div style={{ position: 'fixed', inset: 0, background: S.bg, overflow: 'hidden', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <Starfield />

      <div style={{
        position: 'relative', zIndex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        gap: 60, width: '100%', maxWidth: 1080, padding: '0 32px', flexWrap: 'wrap',
      }}>
        {/* Left: brand + orbit graphic + description */}
        <div style={{ flex: '1 1 420px', minWidth: 320, maxWidth: 480 }}>
          <div style={{ fontFamily: S.mono, fontSize: 13, letterSpacing: 4, color: S.gold, marginBottom: 6 }}>VAYU</div>
          <div style={{ fontFamily: S.mono, fontSize: 12, letterSpacing: 2, color: S.text3, textTransform: 'uppercase', marginBottom: 28 }}>
            Geospatial &amp; Business Intelligence
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 8 }}>
            <OrbitGraphic />
          </div>
          <div style={{ fontFamily: S.mono, fontSize: 12.5, color: S.text2, lineHeight: 1.8, marginTop: 12 }}>
            Live satellite analysis, maritime &amp; aviation tracking, commodity
            markets, drought &amp; agri risk scoring, and global hazard intel —
            unified in one terminal.
          </div>
        </div>

        {/* Right: auth card */}
        <div style={{
          flex: '0 0 340px', background: S.surface, border: `1px solid ${S.border}`,
          borderRadius: 8, padding: '30px 28px', backdropFilter: 'blur(6px)',
        }}>
          <div style={{ display: 'flex', gap: 6, marginBottom: 22 }}>
            {['login', 'signup'].map(m => (
              <button key={m} onClick={() => { setMode(m); setError(null); }}
                style={{
                  flex: 1, padding: '8px', fontFamily: S.mono, fontSize: 11, letterSpacing: 1.5, textTransform: 'uppercase',
                  cursor: 'pointer', background: mode === m ? 'rgba(201,168,106,0.12)' : 'transparent',
                  border: `1px solid ${mode === m ? S.gold : S.border}`, color: mode === m ? S.gold : S.text3,
                }}>
                {m === 'login' ? 'Log In' : 'Sign Up'}
              </button>
            ))}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <div style={{ fontFamily: S.mono, fontSize: 10, letterSpacing: 1, color: S.text3, marginBottom: 5, textTransform: 'uppercase' }}>Email</div>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submit()}
                style={{ width: '100%', boxSizing: 'border-box', background: 'rgba(0,0,0,0.3)', border: `1px solid ${S.border}`, borderRadius: 3, color: S.text, fontFamily: S.mono, fontSize: 13, padding: '10px 11px' }} />
            </div>
            <div>
              <div style={{ fontFamily: S.mono, fontSize: 10, letterSpacing: 1, color: S.text3, marginBottom: 5, textTransform: 'uppercase' }}>Password</div>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && submit()}
                style={{ width: '100%', boxSizing: 'border-box', background: 'rgba(0,0,0,0.3)', border: `1px solid ${S.border}`, borderRadius: 3, color: S.text, fontFamily: S.mono, fontSize: 13, padding: '10px 11px' }} />
            </div>

            {error && <div style={{ fontFamily: S.mono, fontSize: 11.5, color: '#ff7a45' }}>{error}</div>}

            <button onClick={submit} disabled={status === 'loading'}
              style={{
                marginTop: 6, padding: '11px', fontFamily: S.mono, fontSize: 12, letterSpacing: 2, textTransform: 'uppercase',
                background: `linear-gradient(180deg, ${S.goldBright}, ${S.gold})`, border: 'none', borderRadius: 4,
                color: '#05070c', fontWeight: 700, cursor: status === 'loading' ? 'wait' : 'pointer',
                opacity: status === 'loading' ? 0.7 : 1,
              }}>
              {status === 'loading' ? '...' : (mode === 'login' ? 'ENTER TERMINAL' : 'CREATE ACCOUNT')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
