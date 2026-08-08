import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 注意：Tauri 要求固定端口，避免 dev 时端口漂移导致 WebSocket 重连。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@shared': fileURLToPath(new URL('../../packages/shared-types/src', import.meta.url)),
      'url': fileURLToPath(new URL('./src/shims/url.ts', import.meta.url)),
    },
  },
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
    watch: {
      // 防止 Vite HMR 监视 Rust 编译产物导致 EBUSY 崩溃
      ignored: ['**/src-tauri/**'],
    },
    proxy: {
      // 把 API / 静态资源请求代理到 Python 后端，避免 CORS 跨域问题
      // T-29-A3：monitor 走 bus（8766），比 /api 更长的前缀优先匹配
      '/api/v1/monitor': 'http://100.111.201.71:8766',
      // T-31-A2：fitness（最新 + 历史）走 bus（8766）
      '/api/v1/fitness': 'http://100.111.201.71:8766',
      // 2026-08-08：手机状态 + 轨迹（bus 转发 hub）
      '/api/v1/phone-state': 'http://100.111.201.71:8766',
      '/api/v1/phone-track': 'http://100.111.201.71:8766',
      '/api/v1/phone-notify': 'http://100.111.201.71:8766',
      '/api': 'http://100.111.201.71:8765',
      '/photo': 'http://100.111.201.71:8765', // T34：背景图/头像 dev 走 proxy（photoUrl 相对路径）
      '/health': 'http://100.111.201.71:8765',
      '/memes': 'http://100.111.201.71:8765',
      '/user-memes': 'http://100.111.201.71:8765',
    },
  },
})
