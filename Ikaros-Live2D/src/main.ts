// Load Cubism Core globally before PIXI and pixi-live2d-display
// pixi-live2d-display requires BOTH:
// 1. live2d.min.js (Cubism 2 runtime) - for the plugin itself
// 2. live2dcubismcore.min.js (Cubism 4 runtime) - for Cubism 4 models

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = src
    script.onload = () => {
      console.log(`[main] Loaded: ${src}`)
      resolve()
    }
    script.onerror = (e) => {
      console.error(`[main] Failed to load: ${src}`, e)
      reject(new Error(`Failed to load ${src}`))
    }
    document.head.appendChild(script)
  })
}

async function loadCubismCore(): Promise<void> {
  console.log('[main] Loading Live2D runtimes...')
  await loadScript('/live2d/live2d.js')
  await loadScript('/live2d/live2dcubismcore.js')
  console.log('[main] window.Live2D:', !!(window as any).Live2D)
  console.log('[main] window.Live2DCubismCore:', !!(window as any).Live2DCubismCore)
}

async function bootstrap() {
  console.log('[main] bootstrap start')
  try {
    await loadCubismCore()
    console.log('[main] Live2D loaded')
  } catch (e) {
    // Live2D may fail to load in Tauri's wry custom protocol;
    // the app works without it (WS + monitor + backend features).
    console.warn('[main] Live2D failed to load, continuing without:', e)
  }
  console.log('[main] importing Vue...')
  const { createApp } = await import('vue')
  console.log('[main] importing App.vue...')
  const App = (await import('./App.vue')).default
  console.log('[main] mounting app...')
  createApp(App).mount('#app')
  console.log('[main] app mounted')
}
bootstrap().catch(e => console.error('[main] bootstrap failed:', e))
