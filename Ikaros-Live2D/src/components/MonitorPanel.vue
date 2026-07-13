<template>
  <div class="dash">
    <!-- Top bar -->
    <div class="topbar">
      <h1>🪶 <span>Ikaros</span> 监控面板</h1>
      <div class="conn">
        <span class="conn-dot" :class="connected ? 'on' : 'off'"></span>
        <span class="conn-label">{{ connected ? '已连接' : '未连接' }}</span>
      </div>
    </div>

    <div class="body">
      <!-- Service status -->
      <div class="card">
        <div class="card-title">🔌 服务状态</div>
        <div class="status-grid">
          <div class="status-card" :class="statusClass(sttStatus.status)" :title="sttStatus.label">
            <span class="sdot">{{ dot(sttStatus.status) }}</span>
            <span class="slabel">STT</span>
            <span class="stext">{{ statusText(sttStatus.status) }}</span>
          </div>
          <div class="status-card" :class="statusClass(ttsStatus.status)">
            <span class="sdot">{{ dot(ttsStatus.status) }}</span>
            <span class="slabel">TTS</span>
            <span class="stext">{{ statusText(ttsStatus.status) }}</span>
          </div>
          <div class="status-card" :class="statusClass(llmStatus.status)">
            <span class="sdot">{{ dot(llmStatus.status) }}</span>
            <span class="slabel">LLM</span>
            <span class="stext">{{ statusText(llmStatus.status) }}</span>
          </div>
          <div class="status-card" :class="statusClass(live2dStatus.status)">
            <span class="sdot">{{ dot(live2dStatus.status) }}</span>
            <span class="slabel">Live2D</span>
            <span class="stext">{{ statusText(live2dStatus.status) }}</span>
          </div>
        </div>
        <div class="state-line">
          <span class="dim">当前状态</span>
          <span class="state-val">{{ stateEmoji }} {{ stateText }}</span>
          <span class="dim activity" v-if="activity">· {{ activity }}</span>
        </div>
      </div>

      <!-- Affect (PAD) -->
      <div class="card">
        <div class="card-title">🎭 情感 (PAD)</div>
        <div class="affect-row">
          <span class="affect-label">愉悦</span><span class="affect-emoji">😊</span>
          <div class="affect-track"><div class="affect-fill P" :style="{ width: affW(affect?.pleasure) + '%' }"></div></div>
          <span class="affect-value">{{ affFmt(affect?.pleasure) }}</span>
        </div>
        <div class="affect-row">
          <span class="affect-label">唤醒</span><span class="affect-emoji">⚡</span>
          <div class="affect-track"><div class="affect-fill A" :style="{ width: affW(affect?.arousal) + '%' }"></div></div>
          <span class="affect-value">{{ affFmt(affect?.arousal) }}</span>
        </div>
        <div class="affect-row">
          <span class="affect-label">支配</span><span class="affect-emoji">👑</span>
          <div class="affect-track"><div class="affect-fill D" :style="{ width: affW(affect?.dominance) + '%' }"></div></div>
          <span class="affect-value">{{ affFmt(affect?.dominance) }}</span>
        </div>
      </div>

      <!-- Vitality + relationship -->
      <div class="card">
        <div class="card-title">✨ 生命活力</div>
        <div class="vitality-row">
          <span class="vitality-label">活力</span>
          <div class="vitality-track"><div class="vitality-fill" :style="{ width: pct(vitality?.vitality) + '%' }"></div></div>
          <span class="vitality-num">{{ pct(vitality?.vitality) }}%</span>
        </div>
        <div class="stat-grid">
          <div class="stat"><span class="stat-num">{{ vitality?.conversation_count ?? '—' }}</span><span class="stat-cap">对话</span></div>
          <div class="stat"><span class="stat-num">{{ uptimeHours }}</span><span class="stat-cap">在线(时)</span></div>
          <div class="stat"><span class="stat-num">{{ relationship?.shared_experiences ?? '—' }}</span><span class="stat-cap">共同经历</span></div>
        </div>
      </div>

      <!-- Relationship depth / warmth -->
      <div class="card">
        <div class="card-title">💞 关系</div>
        <div class="affect-row">
          <span class="affect-label">深度</span><span class="affect-emoji">🌊</span>
          <div class="affect-track"><div class="affect-fill Rd" :style="{ width: pct(relationship?.depth) + '%' }"></div></div>
          <span class="affect-value">{{ pct(relationship?.depth) }}%</span>
        </div>
        <div class="affect-row">
          <span class="affect-label">温暖</span><span class="affect-emoji">🔥</span>
          <div class="affect-track"><div class="affect-fill Rw" :style="{ width: pct(relationship?.warmth) + '%' }"></div></div>
          <span class="affect-value">{{ pct(relationship?.warmth) }}%</span>
        </div>
      </div>

      <!-- Self / Curiosity -->
      <div class="card">
        <div class="card-title">🧭 自我 / 探索欲</div>
        <div class="affect-row">
          <span class="affect-label">探索欲</span><span class="affect-emoji">🔍</span>
          <div class="affect-track"><div class="affect-fill Cu" :style="{ width: curW + '%' }"></div></div>
          <span class="affect-value">{{ curPct }}%</span>
        </div>
        <div class="self-thought">
          <div v-if="latestThought?.text" class="self-thought-text">{{ latestThought.text }}</div>
          <div v-else class="self-thought-empty">暂未开始自我思考 — 空闲时她会独自内省、探索爱·人·机器人</div>
          <div v-if="latestThought?.text" class="self-thought-meta">
            <span v-if="latestThought.theme" class="theme-tag">{{ themeCn(latestThought.theme) }}</span>
            <span v-if="latestThought.kind" class="kind-tag">{{ kindCn(latestThought.kind) }}</span>
            <span>{{ fmtTime(latestThought.ts) }}</span>
          </div>
        </div>
        <div class="stat-grid4">
          <div class="stat"><span class="stat-num">{{ selfModel?.metacog?.reflection_count ?? '—' }}</span><span class="stat-cap">反思</span></div>
          <div class="stat"><span class="stat-num">{{ selfModel?.metacog?.philosophy_count ?? '—' }}</span><span class="stat-cap">哲思</span></div>
          <div class="stat"><span class="stat-num">{{ memTotal }}</span><span class="stat-cap">记忆</span></div>
          <div class="stat"><span class="stat-num">{{ questionsCount }}</span><span class="stat-cap">问题</span></div>
        </div>
      </div>

      <!-- Monologue -->
      <div class="card">
        <div class="card-title">💭 内心独白</div>
        <div v-if="monologue" class="mono-text">{{ monologue.text }}</div>
        <div v-else class="mono-empty">暂无内心独白</div>
        <div v-if="monologue" class="mono-meta">
          <span v-if="monologue.mood">情绪: {{ monologue.mood }}</span>
          <span v-if="monologue.intensity != null">强度: {{ monologue.intensity.toFixed(2) }}</span>
          <span>{{ fmtTime(monologue.ts) }}</span>
        </div>
      </div>

      <!-- Conversation stream -->
      <div class="card stream-card">
        <div class="card-title">💬 对话流 <span class="count">{{ stream.length }}</span></div>
        <div class="stream" ref="streamEl">
          <div v-for="(m, i) in stream" :key="i" class="msg" :class="'msg-' + msgKind(m)">
            <div class="msg-head">
              <span class="msg-label">{{ msgLabel(m) }}</span>
              <span class="msg-time">{{ fmtTime(m.ts) }}</span>
            </div>
            <div class="msg-body">{{ m.text || '…' }}</div>
            <span v-if="m.mood" class="mood-tag">{{ m.mood }}</span>
          </div>
          <div v-if="stream.length === 0" class="stream-empty">🪶 等待伊卡洛斯的消息…</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * MonitorPanel.vue — Ikaros 监控面板（独立窗口）。
 * 参照 tools/ikaros-dashboard 暖色深色主题：服务状态 + PAD 情感 + 活力 +
 * 关系 + 内心独白 + 对话流。服务状态来自主窗口 Tauri 事件（实时），其余来自
 * read_ikaros_state 命令读取的 V5 状态文件与对话日志。
 */
