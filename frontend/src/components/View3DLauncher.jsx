import { lazy, Suspense, useState } from 'react';
import { view3dEnabled } from '../lib/view3d/quality.js';

const Terrain3D = lazy(() => import('./Terrain3D.jsx'));

// Floating button that opens the optional 3D view over the current map position. Renders nothing unless VITE_ENABLE_3D_VIEW=1, and the 3D code is only downloaded on first click.
// getView() -> { lat, lon, zoom } | null (read at click time, so it always reflects where the map is now).
export default function View3DLauncher({ getView, env = import.meta.env }) {
  const [view, setView] = useState(null);
  if (!view3dEnabled(env)) return null;
  return (
    <>
      <button data-testid="open-3d" aria-label="Open 3D view of the current map area" onClick={() => { const v = getView?.(); if (v && Number.isFinite(v.lat) && Number.isFinite(v.lon)) setView(v); }}
        style={{ position: 'fixed', left: 12, bottom: 96, zIndex: 1500, padding: '8px 12px', fontSize: 13, fontFamily: "'JetBrains Mono','Courier New',monospace", letterSpacing: 1.5, background: 'rgba(10,18,14,0.85)', border: '1px solid rgba(126,184,212,0.6)', color: '#7eb8d4', cursor: 'pointer' }}>
        3D VIEW
      </button>
      {view && (
        <Suspense fallback={<div style={{ position: 'fixed', inset: 0, zIndex: 2100, background: 'rgba(0,0,0,0.6)', color: '#e8efe9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading 3D view…</div>}>
          <Terrain3D center={{ lat: view.lat, lon: view.lon }} zoom={view.zoom} onClose={() => setView(null)} />
        </Suspense>
      )}
    </>
  );
}
