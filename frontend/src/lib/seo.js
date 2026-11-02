/**
 * seo.js — pure helpers for sitemap.xml, robots.txt, <head> meta/JSON-LD and the no-JS fallback.
 * Used by vite.config.js at BUILD time (node) and unit-tested in ui-tests/seo.test.jsx.
 * No imports, no import.meta, no DOM: it must run in plain node.
 *
 * The app is a single-page, client-rendered terminal: there is exactly ONE indexable URL per site
 * ("/"), so the sitemap lists one URL. We say so rather than inventing pages.
 */

const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** 'https://x.com/a?b#c' | 'x.com' | ' X.com/ ' -> 'https://x.com'. Returns null for anything unusable. */
export function normalizeSiteUrl(raw) {
  if (typeof raw !== 'string') return null;
  let v = raw.trim();
  if (!v) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(v)) v = `https://${v}`;
  let u;
  try { u = new URL(v); } catch { return null; }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  if (!u.hostname || u.hostname === 'localhost' || /^\d{1,3}(\.\d{1,3}){3}$/.test(u.hostname) || !u.hostname.includes('.')) return null;
  return `${u.protocol}//${u.host}`.toLowerCase();
}

/**
 * Where is this site served? Priority: explicit VITE_SITE_URL (custom domain), then Vercel's
 * VERCEL_PROJECT_PRODUCTION_URL (build-time, no protocol), then `fallback` (main site only).
 */
export function resolveSiteUrl(env = {}, fallback = null) {
  return normalizeSiteUrl(env.VITE_SITE_URL) || normalizeSiteUrl(env.VERCEL_PROJECT_PRODUCTION_URL) || normalizeSiteUrl(fallback);
}

export function buildRobotsTxt(siteUrl, { siteName = 'Vayu' } = {}) {
  const lines = [
    `# ${siteName}: client-rendered single-page app with one indexable URL (/).`,
    '# Generated at build time by vite.config.js (src/lib/seo.js). Do not edit by hand.',
    '',
    'User-agent: *',
    'Allow: /',
    'Disallow: /404.html',
    'Disallow: /offline.html',
  ];
  if (siteUrl) lines.push('', `Sitemap: ${siteUrl}/sitemap.xml`);
  return lines.join('\n') + '\n';
}

export function buildSitemapXml(siteUrl, lastmod) {
  if (!siteUrl) return null;
  const day = /^\d{4}-\d{2}-\d{2}/.test(String(lastmod)) ? String(lastmod).slice(0, 10) : new Date().toISOString().slice(0, 10);
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n  <url>\n    <loc>${esc(siteUrl)}/</loc>\n    <lastmod>${day}</lastmod>\n  </url>\n</urlset>\n`;
}

/** JSON for a <script type="application/ld+json">: '<' is escaped so content can never close the tag. */
export function jsonLdScript(obj) {
  return `<script type="application/ld+json">${JSON.stringify(obj).replace(/</g, '\\u003c')}</script>`;
}

export function buildJsonLd({ siteUrl, name, description, imagePath, author }) {
  const url = siteUrl ? `${siteUrl}/` : undefined;
  const app = { '@type': 'WebApplication', name, description, applicationCategory: 'BusinessApplication', operatingSystem: 'Web browser' };
  if (url) { app.url = url; app['@id'] = `${url}#app`; }
  if (siteUrl && imagePath) app.image = `${siteUrl}${imagePath}`;
  if (author) app.author = { '@type': 'Person', name: author.name, url: author.url, sameAs: author.sameAs };
  const graph = [app];
  if (url) graph.unshift({ '@type': 'WebSite', '@id': `${url}#site`, name, url });
  return { '@context': 'https://schema.org', '@graph': graph };
}

/** Plain-HTML summary for crawlers/link scrapers that do not run JavaScript. Shown only when JS is off. */
export function buildNoscript({ heading, paragraph, items = [] }) {
  const li = items.map((i) => `<li>${esc(i)}</li>`).join('');
  return `<!-- seo:noscript:start --><noscript><main><h1>${esc(heading)}</h1><p>${esc(paragraph)}</p>${li ? `<ul>${li}</ul>` : ''}<p>This site is an interactive web application; please enable JavaScript to use it.</p></main></noscript><!-- seo:noscript:end -->`;
}

const BLOCK = /<!-- seo:start -->[\s\S]*?<!-- seo:end -->\s*/g;
const NOSCRIPT = /<!-- seo:noscript:start -->[\s\S]*?<!-- seo:noscript:end -->\s*/g;

/**
 * Rewrites index.html's <head>/<body> for SEO. Idempotent: running it twice gives the same output.
 * seo = { title, description, themeColor, siteName, siteUrl|null, image:{path,width,height,alt}|null,
 *         jsonLd|null, noscript|null, googleVerification|null }
 */
export function injectSeo(html, seo) {
  let out = html.replace(BLOCK, '').replace(NOSCRIPT, '');
  out = out
    .replace(/<title>[\s\S]*?<\/title>\s*/i, '')
    .replace(/<meta\s+name="description"[^>]*>\s*/i, '')
    .replace(/<meta\s+name="theme-color"[^>]*>\s*/i, '');
  if (seo.googleVerification) out = out.replace(/<meta\s+name="google-site-verification"[^>]*>\s*/i, '');
  const tags = [
    '<!-- seo:start -->',
    `<title>${esc(seo.title)}</title>`,
    `<meta name="description" content="${esc(seo.description)}" />`,
    `<meta name="theme-color" content="${esc(seo.themeColor)}" />`,
    '<meta name="robots" content="index,follow,max-image-preview:large" />',
  ];
  if (seo.googleVerification) tags.push(`<meta name="google-site-verification" content="${esc(seo.googleVerification)}" />`);
  tags.push(
    `<meta property="og:site_name" content="${esc(seo.siteName)}" />`, '<meta property="og:type" content="website" />',
    `<meta property="og:title" content="${esc(seo.title)}" />`, `<meta property="og:description" content="${esc(seo.description)}" />`,
    `<meta name="twitter:card" content="${seo.image && seo.siteUrl ? 'summary_large_image' : 'summary'}" />`,
    `<meta name="twitter:title" content="${esc(seo.title)}" />`, `<meta name="twitter:description" content="${esc(seo.description)}" />`,
  );
  if (seo.siteUrl) {
    tags.push(`<link rel="canonical" href="${esc(seo.siteUrl)}/" />`, `<meta property="og:url" content="${esc(seo.siteUrl)}/" />`);
    if (seo.image) {
      const abs = `${seo.siteUrl}${seo.image.path}`;
      tags.push(`<meta property="og:image" content="${esc(abs)}" />`, `<meta property="og:image:width" content="${Number(seo.image.width)}" />`, `<meta property="og:image:height" content="${Number(seo.image.height)}" />`,
        `<meta property="og:image:alt" content="${esc(seo.image.alt)}" />`, `<meta name="twitter:image" content="${esc(abs)}" />`, `<meta name="twitter:image:alt" content="${esc(seo.image.alt)}" />`);
    }
  }
  if (seo.jsonLd) tags.push(jsonLdScript(seo.jsonLd));
  tags.push('<!-- seo:end -->');
  const block = tags.map((t) => `    ${t}`).join('\n') + '\n';
  const at = out.search(/\n\s*<link rel="icon"/);
  out = at >= 0 ? out.slice(0, at + 1) + block + out.slice(at + 1) : out.replace('</head>', `${block}  </head>`);
  if (seo.noscript) out = out.replace(/(\s*)<div id="root"><\/div>/, `$1${seo.noscript}$1<div id="root"></div>`);
  return out;
}
