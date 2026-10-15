// Tiny DOM test harness: mount, act, fetch routing, user-event helpers.
import { act } from 'react';
import { createRoot } from 'react-dom/client';

export const routes = [];            // [substring, bodyOrFn]; first match wins; reset per test
export const calls = [];             // every URL fetched
export function route(sub, body) { routes.unshift([sub, body]); }
export function resetNet() { routes.length = 0; calls.length = 0; }

globalThis.fetch = async (url) => {
  const u = String(url);
  calls.push(u);
  for (const [sub, body] of routes) {
    if (u.includes(sub)) {
      const b = typeof body === 'function' ? body(u) : body;
      if (b instanceof Error) throw b;
      const status = b && b.__status ? b.__status : 200;
      return { ok: status < 400, status, json: async () => b, text: async () => JSON.stringify(b), headers: { get: () => null } };
    }
  }
  return { ok: true, status: 200, json: async () => ({}), text: async () => '{}', headers: { get: () => null } };
};

export async function mount(element) {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(element); });
  await flush();
  return { container, rerender: async (el) => { await act(async () => { root.render(el); }); await flush(); }, unmount: async () => { await act(async () => root.unmount()); container.remove(); } };
}
export async function flush(ms = 25) { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); }
export async function click(el) { await act(async () => { el.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true })); }); await flush(5); }
export async function typeInto(el, value) {
  const proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value); el.dispatchEvent(new window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); });
}
export async function submit(form) { await act(async () => { form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); }); await flush(10); }
export async function key(el, k) { await act(async () => { el.dispatchEvent(new window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true })); }); await flush(5); }
export const text = (c) => c.textContent.replace(/\s+/g, ' ');
export const q = (c, sel) => c.querySelector(sel);
export const qa = (c, sel) => [...c.querySelectorAll(sel)];
export const byLabel = (c, label) => c.querySelector(`[aria-label="${label}"]`);
export const buttonWith = (c, t) => qa(c, 'button, a').find((b) => b.textContent.includes(t));

const results = [];
export function test(name, fn) { results.push([name, fn]); }
export async function runAll() {
  let pass = 0, fail = 0;
  for (const [name, fn] of results) {
    resetNet(); localStorage.clear(); document.body.innerHTML = ''; window.__vayuBusinessCommand = null;
    try { await fn(); pass++; console.log('  ✓', name); } catch (e) { fail++; console.log('  ✗', name, '\n      ', (e && e.errors ? e.errors.map((x) => String(x.stack || x).split('\n').slice(0, 3).join(' | ')).join(' || ') : String((e && e.stack) || e).split('\n').slice(0, 4).join(' | '))); }
  }
  console.log(`\n${pass} passed, ${fail} failed`);
  return fail;
}
export function eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${msg || 'not equal'}: got ${JSON.stringify(a)} expected ${JSON.stringify(b)}`); }
export function ok(c, msg) { if (!c) throw new Error(msg || 'assertion failed'); }
export function has(haystack, needle, msg) { if (!String(haystack).includes(needle)) throw new Error(`${msg || 'missing text'}: "${needle}" not in "${String(haystack).slice(0, 300)}"`); }
export function lacks(haystack, needle, msg) { if (String(haystack).includes(needle)) throw new Error(`${msg || 'unexpected text'}: "${needle}"`); }
