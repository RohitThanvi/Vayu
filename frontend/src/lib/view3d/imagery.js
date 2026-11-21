// Base imagery for the 3D view. Free, keyless, and only sources whose licence allows a commercial product:
//  * DEFAULT  EOX Sentinel-2 cloudless 2016 (~10 m, levels 0-14). EOX publishes the 2016 mosaic under CC BY (commercial use allowed with attribution); its LATER yearly mosaics are CC BY-NC-SA and must not be used here.
//             The tile URL follows the same pattern as the 2024 layer already used by the 2D map, with the layer id s2cloudless-2016 (id and levels from EOX's own layer list). It has not been fetched from the dev sandbox,
//             so the view falls back to NASA GIBS automatically if its tiles fail (see watchImageryErrors). The imagery is from 2016: new construction and land-use change are not shown.
//  * FALLBACK NASA GIBS MODIS true colour (coarse ~250 m, daily, keyless, public NASA data).
//  * OVERRIDE  VITE_3D_IMAGERY_URL (https template with {z}/{x}/{y}) + VITE_3D_IMAGERY_ATTRIBUTION + VITE_3D_IMAGERY_MAX_ZOOM: your own licensed imagery.  VITE_3D_IMAGERY_SOURCE=gibs forces the fallback source.
export const GIBS_ATTRIBUTION = 'Imagery: NASA GIBS / Worldview (MODIS)';
export const EOX_2016_URL = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2016_3857/default/g/{z}/{y}/{x}.jpg';
export const EOX_2016_ATTRIBUTION = 'Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016)';
export function gibsUrl(now = new Date()) {
  const d = new Date(now.getTime()); d.setUTCDate(d.getUTCDate() - 1);                         // GIBS has ~1 day latency and no "latest" alias in plain XYZ URLs
  return `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${d.toISOString().slice(0, 10)}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
}
const gibs = (now, extra = {}) => ({ source: 'gibs', url: gibsUrl(now), maxZoom: 9, attribution: GIBS_ATTRIBUTION, ...extra });

/** { source, url, maxZoom, attribution, fallback? , warning? } - `fallback` (a GIBS config) is present whenever the primary is not already GIBS. */
export function imageryConfig(env = {}, now = new Date()) {
  const forced = (env.VITE_3D_IMAGERY_SOURCE || '').trim().toLowerCase(), url = (env.VITE_3D_IMAGERY_URL || '').trim();
  if (forced === 'gibs') return gibs(now);
  if (url) {
    const ok = /^https:\/\//.test(url) && ['{z}', '{x}', '{y}'].every((t) => url.includes(t));
    if (ok) return { source: 'custom', url, maxZoom: Math.min(Math.max(parseInt(env.VITE_3D_IMAGERY_MAX_ZOOM, 10) || 13, 1), 19), attribution: (env.VITE_3D_IMAGERY_ATTRIBUTION || '').trim() || 'Imagery: custom source', fallback: gibs(now) };
    return gibs(now, { warning: 'VITE_3D_IMAGERY_URL must be an https URL containing {z}, {x} and {y}; using NASA GIBS instead.' });
  }
  return { source: 'eox2016', url: EOX_2016_URL, maxZoom: 14, attribution: EOX_2016_ATTRIBUTION, fallback: gibs(now) };
}
