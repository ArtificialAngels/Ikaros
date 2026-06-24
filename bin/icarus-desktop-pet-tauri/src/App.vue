<template>
  <div class="pet-container" :class="stateClass">
    <!-- Character display -->
    <div class="character-wrapper" @mousedown="startDrag" @touchstart.prevent="startDragTouch">
      <img :src="characterImage" class="character" :class="stateClass" draggable="false" />
    </div>
    <!-- Speech bubble -->
    <transition name="bubble">
      <div v-if="bubbleText" class="speech-bubble">{{ bubbleText }}</div>
    </transition>
    <!-- Status indicator -->
    <div class="status-bar">{{ statusEmoji }} {{ statusText }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { invoke } from '@tauri-apps/api/core';

// ─── State ───
const STATES = {
  idle:     { emoji: '🪽', text: '待机', cls: 'idle' },
  listening:{ emoji: '🎤', text: '聆听', cls: 'listening' },
  thinking: { emoji: '🧠', text: '思考', cls: 'thinking' },
  speaking: { emoji: '🔊', text: '说话', cls: 'speaking' },
  happy:    { emoji: '😊', text: '开心', cls: 'happy' },
  sleepy:   { emoji: '💤', text: '休息', cls: 'sleepy' },
};

const state = ref('idle');
const bubbleText = ref('');
const bubbleTimer = ref(null);

const characterImage = ref('/assets/character.png'); // Will use the reference image

const stateClass = computed(() => STATES[state.value]?.cls || 'idle');
const statusEmoji = computed(() => STATES[state.value]?.emoji || '🪽');
const statusText = computed(() => STATES[state.value]?.text || '待机');

// ─── Drag (window movement) ───
function startDrag(e) {
  invoke('drag_window');
}

function startDragTouch(e) {
  if (e.touches.length === 1) {
    invoke('drag_window');
  }
}

// ─── Show speech bubble ───
function showBubble(text, duration = 3000) {
  bubbleText.value = text;
  if (bubbleTimer.value) clearTimeout(bubbleTimer.value);
  if (duration > 0) {
    bubbleTimer.value = setTimeout(() => { bubbleText.value = ''; }, duration);
  }
}

function setState(newState) {
  state.value = newState;
}

// ─── WebSocket to Python Audio Engine ───
let ws = null;

function connectWebSocket() {
  ws = new WebSocket('ws://127.0.0.1:7860/v1/voice/ws');
  ws.onopen = () => {
    ws.send(JSON.stringify({ action: 'start', session_id: 'icarus_tauri' }));
    setState('listening');
  };
  ws.onmessage = (event) => {
    if (event.data instanceof Blob) return; // TTS audio
    try {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case 'transcription':
          showBubble(`👤 ${msg.text}`, 3000);
          break;
        case 'thinking':
          setState('thinking');
          break;
        case 'status':
          showBubble(msg.message, 2000);
          break;
        case 'done':
          showBubble(msg.text, 5000);
          setState('speaking');
          setTimeout(() => setState('listening'), 2000);
          break;
      }
    } catch (e) {}
  };
  ws.onclose = () => setTimeout(connectWebSocket, 3000);
}

// ─── Expose to Tauri ───
onMounted(() => {
  // Expose API for Tauri to call
  window.__ICARUS_API__ = { setState, showBubble };
  // Connect WebSocket
  connectWebSocket();
});

onUnmounted(() => {
  if (ws) ws.close();
  if (bubbleTimer.value) clearTimeout(bubbleTimer.value);
});
</script>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }

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
  cursor: grab;
}
.pet-container:active { cursor: grabbing; }

.character-wrapper {
  width: 300px;
  height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.character {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  transition: transform 0.3s ease, filter 0.3s ease;
  animation: float 3s ease-in-out infinite;
  pointer-events: none;
}

.character.listening {
  animation: float-listening 1.5s ease-in-out infinite;
  filter: brightness(1.1);
}

.character.thinking {
  animation: none;
  transform: scale(0.98);
  filter: brightness(0.95) saturate(0.8);
}

.character.speaking {
  animation: speak 0.4s ease-in-out infinite alternate;
}

.character.sleepy {
  animation: none;
  filter: brightness(0.6) saturate(0.5);
  transform: scale(0.95);
}

@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-6px); }
}

@keyframes float-listening {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}

@keyframes speak {
  0% { transform: translateY(0) scaleY(1); }
  100% { transform: translateY(-2px) scaleY(1.02); }
}

/* Status bar */
.status-bar {
  position: absolute;
  bottom: 20px;
  background: rgba(0,0,0,0.3);
  color: rgba(255,255,255,0.7);
  padding: 4px 14px;
  border-radius: 12px;
  font-size: 13px;
  font-family: -apple-system, 'Segoe UI', sans-serif;
  backdrop-filter: blur(4px);
  pointer-events: none;
}

/* Speech bubble */
.speech-bubble {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255,255,255,0.95);
  color: #222;
  padding: 10px 16px;
  border-radius: 14px;
  font-size: 13px;
  max-width: 280px;
  line-height: 1.4;
  box-shadow: 0 3px 15px rgba(0,0,0,0.15);
  backdrop-filter: blur(8px);
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
}

.bubble-enter-active { transition: all 0.3s ease; }
.bubble-leave-active { transition: all 0.2s ease; }
.bubble-enter-from { opacity: 0; transform: translateX(-50%) translateY(10px); }
.bubble-leave-to { opacity: 0; transform: translateX(-50%) translateY(-10px); }
</style>
