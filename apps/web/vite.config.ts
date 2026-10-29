import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(['/api', '/healthz', '/readyz'].map((path) => [path, { target: 'http://localhost:8000' }])),
  },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], restoreMocks: true },
})
