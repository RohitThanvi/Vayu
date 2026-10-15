import { useEffect } from 'react';
import { test, mount, flush, click, typeInto, submit, route, calls, text, q, qa, byLabel, buttonWith, key, eq, ok, has, lacks } from './harness.jsx';
import { act } from 'react';
import { useAlerts } from '../src/hooks/useAlerts.js';
import AlertsView from '../src/components/business/AlertsView.jsx';
import CommandBar from '../src/components/CommandBar.jsx';

let SNAP;
const snap = (value, stale = false, extra = {}) => ({ as_of: 't', feed_id: 'f', quotes: { 'BZ=F': { value, change_pct: 1, as_of: 1, stale } }, risk: {}, sites: {}, ...extra });
function Harness({ holder, onFire }) {
  const alerts = useAlerts('', onFire);
  useEffect(() => { holder.alerts = alerts; });
  return <AlertsView apiUrl="" alerts={alerts} />;
}
const addRule = async (c, symbol, level) => {
  await typeInto(byLabel(c, 'Symbol'), symbol); await typeInto(byLabel(c, 'Price level'), level);
  await submit(q(c, 'form'));
};

export function register() {
  test('Alerts: full lifecycle — arm, fire on crossing, cooldown, stale data, toggle, delete', async () => {
    SNAP = snap(80); route('/intel/alerts-snapshot', () => SNAP); route('/intel/strategic-sites', []);
    const holder = {}; const fired = [];
    const { container: c } = await mount(<Harness holder={holder} onFire={(t) => fired.push(t)} />);
    has(text(c), 'No alerts yet');
    await addRule(c, 'bz=f', '85');
    has(text(c), 'BZ=F rises above 85'); has(text(c), 'Nothing has fired yet');
    ok(calls.some((u) => u.includes('alerts-snapshot?symbols=BZ%3DF')), 'the watched symbol is requested from the server');
    SNAP = snap(90);
    await act(async () => { await holder.alerts.checkNow(); }); await flush();
    eq(fired.length, 1, 'fires when it crosses'); has(text(c), 'BZ=F rises above 85 — now 90'); eq(holder.alerts.unread, 1);
    await act(async () => { await holder.alerts.checkNow(); }); await flush();
    eq(fired.length, 1, 'does not repeat while still above (cooldown)');
    SNAP = snap(70, true);
    await act(async () => { await holder.alerts.checkNow(); }); await flush();
    has(text(c), 'waiting for fresh data'); eq(fired.length, 1, 'stale data never fires');
    await click(byLabel(c, 'Enable BZ=F rises above 85'));
    SNAP = snap(60); await act(async () => { await holder.alerts.checkNow(); });
    SNAP = snap(95); await act(async () => { await holder.alerts.checkNow(); }); await flush();
    eq(fired.length, 1, 'a disabled rule never fires');
    await click(byLabel(c, 'Delete alert')); has(text(c), 'No alerts yet');
  });
  test('Alerts: a rule whose condition is already true is armed silently (no instant notification)', async () => {
    SNAP = snap(90); route('/intel/alerts-snapshot', () => SNAP); route('/intel/strategic-sites', []);
    const holder = {}; const fired = [];
    const { container: c } = await mount(<Harness holder={holder} onFire={(t) => fired.push(t)} />);
    await addRule(c, 'BZ=F', '85'); await flush(40);
    eq(fired.length, 0, 'adding a rule must not notify about what is already visible'); has(text(c), 'Nothing has fired yet');
    SNAP = snap(80); await act(async () => { await holder.alerts.checkNow(); });
    SNAP = snap(90); await act(async () => { await holder.alerts.checkNow(); }); await flush();
    eq(fired.length, 1, 'it fires on the next genuine crossing');
  });
  test('Alerts: invalid input is rejected with a reason; rules persist across a reload', async () => {
    SNAP = snap(80); route('/intel/alerts-snapshot', () => SNAP); route('/intel/strategic-sites', []);
    const holder = {};
    const first = await mount(<Harness holder={holder} />);
    await addRule(first.container, 'a b', '85'); has(text(first.container), 'valid symbol');
    await addRule(first.container, 'BZ=F', 'abc'); has(text(first.container), 'numeric');
    await addRule(first.container, 'BZ=F', '85'); has(text(first.container), 'BZ=F rises above 85');
    ok(localStorage.getItem('vayu.alerts.rules.v1').includes('BZ=F'));
    await first.unmount();
    const second = await mount(<Harness holder={{}} />);
    has(text(second.container), 'BZ=F rises above 85', 'rule restored after reload');
  });
  test('Alerts: corrupted saved rules are dropped, not crashed on', async () => {
    localStorage.setItem('vayu.alerts.rules.v1', JSON.stringify([{ id: 'x', type: 'price_above', symbol: '<script>', level: 1 }, { id: 'y', type: 'nope' }, null, 5]));
    route('/intel/alerts-snapshot', () => snap(1)); route('/intel/strategic-sites', []);
    has(text((await mount(<Harness holder={{}} />)).container), 'No alerts yet');
  });
  test('Alerts: honest about where they run and what is not offered', async () => {
    route('/intel/alerts-snapshot', () => snap(1)); route('/intel/strategic-sites', []);
    const t = text((await mount(<Harness holder={{}} />)).container);
    has(t, 'in this browser'); has(t, 'never fires on stale or missing data'); has(t, 'Email and WhatsApp alerts are not offered yet');
  });
  test('Alerts: chokepoint and site rules evaluate from the snapshot', async () => {
    SNAP = snap(1, false, { risk: { strait_of_hormuz: { band: 'normal', score: 5, display: 'Strait of Hormuz' } }, sites: {} });
    route('/intel/alerts-snapshot', () => SNAP); route('/intel/strategic-sites', [{ id: 'jebel_ali', name: 'Jebel Ali Port' }]);
    const holder = {}; const fired = [];
    const { container: c } = await mount(<Harness holder={holder} onFire={(t) => fired.push(t)} />);
    await typeInto(byLabel(c, 'Alert type'), 'chokepoint_band'); await typeInto(byLabel(c, 'Risk band'), 'elevated'); await submit(q(c, 'form'));
    has(text(c), 'Strait of Hormuz risk reaches elevated');
    SNAP = snap(1, false, { risk: { strait_of_hormuz: { band: 'elevated', score: 41, display: 'Strait of Hormuz' } } });
    await act(async () => { await holder.alerts.checkNow(); }); await flush();
    eq(fired.length, 1); has(fired[0].message, 'Strait of Hormuz: elevated, score 41');
    await typeInto(byLabel(c, 'Alert type'), 'site_signal'); await flush(30);
    await typeInto(byLabel(c, 'Site'), 'jebel_ali'); await submit(q(c, 'form'));
    has(text(c), 'A signal is detected near Jebel Ali Port');
  });

  // ── Command bar ────────────────────────────────────────────────────────────
  const cmdMount = (props = {}) => { const log = { cmds: [], closed: 0 }; return mount(<CommandBar open tabs={['Analyze', 'Business', 'Weather']} apiUrl="http://api" onClose={() => { log.closed++; }} onCommand={(c) => log.cmds.push(c)} {...props} />).then((m) => ({ ...m, log })); };
  test('Command bar: filter, run with Enter, close on run', async () => {
    const { container: c, log } = await cmdMount();
    const input = byLabel(c, 'Type a command or a symbol');
    await typeInto(input, 'mark'); has(text(c), 'Business · Markets'); lacks(text(c), 'Go to Weather');
    await key(input, 'Enter');
    eq(log.cmds, [{ type: 'business', view: 'markets' }]); eq(log.closed, 1);
  });
  test('Command bar: a typed symbol offers chart and profile; arrows choose', async () => {
    const { container: c, log } = await cmdMount();
    const input = byLabel(c, 'Type a command or a symbol');
    await typeInto(input, 'reliance.ns'); has(text(c), 'Chart RELIANCE.NS'); has(text(c), 'One-page profile: RELIANCE.NS');
    await key(input, 'ArrowDown'); await key(input, 'Enter');
    eq(log.cmds[0], { type: 'business', view: 'markets', symbol: 'RELIANCE.NS', asset: { kind: 'ticker', id: 'RELIANCE.NS' } });
  });
  test('Command bar: Escape closes; downloads point at the API; unknown input says so', async () => {
    const { container: c, log } = await cmdMount();
    const input = byLabel(c, 'Type a command or a symbol');
    await key(input, 'Escape'); eq(log.closed, 1);
    await typeInto(input, 'brief'); await key(input, 'Enter');
    eq(log.cmds[0], { type: 'download', url: 'http://api/api/v1/report/business-brief' });
    await typeInto(input, 'zzz qqq'); has(text(c), 'No matching command');
  });
  test('Command bar: tiers without Business get no Business commands or symbol actions; closed renders nothing', async () => {
    const { container: c } = await cmdMount({ tabs: ['Analyze', 'Agri'] });
    await typeInto(byLabel(c, 'Type a command or a symbol'), 'markets'); has(text(c), 'No matching command');
    await typeInto(byLabel(c, 'Type a command or a symbol'), 'AAPL'); lacks(text(c), 'Chart AAPL');
    const closed = await mount(<CommandBar open={false} tabs={['Business']} apiUrl="" onClose={() => {}} onCommand={() => {}} />);
    eq(text(closed.container), '');
  });
}
