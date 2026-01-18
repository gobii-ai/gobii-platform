import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRootDir = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ mode }) => ({
  plugins: [
    react({
      babel: {
        plugins: ['babel-plugin-react-compiler'],
      },
    }),
  ],
  base: mode === 'development' ? '/' : '/static/frontend/',
  resolve: {
    alias: {
      '@': resolve(projectRootDir, './src'),
    },
  },
  build: {
    outDir: '../static/frontend',
    emptyOutDir: true,
    manifest: 'manifest.json',
    rollupOptions: {
      input: {
        main: resolve(projectRootDir, 'src/main.tsx'),
      },
    },
  },
  server: {
    // Use VITE_HOST to bind to all interfaces for mobile/remote testing
    host: process.env.VITE_HOST || '127.0.0.1',
    port: 5173,
    cors: true,
    // When accessed via external IP, Vite needs to know its public origin
    origin: process.env.VITE_ORIGIN,
    hmr: process.env.VITE_HMR_HOST
      ? { host: process.env.VITE_HMR_HOST }
      : true,
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
}))
