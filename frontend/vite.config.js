import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { parseLockedTier } from './src/lib/tierConfig.js'
import { getTierLanding } from './src/lib/tierLandingConfigs.js'

const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

// Single-product (locked-tier) builds get their own <title>, description, theme colour and
// share-preview tags at BUILD time, because link-preview crawlers do not run JavaScript.
// With VITE_LOCKED_TIER unset (the main site) index.html is left untouched.
function lockedTierHtml(mode) {
  const locked = parseLockedTier(loadEnv(mode, process.cwd(), 'VITE_').VITE_LOCKED_TIER || process.env.VITE_LOCKED_TIER)
  const meta = locked ? getTierLanding(locked)?.meta : null
  return {
    name: 'vayu-locked-tier-html',
    transformIndexHtml(html) {
      if (!meta) return html
      return html
        .replace(/<title>[^<]*<\/title>/, `<title>${esc(meta.title)}</title>`)
        .replace(/(<meta name="description" content=")[^"]*(")/, `$1${esc(meta.description)}$2`)
        .replace(/(<meta name="theme-color" content=")[^"]*(")/, `$1${esc(meta.themeColor)}$2`)
        .replace('</title>', `</title>\n    <meta property="og:title" content="${esc(meta.title)}" />\n    <meta property="og:description" content="${esc(meta.description)}" />\n    <meta property="og:type" content="website" />`)
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), lockedTierHtml(mode)],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: { output: { manualChunks: { react: ['react','react-dom'] } } },
  },
}))
