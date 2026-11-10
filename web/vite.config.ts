import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { '@': path.resolve(import.meta.dirname, './src') } },
  // dev only: forward API calls to `gdpr-rag serve`
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
