<template>
  <div class="pet-container" ref="containerRef"
    :class="{ 'ball-mode': floatBallMode }"
    @pointerdown="onPointerDown"
    @pointerup="onPointerUp"
    @mousemove="onMouseMove"
    @mouseleave="onMouseLeave"
    @dblclick="onDoubleClick">

    <!-- ====== Normal pet UI (hidden in float ball mode) ====== -->
    <template v-if="!floatBallMode">
      <!-- Drag handle (thin top bar) -->
      <div class="drag-handle"></div>

      <!-- Live2D rendering -->
      <Live2DCanvas ref="live2dRef" />

      <!-- Status bar -->
      <StatusBar :emoji="statusEmoji" :text="statusText" :activity="activityText" />

      <!-- Mode toggle + FloatBall + Monitor buttons -->
      <div class="bottom-buttons">
        <div class="mode-toggle" :class="{ active: !clickThrough }" @click.stop="toggleMode" :title="clickThrough ? '点击交互 (或按Ctrl)' : '点击穿透 (或松开Ctrl)'">
          {{ clickThrough ? '👁' : '👆' }}
        </div>
        <div class="mode-toggle" @click.stop="toggleFloatBall" :class="{ active: floatBallMode }" title="切换悬浮球">
          🔮
        </div>
        <div class="mode-toggle" @click.stop="toggleMic" :class="{ active: micActive }" title="麦克风聆听">
          🎤
        </div>
      </div>
      <!-- Monitor panel now in independent window (tray → 📊 监控面板) -->
      <!-- Settings panel -->
      <SettingsPanel
        :visible="settingsVisible"
        :config="petConfig"
        @close="settingsVisible = false"
        @update:config="onConfigUpdate"
      />

      <!-- Chat Dock (double-click to toggle) -->
      <ChatDock
        :visible="chatDockVisible"
        @close="chatDockVisible = false"
        @reply="onChatReply"
      />
    </template>

    <!-- ====== Speech bubble (visible in both modes) ====== -->
    <transition name="bubble">
      <div v-if="bubbleText" class="speech-bubble" :class="{ 'ball-bubble': floatBallMode }">{{ bubbleText }}</div>
    </transition>

    <!-- ====== FloatBall mode ====== -->
    <transition name="ball-fade">
      <FloatBall
        v-if="floatBallMode"
        @restore="toggleFloatBall"
        @context-menu="onFloatBallContext"
      />
    </transition>
  </div>
</template>

<script setup lang="ts">
/**
 * App.vue — Ikaros Desktop Pet main container.
 * Ctrl toggles click-through: hold Ctrl = interact, release = pass through.
 * Toggle button also switches mode.
 */
import { ref, computed, onMounted, onUnmounted, watch, type Ref } from 'vue'
import Live2DCanvas from './components/Live2DCanvas.vue'
import StatusBar from './components/StatusBar.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ChatDock from './components/ChatDock.vue'
import FloatBall from './components/FloatBall.vue'
import { useMouseTracking } from './composables/useMouseTracking'
import { useStateReactions } from './composables/useStateReactions'
import { useLipSync } from './composables/useLipSync'
import { useSpeechInput } from './composables/useSpeechInput'
import {
  selectedMic, selectedSpeaker, setSelectedMic, setSelectedSpeaker,
  labelForMic, labelForSpeaker, enumerateAudioDevices, acquireMediaPermission,
} from './composables/useAudioDevices'
import { loadConfig, saveConfig, type PetConfig } from './services/pet-config'
import { NeuroService, type NeuroStatus } from './services/neuro'
import { llmManager } from './services/llm-manager'
import { ContextEngine, getCategoryInfo, type WindowContext } from './services/context-engine'
import { EmotionSystem, type Emotion } from './services/emotion-system'
import { VlmService } from './services/vlm-service'

// ── Tauri imports (static — Vite tree-shakes in browser, works in Tauri) ──
let tauriInvoke: any = null
let tauriWindow: any = null
let tauriListen: any = null

async function ensureTauri() {
  if (!tauriInvoke) {
    try {
      const core = await import('@tauri-apps/api/core')
      tauriInvoke = core.invoke
    } catch { /* not in Tauri */ }
  }
  if (!tauriWindow) {
    try {
      const win = await import('@tauri-apps/api/window')
      tauriWindow = win
    } catch { /* not in Tauri */ }
  }
  if (!tauriListen) {
    try {
      const evt = await import('@tauri-apps/api/event')
      tauriListen = evt.listen
    } catch { /* not in Tauri */ }
  }
}

// ─── State ───
const STATES: Record<string, { emoji: string; text: string }> = {
  idle:      { emoji: '🪽', text: '待机' },
  listening: { emoji: '🎤', text: '聆听' },
  thinking:  { emoji: '🧠', text: '思考' },
  speaking:  { emoji: '🔊', text: '说话' },
  happy:     { emoji: '😊', text: '开心' },
  sleepy:    { emoji: '💤', text: '休息' },
}

const state = ref('idle')
const bubbleText = ref('')
const containerRef = ref<HTMLElement>()
const live2dRef = ref<InstanceType<typeof Live2DCanvas> | null>(null)

