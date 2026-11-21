// CesiumJS is loaded on demand from jsDelivr at an exact pinned version, so users who never open the 3D view download nothing and the Android app does not grow by ~20 MB of Cesium assets.
// (No SRI hash: it could not be checked from the dev sandbox against what jsDelivr serves. Add one after fetching the file yourself.)
export const CESIUM_VERSION = '1.147.0';
export const CESIUM_BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VERSION}/Build/Cesium/`;
export function loadCesium({ doc = document, win = window } = {}) {
  if (win.Cesium) return Promise.resolve(win.Cesium);
  if (win.__vayuCesiumLoading) return win.__vayuCesiumLoading;                          // one load per window, shared by concurrent callers
  const clear = () => { win.__vayuCesiumLoading = null; };
  win.__vayuCesiumLoading = new Promise((resolve, reject) => {
    win.CESIUM_BASE_URL = CESIUM_BASE;                                                   // workers and assets resolve from here
    const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = `${CESIUM_BASE}Widgets/widgets.css`; doc.head.appendChild(css);
    const s = doc.createElement('script'); s.src = `${CESIUM_BASE}Cesium.js`; s.async = true;
    s.onload = () => (win.Cesium ? resolve(win.Cesium) : (clear(), reject(new Error('Cesium loaded but did not define window.Cesium'))));
    s.onerror = () => { clear(); s.remove(); css.remove(); reject(new Error('Could not load the 3D engine (CesiumJS). Check your connection.')); };
    doc.head.appendChild(s);
  });
  return win.__vayuCesiumLoading;
}
