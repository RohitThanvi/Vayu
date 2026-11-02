import { readFileSync } from 'node:fs';
import { test, eq, ok, has, lacks } from './harness.jsx';
import { normalizeSiteUrl, resolveSiteUrl, buildRobotsTxt, buildSitemapXml, buildJsonLd, jsonLdScript, buildNoscript, injectSeo } from '../src/lib/seo.js';
import { buildSiteSeo, MAIN_SITE_FALLBACK_URL } from '../src/lib/siteSeo.js';
import { TIER_LANDINGS } from '../src/lib/tierLandingConfigs.js';

const SAMPLE = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="description" content="old" />
    <meta name="theme-color" content="#000000" />
    <meta name="google-site-verification" content="MAIN-TOKEN" />
    <title>Old title</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>`;
const count = (s, re) => (s.match(re) || []).length;
const SEO = { title: 'T & <Co>', description: 'D "q"', themeColor: '#111111', siteName: 'S', siteUrl: 'https://x.example.com', image: { path: '/i.jpg', width: 10, height: 5, alt: 'alt' }, jsonLd: { a: 1 }, noscript: buildNoscript({ heading: 'H', paragraph: 'P', items: ['one'] }), googleVerification: null };

export function register() {
  test('SEO: normalizeSiteUrl keeps only a clean https origin and rejects unusable values', async () => {
    eq(normalizeSiteUrl('https://Vayu-Agri.vercel.app/path?x=1#h'), 'https://vayu-agri.vercel.app');
    eq(normalizeSiteUrl('vayu-agri.vercel.app'), 'https://vayu-agri.vercel.app', 'Vercel gives no protocol');
    eq(normalizeSiteUrl('  example.com/  '), 'https://example.com');
    for (const bad of ['', '   ', null, undefined, 5, 'localhost', 'http://localhost:5173', '127.0.0.1', 'ftp://example.com', 'javascript:alert(1)', 'not a url', 'nodot']) eq(normalizeSiteUrl(bad), null, `rejects ${String(bad)}`);
  });

  test('SEO: resolveSiteUrl priority is VITE_SITE_URL, then Vercel production URL, then the fallback', async () => {
    eq(resolveSiteUrl({ VITE_SITE_URL: 'agri.example.com', VERCEL_PROJECT_PRODUCTION_URL: 'p.vercel.app' }, 'fb.example.com'), 'https://agri.example.com');
    eq(resolveSiteUrl({ VERCEL_PROJECT_PRODUCTION_URL: 'p.vercel.app' }, 'fb.example.com'), 'https://p.vercel.app');
    eq(resolveSiteUrl({ VITE_SITE_URL: 'garbage', VERCEL_PROJECT_PRODUCTION_URL: 'p.vercel.app' }), 'https://p.vercel.app', 'bad override falls through');
    eq(resolveSiteUrl({}, 'fb.example.com'), 'https://fb.example.com'); eq(resolveSiteUrl({}), null);
  });

  test('SEO: robots.txt allows crawling, hides utility pages, and names the sitemap only when the URL is known', async () => {
    const r = buildRobotsTxt('https://x.example.com');
    has(r, 'User-agent: *'); has(r, 'Allow: /'); has(r, 'Disallow: /404.html'); has(r, 'Disallow: /offline.html'); has(r, 'Sitemap: https://x.example.com/sitemap.xml');
    lacks(r, 'Disallow: /\n', 'must not block the whole site');
    lacks(buildRobotsTxt(null), 'Sitemap:');
  });

  test('SEO: sitemap.xml is valid XML with exactly one absolute URL and a date-only lastmod', async () => {
    const xml = buildSitemapXml('https://x.example.com', '2026-11-02T10:00:00.000Z');
    const doc = new window.DOMParser().parseFromString(xml, 'application/xml');
    ok(!doc.querySelector('parsererror'), 'well-formed');
    eq(doc.documentElement.localName, 'urlset'); eq(doc.documentElement.namespaceURI, 'http://www.sitemaps.org/schemas/sitemap/0.9');
    eq(doc.getElementsByTagName('url').length, 1); eq(doc.getElementsByTagName('loc')[0].textContent, 'https://x.example.com/'); eq(doc.getElementsByTagName('lastmod')[0].textContent, '2026-11-02');
    ok(/^\d{4}-\d{2}-\d{2}$/.test(new window.DOMParser().parseFromString(buildSitemapXml('https://x.example.com', 'garbage'), 'application/xml').getElementsByTagName('lastmod')[0].textContent), 'bad date replaced with today');
    eq(buildSitemapXml(null, '2026-01-01'), null, 'no URL, no sitemap');
  });

  test('SEO: JSON-LD cannot break out of its script tag and round-trips', async () => {
    const evil = buildJsonLd({ siteUrl: 'https://x.example.com', name: 'A</script><script>alert(1)</script>', description: 'd', imagePath: '/i.jpg', author: { name: 'N', url: 'https://a.example.com', sameAs: [] } });
    const tag = jsonLdScript(evil);
    eq(count(tag, /<\/script>/g), 1, 'only the real closing tag'); eq(count(tag, /<script/g), 1);
    eq(JSON.parse(tag.replace(/^<script[^>]*>/, '').replace(/<\/script>$/, ''))['@graph'][1].name, 'A</script><script>alert(1)</script>');
    const g = buildJsonLd({ siteUrl: null, name: 'N', description: 'd', imagePath: '/i.jpg' })['@graph'];
    eq(g.length, 1, 'no WebSite entry without a URL'); ok(!g[0].url && !g[0].image, 'no relative/invented URLs');
  });

  test('SEO: injectSeo sets one of each tag, escapes text, keeps the Search Console tag, and is idempotent', async () => {
    const once = injectSeo(SAMPLE, SEO); const twice = injectSeo(once, SEO);
    eq(twice, once, 'idempotent');
    eq(count(once, /<title>/g), 1); eq(count(once, /name="description"/g), 1); eq(count(once, /name="theme-color"/g), 1); eq(count(once, /rel="canonical"/g), 1);
    has(once, '<title>T &amp; &lt;Co&gt;</title>'); has(once, 'content="D &quot;q&quot;"'); lacks(once, 'Old title');
    has(once, '<link rel="canonical" href="https://x.example.com/" />'); has(once, 'property="og:image" content="https://x.example.com/i.jpg"'); has(once, 'name="twitter:card" content="summary_large_image"');
    has(once, 'name="google-site-verification" content="MAIN-TOKEN"'); has(once, '<link rel="icon"');
    ok(once.indexOf('<noscript>') < once.indexOf('<div id="root">'), 'no-JS fallback precedes the app root'); has(once, '<h1>H</h1>');
    const ver = injectSeo(SAMPLE, { ...SEO, googleVerification: 'AGRI-TOKEN' });
    has(ver, 'content="AGRI-TOKEN"'); lacks(ver, 'MAIN-TOKEN'); eq(count(ver, /google-site-verification/g), 1);
  });

  test('SEO: without a site URL there is no canonical, og:url or og:image, and the card falls back to summary', async () => {
    const o = injectSeo(SAMPLE, { ...SEO, siteUrl: null });
    lacks(o, 'rel="canonical"'); lacks(o, 'og:url'); lacks(o, 'og:image'); lacks(o, 'twitter:image'); has(o, 'name="twitter:card" content="summary"'); has(o, 'og:title');
  });

  test('SEO: main site build — fallback URL, sane title/description lengths, real content', async () => {
    const b = buildSiteSeo({ locked: null, env: {} });
    eq(b.siteUrl, MAIN_SITE_FALLBACK_URL); has(b.robots, `Sitemap: ${MAIN_SITE_FALLBACK_URL}/sitemap.xml`); has(b.sitemap, `<loc>${MAIN_SITE_FALLBACK_URL}/</loc>`);
    eq(buildSiteSeo({ locked: null, env: { VERCEL_PROJECT_PRODUCTION_URL: 'vayu.example.com' } }).siteUrl, 'https://vayu.example.com', 'Vercel variable beats the hardcoded fallback');
    has(b.seo.noscript, 'Satellite analysis on Google Earth Engine'); has(JSON.stringify(b.seo.jsonLd), 'Rohit Thanvi');
  });

  test('SEO: locked agri build uses its own URL and never leaks the main site URL', async () => {
    const withVercel = buildSiteSeo({ locked: 'agri', env: { VERCEL_PROJECT_PRODUCTION_URL: 'vayu-agri.vercel.app' } });
    eq(withVercel.siteUrl, 'https://vayu-agri.vercel.app'); has(withVercel.sitemap, '<loc>https://vayu-agri.vercel.app/</loc>'); has(withVercel.robots, 'Sitemap: https://vayu-agri.vercel.app/sitemap.xml'); lacks(withVercel.sitemap, 'vayu-geop');
    eq(buildSiteSeo({ locked: 'agri', env: { VITE_SITE_URL: 'agri.example.org', VERCEL_PROJECT_PRODUCTION_URL: 'v.vercel.app' } }).siteUrl, 'https://agri.example.org', 'custom domain wins');
    const none = buildSiteSeo({ locked: 'agri', env: {} });
    eq(none.siteUrl, null, 'unknown URL stays unknown: no guessing'); eq(none.sitemap, null); lacks(none.robots, 'Sitemap:'); lacks(none.robots, 'vayu-geop');
    has(withVercel.seo.title, 'Vayu Agri'); has(withVercel.seo.noscript, 'Composite risk score'); has(JSON.stringify(withVercel.seo.jsonLd), '"name":"Vayu Agri"'); lacks(withVercel.seo.noscript, 'Maritime');
    eq(buildSiteSeo({ locked: 'agri', env: { VITE_GOOGLE_SITE_VERIFICATION: ' tok ' } }).seo.googleVerification, 'tok');
  });

  test('SEO: every site has a title <= 65 and a description of 70-160 characters (search-result limits)', async () => {
    const all = [buildSiteSeo({ locked: null, env: {} }).seo, ...Object.keys(TIER_LANDINGS).map((t) => buildSiteSeo({ locked: t, env: {} }).seo)];
    for (const s of all) { ok(s.title.length <= 65, `title length ${s.title.length}: ${s.title}`); ok(s.description.length >= 70 && s.description.length <= 160, `description length ${s.description.length}: ${s.description}`); ok(/^#[0-9a-f]{6}$/i.test(s.themeColor)); ok(s.image.alt.length > 20 && s.image.path.startsWith('/')); }
  });

  test('SEO: the real index.html gets the full treatment and keeps its verification tag, icons and scripts', async () => {
    const html = readFileSync(`${process.cwd()}/index.html`, 'utf8');
    const out = injectSeo(html, buildSiteSeo({ locked: null, env: {} }).seo);
    eq(count(out, /<title>/g), 1); eq(count(out, /rel="canonical"/g), 1); has(out, 'google-site-verification'); has(out, 'rel="manifest"'); has(out, 'type="module" src="/src/main.jsx"'); has(out, 'leaflet.js');
    ok(out.indexOf('<!-- seo:start -->') < out.indexOf('<link rel="icon"'), 'seo block placed before the icon links');
  });
}