const statusEmoji = computed(() => STATES[state.value]?.emoji || '🪽')
const contextOverride = ref('')
const statusText = computed(() => contextOverride.value || STATES[state.value]?.text || '待机')

// 当前前台窗口/程序名 (voice-ws 推 {type:activity}, 由本地监测采集)
const activityText = ref('')

// ─── Config ───
const petConfig = ref<PetConfig>(loadConfig())

// ─── Float Ball Mode ───
const floatBallMode = ref(false)

// ─── Manual interactive override (toggled by the 👁/👆 button) ───
const manualInteractive = ref(false)

// ─── Mouse Tracking (event-driven, Rust polls internally) ───
const { trackX, trackY, ctrlHeld, forceSetClickThrough, setBallMode, setManualOverride } = useMouseTracking(
  computed(() => live2dRef.value?.adapter ?? null),
  containerRef as Ref<HTMLElement | undefined>,
)

// ─── State Reactions ───
useStateReactions(state, computed(() => live2dRef.value?.adapter ?? null), containerRef as any)

// ─── Lip Sync ───
const { start: startLipSync, stop: stopLipSync } = useLipSync(
  computed(() => live2dRef.value?.adapter ?? null)
)

watch(state, (newState) => {
  if (newState === 'speaking' && petConfig.value.lipSyncEnabled) {
    startLipSync()
  } else {
    stopLipSync()
  }
})

// ─── Click-through Mode ───
// Ctrl held or manualInteractive = interactive mode; otherwise pass-through.
const clickThrough = computed(() => !ctrlHeld.value && !manualInteractive.value)

function toggleMode() {
  // Toggle manual interactive override (independent of Ctrl key)
  manualInteractive.value = !manualInteractive.value
  setManualOverride(manualInteractive.value)
  if (manualInteractive.value) {
    // Force interactive mode — Rust will skip auto-management
    forceSetClickThrough(false)
    showBubble('👆 交互模式', 2000)
  } else {
    // Return to Ctrl-based pass-through mode
    forceSetClickThrough(true)
    showBubble('👁 穿透模式', 2000)
  }
}

// ─── Drag / Click ───
let downX = 0, downY = 0, downTime = 0

async function onPointerDown(e: PointerEvent) {
  // In float ball mode, let FloatBall component handle its own drag
  if (floatBallMode.value) return

  downX = e.screenX
  downY = e.screenY
  downTime = Date.now()

  // Drag can be triggered by Ctrl+drag or in manual interactive mode
}

function onPointerUp(e: PointerEvent) {
  const elapsed = Date.now() - downTime
  const dx = Math.abs(e.screenX - downX)
  const dy = Math.abs(e.screenY - downY)
  downTime = 0
  dragStarted = false

  if (elapsed < 300 && dx < 8 && dy < 8) {
    // Click — hit test on model
    if (live2dRef.value) {
      const areas = live2dRef.value.hitTest(e.clientX, e.clientY)
      if (areas.length > 0) {
        live2dRef.value.playMotion('Tap', Math.floor(Math.random() * 2))
      }
    }
  }
}

// ─── Mouse Tracking Events ───
// Ctrl-based click-through is managed by useMouseTracking's polling loop
// (Rust GetAsyncKeyState) — no DOM keyboard events needed.

let dragStarted = false

async function onMouseMove(e: MouseEvent) {
  // Drag: Ctrl+drag always works; in manual interactive mode, any drag works
  if ((e.ctrlKey || manualInteractive.value) && downTime > 0 && !dragStarted) {
    const dx = Math.abs(e.screenX - downX)
    const dy = Math.abs(e.screenY - downY)
    if (dx > 5 || dy > 5) {
      dragStarted = true
      if (tauriInvoke) {
        try {
          await tauriInvoke('drag_window')
        } catch { /* ignore */ }
      }
    }
  }
}

function onMouseLeave() {
  dragStarted = false
}

// ─── Settings Panel ───
const settingsVisible = ref(false)

// ─── Neuro / PATIENCE ───
let neuroService: NeuroService | null = null

// ─── Emotion System ───
let emotionSystem: EmotionSystem | null = null

// ─── VLM Service ───
let vlmService: VlmService | null = null

// ─── Context Engine ───
let contextEngine: ContextEngine | null = null

function onContextChange(ctx: WindowContext) {
  const info = getCategoryInfo(ctx.category)
  // Update status bar with context info
  contextOverride.value = `${info.emoji} ${info.label}`
  // Context change — only log to monitor, don't show bubble
  // (voice-ws activity messages provide the natural-language bubble)
  addMonitorEvent(info.emoji, `${ctx.processName} → ${info.label}`)
}

function onNeuroStatusChange(status: NeuroStatus) {
  if (status.state !== state.value) {
    state.value = status.state
  }
  // Emotion linkage: bored/sleepy → sleepy emotion
  if (status.state === 'sleepy' || status.state === 'bored') {
    emotionSystem?.force('sleepy', 0.6)
  } else if (status.state === 'happy') {
    emotionSystem?.trigger('happy', 0.4)
  } else if (status.state === 'idle' && (emotionSystem?.intensity ?? 0) < 0.1) {
    emotionSystem?.reset()
  }
}

