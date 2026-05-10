/// <reference types="vitest/config" />
import { readFile } from 'node:fs/promises'
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const devProxyTarget = process.env.BIMEMO_DEV_PROXY_TARGET || 'http://localhost:8000'
const FRONTEND_ALLOWED_HOSTS = ['.mesh.lan']
const FRONTEND_APP_ROUTES = new Set([
  '/',
  '/admin',
  '/admin/integrations',
  '/admin/backups',
  '/admin/summaries',
  '/admin/llm',
  '/admin/profile',
  '/admin/memory-debug',
])

function frontendRouteFallbackPlugin() {
  return {
    name: 'bimemo-frontend-route-fallback',
    configureServer(server: import('vite').ViteDevServer) {
      server.middlewares.use(async (req, res, next) => {
        const method = req.method || 'GET'
        if (method !== 'GET' && method !== 'HEAD') {
          next()
          return
        }

        const rawUrl = req.url || '/'
        const pathname = rawUrl.split('?')[0]
        if (!FRONTEND_APP_ROUTES.has(pathname)) {
          next()
          return
        }

        const acceptHeader = req.headers.accept || ''
        const fetchDestination = req.headers['sec-fetch-dest'] || ''
        const isDocumentRequest =
          fetchDestination === 'document' ||
          acceptHeader.includes('text/html')

        if (!isDocumentRequest) {
          next()
          return
        }

        const indexHtmlPath = path.resolve(__dirname, 'index.html')
        const template = await readFile(indexHtmlPath, 'utf-8')
        const html = await server.transformIndexHtml(pathname, template)
        res.statusCode = 200
        res.setHeader('Content-Type', 'text/html')
        res.end(html)
      })
    },
  }
}
// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), frontendRouteFallbackPlugin()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: FRONTEND_ALLOWED_HOSTS,
    watch: {
      usePolling: true,
      interval: 200,
    },
    hmr: {
      // The page is served over HTTPS through Caddy on the host's
      // 443; the browser refuses plain ws from an https page, so
      // route the HMR socket through the same Caddy proxy.
      protocol: 'wss',
      host: 'localhost',
      clientPort: 443,
    },
    proxy: {
      '/health': devProxyTarget,
      '/auth': devProxyTarget,
      '/setup': devProxyTarget,
      '/unlock': devProxyTarget,
      '/refresh': devProxyTarget,
      '/logout': devProxyTarget,
      '/csrf-token': devProxyTarget,
      '/chat': devProxyTarget,
      '/stop': devProxyTarget,
      '/confirm': devProxyTarget,
      '/stream': devProxyTarget,
      '/ws': {
        target: devProxyTarget,
        ws: true,
      },
      '/files': devProxyTarget,
      '/admin': devProxyTarget,
      '/security': devProxyTarget,
      '/tts': devProxyTarget,
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    include: ['tests/**/*.test.{ts,tsx}'],
  },
})
