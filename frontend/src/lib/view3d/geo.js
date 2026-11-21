// Pure geometry for the optional 3D view: Web-Mercator tile maths, Terrarium elevation decoding, a small LRU cache. No DOM, no Cesium.
export const TILE = 256;
export const MAX_LAT = 85.0511287798;
export const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
export const wrapX = (x, z) => ((x % 2 ** z) + 2 ** z) % 2 ** z;

/** Global pixel coordinates of a lon/lat at zoom z (origin top-left of the world, y grows southwards). */
export function lonLatToGlobalPixel(lon, lat, z) {
  const n = TILE * 2 ** z, s = Math.sin(clamp(lat, -MAX_LAT, MAX_LAT) * Math.PI / 180);
  return { gx: (lon + 180) / 360 * n, gy: clamp((0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n, 0, n - 1e-6), n };
}
export function lonLatToTile(lon, lat, z) { const { gx, gy } = lonLatToGlobalPixel(lon, lat, z); return { z, x: Math.floor(gx / TILE), y: Math.floor(gy / TILE) }; }
/** North-west corner of tile (x, y) at zoom z, in degrees. */
export function tileCorner(z, x, y) {
  const n = 2 ** z, lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
  return { lon: x / n * 360 - 180, lat };
}
export const metersPerPixel = (lat, z) => 156543.03392804097 * Math.cos(lat * Math.PI / 180) / 2 ** z;

/** Tiles at zoom z touching the square of half-side radiusM around (lat, lon). Capped so a huge radius cannot request thousands of tiles. */
export function tilesCoveringCircle(lat, lon, radiusM, z, maxTiles = 64) {
  const dLat = radiusM / 111320, dLon = radiusM / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.05));
  const a = lonLatToTile(lon - dLon, lat + dLat, z), b = lonLatToTile(lon + dLon, lat - dLat, z), out = [];
  for (let y = a.y; y <= b.y; y++) for (let x = a.x; x <= b.x; x++) out.push({ z, x: wrapX(x, z), y });
  if (out.length > maxTiles) { const cx = lonLatToTile(lon, lat, z); out.sort((p, q) => Math.hypot(p.x - cx.x, p.y - cx.y) - Math.hypot(q.x - cx.x, q.y - cx.y)); out.length = maxTiles; }
  return out;
}

/** Terrarium PNG pixels -> metres: (R*256 + G + B/256) - 32768 (documented by Mapzen/Tilezen). rgba: RGBA bytes of a 256x256 tile. */
export function decodeTerrarium(rgba) {
  const out = new Float32Array(TILE * TILE);
  for (let i = 0, p = 0; i < out.length; i++, p += 4) out[i] = rgba[p] * 256 + rgba[p + 1] + rgba[p + 2] / 256 - 32768;
  return out;
}

/** Initial camera height (m) that roughly frames what a Leaflet map at this zoom shows (about 800 px tall). Clamped to a sensible 3D range. */
export function altitudeForZoom(zoom, lat) {
  const z = Number.isFinite(zoom) ? zoom : 10;
  return clamp(metersPerPixel(lat || 0, z) * 800 * 1.1, 700, 120000);
}

export class LRU {
  constructor(max) { this.max = Math.max(1, max); this.m = new Map(); }
  get(k) { if (!this.m.has(k)) return undefined; const v = this.m.get(k); this.m.delete(k); this.m.set(k, v); return v; }
  has(k) { return this.m.has(k); }
  set(k, v) { this.m.delete(k); this.m.set(k, v); while (this.m.size > this.max) this.m.delete(this.m.keys().next().value); }
  get size() { return this.m.size; }
}
/** Deterministic 0..1 value from an integer id (for stable per-building colour variation). */
export const hash01 = (n) => { let h = (Number(n) | 0) ^ 0x9e3779b9; h = Math.imul(h ^ (h >>> 16), 0x85ebca6b); h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
