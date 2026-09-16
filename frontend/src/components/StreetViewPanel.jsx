/**
 * StreetViewPanel.jsx — free, crowdsourced street-level imagery via
 * Mapillary (mapillary-js), shown as an overlay panel when the user
 * clicks the map in Street View mode (see App.jsx's streetViewMode).
 *
 * Honest framing, since this gets compared to Google Street View in
 * people's heads by default: Mapillary is genuinely free (no billing,
 * generous free API tier) and crowdsourced, but coverage is real and
 * uneven — dense in some cities, sparse-to-nonexistent in many rural
 * or less-mapped areas. "No imagery found here" is an expected, normal
 * outcome, not a bug.
 */

import { useEffect, useRef, useState } from 'react';
import { Viewer } from 'mapillary-js';
import 'mapillary-js/dist/mapillary.css';

const S_BG = '#0a0c0f';
const S_BORDER = '#2a3040';
const S_TEXT = '#ffffff';
const S_TEXT2 = 'rgba(255,255,255,0.7)';
const S_ACCENT = '#7eb8d4';

export default function StreetViewPanel({ imageId, accessToken, capturedAt, onClose }) {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    if (!containerRef.current || !imageId) return;
    let cancelled = false;
    try {
      const viewer = new Viewer({
        accessToken,
        container: containerRef.current,
        imageId,
      });
      viewerRef.current = viewer;
      viewer.on('image', () => { if (!cancelled) setLoadError(null); });
    } catch (e) {
      setLoadError(e.message || 'Failed to load street-level imagery.');
    }
    return () => {
      cancelled = true;
      if (viewerRef.current) {
        try { viewerRef.current.remove(); } catch {}
        viewerRef.current = null;
      }
    };
  }, [imageId, accessToken]);

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,0.6)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20,
    }}>
      <div style={{
        width: '100%', maxWidth: 960, height: '70vh', background: S_BG,
        border: `1px solid ${S_BORDER}`, borderRadius: 6, overflow: 'hidden',
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px', borderBottom: `1px solid ${S_BORDER}`,
        }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ fontFamily: "'JetBrains Mono','Courier New',monospace", fontSize: 12, letterSpacing: 1, color: S_TEXT }}>
              STREET VIEW — MAPILLARY
            </span>
            {capturedAt && (
              <span style={{ fontSize: 10.5, color: S_TEXT2, fontFamily: "'JetBrains Mono','Courier New',monospace" }}>
                Captured {new Date(capturedAt).toLocaleDateString()} · crowdsourced imagery, not a scheduled survey
              </span>
            )}
          </div>
          <button onClick={onClose} style={{
            background: 'transparent', border: `1px solid ${S_BORDER}`, color: S_TEXT2,
            width: 28, height: 28, borderRadius: 3, cursor: 'pointer', fontSize: 14,
          }}>✕</button>
        </div>
        <div style={{ flex: 1, position: 'relative', background: '#000' }}>
          <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
          {loadError && (
            <div style={{
              position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: S_TEXT2, fontSize: 13, fontFamily: "'JetBrains Mono','Courier New',monospace", padding: 20, textAlign: 'center',
            }}>
              {loadError}
            </div>
          )}
        </div>
        <div style={{ padding: '8px 14px', borderTop: `1px solid ${S_BORDER}`, fontSize: 10.5, color: S_TEXT2, fontFamily: "'JetBrains Mono','Courier New',monospace" }}>
          Drag to look around · click the arrows on the ground to move along the sequence · <span style={{ color: S_ACCENT }}>Mapillary</span> (CC BY-SA, crowdsourced)
        </div>
      </div>
    </div>
  );
}
