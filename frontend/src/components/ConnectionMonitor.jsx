/**
 * ConnectionMonitor — what the person sees when Vayu is already open and the connection drops.
 *
 *  - Browser reports it is OFFLINE  -> full-screen "No Signal" screen (same look as public/offline.html), dismissible so the last loaded
 *    map/data stays usable; once dismissed it becomes a slim banner. Back online -> a brief "link re-established" toast.
 *  - Browser is online but Vayu's SERVER stops answering (a heartbeat to GET /health fails twice in a row) -> a slim banner, NOT a full
 *    screen, because the site itself still works. Free-tier servers sleep when idle, so "waking up" is a common, honest explanation.
 *
 * The heartbeat only runs while the tab is visible and the browser is online (a few hundred bytes every 30 s). Nothing here changes
 * application state or blocks the app.
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const PUNCHLINES = [
  'Perhaps Vayu needs to enter the satnet industry as well.',
  'Our satellites can see your whole continent. Your Wi-Fi, not so much.',
  "Orbit is fine. It's the last mile that's down.",
  'Have you tried turning the planet off and on again?',
  'Somewhere, a ground station is blushing.',
  'We track ships, storms and markets. Your router is next on the list.',
];
const GOLD = '#e8c15c', GOLD_BRIGHT = '#f5d98a', SPACE = '#05070c';
const DISPLAY = "'Orbitron','Eurostile','Bank Gothic','Segoe UI',system-ui,sans-serif";
const BODY = "'Rajdhani','Segoe UI',system-ui,-apple-system,sans-serif";

const CSS = `
@font-face{font-family:'Orbitron';font-weight:700;font-display:swap;src:local('Orbitron Bold'),url('/fonts/orbitron-latin-700-normal.woff2') format('woff2')}
@font-face{font-family:'Orbitron';font-weight:900;font-display:swap;src:local('Orbitron Black'),url('/fonts/orbitron-latin-900-normal.woff2') format('woff2')}
@font-face{font-family:'Rajdhani';font-weight:500;font-display:swap;src:local('Rajdhani Medium'),url('/fonts/rajdhani-latin-500-normal.woff2') format('woff2')}
@font-face{font-family:'Rajdhani';font-weight:600;font-display:swap;src:local('Rajdhani SemiBold'),url('/fonts/rajdhani-latin-600-normal.woff2') format('woff2')}
@keyframes vcm-float{0%,100%{transform:translateY(0) rotate(-1.2deg)}50%{transform:translateY(-9px) rotate(1.2deg)}}
@keyframes vcm-ping{0%{opacity:0;transform:translateY(0) scale(.35)}12%{opacity:.95}100%{opacity:0;transform:translateY(92px) scale(1.7)}}
@keyframes vcm-twinkle{0%,100%{opacity:.25}50%{opacity:1}}
@keyframes vcm-blink{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes vcm-in{from{opacity:0;transform:translate(-50%,10px)}to{opacity:1;transform:translate(-50%,0)}}
.vcm-sat{animation:vcm-float 7s ease-in-out infinite;overflow:visible}
.vcm-arc{fill:none;stroke:${GOLD};stroke-width:3;stroke-linecap:round;opacity:0;transform-box:fill-box;transform-origin:50% 0;animation:vcm-ping 3.6s ease-out infinite}
.vcm-arc:nth-of-type(2){animation-delay:1.2s}.vcm-arc:nth-of-type(3){animation-delay:2.4s}
.vcm-star{position:absolute;border-radius:50%;background:#fff;animation:vcm-twinkle 3s ease-in-out infinite}
.vcm-dot{animation:vcm-blink 1.6s ease-in-out infinite}
.vcm-btn{font-family:${DISPLAY};font-weight:700;font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:${SPACE};cursor:pointer;background:linear-gradient(180deg,${GOLD_BRIGHT},${GOLD});border:1px solid rgba(232,193,92,.6);border-radius:4px;padding:12px 26px;box-shadow:0 0 24px rgba(232,193,92,.25)}
.vcm-btn:disabled{opacity:.6;cursor:progress}.vcm-btn:focus-visible,.vcm-link:focus-visible{outline:2px solid ${GOLD_BRIGHT};outline-offset:3px}
.vcm-link{background:none;border:none;color:rgba(232,193,92,.75);font-family:${BODY};font-weight:600;font-size:13px;letter-spacing:.08em;cursor:pointer;text-decoration:underline;padding:6px}
/* Bottom-centre: a persistent notice must never cover the navigation or top bars. On phones it clears the bottom navigation bar. */
.vcm-bar{position:fixed;left:50%;bottom:max(16px,env(safe-area-inset-bottom));transform:translateX(-50%);width:max-content;animation:vcm-in .25s ease-out}
@media (max-width:768px){.vcm-bar{bottom:calc(env(safe-area-inset-bottom) + 68px)}}
@media (prefers-reduced-motion:reduce){.vcm-sat,.vcm-arc,.vcm-star,.vcm-dot,.vcm-bar{animation:none!important}.vcm-arc{opacity:.35}}
`;

// Deterministic pseudo-random so the sky is identical on every render (no flicker, no layout shift).
const rng = (seed) => () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
const STARS = (() => { const r = rng(7); return Array.from({ length: 70 }, () => ({ x: r() * 100, y: r() * 78, s: r() * 1.6 + 0.6, d: r() * 3, o: 0.4 + r() * 0.6 })); })();
const LIGHTS = (() => { const r = rng(11); return Array.from({ length: 46 }, () => { const a = (r() - 0.5) * 1.0, d = Math.pow(r(), 1.8) * 14 + 0.6; return { x: 50 + Math.sin(a) * (50 - d * 0.5), y: d, o: 0.35 + r() * 0.6 }; }); })();

function Satellite({ width = 'clamp(150px,28vw,300px)' }) {
  return (
    <svg className="vcm-sat" viewBox="0 0 400 270" style={{ width, maxHeight: '100%' }} aria-hidden="true">
      <defs>
        <linearGradient id="vcm-gold" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#f5d98a" /><stop offset=".55" stopColor="#d4a13a" /><stop offset="1" stopColor="#8f6418" /></linearGradient>
        <linearGradient id="vcm-panel" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#2a4274" /><stop offset="1" stopColor="#101c3a" /></linearGradient>
        <linearGradient id="vcm-dish" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff" /><stop offset="1" stopColor="#b9bec4" /></linearGradient>
      </defs>
      <g stroke="#e8c15c" strokeOpacity=".55" strokeWidth="1">
        <rect x="6" y="88" width="136" height="64" rx="2" fill="url(#vcm-panel)" /><rect x="258" y="88" width="136" height="64" rx="2" fill="url(#vcm-panel)" />
        <path d="M28 88v64M50 88v64M72 88v64M94 88v64M116 88v64M6 120h136M280 88v64M302 88v64M324 88v64M346 88v64M368 88v64M258 120h136" fill="none" />
      </g>
      <rect x="142" y="116" width="26" height="8" fill="#b9bec4" /><rect x="232" y="116" width="26" height="8" fill="#b9bec4" />
      <rect x="164" y="74" width="72" height="92" rx="4" fill="url(#vcm-gold)" stroke="#f5d98a" strokeOpacity=".6" />
      <path d="M164 104h72M164 136h72" stroke="#7a560f" strokeOpacity=".55" fill="none" /><rect x="214" y="82" width="14" height="14" rx="2" fill="#2a2a2e" />
      <g stroke="#2a2a2e" strokeWidth="2" strokeLinecap="round"><path d="M176 74V46M200 74V34M224 74V46" /></g><circle cx="200" cy="33" r="3" fill="#e8c15c" />
      <path d="M156 174 Q200 214 244 174 Z" fill="url(#vcm-dish)" stroke="#8d939a" /><path d="M200 166v18" stroke="#2a2a2e" strokeWidth="3" /><circle cx="200" cy="190" r="4" fill="#2a2a2e" />
      <g transform="translate(0 196)"><path className="vcm-arc" d="M168 0 Q200 26 232 0" /><path className="vcm-arc" d="M168 0 Q200 26 232 0" /><path className="vcm-arc" d="M168 0 Q200 26 232 0" /></g>
    </svg>
  );
}

const pad = (n) => (n < 10 ? '0' : '') + n;
const clock = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(s / 60); return `${m >= 60 ? pad(Math.floor(m / 60)) + ':' : ''}${pad(m % 60)}:${pad(s % 60)}`; };

export function OfflineScreen({ since, busy, note, onRetry, onDismiss }) {
  const [now, setNow] = useState(() => Date.now());
  const [pi, setPi] = useState(0);
  const [fade, setFade] = useState(false);
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  useEffect(() => {
    if (reduced) return undefined;
    let swap;
    const t = setInterval(() => { setFade(true); swap = setTimeout(() => { setPi((i) => (i + 1) % PUNCHLINES.length); setFade(false); }, 600); }, 6500);
    return () => { clearInterval(t); clearTimeout(swap); };
  }, [reduced]);
  useEffect(() => { const k = (e) => { if (e.key === 'Escape') onDismiss(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onDismiss]);
  return (
    <div role="dialog" aria-modal="true" aria-label="No internet connection" data-testid="offline-screen"
         style={{ position: 'fixed', inset: 0, zIndex: 100000, background: SPACE, color: GOLD, fontFamily: BODY, overflow: 'hidden', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'space-between', padding: 'max(clamp(20px,5vh,56px),env(safe-area-inset-top)) 20px max(clamp(20px,4.5vh,44px),env(safe-area-inset-bottom))' }}>
      <style>{CSS}</style>
      <div aria-hidden="true" style={{ position: 'absolute', inset: 0 }}>{STARS.map((s, i) => <i key={i} className="vcm-star" style={{ left: `${s.x}%`, top: `${s.y}%`, width: s.s, height: s.s, opacity: s.o, animationDelay: `${s.d}s` }} />)}</div>
      <div aria-hidden="true" style={{ position: 'absolute', left: '50%', bottom: 'calc(-150vmax + 17vh)', width: '170vmax', height: '170vmax', transform: 'translateX(-50%)', borderRadius: '50%', overflow: 'hidden',
        background: 'radial-gradient(circle at 50% 0%,#0f2742 0%,#0a1830 22%,#060d1c 48%,#04070f 100%)', boxShadow: '0 -3px 50px rgba(111,168,220,.38),0 -1px 0 rgba(160,205,245,.55),inset 0 10px 60px rgba(111,168,220,.2)' }}>
        {LIGHTS.map((l, i) => <i key={i} style={{ position: 'absolute', left: `${l.x}%`, top: `${l.y}%`, width: 2, height: 2, borderRadius: '50%', background: GOLD_BRIGHT, boxShadow: '0 0 5px 1px rgba(232,193,92,.65)', opacity: l.o }} />)}
      </div>
      <header style={{ position: 'relative' }}>
        <h1 style={{ margin: 0, fontFamily: DISPLAY, fontWeight: 900, lineHeight: 1.02, letterSpacing: '.05em', textTransform: 'uppercase', fontSize: 'clamp(40px,11vw,118px)', background: `linear-gradient(180deg,${GOLD_BRIGHT} 0%,${GOLD} 55%,#a9781f 100%)`, WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent', filter: 'drop-shadow(0 0 22px rgba(232,193,92,.3))' }}>No Signal</h1>
        <p style={{ margin: '8px 0 10px', fontFamily: DISPLAY, fontWeight: 700, fontStyle: 'italic', textTransform: 'uppercase', letterSpacing: '.12em', fontSize: 'clamp(14px,2.8vw,24px)' }}>Downlink interrupted</p>
        <p style={{ margin: '0 auto', fontWeight: 600, fontSize: 'clamp(17px,2.4vw,22px)', letterSpacing: '.04em', color: 'rgba(245,217,138,.92)' }}>Seems there's no internet.</p>
        <p data-testid="punchline" style={{ margin: '10px auto 0', minHeight: '2.6em', maxWidth: 560, fontWeight: 500, fontStyle: 'italic', fontSize: 'clamp(15px,2vw,19px)', letterSpacing: '.03em', lineHeight: 1.3, color: 'rgba(232,193,92,.78)', transition: 'opacity .6s ease', opacity: fade ? 0 : 1 }}>{PUNCHLINES[pi]}</p>
        <div style={{ marginTop: 12, fontWeight: 600, fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: 'rgba(232,193,92,.45)', fontVariantNumeric: 'tabular-nums' }}>
          VAYU · Link <b style={{ color: GOLD, fontWeight: 600 }}><span className="vcm-dot" style={{ display: 'inline-block', width: 7, height: 7, marginRight: 6, borderRadius: '50%', background: GOLD, boxShadow: `0 0 8px ${GOLD}` }} />Offline</b> · T+{clock(now - since)}
        </div>
      </header>
      <div aria-hidden="true" style={{ position: 'relative', flex: '1 1 auto', minHeight: 110, display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Satellite /></div>
      <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
        <button className="vcm-btn" type="button" onClick={onRetry} disabled={busy} autoFocus>Retry connection</button>
        <div role="status" aria-live="polite" style={{ minHeight: '1.4em', fontWeight: 600, fontSize: 13, letterSpacing: '.08em', color: 'rgba(232,193,92,.75)' }}>{busy ? 'Checking for signal…' : note || 'Listening for signal…'}</div>
        <button className="vcm-link" type="button" onClick={onDismiss}>Keep viewing the last loaded data</button>
      </div>
    </div>
  );
}

function Bar({ children, action, tone = 'warn', testid }) {
  return (
    <div role="status" aria-live="polite" data-testid={testid} className="vcm-bar"
         style={{ zIndex: 99999, maxWidth: 'min(640px,calc(100vw - 16px))', display: 'flex', gap: 10, alignItems: 'center',
                  background: 'rgba(5,7,12,.94)', border: `1px solid ${tone === 'ok' ? 'rgba(123,216,143,.6)' : 'rgba(232,193,92,.55)'}`, borderRadius: 6, padding: '8px 14px', boxShadow: '0 6px 24px rgba(0,0,0,.6)',
                  fontFamily: BODY, fontWeight: 600, fontSize: 13, letterSpacing: '.04em', color: tone === 'ok' ? '#9be3ab' : GOLD_BRIGHT }}>
      <style>{CSS}</style>
      <span>{children}</span>{action}
    </div>
  );
}

export default function ConnectionMonitor({ apiUrl = '', healthPath = '/health', intervalMs = 30000, firstCheckMs = 3000, timeoutMs = 8000, failThreshold = 2, toastMs = 3000 }) {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine !== false));
  const [serverDown, setServerDown] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const since = useRef(Date.now());
  const fails = useRef(0);
  const downRef = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const ping = useCallback(async () => {
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const to = setTimeout(() => ctl?.abort(), timeoutMs);
    try {
      const r = await fetch(`${apiUrl}${healthPath}`, { cache: 'no-store', signal: ctl?.signal });
      return !!(r && r.ok);
    } catch { return false; } finally { clearTimeout(to); }
  }, [apiUrl, healthPath, timeoutMs]);

  const heartbeat = useCallback(async () => {
    const ok = await ping();
    if (!alive.current) return ok;
    if (ok) {
      fails.current = 0;
      if (downRef.current) { downRef.current = false; setServerDown(false); setToast('server'); }
    } else {
      fails.current += 1;
      if (fails.current >= failThreshold && !downRef.current) { downRef.current = true; setServerDown(true); }
    }
    return ok;
  }, [ping, failThreshold]);

  useEffect(() => {
    const off = () => { since.current = Date.now(); setOnline(false); setDismissed(false); setNote(''); };
    const on = () => { setOnline(true); setToast('net'); };
    window.addEventListener('offline', off); window.addEventListener('online', on);
    return () => { window.removeEventListener('offline', off); window.removeEventListener('online', on); };
  }, []);

  useEffect(() => {
    if (!online) return undefined;
    let timer = null, first = null;
    const tick = () => { if (typeof document === 'undefined' || !document.hidden) heartbeat(); };
    first = setTimeout(() => { tick(); timer = setInterval(tick, intervalMs); }, firstCheckMs);
    const vis = () => { if (!document.hidden) heartbeat(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearTimeout(first); clearInterval(timer); document.removeEventListener('visibilitychange', vis); };
  }, [online, heartbeat, intervalMs, firstCheckMs]);

  useEffect(() => { if (!toast) return undefined; const t = setTimeout(() => setToast(null), toastMs); return () => clearTimeout(t); }, [toast, toastMs]);

  const retry = useCallback(async () => {
    setBusy(true); setNote('');
    const ok = await ping();
    if (!alive.current) return;
    setBusy(false);
    if (ok) { setOnline(true); setToast('net'); fails.current = 0; } else { setNote('Still no signal.'); }
  }, [ping]);

  if (!online) {
    return dismissed
      ? <Bar testid="conn-banner" action={<button className="vcm-link" type="button" onClick={() => setDismissed(false)}>Details</button>}>Offline — showing the last loaded data. Vayu reconnects automatically.</Bar>
      : <OfflineScreen since={since.current} busy={busy} note={note} onRetry={retry} onDismiss={() => setDismissed(true)} />;
  }
  if (serverDown) {
    return (
      <Bar testid="conn-banner" action={<button className="vcm-link" type="button" onClick={heartbeat}>Retry now</button>}>
        Can't reach Vayu's servers — they may be waking up. Retrying automatically…
      </Bar>
    );
  }
  if (toast) return <Bar testid="conn-toast" tone="ok">{toast === 'server' ? 'Vayu servers are back.' : 'Link re-established.'}</Bar>;
  return null;
}
