import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// Backend API the dev server proxies to. Defaults to the port in the deployment and local
// development docs (uvicorn on 8000). Override with AKESIS_API_PROXY_TARGET. This is a
// development-only setting and is never bundled into the browser.
const apiTarget = process.env.AKESIS_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/v1': {
        target: apiTarget,
        changeOrigin: true,
      },
      // Only the API probe paths; the /health page route must stay with the SPA.
      '/health/': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
  },
})
