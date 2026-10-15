/**
 * Pure alert-rule engine (no React, no network) so it can be tested in plain Node.
 *
 * Alerts run in the BROWSER, against the small /intel/alerts-snapshot the server publishes. That is deliberate: server-sent
 * email/WhatsApp alerts would need verified addresses (double opt-in), otherwise anyone could point alerts at someone else's
 * inbox. In-browser alerts have no such abuse path, cost the server almost nothing, and work while a Vayu tab is open.
 *
 * Rules fire on an EDGE (the condition becomes true), then stay quiet until it clears or the cooldown passes, so a price that
 * sits above a level does not notify every five minutes. A rule never fires on stale or missing data: no data is not a signal.
 */

export const SYMBOL_RE = /^[A-Za-z0-9.^=-]{1,15}$/;
export const BAND_RANK = { normal: 0, elevated: 1, critical: 2 };   // matches business_risk bands
export const SEVERITY_RANK = { info: 0, warn: 1, critical: 2 };
export const DEFAULT_COOLDOWN_MIN = 360;
export const MAX_RULES = 20;

export const RULE_TYPES = {
  price_above: { label: 'Price rises above', needs: ['symbol', 'level'] },
  price_below: { label: 'Price falls below', needs: ['symbol', 'level'] },
  day_move: { label: 'Day move of at least (%)', needs: ['symbol', 'pct'] },
  chokepoint_band: { label: 'Chokepoint risk reaches', needs: ['chokepoint', 'band'] },
  site_signal: { label: 'Signal detected near a strategic site', needs: ['siteId'] },
};

export function validateRule(rule) {
  const t = RULE_TYPES[rule?.type];
  if (!t) return 'Unknown alert type.';
  if (t.needs.includes('symbol') && !SYMBOL_RE.test(String(rule.symbol || '').trim())) return 'Enter a valid symbol (letters, digits and . ^ = - only).';
  if (t.needs.includes('level') && !Number.isFinite(Number(rule.level))) return 'Enter a numeric price level.';
  if (t.needs.includes('pct') && !(Number(rule.pct) > 0 && Number(rule.pct) <= 100)) return 'Enter a percentage between 0 and 100.';
  if (t.needs.includes('chokepoint') && !rule.chokepoint) return 'Choose a chokepoint.';
  if (t.needs.includes('band') && !(rule.band in BAND_RANK) || (rule.type === 'chokepoint_band' && rule.band === 'normal')) return 'Choose "elevated" or "critical".';
  if (t.needs.includes('siteId') && !rule.siteId) return 'Choose a site.';
  return null;
}

export function normalizeRule(rule) {
  return {
    id: rule.id, type: rule.type, enabled: rule.enabled !== false, createdAt: rule.createdAt || Date.now(),
    cooldownMin: Number.isFinite(Number(rule.cooldownMin)) ? Number(rule.cooldownMin) : DEFAULT_COOLDOWN_MIN,
    ...(rule.symbol != null ? { symbol: String(rule.symbol).trim().toUpperCase() } : {}),
    ...(rule.level != null ? { level: Number(rule.level) } : {}),
    ...(rule.pct != null ? { pct: Number(rule.pct) } : {}),
    ...(rule.chokepoint ? { chokepoint: rule.chokepoint } : {}),
    ...(rule.band ? { band: rule.band } : {}),
    ...(rule.siteId ? { siteId: rule.siteId } : {}),
    ...(rule.siteName ? { siteName: rule.siteName } : {}),
  };
}

export function describeRule(rule, names = {}) {
  switch (rule.type) {
    case 'price_above': return `${rule.symbol} rises above ${rule.level}`;
    case 'price_below': return `${rule.symbol} falls below ${rule.level}`;
    case 'day_move': return `${rule.symbol} moves at least ${rule.pct}% in a day`;
    case 'chokepoint_band': return `${names[rule.chokepoint] || rule.chokepoint} risk reaches ${rule.band}`;
    case 'site_signal': return `A signal is detected near ${rule.siteName || names[rule.siteId] || rule.siteId}`;
    default: return rule.type;
  }
}

/** Is the rule's condition true right now? -> { state: 'true'|'false'|'nodata', detail } */
export function checkCondition(rule, snap) {
  const q = rule.symbol ? snap?.quotes?.[rule.symbol] : null;
  switch (rule.type) {
    case 'price_above':
    case 'price_below':
    case 'day_move': {
      if (!q || q.stale || !Number.isFinite(q.value)) return { state: 'nodata' };
      if (rule.type === 'day_move') {
        if (!Number.isFinite(q.change_pct)) return { state: 'nodata' };
        return { state: Math.abs(q.change_pct) >= rule.pct ? 'true' : 'false', detail: `${q.change_pct > 0 ? '+' : ''}${q.change_pct}% today (${q.value})` };
      }
      const hit = rule.type === 'price_above' ? q.value > rule.level : q.value < rule.level;
      return { state: hit ? 'true' : 'false', detail: `now ${q.value}` };
    }
    case 'chokepoint_band': {
      const r = snap?.risk?.[rule.chokepoint];
      if (!r) return { state: 'nodata' };
      return { state: (BAND_RANK[r.band] ?? -1) >= BAND_RANK[rule.band] ? 'true' : 'false', detail: `${r.display || rule.chokepoint}: ${r.band}, score ${r.score}` };
    }
    case 'site_signal': {
      if (!snap?.sites || !snap?.as_of) return { state: 'nodata' };
      const s = snap.sites[rule.siteId];
      if (!s || !s.signals?.length) return { state: 'false' };
      const worst = s.signals.reduce((a, b) => ((SEVERITY_RANK[b.severity] ?? 0) > (SEVERITY_RANK[a.severity] ?? 0) ? b : a));
      return { state: 'true', detail: `${s.name}: ${worst.note}` };
    }
    default: return { state: 'nodata' };
  }
}

/**
 * Evaluate every enabled rule. `state` = { active: {ruleId: bool}, lastFired: {ruleId: ms} } (persist it between calls).
 * Returns { triggers, waiting, state }: `waiting` are rules that could not be evaluated for lack of fresh data.
 */
export function evaluateRules(rules, snap, state = {}, now = Date.now()) {
  const active = { ...(state.active || {}) };
  const lastFired = { ...(state.lastFired || {}) };
  const triggers = [];
  const waiting = [];
  for (const rule of rules || []) {
    if (rule.enabled === false) continue;
    const c = checkCondition(rule, snap);
    if (c.state === 'nodata') { waiting.push(rule.id); continue; }
    const wasActive = !!active[rule.id];
    const cooled = !lastFired[rule.id] || now - lastFired[rule.id] >= (rule.cooldownMin ?? DEFAULT_COOLDOWN_MIN) * 60000;
    if (c.state === 'true') {
      if (!wasActive || cooled) {
        triggers.push({ ruleId: rule.id, at: now, message: `${describeRule(rule)} — ${c.detail}` });
        lastFired[rule.id] = now;
      }
      active[rule.id] = true;
    } else {
      active[rule.id] = false;
    }
  }
  return { triggers, waiting, state: { active, lastFired } };
}

/** Symbols the server should include in the snapshot for these rules. */
export function symbolsForRules(rules) {
  return [...new Set((rules || []).filter((r) => r.enabled !== false && r.symbol).map((r) => r.symbol))];
}
