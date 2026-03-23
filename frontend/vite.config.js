// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 1. 转发普通地图业务到 8000
      '/api/main': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/main/, '/api') // 把 /api/main 转回后端的 /api
      },
      // 2. 转发 AI 分析到 Vercel AI 服务
      '/api/ai': {
        target: 'https://mapscale-ai-service.vercel.app',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/api\/ai/, '/api') // 把 /api/ai 转回后端的 /api
      }
    }
  }
})