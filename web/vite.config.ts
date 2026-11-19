/// <reference types="vitest/config" />
import { existsSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

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

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
