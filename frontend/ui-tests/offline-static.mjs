/**
 * Tests for the pieces that run OUTSIDE React: public/sw.js (the offline fallback worker), public/offline.html (the standalone page) and
 * src/lib/registerOffline.js. Plain Node: sw.js runs inside a vm sandbox with faked service-worker globals; offline.html runs in jsdom.
 * Run via `npm run test:ui`.
 */
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';
import { registerOfflineSupport } from '../src/lib/registerOffline.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const pub = path.join(here, '..', 'public');
const SW = fs.readFileSync(path.join(pub, 'sw.js'), 'utf8');
const HTML = fs.readFileSync(path.join(pub, 'offline.html'), 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const test = (name, fn) => results.push([name, fn]);

// ── a fake service-worker world ──────────────────────────────────────────────
class FakeRequest { constructor(url, init = {}) { this.url = new URL(typeof url === 'string' ? url : url.url, 'https://vayu.test').href; this.method = init.method || 'GET'; this.mode = init.mode || 'cors'; this.cache = init.cache; } }
class FakeResponse { constructor(body = '', init = {}) { this.body = body; this.status = init.status ?? 200; this.headers = init.headers || {}; } }
function world({ network = () => new FakeResponse('net'), failAdd = [] } = {}) {
  const store = new Map();                                  // cacheName -> Map(pathname -> response)
  const listeners = {}, timers = [], state = { skip: 0, claimed: 0, fetched: [] };
  const fetchFn = (req) => { state.fetched.push(typeof req === 'string' ? req : req.url); return Promise.resolve().then(() => network(req)); };
  const caches = {
    open: async (name) => { if (!store.has(name)) store.set(name, new Map()); const m = store.get(name);
      return { add: async (req) => { const p = new URL(req.url).pathname; if (failAdd.includes(p)) throw new Error('add failed ' + p); const r = await fetchFn(req); if (r.status >= 400) throw new Error('bad ' + r.status); m.set(p, r); } }; },
    keys: async () => [...store.keys()], delete: async (k) => store.delete(k),
    match: async (req) => { const p = new URL(typeof req === 'string' ? req : req.url, 'https://vayu.test').pathname; for (const m of store.values()) if (m.has(p)) return m.get(p); return undefined; },
  };
  const self = { addEventListener: (t, fn) => { listeners[t] = fn; }, location: { origin: 'https://vayu.test' }, skipWaiting: async () => { state.skip += 1; }, clients: { claim: async () => { state.claimed += 1; } } };
  const sandbox = { self, caches, Request: FakeRequest, Response: FakeResponse, fetch: fetchFn, URL, Promise, Error, Array, setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {} };
  vm.runInNewContext(SW, sandbox);
  const ev = (props) => { const e = { ...props, waited: null, responded: null, waitUntil(p) { e.waited = p; }, respondWith(p) { e.responded = p; } }; return e; };
  return { listeners, store, timers, state, ev, setNetwork: (fn) => { network = fn; } };
}
const nav = (url = 'https://vayu.test/x', extra = {}) => ({ request: new FakeRequest(url, { mode: 'navigate', ...extra }) });

// ── sw.js ────────────────────────────────────────────────────────────────────
test('sw install: caches the offline page and fonts, tolerates a missing optional asset, activates immediately', async () => {
  const w = world({ failAdd: ['/favicon.ico'] });
  const e = w.ev({}); w.listeners.install(e); await e.waited;
  const cached = [...w.store.get('vayu-offline-v1').keys()].sort();
  assert.ok(cached.includes('/offline.html')); assert.equal(cached.filter((p) => p.startsWith('/fonts/')).length, 4); assert.ok(!cached.includes('/favicon.ico'));
  assert.equal(w.state.skip, 1);
});
test('sw install: fails (to be retried next visit) if the offline page itself cannot be cached', async () => {
  const w = world({ network: () => new FakeResponse('', { status: 500 }) });
  const e = w.ev({}); w.listeners.install(e);
  await assert.rejects(e.waited);
});
test('sw activate: removes old vayu-offline caches only, then claims clients', async () => {
  const w = world();
  w.store.set('vayu-offline-v0', new Map()); w.store.set('vayu-offline-v1', new Map()); w.store.set('someone-elses-cache', new Map());
  const e = w.ev({}); w.listeners.activate(e); await e.waited;
  assert.deepEqual([...w.store.keys()].sort(), ['someone-elses-cache', 'vayu-offline-v1']); assert.equal(w.state.claimed, 1);
});
async function primed(opts) { const w = world(opts); const e = w.ev({}); w.listeners.install(e); await e.waited; return w; }
test('sw fetch: ignores everything except same-origin GET navigations and /fonts', async () => {
  const w = await primed();
  for (const req of [new FakeRequest('https://vayu.test/assets/app.js'), new FakeRequest('https://vayu.test/api/v1/intel/feed'), new FakeRequest('https://tiles.example/a.png'),
                     new FakeRequest('https://other.example/page', { mode: 'navigate' }), new FakeRequest('https://vayu.test/x', { mode: 'navigate', method: 'POST' })]) {
    const e = w.ev({ request: req }); w.listeners.fetch(e);
    assert.equal(e.responded, null, `must not intercept ${req.method} ${req.url} (${req.mode})`);
  }
});
test('sw fetch: a working navigation passes through untouched — including a 404 (so your 404 page still shows)', async () => {
  const ok = new FakeResponse('page'), nf = new FakeResponse('nf', { status: 404 });
  const w = await primed();
  w.setNetwork(() => ok); let e = w.ev(nav()); w.listeners.fetch(e); assert.equal(await e.responded, ok);
  w.setNetwork(() => nf); e = w.ev(nav('https://vayu.test/missing')); w.listeners.fetch(e); assert.equal(await e.responded, nf);
});
test('sw fetch: a network failure on navigation serves the cached offline page', async () => {
  const w = await primed();
  w.setNetwork(() => { throw new Error('ERR_INTERNET_DISCONNECTED'); });
  const e = w.ev(nav('https://vayu.test/deep/link')); w.listeners.fetch(e);
  const r = await e.responded; assert.equal(r.body, 'net'); assert.equal(r.status, 200);        // 'net' = what the (faked) network returned for /offline.html at install
  assert.equal(w.store.get('vayu-offline-v1').get('/offline.html'), r);
});
test('sw fetch: no cached offline page yet -> a plain 503 text, never a crash', async () => {
  const w = world(); w.setNetwork(() => { throw new Error('down'); });
  const e = w.ev(nav()); w.listeners.fetch(e); const r = await e.responded;
  assert.equal(r.status, 503); assert.match(r.body, /offline/i);
});
test('sw fetch: 502/503/504 from the host show the offline page; with nothing cached the host response is kept', async () => {
  const w = await primed();
  for (const status of [502, 503, 504]) { w.setNetwork(() => new FakeResponse('bad gateway', { status })); const e = w.ev(nav()); w.listeners.fetch(e); assert.equal((await e.responded).body, 'net', `status ${status}`); }
  w.setNetwork(() => new FakeResponse('server error', { status: 500 })); const e500 = w.ev(nav()); w.listeners.fetch(e500); assert.equal((await e500.responded).body, 'server error', '500 is a real app error: pass through');
  const bare = world(); bare.setNetwork(() => new FakeResponse('bad gateway', { status: 502 })); const eb = bare.ev(nav()); bare.listeners.fetch(eb);
  assert.equal((await eb.responded).body, 'bad gateway');
});
test('sw fetch: a navigation that hangs for 15 s falls back to the offline page', async () => {
  const w = await primed();
  w.setNetwork(() => new Promise(() => {}));                     // never settles: a dead connection
  const e = w.ev(nav()); w.listeners.fetch(e);
  await sleep(5);
  const t = w.timers.find((x) => x.ms === 15000); assert.ok(t, 'a 15 s timeout was armed'); t.fn();
  assert.equal((await e.responded).body, 'net');
});
test('sw fetch: /fonts files are served cache-first, falling back to the network', async () => {
  const w = await primed();
  w.state.fetched.length = 0;
  let e = w.ev({ request: new FakeRequest('https://vayu.test/fonts/orbitron-latin-900-normal.woff2') }); w.listeners.fetch(e); await e.responded;
  assert.equal(w.state.fetched.length, 0, 'served from cache with no network');
  e = w.ev({ request: new FakeRequest('https://vayu.test/fonts/not-cached.woff2') }); w.listeners.fetch(e);
  assert.equal(e.responded, null, 'only the four known font files are handled');
});
test('sw is self-consistent with the files on disk: every font it precaches exists, and offline.html uses exactly those', () => {
  const fonts = [...SW.matchAll(/'(\/fonts\/[^']+\.woff2)'/g)].map((m) => m[1]);
  assert.equal(fonts.length, 4);
  for (const f of fonts) assert.ok(fs.existsSync(path.join(pub, f)), `${f} exists`);
  const used = [...HTML.matchAll(/url\('(\/fonts\/[^']+)'\)/g)].map((m) => m[1]);
  assert.deepEqual([...new Set(used)].sort(), [...fonts].sort(), 'the page uses the fonts the worker caches');
  assert.ok(fs.existsSync(path.join(pub, 'offline.html')) && SW.includes("'/offline.html'"));
  for (const l of ['OFL', 'Orbitron', 'Rajdhani']) assert.ok(fs.readdirSync(path.join(pub, 'fonts')).some((n) => n.includes(l) || l === 'OFL' && /LICENSE/.test(n)), `font licence kept (${l})`);
});

// ── offline.html: content and self-containment ───────────────────────────────
test('offline.html: carries the copy, including the satnet punchline, and works without scripts', () => {
  const dom = new JSDOM(HTML);
  const t = dom.window.document.body.textContent.replace(/\s+/g, ' ');
  for (const s of ['No Signal', 'Downlink interrupted', "Seems there's no internet.", 'Perhaps Vayu needs to enter the satnet industry as well.', 'Retry connection', 'Vayu itself may be unreachable']) assert.ok(t.includes(s), s);
  assert.match(dom.window.document.title, /No Signal/); assert.equal(dom.window.document.documentElement.lang, 'en');
  assert.ok(dom.window.document.querySelector('meta[name="robots"][content="noindex"]'));
});
test('offline.html: fully self-contained — no CDN, no external font/script/image, only root-relative local files', () => {
  assert.ok(!/<script[^>]+src=/i.test(HTML), 'no external scripts'); assert.ok(!/@import/i.test(HTML)); assert.ok(!/<link[^>]+rel=["']?stylesheet/i.test(HTML), 'no linked stylesheets');
  assert.ok(!/(?:src|href)=["'](?:https?:)?\/\//i.test(HTML), 'no absolute/remote src or href');
  assert.ok(!/(googleapis|gstatic|unpkg|cdnjs|jsdelivr|githubusercontent)/i.test(HTML), 'no CDN hosts mentioned');
  for (const m of HTML.matchAll(/url\(([^)]+)\)/g)) assert.ok(/^'?\/fonts\//.test(m[1]) || /^#/.test(m[1]) || /^'?data:/.test(m[1]), `url(${m[1]}) is local`);
  for (const m of HTML.matchAll(/(?:src|href)=["']([^"']+)["']/g)) assert.ok(m[1].startsWith('/') && !m[1].startsWith('//'), `link ${m[1]} is root-relative (the page is served at any URL)`);
});

// ── offline.html: behaviour in jsdom ─────────────────────────────────────────
function page({ reduced = false } = {}) {
  const vc = new VirtualConsole();                              // jsdom has no canvas; the page must cope silently
  const fetched = [], intervals = [];
  const dom = new JSDOM(HTML, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/deep/link?x=1', virtualConsole: vc,
    beforeParse(w) {
      w.__reloads = 0; w.__vayuReload = () => { w.__reloads += 1; }; w.__mode = 'fail';
      w.fetch = (url, opts) => { fetched.push({ url: String(url), opts }); if (w.__mode === 'fail') return Promise.reject(new TypeError('Failed to fetch')); return Promise.resolve({ ok: w.__mode === 'ok', status: w.__mode === 'ok' ? 200 : 503 }); };
      w.matchMedia = () => ({ matches: reduced });
      const si = w.setInterval.bind(w); w.setInterval = (fn, ms) => { intervals.push({ fn, ms }); return si(fn, ms); };
    } });
  return { w: dom.window, d: dom.window.document, fetched, intervals, close: () => dom.window.close() };
}
test('offline.html: Retry probes a small same-origin file with no cache, failures keep it waiting and re-enable the button', async () => {
  const p = page();
  try {
    assert.equal(p.d.getElementById('status').textContent, 'Listening for signal…'); assert.equal(p.d.getElementById('link').textContent, 'Offline');
    p.d.getElementById('retry').click(); await sleep(30);
    assert.equal(p.fetched.length, 1); assert.match(p.fetched[0].url, /^\/manifest\.webmanifest\?probe=\d+$/); assert.equal(p.fetched[0].opts.cache, 'no-store');
    assert.match(p.d.getElementById('status').textContent, /Still no signal · next check in \d+s · attempt 2/); assert.equal(p.d.getElementById('retry').disabled, false);
    p.w.__mode = 'bad'; p.d.getElementById('retry').click(); await sleep(30);                         // reachable but 503: still "no signal"
    assert.equal(p.w.__reloads, 0); assert.match(p.d.getElementById('status').textContent, /attempt 3/);
  } finally { p.close(); }
});
test('offline.html: signal returns (browser "online" event) -> says so, then reloads the original URL once', async () => {
  const p = page();
  try {
    p.w.__mode = 'ok'; p.w.dispatchEvent(new p.w.Event('online')); await sleep(40);
    assert.equal(p.d.getElementById('link').textContent, 'Re-established'); assert.match(p.d.getElementById('status').textContent, /Signal acquired/); assert.equal(p.d.getElementById('dot').className, 'dot ok');
    assert.equal(p.w.__reloads, 0, 'not reloaded instantly');
    await sleep(800); assert.equal(p.w.__reloads, 1, 'reloaded exactly once');
    p.d.getElementById('retry').click(); p.w.dispatchEvent(new p.w.Event('online')); await sleep(30);
    assert.equal(p.fetched.length, 1, 'no further probes after reconnecting');
  } finally { p.close(); }
});
test('offline.html: punchlines rotate through different lines, starting with the satnet one', async () => {
  const p = page();
  try {
    const rot = p.intervals.find((i) => i.ms === 6500); assert.ok(rot, 'a rotation timer exists');
    const el = p.d.getElementById('punch'); const seen = [el.textContent];
    for (let i = 0; i < 3; i++) { rot.fn(); await sleep(700); seen.push(el.textContent); }
    assert.match(seen[0], /satnet industry/); assert.equal(new Set(seen).size, 4, 'each rotation shows a new line'); assert.ok(seen.every((s) => s.length > 10));
    assert.ok(!el.classList.contains('fade'), 'fade class removed after the swap');
  } finally { p.close(); }
});
test('offline.html: with reduced motion the punchline never rotates and the sky is drawn once', async () => {
  const p = page({ reduced: true });
  try { assert.ok(!p.intervals.some((i) => i.ms === 6500), 'no rotation timer'); assert.match(p.d.getElementById('punch').textContent, /satnet industry/); } finally { p.close(); }
});
test('offline.html: the elapsed clock counts up', async () => {
  const p = page();
  try { await sleep(1300); assert.match(p.d.getElementById('elapsed').textContent, /^00:0[1-9]$/); } finally { p.close(); }
});

// ── registerOffline.js ───────────────────────────────────────────────────────
function env({ sw = true, ready = 'complete', cap, regImpl } = {}) {
  const reg = []; const listeners = {};
  const nav = sw ? { serviceWorker: { register: (u) => { reg.push(u); return regImpl ? regImpl() : Promise.resolve({}); } } } : {};
  const win = { Capacitor: cap, document: { readyState: ready }, addEventListener: (t, fn) => { listeners[t] = fn; } };
  return { nav, win, reg, listeners };
}
test('registerOffline: registers /sw.js only in production web builds that support service workers', () => {
  let e = env(); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }), true); assert.deepEqual(e.reg, ['/sw.js']);
  e = env(); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: false }), false); assert.deepEqual(e.reg, [], 'never in dev');
  e = env({ sw: false }); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }), false);
  assert.equal(registerOfflineSupport({ nav: undefined, win: undefined, isProd: true }), false);
});
test('registerOffline: never inside the native Capacitor app; a broken shim does not block the web build', () => {
  let e = env({ cap: { isNativePlatform: () => true } }); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }), false); assert.deepEqual(e.reg, []);
  e = env({ cap: { isNativePlatform: () => false } }); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }), true);
  e = env({ cap: { isNativePlatform: () => { throw new Error('shim'); } } }); assert.equal(registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }), true);
});
test('registerOffline: waits for the load event when the page is still loading, and never throws on failure', async () => {
  let e = env({ ready: 'loading' }); registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true });
  assert.deepEqual(e.reg, []); e.listeners.load(); assert.deepEqual(e.reg, ['/sw.js']);
  e = env({ regImpl: () => Promise.reject(new Error('SecurityError')) }); assert.doesNotThrow(() => registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true })); await sleep(5);
  e = env({ regImpl: () => { throw new Error('sync boom'); } }); assert.doesNotThrow(() => registerOfflineSupport({ nav: e.nav, win: e.win, isProd: true }));
});

// ── runner ───────────────────────────────────────────────────────────────────
let pass = 0, fail = 0;
for (const [name, fn] of results) {
  try { await fn(); pass += 1; console.log('  ✓', name); } catch (e) { fail += 1; console.log('  ✗', name, '\n      ', String((e && e.message) || e).split('\n').slice(0, 3).join(' | ')); }
}
console.log(`\n${pass} passed, ${fail} failed (offline: service worker, page, registration)`);
process.exit(fail ? 1 : 0);
