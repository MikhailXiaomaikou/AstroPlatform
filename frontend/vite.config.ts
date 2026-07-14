/// <reference types="vitest" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiUrl = String(process.env.VITE_API_URL || env.VITE_API_URL || '').trim()
  if (command === 'build' && !apiUrl) {
    throw new Error('VITE_API_URL is required for a production frontend build')
  }

  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            plotly: ['plotly.js-dist-min'],
          },
        },
      },
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/__tests__/setup.ts'],
      // Playwright specs live under ./playwright/ and use @playwright/test
      // (a separately-installed package, not in CI). They are NOT vitest
      // tests — excluding the folder prevents vitest from collecting them
      // and failing on the missing import resolution.
      exclude: ['playwright/**', 'node_modules/**', 'dist/**'],
    },
  }
})
