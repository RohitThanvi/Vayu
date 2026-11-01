/**
 * Per-tier landing-page content for single-product (VITE_LOCKED_TIER) sites.
 * Every claim here is taken from the repo README / docs; keep it that way. If a feature's
 * status changes (e.g. the crop-suitability engine gets a published accuracy figure),
 * update `trust` below in the same commit. A tier with no entry here skips the landing page
 * and opens its app directly.
 */

export const TIER_LANDINGS = {
  agri: {
    id: 'agri',
    product: 'Vayu Agri',
    meta: {
      title: 'Vayu Agri · Satellite crop & drought intelligence',
      description: 'Satellite-based agricultural risk scoring, crop-stage tracking, irrigation advisory and season-aware crop suitability for India. Every number states its source and method.',
      themeColor: '#08140e',
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
        { name: 'Kharif', window: 'Jun\u2013Oct', from: 5, to: 9, color: '#86c98a' },
        { name: 'Rabi', window: 'Nov\u2013Mar', from: 10, to: 2, color: '#7fb4d6' },
        { name: 'Zaid', window: 'Mar\u2013Jun', from: 2, to: 5, color: '#d8c27a' },
      ],
      note: 'Perennials are scored year-round. For example, jowar is evaluated in both Kharif and Rabi; wheat only in Rabi.',
    },
    trust: {
      title: 'What is checked, and what is not',
      intro: 'Vayu is built to be checked, not just read. These are the current facts.',
      done: [
        'Every result states its source dataset, resolution and formula.',
        'A held-out validation harness exists: about 60 districts across 22 states, with whole states held out so nearby districts cannot leak between tuning and testing.',
        'Crop requirements are cited and schema-checked in tests; missing evidence lowers confidence and is never counted against a crop.',
      ],
      notYet: [
        'No accuracy figure for crop suitability is published yet.',
        'Suitability scores are not calibrated yet, and the response says so. Confidence is a data-completeness heuristic, not a statistical interval.',
        'The tools have not yet been validated against documented real-world events.',
      ],
    },
    sources: ['Sentinel-2', 'SMAP', 'GRACE', 'CHIRPS', 'Landsat', 'ERA5-Land', 'OpenLandMap', 'Copernicus DEM', 'Agmarknet (data.gov.in)'],
  },
};

export function getTierLanding(tier) {
  return Object.prototype.hasOwnProperty.call(TIER_LANDINGS, tier) ? TIER_LANDINGS[tier] : null;
}
