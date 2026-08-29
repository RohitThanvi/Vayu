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
const EARTH_TEXTURE_URL = 'https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg';
const EARTH_BUMP_URL = 'https://threejs.org/examples/textures/planets/earth_normal_2048.jpg';

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
export default function OrbitalGlobe({ stations = [], otherSats = [], aircraft = [], showSatellites, showAircraft, onSelect }) {
  const containerRef = useRef(null);
  const stationPointsRef = useRef(null);
  const satellitePointsRef = useRef(null);
  const aircraftPointsRef = useRef(null);
  const stationDataRef = useRef([]);
  const satelliteDataRef = useRef([]);
  const aircraftDataRef = useRef([]);
  const onSelectRef = useRef(onSelect);
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
    // instead of a free camera. Honest limitation: the Earth texture is
    // a single static 2048px image, not tiled multi-resolution imagery
    // like a real map service, so extreme close zoom will look soft
    // rather than revealing more real detail — minDistance is set to
    // stop shy of where that becomes obviously blurry.
    controls.enablePan = false;
    controls.minDistance = EARTH_RADIUS * 1.08;
    controls.maxDistance = EARTH_RADIUS * 8;
    controls.rotateSpeed = 0.5;
    controls.zoomSpeed = 0.8;

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 1.1);
    sun.position.set(5, 3, 5);
    scene.add(sun);

    const loader = new THREE.TextureLoader();
    const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
    const material = new THREE.MeshPhongMaterial({ color: 0x223344, shininess: 5 });
    const earth = new THREE.Mesh(geometry, material);
    scene.add(earth);
    loader.load(EARTH_TEXTURE_URL, (tex) => { material.map = tex; material.color.set(0xffffff); material.needsUpdate = true; });
    loader.load(EARTH_BUMP_URL, (tex) => { material.bumpMap = tex; material.bumpScale = 0.02; material.needsUpdate = true; });

    // Atmosphere glow — fresnel rim-light shell, the single biggest thing
    // separating "a sphere with a texture on it" from something that
    // reads as an actual planet.
    const atmosphereGeo = new THREE.SphereGeometry(EARTH_RADIUS * 1.04, 64, 64);
    const atmosphereMat = new THREE.ShaderMaterial({
      uniforms: { glowColor: { value: new THREE.Color(0x5fa8ff) } },
      vertexShader: `
        varying float intensity;
        void main() {
          vec3 vNormal = normalize(normalMatrix * normal);
          vec3 vViewDir = normalize(-(modelViewMatrix * vec4(position, 1.0)).xyz);
          intensity = pow(0.65 - dot(vNormal, vViewDir), 2.5);
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 glowColor;
        varying float intensity;
        void main() {
          gl_FragColor = vec4(glowColor, clamp(intensity, 0.0, 1.0) * 0.55);
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
    };
    renderer.domElement.addEventListener('click', onClick);

    let raf;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      // Deliberately NOT spinning the Earth mesh independently — positions
      // are computed in a fixed Earth-fixed reference frame (satellite
      // positions specifically already account for Earth's true rotation
      // via GMST in the SGP4 propagation), so an unrelated decorative spin
      // desyncs the visible coastlines from every correctly-placed point.
      controls.update();
      renderer.render(scene, camera);
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

  // ── Update station/satellite point positions whenever props change ─────
  useEffect(() => {
    const stationPts = stationPointsRef.current;
    const satPts = satellitePointsRef.current;
    if (!stationPts || !satPts) return;

    stationPts.visible = showSatellites;
    satPts.visible = showSatellites;
    stationDataRef.current = stations;
    satelliteDataRef.current = otherSats;

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
    fill(stationPts, stations);
    fill(satPts, otherSats);
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
