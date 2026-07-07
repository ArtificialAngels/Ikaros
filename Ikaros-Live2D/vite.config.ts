import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ['**/src-tauri/**'],
    },
  },
  envPrefix: ['VITE_', 'TAURI_'],
  build: {
    // Keep dist/live2d (Live2D model assets) across rebuilds instead of
    // wiping the whole outDir. Safe for incremental frontend-only builds.
    emptyOutDir: false,
    target: 'esnext',
    minify: !process.env.TAURI_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_DEBUG,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        monitor: resolve(__dirname, 'monitor.html'),
      },
    },
  },
  optimizeDeps: {
    include: ['pixi.js', 'pixi-live2d-display'],
  },
})
