// 3D buildings from OpenFreeMap's OpenMapTiles vector tiles (free, no key, no request limits, attribution required). Layer "building", fields render_height, render_min_height, colour, hide_3d
// (OpenMapTiles schema). Heights exist only on zoom-14 tiles, and where OSM has no height or levels tag the schema substitutes a 5 m default, so many heights are placeholders.
import { PbfReader } from 'pbf';
import { VectorTile } from '@mapbox/vector-tile';
import { hash01, tilesCoveringCircle } from './geo.js';

export const OFM_TILEJSON = 'https://tiles.openfreemap.org/planet';
export const BUILDING_ZOOM = 14;
export const BUILDINGS_ATTRIBUTION = 'Buildings: OpenFreeMap, \u00A9 OpenMapTiles, data \u00A9 OpenStreetMap contributors';
export const DEFAULT_HEIGHT_NOTE = 'Heights come from OSM height/levels tags where mapped; unmapped buildings use a 5 m default.';

let templatePromise = null;
/** OpenFreeMap's tile URLs change with every planet build, so the template is read from its TileJSON (never hard-coded). */
export function getTileTemplate(fetchJson = (u) => fetch(u).then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })) {
  if (!templatePromise) templatePromise = fetchJson(OFM_TILEJSON).then((j) => {
    const t = Array.isArray(j?.tiles) ? j.tiles[0] : null;
    if (typeof t !== 'string' || !['{z}', '{x}', '{y}'].every((k) => t.includes(k))) throw new Error('OpenFreeMap TileJSON has no usable tile URL');
    return t;
  }).catch((e) => { templatePromise = null; throw e; });
  return templatePromise;
}
export const resetTileTemplate = () => { templatePromise = null; };
export const tileUrl = (tpl, z, x, y) => tpl.replace('{z}', z).replace('{x}', x).replace('{y}', y);

const ringArea = (ring) => { let a = 0; for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += (ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]); return Math.abs(a) / 2; };
const num = (v, d) => (typeof v === 'number' && Number.isFinite(v) ? v : d);

/** Decode one building tile into footprints. Returns [{id, rings: [outer, ...holes] as [lon,lat][], height, minHeight, colour}]. */
export function parseBuildings(buffer, z, x, y, { maxPerTile = 4000, minAreaM2 = 12 } = {}) {
  const layer = new VectorTile(new PbfReader(buffer)).layers.building, out = [];
  if (!layer) return out;
  for (let i = 0; i < layer.length && out.length < maxPerTile; i++) {
    const f = layer.feature(i), p = f.properties || {};
    if (p.hide_3d) continue;                                                 // building outlines whose parts are drawn separately
    const g = f.toGeoJSON(x, y, z).geometry, polys = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
    for (const rings of polys) {
      const outer = rings[0]; if (!outer || outer.length < 4) continue;
      const latC = outer[0][1], areaM2 = ringArea(outer) * (111320 ** 2) * Math.cos(latC * Math.PI / 180);
      if (areaM2 < minAreaM2) continue;
      const height = Math.max(num(p.render_height, 5), 3), minHeight = Math.min(Math.max(num(p.render_min_height, 0), 0), height - 1);
      out.push({ id: f.id ?? (out.length + i * 7919), rings: rings.filter((r) => r.length >= 4), height, minHeight, colour: typeof p.colour === 'string' ? p.colour : null });
    }
  }
  return out;
}

const PALETTE = ['#d9d0c3', '#cdc1b0', '#e3dbce', '#c2b6a6', '#d4c7b4', '#cbb9a1'];
const HEX = /^#([0-9a-f]{6}|[0-9a-f]{3})$/i;
/** Wall colour: the OSM colour tag when it is a plain hex value, else a stable muted neutral that lightens a little with height. */
export function buildingColorHex(b) {
  if (b.colour && HEX.test(b.colour)) return b.colour;
  const base = PALETTE[Math.floor(hash01(b.id) * PALETTE.length)], k = Math.min(b.height / 60, 1) * 0.12, n = parseInt(base.slice(1), 16);
  const ch = (s) => Math.round(Math.min(255, ((n >> s) & 255) * (1 + k)));
  return `#${[16, 8, 0].map((s) => ch(s).toString(16).padStart(2, '0')).join('')}`;
}

/** Zoom-14 tiles around a point, nearest first. */
export const buildingTilesAround = (lat, lon, radiusM, maxTiles = 12) => tilesCoveringCircle(lat, lon, radiusM, BUILDING_ZOOM, maxTiles);

/** Lowest ground height under a footprint (sampled at up to 12 vertices), so walls reach the ground on slopes. null when elevation is not loaded. */
export function footprintBase(outer, heightAt) {
  const step = Math.max(1, Math.floor(outer.length / 12)); let lo = Infinity;
  for (let i = 0; i < outer.length; i += step) { const h = heightAt(outer[i][0], outer[i][1]); if (h != null && h < lo) lo = h; }
  return Number.isFinite(lo) ? lo : null;
}
