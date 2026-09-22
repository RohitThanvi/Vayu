/**
 * HeroEarth.jsx
 * Photorealistic 3D Earth for the landing page hero — replaces the flat
 * Ken-Burns satellite photo. Deliberately NOT the low-poly/neon look:
 * this uses real NASA Blue Marble-derived day/night/cloud/specular maps
 * (the same free, no-key texture set OrbitalGlobe.jsx already uses for
 * the in-app Orbital tab, mirrored locally under /public/earth/ here so
 * the hero doesn't depend on a third-party CDN being up), a custom
 * day/night terminator shader with an ocean specular highlight, a
 * separate cloud shell, and a fresnel-rim atmosphere glow — not a
 * MeshBasicMaterial sphere with a wireframe or a particle halo.
 *
 * Camera orbits slowly on its own (autoRotate) and responds to
 * drag/scroll via OrbitControls (zoom/pan disabled — this reads as "a
 * globe you nudge", not a free camera that can drift off it). Falls
 * back to a static image (same photo as the old hero) on mobile or if
 * WebGL isn't available, per the "no janky 3D on low-power devices"
 * constraint — those users get the same instant, battery-cheap hero
 * the page always had rather than a stuttering globe.
 */

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const TEX = {
  day: '/earth/earth_atmos_2048.jpg',
  night: '/earth/earth_lights_2048.png',
  clouds: '/earth/earth_clouds_1024.png',
  specular: '/earth/earth_specular_2048.jpg',
};

// World-space sun direction — fixed regardless of camera orbit, so the
// day/night terminator reads as a real fixed-illumination globe rather
// than a texture that reorients with the camera.
const SUN_DIR = new THREE.Vector3(-1, 0.2, 1).normalize();

const EARTH_VERTEX = `
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;
  void main() {
    vUv = uv;
    vWorldNormal = normalize(mat3(modelMatrix) * normal);
    vWorldPosition = (modelMatrix * vec4(position, 1.0)).xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const EARTH_FRAGMENT = `
  uniform sampler2D dayTexture;
  uniform sampler2D nightTexture;
  uniform sampler2D specularMap;
  uniform vec3 sunDirection;
  uniform vec3 cameraWorldPosition;
  varying vec2 vUv;
  varying vec3 vWorldNormal;
  varying vec3 vWorldPosition;

  void main() {
    vec3 normal = normalize(vWorldNormal);
    float sunFacing = dot(normal, normalize(sunDirection));
    // Soft terminator band rather than a hard day/night line — real
    // atmospheric scattering fades the boundary over a few degrees.
    float mixFactor = smoothstep(-0.18, 0.12, sunFacing);

    vec3 dayColor = texture2D(dayTexture, vUv).rgb;
    vec3 nightColor = texture2D(nightTexture, vUv).rgb * 1.6;
    vec3 color = mix(nightColor, dayColor, mixFactor);

    // Ocean specular highlight — only where the specular mask (water)
    // is bright AND the surface faces the sun, so it reads as a real
    // sun-glint on water, not a uniform sheen over the whole day side.
    float specMask = texture2D(specularMap, vUv).r;
    vec3 viewDir = normalize(cameraWorldPosition - vWorldPosition);
    vec3 halfDir = normalize(normalize(sunDirection) + viewDir);
    float specAngle = max(dot(normal, halfDir), 0.0);
    float specular = pow(specAngle, 40.0) * specMask * mixFactor;
    color += vec3(0.85, 0.92, 1.0) * specular * 0.9;

    // Faint blue atmospheric edge-tint on the day side (Rayleigh-ish)
    float rim = pow(1.0 - max(dot(normal, viewDir), 0.0), 3.0);
    color += vec3(0.25, 0.45, 0.75) * rim * mixFactor * 0.35;

    gl_FragColor = vec4(color, 1.0);
  }
`;

const ATMOSPHERE_VERTEX = `
  varying vec3 vNormal;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const ATMOSPHERE_FRAGMENT = `
  uniform vec3 glowColor;
  varying vec3 vNormal;
  void main() {
    float intensity = pow(0.62 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0);
    gl_FragColor = vec4(glowColor, 1.0) * clamp(intensity, 0.0, 1.0);
  }
`;

