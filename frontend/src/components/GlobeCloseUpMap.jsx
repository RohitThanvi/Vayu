/**
 * GlobeCloseUpMap.jsx — the high-res 2D handoff shown over OrbitalGlobe
 * when the user zooms the 3D globe in close (see OrbitalGlobe.jsx's
 * CLOSE_ZOOM_ENTER_DISTANCE and App.jsx's globeCloseUp state).
 *
 * Reuses the same free, keyless Esri World Imagery tile source as the
 * main map's "High-Res Close-Up" satellite layer (see App.jsx's
 * ESRI_HIGH_RES_URL) — the 3D Earth texture is a single static 2048px
 * image, so it can't show real close-up detail no matter how far you
 * zoom into it; this swaps to an actual tiled, high-resolution source
 * instead of trying to fake more detail out of the globe texture.
 *
 * A lightweight, standalone Leaflet instance, not the app's main map —
 * this one is deliberately minimal (no AOI drawing, no vessel layers,
 * no intel overlays), since its only job is "let the user see this one
 * spot close up," not replace the main 2D map.
 */

import { useEffect, useRef } from 'react';
// Leaflet is loaded globally via a <script> CDN tag in index.html (see
// App.jsx's VayuMap, which uses the same global `L` for the main map) —
// not an npm package, so no import here, same as everywhere else.

const ESRI_HIGH_RES_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
const ESRI_ATTRIBUTION = 'Esri, Maxar, Earthstar Geographics, and the GIS community';

const START_ZOOM = 15;
const EXIT_ZOOM = 3; // zooming out past this hands control back to the 3D globe

export default function GlobeCloseUpMap({ lat, lon, onExit }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const onExitRef = useRef(onExit);
  useEffect(() => { onExitRef.current = onExit; }, [onExit]);

  useEffect(() => {
    if (!containerRef.current) return;
    const map = L.map(containerRef.current, {
      center: [lat, lon], zoom: START_ZOOM,
      minZoom: 2, maxZoom: 20, maxNativeZoom: 19,
      zoomControl: true, attributionControl: true,
    });
    L.tileLayer(ESRI_HIGH_RES_URL, { attribution: ESRI_ATTRIBUTION, maxZoom: 20, maxNativeZoom: 19 }).addTo(map);
    L.marker([lat, lon]).addTo(map);
    map.on('zoomend', () => {
      if (map.getZoom() <= EXIT_ZOOM) onExitRef.current?.();
    });
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
    // Deliberately NOT re-centering on every lat/lon change — this
    // mounts fresh each time App.jsx's globeCloseUp goes from null to a
    // value (that's the natural remount boundary; see the key prop on
    // its usage site), so lat/lon here are only ever the values at the
    // moment this specific handoff started.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ position: 'absolute', inset: 0, zIndex: 2 }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <button
        onClick={() => onExitRef.current?.()}
        style={{
          position: 'absolute', top: 12, left: 12, zIndex: 1000,
          display: 'flex', alignItems: 'center', gap: 6,
          background: 'rgba(10,12,15,0.85)', border: '1px solid #2a3040', color: '#e8eef4',
          fontFamily: "'JetBrains Mono','Courier New',monospace", fontSize: 12,
          padding: '6px 10px', borderRadius: 4, cursor: 'pointer',
        }}
      >
        ← Back to Globe
      </button>
      <div style={{
        position: 'absolute', bottom: 8, left: 12, zIndex: 1000,
        fontFamily: "'JetBrains Mono','Courier New',monospace", fontSize: 10.5, color: 'rgba(232,238,244,0.6)',
      }}>
        High-res close-up (Esri) — zoom out past street level to return to the 3D globe
      </div>
    </div>
  );
}
