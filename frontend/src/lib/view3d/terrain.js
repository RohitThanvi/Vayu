// Cesium bindings that turn the shared elevation sampler into (a) a terrain mesh and (b) a hillshade imagery layer. Cesium is passed in (it is loaded lazily from a CDN).
// Why the hillshade exists: CustomHeightmapTerrainProvider has no vertex normals (Cesium docs), so lit terrain would look flat; relief is baked into a translucent overlay instead.
import { TERRARIUM_MAX_ZOOM } from './elevation.js';

export const TERRAIN_SAMPLES = 65;
// Cesium's geographic level L has ~2x finer sample spacing than Mercator zoom L-1 needs, so zoom = L oversamples slightly (no aliasing). Beyond the data's finest zoom the same tiles are reused.
export const zoomForLevel = (level) => Math.min(Math.max(level, 0), TERRARIUM_MAX_ZOOM);

export function createTerrainProvider(Cesium, sampler, { samples = TERRAIN_SAMPLES, credit } = {}) {
  const tilingScheme = new Cesium.GeographicTilingScheme(), deg = Cesium.Math.toDegrees;
  return new Cesium.CustomHeightmapTerrainProvider({
    width: samples, height: samples, tilingScheme, credit,
    callback: async (x, y, level) => {
      const r = tilingScheme.tileXYToRectangle(x, y, level);
      return sampler.sampleGrid(deg(r.west), deg(r.south), deg(r.east), deg(r.north), samples, samples, zoomForLevel(level));   // row-major, north to south, west to east
    },
  });
}

export function createBaseImageryProvider(Cesium, { url, maxZoom, attribution }) {
  return new Cesium.UrlTemplateImageryProvider({ url, maximumLevel: maxZoom, credit: attribution });          // Web-Mercator, 256 px by default
}

/** A normal UrlTemplateImageryProvider (so the tiling scheme, levels and credit are Cesium's own) whose requestImage is replaced by one that draws the hillshade onto a canvas. */
export function createHillshadeProvider(Cesium, sampler, { minZoom = 7, maxZoom = TERRARIUM_MAX_ZOOM, credit, makeCanvas } = {}) {
  const provider = new Cesium.UrlTemplateImageryProvider({ url: 'https://hillshade.invalid/{z}/{x}/{y}.png', minimumLevel: minZoom, maximumLevel: maxZoom, credit });   // the URL is never fetched
  const canvasFactory = makeCanvas || (() => { const c = document.createElement('canvas'); c.width = c.height = 256; return c; });
  provider.requestImage = async (x, y, level) => {
    const rgba = await sampler.hillshade(level, x, y), canvas = canvasFactory(), ctx = canvas.getContext('2d');
    ctx.putImageData(new ImageData(rgba, 256, 256), 0, 0); return canvas;
  };
  return provider;
}

/** Calls onFail() once when a provider has reported `limit` tile errors (a wrong URL or an outage), so the view can swap to a fallback instead of showing a black globe. Returns an unsubscribe function. */
export function watchImageryErrors(provider, { limit = 6, onFail }) {
  let n = 0, fired = false;
  const handler = () => { n += 1; if (!fired && n >= limit) { fired = true; try { onFail(); } finally { provider.errorEvent?.removeEventListener?.(handler); } } };
  provider.errorEvent?.addEventListener?.(handler);
  return () => provider.errorEvent?.removeEventListener?.(handler);
}