// ---------------------------------------------------------------------
// Procedural photorealistic satellite (no external model file — built
// from primitives + generated PBR-ish textures so it needs no asset
// pipeline). Orbits Earth at a fixed attitude (only the solar wings
// articulate, on click) rather than tumbling or re-orienting to face
// the direction of travel, since a real 3-axis-stabilized satellite
// holds attitude independent of its orbital position.
// ---------------------------------------------------------------------

// Crinkled gold MLI (multi-layer insulation) foil — subtle noise bump
// map so the body doesn't read as a flat-shaded box.
function makeFoilBumpTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#808080';
  ctx.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 2200; i++) {
    const v = 90 + Math.random() * 130;
    ctx.fillStyle = `rgb(${v},${v},${v})`;
    const x = Math.random() * 256, y = Math.random() * 256;
    ctx.fillRect(x, y, 1 + Math.random() * 2.5, 1 + Math.random() * 2.5);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(3, 2);
  return tex;
}

// Solar-cell grid — deep indigo cells with a fine silver grid and a
// soft diagonal sheen, read through a physical (clearcoat) material so
// it catches a glassy specular highlight like real cover-glass cells.
function makeSolarPanelTexture() {
  const c = document.createElement('canvas');
  c.width = 256; c.height = 512;
  const ctx = c.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 256, 512);
  grad.addColorStop(0, '#141c33');
  grad.addColorStop(0.5, '#0d1226');
  grad.addColorStop(1, '#161d38');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 256, 512);
  ctx.strokeStyle = 'rgba(180,195,225,0.55)';
  ctx.lineWidth = 1.4;
  const cols = 6, rows = 12;
  for (let i = 0; i <= cols; i++) {
    ctx.beginPath(); ctx.moveTo((256 / cols) * i, 0); ctx.lineTo((256 / cols) * i, 512); ctx.stroke();
  }
  for (let j = 0; j <= rows; j++) {
    ctx.beginPath(); ctx.moveTo(0, (512 / rows) * j); ctx.lineTo(256, (512 / rows) * j); ctx.stroke();
  }
  ctx.fillStyle = 'rgba(255,255,255,0.05)';
  ctx.beginPath();
  ctx.moveTo(0, 0); ctx.lineTo(256, 100); ctx.lineTo(256, 220); ctx.lineTo(0, 90); ctx.closePath();
  ctx.fill();
  const tex = new THREE.CanvasTexture(c);
  return tex;
}

// White radiator-panel texture (aft equipment deck) — thin black grid
// on satin white, standard spacecraft thermal-panel look.
function makeRadiatorTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#eef0f2';
  ctx.fillRect(0, 0, 128, 128);
  ctx.strokeStyle = 'rgba(30,30,35,0.35)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    ctx.beginPath(); ctx.moveTo((128 / 4) * i, 0); ctx.lineTo((128 / 4) * i, 128); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, (128 / 4) * i); ctx.lineTo(128, (128 / 4) * i); ctx.stroke();
  }
  return new THREE.CanvasTexture(c);
}

