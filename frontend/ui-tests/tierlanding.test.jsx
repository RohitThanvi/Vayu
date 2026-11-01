import { test, mount, click, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import TierLanding from '../src/components/TierLanding.jsx';
import { KNOWN_TIERS, parseLockedTier, getLockedTier, initialTier } from '../src/lib/tierConfig.js';
import { TIER_LANDINGS, getTierLanding } from '../src/lib/tierLandingConfigs.js';

const AGRI = TIER_LANDINGS.agri;
const withWidth = async (w, fn) => {
  const prev = window.innerWidth;
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true });
  try { await fn(); } finally { Object.defineProperty(window, 'innerWidth', { value: prev, configurable: true, writable: true }); }
};

export function register() {
  test('Tier lock: parseLockedTier accepts only known tiers (case/space tolerant), never throws', async () => {
    for (const t of KNOWN_TIERS) eq(parseLockedTier(t), t, `known tier ${t}`);
    eq(parseLockedTier('  AGRI '), 'agri'); eq(parseLockedTier('Remote_Sensing'), 'remote_sensing');
    for (const bad of ['', 'nonsense', 'agri,business', undefined, null, 5, {}, '__proto__', 'constructor']) eq(parseLockedTier(bad), null, `rejects ${String(bad)}`);
    eq(getLockedTier(), null, 'no VITE_LOCKED_TIER in the test build means an unlocked site');
  });

  test('Tier lock: initialTier — a locked site ignores localStorage, the open site restores only valid picks', async () => {
    eq(initialTier('agri', 'full'), 'agri', 'stale stored tier cannot override the lock');
    eq(initialTier('agri', null), 'agri');
    eq(initialTier(null, 'business'), 'business'); eq(initialTier(null, null), null);
    eq(initialTier(null, 'garbage'), null, 'an unknown stored value shows the picker rather than a broken app');
  });

  test('Landing configs: lookup is own-property only; agri has the content + honesty sections', async () => {
    eq(getTierLanding('agri').id, 'agri'); eq(getTierLanding('business'), null); eq(getTierLanding('constructor'), null); eq(getTierLanding('toString'), null);
    for (const [id, c] of Object.entries(TIER_LANDINGS)) {
      eq(c.id, id, 'config id matches its key'); ok(KNOWN_TIERS.includes(id), 'config is for a known tier');
      for (const k of ['bg', 'surface', 'border', 'text', 'accent', 'accentBright', 'onAccent']) ok(c.palette[k], `palette.${k}`);
      ok(c.meta.title && c.meta.description && /^#[0-9a-f]{6}$/i.test(c.meta.themeColor), 'meta complete');
      ok(c.trust.notYet.length > 0, 'a landing page must state what is NOT validated');
      for (const r of c.seasons.rows) ok(r.from >= 0 && r.from <= 11 && r.to >= 0 && r.to <= 11, `season ${r.name} months in range`);
    }
  });

  test('TierLanding (agri): renders product, hero, every capability, and the Open button calls onEnter once', async () => {
    let entered = 0;
    const m = await mount(<TierLanding config={AGRI} onEnter={() => { entered++; }} />);
    try {
      const t = text(m.container);
      has(t, 'Vayu Agri'); has(t, AGRI.hero.highlight); has(t, 'Illustrative tile, not live data');
      for (const cap of AGRI.capabilities) has(t, cap.title);
      eq(q(m.container, '[data-testid="tier-landing"]').dataset.tier, 'agri');
      ok(!q(m.container, 'canvas'), '2D page: no WebGL canvas');
      ok(qa(m.container, 'svg rect').length >= 64 * 40, 'vegetation tile rendered');
      await click(buttonWith(m.container, AGRI.hero.cta)); eq(entered, 1);
      await click(buttonWith(m.container, 'Open app')); eq(entered, 2);
    } finally { await m.unmount(); }
  });

  test('TierLanding: trust section lists both what is in place and what is NOT validated', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      has(text(q(m.container, '[data-testid="trust-done"]')), 'held-out');
      const no = text(q(m.container, '[data-testid="trust-not-yet"]'));
      has(no, 'No accuracy figure'); has(no, 'not calibrated'); has(no, 'documented real-world events');
      lacks(text(m.container), 'Vayu Business', 'locked page must not advertise other tiers'); lacks(text(m.container), 'Remote Sensing');
    } finally { await m.unmount(); }
  });

  test('TierLanding: season bars — wrap-around Rabi is drawn as two segments at the right months', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const rects = (name) => qa(m.container, `rect[data-season="${name}"]`);
      eq(rects('Kharif').length, 1); eq(rects('Zaid').length, 1); eq(rects('Rabi').length, 2, 'Nov-Mar wraps the year end');
      const w = (r) => parseFloat(r.getAttribute('width')); const x = (r) => parseFloat(r.getAttribute('x'));
      const [late, early] = rects('Rabi').sort((a, b) => x(b) - x(a));       // Nov-Dec (right), Jan-Mar (left)
      ok(Math.abs(w(early) / w(late) - 3 / 2) < 0.1, 'Jan-Mar is 3 months wide, Nov-Dec is 2');
      ok(x(rects('Kharif')[0]) < x(late) && x(early) < x(rects('Kharif')[0]), 'Kharif sits between the two Rabi segments');
      has(text(m.container), 'jowar'); has(text(m.container), 'wheat only in Rabi');
    } finally { await m.unmount(); }
  });

  test('TierLanding: nav buttons scroll to their section; mobile shows a menu that opens and closes', async () => {
    const scrolled = []; const proto = window.HTMLElement.prototype; const prev = proto.scrollIntoView;
    proto.scrollIntoView = function () { scrolled.push(this); };
    try {
      let m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
      try { await click(buttonWith(m.container, 'Trust')); eq(scrolled.length, 1, 'Trust nav scrolls once'); has(text(scrolled[0]), 'What is checked'); } finally { await m.unmount(); }
      await withWidth(500, async () => {
        m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
        try {
          ok(!buttonWith(m.container, 'Trust'), 'desktop nav hidden on mobile');
          await click(q(m.container, '[aria-label="Open menu"]'));
          await click(buttonWith(m.container, 'Seasons')); eq(scrolled.length, 2);
          ok(q(m.container, '[aria-label="Open menu"]'), 'menu closes after choosing a section');
        } finally { await m.unmount(); }
      });
    } finally { proto.scrollIntoView = prev; }
  });
}
