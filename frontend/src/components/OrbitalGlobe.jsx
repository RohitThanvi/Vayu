/**
 * OrbitalGlobe.jsx
 * A standalone 3D Earth view for satellite tracking — separate from the
 * main 2D Leaflet operational map (that map's whole stack — vessel/aircraft
 * markers, AOI drawing, intel layers — is Leaflet-specific, so this is a
 * dedicated view users switch to, not a replacement for the main map).
 * Satellites live ONLY here, not on the 2D map — a flat dot for an orbiting
 * object is a worse representation than an actual 3D position.
 *
 * Renders a textured sphere (free NASA Blue Marble texture served from
 * three.js's own examples CDN — no key, no billing account, unlike
 * Cesium ion / Google Photorealistic 3D Tiles) with live satellite
 * positions from useSatelliteTracker plotted on its surface, rotatable
 * via mouse/touch drag (OrbitControls).
 *
 * This is meant to be a real data view, not just an animation: clicking a
 * point (or picking from the searchable list panel) surfaces that
 * satellite's real orbital data — name, group, live lat/lon, altitude —
 * in a detail card, and the numbers keep updating live rather than
 * freezing at the moment of selection.
 */

import { useEffect, useRef, useState, useMemo } from 'react';
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
  const satellitesRef = useRef([]);   // mirrors `satellites` state for the click handler (avoids stale closure)
  const [satCount, setSatCount] = useState(0);
  const [selected, setSelected] = useState(null);   // clicked/chosen satellite — { name, group, lat, lon, alt_km }
  const [search, setSearch] = useState('');

  const { satellites, loaded } = useSatelliteTracker(apiUrl, true);

  const filteredList = useMemo(() => {
    const list = [...satellites].sort((a, b) => a.name.localeCompare(b.name));
    if (!search.trim()) return list;
    const q = search.trim().toLowerCase();
    return list.filter(s => s.name.toLowerCase().includes(q));
  }, [satellites, search]);

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

    // ── Click-to-inspect: raycast against the satellite point cloud so
    // clicking a dot in the 3D view surfaces the same real orbital data
    // (name, group, lat/lon, altitude) as picking it from the list panel —
    // this is the "gives real details, not just a fancy animation" bit.
    const raycaster = new THREE.Raycaster();
    raycaster.params.Points.threshold = EARTH_RADIUS * 0.05;   // points are tiny; widen the hit area so clicking is forgiving
    const pointer = new THREE.Vector2();
    const onClick = (ev) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(satPointsRef.current, false);
      if (hits.length > 0) {
        const idx = hits[0].index;
        const sat = satellitesRef.current[idx];
        if (sat) setSelected(sat);
      }
    };
    renderer.domElement.addEventListener('click', onClick);

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
      renderer.domElement.removeEventListener('click', onClick);
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
    satellitesRef.current = satellites;   // keep the click handler's lookup current
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

    // Keep the selected detail card's numbers live (lat/lon/alt change
    // continuously) rather than freezing at the moment it was clicked.
    setSelected(prev => {
      if (!prev) return prev;
      const fresh = satellites.find(s => s.name === prev.name);
      return fresh || prev;
    });
  }, [satellites]);

  const selectFromList = (sat) => setSelected(sat);

  return (
    <div style={{ width:'100%', height:'100%', position:'relative', display:'flex' }}>
      <div ref={containerRef} style={{ flex:1, height:'100%' }} />

      <div style={{
        position:'absolute', top:14, left:14, padding:'8px 12px',
        background:'rgba(10,12,15,0.75)', border:'1px solid #2a2f36', borderRadius:4,
        fontFamily:'monospace', fontSize:12, color:'#c9c9c9', letterSpacing:0.5, pointerEvents:'none', maxWidth:260,
      }}>
        <div style={{ letterSpacing:1.5, textTransform:'uppercase', opacity:0.7, marginBottom:3 }}>Orbital View</div>
        <div>{loaded ? `${satCount} objects tracked` : 'Loading orbital elements…'}</div>
        <div style={{ opacity:0.6, marginTop:2 }}>Drag to rotate · scroll to zoom · click a point for details</div>
      </div>

      {selected && (
        <div style={{
          position:'absolute', bottom:14, left:14, padding:'12px 14px', minWidth:220,
          background:'rgba(10,12,15,0.88)', border:'1px solid #3a3140', borderRadius:6,
          fontFamily:'monospace', fontSize:13, color:'#e8e8e8',
        }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:10 }}>
            <div style={{ fontSize:14, fontWeight:700, color: selected.group === 'stations' ? '#ff6b6b' : '#c3b8f5' }}>
              {selected.name}
            </div>
            <button onClick={() => setSelected(null)}
              style={{ background:'none', border:'none', color:'#7a8088', cursor:'pointer', fontSize:14, lineHeight:1, padding:0 }}>
              ✕
            </button>
          </div>
          <div style={{ marginTop:6, opacity:0.7, textTransform:'uppercase', fontSize:11, letterSpacing:1 }}>
            {selected.group === 'stations' ? 'Space Station' : 'Tracked Satellite'}
          </div>
          <div style={{ marginTop:8, display:'grid', gridTemplateColumns:'auto auto', gap:'2px 12px', fontSize:12 }}>
            <span style={{ opacity:0.6 }}>Latitude</span><span>{selected.lat.toFixed(2)}°</span>
            <span style={{ opacity:0.6 }}>Longitude</span><span>{selected.lon.toFixed(2)}°</span>
            <span style={{ opacity:0.6 }}>Altitude</span><span>{Math.round(selected.alt_km).toLocaleString()} km</span>
          </div>
        </div>
      )}

      {/* Satellite list — the real "meaningful details" panel: every tracked
          object, searchable, click one to select it (same effect as
          clicking its point in the 3D view). */}
      <div style={{
        width:220, flexShrink:0, height:'100%', background:'rgba(10,12,15,0.92)',
        borderLeft:'1px solid #2a2f36', display:'flex', flexDirection:'column',
      }}>
        <div style={{ padding:'10px 12px', borderBottom:'1px solid #2a2f36' }}>
          <input
            type="text" placeholder="Search satellites…" value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width:'100%', padding:'6px 8px', background:'#15181d', border:'1px solid #2a2f36',
              borderRadius:3, color:'#e8e8e8', fontFamily:'monospace', fontSize:12, outline:'none', boxSizing:'border-box',
            }}
          />
        </div>
        <div style={{ flex:1, overflowY:'auto' }}>
          {filteredList.map(sat => (
            <button key={sat.name} onClick={() => selectFromList(sat)}
              style={{
                display:'block', width:'100%', textAlign:'left', padding:'8px 12px',
                background: selected?.name === sat.name ? 'rgba(155,140,232,0.12)' : 'transparent',
                border:'none', borderBottom:'1px solid #1c1f24', cursor:'pointer',
                color: sat.group === 'stations' ? '#ff9d9d' : '#d8d0f5', fontFamily:'monospace', fontSize:12,
              }}>
              <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{sat.name}</div>
              <div style={{ fontSize:10, opacity:0.55, marginTop:2 }}>{Math.round(sat.alt_km).toLocaleString()} km</div>
            </button>
          ))}
          {filteredList.length === 0 && (
            <div style={{ padding:'16px 12px', fontSize:12, color:'#7a8088', fontFamily:'monospace' }}>
              {loaded ? 'No matches' : 'Loading…'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
