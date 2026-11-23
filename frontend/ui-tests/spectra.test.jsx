import { test, mount, flush, click, route, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import SpectraPanel from '../src/components/SpectraPanel.jsx';
import FX from './fixtures/spectra.json';

const AOI = { type: 'Polygon', coordinates: [[[75, 26], [75.1, 26], [75.1, 26.1], [75, 26.1], [75, 26]]] };
const POLL_WAIT = 2400;   // the panel polls the job every 2 s

const spy = () => {
  const prev = globalThis.fetch, calls = [];
  globalThis.fetch = (u, init) => { calls.push({ u: String(u), method: init?.method, body: init?.body ? JSON.parse(init.body) : null }); return prev(u, init); };
  return { calls, posts: () => calls.filter((c) => c.method === 'POST' && c.u.includes('/remote-sensing/analyze')), restore: () => { globalThis.fetch = prev; } };
};
const answer = (result) => route('/remote-sensing/analyze', { request_id: 'job1', status: 'done', result });   // POST gets the id; the poll gets 'done' + result
const panel = () => <SpectraPanel apiUrl="" drawnAOI={AOI} onShowOverlay={() => {}} onClearOverlay={() => {}} onSetPointPickHandler={() => {}} />;
const run = async (m) => { await click(buttonWith(m.container, 'Run')); await flush(POLL_WAIT); };
const box = (m, label) => qa(m.container, 'label').find((l) => text(l).trim() === label);

export function register() {
  test('Spectra: the index picker offers all 11 indices, all selected, and the run request asks for all 11', async () => {
    answer(FX.indices_ok); const s = spy(); const m = await mount(panel());
    try {
      for (const id of ['NDVI', 'NDWI', 'MNDWI', 'NDBI', 'SAVI', 'EVI', 'NDSI', 'NDMI', 'NDRE', 'MSAVI2', 'NBR']) { const l = box(m, id); ok(l, `${id} checkbox`); ok(l.querySelector('input').checked, `${id} selected by default`); }
      await run(m); const body = s.posts()[0].body; eq(body.tool, 'spectral_indices'); eq(body.indices, FX.indices_ids); eq(body.indices.length, 11);
      for (const l of ['NDMI — Normalized Difference Moisture Index', 'NDRE — Normalized Difference Red Edge Index', 'MSAVI2 — Modified Soil-Adjusted Vegetation Index', 'NBR — Normalized Burn Ratio']) has(text(m.container), l);
    } finally { await m.unmount(); s.restore(); }
  });

  test('Spectra: unchecking an index removes it from the request; none selected disables Run', async () => {
    answer(FX.indices_ok); const s = spy(); const m = await mount(panel());
    try {
      await click(box(m, 'NDRE').querySelector('input')); await run(m); const sent = s.posts()[0].body.indices; ok(!sent.includes('ndre') && sent.length === 10 && sent.includes('nbr'), 'only NDRE was dropped');
      for (const l of qa(m.container, 'label')) { const i = l.querySelector('input[type="checkbox"]'); if (i && i.checked) await click(i); }
      ok(buttonWith(m.container, 'Run').disabled, 'Run disabled with nothing selected');
    } finally { await m.unmount(); s.restore(); }
  });

  test('Spectra: the new indices carry their honest explanations (NDRE is not nitrogen; NDMI is not open water; NBR is single-date)', async () => {
    answer(FX.indices_ok); const s = spy(); const m = await mount(panel());
    try {
      await run(m); const t = text(m.container);
      has(t, 'not nitrogen directly'); has(t, 'NOT the open-water NDWI'); has(t, 'single-date value'); has(t, 'Gitelson & Merzlyak 1994'); has(t, 'Qi et al. 1994'); has(t, 'Key & Benson 2006'); has(t, 'Gao 1996');
      has(t, '(B8 - B5) / (B8 + B5)'); has(t, '(B8 - B12) / (B8 + B12)'); has(t, '(B8 - B11) / (B8 + B11)');
    } finally { await m.unmount(); s.restore(); }
  });

  test('Spectra: an index with no reading says "no data" (never blank, null or 0)', async () => {
    answer(FX.indices_null); const s = spy(); const m = await mount(panel());
    try {
      await run(m); const t = text(m.container); has(t, 'no data'); lacks(t, 'null'); lacks(t, 'NaN'); lacks(t, 'undefined');
      eq((t.match(/no data/g) || []).length, 11, 'one "no data" per index'); has(t, 'min —'); lacks(t, 'min 0');
    } finally { await m.unmount(); s.restore(); }
  });

  test('Spectra: a very small area tells you the value is the pixel under its centre', async () => {
    answer(FX.indices_small); const s = spy(); const m = await mount(panel());
    try { await run(m); has(text(q(m.container, '[data-testid="sampling-note"]')), 'smaller than one Sentinel-2 pixel'); } finally { await m.unmount(); s.restore(); }
    answer(FX.indices_ok); const s2 = spy(); const m2 = await mount(panel());
    try { await run(m2); ok(!q(m2.container, '[data-testid="sampling-note"]'), 'no note for a normal area'); } finally { await m2.unmount(); s2.restore(); }
  });

  test('Spectra soil moisture: a missing reading is not presented as "no coverage" or as zero; a real one shows its sampling note', async () => {
    const cases = [[FX.moisture_missing, 'missing reading, not zero moisture', 'widening the dates'], [FX.moisture_nocoverage, 'widening the dates', 'missing reading']];
    for (const [fx, want, notWant] of cases) {
      answer(fx); const s = spy(); const m = await mount(panel());
      try { await click(buttonWith(m.container, 'Soil Moisture')); await run(m); const t = text(m.container); has(t, want); lacks(t, notWant); lacks(t, 'm³/m³'); } finally { await m.unmount(); s.restore(); }
    }
    answer(FX.moisture_small); const s = spy(); const m = await mount(panel());
    try { await click(buttonWith(m.container, 'Soil Moisture')); await run(m); has(text(m.container), '0.18 m³/m³'); has(text(q(m.container, '[data-testid="sampling-note"]')), 'smaller than one SMAP pixel'); } finally { await m.unmount(); s.restore(); }
    answer(FX.moisture_ok); const s2 = spy(); const m2 = await mount(panel());
    try { await click(buttonWith(m2.container, 'Soil Moisture')); await run(m2); has(text(m2.container), '0.213 m³/m³'); ok(!q(m2.container, '[data-testid="sampling-note"]')); } finally { await m2.unmount(); s2.restore(); }
  });

  test('Spectra drought index (VHI): no data shows "no data", not "null%" or an empty bar', async () => {
    answer(FX.vhi_null); const s = spy(); const m = await mount(panel());
    try {
      await click(buttonWith(m.container, 'Drought Index')); await run(m); const t = text(m.container);
      lacks(t, 'null'); lacks(t, 'NaN'); has(t, 'no data'); eq((t.match(/no data/g) || []).length >= 8, true, 'means for three indices plus the five classes');
      eq(qa(m.container, '[style*="null"]').length, 0, 'no style carries "null"'); ok(qa(m.container, '[style*="width: 0%"]').length >= 5, 'the five class bars are drawn 0 wide');
    } finally { await m.unmount(); s.restore(); }
  });
}
