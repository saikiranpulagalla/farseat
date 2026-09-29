import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.FARSEAT_DEV_API ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
    },
  },
});
