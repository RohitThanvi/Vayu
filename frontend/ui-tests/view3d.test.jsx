import { test, mount, flush, click, key, text, q, qa, buttonWith, byLabel, eq, ok, has, lacks } from './harness.jsx';
import geojsonvt from 'geojson-vt';
import vtpbf from 'vt-pbf';
import { TILE, LRU, altitudeForZoom, decodeTerrarium, hash01, lonLatToGlobalPixel, lonLatToTile, metersPerPixel, tileCorner, tilesCoveringCircle } from '../src/lib/view3d/geo.js';
import { createElevationSampler } from '../src/lib/view3d/elevation.js';
import { createBaseImageryProvider, createHillshadeProvider, createTerrainProvider, watchImageryErrors, zoomForLevel } from '../src/lib/view3d/terrain.js';
import { BUILDING_ZOOM, buildingColorHex, footprintBase, getTileTemplate, parseBuildings, resetTileTemplate, tileUrl } from '../src/lib/view3d/buildings.js';
import { EOX_2016_ATTRIBUTION, EOX_2016_URL, GIBS_ATTRIBUTION, gibsUrl, imageryConfig } from '../src/lib/view3d/imagery.js';
import { QUALITY, detectQuality, utcForSolarHour, view3dEnabled } from '../src/lib/view3d/quality.js';
import { CESIUM_BASE, CESIUM_VERSION, loadCesium, CESIUM_JS_SRI, CESIUM_CSS_SRI } from '../src/lib/view3d/loadCesium.js';
import View3DLauncher from '../src/components/View3DLauncher.jsx';

const near = (a, b, tol = 1e-3, msg) => ok(Math.abs(a - b) <= tol, `${msg || 'not near'}: ${a} vs ${b}`);
// Encode metres as Terrarium RGBA, and build a fake tile server from a height function of global pixel (gx, gy).
const enc = (m) => { const v = m + 32768, r = Math.floor(v / 256), g = Math.floor(v) % 256, b = Math.floor((v - Math.floor(v)) * 256); return [r, g, b]; };
const tileServer = (fn, log = []) => async (z, x, y) => { log.push(`${z}/${x}/${y}`); const out = new Uint8ClampedArray(TILE * TILE * 4); for (let r = 0; r < TILE; r++) for (let c = 0; c < TILE; c++) { const [R, G, B] = enc(fn(z, x * TILE + c, y * TILE + r)); const p = (r * TILE + c) * 4; out[p] = R; out[p + 1] = G; out[p + 2] = B; out[p + 3] = 255; } return out; };

