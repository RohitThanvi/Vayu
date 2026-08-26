/**
 * OrbitalGlobe.jsx
 * A standalone 3D Earth view for satellite tracking — separate from the
 * main 2D Leaflet operational map (that map's whole stack — vessel/aircraft
 * markers, AOI drawing, intel layers — is Leaflet-specific, so this is a
 * dedicated view users switch to, not a replacement for the main map).
 *
 * Renders a textured sphere (free NASA Blue Marble texture served from
 * three.js's own examples CDN — no key, no billing account, unlike
 * Cesium ion / Google Photorealistic 3D Tiles) with live satellite
 * positions from useSatelliteTracker plotted on its surface, rotatable
 * via mouse/touch drag (OrbitControls).
 */

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { useSatelliteTracker } from '../hooks/useSatelliteTracker';

const EARTH_RADIUS = 5;
// Same free, keyless, no-billing-account texture set three.js's own
// official examples use — appropriate to lean on for a non-commercial
// research tool, unlike Cesium ion or Google Photorealistic 3D Tiles
// which both need a billing-enabled account even on their free tier.
const EARTH_TEXTURE_URL = 'https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg';
const EARTH_BUMP_URL = 'https://threejs.org/examples/textures/planets/earth_normal_2048.jpg';

const SATELLITE_COLORS = {
  stations: 0xff6b6b,   // space stations stand out — ISS etc
  visual:   0x9b8ce8,   // everything else, matches the violet marker color used on the 2D map
};

function latLonAltToVec3(lat, lon, altKm, earthRadius) {
  // altKm is real orbital altitude (hundreds-thousands of km); scaled down
  // visually so satellites sit at a readable distance from the globe
  // surface rather than needing an enormous camera distance to see both
  // Earth and orbit — this is a display convenience, not a physically
  // accurate scale model.
  const displayAlt = earthRadius * (0.15 + Math.min(altKm, 40000) / 40000 * 0.6);
  const r = earthRadius + displayAlt;
  const phi = (90 - lat) * (Math.PI / 180);
  const theta = (lon + 180) * (Math.PI / 180);
  return new THREE.Vector3(
    -r * Math.sin(phi) * Math.cos(theta),
    r * Math.cos(phi),
    r * Math.sin(phi) * Math.sin(theta)
  );
}

export default function OrbitalGlobe({ apiUrl }) {
  const containerRef = useRef(null);
  const sceneRef = useRef(null);
  const satPointsRef = useRef(null);
  const [satCount, setSatCount] = useState(0);

  const { satellites, loaded } = useSatelliteTracker(apiUrl, true);

  // ── Scene setup (once) ──────────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x05070a);

    const camera = new THREE.PerspectiveCamera(
      45, container.clientWidth / container.clientHeight, 0.1, 1000
    );
    camera.position.set(0, 0, EARTH_RADIUS * 3.2);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(container.clientWidth, container.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = EARTH_RADIUS * 1.3;
    controls.maxDistance = EARTH_RADIUS * 8;
    controls.rotateSpeed = 0.5;

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const sun = new THREE.DirectionalLight(0xffffff, 1.1);
    sun.position.set(5, 3, 5);
    scene.add(sun);

    // Earth sphere — texture load is async; sphere renders untextured
    // (dark gray) for the brief moment before it resolves rather than
    // blocking the whole view on it.
    const loader = new THREE.TextureLoader();
    const geometry = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
    const material = new THREE.MeshPhongMaterial({ color: 0x223344, shininess: 5 });
    const earth = new THREE.Mesh(geometry, material);
    scene.add(earth);
    loader.load(EARTH_TEXTURE_URL, (tex) => { material.map = tex; material.color.set(0xffffff); material.needsUpdate = true; });
    loader.load(EARTH_BUMP_URL, (tex) => { material.bumpMap = tex; material.bumpScale = 0.02; material.needsUpdate = true; });

    // Thin starfield backdrop so rotation reads clearly against something
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

    // Satellite points — geometry/positions rewritten each time the
    // propagation hook produces new data (see the effect below); created
    // empty here so the render loop always has something to draw.
    const satGeo = new THREE.BufferGeometry();
    const satMat = new THREE.PointsMaterial({ color: 0x9b8ce8, size: 0.09, sizeAttenuation: true });
    const satPoints = new THREE.Points(satGeo, satMat);
    scene.add(satPoints);
    satPointsRef.current = satPoints;

    sceneRef.current = { scene, camera, renderer, controls, earth };

    let raf;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      earth.rotation.y += 0.0006;   // slow idle rotation — real satellite motion carries the "live" feel, this is just ambience
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
      controls.dispose();
      renderer.dispose();
      geometry.dispose();
      material.dispose();
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement);
    };
  }, []);

  // ── Update satellite point positions whenever propagation ticks ────────
  useEffect(() => {
    const points = satPointsRef.current;
    if (!points || satellites.length === 0) return;

    const positions = new Float32Array(satellites.length * 3);
    const colors = new Float32Array(satellites.length * 3);
    const c = new THREE.Color();
    satellites.forEach((sat, i) => {
      const v = latLonAltToVec3(sat.lat, sat.lon, sat.alt_km, EARTH_RADIUS);
      positions[i*3] = v.x; positions[i*3+1] = v.y; positions[i*3+2] = v.z;
      c.set(sat.group === 'stations' ? SATELLITE_COLORS.stations : SATELLITE_COLORS.visual);
      colors[i*3] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
    });
    points.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    points.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    points.geometry.computeBoundingSphere();
    points.material.vertexColors = true;
    points.material.needsUpdate = true;
    setSatCount(satellites.length);
  }, [satellites]);

  return (
    <div style={{ width:'100%', height:'100%', position:'relative' }}>
      <div ref={containerRef} style={{ width:'100%', height:'100%' }} />
      <div style={{
        position:'absolute', top:14, left:14, padding:'8px 12px',
        background:'rgba(10,12,15,0.75)', border:'1px solid #2a2f36', borderRadius:4,
        fontFamily:'monospace', fontSize:12, color:'#c9c9c9', letterSpacing:0.5, pointerEvents:'none',
      }}>
        <div style={{ letterSpacing:1.5, textTransform:'uppercase', opacity:0.7, marginBottom:3 }}>Orbital View</div>
        <div>{loaded ? `${satCount} objects tracked` : 'Loading orbital elements…'}</div>
        <div style={{ opacity:0.6, marginTop:2 }}>Drag to rotate · scroll to zoom</div>
      </div>
    </div>
  );
}
