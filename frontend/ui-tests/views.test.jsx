import { test, mount, flush, click, typeInto, submit, route, calls, text, q, qa, byLabel, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import MarketsView from '../src/components/business/MarketsView.jsx';
import IndiaRiskPanel from '../src/components/business/IndiaRiskPanel.jsx';
import YieldCurvePanel from '../src/components/business/YieldCurvePanel.jsx';
import AssetProfile from '../src/components/business/AssetProfile.jsx';

const Q = (symbol, name, value, change_pct, extra = {}) => ({ symbol, name, value, change_pct, prev_close: value, market_time: 1700000000, ...extra });
const DQ = { source: 'Yahoo Finance (unofficial chart API)', age_seconds: 300, stale: false, delay_note: 'Typically 15–20 minutes delayed', last_error: null };
const MARKETS = { indices: [Q('^NSEI', 'Nifty 50', 25000, 0.4), Q('^GSPC', 'S&P 500', 6800, -0.2)],
  sectors: [Q('^CNXIT', 'Nifty IT', 38000, 1.8), Q('^NSEBANK', 'Nifty Bank', 55000, -0.7, { stale: true }), Q('^CNXAUTO', 'Nifty Auto', 26000, 0.3)],
  fx: [Q('INR=X', 'USD/INR', 88.1, 0.1)], stocks: [], data_quality: DQ };
const QUOTES = { quotes: [Q('^NSEI', 'Nifty 50', 25000, 0.4), Q('INR=X', 'USD/INR', 88.1, 0.1), Q('^GSPC', 'S&P 500', 6800, -0.2)], errors: { '^BSESN': 'no quote available', 'BZ=F': 'no quote available' }, data_quality: DQ };
const HISTORY = { symbol: '^NSEI', name: 'Nifty 50', range: '1y', points: Array.from({ length: 60 }, (_, i) => ({ date: 1700000000 + 86400 * i, value: 100 + i })) };
const wire = () => { route('/intel/markets', MARKETS); route('/intel/quotes', (u) => ({ ...QUOTES, quotes: QUOTES.quotes })); route('/intel/quote-history', HISTORY); };

export function register() {
  // ── Markets ────────────────────────────────────────────────────────────────
  test('Markets: default watchlist, partial quotes, sector heatmap and stamps', async () => {
    wire();
    const { container: c } = await mount(<MarketsView apiUrl="" />);
    const t = text(c);
    has(t, 'Watchlist'); has(t, '5/12'); has(t, 'Nifty 50'); has(t, 'USD/INR');
    eq(t.split('no quote available').length - 1, 2, 'two symbols without a quote are labelled, not hidden');
    has(t, '3 of 10 available', 'heatmap states how many sectors are available');
    ok(q(c, '[title*="last good value"]'), 'the stale sector tile is flagged');
    has(t, 'Yahoo Finance (unofficial chart API) · 5 min old');
    ok(!t.includes('STALE'), 'fresh data is not labelled stale');
    ok(qa(c, 'a').some((a) => a.getAttribute('href').endsWith('/api/v1/intel/export/markets.csv')), 'CSV export link');
    ok(calls.some((u) => u.includes('/quote-history?symbol=%5ENSEI&range=1y')), 'first symbol is charted');
  });
  test('Markets: stale data banner, quotes failure keeps screen usable', async () => {
    route('/intel/markets', { ...MARKETS, data_quality: { ...DQ, stale: true, age_seconds: 9000 } });
    route('/intel/quotes', new Error('network down')); route('/intel/quote-history', HISTORY);
    const { container: c } = await mount(<MarketsView apiUrl="" />);
    has(text(c), 'STALE'); has(text(c), 'Quotes unavailable right now');
  });
  test('Markets: add, validate, de-duplicate, cap at 12, remove, persist', async () => {
    wire();
    const { container: c } = await mount(<MarketsView apiUrl="" />);
    const add = async (v) => { await typeInto(byLabel(c, 'Add symbol'), v); await submit(q(c, 'form')); };
    await add('reliance.ns');
    ok(JSON.parse(localStorage.getItem('vayu.watchlist.v1')).includes('RELIANCE.NS'), 'persisted upper-cased');
    ok(calls.some((u) => u.includes('RELIANCE.NS')), 'new symbol is fetched');
    await add('a b'); has(text(c), 'Use a Yahoo symbol');
    await add('reliance.ns'); has(text(c), 'Already on your watchlist');
    for (const s of ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF']) await add(s);
    has(text(c), '12/12');
    await add('OVERFLOW'); has(text(c), 'holds up to 12');
    ok(!JSON.parse(localStorage.getItem('vayu.watchlist.v1')).includes('OVERFLOW'));
    await click(byLabel(c, 'Remove ^GSPC'));
    ok(!JSON.parse(localStorage.getItem('vayu.watchlist.v1')).includes('^GSPC'), 'removed and persisted');
  });
  test('Markets: corrupted or hostile saved watchlist falls back safely', async () => {
    localStorage.setItem('vayu.watchlist.v1', '["<img src=x onerror=alert(1)>","ok"]');
    wire();
    const { container: c } = await mount(<MarketsView apiUrl="" />);
    lacks(c.innerHTML, '<img src=x', 'hostile entry never rendered'); has(text(c), '^NSEI');
    localStorage.setItem('vayu.watchlist.v1', 'not json');
    const m2 = await mount(<MarketsView apiUrl="" />);
    has(text(m2.container), '^NSEI');
  });
  test('Markets: a command selects (charts) the requested symbol', async () => {
    wire();
    const m = await mount(<MarketsView apiUrl="" command={null} />);
    await m.rerender(<MarketsView apiUrl="" command={{ symbol: 'tcs.ns', n: 1 }} />);
    ok(calls.some((u) => u.includes('quote-history?symbol=TCS.NS')), 'TCS.NS charted');
    ok(buttonWith(m.container, 'Add to watchlist'), 'offers to add a symbol that is not on the list');
  });
  test('Markets: the profile button opens an asset profile for the selected symbol', async () => {
    wire(); let opened = null;
    const { container: c } = await mount(<MarketsView apiUrl="" onOpenAsset={(k, i) => { opened = [k, i]; }} />);
    await click(buttonWith(c, 'One-page profile'));
    eq(opened, ['ticker', '^NSEI']);
  });

  // ── India panel ────────────────────────────────────────────────────────────
  const IND = (id, label, latest, pos, pct) => ({ id, label, symbol: id, unit: 'USD', group: 'fuel', why: 'because', latest, as_of: 1, change_1m_pct: 2.1, change_3m_pct: -1.5, change_1y_pct: 10, percentile_12m: pct, position: pos, low_12m: 1, high_12m: 2, sparkline: [1, 2, 3, 2, 4] });
  test('India panel: indicators, chokepoints, method — and no composite index anywhere', async () => {
    route('/intel/india-risk', { indicators: [IND('brent', 'Brent crude', 82.4, 'top fifth of its 12-month range', 91), IND('usdinr', 'USD/INR', 88.1, 'middle of its 12-month range', 50)], expected: 5,
      chokepoints: [{ chokepoint: 'strait_of_hormuz', display: 'Strait of Hormuz', band: 'elevated', score: 41 }], method: 'Each indicator shows where today sits within its own last 12 months. There is deliberately no combined index.', data_quality: { ...DQ, last_error: 'only 2/5 indicators available this cycle' } });
    const { container: c } = await mount(<IndiaRiskPanel apiUrl="" />);
    const t = text(c);
    has(t, 'Brent crude'); has(t, 'top fifth of its 12-month range'); has(t, 'Strait of Hormuz · elevated'); has(t, 'no combined index'); has(t, 'partial');
    ok(q(c, '[aria-label="Percentile 91"]'), 'percentile gauge rendered');
    lacks(t.toLowerCase(), 'risk score', 'no composite score is shown');
  });
  test('India panel: empty and failed states are honest', async () => {
    route('/intel/india-risk', { indicators: [], data_quality: { ...DQ, last_error: 'no indicator data available this cycle', stale: true } });
    has(text((await mount(<IndiaRiskPanel apiUrl="" />)).container), 'No indicator data available this cycle');
    route('/intel/india-risk', new Error('down'));
    has(text((await mount(<IndiaRiskPanel apiUrl="" />)).container), 'unavailable right now');
  });

  // ── Yield curve ────────────────────────────────────────────────────────────
  const CURVE = { as_of: '2026-10-09', tenors: ['3M', '2Y', '10Y'], curves: { latest: { '3M': 4.6, '2Y': 4.4, '10Y': 4.1 }, one_month_ago: { '3M': 4.7, '2Y': 4.5, '10Y': null }, one_year_ago: { '3M': 5.2, '2Y': 4.8, '10Y': 4.5 } },
    spreads: { '10y_minus_2y': { value: -0.3, inverted: true }, '10y_minus_3m': { value: -0.5, inverted: true } }, spread_10y_2y_history: [{ date: '2026-01-01', value: 0.1 }, { date: '2026-10-09', value: -0.3 }], data_quality: { ...DQ, source: 'FRED' } };
  test('Yield curve: three curves, inversion flagged, gap in a curve does not break it, no forecast language', async () => {
    route('/intel/yield-curve', CURVE);
    const { container: c } = await mount(<YieldCurvePanel apiUrl="" />);
    const t = text(c);
    has(t, '-0.30 pp · inverted'); has(t, '-0.50 pp · inverted'); has(t, 'As of 2026-10-09'); has(t, 'no recession probability');
    eq(qa(c, 'svg[role="img"] path').length, 3, 'one path per curve');
    ok(qa(c, 'a').some((a) => a.getAttribute('href').endsWith('/export/yield-curve.csv')));
  });
  test('Yield curve: error payload handled', async () => {
    route('/intel/yield-curve', { error: 'yield curve unavailable this cycle', data_quality: { ...DQ, stale: true } });
    has(text((await mount(<YieldCurvePanel apiUrl="" />)).container), 'unavailable right now');
  });

  // ── Asset profile ──────────────────────────────────────────────────────────
  test('Asset profile (ticker): quote, statistics, linked chokepoints, notes; close works', async () => {
    route('/intel/asset-profile', { kind: 'ticker', id: 'XOM', name: 'ExxonMobil', quote: { value: 110.5, change_pct: 1.2, currency: 'USD', exchange: 'NYSE' },
      stats: { n_days: 250, return_1m_pct: 2, return_3m_pct: 5, return_1y_pct: 12, volatility_annualised_pct: 21.3, max_drawdown_pct: -14.2, position_in_52w_range_pct: 70, low_52w: 90, high_52w: 120 },
      linked_chokepoints: [{ chokepoint: 'strait_of_hormuz', display: 'Strait of Hormuz', band: 'elevated', score: 41 }], notes: ['Statistics are descriptive. They are not a forecast.'] });
    let closed = false;
    const { container: c } = await mount(<AssetProfile apiUrl="" kind="ticker" id="XOM" onClose={() => { closed = true; }} />);
    const t = text(c);
    has(t, 'ExxonMobil'); has(t, 'Max drawdown'); has(t, '-14.20%'); has(t, 'Strait of Hormuz · elevated (41)'); has(t, 'not a forecast'); has(t, '52-week range');
    ok(calls.some((u) => u.includes('kind=ticker&id=XOM')));
    await click(buttonWith(c, 'Close')); ok(closed);
  });
  test('Asset profile (ticker): short history says so instead of showing numbers', async () => {
    route('/intel/asset-profile', { kind: 'ticker', id: 'NEW', name: 'NEW', quote: null, stats: { n_days: 10, volatility_annualised_pct: null }, linked_chokepoints: [], notes: [] });
    const t = text((await mount(<AssetProfile apiUrl="" kind="ticker" id="NEW" onClose={() => {}} />)).container);
    has(t, 'Not enough price history'); has(t, 'No quote available right now');
  });
  test('Asset profile (site): signals, honest empty state, related tickers navigate, news links are safe', async () => {
    const SITE = { kind: 'site', id: 'jebel_ali', name: 'Jebel Ali Port', site_type: 'port', note: 'Largest port in the Middle East.', signals: [{ kind: 'seismic', note: 'M5.1 quake 80 km away', severity: 'warn' }],
      recent_news: [{ title: 'Port expansion <b>announced</b>', url: 'https://example.com/a', domain: 'example.com' }], linked_chokepoints: [{ chokepoint: 'strait_of_hormuz', display: 'Strait of Hormuz', band: 'normal', score: 9 }], related_tickers: ['XOM', 'CVX'], notes: ['n'] };
    route('/intel/asset-profile', SITE);
    let nav = null;
    const { container: c } = await mount(<AssetProfile apiUrl="" kind="site" id="jebel_ali" onClose={() => {}} onOpenAsset={(k, i) => { nav = [k, i]; }} />);
    has(text(c), 'M5.1 quake 80 km away'); has(text(c), 'Related bellwether tickers');
    lacks(c.innerHTML, '<b>announced</b>', 'headline markup is escaped, not injected');
    ok(q(c, 'a[href="https://example.com/a"]'));
    await click(buttonWith(c, 'XOM')); eq(nav, ['ticker', 'XOM']);
    route('/intel/asset-profile', { ...SITE, signals: [], related_tickers: [], recent_news: [] });
    has(text((await mount(<AssetProfile apiUrl="" kind="site" id="jebel_ali" onClose={() => {}} />)).container), 'None detected near this site');
  });
  test('Asset profile: load failure is reported', async () => {
    route('/intel/asset-profile', { __status: 404 });
    has(text((await mount(<AssetProfile apiUrl="" kind="site" id="nope" onClose={() => {}} />)).container), 'Could not load this profile');
  });
}
