import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  // shadcn-vue 组件（src/components/ui）按 components.json 的约定用 @/ 引用 src
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    proxy: Object.fromEntries(['/api', '/healthz', '/readyz'].map((path) => [path, { target: 'http://localhost:8000' }])),
  },
  test: { environment: 'jsdom', setupFiles: ['./tests/setup.ts'], restoreMocks: true },
})
