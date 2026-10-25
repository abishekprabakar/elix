import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// ELIX_API_TARGET lets the dev and preview servers point at an API on another port.
const target = process.env.ELIX_API_TARGET ?? 'http://127.0.0.1:8400';
const proxy = {
  '/api': { target, changeOrigin: false },
  '/healthz': { target, changeOrigin: false },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { proxy },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
});