function onEmotionChange(emotion: Emotion, intensity: number) {
  // Update status bar with emotion
  const emojiMap: Record<Emotion, string> = {
    neutral: '😐', happy: '😊', sad: '😢', surprised: '😮',
    angry: '😠', shy: '😳', sleepy: '💤', excited: '🤩', curious: '🤔'
  }
  contextOverride.value = `${emojiMap[emotion] || '🪽'} ${emotion} ${Math.round(intensity * 100)}%`

  // Link emotion to Live2D expression
  const exprMap: Record<Emotion, string> = {
    neutral: '', happy: 'F01', sad: 'F04', surprised: 'F02',
    angry: 'F03', shy: 'F06', sleepy: 'F05', excited: 'F01', curious: 'F07'
  }
  const expr = exprMap[emotion]
  if (expr && live2dRef.value) {
    const avail = live2dRef.value.getExpressions()
    if (avail.includes(expr)) {
      live2dRef.value.setExpression(expr)
      // Auto-revert after 5s
      setTimeout(() => live2dRef.value?.revertExpression(), 5000)
    }
  }
}

function onVlmResult(result: { summary: string; suggestedTopic: string }) {
  if (result.suggestedTopic) {
    showBubble(`💡 ${result.suggestedTopic}`, 4000)
    addMonitorEvent('👁', `VLM: ${result.summary.substring(0, 50)}`)
  }
}

// ─── Float Ball ───
// Default window size for normal mode (saved before entering ball mode)
const FLOAT_BALL_SIZE = 90
const NORMAL_WIDTH = 400
const NORMAL_HEIGHT = 500

async function toggleFloatBall() {
  floatBallMode.value = !floatBallMode.value
  // Tell Rust about ball mode change
  setBallMode(floatBallMode.value)
  if (!tauriWindow) {
    showBubble(floatBallMode.value ? '🔮 悬浮球模式' : '🪟 窗口模式', 2000)
    return
  }
  try {
    const win = tauriWindow.getCurrentWindow()
    if (floatBallMode.value) {
      // Enter ball mode: disable click-through (ball always interactive)
      await forceSetClickThrough(false)
      await win.setSize({ width: FLOAT_BALL_SIZE, height: FLOAT_BALL_SIZE } as any)
      showBubble('🔮 悬浮球模式 · 双击还原', 3000)
    } else {
      // Exit ball mode: restore window + re-enable click-through
      await win.setSize({ width: NORMAL_WIDTH, height: NORMAL_HEIGHT } as any)
      showBubble('🪟 窗口模式', 2000)
      setTimeout(() => forceSetClickThrough(true), 300)
    }
  } catch (e) {
    console.warn('[App] FloatBall toggle failed:', e)
  }
}

function onFloatBallContext(_pos: { x: number; y: number }) {
  // Open independent monitor window via Tauri
  if (tauriInvoke) {
    tauriInvoke('toggle_monitor_window').catch(() => {})
  }
}

// ─── LLM Model ───
const currentLlmModel = ref(llmManager.currentModel)
const llmModelList = ref<Array<{ id: string; cloud: boolean }>>([])
const monitorData = ref({
  stt: { status: 'unknown', label: 'STT' },
  tts: { status: 'unknown', label: 'TTS' },
  llm: { status: 'unknown', label: 'LLM' },
  live2d: { status: 'active', label: 'Live2D' },
})
const monitorEvents = ref<Array<{ time: string; icon: string; text: string }>>([])

