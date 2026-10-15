/** Brief on-screen notices for alerts that just fired (works even where desktop notifications are blocked). */
import { useEffect } from 'react';

export default function AlertToasts({ toasts, onDismiss }) {
  useEffect(() => {
    if (!toasts.length) return undefined;
    const timers = toasts.map((t) => setTimeout(() => onDismiss(t.id), 12000));
    return () => timers.forEach(clearTimeout);
  }, [toasts, onDismiss]);
  if (!toasts.length) return null;
  return (
    <div role="status" aria-live="polite" style={{ position: 'fixed', right: 16, bottom: 16, zIndex: 5000, display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 360 }}>
      {toasts.map((t) => (
        <div key={t.id} style={{ background: 'var(--vayu-surface)', border: '1px solid #e0c23c', borderRadius: 4, padding: '9px 12px', boxShadow: '0 4px 18px rgba(0,0,0,0.35)', display: 'flex', gap: 10, alignItems: 'flex-start' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 9.5, color: '#e0c23c', letterSpacing: 1, textTransform: 'uppercase', fontFamily: "'JetBrains Mono',monospace" }}>Vayu alert</div>
            <div style={{ fontSize: 12, color: 'var(--vayu-text)', lineHeight: 1.4 }}>{t.message}</div>
          </div>
          <button onClick={() => onDismiss(t.id)} aria-label="Dismiss alert" style={{ background: 'none', border: 'none', color: 'var(--vayu-text3)', cursor: 'pointer', fontSize: 15 }}>×</button>
        </div>
      ))}
    </div>
  );
}
