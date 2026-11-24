// Tile-load bookkeeping for the optional 3D view, so a blank globe explains itself instead of showing a bare blue screen.
// Imagery and terrain report each tile result here; problemText() turns the counts into one honest sentence (or null when all is well). Pure: no DOM, no Cesium.
const slot = () => ({ ok: 0, fail: 0, first: null });
const clip = (m) => String(m || 'unknown error').replace(/\s+/g, ' ').trim().slice(0, 160);

export function createTileStats() {
  const stats = { imagery: slot(), terrain: slot() };
  const rec = (k) => (r) => { if (r && r.ok) stats[k].ok += 1; else { stats[k].fail += 1; if (!stats[k].first) stats[k].first = clip(r && r.message); } };
  return { stats, imagery: rec('imagery'), terrain: rec('terrain'), resetImagery() { stats.imagery = slot(); } };
}

/** One sentence describing what is wrong, or null. elapsedMs = time since the view opened; graceMs = how long "nothing loaded yet" is still just loading. */
export function problemText(stats, elapsedMs = 0, graceMs = 6000) {
  const parts = [], im = stats.imagery, te = stats.terrain;
  if (im.fail > 0 && im.ok === 0) parts.push(`Imagery: no tile loaded, ${im.fail} failed (first error: ${im.first}).`);
  else if (im.ok === 0 && elapsedMs > graceMs) parts.push('Imagery: no tile has loaded yet.');
  else if (im.fail > 0) parts.push(`Imagery: ${im.ok} loaded, ${im.fail} failed (first error: ${im.first}).`);
  if (te.fail > 0) parts.push(`Terrain: ${te.fail} tile${te.fail === 1 ? '' : 's'} could not be built from elevation data and ${te.fail === 1 ? 'is' : 'are'} drawn flat (first error: ${te.first}).`);
  return parts.length ? parts.join(' ') : null;
}

export const errMessage = (err) => (err && (err.message || (err.statusCode ? `HTTP ${err.statusCode}` : err.type))) || String(err);