import { computed, ref, nextTick, watch, unref } from 'vue'

interface LogEntry { kind?: string; type?: string; ts?: number; text?: string; mood?: string; intensity?: number; session_id?: string }

const props = defineProps<{
  state: string
  activity?: string
  connected?: boolean
  sttStatus: { status: string; label: string }
  ttsStatus: { status: string; label: string }
  llmStatus: { status: string; label: string }
  live2dStatus: { status: string; label: string }
  affect?: { pleasure?: number; arousal?: number; dominance?: number } | null
  vitality?: { vitality?: number; conversation_count?: number; total_uptime_sec?: number } | null
  care?: Record<string, unknown> | null
  relationship?: { depth?: number; warmth?: number; shared_experiences?: number } | null
  log?: LogEntry[]
  selfModel?: {
    curiosity?: { level?: number }
    identity?: { name?: string }
    metacog?: { reflection_count?: number; philosophy_count?: number; philosophy_by_theme?: Record<string, number> }
    memory_self_view?: { total?: number }
    questions?: unknown[]
  } | null
  latestThought?: {
    text?: string
    kind?: string
    theme?: string
    curiosity?: number
    ts?: number
  } | null
}>()

// ── Defensive unwrap of root props ──
// When refs are passed as root props via createApp(), Vue may deliver them
// still wrapped (not auto-unwrapped). unref() makes this correct either way.
const state = computed(() => unref(props.state as any) as string)
const activity = computed(() => unref(props.activity as any) as string)
const connected = computed(() => unref(props.connected as any) ?? false)
const sttStatus = computed(() => unref(props.sttStatus as any) as { status: string; label: string })
const ttsStatus = computed(() => unref(props.ttsStatus as any) as { status: string; label: string })
const llmStatus = computed(() => unref(props.llmStatus as any) as { status: string; label: string })
const live2dStatus = computed(() => unref(props.live2dStatus as any) as { status: string; label: string })
const affect = computed(() => unref(props.affect as any) as { pleasure?: number; arousal?: number; dominance?: number } | null)
const vitality = computed(() => unref(props.vitality as any) as { vitality?: number; conversation_count?: number; total_uptime_sec?: number } | null)
const care = computed(() => unref(props.care as any) as Record<string, unknown> | null)
const relationship = computed(() => unref(props.relationship as any) as { depth?: number; warmth?: number; shared_experiences?: number } | null)
const log = computed(() => (unref(props.log as any) as LogEntry[] | undefined) ?? [])
const selfModel = computed(() => unref(props.selfModel as any) as {
  curiosity?: { level?: number }
  identity?: { name?: string }
  metacog?: { reflection_count?: number; philosophy_count?: number; philosophy_by_theme?: Record<string, number> }
  memory_self_view?: { total?: number }
  questions?: unknown[]
} | null)
const latestThought = computed(() => unref(props.latestThought as any) as {
  text?: string; kind?: string; theme?: string; curiosity?: number; ts?: number
} | null)

