/**
 * useAlerts — in-browser alerts for the Business tab.
 *
 * Rules, state and history live in localStorage; every 5 minutes (and when the tab becomes visible, and when rules change)
 * the hook fetches the small /intel/alerts-snapshot and evaluates the rules with the pure engine in lib/alertRules.js.
 * Runs at App level so alerts keep working whichever tab you are on, as long as a Vayu tab is open.
 * Server-sent email/WhatsApp alerts are intentionally NOT offered: they need verified addresses (double opt-in) to be safe.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { evaluateRules, normalizeRule, validateRule, symbolsForRules, MAX_RULES } from '../lib/alertRules';

const RULES_KEY = 'vayu.alerts.rules.v1';
const STATE_KEY = 'vayu.alerts.state.v1';
const HISTORY_KEY = 'vayu.alerts.history.v1';
const POLL_MS = 5 * 60 * 1000;
const MAX_HISTORY = 50;

function load(key, fallback) {
  try { const v = JSON.parse(localStorage.getItem(key)); return v ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable: alerts last this session only */ }
}

export function useAlerts(apiUrl, onFire) {
  const [rules, setRules] = useState(() => (Array.isArray(load(RULES_KEY, [])) ? load(RULES_KEY, []) : []).filter((r) => r && !validateRule(r)).map(normalizeRule));
  const [history, setHistory] = useState(() => (Array.isArray(load(HISTORY_KEY, [])) ? load(HISTORY_KEY, []) : []));
  const [unread, setUnread] = useState(0);
  const [status, setStatus] = useState({ lastChecked: null, error: null, waiting: [] });
  const [permission, setPermission] = useState(() => (typeof Notification !== 'undefined' ? Notification.permission : 'unsupported'));

  const stateRef = useRef(load(STATE_KEY, {}));
  const rulesRef = useRef(rules);
  rulesRef.current = rules;
  const onFireRef = useRef(onFire);
  onFireRef.current = onFire;
  const checking = useRef(false);

  useEffect(() => { save(RULES_KEY, rules); }, [rules]);

  const check = useCallback(async () => {
    const active = rulesRef.current.filter((r) => r.enabled !== false);
    if (!active.length || checking.current) return;
    checking.current = true;
    try {
      const syms = symbolsForRules(active);
      const res = await fetch(`${apiUrl}/api/v1/intel/alerts-snapshot${syms.length ? `?symbols=${encodeURIComponent(syms.join(','))}` : ''}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(String(res.status));
      const snap = await res.json();
      const { triggers, waiting, state } = evaluateRules(active, snap, stateRef.current, Date.now());
      stateRef.current = state;
      save(STATE_KEY, state);
      if (triggers.length) {
        setHistory((h) => { const next = [...triggers.map((t) => ({ ...t, id: `${t.ruleId}-${t.at}` })), ...h].slice(0, MAX_HISTORY); save(HISTORY_KEY, next); return next; });
        setUnread((u) => u + triggers.length);
        triggers.forEach((t) => {
          onFireRef.current?.(t);
          if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            try { new Notification('Vayu alert', { body: t.message, tag: t.ruleId }); } catch { /* some browsers only allow notifications from a service worker */ }
          }
        });
      }
      setStatus({ lastChecked: Date.now(), error: null, waiting });
    } catch {
      setStatus((s) => ({ ...s, error: 'Could not check alerts just now; will retry.' }));
    } finally {
      checking.current = false;
    }
  }, [apiUrl]);

  useEffect(() => { check(); }, [rules, check]);
  useEffect(() => {
    const id = setInterval(check, POLL_MS);
    const onVisible = () => { if (document.visibilityState === 'visible') check(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { clearInterval(id); document.removeEventListener('visibilitychange', onVisible); };
  }, [check]);

  /** Returns an error string, or null when the rule was added. */
  const addRule = useCallback((draft) => {
    const err = validateRule(draft);
    if (err) return err;
    if (rulesRef.current.length >= MAX_RULES) return `You can have up to ${MAX_RULES} alerts.`;
    const rule = normalizeRule({ ...draft, id: `r_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}` });
    // Arm silently: if the condition is ALREADY true, do not notify about something the user can see right now.
    stateRef.current = { active: { ...(stateRef.current.active || {}), [rule.id]: true }, lastFired: { ...(stateRef.current.lastFired || {}), [rule.id]: Date.now() } };
    save(STATE_KEY, stateRef.current);
    setRules((rs) => [...rs, rule]);
    return null;
  }, []);
  const removeRule = useCallback((id) => setRules((rs) => rs.filter((r) => r.id !== id)), []);
  const toggleRule = useCallback((id) => setRules((rs) => rs.map((r) => (r.id === id ? { ...r, enabled: r.enabled === false } : r))), []);
  const markRead = useCallback(() => setUnread(0), []);
  const clearHistory = useCallback(() => { setHistory([]); save(HISTORY_KEY, []); }, []);
  const requestPermission = useCallback(async () => {
    if (typeof Notification === 'undefined') return 'unsupported';
    const p = await Notification.requestPermission();
    setPermission(p);
    return p;
  }, []);

  return { rules, addRule, removeRule, toggleRule, history, unread, markRead, clearHistory, status, permission, requestPermission, checkNow: check };
}
