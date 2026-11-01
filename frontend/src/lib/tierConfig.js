/**
 * tierConfig.js — build-time "locked tier" support for single-product demo sites.
 *
 * The main site (vayu-geop.vercel.app) leaves VITE_LOCKED_TIER unset and keeps the
 * full landing page + tier picker. A separate Vercel project built from the SAME repo
 * can set e.g. VITE_LOCKED_TIER=agri: that site then shows only that product's landing
 * page and app, with no picker and no way to switch tier.
 *
 * Note: this hides the other tiers' UI; their code is still in the JS bundle. It is a
 * presentation lock for demos, not a security boundary.
 */

export const KNOWN_TIERS = ['business', 'agri', 'remote_sensing', 'full'];

/** Returns a valid tier id, or null for unset / unknown values (never throws). */
export function parseLockedTier(raw) {
  if (typeof raw !== 'string') return null;
  const v = raw.trim().toLowerCase();
  return KNOWN_TIERS.includes(v) ? v : null;
}

export function getLockedTier() {
  // optional chaining: import.meta.env is undefined under the node-based UI test bundle.
  return parseLockedTier(import.meta.env?.VITE_LOCKED_TIER);
}

/**
 * Which tier the app should start in.
 * A locked site ignores whatever is in localStorage; the open site restores the saved pick.
 */
export function initialTier(locked, stored) {
  if (locked) return locked;
  return parseLockedTier(stored) || null;
}