function addMonitorEvent(icon: string, text: string) {
  const now = new Date()
  const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`
  const entry = { time, icon, text }
  monitorEvents.value.unshift(entry)
  if (monitorEvents.value.length > 50) monitorEvents.value.pop()
  // Forward to independent monitor window
  emitMonitorEvent(entry)
}

// ─── Forward data to independent monitor window via Tauri events ───
let _emitToMonitor: ((event: string, payload: unknown) => Promise<void>) | null = null

async function ensureEmitToMonitor() {
  if (!_emitToMonitor) {
    try {
      const evt = await import('@tauri-apps/api/event')
      _emitToMonitor = async (event: string, payload: unknown) => {
        try { await evt.emit(event, payload) } catch { /* ignore */ }
      }
    } catch { /* not in Tauri */ }
  }
}

async function emitMonitorStatus() {
  await ensureEmitToMonitor()
  if (!_emitToMonitor) return
  _emitToMonitor('monitor-status', {
    state: state.value,
    stt: monitorData.value.stt,
    tts: monitorData.value.tts,
    llm: monitorData.value.llm,
    live2d: monitorData.value.live2d,
  })
}

async function emitMonitorEvent(entry: { time: string; icon: string; text: string }) {
  await ensureEmitToMonitor()
  if (!_emitToMonitor) return
  _emitToMonitor('monitor-event', entry)
}

// Watch monitorData for changes → forward to monitor window
watch(monitorData, () => {
  emitMonitorStatus()
}, { deep: true })

// Also emit state changes
watch(state, () => {
  emitMonitorStatus()
})

function onConfigUpdate(newConfig: PetConfig) {
  petConfig.value = newConfig
  saveConfig(newConfig)

  // Apply emotion config
  if (emotionSystem) {
    emotionSystem.setConfig({ decayRate: newConfig.emotionDecayRate })
    if (newConfig.emotionEnabled && emotionSystem) {
      emotionSystem.start()
    } else {
      emotionSystem.stop()
      emotionSystem.reset()
    }
  }

  // Apply VLM config
  if (vlmService) {
    vlmService.setConfig({
      enabled: newConfig.vlmEnabled,
      intervalMs: newConfig.vlmInterval,
    })
  }

  // Apply patience config
  if (neuroService) {
    neuroService.setPatience(newConfig.patienceSeconds)
  }
}

// ─── Bubble ───
let bubbleTimer: ReturnType<typeof setTimeout> | null = null
let streamAccum = ''

function showBubble(text: string, duration = 3000) {
  bubbleText.value = text
  if (bubbleTimer) clearTimeout(bubbleTimer)
  if (duration > 0) {
    bubbleTimer = setTimeout(() => { bubbleText.value = '' }, duration)
  }
}

// ─── WebSocket ───
let ws: WebSocket | null = null

function connectWebSocket() {
  ws = new WebSocket('ws://127.0.0.1:7870/v1/voice/ws')
  ws.onopen = () => {
      ws!.send(JSON.stringify({ action: 'start', session_id: 'icarus_tauri' }))
      state.value = 'idle'
      monitorData.value.stt = { status: 'connected', label: 'STT' }
    addMonitorEvent('🔌', 'WebSocket 已连接')
  }
  ws.binaryType = 'arraybuffer'
  ws.onmessage = (event) => {
    // 二进制帧 = TTS 音频 (Hermes/edge 产出的 mp3/wav)。原代码在此直接 return
    // 丢弃, 导致 TTS 永不发声 —— 现在真正播放。
    if (event.data instanceof ArrayBuffer) {
      enqueueTtsAudio(new Blob([event.data]))
      return
    }
    try {
      const msg = JSON.parse(event.data)
      switch (msg.type) {
        case 'transcription':
          // 真实识别到用户语音 → 不再打断正在播放的 TTS (按顺序播放)
          showBubble(`👤 ${msg.text}`, 3000)
          addMonitorEvent('🎤', msg.text)
          neuroService?.setState('listening')
          break
        case 'thinking':
          // 不再打断播放: 旧音频继续播完, 新音频入队后排其后
          state.value = 'thinking'
          monitorData.value.llm = { status: 'thinking', label: 'LLM' }
          addMonitorEvent('🧠', '思考中...')
          neuroService?.setState('thinking')
          break
        case 'status':
          showBubble(msg.message, 2000)
          addMonitorEvent('📋', msg.message)
          break
        case 'done':
          // 不静音麦克风: TTS 期间保持聆听, 以支持自动打断 (barge-in)
          showBubble(msg.text, 5000)
          state.value = 'speaking'
          monitorData.value.tts = { status: 'speaking', label: 'TTS' }
          addMonitorEvent('🔊', msg.text?.substring(0, 30) || '完成')
          neuroService?.setState('speaking')
          // 用户语气驱动宠物反应 (SenseVoice 检测)
          if (msg.emotion) {
            const emoMap: Record<string, string> = {
              HAPPY: 'happy', SAD: 'sad', ANGRY: 'angry',
              DISGUSTED: 'disgusted', SURPRISED: 'surprised',
            }
            const pe = emoMap[msg.emotion]
            if (pe) emotionSystem?.trigger(pe, 0.35)
          } else {
            emotionSystem?.trigger('happy', 0.3)
          }
          setTimeout(() => {
            state.value = 'listening'
            monitorData.value.tts = { status: 'connected', label: 'TTS' }
            neuroService?.setState('listening')
          }, 4000)
          break
        case 'partial':
          showBubble(`🎤 ${msg.text}`, 1500)
          addMonitorEvent('🎤', msg.text)
          break
        case 'delta':
          // 流式首字上屏 (对标 N.E.K.O gemini_response)
          // is_first 时重置累积气泡, 把宠物状态切到 speaking (清 thinking)
          if (msg.is_first) {
            streamAccum = ''
            state.value = 'speaking'
            monitorData.value.tts = { status: 'speaking', label: 'TTS' }
            neuroService?.setState('speaking')
          }
          streamAccum += msg.text || ''
          showBubble(streamAccum, 5000)
          break
        case 'stop_tts':
          // 显式中断: 清空队列并停掉当前播放 (正常对话不再自动下发,
          // 仅作手动 "闭嘴" 逃生口保留)
          stopTtsAudio()
          break
        case 'emotion':
          // 用户语气/事件标签 (SenseVoice), 仅作监控展示
          if (msg.emotion || msg.event) {
            const label = [msg.emotion, msg.event].filter(Boolean).join(' / ')
            addMonitorEvent('😊', '用户: ' + label)
          }
          break
        case 'stt_status':
          monitorData.value.stt = { status: msg.status, label: 'STT' }
          if (msg.status === 'unavailable') showBubble('⚠️ ' + msg.message, 4000)
          else addMonitorEvent('🎙', msg.message)
          break
        case 'activity':
          // 哥哥当前前台窗口/程序 (N.E.K.O 式本地活动监测, voice-ws 推)
          activityText.value = msg.phrase || ''
          if (msg.phrase) addMonitorEvent('🖥️', msg.phrase)
          break
        case 'screen':
          // look 动作: 屏幕视觉描述 (Layer3, 配置门控 IKAROS_VISION_*)
          if (msg.desc) showBubble('👀 ' + msg.desc, 5000)
          else if (msg.message) showBubble(msg.message, 3000)
          break
      }
    } catch (_e) {}
  }
  ws.onclose = () => {
    state.value = 'idle'
    monitorData.value.stt = { status: 'disconnected', label: 'STT' }
    addMonitorEvent('⚠️', 'WebSocket 断开')
    setTimeout(connectWebSocket, 3000)
  }
  ws.onerror = () => {
    monitorData.value.stt = { status: 'error', label: 'STT' }
  }
}

// ─── TTS 音频队列 (二进制帧 → 顺序播放, 不打断) ───
// 每句 TTS 是一个独立的完整音频 blob; 全部入队, 由播放循环逐个播完,
// 新的音频不再覆盖/打断正在播放的旧音频 (满足"按顺序逐个播放完成")。
let _ttsQueue: Blob[] = []
let _ttsElem: HTMLAudioElement | null = null
let _ttsBusy = false
let _ttsCurUrl: string | null = null

function _ensureTtsElem(): HTMLAudioElement {
  if (!_ttsElem) {
    _ttsElem = new Audio()
    // 套用已选扬声器 (系统默认则不动)
    if (selectedSpeaker.value && selectedSpeaker.value !== 'default' && typeof (_ttsElem as any).setSinkId === 'function') {
      (_ttsElem as any).setSinkId(selectedSpeaker.value).catch(() => {})
    }
    _ttsElem.addEventListener('ended', _ttsOnEnded)
    _ttsElem.addEventListener('error', _ttsOnEnded)
  }
  return _ttsElem
}

/** 新音频入队 (不打断正在播放的) */
function enqueueTtsAudio(blob: Blob) {
  _ttsQueue.push(blob)
  _pumpTtsQueue()
}

function _pumpTtsQueue() {
  if (_ttsBusy) return
  const blob = _ttsQueue.shift()
  if (!blob) return
  _ttsBusy = true
  if (_ttsCurUrl) {
    URL.revokeObjectURL(_ttsCurUrl)
    _ttsCurUrl = null
  }
  const url = URL.createObjectURL(blob)
  _ttsCurUrl = url
  const elem = _ensureTtsElem()
  elem.src = url
  elem.play().catch((e: unknown) => {
    console.warn('[TTS] play failed:', e)
    _ttsBusy = false
    _pumpTtsQueue()
  })
}

function _ttsOnEnded() {
  _ttsBusy = false
  _pumpTtsQueue()
}

/** 显式中断: 清空队列并停掉当前播放 (手动 "闭嘴" 用, 正常对话不再自动触发) */
function stopTtsAudio() {
  _ttsQueue = []
  if (_ttsElem) {
    try { _ttsElem.pause() } catch {}
    try { _ttsElem.currentTime = 0 } catch {}
  }
  _ttsBusy = false
}

// ─── Speech input (microphone → WS PCM → server local STT) ───
const micActive = ref(false)
const speech = useSpeechInput({
  ws: () => ws,
  onPartial: (text: string) => showBubble('🎤 ' + text, 1500),
  onError: (msg: string) => {
    showBubble('⚠️ ' + msg, 4000)
    addMonitorEvent('⚠️', msg)
  },
  onStatus: (on: boolean) => {
    micActive.value = on
    state.value = on ? 'listening' : 'idle'
    showBubble(on ? '🎤 聆听中' : '🔇 已静音', 1500)
    // 首次授权麦克风后设备 label 才会填充, 重新推给托盘菜单
    if (on) pushAudioDevicesToTray()
  },
})

function toggleMic() {
  if (micActive.value) speech.stop()
  else speech.start()
}

// ─── Health check for LLM ───
async function checkLlmHealth() {
  try {
    const resp = await fetch('http://127.0.0.1:8080/health', { signal: AbortSignal.timeout(2000) })
    if (resp.ok) {
      monitorData.value.llm = { status: 'running', label: 'LLM' }
    } else {
      monitorData.value.llm = { status: 'error', label: 'LLM' }
    }
  } catch {
    monitorData.value.llm = { status: 'offline', label: 'LLM' }
  }
}

// ─── Double click: toggle chat dock ───
const chatDockVisible = ref(false)

function onDoubleClick() {
  chatDockVisible.value = !chatDockVisible.value
}

function onChatReply(text: string) {
  showBubble(`🪽 ${text}`, 5000)
  state.value = 'speaking'
  setTimeout(() => { state.value = 'listening' }, 2000)
}

// ─── Model & Action Handlers (called from tray events) ───
function doPrevModel() { live2dRef.value?.prevModel() }
function doNextModel() { live2dRef.value?.nextModel() }
function doSwitchModel(idx: number) { live2dRef.value?.switchToModel(idx) }
function doRandomModel() {
  const count = live2dRef.value?.getModelCount() ?? 0
  if (count <= 1) return
  let idx = Math.floor(Math.random() * count)
  const cur = live2dRef.value?.getCurrentModelIndex() ?? 0
  if (idx === cur) idx = (idx + 1) % count
  live2dRef.value?.switchToModel(idx)
}
function doNextTexture() { live2dRef.value?.nextTexture() }
function doSetScale(value: number) { live2dRef.value?.setModelScale(value) }
function doSetExpression(name: string) {
  live2dRef.value?.setExpression(name)
  setTimeout(() => live2dRef.value?.revertExpression(), 5000)
}

function doSelectLlm(modelId: string) {
  currentLlmModel.value = modelId
  llmManager.selectModel(modelId)
  const isCloud = llmManager.isCloudModel(modelId)
  showBubble(`${isCloud ? '☁️' : '💻'} ${modelId}`, 2000)
}

async function doScreenshot() {
  const dataUrl = live2dRef.value?.screenshot()
  if (!dataUrl) return
  if (tauriInvoke) {
    try {
      const filename = `ikaros-${Date.now()}.png`
      await tauriInvoke('save_screenshot', {
        base64Data: dataUrl,
        filename,
      })
      showBubble(`📸 已保存`, 3000)
      return
    } catch {
      // Fallback to browser download
    }
  }
  // Browser fallback download
  const link = document.createElement('a')
  link.download = `ikaros-screenshot-${Date.now()}.png`
  link.href = dataUrl
  link.click()
}

async function doRestart() {
  if (!tauriInvoke) return
  try {
    await tauriInvoke('restart_app')
  } catch {
    console.warn('[App] restart failed')
  }
}

async function fetchLlmModels() {
  const models = await llmManager.fetchModels()
  llmModelList.value = models
  currentLlmModel.value = llmManager.currentModel
}

// ─── Tray menu state sync ───
let hitFramesOn = false
let alwaysOnTop = false
let modeContinuous = true
let currentModelIdx = 0
let currentScaleVal = 100
let currentNeuroPatience = 30

function doToggleHitFrames() {
  live2dRef.value?.toggleHitFrames()
  hitFramesOn = !hitFramesOn
}

async function toggleAlwaysOnTop() {
  alwaysOnTop = !alwaysOnTop
  if (tauriInvoke) {
    try {
      await tauriInvoke('set_always_on_top', { onTop: alwaysOnTop })
      showBubble(alwaysOnTop ? '📌 窗口置顶' : '📌 取消置顶', 2000)
    } catch { /* ignore */ }
  }
  syncTrayMenu()
}

async function syncTrayMenu() {
  if (!tauriInvoke) return
  try {
    await tauriInvoke('sync_tray_menu', {
      floatBall: floatBallMode.value,
      modeContinuous: modeContinuous,
      hitFrames: hitFramesOn,
      llmLocal: currentLlmModel.value === 'qwen3-8b',
      alwaysOnTop: alwaysOnTop,
      neuroPatience: currentNeuroPatience,
      currentModel: currentModelIdx,
      currentScale: currentScaleVal,
    })
  } catch (e) {
    console.warn('[App] syncTrayMenu failed:', e)
  }
}

// ─── Audio device selection (right-click tray menu) ───
async function pushAudioDevicesToTray() {
  if (!tauriInvoke) return
  try {
    // 确保媒体权限已获取 (否则 enumerateDevices 只返回默认设备)
    await acquireMediaPermission()
    const { mics, speakers } = await enumerateAudioDevices()
    await tauriInvoke('update_audio_devices', {
      mics: mics.map((d) => [d.deviceId, d.label]),
      speakers: speakers.map((d) => [d.deviceId, d.label]),
      selected_mic: selectedMic.value,
      selected_speaker: selectedSpeaker.value,
    })
  } catch (e) {
    console.warn('[App] pushAudioDevicesToTray failed:', e)
  }
}

async function selectMic(dev: string) {
  setSelectedMic(dev)
  showBubble(`🎤 麦克风: ${labelForMic(dev)}`, 2000)
  // 若正在聆听, 重启以套用新设备
  if (speech.active.value) {
    speech.stop()
    await new Promise((r) => setTimeout(r, 150))
    speech.start()
  }
  await pushAudioDevicesToTray()
}

async function selectSpeaker(dev: string) {
  setSelectedSpeaker(dev)
  // 套用到当前 TTS 播放元素 (系统默认则不动)
  if (_ttsElem && typeof (_ttsElem as any).setSinkId === 'function') {
    try {
      if (dev !== 'default') await (_ttsElem as any).setSinkId(dev)
    } catch (e) {
      console.warn('[App] setSinkId failed:', e)
    }
  }
  showBubble(`🔊 扬声器: ${labelForSpeaker(dev)}`, 2000)
  await pushAudioDevicesToTray()
}

// ─── Lifecycle ───
let healthTimer: ReturnType<typeof setInterval> | null = null
let devicePollTimer: ReturnType<typeof setInterval> | null = null

onMounted(async () => {
  // ── Ensure Tauri APIs are loaded ──
  await ensureTauri()

  // Expose API for Tauri
  ;(window as any).__ICARUS_API__ = {
    setState: (s: string) => { state.value = s },
    showBubble,
  }

  connectWebSocket()
  checkLlmHealth()

  // Force initial click-through ON (Tauri invoke is now ready)
  setTimeout(() => forceSetClickThrough(true), 500)

  // Listen to Tauri window resize
  if (tauriWindow) {
    try {
      const win = tauriWindow.getCurrentWindow()
      await win.onResized(() => {
        live2dRef.value?.resize(window.innerWidth, window.innerHeight)
      })
    } catch (e) {
      console.warn('[App] Failed to setup resize listener:', e)
    }
  }

  // Start Neuro service
  neuroService = new NeuroService(onNeuroStatusChange)
  neuroService.start()

  // Start Emotion System
  emotionSystem = new EmotionSystem(onEmotionChange)
  if (petConfig.value.emotionEnabled) {
    emotionSystem.setConfig({ decayRate: petConfig.value.emotionDecayRate })
    emotionSystem.start()
  }

  // Start VLM Service
  vlmService = new VlmService(onVlmResult)
  if (petConfig.value.vlmEnabled) {
    vlmService.setConfig({ enabled: true, intervalMs: petConfig.value.vlmInterval })
    vlmService.start()
  }

  // Start Context Engine
  contextEngine = new ContextEngine(onContextChange)
  contextEngine.start()

  // Model list — set available models
  if (live2dRef.value) {
    live2dRef.value.setModelList([
      { name: 'Hiyori', path: '/live2d/hiyori_free_t08/hiyori_free_t08.model3.json' },
      { name: 'Haru', path: '/live2d/Haru/Haru.model3.json' },
      { name: 'Senko', path: '/live2d/Senko_Normals/senko.model3.json' },
    ], 0)
  }

  // Fetch LLM models
  fetchLlmModels()

  // Listen to tray events from Rust
  if (tauriListen) {
    try {
      await tauriListen('tray-event', async (event: any) => {
        const id = event.payload as string
        console.log('[App] tray-event:', id)
        // ── Audio device selection (dynamic ids from device submenus) ──
        if (id.startsWith('mic:')) { await selectMic(id.slice(4)); return }
        if (id.startsWith('speaker:')) { await selectSpeaker(id.slice(9)); return }
        switch (id) {
          // ── Model switching ──
          case 'model_prev':  doPrevModel(); currentModelIdx = live2dRef.value?.getCurrentModelIndex() ?? 0; syncTrayMenu(); break
          case 'model_next':  doNextModel(); currentModelIdx = live2dRef.value?.getCurrentModelIndex() ?? 0; syncTrayMenu(); break
          case 'model_1':     doSwitchModel(0); currentModelIdx = 0; syncTrayMenu(); break
          case 'model_2':     doSwitchModel(1); currentModelIdx = 1; syncTrayMenu(); break
          case 'model_3':     doSwitchModel(2); currentModelIdx = 2; syncTrayMenu(); break
          case 'random_model': doRandomModel(); currentModelIdx = live2dRef.value?.getCurrentModelIndex() ?? 0; syncTrayMenu(); break
          // ── Texture / Expression ──
          case 'texture_next': doNextTexture(); break
          case 'expr_F01': case 'expr_F02': case 'expr_F03': case 'expr_F04':
          case 'expr_F05': case 'expr_F06': case 'expr_F07': case 'expr_F08':
            doSetExpression(id.replace('expr_', '')); break
          // ── Scale ──
          case 'scale_050': doSetScale(0.5); currentScaleVal = 50; syncTrayMenu(); break
          case 'scale_075': doSetScale(0.75); currentScaleVal = 75; syncTrayMenu(); break
          case 'scale_100': doSetScale(1.0); currentScaleVal = 100; syncTrayMenu(); break
          case 'scale_125': doSetScale(1.25); currentScaleVal = 125; syncTrayMenu(); break
          case 'scale_150': doSetScale(1.5); currentScaleVal = 150; syncTrayMenu(); break
          case 'scale_200': doSetScale(2.0); currentScaleVal = 200; syncTrayMenu(); break
          // ── Tools ──
          case 'screenshot':   doScreenshot(); break
          case 'hit_frames':   doToggleHitFrames(); syncTrayMenu(); break
          case 'monitor':
            // Independent monitor window toggled by Rust — push current state so it
            // isn't empty/stale when it opens (it only holds default "unknown" otherwise)
            emitMonitorStatus()
            for (const ev of monitorEvents.value.slice(0, 20)) {
              emitMonitorEvent(ev)
            }
            break
          case 'settings':     settingsVisible.value = !settingsVisible.value; break
          case 'restart':      doRestart(); break
          case 'float_ball':   toggleFloatBall(); break
          case 'always_on_top': toggleAlwaysOnTop(); break
          case 'show_hide':
          case 'hide':
            // Handled by Rust side (window show/hide); nothing needed here
            break
          case 'quit':
            // Handled by Rust side (process::exit)
            break
          // ── LLM ──
          case 'llm_local':    doSelectLlm('qwen3-8b'); syncTrayMenu(); break
          case 'llm_cloud_ds': doSelectLlm('deepseek-chat'); syncTrayMenu(); break
          case 'llm_refresh':  fetchLlmModels(); break
          // ── Mode / Neuro (sync tray after state change) ──
          case 'mode_continuous':
            modeContinuous = true
            showBubble('🎤 连续对话模式', 2000);
            syncTrayMenu();
            break
          case 'mode_wake':
            modeContinuous = false
            showBubble('🔑 唤醒词模式', 2000);
            syncTrayMenu();
            break
          case 'neuro_trigger':
            neuroService?.triggerPatience()
            showBubble('💬 主动说话已触发', 2000)
            break
          case 'neuro_patience_15':
            neuroService?.setPatience(15)
            currentNeuroPatience = 15
            showBubble('⏱️ PATIENCE: 15s', 2000)
            syncTrayMenu();
            break
          case 'neuro_patience_30':
            neuroService?.setPatience(30)
            currentNeuroPatience = 30
            showBubble('⏱️ PATIENCE: 30s', 2000)
            syncTrayMenu();
            break
          case 'neuro_patience_60':
            neuroService?.setPatience(60)
            currentNeuroPatience = 60
            showBubble('⏱️ PATIENCE: 60s', 2000)
            syncTrayMenu();
            break
          case 'neuro_patience_120':
            neuroService?.setPatience(120)
            currentNeuroPatience = 120
            showBubble('⏱️ PATIENCE: 120s', 2000)
            syncTrayMenu();
            break
          case 'neuro_reset':
            neuroService?.resetSignals()
            showBubble('🔄 说话标志已重置', 2000)
            break
          case 'mic_high':
          case 'mic_mid':
          case 'mic_low':
            showBubble(`🎚️ 麦克风: ${id.replace('mic_', '')}`, 2000)
            break
          default:
            console.warn('[App] Unknown tray-event id:', id)
        }
      })
      console.log('[App] tray-event listener registered')
    } catch (e) {
      console.warn('[App] tray-event listener failed:', e)
    }
  } else {
    console.warn('[App] tauriListen not available — tray events disabled')
  }

  // ── Audio device enumeration → push to tray menu (so 麦克风/扬声器 submenus populate) ──
  pushAudioDevicesToTray()
  // devicechange 在某些 WebView2 里不可靠, 加 15s 轮询兜底
  if (navigator.mediaDevices?.addEventListener) {
    navigator.mediaDevices.addEventListener('devicechange', () => pushAudioDevicesToTray())
  }
  devicePollTimer = setInterval(() => pushAudioDevicesToTray(), 15000)

  // Click-through is now handled by polling-based mouse tracking
  // Initial state: cursor events pass through transparent areas

  // Periodic health checks
  healthTimer = setInterval(() => {
    checkLlmHealth()
  }, 10000)
})

onUnmounted(() => {
  if (ws) ws.close()
  if (bubbleTimer) clearTimeout(bubbleTimer)
  if (healthTimer) clearInterval(healthTimer)
  if (neuroService) neuroService.stop()
  if (emotionSystem) emotionSystem.stop()
  if (vlmService) vlmService.stop()
  if (contextEngine) contextEngine.stop()
  if (devicePollTimer) clearInterval(devicePollTimer)
})
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

html, body {
  background: transparent !important;
  overflow: hidden;
}

#app {
  background: transparent !important;
}

.pet-container {
  width: 100vw;
  height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: transparent;
  position: relative;
  user-select: none;
}

/* Float ball mode — let FloatBall fill the entire window */
.pet-container.ball-mode {
  justify-content: stretch;
  align-items: stretch;
}

/* Speech bubble in ball mode — smaller, positioned at top */
.ball-bubble {
  top: -4px !important;
  font-size: 11px !important;
  padding: 6px 10px !important;
  max-width: 200px !important;
  border-radius: 10px !important;
}

/* FloatBall enter/leave transition */
.ball-fade-enter-active,
.ball-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.ball-fade-enter-from {
  opacity: 0;
  transform: scale(0.6);
}
.ball-fade-leave-to {
  opacity: 0;
  transform: scale(0.6);
}

/* Bottom buttons */
.bottom-buttons {
  position: absolute;
  bottom: 8px;
  right: 8px;
  display: flex;
  gap: 6px;
  z-index: 200;
}

/* Drag handle (thin top bar for window dragging) */
.drag-handle {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 20px;
  -webkit-app-region: drag;
  z-index: 50;
}

.mode-toggle {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  cursor: pointer;
  backdrop-filter: blur(4px);
  transition: all 0.2s;
  -webkit-app-region: no-drag;
}
.mode-toggle:hover {
  background: rgba(0, 0, 0, 0.6);
  transform: scale(1.1);
}
.mode-toggle.active {
  background: rgba(102, 204, 255, 0.3);
  box-shadow: 0 0 8px rgba(102, 204, 255, 0.4);
}

/* Speech bubble */
.speech-bubble {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255, 255, 255, 0.95);
  color: #222;
  padding: 10px 16px;
  border-radius: 14px;
  font-size: 13px;
  max-width: 280px;
  line-height: 1.4;
  box-shadow: 0 3px 15px rgba(0, 0, 0, 0.15);
  backdrop-filter: blur(8px);
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
  z-index: 100;
  -webkit-app-region: no-drag;
}

.bubble-enter-active { transition: all 0.3s ease; }
.bubble-leave-active { transition: all 0.2s ease; }
.bubble-enter-from { opacity: 0; transform: translateX(-50%) translateY(10px); }
.bubble-leave-to { opacity: 0; transform: translateX(-50%) translateY(-10px); }
</style>
