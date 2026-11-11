// vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 loopback explicitly. Vite's default host is the NAME `localhost`, which on
    // macOS resolves to ::1 first, so the dev server ends up listening on [::1]:5173 ONLY --
    // measured: `curl 127.0.0.1:5173` was refused while `curl [::1]:5173` answered 200. A
    // browser that picks the IPv4 address for `localhost` then gets a blank page, and the
    // failure looks like the app being broken rather than like a bind-address mismatch.
    // 127.0.0.1 keeps the server off the LAN (unlike `host: true`, which binds 0.0.0.0 too)
    // and every mainstream browser still reaches it as `localhost`.
    host: '127.0.0.1',
    proxy: {
      // 1. 转发普通地图业务到 8000（路径原样转发，FastAPI 路由含 /api/main/ 前缀）
      // 127.0.0.1, not the name `localhost`: uvicorn binds 127.0.0.1, so resolving the name
      // here is the same ::1-first gamble as above, decided inside Node instead of the
      // browser. Naming the address makes the two ends agree by construction.
      '/api/main': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
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