/**
 * lib/api.js — every network call to our own backend goes through
 * apiFetch() or wsUrl() here, not raw fetch()/`new WebSocket()`. That's
 * the whole point of this file: it's the ONE place that attaches the
 * Clerk session token, so no call site can accidentally ship
 * unauthenticated and silently 401 (or, worse, get missed when the auth
 * scheme changes again later).
 *
 * Every protected backend route (see backend/app/main.py's
 * dependencies=[Depends(get_current_user)]) requires this token. Without
 * it every one of these calls gets a 401, by design — the backend fails
 * closed, not open.
 */

export const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000';

/**
 * Clerk mounts a global `window.Clerk` instance once ClerkProvider has
 * loaded. getToken() returns the current session JWT (Clerk transparently
 * refreshes it under the hood — this always returns a fresh one, never a
 * cached stale one), or null if there's no signed-in session.
 */
async function getClerkToken() {
  const clerk = window.Clerk;
  if (!clerk || !clerk.session) return null;
  try {
    return await clerk.session.getToken();
  } catch {
    return null;
  }
}

/**
 * Drop-in replacement for fetch() against OUR backend that attaches
 * `Authorization: Bearer <clerk-token>` automatically. Accepts either a
 * bare path ('/api/v1/intel/events') or an already-built full URL
 * (`${apiUrl}/api/v1/intel/events`, the pattern used throughout this
 * codebase) — full URLs are used as-is, bare paths get API_URL
 * prepended, so every existing `fetch(...)` call site can switch to
 * `apiFetch(...)` with its argument unchanged.
 *
 * Throws (does not silently swallow) if there's no signed-in session,
 * since every caller of this in the app only runs once AppGate has
 * already confirmed <SignedIn> — reaching here signed-out means
 * something upstream is broken and should be loud, not a quiet 401.
 */
export async function apiFetch(urlOrPath, options = {}) {
  const token = await getClerkToken();
  if (!token) {
    throw new Error('apiFetch called with no signed-in Clerk session — this should never happen inside <SignedIn>.');
  }
  const url = /^https?:\/\//.test(urlOrPath) ? urlOrPath : `${API_URL}${urlOrPath}`;
  const headers = {
    ...(options.headers || {}),
    Authorization: `Bearer ${token}`,
  };
  return fetch(url, { ...options, headers });
}

/**
 * Appends the Clerk token as a `?token=` query param to a WebSocket URL
 * (needed because the browser WebSocket API has no way to set a custom
 * Authorization header on the handshake — see intel_endpoints.py's /ws,
 * which reads it from the query string instead for this one route).
 * Accepts either a bare path or an already-built ws(s):// URL.
 */
export async function apiWsUrl(wsUrlOrPath) {
  const token = await getClerkToken();
  if (!token) {
    throw new Error('apiWsUrl called with no signed-in Clerk session.');
  }
  const isFullWsUrl = /^wss?:\/\//.test(wsUrlOrPath);
  let target;
  if (isFullWsUrl) {
    target = new URL(wsUrlOrPath);
  } else {
    const httpUrl = new URL(`${API_URL}${wsUrlOrPath}`);
    const wsProtocol = httpUrl.protocol === 'https:' ? 'wss:' : 'ws:';
    target = new URL(`${wsProtocol}//${httpUrl.host}${httpUrl.pathname}`);
  }
  target.searchParams.set('token', token);
  return target.toString();
}
