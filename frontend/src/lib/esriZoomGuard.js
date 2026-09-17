/**
 * lib/esriZoomGuard.js — dynamically caps how far a Leaflet map can zoom
 * into Esri World Imagery, based on where real tile data actually stops
 * for the CURRENT location — rather than a single guessed-at global
 * constant (which we tried first: maxNativeZoom:19 with auto-upscale
 * past it. Problem: real coverage is genuinely location-dependent — some
 * areas have real imagery to z22+, many stop around z16-19, and some
 * remote areas have "no data" even below that. A fixed constant is
 * always wrong somewhere.)
 *
 * How this works: Esri's tile REST endpoint supports a `blankTile=false`
 * query param (see ESRI_HIGH_RES_URL below) that makes it return a real
 * HTTP 404 when a tile doesn't exist, instead of a placeholder "no data"
 * image — this is documented ArcGIS REST API behavior, not a guess. That
 * turns "zoomed past real data" into a normal Leaflet `tileerror` event,
 * which this attaches a handler to: the first time a tile at zoom Z
 * fails, the map's maxZoom is clamped to Z-1 and the view is pulled back
 * if it's currently past that — so the user simply can't zoom further
 * into a spot that has no more real data, instead of landing on a "map
 * data not available" tile.
 */

export const ESRI_HIGH_RES_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}?blankTile=false';
export const ESRI_ATTRIBUTION = 'Esri, Maxar, Earthstar Geographics, and the GIS community';

/**
 * Attaches the dynamic zoom-ceiling guard to a tile layer already added
 * to `map`. Call once per tile layer instance.
 */
export function attachEsriZoomGuard(map, tileLayer) {
  tileLayer.on('tileerror', (err) => {
    const erroredZoom = err.coords?.z;
    if (erroredZoom == null) return;
    const newMax = erroredZoom - 1;
    // Never clamp below a sane floor (a 404 at low zoom is more likely a
    // transient network blip than "no data ever exists here") and never
    // raise the ceiling back up from here — this guard only ever
    // tightens, per location, as real gaps are discovered.
    if (newMax < 4) return;
    if (map.getMaxZoom() <= newMax) return;
    map.setMaxZoom(newMax);
    if (map.getZoom() > newMax) map.setZoom(newMax);
  });
}
