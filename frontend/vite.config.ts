import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        pet: 'pet.html',
        petChat: 'pet-chat.html',
        mobile: 'mobile.html',
        ...(process.env.NODE_ENV !== 'production' ? {
          pixelLab: 'pixel-lab.html',
          propLab: 'prop-lab.html',
        } : {}),
      },
    },
  },
})
