/**
 * Per-tier landing-page content for single-product (VITE_LOCKED_TIER) sites.
 * Every claim here is taken from the repo README / docs; keep it that way. If a feature's
 * status changes (e.g. the crop-suitability engine gets a published accuracy figure),
 * update the copy here in the same commit. A tier with no entry here skips the landing page
 * and opens its app directly.
 */

export const TIER_LANDINGS = {
  agri: {
    id: 'agri',
    product: 'Vayu Agri',
    meta: {
      title: 'Vayu Agri \u00b7 Satellite crop & drought intelligence',
      description: 'Satellite crop and drought intelligence for India: risk scoring, crop-stage tracking, irrigation advisory and season-aware crop suitability, with sources shown.',
      themeColor: '#08140e',
    },
    seo: {
      image: { path: '/images/agri-hero.jpg', width: 1256, height: 743, alt: 'Vayu Agri screenshot: a drawn district outlined on satellite imagery, with a drought trend chart and the contributing risk indicators beside it' },
    },
    palette: {
      bg: '#08140e', surface: 'rgba(16,36,26,0.85)', surface2: '#10241a', border: '#2c4a3a', borderLight: '#41674f',
      text: '#ffffff', text2: 'rgba(255,255,255,0.86)', text3: 'rgba(255,255,255,0.68)', text4: 'rgba(255,255,255,0.5)',
      accent: '#86c98a', accentBright: '#b9ecb0', accentDim: 'rgba(134,201,138,0.55)', wheat: '#d8c27a',
      onAccent: '#08140e',
    },
    hero: {
      overline: 'Satellite agriculture intelligence',
      headline: 'Read the field ',
      highlight: 'from orbit, season by season.',
      body: 'Drought and crop-stress scoring, crop-stage tracking, an irrigation advisory and crop suitability that knows Kharif from Rabi \u2014 built on open satellite data, with the source and method shown next to every number.',
      cta: 'Open Vayu Agri',
      secondary: 'See what it does',
      // blurred backdrop built from real Sentinel-2 NDVI + satellite basemap (see README: Agri landing page)
      background: '/images/agri-bg.jpg',
      image: {
        src: '/images/agri-hero.jpg',
        alt: 'Vayu Agri screenshot: a drawn district outlined on satellite imagery, with a drought trend chart and the contributing risk indicators beside it',
        caption: 'Screenshot of the app: a composite risk score for a drawn area, with its contributing indicators and data sources.',
      },
    },
    capabilities: [
      { title: 'Composite risk score', desc: 'A 0\u2013100 score from drought (NDDI), vegetation decline (NDVI) and soil-moisture deficit (SMAP), weighted 40/35/25 and renormalised if an indicator is unavailable.' },
      { title: 'Regional context', desc: 'Groundwater trend (GRACE, a coarse ~300 km grid \u2014 a regional signal, not a well-level one), rainfall anomaly against a 10-year normal (CHIRPS) and land surface temperature (Landsat).' },
      { title: 'Crop stage', desc: 'Green-up, peak and senescence read from the area\u2019s own NDVI seasonal curve, so the read is not crop-agnostic.' },
      { title: 'Irrigation advisory', desc: 'Irrigate now, monitor or hold off, by cross-checking SMAP dry stress against rainfall versus normal. A directional regional read, not a field-level prescription.' },
      { title: 'Crop suitability', desc: 'Ranked crops for a location from modelled soil, slope, 10-year rainfall and temperature, scored per season with the limiting factor named. Farmers can override with a soil test.' },
      { title: 'Mandi prices & extent', desc: 'Market prices from Agmarknet (data.gov.in) with historical trend, plus Random Forest crop-extent classification from points you mark.' },
    ],
    seasons: {
      title: 'Season-aware by design',
      intro: 'A crop is scored in each season it is actually grown, using Directorate of Economics & Statistics area-by-season data \u2014 and the best season is called out in plain language.',
      // months are 0-based (Jan = 0); ranges may wrap the year end.
      rows: [
        { name: 'Kharif', tag: 'Monsoon', window: 'June to October', from: 5, to: 9, color: '#86c98a' },
        { name: 'Rabi', tag: 'Winter', window: 'November to March', from: 10, to: 2, color: '#7fb4d6' },
        { name: 'Zaid', tag: 'Summer', window: 'March to June', from: 2, to: 5, color: '#d8c27a' },
      ],
      note: 'Perennials are scored year-round. For example, jowar is evaluated in both Kharif and Rabi; wheat only in Rabi.',
    },
    sources: ['Sentinel-2', 'SMAP', 'GRACE', 'CHIRPS', 'Landsat', 'ERA5-Land', 'OpenLandMap', 'Copernicus DEM', 'Agmarknet (data.gov.in)'],
  },
};

export function getTierLanding(tier) {
  return Object.prototype.hasOwnProperty.call(TIER_LANDINGS, tier) ? TIER_LANDINGS[tier] : null;
}
