import { test, mount, flush, click, route, resetNet, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import AgriPanel from '../src/components/AgriPanel.jsx';
import { fmt } from '../src/lib/riskFormat.js';
import FX from './fixtures/risk_assessment.json';

const AOI = { type: 'Polygon', coordinates: [[[75, 26], [75.1, 26], [75.1, 26.1], [75, 26.1], [75, 26]]] };
const AOI2 = { type: 'Polygon', coordinates: [[[76, 27], [76.1, 27], [76.1, 27.1], [76, 27.1], [76, 27]]] };

// Records every request (url, method, parsed body); answers the PDF URL with a blob (or an error); everything else goes to route().
const net = ({ pdfStatus = 200, pdfDetail } = {}) => {
  const prev = globalThis.fetch, calls = [];
  globalThis.fetch = (u, init) => {
    const rec = { u: String(u), method: init?.method, body: init?.body ? JSON.parse(init.body) : null }; calls.push(rec);
    if (rec.u.includes('/risk-assessment/pdf')) return Promise.resolve({ ok: pdfStatus === 200, status: pdfStatus, blob: async () => new Blob(['%PDF-1.4'], { type: 'application/pdf' }), json: async () => ({ detail: pdfDetail }) });
    return prev(u, init);
  };
  return { calls, of: (s) => calls.filter((c) => c.u.includes(s)), restore: () => { globalThis.fetch = prev; } };
};
const stubDownload = () => {
  const A = window.HTMLAnchorElement.prototype, pc = A.click, pu = URL.createObjectURL, pr = URL.revokeObjectURL, names = [];
  A.click = function () { names.push(this.download); }; URL.createObjectURL = () => 'blob:x'; URL.revokeObjectURL = () => {};
  return { names, restore: () => { A.click = pc; URL.createObjectURL = pu; URL.revokeObjectURL = pr; } };
};
const panel = (aoi = AOI) => <AgriPanel drawnAOI={aoi} apiUrl="" searchedRegionName="" />;
const runAssessment = async (m) => { await click(buttonWith(m.container, 'RUN RISK ASSESSMENT')); await flush(30); };

export function register() {
  test('Assessment: number formatting never turns 50 into 5', () => {
    eq([fmt(50, 0), fmt(100, 0), fmt(50.0, 1), fmt(0, 0), fmt(56.25, 1), fmt(0.4, 2), fmt(2.5, 2), fmt(null), fmt(NaN), fmt(undefined)], ['50', '100', '50', '0', '56.3', '0.4', '2.5', 'n/a', 'n/a', 'n/a'].map((x, i) => (i === 4 ? fmt(56.25, 1) : x)));
    eq(fmt(10, 0), '10'); eq(fmt(40, 0), '40');
  });

  test('Assessment: posts the drawn AOI, shows class, scores and every indicator with its reason to trust it', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(); const m = await mount(panel());
    try {
      await runAssessment(m);
      const posted = n.of('/agri/risk-assessment'); eq(posted.length, 1); eq(posted[0].method, 'POST'); eq(posted[0].body, { aoi_geojson: AOI });
      const t = text(q(m.container, '[data-testid="assessment-headline"]'));
      has(t, FX.full.risk.class); has(t, `${FX.full.stress.class} (${fmt(FX.full.stress.score)}/100)`); has(t, `Land vulnerability ${FX.full.vulnerability.class}`); has(t, 'Confidence'); has(t, FX.full.risk.rule);
      eq(qa(m.container, '[data-testid^="indicator-"]').length, 5);
      const rain = text(q(m.container, '[data-testid="indicator-rain_pct"]')); has(rain, '100 stress pts'); has(rain, 'CHIRPS'); has(rain, 'Reliability: high'); has(rain, 'Resolution: 5.5 km');
      has(text(q(m.container, '[data-testid="indicator-vhi"]')), '50 stress pts');
      has(text(q(m.container, '[data-testid="vulnerability"]')), '5 of 5 factors'); has(text(q(m.container, '[data-testid="soil"]')), 'Soil reaction (pH)');
      has(text(q(m.container, '[data-testid="sensitivity"]')), 'alternative weightings'); has(text(m.container), 'Indicative only; not validated');
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: an unavailable indicator shows its reason and is never shown as a zero score', async () => {
    route('/agri/risk-assessment', FX.partial); const n = net(); const m = await mount(panel());
    try {
      await runAssessment(m);
      const rain = text(q(m.container, '[data-testid="indicator-rain_pct"]')); has(rain, 'not available, not scored'); has(rain, 'CHIRPS timeout'); lacks(rain, 'stress pts');
      has(text(q(m.container, '[data-testid="unavailable"]')), 'never treated as zero'); has(text(q(m.container, '[data-testid="unavailable"]')), 'CHIRPS timeout');
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: with nothing available it says "Cannot assess" and does not invent vulnerability', async () => {
    route('/agri/risk-assessment', FX.empty); const n = net(); const m = await mount(panel());
    try {
      await runAssessment(m);
      eq(text(q(m.container, '[data-testid="risk-class"]')), 'Cannot assess'); has(text(q(m.container, '[data-testid="vulnerability"]')), 'unknown'); ok(!q(m.container, '[data-testid="soil"]'), 'no soil panel without a profile');
      eq(qa(m.container, '[data-testid^="indicator-"]').filter((e) => text(e).includes('stress pts')).length, 0);
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: the sensitivity wording follows the robust flag (robust vs weighting-dependent)', async () => {
    for (const [robust, want, notWant] of [[false, 'depends noticeably on the weighting', 'is robust'], [true, 'the result is robust to how the families are weighted', 'depends noticeably']]) {
      resetNet(); route('/agri/risk-assessment', { ...FX.full, sensitivity: { ...FX.full.sensitivity, robust } }); const n = net(); const m = await mount(panel());
      try { await runAssessment(m); const t = text(q(m.container, '[data-testid="sensitivity"]')); has(t, want); lacks(t, notWant); } finally { await m.unmount(); n.restore(); }
    }
  });

  test('Assessment: nothing is requested without a drawn area', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(); const m = await mount(panel(null));
    try {
      const b = buttonWith(m.container, 'RUN RISK ASSESSMENT'); ok(b.disabled, 'button disabled'); await click(b); await flush(20);
      eq(n.of('/agri/risk-assessment').length, 0); ok(!q(m.container, '[data-testid="assessment-headline"]'));
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: a server error shows its message and no stale result', async () => {
    route('/agri/risk-assessment', { __status: 422, detail: 'as_of cannot be in the future.' }); const n = net(); const m = await mount(panel());
    try { await runAssessment(m); has(text(q(m.container, '[data-testid="assessment-error"]')), 'as_of cannot be in the future.'); ok(!q(m.container, '[data-testid="assessment-headline"]')); ok(!buttonWith(m.container, 'ASSESSING'), 'button usable again'); }
    finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: drawing a different area clears the old assessment instead of leaving it under the new AOI', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(); const m = await mount(panel(AOI));
    try {
      await runAssessment(m); ok(q(m.container, '[data-testid="assessment-headline"]'));
      await m.rerender(panel(AOI2)); await flush(20);
      ok(!q(m.container, '[data-testid="assessment-headline"]'), 'old result gone'); ok(!buttonWith(m.container, 'FULL REPORT'), 'old PDF button gone');
      await runAssessment(m); eq(n.of('/agri/risk-assessment').pop().body, { aoi_geojson: AOI2 }, 'the new area is what gets assessed');
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: the PDF button posts the same AOI and downloads a PDF named by the as-of date', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(), dl = stubDownload(); const m = await mount(panel());
    try {
      await runAssessment(m); await click(buttonWith(m.container, 'FULL REPORT WITH ALL METRICS (PDF)')); await flush(30);
      const p = n.of('/risk-assessment/pdf'); eq(p.length, 1); eq(p[0].method, 'POST'); has(p[0].u, '/api/v1/agri/risk-assessment/pdf'); eq(p[0].body, { aoi_geojson: AOI });
      eq(dl.names.join(), `Vayu_risk_assessment_${FX.full.as_of}.pdf`); ok(!q(m.container, '[data-testid="pdf-error"]')); ok(q(m.container, '[data-testid="assessment-headline"]'), 'assessment stays on screen');
    } finally { await m.unmount(); n.restore(); dl.restore(); }
  });

  test('Assessment: a failed PDF shows the server message, downloads nothing, and the assessment stays', async () => {
    route('/agri/risk-assessment', FX.full); const n = net({ pdfStatus: 500, pdfDetail: 'Could not build the PDF report.' }), dl = stubDownload(); const m = await mount(panel());
    try {
      await runAssessment(m); await click(buttonWith(m.container, 'FULL REPORT')); await flush(30);
      has(text(q(m.container, '[data-testid="pdf-error"]')), 'Could not build the PDF report.'); eq(dl.names.length, 0); ok(q(m.container, '[data-testid="assessment-headline"]')); ok(!buttonWith(m.container, 'PREPARING'));
    } finally { await m.unmount(); n.restore(); dl.restore(); }
  });

  test('Assessment: the Score tab has ONE assessment and ONE report: the old score button, card and report download are gone', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(), dl = stubDownload(); const m = await mount(panel());
    try {
      const t = text(m.container); lacks(t, 'RUN RISK SCORE'); lacks(t, 'DOWNLOAD REPORT (PDF)'); ok(!buttonWith(m.container, 'RUN RISK SCORE'), 'no legacy score button');
      await runAssessment(m);
      eq(n.of('/agri/risk-score').length, 0, 'the legacy score endpoint is never called from this tab'); lacks(text(m.container), 'DOWNLOAD REPORT (PDF)');
      const pdfButtons = qa(m.container, 'button').filter((b) => /PDF/.test(text(b))); eq(pdfButtons.length, 1, 'exactly one PDF button'); has(text(pdfButtons[0]), 'FULL REPORT WITH ALL METRICS');
      await click(pdfButtons[0]); await flush(30); eq(n.of('/report/agri-risk').length, 0, 'the removed report endpoint is never requested'); eq(n.of('/risk-assessment/pdf').length, 1);
    } finally { await m.unmount(); n.restore(); dl.restore(); }
  });

  test('Assessment: the original three indicators are shown in full inside the one assessment, with weights, sub-scores and raw metrics', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(); const m = await mount(panel());
    try {
      await runAssessment(m); const o = q(m.container, '[data-testid="original-indicators"]'); ok(o, 'original indicators block');
      has(text(o), 'Composite 42/100 (moderate), confidence 80%'); has(text(o), 'elevated drought stress and declining soil moisture'); has(text(o), 'alerts and the watchlist use');
      const d = text(q(m.container, '[data-testid="original-drought"]')); has(d, 'Drought extent (NDDI): 45/100'); has(d, 'weight 40%'); has(d, '2.7 km² (27% of the area)'); has(d, 'Share of the assessable area under drought: 45%'); has(d, 'Area where NDDI could be computed: 60%'); has(d, '0.31 / 0.52 / 0.21');
      const v = text(q(m.container, '[data-testid="original-vegetation_loss"]')); has(v, '30/100'); has(v, 'weight 35%'); has(v, '1.2 km² (30% of the initial vegetated area)'); has(v, 'Vegetation gained: 0.4 km²');
      const s2 = text(q(m.container, '[data-testid="original-moisture_deficit"]')); has(s2, '52.5/100'); has(s2, 'weight 25%'); has(s2, '0.24 / 0.19 m³/m³'); has(s2, 'Dry-stress share (below 0.1 m³/m³): 30% (3 km²)');
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: an original indicator that could not be scored says why instead of showing a number', async () => {
    route('/agri/risk-assessment', FX.legacy_not_assessed); const n = net(); const m = await mount(panel());
    try {
      await runAssessment(m); const d = text(q(m.container, '[data-testid="original-drought"]'));
      has(d, 'Drought extent (NDDI): not assessed'); has(d, 'only 5% of the area could be scored'); lacks(d, '45/100'); has(text(q(m.container, '[data-testid="original-vegetation_loss"]')), '30/100');
    } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: a barren area shows "no vegetation at the start" instead of a measured 0% loss', async () => {
    const fx = JSON.parse(JSON.stringify(FX.full)); Object.assign(fx.legacy.raw_metrics.vegetation, { loss_pct: null, no_baseline_vegetation: true, initial_vegetation_km2: 0, vegetation_loss_km2: 0 });
    route('/agri/risk-assessment', fx); const n = net(); const m = await mount(panel());
    try { await runAssessment(m); const v = text(q(m.container, '[data-testid="original-vegetation_loss"]')); has(v, 'No vegetation was detected at the start of the period'); has(v, 'not a verified 0%'); has(v, 'n/a of the initial vegetated area'); lacks(v, '0% of the initial'); }
    finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: no original-indicator block when the original score was unavailable', async () => {
    route('/agri/risk-assessment', FX.empty); const n = net(); const m = await mount(panel());
    try { await runAssessment(m); ok(!q(m.container, '[data-testid="original-indicators"]')); } finally { await m.unmount(); n.restore(); }
  });

  test('Assessment: the searched place name is sent with the PDF request only (never with the assessment request)', async () => {
    route('/agri/risk-assessment', FX.full); const n = net(), dl = stubDownload(); const m = await mount(<AgriPanel drawnAOI={AOI} apiUrl="" searchedRegionName="Sikar" />);
    try {
      await runAssessment(m); eq(n.of('/agri/risk-assessment').filter((c) => !c.u.includes('/pdf'))[0].body, { aoi_geojson: AOI });
      await click(buttonWith(m.container, 'FULL REPORT WITH ALL METRICS (PDF)')); await flush(30); eq(n.of('/risk-assessment/pdf')[0].body, { aoi_geojson: AOI, region_name: 'Sikar' });
    } finally { await m.unmount(); n.restore(); dl.restore(); }
  });
}
