import { act } from 'react';
import { test, mount, flush, click, route, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import { PathwayTab, BasisLine } from '../src/components/AgriIntelBar.jsx';

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
}
