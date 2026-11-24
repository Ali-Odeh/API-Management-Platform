/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The app, the control plane and both GoTrue servers share one origin in
// development: Vite forwards these paths, so the browser never makes a
// cross-origin call and no CORS setup is needed. Inside Compose the targets
// are the services' names; run outside Docker, the host ports.
const env = process.env
const apiTarget = env.API_PROXY_TARGET ?? 'http://localhost:8000'
const authTarget = env.AUTH_PROXY_TARGET ?? 'http://localhost:9999'
const portalAuthTarget = env.PORTAL_AUTH_PROXY_TARGET ?? 'http://localhost:9998'
const gatewayTarget = env.GATEWAY_PROXY_TARGET ?? 'http://localhost:8081'

// `/try/<slug>/<path>`: the portal's try-it console calls a workspace's API
// through the gateway, which picks the workspace by Host (`<slug>.localhost`).
// A browser can't set Host, and the gateway answers CORS only for origins a
// route allows, so the dev server makes the call instead.
const TRY_PATH = /^\/try\/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(\/.*)?$/

export function tryTarget(url: string): { slug: string; path: string } | null {
  const match = TRY_PATH.exec(url)
  if (!match?.[1]) return null
  // The path group is optional: `/try/acme` has none.
  return { slug: match[1], path: match[2] ?? '/' }
}

function stripPrefix(prefix: string) {
  return (p: string) => p.slice(prefix.length) || '/'
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  build: {
    rollupOptions: {
      output: {
        // Libraries in their own files: they change less often than the
        // app, so browsers keep them cached across deploys.
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (/[\\/](recharts|d3-[a-z-]+|victory-vendor)[\\/]/.test(id)) return 'charts'
          if (/[\\/](radix-ui|@radix-ui)[\\/]/.test(id)) return 'radix'
          if (/[\\/]@supabase[\\/]/.test(id)) return 'auth'
          if (/[\\/](react|react-dom|react-router|scheduler)[\\/]/.test(id)) return 'react'
          return 'vendor'
        },
      },
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // The browser e2e tests reach the app by its Compose name.
    allowedHosts: ['localhost', 'frontend'],
    // Docker Desktop on Windows passes no file events into containers.
    watch: env.VITE_POLL === 'true' ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
      '/auth/v1': { target: authTarget, changeOrigin: true, rewrite: stripPrefix('/auth/v1') },
      '/try': {
        target: gatewayTarget,
        rewrite: (p) => tryTarget(p)?.path ?? p,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            const target = tryTarget((req as { originalUrl?: string }).originalUrl ?? req.url ?? '')
            if (target) proxyReq.setHeader('host', `${target.slug}.localhost`)
          })
        },
      },
      '/portal-auth/v1': {
        target: portalAuthTarget,
        changeOrigin: true,
        rewrite: stripPrefix('/portal-auth/v1'),
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
    testTimeout: 30_000,
    // Whole-app renders are heavy: half the cores keeps every test well inside its timeouts.
    maxWorkers: '50%',
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/main.tsx',
        'src/api/schema.d.ts',
        'src/api/types.ts',
        'src/components/ui/**',
        'src/test/**',
        'src/**/*.test.{ts,tsx}',
        'src/vite-env.d.ts',
      ],
      thresholds: { lines: 90, statements: 90, functions: 85, branches: 80 },
    },
  },
})
