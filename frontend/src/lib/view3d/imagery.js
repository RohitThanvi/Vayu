// Base imagery for the 3D view. Default: NASA GIBS MODIS true colour (the same service the 2D map already uses; free, keyless, coarse ~250 m, daily so it can have cloud/swath gaps).
// Sharper imagery is a licensing decision, so it is configured, never assumed: VITE_3D_IMAGERY_URL (https template with {z}/{x}/{y}), VITE_3D_IMAGERY_ATTRIBUTION, VITE_3D_IMAGERY_MAX_ZOOM.
export const GIBS_ATTRIBUTION = 'Imagery: NASA GIBS / Worldview (MODIS)';
export function gibsUrl(now = new Date()) {
  const d = new Date(now.getTime()); d.setUTCDate(d.getUTCDate() - 1);                         // GIBS has ~1 day latency and no "latest" alias in plain XYZ URLs
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${d.toISOString().slice(0, 10)}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
}
export function imageryConfig(env = {}, now = new Date()) {
  const url = (env.VITE_3D_IMAGERY_URL || '').trim();
  if (url) {
    const ok = /^https:\/\//.test(url) && ['{z}', '{x}', '{y}'].every((t) => url.includes(t));
    if (ok) return { source: 'custom', url, maxZoom: Math.min(Math.max(parseInt(env.VITE_3D_IMAGERY_MAX_ZOOM, 10) || 13, 1), 19), attribution: (env.VITE_3D_IMAGERY_ATTRIBUTION || '').trim() || 'Imagery: custom source' };
    return { source: 'gibs', url: gibsUrl(now), maxZoom: 9, attribution: GIBS_ATTRIBUTION, warning: 'VITE_3D_IMAGERY_URL must be an https URL containing {z}, {x} and {y}; using NASA GIBS instead.' };
  }
  return { source: 'gibs', url: gibsUrl(now), maxZoom: 9, attribution: GIBS_ATTRIBUTION };
}
