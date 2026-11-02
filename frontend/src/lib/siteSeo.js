/**
 * siteSeo.js — assembles everything SEO-related for one build (main site or a locked-tier site).
 * Pure: takes env + date, returns strings. vite.config.js writes them out; tests call it directly.
 */
import { buildJsonLd, buildNoscript, buildRobotsTxt, buildSitemapXml, resolveSiteUrl } from './seo.js';
import { getTierLanding } from './tierLandingConfigs.js';

// The main site's deployed address, used ONLY when neither VITE_SITE_URL nor Vercel's system variable is available.
export const MAIN_SITE_FALLBACK_URL = 'https://vayu-geop.vercel.app';

const AUTHOR = { name: 'Rohit Thanvi', url: 'https://rohitthanvi.vercel.app', sameAs: ['https://linkedin.com/in/rohitthanvi', 'https://github.com/RohitThanvi'] };

export const MAIN_SEO = {
  siteName: 'Vayu',
  title: 'Vayu \u00b7 Geospatial & Business Intelligence',
  description: 'Live satellite analysis, maritime and aviation tracking, commodity markets, drought and agricultural risk scoring, and global hazard intel in one terminal.',
  themeColor: '#0d1117',
  image: { path: '/screenshots/analyze-maritime.jpg', width: 1600, height: 859, alt: 'Vayu terminal: a satellite map of Singapore\u2019s coast with vessel markers, and a live intel feed panel beside it' },
  noscript: {
    heading: 'Vayu \u2014 geospatial and business intelligence',
    paragraph: 'Live satellite analysis, maritime and aviation tracking, commodity markets, drought and agricultural risk scoring, and global hazard intel \u2014 unified in one terminal.',
    items: ['Satellite analysis on Google Earth Engine', 'Maritime and aviation tracking', 'Commodity markets and business intelligence', 'Drought and agricultural risk scoring', 'Remote-sensing tools with documented methods', 'Global hazard and conflict intel feed'],
  },
};

/** locked: a valid tier id or null. env: process env + loaded VITE_ vars. */
export function buildSiteSeo({ locked = null, env = {}, now = new Date() } = {}) {
  const landing = locked ? getTierLanding(locked) : null;
  const siteUrl = resolveSiteUrl(env, locked ? null : MAIN_SITE_FALLBACK_URL);
  let content = MAIN_SEO;
  if (landing) {
    content = {
      siteName: landing.product, title: landing.meta.title, description: landing.meta.description, themeColor: landing.meta.themeColor, image: landing.seo.image,
      noscript: { heading: `${landing.product} \u2014 satellite crop and drought intelligence`, paragraph: landing.hero.body, items: landing.capabilities.map((k) => `${k.title}: ${k.desc}`) },
    };
  }
  const seo = {
    ...content, siteUrl, googleVerification: (env.VITE_GOOGLE_SITE_VERIFICATION || '').trim() || null,
    jsonLd: buildJsonLd({ siteUrl, name: content.siteName, description: content.description, imagePath: content.image.path, author: AUTHOR }),
    noscript: buildNoscript(content.noscript),
  };
  return { seo, siteUrl, robots: buildRobotsTxt(siteUrl, { siteName: content.siteName }), sitemap: buildSitemapXml(siteUrl, now.toISOString()) };
}
