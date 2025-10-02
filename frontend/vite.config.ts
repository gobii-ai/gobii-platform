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
  base: mode === 'development' ? '/' : '/static/',
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
    host: '127.0.0.1',
    port: 5173,
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
}))
