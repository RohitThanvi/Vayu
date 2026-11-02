import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { parseLockedTier } from './src/lib/tierConfig.js'
import { buildSiteSeo } from './src/lib/siteSeo.js'
import { injectSeo } from './src/lib/seo.js'

// SEO for every build, at BUILD time (link-preview crawlers do not run JavaScript):
//  - rewrites <title>/description/theme-color and adds canonical, Open Graph, Twitter and JSON-LD tags
//  - emits robots.txt and sitemap.xml (the site has one indexable URL, "/")
// The site URL comes from VITE_SITE_URL (use it for a custom domain) or, on Vercel, from
// VERCEL_PROJECT_PRODUCTION_URL. With neither, absolute-URL features are skipped and a warning is printed
// (the main site, unlocked, falls back to its known address). Single-product sites (VITE_LOCKED_TIER) get
// their own title, description, image and site name from lib/tierLandingConfigs.js.
function seoPlugin(mode) {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }
  const locked = parseLockedTier(env.VITE_LOCKED_TIER)
  const built = buildSiteSeo({ locked, env })
  return {
    name: 'vayu-seo',
    transformIndexHtml(html) { return injectSeo(html, built.seo) },
    generateBundle() {
      if (!built.siteUrl) this.warn('SEO: no site URL (set VITE_SITE_URL, or enable Vercel system env vars). canonical/og:url/og:image and sitemap.xml are skipped.')
      this.emitFile({ type: 'asset', fileName: 'robots.txt', source: built.robots })
      if (built.sitemap) this.emitFile({ type: 'asset', fileName: 'sitemap.xml', source: built.sitemap })
    },
  }
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), seoPlugin(mode)],
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
