<template>
  <Teleport to="body">
    <transition name="panel-slide">
      <div v-if="visible" class="monitor-overlay" @click="close">
        <div class="monitor-panel" :style="{ transform: `scale(${panelScale})`, transformOrigin: 'bottom right' }" @click.stop>
          <!-- Header -->
          <div class="panel-header">
            <span>📊 监控面板</span>
            <button class="close-btn" @click="close">✕</button>
          </div>

          <!-- Service Status -->
          <div class="status-grid">
            <div class="status-card" :class="getStatusClass(sttStatus.status)">
              <div class="status-dot">{{ getDot(sttStatus.status) }}</div>
              <div class="status-info">
                <div class="status-label">STT</div>
                <div class="status-text">{{ getStatusText(sttStatus.status) }}</div>
              </div>
            </div>
            <div class="status-card" :class="getStatusClass(ttsStatus.status)">
              <div class="status-dot">{{ getDot(ttsStatus.status) }}</div>
              <div class="status-info">
                <div class="status-label">TTS</div>
                <div class="status-text">{{ getStatusText(ttsStatus.status) }}</div>
              </div>
            </div>
            <div class="status-card" :class="getStatusClass(llmStatus.status)">
              <div class="status-dot">{{ getDot(llmStatus.status) }}</div>
              <div class="status-info">
                <div class="status-label">LLM</div>
                <div class="status-text">{{ getStatusText(llmStatus.status) }}</div>
              </div>
            </div>
            <div class="status-card" :class="getStatusClass(live2dStatus.status)">
              <div class="status-dot">{{ getDot(live2dStatus.status) }}</div>
              <div class="status-info">
                <div class="status-label">Live2D</div>
                <div class="status-text">{{ getStatusText(live2dStatus.status) }}</div>
              </div>
            </div>
          </div>

          <!-- Current State -->
          <div class="current-state">
            <span class="state-label">当前状态:</span>
            <span class="state-value">{{ stateEmoji }} {{ stateText }}</span>
          </div>

          <!-- Current Activity (foreground window / program) -->
          <div class="current-activity" v-if="activity">
            <span class="activity-label">当前窗口:</span>
            <span class="activity-value">{{ activity }}</span>
          </div>

          <!-- Event Log -->
          <div class="event-log">
            <div class="log-header">事件日志</div>
            <div class="log-list">
              <div v-for="(evt, i) in events.slice(0, 20)" :key="i" class="log-item">
                <span class="log-time">{{ evt.time }}</span>
                <span class="log-icon">{{ evt.icon }}</span>
                <span class="log-text">{{ evt.text }}</span>
              </div>
              <div v-if="events.length === 0" class="log-empty">暂无事件</div>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * MonitorPanel.vue — STT/TTS/Live2D/LLM status monitor.
 */
import { computed, ref, onMounted, onUnmounted } from 'vue'

const props = defineProps<{
  visible: boolean
  state: string
  activity?: string
  sttStatus: { status: string; label: string }
  ttsStatus: { status: string; label: string }
  llmStatus: { status: string; label: string }
  live2dStatus: { status: string; label: string }
  events: Array<{ time: string; icon: string; text: string }>
  onClose?: () => void
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

// Scale panel based on window size
const windowWidth = ref(window.innerWidth)
const panelScale = computed(() => Math.max(0.6, Math.min(1, windowWidth.value / 400)))

function updateWindowWidth() {
  windowWidth.value = window.innerWidth
}

onMounted(() => window.addEventListener('resize', updateWindowWidth))
onUnmounted(() => window.removeEventListener('resize', updateWindowWidth))

const STATE_MAP: Record<string, { emoji: string; text: string }> = {
  idle:      { emoji: '🪽', text: '待机' },
  listening: { emoji: '🎤', text: '聆听' },
  thinking:  { emoji: '🧠', text: '思考' },
  speaking:  { emoji: '🔊', text: '说话' },
  happy:     { emoji: '😊', text: '开心' },
  sleepy:    { emoji: '💤', text: '休息' },
}

const stateEmoji = computed(() => STATE_MAP[props.state]?.emoji || '🪽')
const stateText = computed(() => STATE_MAP[props.state]?.text || '待机')

function close() {
  if (props.onClose) {
    props.onClose()
  } else {
    emit('close')
  }
}

function getDot(status: string): string {
  switch (status) {
    case 'connected': case 'running': case 'active': case 'speaking': case 'thinking':
      return '🟢'
    case 'disconnected': case 'offline':
      return '⚫'
    case 'error':
      return '🔴'
    default:
      return '⚪'
  }
}

function getStatusText(status: string): string {
  switch (status) {
    case 'connected': return '已连接'
    case 'running': return '运行中'
    case 'active': return '活跃'
    case 'speaking': return '播报中'
    case 'thinking': return '思考中'
    case 'disconnected': return '已断开'
    case 'offline': return '离线'
    case 'error': return '错误'
    default: return '未知'
  }
}

function getStatusClass(status: string): string {
  switch (status) {
    case 'connected': case 'running': case 'active': case 'speaking': case 'thinking':
      return 'status-ok'
    case 'disconnected': case 'offline':
      return 'status-off'
    case 'error':
      return 'status-err'
    default:
      return 'status-unknown'
  }
}
</script>

<style scoped>
.monitor-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 9000;
  background: rgba(0, 0, 0, 0.3);
  -webkit-app-region: no-drag;
}

.monitor-panel {
  position: fixed;
  bottom: 40px;
  right: 10px;
  width: min(320px, 90vw);
  max-height: min(450px, 80vh);
  background: rgba(20, 20, 30, 0.95);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  backdrop-filter: blur(16px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
  color: rgba(255, 255, 255, 0.9);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 13px;
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.5);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 4px;
}
.close-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: white;
}

.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 10px;
}

.status-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.status-dot {
  font-size: 10px;
  flex-shrink: 0;
}

.status-label {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.6);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.status-text {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.8);
}

.status-ok { border-color: rgba(63, 185, 80, 0.3); }
.status-err { border-color: rgba(255, 80, 80, 0.3); }
.status-off { opacity: 0.5; }

.current-state {
  padding: 8px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.state-label {
  color: rgba(255, 255, 255, 0.5);
}

.state-value {
  color: rgba(255, 255, 255, 0.9);
}

.current-activity {
  padding: 8px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.activity-label {
  color: rgba(255, 255, 255, 0.5);
}

.activity-value {
  color: rgba(120, 200, 255, 0.95);
}

.event-log {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.log-header {
  padding: 8px 14px 4px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.4);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.log-list {
  flex: 1;
  overflow-y: auto;
  padding: 4px 10px 10px;
  max-height: 180px;
}

.log-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 0;
  font-size: 11px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}

.log-time {
  color: rgba(255, 255, 255, 0.3);
  font-family: 'Cascadia Code', monospace;
  font-size: 10px;
  flex-shrink: 0;
}

.log-icon {
  flex-shrink: 0;
}

.log-text {
  color: rgba(255, 255, 255, 0.75);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-empty {
  text-align: center;
  color: rgba(255, 255, 255, 0.3);
  font-size: 11px;
  padding: 16px;
}

/* Transitions */
.panel-slide-enter-active { transition: all 0.25s ease; }
.panel-slide-leave-active { transition: all 0.2s ease; }
.panel-slide-enter-from { opacity: 0; transform: translateY(20px); }
.panel-slide-leave-to { opacity: 0; transform: translateY(10px); }

/* Scrollbar */
.log-list::-webkit-scrollbar { width: 4px; }
.log-list::-webkit-scrollbar-track { background: transparent; }
.log-list::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 2px; }
</style>
