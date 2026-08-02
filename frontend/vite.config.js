import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Vite config.
 *
 * The dev server proxies /api to the FastAPI backend. That means the browser
 * only ever talks to one origin, so CORS is a non-issue in development and
 * relative image URLs returned by the API (`/api/bills/{id}/image`) just work.
 * CORS is still configured on the backend for anyone who prefers to point the
 * frontend at a remote API via VITE_API_BASE_URL.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
