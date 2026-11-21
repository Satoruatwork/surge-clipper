import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const engine = 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': engine,
      '/media': engine,
      '/ws': { target: engine, ws: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
})