// ── self / curiosity card ──
const curiosity = computed(() => selfModel.value?.curiosity?.level ?? 0)
const curPct = computed(() => Math.round(curiosity.value * 100))
const curW = computed(() => Math.max(3, Math.round(curiosity.value * 100)))
const memTotal = computed(() => selfModel.value?.memory_self_view?.total ?? '—')
const questionsCount = computed(() => selfModel.value?.questions?.length ?? '—')
function themeCn(t?: string): string {
  const m: Record<string, string> = { love: '爱', human: '人', robot: '机器人', self: '自我' }
  return t ? (m[t] || t) : ''
}
function kindCn(k?: string): string {
  return k === 'philosophy' ? '哲思' : k === 'self' ? '内省' : (k || '')
}

// ── conversation stream (from file log) ──
const stream = computed<LogEntry[]>(() => {
  const list = log.value ?? []
  return list.filter(m => {
    const k = m.kind || m.type
    return k === 'user_msg' || k === 'assistant_msg' || k === 'thought'
  })
})

const monologue = computed<LogEntry | null>(() => {
  const list = log.value ?? []
  for (let i = list.length - 1; i >= 0; i--) {
    const k = list[i].kind || list[i].type
    if (k === 'thought' && list[i].text) return list[i]
  }
  return null
})

const uptimeHours = computed(() => {
  const s = vitality.value?.total_uptime_sec
  if (s == null) return '—'
  return (s / 3600).toFixed(1)
})

// auto-scroll stream to bottom on new messages
const streamEl = ref<HTMLElement | null>(null)
watch(() => stream.value.length, () => {
  nextTick(() => { if (streamEl.value) streamEl.value.scrollTop = streamEl.value.scrollHeight })
})

// ── state map ──
const STATE_MAP: Record<string, { emoji: string; text: string }> = {
  idle: { emoji: '🪽', text: '待机' },
  listening: { emoji: '🎤', text: '聆听' },
  thinking: { emoji: '🧠', text: '思考' },
  speaking: { emoji: '🔊', text: '说话' },
  happy: { emoji: '😊', text: '开心' },
  sleepy: { emoji: '💤', text: '休息' },
}
const stateEmoji = computed(() => STATE_MAP[state.value]?.emoji || '🪽')
const stateText = computed(() => STATE_MAP[state.value]?.text || '待机')

// ── helpers ──
function affW(v?: number | null): number { if (v == null) return 50; return Math.round((v + 1) * 50) }
function affFmt(v?: number | null): string { if (v == null) return '—'; return (v >= 0 ? '+' : '') + Math.round(v * 100) + '%' }
function pct(v?: number | null): number { if (v == null) return 0; return Math.max(0, Math.min(100, Math.round(v * 100))) }

