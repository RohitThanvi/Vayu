import { act } from 'react';
import { test, mount, flush, click, route, calls, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import { IrrigationTab, StageBar } from '../src/components/AgriIntelBar.jsx';

const AOI = { type: 'Polygon', coordinates: [[[75, 26], [76, 26], [76, 27], [75, 26]]] };
const CROPS = { crops: [{ id: 'wheat', name: 'Wheat', total_days: 120 }, { id: 'maize', name: 'Maize', total_days: 125 }] };
const cal = (L, sow = '2025-11-15') => { let e = 0; return ['initial', 'development', 'mid-season', 'late season'].map((n, i) => { const o = { stage: n, label: n, start_das: e + 1, end_das: e + L[i], start_date: sow, end_date: sow }; e += L[i]; return o; }); };
const STAGE = { status: 'ok', crop_name: 'Wheat', stage: 'mid-season', stage_label: 'Mid-season (peak demand)', stage_day: 27, stage_length: 50, kc: 1.15, relative_demand: 1, das: 67, total_days: 120, sowing_date: '2025-11-15', calendar: cal([15, 25, 50, 30]), caveat: 'Stage lengths are FAO indicative values.' };
const RZ = { available: true, status: 'dry', sm_surface_0_5cm: 0.1, sm_rootzone_0_100cm: 0.2, sm_rootzone_percentile: 12.0, era5_layer2_7_28cm: 0.18, era5_layer3_28_100cm: 0.2, depth_note: 'No 15-25 cm layer exists; ERA5-Land layer 2 (7-28 cm) spans it.' };
const RESULT = { recommendation: 'irrigate_now', urgency: 'high', reason: 'Root-zone moisture is dry.', decision_basis: ['SMAP L4 root zone (0-100 cm, modelled) at the 12.0th percentile: dry', 'FAO-56 stage: Mid-season'], soil_moisture: { dry_stress_pct: 70 }, rainfall: { condition: 'well below normal' }, root_zone: RZ, crop_stage: STAGE, disclaimer: 'Regional read.' };

// fetch spy that records request bodies, delegating to the harness fetch
const spy = () => { const prev = globalThis.fetch, bodies = []; globalThis.fetch = (u, init) => { if (init?.body) bodies.push(JSON.parse(init.body)); return prev(u, init); }; return { bodies, restore: () => { globalThis.fetch = prev; } }; };
const setField = async (el, value) => { const proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype; await act(async () => { Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value); el.dispatchEvent(new window.Event(el.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })); }); await flush(5); };

