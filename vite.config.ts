// Save this directly into your file: vite.config.ts

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
        '@root': path.resolve(__dirname, '.'),
      },
    },
    build: {
      outDir: path.resolve(__dirname, 'static/dist'),
      emptyOutDir: true,
      manifest: true,
      rollupOptions: {
        output: {
          manualChunks(id) {
            const normalizedId = id.replaceAll('\\', '/');
            if (
              normalizedId.includes('/node_modules/react/') ||
              normalizedId.includes('/node_modules/react-dom/')
            ) {
              return 'vendor';
            }
          },
        },
      },
    },
    server: {
      port: 5173,
      hmr: process.env.DISABLE_HMR !== 'true',
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
          secure: false,
        },
        '/payment': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
          secure: false,
        },
        '/healthz': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
        },
      },
    },
  };
});