function buildSatellite() {
  const group = new THREE.Group(); // orbit position only — never rotated
  const attitude = new THREE.Group(); // fixed-attitude body — orientation held constant
  attitude.rotation.set(0.35, 0.9, 0.12);
  group.add(attitude);

  const hitMeshes = [];
  const foilBump = makeFoilBumpTexture();
  const solarTex = makeSolarPanelTexture();
  const radiatorTex = makeRadiatorTexture();

  const foilMat = new THREE.MeshStandardMaterial({ color: 0xc79a56, metalness: 0.55, roughness: 0.42, bumpMap: foilBump, bumpScale: 0.006 });
  const chassisMat = new THREE.MeshStandardMaterial({ color: 0xb9bfc7, metalness: 0.75, roughness: 0.3 });
  const darkMat = new THREE.MeshStandardMaterial({ color: 0x1c2027, metalness: 0.4, roughness: 0.6 });
  const dishMat = new THREE.MeshStandardMaterial({ color: 0xe9ecef, metalness: 0.35, roughness: 0.28, side: THREE.DoubleSide });
  const radiatorMat = new THREE.MeshStandardMaterial({ map: radiatorTex, metalness: 0.15, roughness: 0.45 });
  const solarMat = new THREE.MeshPhysicalMaterial({ map: solarTex, metalness: 0.25, roughness: 0.35, clearcoat: 0.65, clearcoatRoughness: 0.22 });
  const railMat = new THREE.MeshStandardMaterial({ color: 0xd8dbe0, metalness: 0.8, roughness: 0.25 });

  // --- Bus (main body) ---
  const bodyGeo = new THREE.BoxGeometry(0.15, 0.11, 0.2);
  const body = new THREE.Mesh(bodyGeo, foilMat);
  attitude.add(body);
  hitMeshes.push(body);
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(bodyGeo), new THREE.LineBasicMaterial({ color: 0x2a2f36, transparent: true, opacity: 0.5 }));
  body.add(edges);

  // Aft radiator deck
  const radiator = new THREE.Mesh(new THREE.BoxGeometry(0.152, 0.112, 0.02), radiatorMat);
  radiator.position.set(0, 0, -0.11);
  attitude.add(radiator);
  hitMeshes.push(radiator);

  // --- Dish antenna, offset boom off the front face ---
  const boom = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.09, 8), chassisMat);
  boom.rotation.z = Math.PI / 2.4;
  boom.position.set(0.09, 0.07, 0.09);
  attitude.add(boom);
  const dishPts = [];
  for (let i = 0; i <= 10; i++) { const t = i / 10; dishPts.push(new THREE.Vector2(t * 0.065, t * t * 0.03)); }
  const dish = new THREE.Mesh(new THREE.LatheGeometry(dishPts, 24), dishMat);
  dish.rotation.x = Math.PI / 2;
  dish.position.set(0.15, 0.11, 0.13);
  attitude.add(dish);
  hitMeshes.push(dish);
  const feed = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.045, 6), darkMat);
  feed.position.set(0.15, 0.155, 0.13);
  attitude.add(feed);

  // Whip antennas
  [[-0.06, 0.06, 0.1, 0.5], [-0.02, 0.06, 0.1, -0.35]].forEach(([x, y, z, tilt]) => {
    const whip = new THREE.Mesh(new THREE.CylinderGeometry(0.0015, 0.0015, 0.07, 5), darkMat);
    whip.position.set(x, y, z);
    whip.rotation.z = tilt;
    attitude.add(whip);
  });

  // Aft thruster nozzles
  [-0.045, 0.045].forEach((x) => {
    const nozzle = new THREE.Mesh(new THREE.ConeGeometry(0.014, 0.03, 10, 1, true), darkMat);
    nozzle.rotation.x = Math.PI / 2;
    nozzle.position.set(x, -0.03, -0.135);
    attitude.add(nozzle);
  });

  // Small star-tracker sensor box
  const tracker = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.025, 0.04), darkMat);
  tracker.position.set(-0.06, 0.075, 0.06);
  attitude.add(tracker);

  // --- Solar wings — each is a hinge group at the body edge; a panel
  // segment stack extends outward from the hinge. Rotating the hinge
  // about its local Y axis swings the wing from stowed (folded flush
  // against the body) to deployed (extended straight out to the side).
  const wings = [];
  [1, -1].forEach((side) => {
    const hinge = new THREE.Group();
    hinge.position.set(side * 0.078, 0, 0.02);
    attitude.add(hinge);

    const yoke = new THREE.Mesh(new THREE.BoxGeometry(0.012, 0.03, 0.03), chassisMat);
    hinge.add(yoke);

    const panelGroup = new THREE.Group();
    hinge.add(panelGroup);

    const segCount = 3, segW = 0.09, segH = 0.2, gap = 0.006;
    for (let i = 0; i < segCount; i++) {
      const seg = new THREE.Mesh(new THREE.BoxGeometry(segW, segH, 0.004), solarMat);
      seg.position.set(side * (segW / 2 + i * (segW + gap)), 0, 0);
      panelGroup.add(seg);
      hitMeshes.push(seg);
      const railTop = new THREE.Mesh(new THREE.BoxGeometry(segW, 0.006, 0.006), railMat);
      railTop.position.set(seg.position.x, segH / 2, 0.003);
      panelGroup.add(railTop);
      const railBot = railTop.clone();
      railBot.position.y = -segH / 2;
      panelGroup.add(railBot);
    }

    // rotation.y: 0 = deployed (extended sideways), ±HALF_PI = stowed
    // (folded flat against the body's flank).
    wings.push({ hinge, current: -side * (Math.PI / 2), target: -side * (Math.PI / 2) });
    hinge.rotation.y = -side * (Math.PI / 2);
  });

  return { group, attitude, wings, hitMeshes, dispose: () => {
    foilBump.dispose(); solarTex.dispose(); radiatorTex.dispose();
    [foilMat, chassisMat, darkMat, dishMat, radiatorMat, solarMat, railMat].forEach((m) => m.dispose());
  } };
}

