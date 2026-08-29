/**
 * OrbitalGlobe.jsx
 * A standalone 3D Earth view — separate from the main 2D Leaflet
 * operational map (that map's whole stack — vessel markers, AOI drawing,
 * intel layers — is Leaflet-specific, so this is a dedicated view users
 * switch to, not a replacement for the main map).
 *
 * Both satellites AND aircraft live ONLY here, not on the 2D map — a flat
 * dot on a 2D projection is a worse representation of heading/altitude/
 * orbit than an actual 3D position, and consolidating both into one view
 * (each independently toggleable) keeps the 2D map focused on maritime/
 * intel work instead of getting cluttered with two more marker types.
 *
 * Renders a textured sphere (free NASA Blue Marble texture served from
 * three.js's own examples CDN — no key, no billing account, unlike
 * Cesium ion / Google Photorealistic 3D Tiles) with live positions from
 * useSatelliteTracker / useAircraftTracker plotted on/near its surface,
 * rotatable via mouse/touch drag (OrbitControls).
 *
 * This is meant to be a real data view, not just an animation: clicking a
 * point (or picking from the searchable list panel) surfaces that
 * object's real data in a detail card, and the numbers keep updating
 * live rather than freezing at the moment of selection. Space stations,
 * other satellites, and aircraft each get a distinct hand-drawn glyph
 * (canvas-texture sprites — no external icon assets needed) instead of
 * a plain dot, so the three categories are visually distinguishable at
 * a glance.
 */

import { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { useSatelliteTracker } from '../hooks/useSatelliteTracker';
import { useAircraftTracker } from '../hooks/useAircraftTracker';

const EARTH_RADIUS = 5;
const EARTH_TEXTURE_URL = 'https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg';
const EARTH_BUMP_URL = 'https://threejs.org/examples/textures/planets/earth_normal_2048.jpg';

const COLORS = {
  station:   '#ff6b6b',
  satellite: '#9b8ce8',
  aircraft:  '#e8c15c',
};

function latLonAltToVec3(lat, lon, altKm, earthRadius) {
  const displayAlt = earthRadius * (0.06 + Math.min(altKm, 40000) / 40000 * 0.7);
  const r = earthRadius + displayAlt;
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
  ctx.fillStyle = colorHex;
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

export default function OrbitalGlobe({ apiUrl }) {
  const containerRef = useRef(null);
  const stationPointsRef = useRef(null);
  const satellitePointsRef = useRef(null);
  const aircraftPointsRef = useRef(null);
  const stationDataRef = useRef([]);
  const satelliteDataRef = useRef([]);
  const aircraftDataRef = useRef([]);
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState('');
  const [showSatellites, setShowSatellites] = useState(true);
  const [showAircraft, setShowAircraft] = useState(false);

  const { satellites, loaded: satLoaded, debug: satDebug } = useSatelliteTracker(apiUrl, showSatellites);
  const { aircraft, stats: aircraftStats } = useAircraftTracker(apiUrl, showAircraft);

  const stations = useMemo(() => satellites.filter(s => s.group === 'stations'), [satellites]);
  const otherSats = useMemo(() => satellites.filter(s => s.group !== 'stations'), [satellites]);

  const filteredList = useMemo(() => {
    const items = [];
    if (showSatellites) {
      stations.forEach(s => items.push({ kind: 'station', key: `sat:${s.name}`, label: s.name, sub: `${Math.round(s.alt_km).toLocaleString()} km`, data: s }));
      otherSats.forEach(s => items.push({ kind: 'satellite', key: `sat:${s.name}`, label: s.name, sub: `${Math.round(s.alt_km).toLocaleString()} km`, data: s }));
    }
    if (showAircraft) {
      aircraft.forEach(a => items.push({
        kind: 'aircraft', key: `ac:${a.icao24}`,
        label: a.callsign || a.icao24,
        sub: a.on_ground ? 'on ground' : (a.baro_altitude_m != null ? `${Math.round(a.baro_altitude_m).toLocaleString()} m` : '—'),
        data: a,
      }));
    }
    items.sort((a, b) => a.label.localeCompare(b.label));
    if (!search.trim()) return items;
    const q = search.trim().toLowerCase();
    return items.filter(it => it.label.toLowerCase().includes(q));
  }, [stations, otherSats, aircraft, showSatellites, showAircraft, search]);

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
    controls.minDistance = EARTH_RADIUS * 1.3;
    controls.maxDistance = EARTH_RADIUS * 8;
    controls.rotateSpeed = 0.5;

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
      if (best && best.item) setSelected({ kind: best.kind, ...best.item });
    };
    renderer.domElement.addEventListener('click', onClick);

    let raf;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      // Deliberately NOT spinning the Earth mesh here. Aircraft/satellite
      // positions are computed from real lat/lon in a fixed Earth-fixed
      // reference frame (satellite positions specifically already account
      // for Earth's true rotation via GMST inside useSatelliteTracker's
      // SGP4 propagation) — an independent decorative spin on the mesh
      // desyncs the visible coastlines from the correctly-computed points,
      // making every point look like it's "orbiting" at the fake spin
      // rate regardless of its real motion, and makes points land on the
      // wrong-looking geography as the texture slides out from under them.
      // Users can still rotate the view manually via OrbitControls drag.
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
      // Null out the refs, not just dispose their contents — StrictMode
      // (enabled in main.jsx) double-invokes this effect in dev
      // (mount -> cleanup -> mount), and without nulling here, the data-
      // population effects below could write new positions onto an
      // already-disposed Points object during the brief window between
      // this cleanup and the next mount reassigning fresh ones. Their own
      // `if (!stationPts || !satPts) return;` guards make that a safe
      // no-op once the refs are actually null, instead of a silent write
      // to disposed WebGL resources that may render nothing.
      [stationPointsRef, satellitePointsRef, aircraftPointsRef].forEach(ref => {
        if (ref.current) { ref.current.geometry.dispose(); ref.current.material.map?.dispose(); ref.current.material.dispose(); }
        ref.current = null;
      });
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement);
    };
  }, []);

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
        const v = latLonAltToVec3(s.lat, s.lon, s.alt_km, EARTH_RADIUS);
        positions[i*3] = v.x; positions[i*3+1] = v.y; positions[i*3+2] = v.z;
      });
      pts.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      pts.geometry.computeBoundingSphere();
    };
    fill(stationPts, stations);
    fill(satPts, otherSats);

    setSelected(prev => {
      if (!prev || (prev.kind !== 'station' && prev.kind !== 'satellite')) return prev;
      const fresh = satellites.find(s => s.name === prev.name);
      return fresh ? { kind: prev.kind, ...fresh } : prev;
    });
  }, [stations, otherSats, satellites, showSatellites]);

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
        const v = latLonAltToVec3(a.lat, a.lon, altKm, EARTH_RADIUS);
        positions[i*3] = v.x; positions[i*3+1] = v.y; positions[i*3+2] = v.z;
      });
      pts.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      pts.geometry.computeBoundingSphere();
    }

    setSelected(prev => {
      if (!prev || prev.kind !== 'aircraft') return prev;
      const fresh = list.find(a => a.icao24 === prev.icao24);
      return fresh ? { kind: 'aircraft', ...fresh } : prev;
    });
  }, [aircraft, showAircraft]);

  const selectItem = (item) => setSelected({ kind: item.kind, ...item.data });

  const kindLabel = { station: 'Space Station', satellite: 'Tracked Satellite', aircraft: 'Aircraft' };

  return (
    <div style={{ width:'100%', height:'100%', position:'relative', display:'flex' }}>
      <div ref={containerRef} style={{ flex:1, height:'100%' }} />

      <div style={{
        position:'absolute', top:14, left:14, padding:'10px 12px',
        background:'rgba(10,12,15,0.8)', border:'1px solid #2a2f36', borderRadius:4,
        fontFamily:'monospace', fontSize:12, color:'#c9c9c9', letterSpacing:0.5, maxWidth:270,
      }}>
        <div style={{ letterSpacing:1.5, textTransform:'uppercase', opacity:0.7, marginBottom:8 }}>Orbital View</div>
        <div style={{ display:'flex', gap:8, marginBottom:8 }}>
          <button onClick={() => setShowSatellites(v => !v)}
            style={{ flex:1, padding:'6px 8px', fontFamily:'monospace', fontSize:11, letterSpacing:0.5, cursor:'pointer', borderRadius:3,
              background: showSatellites ? 'rgba(155,140,232,0.15)' : 'transparent',
              border: `1px solid ${showSatellites ? '#9b8ce8' : '#3a3f46'}`,
              color: showSatellites ? '#c3b8f5' : '#7a8088' }}>
            Satellites {showSatellites ? `(${satellites.length})` : ''}
          </button>
          <button onClick={() => setShowAircraft(v => !v)}
            style={{ flex:1, padding:'6px 8px', fontFamily:'monospace', fontSize:11, letterSpacing:0.5, cursor:'pointer', borderRadius:3,
              background: showAircraft ? 'rgba(232,193,92,0.15)' : 'transparent',
              border: `1px solid ${showAircraft ? '#e8c15c' : '#3a3f46'}`,
              color: showAircraft ? '#f0d488' : '#7a8088' }}>
            Aircraft {showAircraft ? `(${aircraftStats.active_aircraft || aircraft.length})` : ''}
          </button>
        </div>
        <div style={{ fontSize:11, opacity:0.6, lineHeight:1.5 }}>
          {showSatellites && !satLoaded && 'Loading orbital elements… '}
          Drag to rotate · scroll to zoom · click a point for details
        </div>
        {showSatellites && (satDebug.lastError || (satLoaded && satDebug.propagatedCount === 0)) && (
          <div style={{ fontSize:10, color:'#ff9d9d', marginTop:6, lineHeight:1.4 }}>
            Satellite debug: fetched {satDebug.fetchedCount}, parsed {satDebug.parsedCount}, showing {satDebug.propagatedCount}
            {satDebug.lastError ? ` — ${satDebug.lastError}` : ''}
          </div>
        )}
      </div>

      {selected && (
        <div style={{
          position:'absolute', bottom:14, left:14, padding:'12px 14px', minWidth:220, maxWidth:280,
          background:'rgba(10,12,15,0.9)', border:'1px solid #3a3140', borderRadius:6,
          fontFamily:'monospace', fontSize:13, color:'#e8e8e8',
        }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:10 }}>
            <div style={{ fontSize:14, fontWeight:700, color: COLORS[selected.kind] }}>
              {selected.kind === 'aircraft' ? (selected.callsign || selected.icao24) : selected.name}
            </div>
            <button onClick={() => setSelected(null)}
              style={{ background:'none', border:'none', color:'#7a8088', cursor:'pointer', fontSize:14, lineHeight:1, padding:0 }}>
              ✕
            </button>
          </div>
          <div style={{ marginTop:6, opacity:0.7, textTransform:'uppercase', fontSize:11, letterSpacing:1 }}>
            {kindLabel[selected.kind]}
          </div>
          {selected.kind !== 'aircraft' ? (
            <div style={{ marginTop:8, display:'grid', gridTemplateColumns:'auto auto', gap:'2px 12px', fontSize:12 }}>
              <span style={{ opacity:0.6 }}>Latitude</span><span>{selected.lat.toFixed(2)}°</span>
              <span style={{ opacity:0.6 }}>Longitude</span><span>{selected.lon.toFixed(2)}°</span>
              <span style={{ opacity:0.6 }}>Altitude</span><span>{Math.round(selected.alt_km).toLocaleString()} km</span>
            </div>
          ) : (
            <div style={{ marginTop:8, display:'grid', gridTemplateColumns:'auto auto', gap:'2px 12px', fontSize:12 }}>
              {selected.registration && <><span style={{ opacity:0.6 }}>Registration</span><span>{selected.registration}</span></>}
              {selected.type_desc && <><span style={{ opacity:0.6 }}>Type</span><span>{selected.type_desc}</span></>}
              <span style={{ opacity:0.6 }}>Status</span><span>{selected.on_ground ? 'On ground' : 'Airborne'}</span>
              {!selected.on_ground && selected.baro_altitude_m != null && <><span style={{ opacity:0.6 }}>Altitude</span><span>{Math.round(selected.baro_altitude_m).toLocaleString()} m</span></>}
              {selected.velocity_ms != null && <><span style={{ opacity:0.6 }}>Speed</span><span>{Math.round(selected.velocity_ms * 3.6)} km/h</span></>}
              {selected.squawk && <><span style={{ opacity:0.6 }}>Squawk</span><span>{selected.squawk}</span></>}
              {selected.military && <><span style={{ opacity:0.6 }}>Flag</span><span style={{ color:'#ff9d9d' }}>Military</span></>}
            </div>
          )}
        </div>
      )}

      <div style={{
        width:230, flexShrink:0, height:'100%', background:'rgba(10,12,15,0.92)',
        borderLeft:'1px solid #2a2f36', display:'flex', flexDirection:'column',
      }}>
        <div style={{ padding:'10px 12px', borderBottom:'1px solid #2a2f36' }}>
          <input
            type="text" placeholder="Search…" value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              width:'100%', padding:'6px 8px', background:'#15181d', border:'1px solid #2a2f36',
              borderRadius:3, color:'#e8e8e8', fontFamily:'monospace', fontSize:12, outline:'none', boxSizing:'border-box',
            }}
          />
        </div>
        <div style={{ flex:1, overflowY:'auto' }}>
          {filteredList.map(item => (
            <button key={item.key} onClick={() => selectItem(item)}
              style={{
                display:'block', width:'100%', textAlign:'left', padding:'8px 12px',
                background: selected && ((selected.kind === 'aircraft' && selected.icao24 === item.data.icao24) || (selected.kind !== 'aircraft' && selected.name === item.data.name)) ? 'rgba(155,140,232,0.12)' : 'transparent',
                border:'none', borderBottom:'1px solid #1c1f24', cursor:'pointer',
                color: COLORS[item.kind], fontFamily:'monospace', fontSize:12,
              }}>
              <div style={{ overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{item.label}</div>
              <div style={{ fontSize:10, opacity:0.55, marginTop:2 }}>{item.sub}</div>
            </button>
          ))}
          {filteredList.length === 0 && (
            <div style={{ padding:'16px 12px', fontSize:12, color:'#7a8088', fontFamily:'monospace' }}>
              {(showSatellites || showAircraft) ? 'No matches' : 'Toggle a layer above to see data'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
