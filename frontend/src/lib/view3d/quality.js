// Quality tiers and small pure helpers for the 3D view.
export const QUALITY = {
  low:      { label: 'Low',      resolutionScale: 0.75, maximumScreenSpaceError: 4,   msaaSamples: 1, buildingRadiusM: 700,  maxBuildings: 2500,  elevationCache: 128 },
  balanced: { label: 'Balanced', resolutionScale: 1,    maximumScreenSpaceError: 2.5, msaaSamples: 1, buildingRadiusM: 1200, maxBuildings: 6000,  elevationCache: 256 },
  high:     { label: 'High',     resolutionScale: 1,    maximumScreenSpaceError: 1.5, msaaSamples: 4, buildingRadiusM: 2000, maxBuildings: 14000, elevationCache: 384 },
};
/** Conservative default: phones and low-memory or few-core devices start on Low. */
export function detectQuality({ deviceMemory, hardwareConcurrency, mobile } = {}) {
  if (mobile || (deviceMemory && deviceMemory <= 4) || (hardwareConcurrency && hardwareConcurrency <= 4)) return 'low';
  return (deviceMemory && deviceMemory >= 8 && hardwareConcurrency >= 8) ? 'high' : 'balanced';
}
/** UTC instant at which the sun is at the given local SOLAR hour (0-24) at this longitude, on the given date (approximate: ignores the equation of time). */
export function utcForSolarHour(hour, lon, base = new Date()) {
  const d = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate())); d.setTime(d.getTime() + (hour - lon / 15) * 3600e3); return d;
}
/** Is the 3D view switched on at build time? Off unless VITE_ENABLE_3D_VIEW is exactly "1". */
export const view3dEnabled = (env = {}) => String(env.VITE_ENABLE_3D_VIEW || '') === '1';
