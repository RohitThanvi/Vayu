import { act } from 'react';
import { test, mount, flush, click, route, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import { PathwayTab, BasisLine, CropSuitabilityTab } from '../src/components/AgriIntelBar.jsx';

const CROPS = { crops: [{ id: 'dragon_fruit', name: 'Dragon fruit (Kamalam)' }, { id: 'saffron', name: 'Saffron' }], chemical_policy: 'x' };
const line = (text, basis, keys = []) => ({ text, basis, sources: keys.map((k) => ({ key: k, url: `https://example.org/${k}` })) });
const GUIDE = Object.fromEntries(['planting_material', 'land_and_planting', 'irrigation', 'nutrition_organic', 'pests_diseases_organic', 'monitoring', 'harvest_postharvest', 'risks_costs'].map((k) => [k, [line(`${k} cited line`, 'cited', ['S1', 'S2']), line(`${k} general line`, 'general')]]));
const GAP = (kind, sev, extra = {}) => ({ if: kind, severity: sev, do: `${kind} action text`, basis: 'cited', src: ['S1'], sources: [], ...extra });
const REPORT = {
  crop: { id: 'dragon_fruit', name: 'Dragon fruit (Kamalam)', setting: 'open_field' }, controlled: null,
  first_timer: { difficulty: 3, capital: 'high', risk: 'medium', time_to_first_fruit: 'about 1-2 years', why: 'Needs poles up front.' },
  verdict: { tier: 2, tier_label: 'Open-field with help', why: 'Workable with help: ph need attention.', cautions: ['Monthly averages can hide cold nights.'],
             checks: [{ factor: 'soil pH', your_value: 8.1, crop_range: '5.5-7.0', status: 'gap' }, { factor: 'annual rainfall', your_value: null, crop_range: '500-1500 mm', status: 'unknown' }, { factor: 'coldest month mean temperature', your_value: 14.2, crop_range: '>5 C', status: 'ok' }],
             triggered: [GAP('ph', 'fixable', { basis: 'general' }), GAP('dry', 'fixable', { addressed: true })].map((g) => ({ addressed: false, ...g })) },
  gap_actions: [GAP('frost', 'fixable'), GAP('wet', 'fixable')], guide: GUIDE, sources: { S1: 'https://example.org/S1' },
  chemical_policy: 'This guide gives organic and cultural methods only. It does not name or dose pesticides.', disclaimer: 'Advisory aid, not a guarantee.', manual_markdown: '# md',
};
const SAFFRON = { ...REPORT, crop: { id: 'saffron', name: 'Saffron', setting: 'controlled' }, controlled: { what: 'an insulated room with chillers', season: 'Plant around July.' },
  verdict: { tier: 3, tier_label: 'Controlled environment', why: 'Grown in an insulated room.', cautions: [], checks: [], triggered: [] }, gap_actions: [GAP('indoor_climate_control', 'fixable')] };

const spy = () => { const prev = globalThis.fetch, bodies = []; globalThis.fetch = (u, init) => { if (init?.body) bodies.push(JSON.parse(init.body)); return prev(u, init); }; return { bodies, restore: () => { globalThis.fetch = prev; } }; };
const setField = async (el, value) => { const proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype; await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value); el.dispatchEvent(new window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await flush(5); };
const AOI = { type: 'Polygon', coordinates: [[[75, 26], [76, 26], [76, 27], [75, 26]]] };
const go = async (m, { aoi = null } = {}) => { await flush(20); await setField(q(m.container, 'select[aria-label="Crop"]'), 'dragon_fruit'); if (!aoi) { await setField(q(m.container, 'input[aria-label="Latitude"]'), '26.9'); await setField(q(m.container, 'input[aria-label="Longitude"]'), '75.8'); } await click(buttonWith(m.container, 'SHOW ME HOW')); await flush(20); };

export function register() {
  test('Pathway: needs a crop AND a location; the request carries crop_id, point, irrigation and the farmer\'s pH', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT); const s = spy();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await flush(20); eq(qa(m.container, 'select[aria-label="Crop"] option').map((o) => o.textContent).join('|'), 'Choose a crop…|Dragon fruit (Kamalam)|Saffron');
      ok(buttonWith(m.container, 'SHOW ME HOW').disabled, 'disabled with nothing chosen');
      await setField(q(m.container, 'select[aria-label="Crop"]'), 'dragon_fruit'); ok(buttonWith(m.container, 'SHOW ME HOW').disabled, 'a crop alone is not enough');
      await setField(q(m.container, 'input[aria-label="Latitude"]'), '26.9'); await setField(q(m.container, 'input[aria-label="Longitude"]'), '75.8'); ok(!buttonWith(m.container, 'SHOW ME HOW').disabled);
      await click(buttonWith(m.container, 'Yes')); await setField(q(m.container, 'input[aria-label="Soil pH (optional)"]'), '7.4');
      await click(buttonWith(m.container, 'SHOW ME HOW')); await flush(20);
      eq(JSON.stringify(s.bodies.at(-1)), JSON.stringify({ crop_id: 'dragon_fruit', irrigation_available: true, ph_override: 7.4, lat: 26.9, lon: 75.8 }));
    } finally { await m.unmount(); s.restore(); }
  });

  test('Pathway: a location without a crop cannot be submitted', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT);
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={AOI} />);
    try { await flush(20); ok(buttonWith(m.container, 'SHOW ME HOW').disabled, 'a drawn area alone is not enough'); } finally { await m.unmount(); }
  });

  test('Pathway: a drawn area works in place of a point; no pH is sent unless entered', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT); const s = spy();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={AOI} />);
    try {
      await go(m, { aoi: true });
      eq(JSON.stringify(s.bodies.at(-1)), JSON.stringify({ crop_id: 'dragon_fruit', irrigation_available: false, aoi_geojson: AOI }));
    } finally { await m.unmount(); s.restore(); }
  });

  test('Pathway: shows the tier, your land vs the crop, cautions, and only the actions that APPLY to this land', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT);
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await go(m);
      has(text(q(m.container, '[data-testid="tier"]')), 'Open-field with help'); has(text(m.container), 'Workable with help');
      const t = text(q(m.container, '[data-testid="checks"]')); has(t, 'soil pH'); has(t, '8.1'); has(t, '5.5-7.0'); has(t, 'gap'); has(t, 'n/a'); has(t, 'unknown'); lacks(t, 'null');
      has(text(q(m.container, '[data-testid="caution"]')), 'cold nights');
      const ap = text(q(m.container, '[data-testid="applies"]')); has(ap, 'ph action text'); has(ap, 'general knowledge, not verified'); lacks(ap, 'frost action text'); lacks(ap, 'wet action text'); lacks(ap, 'dry action text');
      has(text(q(m.container, '[data-testid="first-timer"]')), 'difficulty 3/5 · capital high · risk medium');
    } finally { await m.unmount(); }
  });

  test('Pathway: every manual section is present, cited lines link their sources, general lines say so, and the no-chemicals policy is shown', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT);
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await go(m);
      eq(qa(m.container, '[data-testid^="sec-"]').length, 8);
      const sec = q(m.container, '[data-testid="sec-irrigation"]'); has(text(sec), 'irrigation cited line'); has(text(sec), 'irrigation general line');
      eq(qa(sec, '[data-testid="src-link"]').map((a) => a.getAttribute('href')).join(), 'https://example.org/S1,https://example.org/S2'); ok(qa(sec, 'a')[0].getAttribute('target') === '_blank' && qa(sec, 'a')[0].getAttribute('rel') === 'noreferrer');
      eq(qa(sec, '[data-testid="general-tag"]').length, 1);
      has(text(q(m.container, '[data-testid="policy"]')), 'does not name or dose pesticides'); has(text(m.container), 'Advisory aid');
    } finally { await m.unmount(); }
  });

  test('Pathway: a controlled-environment crop shows what to build and plan for, not a field to-do list', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', SAFFRON);
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await go(m);
      has(text(q(m.container, '[data-testid="tier"]')), 'Controlled environment'); has(text(q(m.container, '[data-testid="controlled"]')), 'an insulated room with chillers');
      ok(!q(m.container, '[data-testid="applies"]'), 'no open-field action list for a controlled crop'); has(text(m.container), 'What to plan for'); has(text(m.container), 'indoor climate control');
    } finally { await m.unmount(); }
  });

  test('Pathway: when irrigation covers the rainfall gap the farmer is told so; a failed request shows an error and keeps the form', async () => {
    route('/agri/pathway-crops', CROPS);
    route('/agri/pathway-report', { ...REPORT, verdict: { ...REPORT.verdict, tier: 1, tier_label: 'Open-field suitable', triggered: [GAP('dry', 'fixable', { addressed: true })].map((g) => ({ addressed: false, ...g })) } });
    let m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try { await go(m); has(text(q(m.container, '[data-testid="applies"]')), 'Your irrigation covers the rainfall gap'); } finally { await m.unmount(); }
    route('/agri/pathway-report', new Error('boom'));
    m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try { await go(m); ok(!q(m.container, '[data-testid="tier"]'), 'no result shown on failure'); ok(q(m.container, 'select[aria-label="Crop"]'), 'form still there'); } finally { await m.unmount(); }
  });

  test('BasisLine: a cited line with no usable source still renders without a general tag', async () => {
    const m = await mount(<BasisLine line={line('Only text', 'cited', [])} />);
    try { has(text(m.container), 'Only text'); eq(qa(m.container, '[data-testid="general-tag"]').length, 0); eq(qa(m.container, '[data-testid="src-link"]').length, 0); } finally { await m.unmount(); }
  });

  test('Crops tab: tells the farmer when climate came from nearby land instead of the exact spot', async () => {
    const prof = (note) => ({ crops: [], profile: { ph: 7, organic_carbon_gkg: 5, texture_name: 'Loam', annual_rain_mm: 600, annual_mean_temp_c: 25, climate_note: note, value_source: { ph: 'modeled', organic_carbon_gkg: 'modeled', texture: 'modeled' } } });
    const NOTE = 'No climate data exists at the exact spot (water or coast pixel), so rainfall and temperature are land averages within about 25 km.';
    route('/agri/crop-suitability', prof(NOTE));
    let m = await mount(<CropSuitabilityTab apiUrl="" drawnAOI={AOI} />);
    try { await click(buttonWith(m.container, 'FIND SUITABLE CROPS')); await flush(20); has(text(q(m.container, '[data-testid="climate-note"]')), 'land averages within about 25 km'); } finally { await m.unmount(); }
    route('/agri/crop-suitability', prof(null));
    m = await mount(<CropSuitabilityTab apiUrl="" drawnAOI={AOI} />);
    try { await click(buttonWith(m.container, 'FIND SUITABLE CROPS')); await flush(20); has(text(m.container), 'Soil pH'); ok(!q(m.container, '[data-testid="climate-note"]'), 'no note when the exact spot had data'); } finally { await m.unmount(); }
  });

  const PLAN = { area_acres: 2.5, area_ha: 1.012, count: 1124, plant_noun: 'plants', rows: [{ label: 'Plants at 3 m x 3 m', value: 'about 1,124', basis: 'arithmetic', src: ['S2'] }, { label: 'Farmyard manure or compost at planting', value: '11 to 17 tonnes', basis: 'arithmetic', src: ['S1'] }], note: 'Sums of the cited amounts for your plot size.' };
  const WINDOW = { months: [7, 8, 9, 10], month_names: ['Jul', 'Aug', 'Sep', 'Oct'], label: 'Jul to Oct', note: 'July to October', rain_in_window_mm: 660, mean_temp_in_window_c: 26.8, caution: 'Check with your KVK.' };

  test('Pathway: plot size is optional, validated, and sent; the plan and planting window for THIS land are shown', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', { ...REPORT, plan: PLAN, planting_window: WINDOW }); const s = spy();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await flush(20); await setField(q(m.container, 'select[aria-label="Crop"]'), 'dragon_fruit'); await setField(q(m.container, 'input[aria-label="Latitude"]'), '26.9'); await setField(q(m.container, 'input[aria-label="Longitude"]'), '75.8');
      const area = q(m.container, 'input[aria-label="Land area in acres (optional)"]');
      for (const bad of ['0', '-3', 'abc', '1001']) { await setField(area, bad); ok(q(m.container, '[data-testid="area-bad"]'), `${bad} flagged`); ok(buttonWith(m.container, 'SHOW ME HOW').disabled, `${bad} blocks submit`); }
      await setField(area, '2.5'); ok(!q(m.container, '[data-testid="area-bad"]')); ok(!buttonWith(m.container, 'SHOW ME HOW').disabled);
      await click(buttonWith(m.container, 'SHOW ME HOW')); await flush(20);
      eq(JSON.stringify(s.bodies.at(-1)), JSON.stringify({ crop_id: 'dragon_fruit', irrigation_available: false, area_acres: 2.5, lat: 26.9, lon: 75.8 }));
      const plan = text(q(m.container, '[data-testid="plan"]')); has(plan, 'Your planting plan for 2.5 acres'); has(plan, 'about 1,124'); has(plan, '11 to 17 tonnes'); has(plan, 'Sums of the cited amounts');
      const w = text(q(m.container, '[data-testid="window"]')); has(w, '(Jul to Oct)'); has(w, '660 mm'); has(w, '26.8'); has(w, 'Check with your KVK');
    } finally { await m.unmount(); s.restore(); }
  });

  test('Pathway: without a plan or window (saffron, no cited months) neither block is drawn, and blank area is not sent', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', { ...SAFFRON, plan: null, planting_window: null }); const s = spy();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try { await go(m); ok(!q(m.container, '[data-testid="plan"]') && !q(m.container, '[data-testid="window"]')); ok(!('area_acres' in s.bodies.at(-1))); } finally { await m.unmount(); s.restore(); }
  });

  test('Pathway: a planting window whose local rainfall is unknown is not shown as "0 mm"', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', { ...REPORT, plan: PLAN, planting_window: { ...WINDOW, rain_in_window_mm: null, mean_temp_in_window_c: null } });
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try { await go(m); ok(q(m.container, '[data-testid="plan"]'), 'plan still shown'); ok(!q(m.container, '[data-testid="window"]'), 'no window line without local data'); lacks(text(m.container), '0 mm of rain'); } finally { await m.unmount(); }
  });


  test('Pathway: a one-month or split-season window uses its label, never "first to last month"', async () => {
    route('/agri/pathway-crops', CROPS);
    for (const [label, months, names, forbidden] of [['Jul', [7], ['Jul'], 'Jul to Jul'], ['Feb to Mar and Jun to Aug', [6, 7, 8, 2, 3], ['Jun', 'Jul', 'Aug', 'Feb', 'Mar'], 'Jun to Mar']]) {
      route('/agri/pathway-report', { ...REPORT, plan: PLAN, planting_window: { ...WINDOW, label, months, month_names: names } });
      const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
      try { await go(m); const w = text(q(m.container, '[data-testid="window"]')); has(w, `(${label})`); lacks(w, forbidden); } finally { await m.unmount(); }
    }
  });

  test('Pathway: an "edge" result is amber, a gap red, ok green; soil texture shows its name', async () => {
    route('/agri/pathway-crops', CROPS);
    route('/agri/pathway-report', { ...REPORT, verdict: { ...REPORT.verdict, checks: [{ factor: 'annual rainfall', your_value: 1528, crop_range: '500-1500 mm', status: 'edge', note: '' }, { factor: 'soil pH', your_value: 8.1, crop_range: '5.5-7.0', status: 'gap', note: '' }, { factor: 'coldest month mean temperature', your_value: 20, crop_range: '>5 C', status: 'ok', note: '' }, { factor: 'soil texture', your_value: 'Clay loam', crop_range: 'drains freely or is medium', status: 'gap', note: '' }] } });
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await go(m);
      const cell = (factor) => [...qa(m.container, '[data-testid="checks"] tbody tr')].find((r) => r.children[0].textContent === factor).children[3].style.color;
      eq(cell('annual rainfall'), 'rgb(201, 168, 106)'); eq(cell('soil pH'), 'rgb(255, 122, 69)'); eq(cell('coldest month mean temperature'), 'rgb(46, 204, 113)');
      has(text(q(m.container, '[data-testid="checks"]')), 'Clay loam');
    } finally { await m.unmount(); }
  });

  const pdfFetch = (status = 200, detail) => {
    const prev = globalThis.fetch, calls = [];
    globalThis.fetch = (u, init) => { if (String(u).includes('/pathway-report/pdf')) { calls.push({ u: String(u), body: JSON.parse(init.body), method: init.method }); return Promise.resolve({ ok: status === 200, status, blob: async () => new Blob(['%PDF-1.4'], { type: 'application/pdf' }), json: async () => ({ detail }) }); } return prev(u, init); };
    return { calls, restore: () => { globalThis.fetch = prev; } };
  };
  const stubDownload = () => {
    const A = window.HTMLAnchorElement.prototype, prevClick = A.click, prevC = URL.createObjectURL, prevR = URL.revokeObjectURL, names = [];
    A.click = function () { names.push(this.download); }; URL.createObjectURL = () => 'blob:x'; URL.revokeObjectURL = () => {};
    return { names, restore: () => { A.click = prevClick; URL.createObjectURL = prevC; URL.revokeObjectURL = prevR; } };
  };

  test('Pathway: the PDF button posts the SAME inputs the farmer entered and downloads a named PDF', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', { ...REPORT, plan: PLAN, planting_window: WINDOW }); const s = spy(), f = pdfFetch(), dl = stubDownload();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await flush(20); await setField(q(m.container, 'select[aria-label="Crop"]'), 'dragon_fruit'); await setField(q(m.container, 'input[aria-label="Latitude"]'), '26.9'); await setField(q(m.container, 'input[aria-label="Longitude"]'), '75.8');
      await setField(q(m.container, 'input[aria-label="Land area in acres (optional)"]'), '4'); await click(buttonWith(m.container, 'Yes')); await click(buttonWith(m.container, 'SHOW ME HOW')); await flush(20);
      ok(!buttonWith(m.container, '.md'), 'no Markdown download any more'); await click(buttonWith(m.container, 'DOWNLOAD YOUR GUIDE (PDF)')); await flush(30);
      eq(f.calls.length, 1); eq(f.calls[0].method, 'POST'); has(f.calls[0].u, '/api/v1/agri/pathway-report/pdf');
      eq(JSON.stringify(f.calls[0].body), JSON.stringify({ crop_id: 'dragon_fruit', irrigation_available: true, area_acres: 4, lat: 26.9, lon: 75.8 }), 'same body as the report request');
      eq(dl.names.join(), 'Vayu_dragon_fruit_guide.pdf'); ok(!q(m.container, '[data-testid="pdf-error"]'));
    } finally { await m.unmount(); s.restore(); f.restore(); dl.restore(); }
  });

  test('Pathway: a failed PDF request shows the server message and the guide stays on screen', async () => {
    route('/agri/pathway-crops', CROPS); route('/agri/pathway-report', REPORT); const f = pdfFetch(422, 'Land area must be more than 0.'), dl = stubDownload();
    const m = await mount(<PathwayTab apiUrl="" drawnAOI={null} />);
    try {
      await go(m); await click(buttonWith(m.container, 'DOWNLOAD YOUR GUIDE (PDF)')); await flush(30);
      has(text(q(m.container, '[data-testid="pdf-error"]')), 'Land area must be more than 0.'); eq(dl.names.length, 0, 'nothing downloaded'); ok(q(m.container, '[data-testid="tier"]'), 'the on-screen guide is still there');
      ok(!buttonWith(m.container, 'PREPARING'), 'button is usable again');
    } finally { await m.unmount(); f.restore(); dl.restore(); }
  });
}
