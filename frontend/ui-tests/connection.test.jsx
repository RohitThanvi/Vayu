import { act } from 'react';
import { test as rawTest, mount as rawMount, flush, click, route, calls, text, q, qa, buttonWith, key, eq, ok, has, lacks } from './harness.jsx';
import ConnectionMonitor, { OfflineScreen } from '../src/components/ConnectionMonitor.jsx';

// The shared harness never unmounts between tests, so components from earlier tests would keep their heartbeat timers alive and pollute
// call counts. Track every mount made by a test here and unmount them all when it ends.
let mounted = [];
const mount = async (el) => { const m = await rawMount(el); mounted.push(m); return m; };
const test = (name, fn) => rawTest(name, async () => {
  mounted = [];
  try { await fn(); } finally { for (const m of mounted) { try { await m.unmount(); } catch { /* already unmounted */ } } mounted = []; }
});

const FAST = { firstCheckMs: 10, intervalMs: 25, timeoutMs: 500, toastMs: 80 };
const health = () => calls.filter((u) => u.endsWith('/health')).length;
const setOnline = (v) => Object.defineProperty(globalThis.navigator, 'onLine', { configurable: true, get: () => v });
const fire = async (type) => { await act(async () => { window.dispatchEvent(new window.Event(type)); }); await flush(5); };
async function withOnline(v, fn) { setOnline(v); try { await fn(); } finally { setOnline(true); } }

export function register() {
  test('Connection monitor: shows nothing while online and the server answers, and does check the server', async () => {
    route('/health', { status: 'ok' });
    const { container: c } = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
    await flush(120);
    eq(c.innerHTML, '', 'no UI at all when everything works'); ok(health() >= 1, 'the heartbeat ran');
  });

  test('Connection monitor: going offline shows the themed No Signal screen with the satnet punchline; nothing is polled while offline', async () => {
    await withOnline(true, async () => {
      route('/health', { status: 'ok' });
      const { container: c } = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
      setOnline(false); await fire('offline');
      const s = q(c, '[data-testid="offline-screen"]');
      ok(s, 'overlay shown'); eq(s.getAttribute('role'), 'dialog'); eq(s.getAttribute('aria-modal'), 'true');
      const t = text(s);
      has(t, 'No Signal'); has(t, 'Downlink interrupted'); has(t, "Seems there's no internet."); has(t, 'Perhaps Vayu needs to enter the satnet industry as well.');
      has(t, 'Retry connection'); has(t, 'Keep viewing the last loaded data'); has(t, 'T+00:0'); has(t, 'Listening for signal');
      ok(q(s, 'svg'), 'satellite artwork present'); eq(document.activeElement && document.activeElement.textContent, 'Retry connection', 'focus moves to the retry button');
      const before = health(); await flush(120);
      eq(health(), before, 'no heartbeat traffic while the browser is offline');
    });
  });

  test('Connection monitor: dismiss -> banner, Details -> screen again, Escape dismisses, back online -> toast that then disappears', async () => {
    await withOnline(true, async () => {
      route('/health', { status: 'ok' });
      const { container: c } = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
      setOnline(false); await fire('offline');
      await click(buttonWith(c, 'Keep viewing the last loaded data'));
      ok(!q(c, '[data-testid="offline-screen"]'), 'overlay gone'); has(text(q(c, '[data-testid="conn-banner"]')), 'Offline — showing the last loaded data');
      await click(buttonWith(c, 'Details')); ok(q(c, '[data-testid="offline-screen"]'), 'Details reopens the screen');
      await key(window, 'Escape'); ok(!q(c, '[data-testid="offline-screen"]') && q(c, '[data-testid="conn-banner"]'), 'Escape dismisses');
      setOnline(true); await fire('online');
      has(text(q(c, '[data-testid="conn-toast"]')), 'Link re-established.'); ok(!q(c, '[data-testid="conn-banner"]'));
      await flush(160); eq(c.innerHTML.includes('conn-toast'), false, 'the toast clears itself');
      setOnline(false); await fire('offline');
      ok(q(c, '[data-testid="offline-screen"]'), 'a second outage shows the screen again, not the dismissed banner');
    });
  });

  test('Connection monitor: Retry works when the server answers even if the browser still says offline; reports "still no signal" otherwise', async () => {
    await withOnline(true, async () => {
      route('/health', { __status: 503 });
      const { container: c } = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
      setOnline(false); await fire('offline');
      await click(buttonWith(c, 'Retry connection')); await flush(40);
      has(text(c), 'Still no signal.'); ok(q(c, '[data-testid="offline-screen"]'), 'screen stays');
      route('/health', { status: 'ok' });
      await click(buttonWith(c, 'Retry connection')); await flush(40);
      ok(!q(c, '[data-testid="offline-screen"]'), 'a successful check clears the screen'); has(text(c), 'Link re-established.');
    });
  });

  test('Connection monitor: one failed heartbeat is not an outage; two in a row show a slim banner (not the full screen); recovery shows a toast', async () => {
    route('/health', { __status: 503 });
    const once = await mount(<ConnectionMonitor apiUrl="" firstCheckMs={10} intervalMs={100000} timeoutMs={500} toastMs={80} />);
    await flush(70); eq(once.container.innerHTML, '', 'a single miss is ignored'); await once.unmount();

    const { container: c } = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
    await flush(150);
    const b = q(c, '[data-testid="conn-banner"]');
    ok(b, 'banner after repeated failures'); has(text(b), "Can't reach Vayu's servers"); has(text(b), 'waking up'); ok(!q(c, '[data-testid="offline-screen"]'), 'not a full-screen takeover: the site itself still works');
    route('/health', { status: 'ok' });
    await click(buttonWith(c, 'Retry now')); await flush(40);
    ok(!q(c, '[data-testid="conn-banner"]'), 'banner clears once the server answers'); has(text(c), 'Vayu servers are back.');
  });

  test('Connection monitor: a thrown network error counts as a failed heartbeat, and heartbeats stop after unmount', async () => {
    route('/health', new Error('Failed to fetch'));
    const m = await mount(<ConnectionMonitor apiUrl="" {...FAST} />);
    await flush(150); ok(q(m.container, '[data-testid="conn-banner"]'), 'network errors trigger the banner');
    await m.unmount();
    const n = health(); await flush(120);
    eq(health(), n, 'no heartbeats after unmount');
  });

  test('Offline screen: punchlines rotate, honouring the first one by default; clock starts at zero', async () => {
    const m = await mount(<OfflineScreen since={Date.now()} busy={false} note="" onRetry={() => {}} onDismiss={() => {}} />);
    has(text(m.container), 'T+00:00'); eq(text(q(m.container, '[data-testid="punchline"]')), 'Perhaps Vayu needs to enter the satnet industry as well.');
    const busy = await mount(<OfflineScreen since={Date.now()} busy note="" onRetry={() => {}} onDismiss={() => {}} />);
    has(text(busy.container), 'Checking for signal…'); ok(buttonWith(busy.container, 'Retry connection').disabled, 'the retry button is disabled while a check runs');
  });
}
