import { useEffect, useRef, useState, useCallback } from 'react';
import { loadCesium } from '../lib/view3d/loadCesium.js';
import { createElevationSampler, TERRAIN_ATTRIBUTION } from '../lib/view3d/elevation.js';
import { createTerrainProvider, createBaseImageryProvider, createHillshadeProvider, watchImageryErrors } from '../lib/view3d/terrain.js';
import { imageryConfig } from '../lib/view3d/imagery.js';
import { BUILDINGS_ATTRIBUTION, DEFAULT_HEIGHT_NOTE, buildingColorHex, buildingTilesAround, footprintBase, getTileTemplate, parseBuildings, tileUrl, BUILDING_ZOOM } from '../lib/view3d/buildings.js';
import { QUALITY, detectQuality, utcForSolarHour } from '../lib/view3d/quality.js';
import { altitudeForZoom, tileCorner } from '../lib/view3d/geo.js';
import { createTileStats, problemText } from '../lib/view3d/diagnostics.js';

// Optional 3D view: free, commercial-safe data only (no Cesium ion, no Google tiles). Terrain = AWS/Mapzen Terrain Tiles, relief = computed hillshade, buildings = OpenFreeMap (OSM),
// imagery = EOX Sentinel-2 cloudless 2016 by default (falls back to NASA GIBS). It sits on top of the app as an overlay and is off unless VITE_ENABLE_3D_VIEW=1. See README, "Optional 3D view".
const S = { text: '#e8efe9', dim: '#9fb0a5', bg: 'rgba(10,18,14,0.82)', border: 'rgba(255,255,255,0.14)', accent: '#7eb8d4', mono: "'JetBrains Mono','Courier New',monospace" };
const ELEV_ZOOM = 13;       // zoom used for building base heights (the source data is ~30 m, so more detail would not be real)

