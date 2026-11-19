/// <reference types="vitest/config" />
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// In Docker the edge Caddy serves the app and routes /api/* to api:8000 on the same origin.
// Outside Docker, `pnpm dev` proxies /api to a locally running backend instead.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

// Host names the dev server answers to. The dev compose override runs
// `pnpm dev --host 0.0.0.0 --port 5173` behind the edge Caddy (WEB_UPSTREAM=web:5173),
// which forwards APP_HOST. The bind address stays a CLI flag, so a bare `pnpm dev`
// outside Docker only listens on localhost.
const allowedHosts = ['localhost', '127.0.0.1', 'web']
if (process.env.APP_HOST) allowedHosts.push(process.env.APP_HOST)

// In the dev container port 5173 is not published: the browser reaches Vite only through the
// edge Caddy at https://<APP_HOST>:443, so the HMR client must connect back on 443.
// A bare `pnpm dev` on the host keeps Vite's default (same port as the page).
// VITE_HMR_CLIENT_PORT overrides either case.
const inContainer = existsSync('/.dockerenv') || existsSync('/run/.containerenv')
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT
  ? Number.parseInt(process.env.VITE_HMR_CLIENT_PORT, 10)
  : inContainer
    ? 443
    : undefined

// Dep-optimizer cache. Vite defaults to node_modules/.vite, but in the dev container
// node_modules is an anonymous volume that compose carries over when the container is
// recreated, while `pnpm install` on start may relink packages. The carried-over
// _metadata.json then disagrees with the browser's hashes and Vite answers
// `504 Outdated Optimize Dep`. /tmp is the container's own writable layer: every new container
// starts empty and optimizes once from the deps it actually installed. A plain restart keeps the
// cache, which is fine because the deps did not change. On the host the default stays.
const cacheDir = inContainer ? '/tmp/vite-cache' : undefined

// Never inline fonts as data: URIs: the prod CSP is `font-src 'self'`, so an inlined
// @font-face src would be blocked. Other small assets keep Vite's default 4 KiB rule.
const FONT_FILE = /\.(woff2?|ttf|otf|eot)(\?.*)?$/i

// Dev-only: serve MSW's worker script for `VITE_MOCK_API=1 pnpm dev`. It is deliberately not in
// public/, so `vite build` never copies it into dist/ (prod ships no mock code at all).
const MSW_WORKER = fileURLToPath(new URL('./node_modules/msw/lib/mockServiceWorker.js', import.meta.url))
function mockServiceWorker(): Plugin {
  return {
    name: 'holdfast-msw-dev-worker',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use('/mockServiceWorker.js', (_req, res) => {
        res.setHeader('Content-Type', 'text/javascript')
        res.setHeader('Service-Worker-Allowed', '/')
        res.end(readFileSync(MSW_WORKER))
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), mockServiceWorker()],
  cacheDir,
  build: {
    assetsInlineLimit: (filePath) => (FONT_FILE.test(filePath) ? false : undefined),
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    allowedHosts,
    hmr: hmrClientPort === undefined ? true : { clientPort: hmrClientPort },
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  preview: {
    port: 4173,
    strictPort: true,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
})
