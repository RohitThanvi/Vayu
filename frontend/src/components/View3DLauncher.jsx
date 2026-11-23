import { lazy, Suspense, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { view3dEnabled, view3dVisible } from '../lib/view3d/quality.js';

const Terrain3D = lazy(() => import('./Terrain3D.jsx'));

// A cube button that opens the optional 3D view over the current map position. It is rendered INTO the map's own control column (`slot`, made by createView3dSlot) directly below the draw-area tools,
// and only at city scale or closer (`zoom`, see view3dVisible). Renders nothing unless VITE_ENABLE_3D_VIEW=1; the 3D code is only downloaded on first click.
// getView() -> { lat, lon, zoom } | null (read at click time, so it always reflects where the map is now).
const Cube = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" aria-hidden="true" focusable="false">
    <path d="M12 2.5 3.5 7v10l8.5 4.5 8.5-4.5V7L12 2.5Z" />
    <path d="M3.5 7 12 11.5 20.5 7M12 11.5v10" />
  </svg>
);

export default function View3DLauncher({ slot, zoom, getView, env = import.meta.env }) {
  const [view, setView] = useState(null);
  const enabled = view3dEnabled(env), shown = view3dVisible(env, zoom);
  // One console line so it is obvious from the browser console whether this build has the flag (VITE_ variables are baked in at build time, so a changed variable needs a redeploy).
  useEffect(() => { if (typeof console !== 'undefined') console.info(enabled ? '[Vayu] 3D view is enabled in this build (shown at city zoom).' : '[Vayu] 3D view is off in this build: set VITE_ENABLE_3D_VIEW=1 for this Vercel environment and redeploy.'); }, [enabled]);
  // The slot is a Leaflet control box: hide the whole box (not just the button) when out of range, or an empty bordered square would show.
  useEffect(() => { if (slot) slot.style.display = shown ? '' : 'none'; }, [slot, shown]);
  if (!enabled) return null;
  const open = (e) => { e.preventDefault(); const v = getView?.(); if (v && Number.isFinite(v.lat) && Number.isFinite(v.lon)) setView(v); };
  return (
    <>
      {slot && shown && createPortal(
        <a href="#" role="button" data-testid="open-3d" title="3D view" aria-label="Open 3D view of the current map area" onClick={open} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}><Cube /></a>,
        slot,
      )}
      {view && (
        <Suspense fallback={<div style={{ position: 'fixed', inset: 0, zIndex: 2100, background: 'rgba(0,0,0,0.6)', color: '#e8efe9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading 3D view…</div>}>
          <Terrain3D center={{ lat: view.lat, lon: view.lon }} zoom={view.zoom} onClose={() => setView(null)} />
        </Suspense>
      )}
    </>
  );
}