function fmtTime(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function msgKind(m: LogEntry): string { return m.kind || m.type || 'other' }
function msgLabel(m: LogEntry): string {
  const k = msgKind(m)
  if (k === 'user_msg') return '👤 哥哥'
  if (k === 'assistant_msg') return '🪶 伊卡洛斯'
  if (k === 'thought') return '💭 内心'
  return '📋 ' + k
}

function dot(status: string): string {
  switch (status) {
    case 'connected': case 'running': case 'active': case 'speaking': case 'thinking': case 'ready': return '🟢'
    case 'disconnected': case 'offline': case 'unavailable': return '⚫'
    case 'error': return '🔴'
    default: return '⚪'
  }
}
function statusText(status: string): string {
  switch (status) {
    case 'connected': return '已连接'
    case 'ready': return '就绪'
    case 'running': return '运行中'
    case 'active': return '活跃'
    case 'speaking': return '播报中'
    case 'thinking': return '思考中'
    case 'disconnected': return '已断开'
    case 'offline': return '离线'
    case 'unavailable': return '不可用'
    case 'error': return '错误'
    default: return '未知'
  }
}
function statusClass(status: string): string {
  switch (status) {
    case 'connected': case 'running': case 'active': case 'speaking': case 'thinking': case 'ready': return 'status-ok'
    case 'disconnected': case 'offline': case 'unavailable': return 'status-off'
    case 'error': return 'status-err'
    default: return 'status-unknown'
  }
}
</script>

<style scoped>
/* ── ikaros warm dark theme (from tools/ikaros-dashboard) ── */
.dash {
  --bg: #181612;
  --surface: #24201c;
  --surface2: #2d2823;
  --border: #3d3530;
  --text: #e2d8ce;
  --text-muted: #9b9086;
  --accent: #d4a574;
  --accent-dim: #b8895e;
  --warm-green: #7da87d;
  --warm-orange: #c48b5e;
  --warm-purple: #b58bc4;
  --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif;
  --font-mono: 'Cascadia Code', 'Consolas', monospace;
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  color: var(--text);
  font: 13px/1.6 var(--font);
  overflow: hidden;
}

/* top bar */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 9px 14px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.topbar h1 { font-size: 14px; font-weight: 600; letter-spacing: .3px; }
.topbar h1 span { color: var(--accent); }
.conn { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-muted); }
.conn-dot { width: 8px; height: 8px; border-radius: 50%; background: #666; transition: background .3s; }
.conn-dot.on { background: var(--warm-green); box-shadow: 0 0 6px rgba(125,168,125,.5); }
.conn-dot.off { background: var(--warm-orange); }

/* body scroll */
.body { flex: 1; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 10px; min-height: 0; }

/* card */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 11px 13px; flex-shrink: 0; }
.card-title { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; color: var(--text-muted); margin-bottom: 9px; display: flex; align-items: center; gap: 6px; }
.card-title .count { margin-left: auto; font-family: var(--font-mono); background: rgba(255,255,255,.06); padding: 0 6px; border-radius: 3px; }

/* service status grid */
.status-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.status-card { display: flex; align-items: center; gap: 6px; padding: 6px 9px; border-radius: 6px; background: var(--surface2); border: 1px solid var(--border); }
.sdot { font-size: 9px; flex-shrink: 0; }
.slabel { font-size: 10px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; }
.stext { font-size: 11px; color: var(--text); margin-left: auto; }
.status-ok { border-color: rgba(125,168,125,.4); }
.status-err { border-color: rgba(196,139,94,.5); }
.status-off { opacity: .55; }
.state-line { display: flex; align-items: center; gap: 6px; margin-top: 9px; padding-top: 9px; border-top: 1px solid var(--border); font-size: 12px; }
.state-line .dim { color: var(--text-muted); }
.state-line .state-val { color: var(--text); }
.state-line .activity { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--accent); }

