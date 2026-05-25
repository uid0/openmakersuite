/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig, type UserConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';

const config: UserConfig & { test?: unknown } = {
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'apple-touch-icon.png'],
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
        // The SPA shell (`/index.html`) is the navigation fallback for any
        // unmatched route the SW intercepts. Without an explicit denylist,
        // workbox's NavigationRoute hands the cached shell back for every
        // navigation, including Django admin and DRF endpoints, which then
        // render blank because React-Router has no route for them. The
        // denylist makes the SW pass those requests straight through to the
        // network (→ nginx → Django).
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/admin(\/|$)/,
          /^\/auth\//,
          /^\/webhooks\//,
          /^\/flower\//,
          /^\/mqttadmin\//,
          /^\/django-static\//,
          /^\/media\//,
          /^\/\.well-known\//,
          // Anything that looks like a file (has an extension other than
          // the SPA-route style of `/foo.bar` in code) — let workbox serve
          // it from precache or fetch it.
          /\.[A-Za-z0-9]{1,8}$/,
        ],
      },
      manifest: false,
    }),
  ],
  server: {
    port: 3000,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'build',
    sourcemap: true,
    chunkSizeWarningLimit: 1500,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    environmentOptions: {
      jsdom: {
        url: 'http://localhost:3000',
        pretendToBeVisual: true,
      },
    },
    setupFiles: ['./src/setupTests.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules', 'build', 'e2e/**'],
    css: false,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'clover', 'cobertura'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.d.ts',
        'src/index.tsx',
        'src/reportWebVitals.ts',
        'src/types/**',
      ],
      thresholds: {
        branches: 35,
        functions: 33,
        lines: 40,
        statements: 39,
      },
    },
  },
};

export default defineConfig(config);