export function register() {
  // ------------------------------------------------------------------ geometry
  test('3D geo: tile maths round-trips and brackets the point', () => {
    eq(lonLatToTile(0.001, -0.001, 0), { z: 0, x: 0, y: 0 });
    for (const [lon, lat, z] of [[75.787, 26.912, 14], [-122.42, 37.77, 12], [151.2, -33.87, 15]]) {
      const t = lonLatToTile(lon, lat, z), a = tileCorner(z, t.x, t.y), b = tileCorner(z, t.x + 1, t.y + 1);
      ok(a.lon <= lon && lon < b.lon && b.lat < lat && lat <= a.lat, `${lon},${lat} inside its own tile`);
    }
    near(metersPerPixel(0, 0), 156543.03392804097, 1e-6); near(metersPerPixel(60, 10), 156543.03392804097 * 0.5 / 1024, 1e-3);
    ok(lonLatToGlobalPixel(10, 89.9, 3).gy >= 0 && lonLatToGlobalPixel(10, -89.9, 3).gy < 256 * 8, 'poles clamp inside the world');
  });
  test('3D geo: Terrarium decoding matches the documented formula', () => {
    const px = (r, g, b) => decodeTerrarium(new Uint8Array(TILE * TILE * 4).fill(0).map((_, i) => (i % 4 === 0 ? r : i % 4 === 1 ? g : i % 4 === 2 ? b : 255)))[0];
    eq(px(128, 0, 0), 0); eq(px(131, 232, 0), 1000); near(px(127, 255, 128), -0.5); eq(px(0, 0, 0), -32768);
  });
  test('3D geo: tiles around a point include the centre, stay capped, and keep the nearest when capped', () => {
    const t = tilesCoveringCircle(26.912, 75.787, 1500, 14), c = lonLatToTile(75.787, 26.912, 14);
    ok(t.some((k) => k.x === c.x && k.y === c.y), 'centre tile present'); ok(t.length >= 1 && t.length <= 9);
    const big = tilesCoveringCircle(26.9, 75.8, 60000, 14, 10); eq(big.length, 10); ok(big.some((k) => k.x === c.x && k.y === c.y), 'centre tile survives the cap');
    ok(tilesCoveringCircle(89.9, 10, 5000, 14).length > 0, 'polar latitudes do not blow up');
  });
  test('3D geo: initial altitude follows zoom and stays in range; LRU evicts the least recently used; hash is stable', () => {
    ok(altitudeForZoom(8, 26) > altitudeForZoom(12, 26), 'zoomed out is higher'); eq(altitudeForZoom(30, 26), 700); eq(altitudeForZoom(0, 0), 120000); eq(altitudeForZoom(NaN, 26) > 0, true);
    const l = new LRU(2); l.set('a', 1); l.set('b', 2); l.get('a'); l.set('c', 3); eq([l.has('a'), l.has('b'), l.has('c')], [true, false, true]);
    ok(hash01(5) === hash01(5) && hash01(5) !== hash01(6)); for (let i = 0; i < 50; i++) ok(hash01(i) >= 0 && hash01(i) < 1);
  });

  // ------------------------------------------------------------------ elevation
  test('3D elevation: grids are ordered north to south / west to east and match the source field', async () => {
    const o = lonLatToGlobalPixel(75.6, 26.9, 10), f = (z, gx, gy) => 1000 + (gx - o.gx) * 0.5 - (gy - o.gy) * 2;      // rises eastwards, FALLS southwards (gy grows south); relative so it stays inside Terrarium's +-32 km range
    const s = createElevationSampler({ fetchTile: tileServer(f) }), z = 10, w = 5, h = 4;
    const a = lonLatToGlobalPixel(75.5, 27.0, z), b = lonLatToGlobalPixel(75.7, 26.8, z);
    const g = await s.sampleGrid(75.5, 26.8, 75.7, 27.0, w, h, z);
    eq(g.length, w * h); ok(g[0] > g[(h - 1) * w], 'first row (north) is higher than last row (south)'); ok(g[w - 1] > g[0], 'east higher than west');
    near(g[0], f(z, a.gx - 0.5, a.gy - 0.5), 0.5, 'north-west sample'); near(g[w * h - 1], f(z, b.gx - 0.5, b.gy - 0.5), 0.5, 'south-east sample');
  });
  test('3D elevation: tiles are cached and de-duplicated, failures are retried, missing data is 0 not an error', async () => {
    const log = []; const s = createElevationSampler({ fetchTile: tileServer(() => 50, log) });
    await Promise.all([s.tile(8, 100, 100), s.tile(8, 100, 100), s.tile(8, 100, 100)]); await s.tile(8, 100, 100); eq(log.length, 1, 'one network fetch');
    let fail = true, n = 0; const s2 = createElevationSampler({ fetchTile: async () => { n++; if (fail) throw new Error('net'); return tileServer(() => 7)(8, 1, 1); } });
    eq(await s2.tile(8, 1, 1), null); fail = false; ok(await s2.tile(8, 1, 1), 'second attempt succeeds'); eq(n, 2);
    const s3 = createElevationSampler({ fetchTile: async () => null }), g = await s3.sampleGrid(75, 26, 75.1, 26.1, 3, 3, 9); eq([...g], new Array(9).fill(0));
    eq(await s3.tile(5, 0, -1), null, 'outside the world');
  });
  test('3D elevation: heightNow is null until the tiles are loaded, then interpolates', async () => {
    const o = lonLatToGlobalPixel(75.8, 26.9, 13), f = (z, gx) => 1000 + (gx - o.gx) * 0.25, s = createElevationSampler({ fetchTile: tileServer(f) });
    eq(s.heightNow(75.8, 26.9, 13), null); await s.prefetch(75.79, 26.89, 75.81, 26.91, 13);
    const p = lonLatToGlobalPixel(75.8, 26.9, 13); near(s.heightNow(75.8, 26.9, 13), f(13, p.gx - 0.5), 0.3);
  });
  test('3D elevation: hillshade lights slopes that face the sun, darkens the others, and leaves flat ground clear', async () => {
    const mk = (fn) => createElevationSampler({ fetchTile: tileServer(fn) });
    const centre = (rgba) => { const p = (128 * TILE + 128) * 4; return { v: rgba[p], a: rgba[p + 3] }; };
    const ox = 2000 * TILE, oy = 1500 * TILE, rel = (gx, gy) => (gx - ox) + (gy - oy);
    const faceNW = centre(await mk((z, gx, gy) => 1000 + rel(gx, gy) * 4).hillshade(12, 2000, 1500));        // rises towards the south-east, so it faces north-west (the light)
    const faceSE = centre(await mk((z, gx, gy) => 1000 - rel(gx, gy) * 4).hillshade(12, 2000, 1500));
    const flat = centre(await mk(() => 300).hillshade(12, 2000, 1500));
    ok(faceNW.v === 255 && faceNW.a > 0, `NW-facing slope lit: ${JSON.stringify(faceNW)}`); ok(faceSE.v === 0 && faceSE.a > 0, `SE-facing slope in shadow: ${JSON.stringify(faceSE)}`); eq(flat.a, 0, 'flat ground transparent');
    const gap = createElevationSampler({ fetchTile: async () => null }); ok((await gap.hillshade(12, 5, 5)).every((b) => b === 0), 'no data -> fully transparent overlay');
  });

  // ------------------------------------------------------------------ Cesium bindings (fake Cesium)
  test('3D terrain: provider asks the sampler for the tile rectangle in degrees, row-major, at a capped zoom', async () => {
    const seen = []; let opts = null;
    const C = { Math: { toDegrees: (r) => r * 180 / Math.PI }, GeographicTilingScheme: class { tileXYToRectangle(x, y, l) { return { west: -0.2 + x, south: -0.3 + y, east: -0.1 + x, north: 0.1 + y + l * 0 }; } }, CustomHeightmapTerrainProvider: class { constructor(o) { opts = o; } } };
    createTerrainProvider(C, { sampleGrid: async (...a) => { seen.push(a); return new Float32Array(1); } }, { credit: 'c' });
    eq([opts.width, opts.height, opts.credit], [65, 65, 'c']); await opts.callback(1, 2, 7);
    const [w, s, e, n, cols, rows, zoom] = seen[0]; near(w, (-0.2 + 1) * 180 / Math.PI, 1e-9); near(n, (0.1 + 2) * 180 / Math.PI, 1e-9); ok(s < n && w < e); eq([cols, rows, zoom], [65, 65, 7]);
    await opts.callback(0, 0, 22); eq(seen[1][6], 15, 'beyond the data zoom the finest tiles are reused'); eq(zoomForLevel(-3), 0);
  });
  test('3D terrain: hillshade imagery draws the sampler output on a canvas and base imagery uses the configured levels', async () => {
    let made = null; const calls = [];
    const C = { UrlTemplateImageryProvider: class { constructor(o) { made = o; } } };
    globalThis.ImageData = class { constructor(d, w, h) { this.data = d; this.width = w; this.height = h; } };
    const ctx = { putImageData: (img) => calls.push(img) }, cv = { getContext: () => ctx };
    const rgba = new Uint8ClampedArray(TILE * TILE * 4).fill(9);
    const p = createHillshadeProvider(C, { hillshade: async (z, x, y) => { calls.push([z, x, y]); return rgba; } }, { minZoom: 7, maxZoom: 15, credit: 'k', makeCanvas: () => cv });
    eq([made.minimumLevel, made.maximumLevel, made.credit], [7, 15, 'k']); has(made.url, '{z}/{x}/{y}');
    const out = await p.requestImage(3, 4, 11); ok(out === cv); eq(calls[0], [11, 3, 4]); ok(calls[1].data === rgba && calls[1].width === 256);
    let base = null; createBaseImageryProvider({ UrlTemplateImageryProvider: class { constructor(o) { base = o; } } }, { url: 'https://x/{z}/{x}/{y}.jpg', maxZoom: 9, attribution: 'a' }); eq([base.url, base.maximumLevel, base.credit], ['https://x/{z}/{x}/{y}.jpg', 9, 'a']);
  });

  // ------------------------------------------------------------------ buildings
  const T = { x: lonLatToTile(75.8, 26.9, BUILDING_ZOOM).x, y: lonLatToTile(75.8, 26.9, BUILDING_ZOOM).y };
  const sq = (lon, lat, d) => [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]];
  const mkTile = (features, layer = 'building') => { const idx = new geojsonvt({ type: 'FeatureCollection', features }, { maxZoom: 14, indexMaxZoom: 14, tolerance: 0 }); return vtpbf.fromGeojsonVt({ [layer]: idx.getTile(BUILDING_ZOOM, T.x, T.y) }, { version: 2 }); };
  const F = (props, coords, type = 'Polygon') => ({ type: 'Feature', properties: props, geometry: { type, coordinates: coords } });
  test('3D buildings: footprints, heights and the schema defaults are decoded from a tile', () => {
    const buf = mkTile([
      F({ render_height: 12, render_min_height: 0, colour: '#aa8866' }, [sq(75.785, 26.900, 0.0004)]),
      F({}, [sq(75.787, 26.900, 0.0004)]),                                                    // no height tags -> 5 m default
      F({ render_height: 0 }, [sq(75.789, 26.900, 0.0004)]),                                  // implausible -> floor of 3 m
      F({ render_height: 10, render_min_height: 40 }, [sq(75.791, 26.900, 0.0004)]),          // min above top -> clamped below the roof
      F({ render_height: 20, hide_3d: true }, [sq(75.793, 26.900, 0.0004)]),                  // outline of a building drawn as parts
      F({ render_height: 9 }, [sq(75.795, 26.900, 0.00001)]),                                 // a few square metres: dropped
      F({ render_height: 15 }, [[...sq(75.797, 26.900, 0.0006)], [[75.7973, 26.9002], [75.7973, 26.9004], [75.7975, 26.9004], [75.7975, 26.9002], [75.7973, 26.9002]]]),   // courtyard
      F({ render_height: 30 }, [[sq(75.799, 26.900, 0.0004)], [sq(75.801, 26.900, 0.0004)]], 'MultiPolygon'),
      F({ name: 'x' }, [75.8, 26.9], 'Point'),
    ]);
    const b = parseBuildings(buf, BUILDING_ZOOM, T.x, T.y);
    eq(b.map((x) => x.height), [12, 5, 3, 10, 15, 30, 30]); eq(b[0].colour, '#aa8866'); eq(b[3].minHeight, 9, 'clamped under the roof'); eq(b[4].rings.length, 2, 'hole kept'); eq(b[1].minHeight, 0);
    ok(b.every((x) => x.rings[0].length >= 4 && x.rings[0][0][0] > 75.78 && x.rings[0][0][0] < 75.81), 'coordinates are lon/lat');
    eq(parseBuildings(buf, BUILDING_ZOOM, T.x, T.y, { maxPerTile: 2 }).length, 2, 'per-tile cap');
    eq(parseBuildings(mkTile([F({ class: 'primary' }, [[75.785, 26.9], [75.79, 26.9]], 'LineString')], 'transportation'), BUILDING_ZOOM, T.x, T.y).length, 0, 'tile without a building layer');
  });
  test('3D buildings: wall colour is the OSM hex when valid, otherwise a stable muted neutral', () => {
    eq(buildingColorHex({ id: 1, height: 9, colour: '#112233' }), '#112233');
    const a = buildingColorHex({ id: 7, height: 9, colour: 'red' }); ok(/^#[0-9a-f]{6}$/i.test(a), 'named colours are not trusted'); eq(a, buildingColorHex({ id: 7, height: 9, colour: null }));
    const lo = parseInt(buildingColorHex({ id: 3, height: 3 }).slice(1), 16), hi = parseInt(buildingColorHex({ id: 3, height: 90 }).slice(1), 16); ok(hi >= lo, 'taller is not darker');
  });
  test('3D buildings: tile URL comes from the TileJSON once, bad TileJSON fails and can be retried', async () => {
    resetTileTemplate(); let n = 0; const good = async () => { n++; return { tiles: ['https://tiles.example/planet/20260101_pt/{z}/{x}/{y}.pbf'] }; };
    const [a, b] = await Promise.all([getTileTemplate(good), getTileTemplate(good)]); eq(a, b); eq(n, 1, 'fetched once'); eq(tileUrl(a, 14, 11, 12), 'https://tiles.example/planet/20260101_pt/14/11/12.pbf');
    resetTileTemplate(); let threw = false; try { await getTileTemplate(async () => ({ tiles: ['https://nope/no-placeholders'] })); } catch { threw = true; } ok(threw, 'rejects a template without {z}/{x}/{y}');
    eq(await getTileTemplate(good), a, 'a later call retries after a failure'); resetTileTemplate();
  });
  test('3D buildings: base height is the lowest sampled ground point, and null when elevation is not loaded', () => {
    const ring = sq(75.8, 26.9, 0.001); eq(footprintBase(ring, (lo, la) => 400 + (lo - 75.8) * 10000 + (la - 26.9) * 5000), 400); eq(footprintBase(ring, () => null), null);
    eq(footprintBase(ring, (lo) => (lo > 75.8005 ? null : 250)), 250, 'unloaded vertices are ignored');
  });

  // ------------------------------------------------------------------ config, quality, loader
  test('3D imagery: EOX 2016 (the only commercially licensed EOX mosaic) by default with a GIBS fallback; custom https template accepted; bad one falls back with a warning', () => {
    const now = new Date('2026-11-21T00:30:00Z'); has(gibsUrl(now), '/2026-11-20/'); has(gibsUrl(now), '{z}/{y}/{x}.jpg');
    const d = imageryConfig({}, now); eq([d.source, d.maxZoom, d.url, d.attribution], ['eox2016', 14, EOX_2016_URL, EOX_2016_ATTRIBUTION]); eq([d.fallback.source, d.fallback.maxZoom, d.fallback.attribution], ['gibs', 9, GIBS_ATTRIBUTION]);
    ok(/s2cloudless-2016_3857/.test(EOX_2016_URL) && !/s2cloudless-20(1[89]|2\d)/.test(EOX_2016_URL), 'only the CC BY 2016 layer; later EOX mosaics are non-commercial'); has(EOX_2016_ATTRIBUTION, 'Contains modified Copernicus Sentinel data 2016'); has(EOX_2016_ATTRIBUTION, 'EOX IT Services GmbH');
    eq(imageryConfig({ VITE_3D_IMAGERY_SOURCE: 'gibs' }, now).source, 'gibs'); ok(!imageryConfig({ VITE_3D_IMAGERY_SOURCE: 'gibs' }, now).fallback, 'GIBS has no further fallback');
    const c = imageryConfig({ VITE_3D_IMAGERY_URL: 'https://t.example/{z}/{x}/{y}.jpg', VITE_3D_IMAGERY_MAX_ZOOM: '14', VITE_3D_IMAGERY_ATTRIBUTION: 'Ours' }, now); eq([c.source, c.maxZoom, c.attribution, c.fallback.source], ['custom', 14, 'Ours', 'gibs']);
    eq(imageryConfig({ VITE_3D_IMAGERY_URL: 'https://t.example/{z}/{x}/{y}.jpg', VITE_3D_IMAGERY_MAX_ZOOM: '99' }, now).maxZoom, 19);
    for (const bad of ['http://t.example/{z}/{x}/{y}.jpg', 'https://t.example/{z}/{x}.jpg']) { const r = imageryConfig({ VITE_3D_IMAGERY_URL: bad }, now); eq(r.source, 'gibs'); has(r.warning, 'https'); }
  });
  test('3D imagery: the error watcher fires once at the limit, ignores later errors, and can be unsubscribed', () => {
    const mk = () => { const hs = []; return { hs, errorEvent: { addEventListener: (h) => hs.push(h), removeEventListener: (h) => { const k = hs.indexOf(h); if (k >= 0) hs.splice(k, 1); } } }; };
    const p = mk(); let fails = 0; watchImageryErrors(p, { limit: 3, onFail: () => fails++ }); const fire = () => p.hs.slice().forEach((h) => h({}));
    fire(); fire(); eq(fails, 0); fire(); eq(fails, 1); fire(); fire(); eq(fails, 1, 'fires once'); eq(p.hs.length, 0, 'listener removed after firing');
    const p2 = mk(); const off = watchImageryErrors(p2, { limit: 2, onFail: () => fails++ }); off(); eq(p2.hs.length, 0); watchImageryErrors({}, { onFail: () => {} });
    const hs3 = []; let f3 = 0; watchImageryErrors({ errorEvent: { addEventListener: (h) => hs3.push(h) } }, { limit: 2, onFail: () => f3++ }); for (let i = 0; i < 6; i++) hs3.forEach((h) => h({})); eq(f3, 1, 'still fires once when the event cannot unsubscribe');
  });
  test('3D quality: phones and weak devices start Low; the flag is on only for exactly "1"; the sun hour maps to a UTC instant', () => {
    eq(detectQuality({ mobile: true, deviceMemory: 16, hardwareConcurrency: 16 }), 'low'); eq(detectQuality({ deviceMemory: 4, hardwareConcurrency: 16 }), 'low'); eq(detectQuality({ deviceMemory: 8, hardwareConcurrency: 8 }), 'high'); eq(detectQuality({}), 'balanced');
    ok(QUALITY.low.buildingRadiusM < QUALITY.balanced.buildingRadiusM && QUALITY.balanced.maxBuildings < QUALITY.high.maxBuildings && QUALITY.low.elevationCache < QUALITY.high.elevationCache);
    eq([view3dEnabled({}), view3dEnabled({ VITE_ENABLE_3D_VIEW: '0' }), view3dEnabled({ VITE_ENABLE_3D_VIEW: 'true' }), view3dEnabled({ VITE_ENABLE_3D_VIEW: '1' })], [false, false, false, true]);
    eq(utcForSolarHour(12, 0, new Date('2026-11-21T09:00:00Z')).toISOString(), '2026-11-21T12:00:00.000Z'); eq(utcForSolarHour(12, 75, new Date('2026-11-21T09:00:00Z')).toISOString(), '2026-11-21T07:00:00.000Z');
  });
  test('3D loader: pinned version, loads once, resolves with window.Cesium, rejects and allows a retry on failure', async () => {
    eq(CESIUM_VERSION, '1.147.0'); has(CESIUM_BASE, 'cesium@1.147.0/Build/Cesium/');
    const mk = () => { const added = []; const doc = { head: { appendChild: (e) => added.push(e) }, createElement: (t) => ({ tag: t, remove() {} }) }; return { added, doc, win: {} }; };
    const pre = mk(); pre.win.Cesium = { v: 1 }; eq((await loadCesium(pre)).v, 1); eq(pre.added.length, 0, 'already loaded: nothing injected');
    const a = mk(); const p1 = loadCesium(a), p2 = loadCesium(a); eq(a.added.length, 2, 'one stylesheet and one script'); const script = a.added.find((e) => e.tag === 'script');
    has(script.src, 'cesium@1.147.0/Build/Cesium/Cesium.js'); eq(a.win.CESIUM_BASE_URL, CESIUM_BASE); eq(script.integrity, CESIUM_JS_SRI); eq(script.crossOrigin, 'anonymous'); const sheet = a.added.find((e) => e.tag === 'link'); eq(sheet.integrity, CESIUM_CSS_SRI); eq(sheet.crossOrigin, 'anonymous');
    ok(/^sha384-[A-Za-z0-9+/]{64}$/.test(CESIUM_JS_SRI) && /^sha384-[A-Za-z0-9+/]{64}$/.test(CESIUM_CSS_SRI), 'sha384 base64 digests');
    a.win.Cesium = { ok: 1 }; script.onload(); eq(await p1, await p2);
    const b = mk(); const pf = loadCesium(b); b.added.find((e) => e.tag === 'script').onerror(); let msg = ''; try { await pf; } catch (e) { msg = e.message; } has(msg, 'Could not load the 3D engine');
    const retry = loadCesium(b); eq(b.added.filter((e) => e.tag === 'script').length, 2, 'a retry injects a fresh script'); b.win.Cesium = { r: 1 }; b.added.filter((e) => e.tag === 'script')[1].onload(); eq((await retry).r, 1);
  });

  // ------------------------------------------------------------------ launcher + viewer component
  test('3D launcher: renders nothing unless the flag is "1"', async () => {
    for (const env of [{}, { VITE_ENABLE_3D_VIEW: '0' }]) { const m = await mount(<View3DLauncher getView={() => ({ lat: 1, lon: 2, zoom: 5 })} env={env} />); try { ok(!q(m.container, '[data-testid="open-3d"]'), 'no button'); } finally { await m.unmount(); } }
  });

  // A permissive stand-in for Cesium: every property is callable/assignable, Viewer is spied.
  const deep = () => { const store = new Map(); const p = new Proxy(function () {}, { get: (_, k) => { if (k === 'then') return undefined; if (k === Symbol.toPrimitive) return () => 0; if (!store.has(k)) store.set(k, deep()); return store.get(k); }, set: (_, k, v) => { store.set(k, v); return true; }, apply: () => deep(), construct: () => deep() }); return p; };
  const fakeCesium = (log) => { const C = deep();
    C.UrlTemplateImageryProvider = function (opts) { const p = deep(), hs = []; p.errorEvent = { addEventListener: (h) => hs.push(h), removeEventListener: (h) => { const k = hs.indexOf(h); if (k >= 0) hs.splice(k, 1); } }; (log.providers = log.providers || []).push({ opts, fire: () => hs.slice().forEach((h) => h({})) }); return p; };
    C.Viewer = function (el, opts) { log.opts = opts; log.el = el; const v = deep(); log.layers = []; v.imageryLayers = { addImageryProvider: (prov, idx) => { const l = { alpha: 1, prov, idx }; log.layers.push(l); return l; }, remove: (l) => { log.removed = (log.removed || 0) + 1; log.removedLayer = l; } }; v.destroy = () => { log.destroyed = (log.destroyed || 0) + 1; }; v.isDestroyed = () => false; log.viewer = v; return v; }; return C; };
  const open = async (log, props = {}) => { window.Cesium = fakeCesium(log); const closed = { n: 0 }; const Terrain3D = (await import('../src/components/Terrain3D.jsx')).default; const m = await mount(<Terrain3D center={{ lat: 26.9, lon: 75.8 }} zoom={12} onClose={() => closed.n++} {...props} />); await flush(40); return { m, closed }; };

  test('3D view: starts the viewer on free sources only, shows every attribution, and tears down on close', async () => {
    const log = {}; const { m, closed } = await open(log);
    try {
      ok(!q(m.container, '[data-testid="terrain3d-loading"]'), 'past the loading state'); eq(log.opts.baseLayer, false, 'no Cesium ion default imagery'); ok(log.opts.terrainProvider, 'our own terrain provider'); eq(log.opts.requestRenderMode, true);
      const credit = text(q(m.container, '[data-testid="terrain3d-credit"]')); for (const t of ['OpenFreeMap', 'OpenStreetMap', 'Tilezen', 'Sentinel-2 cloudless', 'EOX IT Services GmbH', 'Copernicus Sentinel data 2016']) has(credit, t); lacks(credit, 'NASA GIBS');
      lacks(text(m.container), 'Cesium ion'); await click(byLabel(m.container, 'Close 3D view')); eq(closed.n, 1); await key(window, 'Escape'); eq(closed.n, 2, 'Escape closes too');
    } finally { await m.unmount(); }
    eq(log.destroyed, 1, 'viewer destroyed exactly once on unmount');
    delete window.Cesium;
  });
  test('3D view: quality buttons change the render settings live (Low is lighter; High renders at device pixels)', async () => {
    const log = {}; const { m } = await open(log);
    try {
      await click(buttonWith(m.container, 'High')); eq(log.viewer.useBrowserRecommendedResolution, false); eq(log.viewer.resolutionScale, 1);
      await click(buttonWith(m.container, 'Low')); eq(log.viewer.useBrowserRecommendedResolution, true); eq(log.viewer.resolutionScale, 0.75); eq(log.viewer.scene.globe.maximumScreenSpaceError, QUALITY.low.maximumScreenSpaceError); eq(log.viewer.scene.msaaSamples, 1);
      eq(byLabel(m.container, 'Time of day').type, 'range');
    } finally { await m.unmount(); delete window.Cesium; }
  });
  test('3D view: if the sharper imagery keeps failing it swaps to NASA GIBS under the hillshade, once, and says so; a few errors are tolerated', async () => {
    const log = {}; const { m } = await open(log);
    try {
      eq(log.layers.length, 2, 'base imagery + hillshade'); const base = log.providers[0]; has(base.opts.url, 's2cloudless-2016_3857'); eq(base.opts.maximumLevel, 14);
      for (let i = 0; i < 5; i++) base.fire(); await flush(10); ok(!log.removed, 'below the limit: no swap'); lacks(text(q(m.container, '[data-testid="terrain3d-credit"]')), 'NASA GIBS');
      base.fire(); await flush(20); eq(log.removed, 1, 'failed base layer removed'); const added = log.layers[log.layers.length - 1]; eq(added.idx, 0, 'fallback goes UNDER the hillshade'); has(added.prov === undefined ? '' : JSON.stringify(log.providers[log.providers.length - 1].opts.url), 'gibs.earthdata.nasa.gov');
      eq(log.providers[log.providers.length - 1].opts.maximumLevel, 9); has(text(m.container), 'coarser NASA GIBS imagery is shown instead'); has(text(q(m.container, '[data-testid="terrain3d-credit"]')), 'NASA GIBS'); lacks(text(q(m.container, '[data-testid="terrain3d-credit"]')), 'EOX');
      for (let i = 0; i < 10; i++) base.fire(); await flush(10); eq(log.removed, 1, 'swaps only once');
    } finally { await m.unmount(); delete window.Cesium; }
  });
  test('3D view: building shadows are on only at High quality', async () => {
    const log = {}; const { m } = await open(log);
    try {
      await click(buttonWith(m.container, 'High')); eq(log.viewer.shadows, true); eq(log.viewer.shadowMap.size, 2048); eq(log.viewer.shadowMap.softShadows, true);
      await click(buttonWith(m.container, 'Low')); eq(log.viewer.shadows, false); await click(buttonWith(m.container, 'Balanced')); eq(log.viewer.shadows, false);
      eq([QUALITY.low.shadows, QUALITY.balanced.shadows, QUALITY.high.shadows], [false, false, true]);
    } finally { await m.unmount(); delete window.Cesium; }
  });
  test('3D view: a start-up failure (no WebGL) shows a clear message and leaves the app usable', async () => {
    window.Cesium = deep(); window.Cesium.Viewer = function () { throw new Error('WebGL is not supported.'); }; const closed = { n: 0 };
    const Terrain3D = (await import('../src/components/Terrain3D.jsx')).default; const m = await mount(<Terrain3D center={{ lat: 26.9, lon: 75.8 }} zoom={12} onClose={() => closed.n++} />); await flush(40);
    try { const t = text(q(m.container, '[data-testid="terrain3d-error"]')); has(t, 'WebGL is not supported.'); has(t, 'normal map is unaffected'); await click(buttonWith(q(m.container, '[data-testid="terrain3d-error"]'), 'Back to the map')); eq(closed.n, 1); }
    finally { await m.unmount(); delete window.Cesium; }
  });
  test('3D launcher: with the flag on, the button opens the view at the map position and Close returns to the map', async () => {
    const log = {}; window.Cesium = fakeCesium(log); let asked = 0;
    const m = await mount(<View3DLauncher env={{ VITE_ENABLE_3D_VIEW: '1' }} getView={() => { asked++; return { lat: 26.9, lon: 75.8, zoom: 11 }; }} />);
    try {
      await click(q(m.container, '[data-testid="open-3d"]')); await flush(60); eq(asked, 1); ok(q(m.container, '[data-testid="terrain3d"]'), 'view open');
      await click(byLabel(m.container, 'Close 3D view')); await flush(20); ok(!q(m.container, '[data-testid="terrain3d"]'), 'view closed'); ok(q(m.container, '[data-testid="open-3d"]'), 'button back');
    } finally { await m.unmount(); delete window.Cesium; }
    const m2 = await mount(<View3DLauncher env={{ VITE_ENABLE_3D_VIEW: '1' }} getView={() => null} />);
    try { await click(q(m2.container, '[data-testid="open-3d"]')); await flush(30); ok(!q(m2.container, '[data-testid="terrain3d"]'), 'no map yet -> nothing opens'); } finally { await m2.unmount(); }
  });
}
