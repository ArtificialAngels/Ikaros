/**
 * monitor-main.ts — Standalone entry point for the monitor window.
 * 服务状态（STT/TTS/LLM/Live2D + state）来自主窗口的 Tauri 事件（实时）；
 * 情感/活力/关系/对话流来自 Rust 命令 read_ikaros_state 定时读取 V5 状态文件与对话日志。
 */
import { createApp, ref } from 'vue'
import MonitorPanel from './components/MonitorPanel.vue'

interface LogEntry { kind?: string; type?: string; ts?: number; text?: string; mood?: string; intensity?: number; session_id?: string }

// ── Reactive data fed by Tauri events (real-time service status) ──
const state = ref('idle')
const activity = ref('')
const sttStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'STT' })
const ttsStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'TTS' })
const llmStatus = ref<{ status: string; label: string }>({ status: 'unknown', label: 'LLM' })
const live2dStatus = ref<{ status: string; label: string }>({ status: 'active', label: 'Live2D' })

// ── Reactive data fed by read_ikaros_state command (V5 files + log) ──
const connected = ref(false)
const affect = ref<{ pleasure?: number; arousal?: number; dominance?: number } | null>(null)
const vitality = ref<{ vitality?: number; conversation_count?: number; total_uptime_sec?: number } | null>(null)
const care = ref<Record<string, unknown> | null>(null)
const relationship = ref<{ depth?: number; warmth?: number; shared_experiences?: number } | null>(null)
const log = ref<LogEntry[]>([])
// V5 self-cognition layer (self_model.json + latest_thought.json)
interface SelfModel {
  curiosity?: { level?: number }
  identity?: { name?: string }
  metacog?: { reflection_count?: number; philosophy_count?: number; philosophy_by_theme?: Record<string, number> }
  memory_self_view?: { total?: number }
  questions?: unknown[]
}
interface LatestThought {
  text?: string
  kind?: string
  theme?: string
  curiosity?: number
  ts?: number
}
const selfModel = ref<SelfModel | null>(null)
const latestThought = ref<LatestThought | null>(null)

async function hideWindow() {
  try {
    const { getCurrentWindow } = await import('@tauri-apps/api/window')
    await getCurrentWindow().hide()
  } catch { /* ignore */ }
}

// Show a visible error overlay on the panel (no devtools needed).
let _errShown = false
function showError(msg: string) {
  console.error(msg)
  if (_errShown) return
  _errShown = true
  const el = document.getElementById('app')
  if (!el) return
  const box = document.createElement('div')
  box.style.cssText = 'position:fixed;inset:0;padding:16px;color:#e0a8a8;background:#181612;font:12px/1.6 monospace;white-space:pre-wrap;overflow:auto;z-index:9999'
  box.textContent = '监控面板错误:\n' + msg
  el.appendChild(box)
}

async function refreshState() {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const data = await invoke<{
      affect: unknown
      vitality: unknown
      care: unknown
      relationship: unknown
      log: LogEntry[]
      selfModel: unknown
      latestThought: unknown
    }>('read_ikaros_state', { count: 80 })
    affect.value = (data.affect && typeof data.affect === 'object') ? data.affect as any : null
    vitality.value = (data.vitality && typeof data.vitality === 'object') ? data.vitality as any : null
    care.value = (data.care && typeof data.care === 'object') ? data.care as any : null
    relationship.value = (data.relationship && typeof data.relationship === 'object') ? data.relationship as any : null
    log.value = Array.isArray(data.log) ? data.log : []
    selfModel.value = (data.selfModel && typeof data.selfModel === 'object') ? data.selfModel as any : null
    latestThought.value = (data.latestThought && typeof data.latestThought === 'object') ? data.latestThought as any : null
    connected.value = true
  } catch (e) {
    connected.value = false
    console.warn('[monitor] read_ikaros_state failed:', e)
    showError('[monitor] read_ikaros_state failed: ' + String(e))
  }
}

async function bootstrap() {
  const app = createApp(MonitorPanel, {
    state,
    activity,
    connected,
    sttStatus,
    ttsStatus,
    llmStatus,
    live2dStatus,
    affect,
    vitality,
    care,
    relationship,
    log,
    selfModel,
    latestThought,
    onClose: hideWindow,
  })
  // Surface any Vue render/runtime error instead of failing silently to a blank panel.
  app.config.errorHandler = (err, _inst, info) => {
    console.error('[monitor] Vue error:', info, err)
    showError('[monitor] Vue 渲染错误: ' + info + ' :: ' + String(err))
  }
  app.mount('#app')

  // ── Listen to Tauri events from main window (real-time) ──
  try {
    const { listen } = await import('@tauri-apps/api/event')

    await listen<{
      state: string
      activity?: string
      stt: { status: string; label: string }
      tts: { status: string; label: string }
      llm: { status: string; label: string }
      live2d: { status: string; label: string }
    }>('monitor-status', (event) => {
      const p = event.payload
      state.value = p.state
      if (p.activity != null) activity.value = p.activity
      sttStatus.value = p.stt
      ttsStatus.value = p.tts
      llmStatus.value = p.llm
      live2dStatus.value = p.live2d
    })

    console.log('[monitor] Event listeners registered')
  } catch (e) {
    showError('[monitor] event listeners failed: ' + String(e))
  }

  // ── Poll V5 state files + conversation log via Rust command ──
  await refreshState()
  setInterval(refreshState, 2500)
}

// Catch any uncaught error / unhandled rejection from the monitor webview.
window.addEventListener('error', (e) => showError('error: ' + (e.message || String(e.error))))
window.addEventListener('unhandledrejection', (e) => showError('unhandledrejection: ' + String((e as PromiseRejectionEvent).reason)))

bootstrap().catch(e => showError('[monitor] bootstrap failed: ' + String(e)))
