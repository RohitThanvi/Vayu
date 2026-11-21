// Elevation from AWS/Mapzen Terrain Tiles (Terrarium PNG, free, anonymous): one shared tile cache feeds the terrain mesh, the hillshade and building base heights.
// fetchTile(z, x, y) -> Promise<RGBA bytes (256*256*4) | null>. Injected, so this is testable without a browser.
import { LRU, TILE, clamp, decodeTerrarium, lonLatToGlobalPixel, metersPerPixel, wrapX } from './geo.js';

export const TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
export const TERRARIUM_MAX_ZOOM = 15;
export const TERRAIN_ATTRIBUTION = 'Terrain: Mapzen/Tilezen Terrain Tiles on AWS (SRTM, GMTED, ETOPO1 and others; see github.com/tilezen/joerd/blob/master/docs/attribution.md)';

export function createElevationSampler({ fetchTile, maxZoom = TERRARIUM_MAX_ZOOM, cacheSize = 320 }) {
  const cache = new LRU(cacheSize), pending = new Map(), key = (z, x, y) => `${z}/${x}/${y}`;

  async function tile(z, x, y) {
    if (y < 0 || y >= 2 ** z) return null;
    x = wrapX(x, z); const k = key(z, x, y), hit = cache.get(k); if (hit) return hit;
    if (pending.has(k)) return pending.get(k);
    const p = (async () => {
      try { const rgba = await fetchTile(z, x, y); if (!rgba) return null; const h = decodeTerrarium(rgba); cache.set(k, h); return h; }
      catch { return null; } finally { pending.delete(k); }          // a failed tile is retried later rather than cached as flat ground
    })();
    pending.set(k, p); return p;
  }
  // Height at global pixel (integer) from tiles already loaded; NaN when that tile is not in the cache.
  function px(z, gx, gy) {
    const n = TILE * 2 ** z; if (gy < 0) gy = 0; if (gy > n - 1) gy = n - 1; gx = ((gx % n) + n) % n;
    const h = cache.get(key(z, Math.floor(gx / TILE), Math.floor(gy / TILE))); return h ? h[(gy % TILE) * TILE + (gx % TILE)] : NaN;
  }
  function bilinear(z, gx, gy) {
    gx -= 0.5; gy -= 0.5; const x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
    const a = px(z, x0, y0), b = px(z, x0 + 1, y0), c = px(z, x0, y0 + 1), d = px(z, x0 + 1, y0 + 1);
    if ([a, b, c, d].some(Number.isNaN)) return NaN;
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
  }
  async function ensureRange(z, gx0, gy0, gx1, gy1) {
    const n = 2 ** z, jobs = [];
    for (let ty = Math.floor(clamp(gy0, 0, n * TILE - 1) / TILE); ty <= Math.floor(clamp(gy1, 0, n * TILE - 1) / TILE); ty++)
      for (let tx = Math.floor(gx0 / TILE); tx <= Math.floor(gx1 / TILE); tx++) jobs.push(tile(z, tx, ty));
    await Promise.all(jobs);
  }

  /** width x height heights over the lon/lat box, row-major from NORTH to SOUTH and WEST to EAST (the order Cesium's heightmap terrain expects). Missing data -> 0. */
  async function sampleGrid(west, south, east, north, width, height, zoom) {
    const z = clamp(Math.round(zoom), 0, maxZoom), nw = lonLatToGlobalPixel(west, north, z), se = lonLatToGlobalPixel(east, south, z);
    await ensureRange(z, Math.floor(nw.gx) - 1, Math.floor(nw.gy) - 1, Math.ceil(se.gx) + 1, Math.ceil(se.gy) + 1);
    const out = new Float32Array(width * height);
    for (let r = 0; r < height; r++) {
      const lat = north + (south - north) * (r / (height - 1));
      for (let c = 0; c < width; c++) {
        const lon = west + (east - west) * (c / (width - 1)), g = lonLatToGlobalPixel(lon, lat, z), v = bilinear(z, g.gx, g.gy);
        out[r * width + c] = Number.isNaN(v) ? 0 : v;
      }
    }
    return out;
  }

  /** Ground height (m) at lon/lat from the cache only (call after sampleGrid/prefetch); null if not loaded. */
  function heightNow(lon, lat, zoom = maxZoom) { const z = clamp(Math.round(zoom), 0, maxZoom), g = lonLatToGlobalPixel(lon, lat, z), v = bilinear(z, g.gx, g.gy); return Number.isNaN(v) ? null : v; }
  async function prefetch(west, south, east, north, zoom = 13) {
    const z = clamp(Math.round(zoom), 0, maxZoom), a = lonLatToGlobalPixel(west, north, z), b = lonLatToGlobalPixel(east, south, z);
    await ensureRange(z, Math.floor(a.gx) - 1, Math.floor(a.gy) - 1, Math.ceil(b.gx) + 1, Math.ceil(b.gy) + 1);
  }

  /** Hillshade overlay for imagery tile (z, x, y): RGBA bytes, shadows = black with alpha, lit slopes = white with a lighter alpha, flat ground fully transparent. Horn's method. */
  async function hillshade(z, x, y, { azimuthDeg = 315, altitudeDeg = 40, exaggeration = 1.8, strength = 0.75 } = {}) {
    const zz = Math.min(z, maxZoom); const ox = x * TILE, oy = y * TILE;
    await ensureRange(zz, ox - 1, oy - 1, ox + TILE, oy + TILE);
    const out = new Uint8ClampedArray(TILE * TILE * 4), az = (360 - azimuthDeg + 90) * Math.PI / 180, zen = (90 - altitudeDeg) * Math.PI / 180;
    const lat = (() => { const n = 2 ** zz; return Math.atan(Math.sinh(Math.PI * (1 - 2 * ((y + 0.5) / n)))) * 180 / Math.PI; })();
    const cell = metersPerPixel(lat, zz);
    for (let r = 0; r < TILE; r++) for (let c = 0; c < TILE; c++) {
      const gx = ox + c, gy = oy + r;
      const a = px(zz, gx - 1, gy - 1), b = px(zz, gx, gy - 1), cc = px(zz, gx + 1, gy - 1), d = px(zz, gx - 1, gy), f = px(zz, gx + 1, gy), g = px(zz, gx - 1, gy + 1), h = px(zz, gx, gy + 1), i = px(zz, gx + 1, gy + 1);
      if ([a, b, cc, d, f, g, h, i].some(Number.isNaN)) continue;
      const dzdx = ((cc + 2 * f + i) - (a + 2 * d + g)) / (8 * cell) * exaggeration, dzdy = ((g + 2 * h + i) - (a + 2 * b + cc)) / (8 * cell) * exaggeration;
      const slope = Math.atan(Math.hypot(dzdx, dzdy)), aspect = Math.atan2(dzdy, -dzdx);
      const shade = Math.cos(zen) * Math.cos(slope) + Math.sin(zen) * Math.sin(slope) * Math.cos(az - aspect);     // 0..1, about cos(zenith) on flat ground
      const flat = Math.cos(zen), delta = shade - flat, p = (r * TILE + c) * 4;
      const v = delta < 0 ? 0 : 255;
      out[p] = out[p + 1] = out[p + 2] = v; out[p + 3] = Math.min(255, Math.abs(delta) * 255 * strength * (delta < 0 ? 1.6 : 0.8));
    }
    return out;
  }
  return { tile, sampleGrid, heightNow, prefetch, hillshade, cache };
}
