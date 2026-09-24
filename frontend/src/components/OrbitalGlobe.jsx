/**
 * OrbitalGlobe.jsx
 * A standalone 3D Earth view — separate from the main 2D Leaflet
 * operational map (that map's whole stack — vessel markers, AOI drawing,
 * intel layers — is Leaflet-specific, so this is a dedicated view users
 * switch to, not a replacement for the main map).
 *
 * This component is a pure 3D renderer — it owns no data-fetching, no
 * search/filter state, and no info card of its own. All of that
 * (satellite/aircraft tracking, toggles, search, selection, the detail
 * readout) lives in the left Sidebar's Orbital tab, same place every
 * other tab's controls and detail views live, rather than floating
 * inside the 3D canvas itself. This component just takes the already-
 * categorized position lists as props, renders them, and reports clicks
 * back up via onSelect — the same "dumb view, smart parent" split the
 * rest of the map (VayuMap) already follows.
 *
 * Renders a textured sphere (free NASA Blue Marble texture served from
 * three.js's own examples CDN — no key, no billing account, unlike
 * Cesium ion / Google Photorealistic 3D Tiles) with an atmosphere glow
 * shell, rotatable/zoomable via mouse/touch (OrbitControls) — panning is
 * disabled so it behaves like an actual globe you spin and zoom into
 * rather than a camera that can drift off center. Space stations, other
 * satellites, and aircraft each get a distinct hand-drawn glyph
 * (canvas-texture sprites — no external icon assets needed) instead of
 * a plain dot.
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const EARTH_RADIUS = 5;
// Local Blue Marble set (already shipped for the landing-page globe) —
// day color, night city-lights, cloud shell, specular ocean mask, and a
// bump/normal map. Using the bundled assets instead of an external CDN
// URL means no extra runtime dependency on raw.githubusercontent.com
// staying up, and gives us the specular + cloud layers the old
// single-flat-texture setup didn't have.
const EARTH_TEXTURE_URL = '/earth/earth_atmos_2048.jpg';
const EARTH_BUMP_URL = '/earth/earth_normal_2048.jpg';
const EARTH_SPECULAR_URL = '/earth/earth_specular_2048.jpg';
const EARTH_CLOUDS_URL = '/earth/earth_clouds_1024.png';

const COLORS = {
  station:   0xff6b6b,
  satellite: 0x9b8ce8,
  aircraft:  0xe8c15c,
};

// Two SEPARATE altitude-display curves, not one shared formula — a single
// curve tuned for satellite altitudes (hundreds-to-tens-of-thousands km)
// makes the entire 0-13km aircraft range collapse into a visually
// identical sliver, which is why every aircraft used to render at nearly
// the same height regardless of real flight level.
function satelliteAltToVec3(lat, lon, altKm, earthRadius) {
  const displayAlt = earthRadius * (0.06 + Math.min(altKm, 40000) / 40000 * 0.7);
  return latLonRadiusToVec3(lat, lon, earthRadius + displayAlt);
}

function aircraftAltToVec3(lat, lon, altKm, earthRadius) {
  // 0km (ground) -> ~1.5% of radius above surface; ~13km (typical cruise
  // ceiling) -> ~5% -- a real, visible spread between ground and cruise
  // traffic, staying well under the satellite curve's 6% baseline minimum.
  const clamped = Math.max(0, Math.min(altKm, 13));
  const displayAlt = earthRadius * (0.015 + (clamped / 13) * 0.035);
  return latLonRadiusToVec3(lat, lon, earthRadius + displayAlt);
}

function latLonRadiusToVec3(lat, lon, r) {
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lon + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -r * Math.sin(phi) * Math.cos(theta),
    r * Math.cos(phi),
    r * Math.sin(phi) * Math.sin(theta)
  );
}

// Inverse of latLonRadiusToVec3 — given a direction from the globe's
// center (any length; only the direction matters), recovers the lat/lon
// of the point on the sphere's surface in that direction. Used to find
// what the camera is currently looking at (the near-side surface point
// along the camera-to-center line) for the close-zoom high-res handoff.
function vec3ToLatLon(v) {
  const r = v.length();
  const phi = Math.acos(v.y / r);
  const theta = Math.atan2(v.z, -v.x);
  const lat = 90 - phi * (180 / Math.PI);
  let lon = theta * (180 / Math.PI) - 180;
  if (lon < -180) lon += 360;
  if (lon > 180) lon -= 360;
  return { lat, lon };
}

function makeGlyphTexture(kind, colorHex) {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size);
  ctx.strokeStyle = '#ffffff';
  ctx.fillStyle = `#${colorHex.toString(16).padStart(6, '0')}`;
  ctx.lineWidth = 2.5;
  const c = size / 2;

  if (kind === 'station') {
    ctx.save();
    ctx.translate(c, c);
    [0, 90].forEach(deg => {
      ctx.save();
      ctx.rotate((deg * Math.PI) / 180);
      ctx.fillRect(-22, -6, 44, 12);
      ctx.strokeRect(-22, -6, 44, 12);
      ctx.restore();
    });
    ctx.fillRect(-7, -7, 14, 14);
    ctx.strokeRect(-7, -7, 14, 14);
    ctx.restore();
  } else if (kind === 'satellite') {
    ctx.save();
    ctx.translate(c, c);
    ctx.fillRect(-6, -6, 12, 12);
    ctx.strokeRect(-6, -6, 12, 12);
    ctx.fillRect(-22, -5, 13, 10);
    ctx.strokeRect(-22, -5, 13, 10);
    ctx.fillRect(9, -5, 13, 10);
    ctx.strokeRect(9, -5, 13, 10);
    ctx.beginPath();
    ctx.moveTo(0, -6); ctx.lineTo(0, -16);
    ctx.stroke();
    ctx.restore();
  } else if (kind === 'aircraft') {
    ctx.save();
    ctx.translate(c, c);
    ctx.beginPath();
    ctx.moveTo(0, -20);
    ctx.lineTo(4, -4); ctx.lineTo(22, 4); ctx.lineTo(22, 9);
    ctx.lineTo(4, 4); ctx.lineTo(5, 16); ctx.lineTo(12, 21); ctx.lineTo(12, 24);
    ctx.lineTo(0, 21); ctx.lineTo(-12, 24); ctx.lineTo(-12, 21); ctx.lineTo(-5, 16);
    ctx.lineTo(-4, 4); ctx.lineTo(-22, 9); ctx.lineTo(-22, 4); ctx.lineTo(-4, -4);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

/**
 * Props:
 *   stations, otherSats, aircraft — already-filtered/categorized arrays
 *     (stations/otherSats: {name, group, lat, lon, alt_km}; aircraft:
 *     {icao24, lat, lon, baro_altitude_m, ...})
 *   showSatellites, showAircraft — visibility toggles (state lives in Sidebar)
 *   onSelect(kindAndData | null) — called when a point is clicked
 */
