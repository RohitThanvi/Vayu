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
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';

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

    // Slightly lift the day-side exposure without washing out
    // the underlying Blue Marble texture detail.
    dayColor *= 1.22;

    vec3 nightColor = texture2D(nightTexture, vUv).rgb * 1.6;
    vec3 color = mix(nightColor, dayColor, mixFactor);

    // Subtle ambient lift so the shadow-side terrain isn't crushed.
    color += vec3(0.025, 0.03, 0.04) * (1.0 - mixFactor * 0.65);

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
// Real satellite — NASA's own official 3D model (Landsat 7, from NASA's
// public-domain "NASA-3D-Resources" collection), not a hand-built
// primitive greeble. Loaded as a Draco-compressed glTF; both the model
// and the Draco decoder are mirrored locally under /public (same
// reasoning as the Earth textures above — no runtime dependency on a
// third-party CDN for the hero's first paint). Landsat is also a
// thematically right choice for a geospatial-intelligence product: it's
// a real Earth-observation satellite, not just any satellite.
// ---------------------------------------------------------------------
function loadSatelliteModel(onReady, isCancelled) {
  const dracoLoader = new DRACOLoader();
  dracoLoader.setDecoderPath('/draco/');
  const gltfLoader = new GLTFLoader();
  gltfLoader.setDRACOLoader(dracoLoader);

  const group = new THREE.Group(); // orbit position only — never rotated
  const attitude = new THREE.Group(); // fixed-attitude body — orientation held constant
  attitude.rotation.set(0.32, 0.75, 0.16);
  group.add(attitude);
  const hitMeshes = [];

  gltfLoader.load(
    '/models/landsat7.glb',
    (gltf) => {
      // The component may have unmounted while this (async) load was
      // in flight — bail out and dispose what just loaded rather than
      // attaching it to a group nothing will ever clean up again.
      if (isCancelled()) {
        gltf.scene.traverse((o) => {
          if (o.geometry) o.geometry.dispose();
          if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => m.dispose());
        });
        return;
      }
      const model = gltf.scene;
      // Normalize NASA's real-world-scale model (arbitrary units/size
      // per source file) to a consistent footprint relative to the
      // Earth sphere (radius 1) — center it on its own origin, then
      // scale its longest axis to a fixed target size.
      const box = new THREE.Box3().setFromObject(model);
      const size = new THREE.Vector3();
      box.getSize(size);
      const center = new THREE.Vector3();
      box.getCenter(center);
      model.position.sub(center);
      const targetSize = 0.62;
      const scale = targetSize / Math.max(size.x, size.y, size.z, 0.0001);
      model.scale.setScalar(scale);

      model.traverse((o) => {
        if (o.isMesh) {
          hitMeshes.push(o);
          if (o.material) o.material.side = THREE.FrontSide;
        }
      });

      attitude.add(model);
      onReady();
    },
    undefined,
    (err) => {
      // Model failed to load (e.g. blocked request) — the hero still
      // works fine with just the Earth; log for diagnosis rather than
      // silently leaving a confusing empty orbit.
      console.warn('HeroEarth: satellite model failed to load', err);
    }
  );

  return { group, attitude, hitMeshes, dispose: () => { dracoLoader.dispose(); } };
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
    camera.position.set(0.4 + 0.95, 0.25 - 0.08, 2.85);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    mount.appendChild(renderer.domElement);

    const texManager = new THREE.LoadingManager();
    let texturesReady = false;
    texManager.onLoad = () => { texturesReady = true; };
    const loader = new THREE.TextureLoader(texManager);
    const loadTex = (url) => {
      const t = loader.load(url);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    };
    const dayTex = loadTex(TEX.day);
    const nightTex = loadTex(TEX.night);
    const cloudsTex = loadTex(TEX.clouds);
    const specTex = loadTex(TEX.specular);

    // Everything but the starfield lives in worldGroup, offset to the
    // right of the camera's look-at origin — this is what pushes the
    // globe + satellite into a center-right composition, leaving the
    // left of the hero clear for copy, without any lens distortion
    // (camera + controls target move by the same offset below).
    const WORLD_OFFSET = new THREE.Vector3(0.95, -0.08, 0);
    const worldGroup = new THREE.Group();
    worldGroup.position.copy(WORLD_OFFSET);
    scene.add(worldGroup);

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
    worldGroup.add(earth);

    const cloudsMat = new THREE.MeshStandardMaterial({
      map: cloudsTex, transparent: true, opacity: 0.5, depthWrite: false, roughness: 1,
    });
    const clouds = new THREE.Mesh(new THREE.SphereGeometry(1.012, 64, 64), cloudsMat);
    worldGroup.add(clouds);

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
    worldGroup.add(atmosphere);

    // Textured meshes stay hidden until every texture has actually
    // decoded — otherwise there's a frame (or several, on a slow
    // connection) where THREE's default 1x1 white placeholder texture
    // renders as a plain lit sphere, which reads as a broken blob
    // rather than Earth. The satellite is procedural (canvas textures,
    // no network fetch) so it's unaffected and appears immediately.
    earth.visible = texturesReady; clouds.visible = texturesReady; atmosphere.visible = texturesReady;
    texManager.onLoad = () => { earth.visible = true; clouds.visible = true; atmosphere.visible = true; };

    // Faint distant starfield for depth — cheap point cloud, no texture.
    // Left centered on the true origin (not worldGroup) since it's an
    // infinite backdrop, not part of the Earth/satellite composition.
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

    const sunLight = new THREE.DirectionalLight(0xffffff, 1.35);
    sunLight.position.copy(WORLD_OFFSET.clone().add(SUN_DIR.clone().multiplyScalar(10)));
    sunLight.target.position.copy(WORLD_OFFSET);
    scene.add(sunLight, sunLight.target);
    scene.add(new THREE.AmbientLight(0x263449, 0.7));

    // --- Real satellite (NASA Landsat 7 model) ---
    let satReady = false;
    const sat = loadSatelliteModel(() => { satReady = true; sat.group.visible = true; }, () => disposed);
    sat.group.visible = false; // hidden until the glTF finishes loading
    worldGroup.add(sat.group);
    const orbitRadius = 1.6;
    const orbitTilt = 0.35; // radians, plane inclination
    // Bounded arc rather than a full 360° revolution — a fixed,
    // camera-facing sweep so the satellite is always in frame (a full
    // orbit would spend half its time hidden behind the globe or off
    // to the side), while still visibly "moving in its orbit".
    const ORBIT_CENTER = 0.5;
    const ORBIT_AMPLITUDE = 0.32;
    const ORBIT_SPEED = 0.16;
    let orbitT = 0;
    const raycaster = new THREE.Raycaster();
    const pointerNDC = new THREE.Vector2();

    // Click gives a small one-shot scale "bounce" rather than any
    // deploy/stow animation — the real model has no articulated hinge
    // to drive, so this is just a lightweight, honest acknowledgement
    // that the click landed on the satellite.
    let pingT = -1;
    function pointerToNDC(clientX, clientY) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointerNDC.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointerNDC.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    }
    function hitSatellite() {
      if (!satReady) return false;
      raycaster.setFromCamera(pointerNDC, camera);
      return raycaster.intersectObjects(sat.hitMeshes, false).length > 0;
    }
    function onClick(e) {
      pointerToNDC(e.clientX, e.clientY);
      if (hitSatellite()) pingT = 0;
    }
    function onPointerMove(e) {
      pointerToNDC(e.clientX, e.clientY);
      renderer.domElement.style.cursor = hitSatellite() ? 'pointer' : '';
    }
    renderer.domElement.addEventListener('click', onClick);
    renderer.domElement.addEventListener('pointermove', onPointerMove);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(WORLD_OFFSET);
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
    let targetTiltX = 0, targetTiltY = 0;
    function animate() {
      if (disposed) return;
      raf = requestAnimationFrame(animate);
      const now = performance.now();
      const dt = Math.min(0.05, (now - lastT) / 1000);
      lastT = now;

      clouds.rotation.y += 0.0006;
      earthMat.uniforms.cameraWorldPosition.value.copy(camera.position);

      // Orbit motion — only the group's POSITION advances along a
      // bounded, camera-facing arc; its attitude (set once in
      // loadSatelliteModel) is never touched here, so the satellite keeps
      // a fixed orientation through the whole pass rather than
      // re-facing the direction of travel. Position is local to
      // worldGroup (which already carries the WORLD_OFFSET).
      orbitT += ORBIT_SPEED * dt;
      const angle = ORBIT_CENTER + Math.sin(orbitT) * ORBIT_AMPLITUDE;
      const cx = Math.cos(angle) * orbitRadius;
      const cz = Math.sin(angle) * orbitRadius;
      sat.group.position.set(cx, Math.sin(angle) * orbitRadius * Math.sin(orbitTilt) * 0.4, cz * Math.cos(orbitTilt));

      // Smooth one-shot scale "ping" on click (see onClick above) — a
      // quick overshoot-and-settle rather than a linear pulse, so it
      // reads as a deliberate acknowledgement rather than a glitch.
      if (pingT >= 0) {
        pingT += dt;
        const t = Math.min(1, pingT / 0.5);
        const bump = t < 1 ? Math.sin(t * Math.PI) * (1 - t) * 0.35 : 0;
        sat.attitude.scale.setScalar(1 + bump);
        if (t >= 1) pingT = -1;
      }

      // Mouse-parallax — the whole Earth+satellite group gently leans
      // toward wherever the pointer is, on top of (not instead of) the
      // constant autoRotate drift. Eased toward the target each frame
      // (not snapped) so it reads as weighty/mature rather than
      // twitchy. Purely additive to worldGroup's own position (set via
      // WORLD_OFFSET once above) — only rotation is touched here.
      targetTiltX += (pointerNDC.y * 0.12 - targetTiltX) * Math.min(1, dt * 2.2);
      targetTiltY += (pointerNDC.x * 0.16 - targetTiltY) * Math.min(1, dt * 2.2);
      worldGroup.rotation.x = targetTiltX;
      worldGroup.rotation.y = targetTiltY;

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

      sat.group.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) {
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          mats.forEach((m) => {
            ['map', 'normalMap', 'roughnessMap', 'metalnessMap', 'emissiveMap', 'aoMap'].forEach((k) => m[k]?.dispose());
            m.dispose();
          });
        }
      });
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
