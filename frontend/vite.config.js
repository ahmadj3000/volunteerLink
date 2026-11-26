import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/ngo': {
        target: 'https://ozb9ner0t1.execute-api.us-east-1.amazonaws.com',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/ngo/, ''),
      },
      '/api/volunteer': {
        target: 'https://57bc1lggo1.execute-api.us-east-1.amazonaws.com',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/volunteer/, ''),
      },
    },
  },
})