export function register() {
  test('Irrigation: crop list loads; the date field appears with a crop; Run waits for a date; the request carries crop_id + sowing_date', async () => {
    route('/agri/crop-stages', CROPS); route('/agri/irrigation-advisory', RESULT); const s = spy();
    const m = await mount(<IrrigationTab apiUrl="" drawnAOI={AOI} />);
    try {
      await flush(20);
      eq(qa(m.container, 'select[aria-label="Crop"] option').map((o) => o.textContent).join('|'), 'No crop — regional read|Wheat|Maize');
      ok(!q(m.container, 'input[aria-label="Sowing date"]'), 'no date field until a crop is chosen');
      await setField(q(m.container, 'select[aria-label="Crop"]'), 'wheat');
      ok(q(m.container, 'input[aria-label="Sowing date"]'), 'date field shown'); has(text(m.container), 'Pick the sowing date');
      const run = buttonWith(m.container, 'CHECK IRRIGATION NEED'); ok(run.disabled, 'Run disabled until a sowing date is set');
      await setField(q(m.container, 'input[aria-label="Sowing date"]'), '2025-11-15');
      ok(!buttonWith(m.container, 'CHECK IRRIGATION NEED').disabled); await click(buttonWith(m.container, 'CHECK IRRIGATION NEED')); await flush(20);
      eq(JSON.stringify(s.bodies.at(-1)), JSON.stringify({ aoi_geojson: AOI, crop_id: 'wheat', sowing_date: '2025-11-15' }));
    } finally { await m.unmount(); s.restore(); }
  });

  test('Irrigation: with no crop the request is the plain regional read', async () => {
    route('/agri/crop-stages', CROPS); route('/agri/irrigation-advisory', { ...RESULT, crop_stage: null }); const s = spy();
    const m = await mount(<IrrigationTab apiUrl="" drawnAOI={AOI} />);
    try {
      await flush(20); await click(buttonWith(m.container, 'CHECK IRRIGATION NEED')); await flush(20);
      eq(JSON.stringify(s.bodies.at(-1)), JSON.stringify({ aoi_geojson: AOI }), 'no crop fields sent');
      ok(!q(m.container, '[data-testid="crop-stage"]'), 'no stage block without a crop');
    } finally { await m.unmount(); s.restore(); }
  });

  test('Irrigation: result shows recommendation, urgency, basis, soil moisture by depth with the depth caveat, and the stage block', async () => {
    route('/agri/crop-stages', CROPS); route('/agri/irrigation-advisory', RESULT);
    const m = await mount(<IrrigationTab apiUrl="" drawnAOI={AOI} />);
    try {
      await flush(20); await click(buttonWith(m.container, 'CHECK IRRIGATION NEED')); await flush(20);
      const t = text(m.container); has(t, 'irrigate now'); ok(q(m.container, '[data-testid="urgency"]'), 'high urgency tag');
      has(text(q(m.container, '[data-testid="basis"]')), '12.0th percentile');
      const rz = text(q(m.container, '[data-testid="root-zone"]')); has(rz, '12th percentile · dry'); has(rz, '0.18 m³/m³'); has(rz, 'No 15-25 cm layer exists'); has(rz, '7–28 cm, ERA5-Land');
      const st = text(q(m.container, '[data-testid="crop-stage"]')); has(st, 'Wheat · Mid-season (peak demand) · day 27 of 50 · Kc 1.15 (100% of peak demand)'); has(st, 'FAO indicative');
    } finally { await m.unmount(); }
  });

  test('Irrigation: an unavailable root zone and an unusable stage are shown as such, never as zeros', async () => {
    route('/agri/crop-stages', CROPS);
    route('/agri/irrigation-advisory', { ...RESULT, urgency: 'normal', root_zone: { ...RZ, available: false, status: 'unknown', sm_rootzone_percentile: null, sm_rootzone_0_100cm: null, era5_layer2_7_28cm: null, era5_layer3_28_100cm: null }, crop_stage: { status: 'before_sowing', note: 'Sowing date is 3 day(s) after the check date.' } });
    const m = await mount(<IrrigationTab apiUrl="" drawnAOI={AOI} />);
    try {
      await flush(20); await click(buttonWith(m.container, 'CHECK IRRIGATION NEED')); await flush(20);
      const rz = text(q(m.container, '[data-testid="root-zone"]')); has(rz, 'not available'); has(rz, 'n/a'); lacks(rz, 'null'); lacks(rz, 'undefined'); lacks(rz, 'NaN');
      has(text(q(m.container, '[data-testid="crop-stage"]')), 'Sowing date is 3 day(s) after'); ok(!q(m.container, '[data-testid="urgency"]'), 'no urgency tag when normal'); ok(!q(m.container, '[data-testid="stage-bar"]'));
    } finally { await m.unmount(); }
  });

  test('Irrigation: a failing crop-stages request leaves a working tab with only the regional option', async () => {
    route('/agri/crop-stages', new Error('offline')); route('/agri/irrigation-advisory', { ...RESULT, crop_stage: null });
    const m = await mount(<IrrigationTab apiUrl="" drawnAOI={AOI} />);
    try {
      await flush(20); eq(qa(m.container, 'select[aria-label="Crop"] option').length, 1);
      await click(buttonWith(m.container, 'CHECK IRRIGATION NEED')); await flush(20); has(text(m.container), 'irrigate now');
    } finally { await m.unmount(); }
  });

  test('StageBar: segments are proportional to stage length, the current stage is emphasised, and the marker sits at day/total', async () => {
    const m = await mount(<StageBar stage={STAGE} />);
    try {
      const segs = qa(m.container, '[data-stage]'); eq(segs.map((e) => e.getAttribute('data-stage')).join(), 'initial,development,mid-season,late season');
      eq(segs.map((e) => e.style.flexGrow || e.style.flex.split(' ')[0]).join(), '15,25,50,30');
      eq(segs.map((e) => e.style.opacity).join(), '0.4,0.4,1,0.4');
      has(q(m.container, '[data-testid="stage-marker"]').style.left, String((67 / 120) * 100).slice(0, 6)); has(text(m.container), 'day 67 of 120');
    } finally { await m.unmount(); }
    const n = await mount(<StageBar stage={{ status: 'after_cycle' }} />);
    try { eq(n.container.innerHTML, '', 'renders nothing for a stage that did not resolve'); } finally { await n.unmount(); }
  });
}