export default function OrbitalGlobe({ stations = [], otherSats = [], aircraft = [], showSatellites, showAircraft, onSelect, active = true, onEnterCloseZoom, exitCloseZoomSignal }) {
  const containerRef = useRef(null);
  const stationPointsRef = useRef(null);
  const satellitePointsRef = useRef(null);
  const aircraftPointsRef = useRef(null);
  const stationDataRef = useRef([]);
  const satelliteDataRef = useRef([]);
  const aircraftDataRef = useRef([]);
  const onSelectRef = useRef(onSelect);
  // The render loop below is set up once in the mount effect and closes
  // over this ref rather than the `active` prop directly, for the same
  // reason streetViewModeRef exists in App.jsx: a prop captured at effect-
  // setup time goes stale the moment it changes, since the effect itself
  // doesn't re-run on every prop change.
  const activeRef = useRef(active);
  useEffect(() => { activeRef.current = active; }, [active]);
  // Close-zoom (high-res 2D handoff) machinery — see the animate loop and
  // the exitCloseZoomSignal effect below for how these are used. Refs
  // (not local vars in the mount effect) because a SEPARATE effect
  // (reacting to exitCloseZoomSignal changing) needs to reach the same
  // camera/animation state without re-running the whole mount/WebGL-setup
  // effect.
  const cameraObjRef = useRef(null);
  const cameraAnimRef = useRef(null); // {startTime, startDist, endDist, duration} | null
  const closeZoomFiredRef = useRef(false);
  const onEnterCloseZoomRef = useRef(onEnterCloseZoom);
  useEffect(() => { onEnterCloseZoomRef.current = onEnterCloseZoom; }, [onEnterCloseZoom]);
  onSelectRef.current = onSelect;   // always current inside the click handler without re-binding the listener

  // ── Scene setup (once) ──────────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x05070a);

    const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
    camera.position.set(0, 0, EARTH_RADIUS * 3.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    // Close enough to actually zoom into a region/city like a real globe
    // viewer, not just spin at arm's length. Panning is disabled — for a
    // planet viewer, translating the orbit target away from the globe's
    // center feels broken (you can end up looking at empty space with no
    // way back except zooming out); rotate+zoom only keeps it behaving
    // like Google Earth's "drag to spin, scroll to dive in" interaction
    // instead of a free camera. The Earth texture is a single static
    // 2048px image, not tiled multi-resolution imagery, so close zoom on
    // the 3D globe itself still looks soft — CLOSE_ZOOM_ENTER_DISTANCE
    // below hands off to a real high-res 2D map (GlobeCloseUpMap in
    // App.jsx) before the user reaches that blurriness, rather than
    // just capping how close they can get. minDistance stays as a hard
    // floor slightly past the handoff point, mostly to keep OrbitControls
    // well-behaved (avoids near-plane clipping weirdness) rather than as
    // the primary blur-avoidance mechanism it used to be.
    controls.enablePan = false;
    controls.minDistance = EARTH_RADIUS * 1.08;
    controls.maxDistance = EARTH_RADIUS * 8;
    controls.rotateSpeed = 0.5;
    controls.zoomSpeed = 0.8;

    // Brighter, evenly-lit sphere on purpose — this is the "visible light"
    // difference from the moodier day/night hero globe: high ambient plus
    // a soft fill light from the opposite side of the sun keeps the far
    // hemisphere clearly readable instead of falling into deep shadow.
    scene.add(new THREE.AmbientLight(0xffffff, 0.85));
    const sun = new THREE.DirectionalLight(0xffffff, 1.25);
    sun.position.set(5, 3, 5);
    scene.add(sun);
    const fill = new THREE.DirectionalLight(0xbfd4ff, 0.4);
    fill.position.set(-5, -2, -4);
    scene.add(fill);

    const loader = new THREE.TextureLoader();
    const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
    const material = new THREE.MeshPhongMaterial({ color: 0x223344, shininess: 14, specular: 0x333333 });
    const earth = new THREE.Mesh(geometry, material);
    scene.add(earth);
    loader.load(EARTH_TEXTURE_URL, (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      material.map = tex; material.color.set(0xffffff); material.needsUpdate = true;
    });
    loader.load(EARTH_BUMP_URL, (tex) => { material.bumpMap = tex; material.bumpScale = 0.025; material.needsUpdate = true; });
    loader.load(EARTH_SPECULAR_URL, (tex) => { material.specularMap = tex; material.needsUpdate = true; });

    // Cloud shell — thin, slightly larger sphere, independently and
    // slowly rotated. Purely decorative/visual: it carries no position
    // data, so spinning it doesn't touch the fixed lat/lon->vec3 mapping
    // the satellite/station/aircraft glyphs and the base globe rely on.
    const cloudsGeo = new THREE.SphereGeometry(EARTH_RADIUS * 1.008, 64, 64);
    const cloudsMat = new THREE.MeshLambertMaterial({ transparent: true, opacity: 0.55, depthWrite: false });
    const clouds = new THREE.Mesh(cloudsGeo, cloudsMat);
    scene.add(clouds);
    loader.load(EARTH_CLOUDS_URL, (tex) => { tex.colorSpace = THREE.SRGBColorSpace; cloudsMat.map = tex; cloudsMat.needsUpdate = true; });

    // Atmosphere glow — fresnel rim-light shell, the single biggest thing
    // separating "a sphere with a texture on it" from something that
    // reads as an actual planet. Slightly thicker/brighter shell + a
    // warmer secondary tone at the limb reads closer to a real
    // Rayleigh-scattering halo than a single flat blue rim.
    const atmosphereGeo = new THREE.SphereGeometry(EARTH_RADIUS * 1.055, 64, 64);
    const atmosphereMat = new THREE.ShaderMaterial({
      uniforms: {
        glowColor: { value: new THREE.Color(0x6fb4ff) },
        rimColor: { value: new THREE.Color(0xffe9c4) },
      },
      vertexShader: `
        varying float intensity;
        void main() {
          vec3 vNormal = normalize(normalMatrix * normal);
          vec3 vViewDir = normalize(-(modelViewMatrix * vec4(position, 1.0)).xyz);
          intensity = pow(0.62 - dot(vNormal, vViewDir), 2.2);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 glowColor;
        uniform vec3 rimColor;
        varying float intensity;
        void main() {
          vec3 col = mix(glowColor, rimColor, clamp(intensity * 0.6, 0.0, 1.0));
          gl_FragColor = vec4(col, clamp(intensity, 0.0, 1.0) * 0.65);
        }
      `,
      side: THREE.BackSide,
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
    });
    const atmosphere = new THREE.Mesh(atmosphereGeo, atmosphereMat);
    scene.add(atmosphere);

    const starGeo = new THREE.BufferGeometry();
    const starCount = 1200;
    const starPos = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      const r = 60 + Math.random() * 40;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      starPos[i*3]   = r * Math.sin(phi) * Math.cos(theta);
      starPos[i*3+1] = r * Math.cos(phi);
      starPos[i*3+2] = r * Math.sin(phi) * Math.sin(theta);
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 0.15, sizeAttenuation: true })));

    const makePoints = (kind, size) => {
      const geo = new THREE.BufferGeometry();
      const tex = makeGlyphTexture(kind, COLORS[kind]);
      const mat = new THREE.PointsMaterial({ map: tex, size, sizeAttenuation: true, transparent: true, alphaTest: 0.3, depthWrite: false });
      const pts = new THREE.Points(geo, mat);
      scene.add(pts);
      return pts;
    };
    stationPointsRef.current = makePoints('station', 0.42);
    satellitePointsRef.current = makePoints('satellite', 0.26);
    aircraftPointsRef.current = makePoints('aircraft', 0.22);

    const raycaster = new THREE.Raycaster();
    raycaster.params.Points.threshold = EARTH_RADIUS * 0.06;
    const pointer = new THREE.Vector2();
    const onClick = (ev) => {
      try {
        const rect = renderer.domElement.getBoundingClientRect();
        pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);

        const candidates = [
          { kind: 'station', obj: stationPointsRef.current, data: stationDataRef.current },
          { kind: 'satellite', obj: satellitePointsRef.current, data: satelliteDataRef.current },
          { kind: 'aircraft', obj: aircraftPointsRef.current, data: aircraftDataRef.current },
        ];
        let best = null;
        for (const c of candidates) {
          if (!c.obj || !c.obj.visible) continue;
          const hits = raycaster.intersectObject(c.obj, false);
          if (hits.length > 0 && (!best || hits[0].distance < best.distance)) {
            best = { distance: hits[0].distance, kind: c.kind, item: c.data[hits[0].index] };
          }
        }
        if (best && best.item && onSelectRef.current) onSelectRef.current({ kind: best.kind, ...best.item });
      } catch (e) {
        console.error('OrbitalGlobe click-select error:', e);
      }
    };
    renderer.domElement.addEventListener('click', onClick);

    // Camera fly-in on first mount (eased, not an instant snap) — see
    // the animate loop for how cameraAnimRef drives this, and the
    // exitCloseZoomSignal effect below for the second use of the same
    // mechanism (easing back out after a high-res close-zoom handoff).
    const REST_DISTANCE = EARTH_RADIUS * 3.2;
    const FLY_IN_START_DISTANCE = EARTH_RADIUS * 7;
    const FLY_IN_MS = 900;
    camera.position.set(0, 0, FLY_IN_START_DISTANCE);
    cameraObjRef.current = camera;
    cameraAnimRef.current = { startTime: performance.now(), startDist: FLY_IN_START_DISTANCE, endDist: REST_DISTANCE, duration: FLY_IN_MS };
    const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

    // Close-zoom thresholds for the high-res 2D handoff (see App.jsx's
    // globeCloseUp state + GlobeCloseUpMap): ENTER fires once when the
    // camera gets this close; the "already fired" latch only resets once
    // the camera has pulled back out past REARM (a wider gap than ENTER,
    // not the same value) — otherwise a camera sitting exactly at the
    // boundary would flicker the 2D overlay on/off every frame.
    const CLOSE_ZOOM_ENTER_DISTANCE = EARTH_RADIUS * 1.35;
    const CLOSE_ZOOM_REARM_DISTANCE = EARTH_RADIUS * 1.9;

    let raf;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      // Skip rendering entirely while this tab isn't the active one —
      // the component stays mounted (so its state/camera persist), but
      // there's no reason to keep spending GPU cycles on a canvas nobody
      // can see. controls.update() also skipped: with damping enabled it
      // has its own per-frame decay, and there's nothing to damp toward
      // while no user input is happening on a hidden canvas.
      if (!activeRef.current) return;
      // Deliberately NOT spinning the Earth mesh independently — positions
      // are computed in a fixed Earth-fixed reference frame (satellite
      // positions specifically already account for Earth's true rotation
      // via GMST in the SGP4 propagation), so an unrelated decorative spin
      // desyncs the visible coastlines from every correctly-placed point.
      // Wrapped in try/catch: this loop runs outside React's tree, so an
      // uncaught exception here (e.g. a transient NaN from bad data)
      // would silently freeze the canvas on its last frame every future
      // tick instead of surfacing to the ErrorBoundary — one bad frame
      // should not kill the whole loop.
      try {
        clouds.rotation.y += 0.0004; // decorative only — earth itself never rotates (see note above)
        if (cameraAnimRef.current) {
          const { startTime, startDist, endDist, duration } = cameraAnimRef.current;
          const t = Math.min((performance.now() - startTime) / duration, 1);
          const dist = startDist + (endDist - startDist) * easeOutCubic(t);
          const dir = camera.position.clone().normalize();
          camera.position.copy(dir.multiplyScalar(dist));
          if (t >= 1) cameraAnimRef.current = null;
        }

        // Close-zoom detection: edge-triggered (closeZoomFiredRef), not
        // level-triggered — fires onEnterCloseZoom once per approach, not
        // every frame the camera happens to be close. Only checked while
        // no camera animation is in flight (cameraAnimRef null) — doesn't
        // make sense to trigger a handoff mid fly-in/fly-out.
        if (!cameraAnimRef.current && onEnterCloseZoomRef.current) {
          const dist = camera.position.length();
          if (!closeZoomFiredRef.current && dist < CLOSE_ZOOM_ENTER_DISTANCE) {
            closeZoomFiredRef.current = true;
            const { lat, lon } = vec3ToLatLon(camera.position);
            onEnterCloseZoomRef.current(lat, lon);
          } else if (closeZoomFiredRef.current && dist > CLOSE_ZOOM_REARM_DISTANCE) {
            closeZoomFiredRef.current = false;
          }
        }

        controls.update();
        renderer.render(scene, camera);
      } catch (e) {
        console.error('OrbitalGlobe render error (frame skipped):', e);
      }
    };
    animate();

    const onResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener('resize', onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('click', onClick);
      controls.dispose();
      renderer.dispose();
      geometry.dispose();
      material.dispose();
      material.map?.dispose(); material.bumpMap?.dispose(); material.specularMap?.dispose();
      cloudsGeo.dispose();
      cloudsMat.map?.dispose();
      cloudsMat.dispose();
      atmosphereGeo.dispose();
      atmosphereMat.dispose();
      // Null out the refs, not just dispose their contents — StrictMode
      // double-invokes this effect in dev (mount -> cleanup -> mount);
      // without nulling here, the data-population effects below could
      // write fresh positions onto an already-disposed Points object
      // during the gap before the next mount reassigns live ones.
      [stationPointsRef, satellitePointsRef, aircraftPointsRef].forEach(ref => {
        if (ref.current) { ref.current.geometry.dispose(); ref.current.material.map?.dispose(); ref.current.material.dispose(); }
        ref.current = null;
      });
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement);
    };
  }, []);

  // Eases the camera back out to a comfortable orbital distance whenever
  // the parent bumps exitCloseZoomSignal (i.e. the user zoomed the 2D
  // high-res handoff map back out — see App.jsx's globeCloseUp/
  // GlobeCloseUpMap). Deliberately a SEPARATE effect from the mount one
  // above: this needs to fire on a later prop change, not at setup time,
  // and reuses the same camera/cameraAnimRef refs that effect populated.
  // Skips the very first render (exitCloseZoomSignal starts undefined/0
  // and shouldn't trigger a reset before any close-zoom ever happened).
  const isFirstExitSignalRef = useRef(true);
  useEffect(() => {
    if (isFirstExitSignalRef.current) { isFirstExitSignalRef.current = false; return; }
    const camera = cameraObjRef.current;
    if (!camera) return;
    cameraAnimRef.current = {
      startTime: performance.now(),
      startDist: camera.position.length(),
      endDist: EARTH_RADIUS * 3.2,
      duration: 700,
    };
    // Re-arm immediately (not waiting for the natural REARM_DISTANCE
    // crossing) — the whole point of this reset is that the user just
    // finished a close-zoom cycle and is back at globe scale, so the
    // next approach should be able to trigger a fresh handoff without
    // first needing to pull back further than the reset distance itself.
    closeZoomFiredRef.current = false;
  }, [exitCloseZoomSignal]);

  // ── Update station/satellite point positions whenever props change ─────
  useEffect(() => {
    const stationPts = stationPointsRef.current;
    const satPts = satellitePointsRef.current;
    if (!stationPts || !satPts) return;

    stationPts.visible = showSatellites;
    satPts.visible = showSatellites;
    // Filter out any record with a missing/non-numeric lat, lon, or
    // alt_km BEFORE it reaches the trig in satelliteAltToVec3 — an
    // occasional malformed CelesTrak/propagation record (undefined ->
    // NaN position) was silently poisoning the BufferAttribute and
    // geometry.computeBoundingSphere(), which OrbitControls/raycasting
    // then read every animation frame outside React's render tree, so
    // it never hit the ErrorBoundary and instead froze/blanked the
    // canvas intermittently. Aircraft already had this guard; stations
    // and other satellites didn't.
    const validStations = stations.filter(s => typeof s.lat === 'number' && typeof s.lon === 'number' && typeof s.alt_km === 'number');
    const validSats = otherSats.filter(s => typeof s.lat === 'number' && typeof s.lon === 'number' && typeof s.alt_km === 'number');
    stationDataRef.current = validStations;
    satelliteDataRef.current = validSats;

    const fill = (pts, list) => {
      if (list.length === 0) { pts.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3)); return; }
      const positions = new Float32Array(list.length * 3);
      list.forEach((s, i) => {
        const v = satelliteAltToVec3(s.lat, s.lon, s.alt_km, EARTH_RADIUS);
        positions[i*3] = v.x; positions[i*3+1] = v.y; positions[i*3+2] = v.z;
      });
      pts.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      pts.geometry.computeBoundingSphere();
    };
    fill(stationPts, validStations);
    fill(satPts, validSats);
  }, [stations, otherSats, showSatellites]);

  // ── Update aircraft point positions whenever props change ──────────────
  useEffect(() => {
    const pts = aircraftPointsRef.current;
    if (!pts) return;
    pts.visible = showAircraft;
    const list = aircraft.filter(a => typeof a.lat === 'number' && typeof a.lon === 'number');
    aircraftDataRef.current = list;

    if (list.length === 0) {
      pts.geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array(0), 3));
    } else {
      const positions = new Float32Array(list.length * 3);
      list.forEach((a, i) => {
        const altKm = (a.baro_altitude_m || 0) / 1000;
        const v = aircraftAltToVec3(a.lat, a.lon, altKm, EARTH_RADIUS);
        positions[i*3] = v.x; positions[i*3+1] = v.y; positions[i*3+2] = v.z;
      });
      pts.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      pts.geometry.computeBoundingSphere();
    }
  }, [aircraft, showAircraft]);

  return <div ref={containerRef} style={{ width:'100%', height:'100%' }} />;
}
