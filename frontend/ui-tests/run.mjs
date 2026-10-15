/**
 * UI tests: mounts the real React components in a simulated DOM (jsdom), mocks the API with realistic fixtures and drives them
 * like a user (click, type, keyboard). Run with `npm run test:ui`. No browser and no backend needed.
 */
import { JSDOM } from 'jsdom';
import { build } from 'esbuild';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'vayu-ui-')), 'bundle.mjs');
await build({
  entryPoints: [path.join(here, 'main.jsx')], bundle: true, platform: 'node', format: 'esm', outfile: out, jsx: 'automatic',
  loader: { '.js': 'jsx' }, logLevel: 'warning',
  banner: { js: "import{createRequire}from'module';const require=createRequire(import.meta.url);" },
});

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/', pretendToBeVisual: true });
const w = dom.window;
for (const k of ['window', 'document', 'localStorage', 'Event', 'CustomEvent', 'KeyboardEvent', 'MouseEvent', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'Node', 'MutationObserver', 'getComputedStyle', 'Image']) {
  Object.defineProperty(globalThis, k, { value: w[k], configurable: true, writable: true });
}
Object.defineProperty(globalThis, 'navigator', { value: w.navigator, configurable: true });
globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);
globalThis.cancelAnimationFrame = clearTimeout;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;   // set BEFORE React is imported

const { main } = await import(pathToFileURL(out).href);
process.exit((await main()) ? 1 : 0);
