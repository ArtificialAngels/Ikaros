/**
 * useLipSync — sinusoidal lip sync animation for speaking state.
 */
import { ref, onUnmounted, type Ref } from 'vue'
import type { Live2DAdapter } from '../services/live2d-adapter'

export function useLipSync(adapter: Ref<Live2DAdapter | null>) {
  const isActive = ref(false)
  const sensitivity = ref(1.0)
  let animFrameId: number | null = null
  let startTime = 0

  function start() {
    if (isActive.value) return
    isActive.value = true
    startTime = performance.now()
    loop()
  }

  function stop() {
    isActive.value = false
    if (animFrameId !== null) {
      cancelAnimationFrame(animFrameId)
      animFrameId = null
    }
    // Reset mouth to closed
    if (adapter.value) {
      adapter.value.setParam('ParamMouthOpenY', 0)
    }
  }

  function loop() {
    if (!isActive.value) return
    if (adapter.value) {
      const elapsed = (performance.now() - startTime) / 1000
      // Sinusoidal oscillation [0.1, 0.8] at ~6Hz
      const mouthOpen = 0.1 + 0.35 * (1 + Math.sin(elapsed * 6 * Math.PI * 2)) * sensitivity.value
      adapter.value.setParam('ParamMouthOpenY', Math.min(1, mouthOpen))
    }
    animFrameId = requestAnimationFrame(loop)
  }

  onUnmounted(() => {
    stop()
  })

  return { isActive, sensitivity, start, stop }
}