export default function Terrain3D({ center, zoom, onClose, watchdogMs = 8000 }) {
  const host = useRef(null), api = useRef({}), [status, setStatus] = useState('loading'), [error, setError] = useState(null);
  const [quality, setQuality] = useState(() => detectQuality({ deviceMemory: navigator.deviceMemory, hardwareConcurrency: navigator.hardwareConcurrency, mobile: /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent) }));
  const [hour, setHour] = useState(15), [count, setCount] = useState(0), [notes, setNotes] = useState([]), [problem, setProblem] = useState(null);
  const [imgInfo, setImgInfo] = useState(() => { const c = imageryConfig(import.meta.env); return { source: c.source, attribution: c.attribution }; });

  useEffect(() => {
    let cancelled = false, viewer = null; const aborters = new Set(); const timers = [];
    (async () => {
      try {
        const Cesium = await loadCesium(); if (cancelled) return;
        const fetchTile = async (z, x, y) => {
          const ctl = new AbortController(); aborters.add(ctl);
          try {
            const r = await fetch(`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${z}/${x}/${y}.png`, { signal: ctl.signal }); if (!r.ok) return null;
            const bmp = await createImageBitmap(await r.blob()), c = document.createElement('canvas'); c.width = c.height = 256;
            const ctx = c.getContext('2d', { willReadFrequently: true }); ctx.drawImage(bmp, 0, 0); return ctx.getImageData(0, 0, 256, 256).data;
          } finally { aborters.delete(ctl); }
        };
        const tileStats = createTileStats(), t0 = Date.now(), note = (m) => setNotes((n) => (n.includes(m) ? n : [...n, m]));
        const sampler = createElevationSampler({ fetchTile, cacheSize: QUALITY[quality].elevationCache }), img = imageryConfig(import.meta.env);
        setImgInfo({ source: img.source, attribution: img.attribution }); if (img.warning) note(img.warning);
        // Errors Cesium raises itself for terrain tiles (building the mesh, its web workers) never pass through our sampler, so listen for them too.
        const terrainProvider = createTerrainProvider(Cesium, sampler, { credit: TERRAIN_ATTRIBUTION, onResult: tileStats.terrain });
        terrainProvider.errorEvent?.addEventListener?.((e) => tileStats.mesh({ ok: false, message: e && (e.message || (e.error && e.error.message)) }));
        viewer = new Cesium.Viewer(host.current, {
          baseLayer: false, terrainProvider, requestRenderMode: true,
          baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false, navigationHelpButton: false, animation: false, timeline: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
        });
        const scene = viewer.scene, globe = scene.globe;
        // Where no imagery has loaded, show a neutral ground colour. Cesium's default is solid blue, which looks like a broken blue screen rather than "no imagery".
        globe.baseColor = Cesium.Color.fromCssColorString('#26332b');
        const baseProvider = createBaseImageryProvider(Cesium, img, { onResult: tileStats.imagery }); let baseLayer = viewer.imageryLayers.addImageryProvider(baseProvider), swapped = false;
        // The sharper imagery's URL could not be tested from the dev sandbox. Two independent ways to notice a dead source: repeated tile errors, or simply no tile loading within watchdogMs. Either swaps once to the coarse
        // NASA GIBS layer (under the hillshade); if that also loads nothing, the view says so and carries on with relief shading and buildings.
        const swapToFallback = (why) => {
          if (swapped || !img.fallback || cancelled || viewer.isDestroyed()) return false; swapped = true;
          console.warn(`[Vayu 3D] imagery source failed (${why}); switching to NASA GIBS.`);
          viewer.imageryLayers.remove(baseLayer, true); tileStats.resetImagery();
          baseLayer = viewer.imageryLayers.addImageryProvider(createBaseImageryProvider(Cesium, img.fallback, { onResult: tileStats.imagery }), 0);
          setImgInfo({ source: img.fallback.source, attribution: img.fallback.attribution }); note('The sharper imagery could not be loaded, so coarser NASA GIBS imagery is shown instead.'); scene.requestRender(); return true;
        };
        if (img.fallback) watchImageryErrors(baseProvider, { onFail: () => swapToFallback('repeated tile errors') });
        const NO_IMAGERY = 'No map imagery could be loaded, so only relief shading and buildings are shown.';
        const watch = (after) => { const id = setTimeout(() => { if (cancelled || viewer.isDestroyed() || tileStats.stats.imagery.ok > 0) return; after(); }, watchdogMs); timers.push(() => clearTimeout(id)); };
        watch(() => { if (swapToFallback(`no tile loaded within ${Math.round(watchdogMs / 1000)} s`)) watch(() => note(NO_IMAGERY)); else note(NO_IMAGERY); });
        viewer.imageryLayers.addImageryProvider(createHillshadeProvider(Cesium, sampler, { credit: TERRAIN_ATTRIBUTION })).alpha = 0.6;
        // With on-demand rendering Cesium normally redraws as tiles arrive; a short heartbeat guarantees progressive drawing during start-up whatever the source of a late tile.
        const beat = setInterval(() => { if (!viewer.isDestroyed()) scene.requestRender(); }, 400); timers.push(() => clearInterval(beat)); const beatEnd = setTimeout(() => clearInterval(beat), 25000); timers.push(() => clearTimeout(beatEnd));
        let lastProblem = null;
        const diag = setInterval(() => { const t = problemText(tileStats.stats, Date.now() - t0, Math.max(watchdogMs - 1000, 1000)); setProblem(t); if (t && t !== lastProblem) { lastProblem = t; console.warn(`[Vayu 3D] ${t}`); } }, 1000); timers.push(() => clearInterval(diag));
        globe.depthTestAgainstTerrain = true; globe.enableLighting = true; globe.showGroundAtmosphere = true;
        scene.fog.enabled = true; scene.skyAtmosphere.show = true; scene.screenSpaceCameraController.minimumZoomDistance = 60;
        viewer.clock.shouldAnimate = false;
        api.current = { Cesium, viewer, sampler, applyQuality: null };

        // Cesium renders at the browser's recommended (CSS-pixel) resolution by default; only High asks for full device pixels.
        const apply = (qk) => { const q = QUALITY[qk]; viewer.useBrowserRecommendedResolution = qk !== 'high'; viewer.resolutionScale = q.resolutionScale; globe.maximumScreenSpaceError = q.maximumScreenSpaceError; scene.msaaSamples = q.msaaSamples; viewer.shadows = !!q.shadows; if (q.shadows) { viewer.shadowMap.softShadows = true; viewer.shadowMap.size = 2048; viewer.shadowMap.maximumDistance = 6000; } scene.requestRender(); };
        api.current.applyQuality = apply; apply(quality);

        const alt = altitudeForZoom(zoom, center.lat), pitch = -38, back = alt / Math.tan(-pitch * Math.PI / 180) / 111320;
        const home = () => viewer.camera.setView({ destination: Cesium.Cartesian3.fromDegrees(center.lon, center.lat - back, alt), orientation: { heading: 0, pitch: Cesium.Math.toRadians(pitch), roll: 0 } });
        api.current.home = home; home();

        // ---- buildings: tiles near the view centre, nearest first, within the quality's budget ----
        const tiles = new Map(); let gen = 0, tpl = null;
        const groundPoint = () => {
          const c = viewer.canvas, p = viewer.camera.pickEllipsoid(new Cesium.Cartesian2(c.clientWidth / 2, c.clientHeight / 2)); if (!p) return null;
          const g = Cesium.Cartographic.fromCartesian(p); return { lat: Cesium.Math.toDegrees(g.latitude), lon: Cesium.Math.toDegrees(g.longitude) };
        };
        const buildTile = async (t, my) => {
          const key = `${t.x}/${t.y}`; if (tiles.has(key)) return 0; tiles.set(key, { primitive: null, n: 0 });
          const ctl = new AbortController(); aborters.add(ctl);
          try {
            tpl = tpl || await getTileTemplate();
            const r = await fetch(tileUrl(tpl, BUILDING_ZOOM, t.x, t.y), { signal: ctl.signal }); if (!r.ok) { tiles.delete(key); return 0; }
            const list = parseBuildings(await r.arrayBuffer(), BUILDING_ZOOM, t.x, t.y), nw = tileCorner(BUILDING_ZOOM, t.x, t.y), se = tileCorner(BUILDING_ZOOM, t.x + 1, t.y + 1);
            await sampler.prefetch(nw.lon, se.lat, se.lon, nw.lat, ELEV_ZOOM); if (cancelled || my !== gen) { tiles.delete(key); return 0; }
            const instances = [], hAt = (lo, la) => sampler.heightNow(lo, la, ELEV_ZOOM);
            for (const b of list) {
              const base = footprintBase(b.rings[0], hAt); if (base == null) continue;
              const flat = (ring) => ring.flatMap(([lo, la]) => [lo, la]);
              const hier = new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(flat(b.rings[0])), b.rings.slice(1).map((h) => new Cesium.PolygonHierarchy(Cesium.Cartesian3.fromDegreesArray(flat(h)))));
              instances.push(new Cesium.GeometryInstance({
                geometry: new Cesium.PolygonGeometry({ polygonHierarchy: hier, height: b.minHeight > 0 ? base + b.minHeight : base - 3, extrudedHeight: base + b.height, vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT }),
                attributes: { color: Cesium.ColorGeometryInstanceAttribute.fromColor(Cesium.Color.fromCssColorString(buildingColorHex(b))) },
              }));
            }
            if (!instances.length) return 0;
            const primitive = new Cesium.Primitive({ geometryInstances: instances, appearance: new Cesium.PerInstanceColorAppearance({ translucent: false, closed: true }), asynchronous: true, shadows: Cesium.ShadowMode.ENABLED });
            scene.primitives.add(primitive); tiles.set(key, { primitive, n: instances.length }); scene.requestRender(); return instances.length;
          } catch (e) { tiles.delete(key); if (e.name !== 'AbortError') setNotes((n) => (n.includes('Some building tiles could not be loaded.') ? n : [...n, 'Some building tiles could not be loaded.'])); return 0; }
          finally { aborters.delete(ctl); }
        };
        const refresh = async () => {
          if (cancelled) return; const q = QUALITY[api.current.quality || quality], my = ++gen, h = viewer.camera.positionCartographic.height;
          if (h > 9000) return;                                                       // buildings only make sense close in
          const gp = groundPoint(); if (!gp) return;
          const want = buildingTilesAround(gp.lat, gp.lon, q.buildingRadiusM), keep = new Set(want.map((t) => `${t.x}/${t.y}`));
          for (const [k, v] of tiles) if (!keep.has(k) && v.primitive) { scene.primitives.remove(v.primitive); tiles.delete(k); }
          let total = [...tiles.values()].reduce((s, v) => s + v.n, 0);
          for (const t of want) { if (cancelled || my !== gen || total >= q.maxBuildings) break; total += await buildTile(t, my); }
          if (!cancelled) setCount([...tiles.values()].reduce((s, v) => s + v.n, 0));
        };
        api.current.refresh = refresh; let deb = null;
        viewer.camera.moveEnd.addEventListener(() => { clearTimeout(deb); deb = setTimeout(refresh, 450); }); timers.push(() => clearTimeout(deb));
        setTimeout(refresh, 1200);
        api.current.setHour = (hh) => { viewer.clock.currentTime = Cesium.JulianDate.fromDate(utcForSolarHour(hh, center.lon)); scene.requestRender(); };
        api.current.setHour(15);
        setStatus('ready');
      } catch (e) { if (!cancelled) { setError(e.message || String(e)); setStatus('error'); } }
    })();
    return () => { cancelled = true; aborters.forEach((a) => a.abort()); timers.forEach((t) => t()); try { viewer && !viewer.isDestroyed() && viewer.destroy(); } catch { /* already gone */ } api.current = {}; };
  // The viewer is created once per opening; quality and hour are applied live below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { if (api.current.applyQuality) { api.current.quality = quality; api.current.applyQuality(quality); api.current.refresh?.(); } }, [quality]);
  useEffect(() => { api.current.setHour?.(hour); }, [hour]);
  useEffect(() => { const k = (e) => { if (e.key === 'Escape') onClose(); }; window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k); }, [onClose]);
  const btn = (on) => ({ padding: '4px 9px', fontSize: 12, fontFamily: S.mono, background: on ? 'rgba(126,184,212,0.2)' : 'transparent', border: `1px solid ${on ? S.accent : S.border}`, color: on ? S.accent : S.text, cursor: 'pointer' });
  const credit = useCallback(() => [BUILDINGS_ATTRIBUTION, TERRAIN_ATTRIBUTION, imgInfo.attribution].join(' \u00B7 '), [imgInfo]);

  return (
    <div data-testid="terrain3d" role="dialog" aria-label="3D view" style={{ position: 'fixed', inset: 0, zIndex: 2100, background: '#05080a' }}>
      <div ref={host} data-testid="terrain3d-host" style={{ position: 'absolute', inset: 0 }} />
      <div style={{ position: 'absolute', top: 10, left: 10, right: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', background: S.bg, border: `1px solid ${S.border}`, padding: '6px 9px', color: S.text, fontSize: 12, fontFamily: S.mono }}>
        <b style={{ letterSpacing: 1.5 }}>3D VIEW</b>
        <span style={{ color: S.dim }}>Quality</span>
        {Object.entries(QUALITY).map(([k, v]) => <button key={k} onClick={() => setQuality(k)} aria-pressed={quality === k} style={btn(quality === k)}>{v.label}</button>)}
        <label style={{ color: S.dim, display: 'flex', alignItems: 'center', gap: 6 }}>Sun (local solar hour)
          <input type="range" min="0" max="24" step="0.5" value={hour} onChange={(e) => setHour(Number(e.target.value))} aria-label="Time of day" />{hour}h</label>
        <button onClick={() => api.current.home?.()} style={btn(false)}>Reset view</button>
        <button onClick={onClose} aria-label="Close 3D view" style={{ ...btn(false), marginLeft: 'auto' }}>Close</button>
      </div>
      {status === 'loading' && <div data-testid="terrain3d-loading" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', color: S.text, fontFamily: S.mono, fontSize: 14, pointerEvents: 'none' }}>Loading 3D engine…</div>}
      {status === 'error' && (
        <div data-testid="terrain3d-error" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
          <div style={{ maxWidth: 420, background: S.bg, border: `1px solid ${S.border}`, padding: 16, color: S.text, fontSize: 14, lineHeight: 1.6 }}>
            The 3D view could not start: {error}<div style={{ color: S.dim, fontSize: 12, marginTop: 6 }}>It needs WebGL and an internet connection. The normal map is unaffected.</div>
            <button onClick={onClose} style={{ ...btn(false), marginTop: 10 }}>Back to the map</button>
          </div>
        </div>
      )}
      <div style={{ position: 'absolute', left: 10, right: 10, bottom: 34, color: S.dim, fontSize: 11, lineHeight: 1.5, fontFamily: S.mono, pointerEvents: 'none', textShadow: '0 1px 2px #000' }}>
        {count > 0 && <div data-testid="terrain3d-count">{count} buildings shown. {DEFAULT_HEIGHT_NOTE} Terrain is ~30 m resolution; relief shading is computed. Visualisation only.</div>}
        {notes.map((n) => <div key={n}>{n}</div>)}
        {problem && <div data-testid="terrain3d-problem" style={{ color: '#e0b36a' }}>{problem}</div>}
        <div data-testid="terrain3d-credit">{credit()}</div>
      </div>
    </div>
  );
}
