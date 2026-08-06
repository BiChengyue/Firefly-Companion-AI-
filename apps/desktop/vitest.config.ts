import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// vitest 配置 — 与 vite.config.ts 保持同一套 alias（@ / @shared / url shim），
// 独立成文件以免改动运行时构建配置。environment 用 node + 测试内 stub 浏览器全局
// （WebSocket / window / localStorage），不引入 jsdom 依赖。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@shared': fileURLToPath(new URL('../../packages/shared-types/src', import.meta.url)),
      'url': fileURLToPath(new URL('./src/shims/url.ts', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
