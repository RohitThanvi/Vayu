/**
 * LandingHero3D.jsx
 * Interactive drag-to-rotate/zoom 3D scene: real NASA Earth imagery,
 * starfield, and a satellite built from primitives (bus, solar wings,
 * parabolic dish, thrusters) — the same proven scene design already
 * used on the 404 page, adapted into a real React component here.
 *
 * Lazy-loaded via React.lazy from LandingPage.jsx (not imported
 * directly) so Three.js — a large dependency — only downloads for
 * people actually landing on this page, and doesn't block the initial
 * page shell (navbar, headline, CTA) from painting immediately while
 * it loads in the background.
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export default function LandingHero3D() {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, container.clientWidth / container.clientHeight, 0.1, 4000);
    camera.position.set(0, 2.4, 12);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.minDistance = 6;
    controls.maxDistance = 22;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.4;
    controls.enablePan = false;

    // Starfield
    function makeStarfield(count, radius) {
      const positions = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        const r = radius * (0.6 + Math.random() * 0.4);
        const theta = Math.random() * Math.PI * 2;
        const phi = Math.acos(2 * Math.random() - 1);
        positions[i*3]   = r * Math.sin(phi) * Math.cos(theta);
        positions[i*3+1] = r * Math.sin(phi) * Math.sin(theta);
        positions[i*3+2] = r * Math.cos(phi);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const mat = new THREE.PointsMaterial({ color: 0xffffff, size: 1.1, sizeAttenuation: true, transparent: true, opacity: 0.75 });
      return new THREE.Points(geo, mat);
    }
    scene.add(makeStarfield(1600, 700));

    // Earth — real NASA imagery (same free, keyless texture already used
    // elsewhere in this app's Orbital tab and 404 page)
    const EARTH_RADIUS = 4.2;
    const loader = new THREE.TextureLoader();
    const earthGeo = new THREE.SphereGeometry(EARTH_RADIUS, 64, 64);
    const earthMat = new THREE.MeshPhongMaterial({ shininess: 6 });
    loader.load('https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg', (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      earthMat.map = tex;
      earthMat.needsUpdate = true;
    });
    const earth = new THREE.Mesh(earthGeo, earthMat);
    earth.position.set(1.5, -1, 0);
    scene.add(earth);

    const atmoGeo = new THREE.SphereGeometry(EARTH_RADIUS * 1.02, 64, 64);
    const atmoMat = new THREE.MeshBasicMaterial({ color: 0x6fa8dc, transparent: true, opacity: 0.15, side: THREE.BackSide });
    const atmo = new THREE.Mesh(atmoGeo, atmoMat);
    atmo.position.copy(earth.position);
    scene.add(atmo);

    scene.add(new THREE.AmbientLight(0x223344, 1.1));
    const sun = new THREE.DirectionalLight(0xfff2d0, 1.7);
    sun.position.set(-10, 8, 10);
    scene.add(sun);
    const rim = new THREE.DirectionalLight(0xc9a86a, 0.5);
    rim.position.set(8, -4, -6);
    scene.add(rim);

    // Satellite — same design as the 404 page: gold-foil MLI bus, dual
    // solar wings, parabolic dish + feed horn, thrusters, star tracker
    const satellite = new THREE.Group();
    const goldFoil = new THREE.MeshStandardMaterial({ color: 0xd4a13a, metalness: 0.75, roughness: 0.35, emissive: 0x2a1a05, emissiveIntensity: 0.15 });
    const busMetal  = new THREE.MeshStandardMaterial({ color: 0xb9bec4, metalness: 0.6, roughness: 0.4 });
    const panelBlue = new THREE.MeshStandardMaterial({ color: 0x14213b, metalness: 0.3, roughness: 0.55 });
    const whiteAnt  = new THREE.MeshStandardMaterial({ color: 0xf2f2f2, metalness: 0.2, roughness: 0.4 });
    const darkAnt   = new THREE.MeshStandardMaterial({ color: 0x2a2a2e, metalness: 0.5, roughness: 0.3 });

    const bus = new THREE.Mesh(new THREE.BoxGeometry(1.3, 1.5, 1.3), goldFoil);
    satellite.add(bus);

    const dishPoints = [];
    for (let i = 0; i <= 20; i++) { const t = i / 20; const r = t * 0.85; dishPoints.push(new THREE.Vector2(r, r*r*0.28)); }
    const dish = new THREE.Mesh(new THREE.LatheGeometry(dishPoints, 32), whiteAnt);
    dish.rotation.x = Math.PI;
    dish.position.set(0, 0.2, 1.15);
    satellite.add(dish);
    const feed = new THREE.Mesh(new THREE.ConeGeometry(0.05, 0.35, 10), darkAnt);
    feed.rotation.x = Math.PI / 2;
    feed.position.set(0, 0.2, 0.7);
    satellite.add(feed);

    const nozzle = new THREE.Mesh(new THREE.ConeGeometry(0.16, 0.4, 14, 1, true), darkAnt);
    nozzle.rotation.x = Math.PI / 2;
    nozzle.position.set(0, 0, -0.9);
    satellite.add(nozzle);

    function buildWing(dir) {
      const wing = new THREE.Group();
      const boomLen = 0.6;
      const boom = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, boomLen, 8), busMetal);
      boom.rotation.z = Math.PI / 2;
      boom.position.set(dir * (0.65 + boomLen/2), 0, 0);
      wing.add(boom);
      const panelW = 2.1, panelH = 0.85;
      const panel = new THREE.Mesh(new THREE.BoxGeometry(panelW, panelH, 0.035), panelBlue);
      panel.position.set(dir * (0.65 + boomLen + panelW/2), 0, 0);
      wing.add(panel);
      const edge = new THREE.Mesh(new THREE.BoxGeometry(panelW, 0.03, 0.04), goldFoil);
      edge.position.set(dir * (0.65 + boomLen + panelW/2), panelH/2, 0);
      wing.add(edge);
      const edge2 = edge.clone(); edge2.position.y = -panelH/2; wing.add(edge2);
      return wing;
    }
    satellite.add(buildWing(-1));
    satellite.add(buildWing(1));

    satellite.scale.setScalar(1.1);
    satellite.position.set(-2.2, 2.4, 1.5);
    scene.add(satellite);

    let raf;
    let disposed = false;
    const t0 = performance.now();
    const animate = () => {
      if (disposed) return;
      raf = requestAnimationFrame(animate);
      const t = (performance.now() - t0) / 1000;
      satellite.rotation.y = t * 0.15;
      earth.rotation.y = t * 0.03;
      try {
        controls.update();
        renderer.render(scene, camera);
      } catch {
        // Never let a single bad frame kill the whole loop (same
        // guard used on the Orbital tab's globe and the 404 page).
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
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      controls.dispose();
      renderer.dispose();
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement);
    };
  }, []);

  return <div ref={containerRef} style={{ width: '100%', height: '100%' }} />;
}
