import { act } from 'react';
import { test, mount, click, flush, text, q, qa, buttonWith, eq, ok, has, lacks } from './harness.jsx';
import TierLanding, { seasonMonths } from '../src/components/TierLanding.jsx';
import { KNOWN_TIERS, parseLockedTier, getLockedTier, initialTier } from '../src/lib/tierConfig.js';
import { TIER_LANDINGS, getTierLanding } from '../src/lib/tierLandingConfigs.js';

const AGRI = TIER_LANDINGS.agri;
const withWidth = async (w, fn) => {
  const prev = window.innerWidth;
  Object.defineProperty(window, 'innerWidth', { value: w, configurable: true, writable: true });
  try { await fn(); } finally { Object.defineProperty(window, 'innerWidth', { value: prev, configurable: true, writable: true }); }
};


// Fake IntersectionObserver so enter/exit behaviour can be driven deterministically.
function installFakeIO() {
  const instances = [];
  class FakeIO {
    constructor(cb, opts) { this.cb = cb; this.opts = opts; this.els = new Set(); instances.push(this); }
    observe(el) { this.els.add(el); } unobserve(el) { this.els.delete(el); } disconnect() { this.els.clear(); }
  }
  const prev = globalThis.IntersectionObserver; globalThis.IntersectionObserver = FakeIO;
  const fire = async (el, isIntersecting) => {
    await act(async () => { for (const io of instances) if (io.els.has(el)) io.cb([{ isIntersecting, target: el }]); });
  };
  return { fire, instances, restore: () => { globalThis.IntersectionObserver = prev; } };
}
const fx = (container, variant) => q(container, `[data-fx="${variant}"]`);
const setScrollTop = (el, v) => Object.defineProperty(el, 'scrollTop', { value: v, configurable: true, writable: true });

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

  test('Landing configs: lookup is own-property only; every config is complete', async () => {
    eq(getTierLanding('agri').id, 'agri'); eq(getTierLanding('business'), null); eq(getTierLanding('constructor'), null); eq(getTierLanding('toString'), null);
    for (const [id, c] of Object.entries(TIER_LANDINGS)) {
      eq(c.id, id, 'config id matches its key'); ok(KNOWN_TIERS.includes(id), 'config is for a known tier');
      for (const k of ['bg', 'surface', 'border', 'text', 'accent', 'accentBright', 'onAccent']) ok(c.palette[k], `palette.${k}`);
      ok(c.meta.title && c.meta.description && /^#[0-9a-f]{6}$/i.test(c.meta.themeColor), 'meta complete');
      ok(c.hero.image.src.startsWith('/') && c.hero.image.alt && c.hero.image.caption, 'hero image has src, alt and caption');
      for (const r of c.seasons.rows) ok(r.tag && r.window, `season ${r.name} has tag + window`);
      for (const r of c.seasons.rows) ok(r.from >= 0 && r.from <= 11 && r.to >= 0 && r.to <= 11, `season ${r.name} months in range`);
    }
  });

  test('TierLanding (agri): renders product, hero image, every capability; Open buttons call onEnter', async () => {
    let entered = 0;
    const m = await mount(<TierLanding config={AGRI} onEnter={() => { entered++; }} />);
    try {
      const t = text(m.container);
      has(t, 'Vayu Agri'); has(t, AGRI.hero.highlight); has(t, AGRI.hero.image.caption);
      for (const cap of AGRI.capabilities) has(t, cap.title);
      eq(q(m.container, '[data-testid="tier-landing"]').dataset.tier, 'agri');
      ok(!q(m.container, 'canvas'), '2D page: no WebGL canvas');
      const img = q(m.container, 'img[src="/images/agri-hero.jpg"]');
      ok(img && img.getAttribute('alt').length > 20, 'hero shows the real screenshot with alt text');
      lacks(t, 'Vayu Business', 'locked page must not advertise other tiers'); lacks(t, 'Remote Sensing'); lacks(t, 'Maritime');
      await click(buttonWith(m.container, AGRI.hero.cta)); eq(entered, 1);
      await click(buttonWith(m.container, 'Open app')); eq(entered, 2);
    } finally { await m.unmount(); }
  });

  test('TierLanding: trust section is gone (no nav entry, no section)', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      ok(!buttonWith(m.container, 'Trust'), 'no Trust nav button'); ok(!q(m.container, '[data-testid="trust-done"]'));
      lacks(text(m.container), 'What is checked');
    } finally { await m.unmount(); }
  });

  test('seasonMonths: handles plain and year-wrapping windows', async () => {
    eq(seasonMonths(5, 9).join(), '5,6,7,8,9'); eq(seasonMonths(10, 2).join(), '10,11,0,1,2'); eq(seasonMonths(2, 5).join(), '2,3,4,5'); eq(seasonMonths(3, 3).join(), '3');
  });

  test('TierLanding: season cards highlight exactly the right months (Rabi wraps Nov-Mar)', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const active = (name) => qa(m.container, `[data-season-month^="${name}-"]`).map((e) => Number(e.getAttribute('data-season-month').split('-')[1])).sort((a, b) => a - b);
      eq(active('Kharif').join(), '5,6,7,8,9'); eq(active('Rabi').join(), '0,1,2,10,11'); eq(active('Zaid').join(), '2,3,4,5');
      for (const name of ['Kharif', 'Rabi', 'Zaid']) {   // the colour must match the marked months, not just the attribute
        const cells = qa(q(m.container, `[data-testid="season-${name}"]`), 'div').filter((e) => e.style.height === '26px');
        eq(cells.length, 12, `${name}: 12 month cells`);
        const lit = cells.map((e, i) => (/255, 255, 255/.test(e.style.background) ? -1 : i)).filter((i) => i >= 0);
        eq(lit.join(), active(name).join(), `${name}: coloured cells are exactly the active months`);
      }
      has(text(q(m.container, '[data-testid="season-Rabi"]')), 'November to March'); has(text(q(m.container, '[data-testid="season-Kharif"]')), 'Monsoon');
      has(text(m.container), 'jowar'); has(text(m.container), 'wheat only in Rabi');
    } finally { await m.unmount(); }
  });


  test('Effects: ambient backdrop, cursor glow and the blurred Ken-Burns hero backdrop are rendered', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      ok(q(m.container, '[data-testid="ambient"]'), 'ambient backdrop'); eq(qa(m.container, '[data-testid="ambient"] circle').length, 4, 'pivot-ring watermark');
      ok(q(m.container, '[data-testid="cursor-glow"]'), 'cursor glow');
      const bg = q(m.container, '[data-testid="hero-bg"]');
      has(bg.style.backgroundImage, '/images/agri-bg.jpg'); ok(bg.className.includes('tl-hero-bg'), 'Ken Burns class');
      has(m.container.querySelector('style').textContent, '@keyframes tl-kenburns'); has(m.container.querySelector('style').textContent, 'prefers-reduced-motion');
    } finally { await m.unmount(); }
  });

  test('Effects: scrolling parallaxes the hero backdrop and fades/lifts the hero content', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const c = q(m.container, '[data-testid="tier-landing"]'); const content = q(m.container, '[data-testid="hero-content"]');
      eq(content.style.opacity, '1'); setScrollTop(c, 400);
      await act(async () => { c.dispatchEvent(new window.Event('scroll')); }); await flush(20);
      has(q(m.container, '[data-testid="hero-parallax"]').style.transform, 'translateY(30px)', 'backdrop moves at 0.3 x (scrollTop x 0.25)');
      ok(Math.abs(parseFloat(content.style.opacity) - (1 - 100 / 260)) < 0.01, `content fades (got ${content.style.opacity})`);
      has(content.style.transform, 'translateY(15px)');
      setScrollTop(c, 4000); await act(async () => { c.dispatchEvent(new window.Event('scroll')); }); await flush(20);
      eq(content.style.opacity, '0', 'fully faded far down the page, never negative');
    } finally { await m.unmount(); }
  });

  test('Effects: mouse drives the cursor glow and counter-drifts the hero text (desktop only)', async () => {
    let m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const c = q(m.container, '[data-testid="tier-landing"]'); const heroText = q(m.container, '[data-testid="hero-content"]').firstChild;
      await act(async () => { c.dispatchEvent(new window.MouseEvent('mousemove', { clientX: window.innerWidth, clientY: 0, bubbles: true })); }); await flush(20);
      eq(c.style.getPropertyValue('--mx'), '100.0%'); eq(c.style.getPropertyValue('--my'), '0.0%');
      eq(heroText.style.transform, 'translate(-7.0px, 4.0px)', 'opposite-direction drift');
    } finally { await m.unmount(); }
    await withWidth(500, async () => {
      m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
      try {
        const c = q(m.container, '[data-testid="tier-landing"]');
        await act(async () => { c.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 300, clientY: 100, bubbles: true })); }); await flush(20);
        eq(c.style.getPropertyValue('--mx'), '', 'no pointer effects on touch-width screens');
      } finally { await m.unmount(); }
    });
  });

  test('Effects: SectionFX settles back to hidden on exit (Reveal does not); each section has its own variant', async () => {
    const io = installFakeIO(); const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      eq(['curtain', 'unfurl', 'drift', 'hairline'].map((v) => fx(m.container, v)?.getAttribute('data-fx-state')).join(), 'hidden,hidden,hidden,hidden', 'all start hidden');
      const cur = fx(m.container, 'curtain');
      await io.fire(cur, true); eq(cur.getAttribute('data-fx-state'), 'shown'); has(cur.style.transform, 'translateY(0)');
      await io.fire(cur, false); eq(cur.getAttribute('data-fx-state'), 'hidden', 'exit: recedes again'); has(cur.style.transform, 'translateY(30px)');
      const unf = fx(m.container, 'unfurl'); await io.fire(unf, true); has(unf.style.transform, 'scale(1)');
      await io.fire(fx(m.container, 'drift'), true); eq(fx(m.container, 'drift').style.transform, 'translateX(0)');
      const rv = qa(m.container, '[data-reveal]')[0]; eq(rv.getAttribute('data-reveal'), 'hidden');
      await io.fire(rv, true); eq(rv.getAttribute('data-reveal'), 'shown');
      await io.fire(rv, false); eq(rv.getAttribute('data-reveal'), 'shown', 'one-shot reveal stays shown');
      ok(io.instances.every((i) => i.opts.root === q(m.container, '[data-testid="tier-landing"]')), 'every observer is rooted at the page scroll container, not the viewport');
    } finally { await m.unmount(); io.restore(); }
  });

  test('Effects: nav highlights the section in view', async () => {
    const io = installFakeIO(); const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const active = () => qa(m.container, '.tl-nav-btn').filter((b) => b.getAttribute('data-active') === 'true').map((b) => b.textContent).join();
      eq(active(), 'Home');
      await io.fire(q(m.container, '[data-section="seasons"]'), true); eq(active(), 'Seasons');
      await io.fire(q(m.container, '[data-section="developer"]'), true); eq(active(), 'Developer');
    } finally { await m.unmount(); io.restore(); }
  });

  test('Effects: capability cards tilt toward the pointer and flatten on leave', async () => {
    const m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
    try {
      const card = q(m.container, '[data-tilt]'); eq(card.getAttribute('data-tilt'), '0.00,0.00');
      card.getBoundingClientRect = () => ({ left: 0, top: 0, width: 100, height: 100 });
      await act(async () => { card.dispatchEvent(new window.MouseEvent('mousemove', { clientX: 100, clientY: 0, bubbles: true })); });
      eq(card.getAttribute('data-tilt'), '2.50,2.50'); has(card.style.transform, 'rotateX(2.5deg) rotateY(2.5deg)');
      await act(async () => { card.dispatchEvent(new window.MouseEvent('mouseout', { bubbles: true, relatedTarget: null })); });
      eq(card.getAttribute('data-tilt'), '0.00,0.00', 'flat again after leaving');
    } finally { await m.unmount(); }
  });

  test('TierLanding: nav buttons scroll to their section; mobile shows a menu that opens and closes', async () => {
    const scrolled = []; const proto = window.HTMLElement.prototype; const prev = proto.scrollIntoView;
    proto.scrollIntoView = function () { scrolled.push(this); };
    try {
      let m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
      try { await click(buttonWith(m.container, 'Seasons')); eq(scrolled.length, 1, 'Seasons nav scrolls once'); has(text(scrolled[0]), 'Season-aware'); } finally { await m.unmount(); }
      await withWidth(500, async () => {
        m = await mount(<TierLanding config={AGRI} onEnter={() => {}} />);
        try {
          ok(!buttonWith(m.container, 'What it does'), 'desktop nav hidden on mobile');
          await click(q(m.container, '[aria-label="Open menu"]'));
          await click(buttonWith(m.container, 'Developer')); eq(scrolled.length, 2);
          ok(q(m.container, '[aria-label="Open menu"]'), 'menu closes after choosing a section');
        } finally { await m.unmount(); }
      });
    } finally { proto.scrollIntoView = prev; }
  });
}
