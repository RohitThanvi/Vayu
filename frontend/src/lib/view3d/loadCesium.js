// CesiumJS is loaded on demand from jsDelivr at an exact pinned version, so users who never open the 3D view download nothing and the Android app does not grow by ~20 MB of Cesium assets.
// Subresource Integrity: hashes of Build/Cesium/Cesium.js and Widgets/widgets.css from the npm tarball of THIS version (jsDelivr serves those files byte-for-byte). They could not be compared with jsDelivr's live
// response from the dev sandbox. If the browser console ever reports an integrity error, check with: curl -s <url> | openssl dgst -sha384 -binary | openssl base64 -A . When bumping CESIUM_VERSION, regenerate both hashes.
export const CESIUM_VERSION = '1.147.0';
export const CESIUM_JS_SRI = 'sha384-MsL9BLNAnIOaKqfaH6xNODs0FmBa4Ol8wwzL5MJ+w3w+63xjOHH0MD8x1uZoKlZg';
export const CESIUM_CSS_SRI = 'sha384-ghEeMdcWWzRv/BPeUcX835vcKDGrxvROXisl/Btpv3GeekBUXTSPVcFJpI1Tcrgp';
export const CESIUM_BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VERSION}/Build/Cesium/`;
export function loadCesium({ doc = document, win = window } = {}) {
  if (win.Cesium) return Promise.resolve(win.Cesium);
  if (win.__vayuCesiumLoading) return win.__vayuCesiumLoading;                          // one load per window, shared by concurrent callers
  const clear = () => { win.__vayuCesiumLoading = null; };
  win.__vayuCesiumLoading = new Promise((resolve, reject) => {
    win.CESIUM_BASE_URL = CESIUM_BASE;                                                   // workers and assets resolve from here
    const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = `${CESIUM_BASE}Widgets/widgets.css`; css.integrity = CESIUM_CSS_SRI; css.crossOrigin = 'anonymous'; doc.head.appendChild(css);
    const s = doc.createElement('script'); s.src = `${CESIUM_BASE}Cesium.js`; s.integrity = CESIUM_JS_SRI; s.crossOrigin = 'anonymous'; s.async = true;
    s.onload = () => (win.Cesium ? resolve(win.Cesium) : (clear(), reject(new Error('Cesium loaded but did not define window.Cesium'))));
    s.onerror = () => { clear(); s.remove(); css.remove(); reject(new Error('Could not load the 3D engine (CesiumJS). Check your connection.')); };
    doc.head.appendChild(s);
  });
  return win.__vayuCesiumLoading;
}