/* affect / relationship bars */
.affect-row { display: flex; align-items: center; gap: 7px; margin-bottom: 7px; }
.affect-row:last-child { margin-bottom: 0; }
.affect-label { width: 30px; font-size: 12px; color: var(--text-muted); flex-shrink: 0; }
.affect-emoji { font-size: 14px; width: 18px; text-align: center; flex-shrink: 0; }
.affect-track { flex: 1; height: 14px; background: var(--surface2); border-radius: 7px; overflow: hidden; }
.affect-fill { height: 100%; border-radius: 7px; transition: width .6s ease; }
.affect-fill.P { background: linear-gradient(90deg, #b8895e, #d4a574); }
.affect-fill.A { background: linear-gradient(90deg, #7da87d, #a8d4a8); }
.affect-fill.D { background: linear-gradient(90deg, #b58bc4, #d4b0e0); }
.affect-fill.Rd { background: linear-gradient(90deg, #6f96c4, #9cc0e0); }
.affect-fill.Rw { background: linear-gradient(90deg, #c47d7d, #e0a8a8); }
.affect-fill.Cu { background: linear-gradient(90deg, #5ea8c4, #8fd4e0); }

/* self / curiosity card */
.self-thought { margin: 9px 0 4px; }
.self-thought-text { font-size: 12px; line-height: 1.65; color: var(--text); font-style: italic; word-break: break-word; white-space: pre-wrap; max-height: 116px; overflow-y: auto; }
.self-thought-empty { font-size: 12px; color: var(--text-muted); font-style: italic; }
.self-thought-meta { display: flex; gap: 8px; margin-top: 6px; font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); align-items: center; flex-wrap: wrap; }
.theme-tag { background: rgba(94,168,196,.18); color: #8fd4e0; padding: 1px 7px; border-radius: 3px; }
.kind-tag { background: rgba(181,139,196,.18); color: var(--warm-purple); padding: 1px 7px; border-radius: 3px; }
.stat-grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-top: 9px; }
.affect-value { font-size: 11px; font-family: var(--font-mono); color: var(--text); width: 42px; text-align: right; flex-shrink: 0; }

/* vitality */
.vitality-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.vitality-label { font-size: 12px; color: var(--text-muted); flex-shrink: 0; width: 30px; }
.vitality-track { flex: 1; height: 10px; background: var(--surface2); border-radius: 5px; overflow: hidden; }
.vitality-fill { height: 100%; border-radius: 5px; background: linear-gradient(90deg, var(--accent-dim), var(--accent)); transition: width .6s ease; }
.vitality-num { font-size: 11px; font-family: var(--font-mono); color: var(--text); width: 42px; text-align: right; }
.stat-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.stat { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 4px; background: var(--surface2); border-radius: 6px; }
.stat-num { font-size: 15px; font-weight: 600; color: var(--accent); font-family: var(--font-mono); }
.stat-cap { font-size: 10px; color: var(--text-muted); }

/* monologue */
.mono-text { font-size: 13px; line-height: 1.7; color: var(--warm-purple); font-style: italic; word-break: break-word; }
.mono-empty { font-size: 12px; color: var(--text-muted); font-style: italic; }
.mono-meta { display: flex; gap: 10px; margin-top: 6px; font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); }

/* conversation stream */
.stream-card { flex: 1; display: flex; flex-direction: column; min-height: 160px; }
.stream { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
.msg { padding: 7px 9px; border-radius: 7px; font-size: 12px; line-height: 1.5; animation: fadeIn .25s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
.msg-user_msg { background: rgba(196,139,94,.12); border-left: 3px solid var(--warm-orange); }
.msg-assistant_msg { background: rgba(125,168,125,.12); border-left: 3px solid var(--warm-green); }
.msg-thought { background: rgba(181,139,196,.12); border-left: 3px solid var(--warm-purple); font-style: italic; }
.msg-other { background: rgba(107,98,96,.12); border-left: 3px solid #6b6260; color: var(--text-muted); }
.msg-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 3px; gap: 8px; }
.msg-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .4px; }
.msg-user_msg .msg-label { color: var(--warm-orange); }
.msg-assistant_msg .msg-label { color: var(--warm-green); }
.msg-thought .msg-label { color: var(--warm-purple); }
.msg-time { font-size: 10px; color: var(--text-muted); font-family: var(--font-mono); white-space: nowrap; }
.msg-body { word-break: break-word; white-space: pre-wrap; color: var(--text); }
.mood-tag { display: inline-block; font-size: 9px; padding: 1px 6px; border-radius: 3px; background: rgba(255,255,255,.06); color: var(--text-muted); margin-top: 4px; }
.stream-empty { text-align: center; color: var(--text-muted); font-size: 12px; padding: 30px 10px; }

/* scrollbar */
.body::-webkit-scrollbar, .stream::-webkit-scrollbar { width: 6px; }
.body::-webkit-scrollbar-track, .stream::-webkit-scrollbar-track { background: transparent; }
.body::-webkit-scrollbar-thumb, .stream::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
.body::-webkit-scrollbar-thumb:hover, .stream::-webkit-scrollbar-thumb:hover { background: var(--accent-dim); }
</style>
