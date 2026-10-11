import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command, mode }) => {
  // Without an API address a deployed dashboard would call each visitor's own machine, so the build stops here.
  if (command === 'build' && !loadEnv(mode, process.cwd(), 'VITE_').VITE_API_URL) {
    throw new Error('VITE_API_URL is not set. Set it to the API address before building (see frontend/.env.example).')
  }
  return {
  plugins: [react()],
  server: { port: 5173 },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/recharts')) {
            return 'charts'
          }
          if (id.includes('node_modules/lucide-react')) {
            return 'icons'
          }
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) {
            return 'vendor'
          }
        },
      },
    },
  },
}
})
