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

    function animate() {
      if (disposed) return;
      raf = requestAnimationFrame(animate);
      clouds.rotation.y += 0.0006;
      earthMat.uniforms.cameraWorldPosition.value.copy(camera.position);
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
      controls.dispose();
      [dayTex, nightTex, cloudsTex, specTex].forEach((t) => t.dispose());
      earthGeo.dispose(); earthMat.dispose();
      clouds.geometry.dispose(); cloudsMat.dispose();
      atmosphere.geometry.dispose(); atmosphereMat.dispose();
      starGeo.dispose();
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
