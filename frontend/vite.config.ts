import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  base: '/studio/',
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/studio-api': {
        target: 'http://localhost:8003',
        changeOrigin: true,
        timeout: 300000, // 5分钟超时
      },
      '/studio-uploads': {
        target: 'http://localhost:8003',
        changeOrigin: true,
        timeout: 300000,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
  },
})
