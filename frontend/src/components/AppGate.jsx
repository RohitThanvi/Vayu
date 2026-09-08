/**
 * AppGate.jsx
 * Sits in front of the main App: on load, checks localStorage for a
 * session token and verifies it against GET /auth/me. Shows
 * LandingPage until a valid session exists, then mounts the real App
 * — App.jsx itself is completely untouched by this, so none of its
 * existing hooks/effects run (and no wasted requests fire) until the
 * user is actually authenticated.
 */

import { useState, useEffect, useCallback } from 'react';
import App from '../App.jsx';
import LandingPage from './LandingPage.jsx';

const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000';

const TOKEN_KEY = 'vayu_auth_token';

export default function AppGate() {
  const [status, setStatus] = useState('checking');   // 'checking' | 'authed' | 'unauthed'
  const [userEmail, setUserEmail] = useState(null);

  const checkSession = useCallback(async () => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { setStatus('unauthed'); return; }
    try {
      const resp = await fetch(`${API_URL}/api/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
      if (!resp.ok) throw new Error('invalid session');
      const data = await resp.json();
      setUserEmail(data.email);
      setStatus('authed');
    } catch {
      localStorage.removeItem(TOKEN_KEY);
      setStatus('unauthed');
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
      <div style={{ position: 'fixed', inset: 0, background: '#05070c', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ width: 26, height: 26, border: '3px solid #2a3040', borderTopColor: '#c9a86a', borderRadius: '50%', animation: 'vayu-gate-spin 0.8s linear infinite' }} />
        <style>{'@keyframes vayu-gate-spin { to { transform: rotate(360deg); } }'}</style>
      </div>
    );
  }

  if (status === 'unauthed') {
    return <LandingPage apiUrl={API_URL} onAuthenticated={handleAuthenticated} />;
  }

  return <App userEmail={userEmail} onLogout={handleLogout} />;
}
