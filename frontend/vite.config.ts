/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
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
})
