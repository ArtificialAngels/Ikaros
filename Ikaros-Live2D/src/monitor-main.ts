/**
 * monitor-main.ts — Standalone entry point for the monitor window.
 * Receives data via Tauri events from the main window.
 */
import { createApp, ref } from 'vue'
import MonitorPanel from './components/MonitorPanel.vue'

// ── Reactive data fed by Tauri events ──
const state = ref('idle')
const sttStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'STT' })
const ttsStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'TTS' })
const llmStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'LLM' })
const live2dStatus = ref<{ status: string; label: string }>({ status: 'active', label: 'Live2D' })
const events = ref<Array<{ time: string; icon: string; text: string }>>([])

async function hideWindow() {
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    await getCurrentWindow().hide()
  } catch { /* ignore */ }
}

async function bootstrap() {
  const app = createApp(MonitorPanel, {
    state,
    sttStatus,
    ttsStatus,
    llmStatus,
    live2dStatus,
    events,
    visible: true,
    onClose: hideWindow,
  })
  app.mount('#app')

  // ── Listen to Tauri events from main window ──
  try {
    const { listen } = await import('@tauri-apps/api/event')

    await listen<{
      state: string
      stt: { status: string; label: string }
      tts: { status: string; label: string }
      llm: { status: string; label: string }
      live2d: { status: string; label: string }
    }>('monitor-status', (event) => {
      const p = event.payload
      state.value = p.state
      sttStatus.value = p.stt
      ttsStatus.value = p.tts
      llmStatus.value = p.llm
      live2dStatus.value = p.live2d
    })

    await listen<{ time: string; icon: string; text: string }>('monitor-event', (event) => {
      events.value.unshift(event.payload)
      if (events.value.length > 50) events.value.pop()
    })

    console.log('[monitor] Event listeners registered')
  } catch (e) {
    console.warn('[monitor] Tauri event listeners failed (browser mode?):', e)
  }
}

bootstrap().catch(e => console.error('[monitor] bootstrap failed:', e))
