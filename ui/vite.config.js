import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3420,
    proxy: {
      '/prune': 'http://localhost:8420',
      '/index': 'http://localhost:8420',
      '/search': 'http://localhost:8420',
      '/skeleton': 'http://localhost:8420',
      '/stats': 'http://localhost:8420',
      '/config': 'http://localhost:8420',
      '/ws': { target: 'ws://localhost:8420', ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
