/**
 * SubscribeWidget.jsx
 * Compact, always-visible sidebar-footer widget for the daily executive
 * summary email (see backend/app/services/reporting/). Collapsed to a
 * single line by default; clicking it reveals the email input inline
 * rather than opening a modal — this is a one-field form, a modal would
 * be more interruption than the action deserves.
 */

import { useState } from 'react';

const S = {
  surface2: '#0f1419', border: '#2a3040',
  text2: 'rgba(255,255,255,0.8)', text3: 'rgba(255,255,255,0.6)',
  accent: '#7eb8d4', mono: "'JetBrains Mono','Courier New',monospace",
};

export default function SubscribeWidget({ apiUrl }) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [status, setStatus] = useState('idle');   // idle | loading | done | error
  const [message, setMessage] = useState('');

  const submit = async () => {
    if (!email.trim()) return;
    setStatus('loading');
    try {
      const resp = await fetch(`${apiUrl}/api/v1/subscribe`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: email.trim() }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || 'Subscribe failed');
      setStatus('done');
      setMessage(data.message);
    } catch (e) {
      setStatus('error');
      setMessage(e.message);
    }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        style={{ display:'flex', alignItems:'center', gap:6, background:'none', border:'none', color:S.accent, fontFamily:S.mono, fontSize:12, letterSpacing:0.5, cursor:'pointer', padding:0 }}>
        <span style={{ width:5, height:5, borderRadius:'50%', background:S.accent, display:'inline-block' }} />
        Daily Executive Briefing — Subscribe
      </button>
    );
  }

  if (status === 'done') {
    return <div style={{ fontSize:12, color:'#4a7c59', fontFamily:S.mono }}>{message}</div>;
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
      <div style={{ fontSize:11, color:S.text3, fontFamily:S.mono, letterSpacing:0.5 }}>
        Daily 12PM executive briefing — email only
      </div>
      <div style={{ display:'flex', gap:6 }}>
        <input
          type="email" value={email} placeholder="you@company.com"
          onChange={e => setEmail(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          style={{ flex:1, minWidth:0, background:S.surface2, border:`1px solid ${S.border}`, borderRadius:3, color:S.text2, fontFamily:S.mono, fontSize:12, padding:'7px 8px' }}
        />
        <button onClick={submit} disabled={status === 'loading'}
          style={{ background:'rgba(126,184,212,0.12)', border:`1px solid ${S.accent}`, borderRadius:3, color:S.accent, fontFamily:S.mono, fontSize:11, letterSpacing:1, padding:'0 12px', cursor: status==='loading' ? 'wait' : 'pointer', flexShrink:0 }}>
          {status === 'loading' ? '...' : 'GO'}
        </button>
      </div>
      {status === 'error' && <div style={{ fontSize:11, color:'#c96a3a', fontFamily:S.mono }}>{message}</div>}
    </div>
  );
}
