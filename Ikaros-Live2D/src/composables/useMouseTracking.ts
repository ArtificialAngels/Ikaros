/**
 * useMouseTracking — JS-side polling with single-Rust-IPC per cycle.
 *
 * Calls `poll_input_state` every 20ms.  One IPC call returns:
 * { ctrl_held, track_x, track_y, in_window }
 *
 * Click-through auto-management (corner, Ctrl) runs inside
 * the Rust command — zero extra IPC overhead.
 */
import { ref, onUnmounted, type Ref } from 'vue'
import type { Live2DAdapter } from '../services/live2d-adapter'

let _invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null

async function getInvoke() {
  if (!_invoke) {
    const mod = await import('@tauri-apps/api/core')
    _invoke = mod.invoke
  }
  return _invoke
}

type PollResult = {
  ctrl_held: boolean
  track_x: number
  track_y: number
  in_window: boolean
}

export function useMouseTracking(
  adapter: Ref<Live2DAdapter | null>,
  _containerRef: Ref<HTMLElement | undefined>,
) {
  const trackX = ref(0)
  const trackY = ref(0)
  const sensitivity = ref(1.0)
  const ctrlHeld = ref(false)

  let intervalId: ReturnType<typeof setInterval> | null = null

  async function poll() {
    try {
      const invoke = await getInvoke()
      if (!invoke) return
      const result = await invoke('poll_input_state') as PollResult

      ctrlHeld.value = result.ctrl_held

      if (adapter.value) {
        trackX.value = result.track_x
        trackY.value = result.track_y
        adapter.value.updateTracking(result.track_x, result.track_y)
      }
    } catch {
      // Tauri API not available
    }
  }

  function startPolling() {
    if (intervalId !== null) return
    poll() // immediate first call
    intervalId = setInterval(poll, 20)
  }

  function stopPolling() {
    if (intervalId !== null) {
      clearInterval(intervalId)
      intervalId = null
    }
  }

  async function forceSetClickThrough(ignore: boolean) {
    try {
      const invoke = await getInvoke()
      if (invoke) {
        await invoke('force_click_through', { ignore })
      }
    } catch { /* ignore */ }
  }

  async function setBallMode(active: boolean) {
    try {
      const invoke = await getInvoke()
      if (invoke) {
        await invoke('set_ball_mode', { active })
      }
    } catch { /* ignore */ }
  }

  async function setManualOverride(active: boolean) {
    try {
      const invoke = await getInvoke()
      if (invoke) {
        await invoke('set_manual_override', { active })
      }
    } catch { /* ignore */ }
  }

  startPolling()

  onUnmounted(() => {
    stopPolling()
  })

  return {
    trackX,
    trackY,
    sensitivity,
    ctrlHeld,
    forceSetClickThrough,
    setBallMode,
    setManualOverride,
  }
}
