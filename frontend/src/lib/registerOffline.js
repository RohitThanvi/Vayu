/**
 * Registers the offline-fallback service worker (public/sw.js) — only where it is safe and useful:
 *   - production web builds (never `vite dev`, where a worker would only cause confusing stale-cache behaviour),
 *   - never inside the native Capacitor app, which ships its own assets and has no "can't reach the site" case,
 *   - only where service workers exist (https or localhost).
 * Failure is silent by design: the app works exactly the same without it.
 */
export function registerOfflineSupport({ nav = typeof navigator !== 'undefined' ? navigator : undefined, win = typeof window !== 'undefined' ? window : undefined, isProd = false } = {}) {
  if (!isProd || !nav || !win || !('serviceWorker' in nav)) return false;
  try {
    if (win.Capacitor?.isNativePlatform?.()) return false;
  } catch { /* treat a broken Capacitor shim as "not native" */ }
  const register = () => { try { Promise.resolve(nav.serviceWorker.register('/sw.js')).catch(() => {}); } catch { /* ignore */ } };
  if (win.document?.readyState === 'complete') register(); else win.addEventListener('load', register, { once: true });
  return true;
}