function supportsWebGL() {
  try {
    const canvas = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (canvas.getContext('webgl') || canvas.getContext('experimental-webgl')));
  } catch {
    return false;
  }
}

export default function HeroEarth({ disabled = false, style }) {
  const mountRef = useRef(null);
  const [failed, setFailed] = useState(disabled || !supportsWebGL());

  useEffect(() => {
    if (failed) return;
    const mount = mountRef.current;
    if (!mount) return;

    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch {
      setFailed(true);
      return;
    }

    let raf;
    let disposed = false;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, mount.clientWidth / Math.max(mount.clientHeight, 1), 0.1, 100);
    camera.position.set(0.4, 0.25, 2.85);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const loader = new THREE.TextureLoader();
    const loadTex = (url) => {
      const t = loader.load(url);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    const dayTex = loadTex(TEX.day);
    const nightTex = loadTex(TEX.night);
    const cloudsTex = loadTex(TEX.clouds);
    const specTex = loadTex(TEX.specular);

    const earthGeo = new THREE.SphereGeometry(1, 64, 64);
    const earthMat = new THREE.ShaderMaterial({
      uniforms: {
        dayTexture: { value: dayTex },
        nightTexture: { value: nightTex },
        specularMap: { value: specTex },
        sunDirection: { value: SUN_DIR },
        cameraWorldPosition: { value: camera.position.clone() },
      },
      vertexShader: EARTH_VERTEX,
      fragmentShader: EARTH_FRAGMENT,
    });
    const earth = new THREE.Mesh(earthGeo, earthMat);
    scene.add(earth);

    const cloudsMat = new THREE.MeshStandardMaterial({
      map: cloudsTex, transparent: true, opacity: 0.5, depthWrite: false, roughness: 1,
    });
    const clouds = new THREE.Mesh(new THREE.SphereGeometry(1.012, 64, 64), cloudsMat);
    scene.add(clouds);

    const atmosphereMat = new THREE.ShaderMaterial({
      uniforms: { glowColor: { value: new THREE.Color('#38BDF8') } },
      vertexShader: ATMOSPHERE_VERTEX,
      fragmentShader: ATMOSPHERE_FRAGMENT,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
      transparent: true,
      depthWrite: false,
    });
    const atmosphere = new THREE.Mesh(new THREE.SphereGeometry(1.16, 64, 64), atmosphereMat);
    scene.add(atmosphere);

    // Faint distant starfield for depth — cheap point cloud, no texture.
    const starGeo = new THREE.BufferGeometry();
    const STAR_COUNT = 700;
    const starPos = new Float32Array(STAR_COUNT * 3);
    for (let i = 0; i < STAR_COUNT; i++) {
      const r = 30 + Math.random() * 40;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      starPos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      starPos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      starPos[i * 3 + 2] = r * Math.cos(phi);
    }
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xdbe6f0, size: 0.045, transparent: true, opacity: 0.5 }));
    scene.add(stars);

    const sunLight = new THREE.DirectionalLight(0xffffff, 1.1);
    sunLight.position.copy(SUN_DIR.clone().multiplyScalar(10));
    scene.add(sunLight);
    scene.add(new THREE.AmbientLight(0x1a2233, 0.5));

    // --- Photorealistic orbiting satellite ---
    const sat = buildSatellite();
    scene.add(sat.group);
    const orbitRadius = 1.34;
    const orbitTilt = 0.5; // radians, plane inclination
    let orbitAngle = Math.random() * Math.PI * 2;
    const orbitSpeed = 0.09; // rad/sec — slow, deliberate pass
    const raycaster = new THREE.Raycaster();
    const pointerNDC = new THREE.Vector2();

    function setDeployed(deployed) {
      sat.wings.forEach((w, i) => {
        const side = i === 0 ? 1 : -1;
        w.target = deployed ? 0 : -side * (Math.PI / 2);
      });
    }
    let deployed = true; // starts deployed — normal on-orbit configuration
    setDeployed(deployed);

    function pointerToNDC(clientX, clientY) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointerNDC.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointerNDC.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    }
    function hitSatellite() {
      raycaster.setFromCamera(pointerNDC, camera);
      return raycaster.intersectObjects(sat.hitMeshes, false).length > 0;
    }
    function onClick(e) {
      pointerToNDC(e.clientX, e.clientY);
      if (hitSatellite()) { deployed = !deployed; setDeployed(deployed); }
    }
    function onPointerMove(e) {
      pointerToNDC(e.clientX, e.clientY);
      renderer.domElement.style.cursor = hitSatellite() ? 'pointer' : '';
    }
    renderer.domElement.addEventListener('click', onClick);
    renderer.domElement.addEventListener('pointermove', onPointerMove);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enablePan = false;
    controls.enableZoom = false;
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.rotateSpeed = 0.35;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.32;
    controls.minPolarAngle = Math.PI / 2 - 0.55;
    controls.maxPolarAngle = Math.PI / 2 + 0.55;

    let lastT = performance.now();
    function animate() {
      if (disposed) return;
      raf = requestAnimationFrame(animate);
      const now = performance.now();
      const dt = Math.min(0.05, (now - lastT) / 1000);
      lastT = now;

      clouds.rotation.y += 0.0006;
      earthMat.uniforms.cameraWorldPosition.value.copy(camera.position);

      // Orbit motion — only the group's POSITION advances around Earth;
      // its attitude (set once in buildSatellite) is never touched here,
      // so the satellite keeps a fixed orientation through the whole
      // pass rather than re-facing the direction of travel.
      orbitAngle += orbitSpeed * dt;
      const cx = Math.cos(orbitAngle) * orbitRadius;
      const cz = Math.sin(orbitAngle) * orbitRadius;
      sat.group.position.set(cx, Math.sin(orbitAngle * 1) * orbitRadius * Math.sin(orbitTilt), cz * Math.cos(orbitTilt));

      // Smoothly ease each wing hinge toward its deploy/stow target.
      sat.wings.forEach((w) => {
        w.current += (w.target - w.current) * Math.min(1, dt * 3.2);
        w.hinge.rotation.y = w.current;
      });

      controls.update();
      renderer.render(scene, camera);
    }
    animate();

    function onResize() {
      if (!mount || disposed) return;
      const w = mount.clientWidth, h = Math.max(mount.clientHeight, 1);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    }
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(mount);
    window.addEventListener('resize', onResize);

    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('click', onClick);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      controls.dispose();
      [dayTex, nightTex, cloudsTex, specTex].forEach((t) => t.dispose());
      earthGeo.dispose(); earthMat.dispose();
      clouds.geometry.dispose(); cloudsMat.dispose();
      atmosphere.geometry.dispose(); atmosphereMat.dispose();
      starGeo.dispose();
      sat.group.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
      sat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [failed]);

  if (failed) {
    // Static fallback — same photo the hero always used, still Ken-Burns
    // animated via the caller's existing .vayu-hero-bg class if passed in.
    return (
      <div
        className="vayu-hero-bg"
        style={{
          position: 'absolute', inset: -24, backgroundImage: 'url(/hero-satellite.jpg)',
          backgroundSize: 'cover', backgroundPosition: 'center', ...style,
        }}
      />
    );
  }

  return <div ref={mountRef} style={{ position: 'absolute', inset: 0, ...style }} />;
}
