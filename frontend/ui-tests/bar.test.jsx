import { act } from 'react';
import { test, mount, flush, click, route, calls, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import BusinessIntelBar from '../src/components/BusinessIntelBar.jsx';

const mkAlerts = (over = {}) => ({ unread: 2, markRead: () => { mkAlerts.marked = (mkAlerts.marked || 0) + 1; }, rules: [], history: [], status: { lastChecked: null, error: null, waiting: [] }, permission: 'unsupported',
  addRule: () => null, removeRule() {}, toggleRule() {}, clearHistory() {}, requestPermission: async () => 'denied', checkNow: async () => {}, ...over });
const wire = () => {
  // shapes the existing Live view expects
  route('/intel/dark-vessels', { flags: [] });
  route('/intel/sanctions-screen', { list_status: { loaded: false }, hits: [] });
  route('/intel/macro', { us: {}, global: {}, india: {}, fred_configured: true });
  route('/intel/geo-tone', { regions: [] });
  route('/intel/commodities', { commodities: [], data_quality: null });
  route('/intel/vessels/chokepoints', { chokepoints: [] });
  // shapes the existing Economics (bloc) view expects
  route('/intel/economic-blocs', { blocs: [] });
  route('/intel/economic-blocs/', (u) => (u.endsWith('/macro') ? { countries: [] } : u.endsWith('/tone') ? { sample_headlines: [], avg_tone: null } : u.endsWith('/commodities') ? { commodities: [] } : { filings: [] }));
  route('/intel/business-risk', { chokepoints: [{ chokepoint: 'strait_of_hormuz', chokepoint_display: 'Strait of Hormuz', score: 41, band: 'elevated' }] });
  route('/intel/markets', { indices: [], sectors: [], fx: [], stocks: [], data_quality: { source: 'Yahoo', age_seconds: 60, stale: false } });
  route('/intel/quotes', { quotes: [], errors: {}, data_quality: null });
  route('/intel/quote-history', { symbol: 'X', points: [] });
  route('/intel/india-risk', { indicators: [], data_quality: { source: 'Yahoo', age_seconds: 1, stale: false, last_error: null } });
  route('/intel/yield-curve', { error: 'x', data_quality: { source: 'FRED', age_seconds: 1, stale: false } });
  route('/intel/asset-profile', { kind: 'site', id: 'jebel_ali', name: 'Jebel Ali Port', site_type: 'port', note: 'n', signals: [], recent_news: [], linked_chokepoints: [], related_tickers: [], notes: [] });
};
const fire = async (detail) => { await act(async () => { window.dispatchEvent(new window.CustomEvent('vayu:business-command', { detail })); }); await flush(30); };

export function register() {
  test('Bar: tab strip, unread badge, and the daily-brief download link', async () => {
    wire();
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    const t = text(c);
    for (const label of ['Live', 'Markets', 'Analysis', 'Economics', 'Alerts (2)']) has(t, label);
    const brief = qa(c, 'a').find((a) => a.textContent.includes('Daily brief'));
    ok(brief && brief.getAttribute('href') === '/api/v1/report/business-brief' && brief.hasAttribute('download'), 'brief link');
  });
  test('Bar: opening the Alerts tab marks alerts as read and shows the editor', async () => {
    wire(); mkAlerts.marked = 0;
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    await click(buttonWith(c, 'Alerts (2)'));
    eq(mkAlerts.marked, 1); has(text(c), 'New alert'); has(text(c), 'How alerts work');
  });
  test('Bar: a command issued BEFORE the bar mounted (from another tab) is applied once, then cleared', async () => {
    wire();
    window.__vayuBusinessCommand = { type: 'business', view: 'markets', symbol: 'tcs.ns' };
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    has(text(c), 'Watchlist');
    ok(calls.some((u) => u.includes('quote-history?symbol=TCS.NS')), 'the requested symbol is charted');
    eq(window.__vayuBusinessCommand, null, 'consumed, so a later mount cannot replay it');
  });
  test('Bar: commands while already open switch view; Economics shows India + yield curve', async () => {
    wire();
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    await fire({ view: 'economics' });
    has(text(c), 'India · food & fuel pressure'); has(text(c), 'US Treasury yield curve');
    await fire({ view: 'alerts' }); has(text(c), 'New alert');
  });
  test('Bar: a site profile command opens the one-page profile over the current view, and it can be closed', async () => {
    wire();
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    await fire({ view: 'markets', asset: { kind: 'site', id: 'jebel_ali' } });
    ok(calls.some((u) => u.includes('kind=site&id=jebel_ali')), 'profile requested'); has(text(c), 'Jebel Ali Port');
    await click(buttonWith(c, 'Close'));
    lacks(text(c), 'Strategic site'); has(text(c), 'Watchlist');
  });
  test('Bar: the listener is removed on unmount (no crash on a late command)', async () => {
    wire();
    const m = await mount(<BusinessIntelBar apiUrl="" alerts={mkAlerts()} />);
    await m.unmount();
    await fire({ view: 'markets' });
  });
  test('Bar: without an alerts object the other views still work (graceful)', async () => {
    wire();
    const { container: c } = await mount(<BusinessIntelBar apiUrl="" />);
    has(text(c), 'Alerts'); await click(buttonWith(c, 'Markets')); has(text(c), 'Watchlist');
  });
}
