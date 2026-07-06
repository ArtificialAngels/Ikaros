<template>
  <div class="floatball-wrapper" ref="ballRef"
    @pointerdown="onPointerDown"
    @contextmenu.prevent="onRightClick">
    <!-- Outer glow ring -->
    <div class="floatball-glow"></div>
    <!-- Avatar circle -->
    <div class="floatball-avatar">
      <slot>
        <span class="floatball-emoji">🪽</span>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * FloatBall.vue — Circular floating ball mode for desktop pet.
 * Uses Tauri's native window.start_dragging() for instant, zero-lag drag.
 * Double-click to restore full window, right-click for context.
 *
 * Inspired by MewCo-AI's float ball feature.
 */
import { ref } from 'vue'

const emit = defineEmits<{
  (e: 'restore'): void
  (e: 'context-menu', pos: { x: number; y: number }): void
}>()

const ballRef = ref<HTMLElement>()

// ── Double-click detection ──
let lastClickTime = 0
let clickTimer: ReturnType<typeof setTimeout> | null = null

// ── Tauri invoke cache ──
let _invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null

async function getInvoke() {
  if (!_invoke) {
    try {
      const mod = await import('@tauri-apps/api/core')
      _invoke = mod.invoke
    } catch { /* not in Tauri */ }
  }
  return _invoke
}

async function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return // left button only

  // Double-click detection (300ms threshold)
  const now = Date.now()
  if (now - lastClickTime < 350) {
    // Double click — restore to full window
    lastClickTime = 0
    if (clickTimer) { clearTimeout(clickTimer); clickTimer = null }
    emit('restore')
    return
  }
  lastClickTime = now

  // Reset after 350ms if no second click
  if (clickTimer) clearTimeout(clickTimer)
  clickTimer = setTimeout(() => { lastClickTime = 0; clickTimer = null }, 350)

  // Start Tauri native window drag (OS-level, zero IPC latency per frame)
  try {
    const invoke = await getInvoke()
    if (invoke) {
      await invoke('drag_window')
    }
  } catch { /* ignore */ }
}

function onRestore() {
  emit('restore')
}

function onRightClick(e: MouseEvent) {
  emit('context-menu', { x: e.clientX, y: e.clientY })
}
</script>

<style scoped>
.floatball-wrapper {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  user-select: none;
  cursor: grab;
  -webkit-app-region: no-drag;
  position: relative;
}

.floatball-wrapper:active {
  cursor: grabbing;
}

/* Pulsing glow ring behind the ball */
.floatball-glow {
  position: absolute;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(102, 204, 255, 0.15) 0%, transparent 70%);
  animation: ball-pulse 2.5s ease-in-out infinite;
  pointer-events: none;
}

@keyframes ball-pulse {
  0%, 100% { transform: scale(1); opacity: 0.5; }
  50% { transform: scale(1.15); opacity: 0.8; }
}

.floatball-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: rgba(102, 204, 255, 0.25);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border: 2px solid rgba(255, 255, 255, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  box-shadow:
    0 0 16px rgba(102, 204, 255, 0.25),
    0 4px 20px rgba(0, 0, 0, 0.3);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  z-index: 1;
  pointer-events: none; /* let wrapper handle all pointer events */
}

.floatball-wrapper:hover .floatball-avatar {
  transform: scale(1.08);
  box-shadow:
    0 0 24px rgba(102, 204, 255, 0.4),
    0 6px 24px rgba(0, 0, 0, 0.35);
}

.floatball-wrapper:active .floatball-avatar {
  transform: scale(0.95);
}

.floatball-emoji {
  font-size: 30px;
  line-height: 1;
  filter: drop-shadow(0 0 4px rgba(255, 255, 255, 0.5));
}
</style>
