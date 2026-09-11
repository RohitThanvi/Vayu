/**
 * AppGate.jsx
 *
 * MVP PIVOT: was "check a login session, show LandingPage until
 * authenticated". Now: no accounts — LandingPage's 'Enter Terminal'
 * opens a three-option picker (Business / Agri / Full), the choice is
 * stored in localStorage, and App.jsx renders the panels for that tier.
 * The original session-check flow is commented out below, not deleted
 * — see the matching banner comments in LandingPage.jsx and
 * auth_endpoints.py for how to restore all three together.
 */

import { useState } from 'react';
import App from '../App.jsx';
import LandingPage from './LandingPage.jsx';

const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000';

const TIER_KEY = 'vayu_service_tier';

export default function AppGate() {
  const [tier, setTier] = useState(() => localStorage.getItem(TIER_KEY));

  const handleSelectTier = (selected) => {
    localStorage.setItem(TIER_KEY, selected);
    setTier(selected);
  };

  const handleChangeTier = () => {
    localStorage.removeItem(TIER_KEY);
    setTier(null);
  };

  if (!tier) {
    return <LandingPage apiUrl={API_URL} onSelectTier={handleSelectTier} />;
  }

  return <App tier={tier} onChangeTier={handleChangeTier} />;
}

// ============================================================================
// ORIGINAL SESSION-BASED AUTH FLOW — DISABLED (commented out, not deleted).
// To restore: swap the AppGate body above for this one, revert
// LandingPage's onSelectTier prop back to onAuthenticated, and uncomment
// the account endpoints in backend/app/api/auth_endpoints.py.
// ============================================================================
/*
const TOKEN_KEY = 'vayu_auth_token';
// Render's free tier cold-sleeps the backend after inactivity and can
// take 30-50s to wake on the next request (same behavior this project
// has hit elsewhere, e.g. the AIS bridge). The session check on every
// page load/refresh previously had NO timeout at all, so refreshing
// while the backend was asleep just hung forever with a bare spinner —
// this is what "got stuck on refresh" was. 45s covers a real cold
// start; a "waking up" message appears after 4s so a normal slow
// network doesn't look broken while it's actually just working.
const SESSION_CHECK_TIMEOUT_MS = 45000;
const SLOW_HINT_DELAY_MS = 4000;

function LegacyAppGate() {
  const [status, setStatus] = useState('checking');   // 'checking' | 'authed' | 'unauthed' | 'unreachable'
  const [userEmail, setUserEmail] = useState(null);
  const [showSlowHint, setShowSlowHint] = useState(false);

  const checkSession = useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { setStatus('unauthed'); return; }

    setShowSlowHint(false);
    const slowHintTimer = setTimeout(() => setShowSlowHint(true), SLOW_HINT_DELAY_MS);
    const controller = new AbortController();
    const abortTimer = setTimeout(() => controller.abort(), SESSION_CHECK_TIMEOUT_MS);

    try {
      const resp = await fetch(`${API_URL}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });
      if (resp.status === 401) {
        // A REAL invalid/expired session — this is the only case that
        // should clear the token and send the user back to login.
        localStorage.removeItem(TOKEN_KEY);
        setStatus('unauthed');
        return;
      }
      if (!resp.ok) throw new Error(`unexpected status ${resp.status}`);
      const data = await resp.json();
      setUserEmail(data.email);
      setStatus('authed');
    } catch {
      // Network failure, timeout, or a non-401 error — the session
      // token itself might still be perfectly valid, the backend was
      // just unreachable/slow. Do NOT clear it or force a re-login;
      // show a retry state instead.
      setStatus('unreachable');
    } finally {
      clearTimeout(slowHintTimer);
      clearTimeout(abortTimer);
    }
  }, []);

  useEffect(() => { checkSession(); }, [checkSession]);

  const handleAuthenticated = (token, email) => {
    localStorage.setItem(TOKEN_KEY, token);
    setUserEmail(email);
    setStatus('authed');
  };

  const handleLogout = () => {
    const token = localStorage.getItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    setStatus('unauthed');
    setUserEmail(null);
    if (token) {
      fetch(`${API_URL}/api/v1/auth/logout`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
    }
  };

  if (status === 'checking') {
    return (
      <div style={{ position: 'fixed', inset: 0, background: '#05070c', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14 }}>
        <div style={{ width: 26, height: 26, border: '3px solid #2a3040', borderTopColor: '#c9a86a', borderRadius: '50%', animation: 'vayu-gate-spin 0.8s linear infinite' }} />
        {showSlowHint && (
          <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 12, color: 'rgba(255,255,255,0.5)', letterSpacing: 0.5 }}>
            Waking up the server — this can take up to a minute on first load...
          </div>
        )}
        <style>{'@keyframes vayu-gate-spin { to { transform: rotate(360deg); } }'}</style>
      </div>
    );
  }

  if (status === 'unreachable') {
    return (
      <div style={{ position: 'fixed', inset: 0, background: '#05070c', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, padding: 20, textAlign: 'center' }}>
        <div style={{ fontFamily: "'JetBrains Mono',monospace", fontSize: 13, color: 'rgba(255,255,255,0.7)' }}>
          Couldn't reach the server. Your session is still saved — this is likely just a slow connection.
        </div>
        <button onClick={checkSession}
          style={{ padding: '9px 20px', fontFamily: "'JetBrains Mono',monospace", fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase', background: 'rgba(126,184,212,0.12)', border: '1px solid #7eb8d4', borderRadius: 4, color: '#7eb8d4', cursor: 'pointer' }}>
          Retry
        </button>
      </div>
    );
  }

  if (status === 'unauthed') {
    return <LandingPage apiUrl={API_URL} onAuthenticated={handleAuthenticated} />;
  }

  return <App userEmail={userEmail} onLogout={handleLogout} />;
}
*/
