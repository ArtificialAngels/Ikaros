<template>
  <div class="chat-frame fade-out" v-show="visible">
    <img class="frame-bg" v-if="frameImagePath" :src="frameImagePath" alt="" />
    <div class="message" :class="{ 'text-in': textIn }">{{ displayMessage }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * PetChatBubble.vue -- Ikaros Live2D 桌宠聊天气泡组件.
 *
 * 抽 Live2DPet:src/renderer/pet-chat-bubble.js (83 行, 2026-06-12 真物).
 * 镜像模式:
 *   - showMessage(msg, autoCloseTime) -> 显示气泡 + N ms 后 fade-out
 *   - auto-close 定时器 (默认 8000ms)
 *   - textIn CSS class 防重影 (镜像 requestAnimationFrame 等下一帧再加 class)
 *   - 失败静默: frameImagePath 不存在就 hide bg
 *
 * 不重复发明: App.vue 已有 <speech-bubble> + drag + status 6 态,
 * 这个组件独立, 接收外部 message 通过 v-model:message
 * (Vue 3 v-model 是父 -> 子 双向绑定的现代写法).
 */
import { ref, watch, onUnmounted } from 'vue'

const props = defineProps<{
  /** 父传子 message (default 8s auto close). */
  message: string
  /** 配置文件里气泡 frameImagePath (optional). */
  frameImagePath?: string
}>()

const visible = ref(false)
const textIn = ref(false)
let autoCloseTimer: number | null = null
let fadeTimer: number | null = null

const displayMessage = ref('')

function clearTimers() {
  if (autoCloseTimer !== null) {
    clearTimeout(autoCloseTimer)
    autoCloseTimer = null
  }
  if (fadeTimer !== null) {
    clearTimeout(fadeTimer)
    fadeTimer = null
  }
}

/**
 * showMessage(msg, autoCloseTime) -- 镜像 Live2DPet:pet-chat-bubble.js:39.
 * autoCloseTime <= 0 表示不自动关 (parent 自管).
 */
function showMessage(msg: string, autoCloseTime = 8000) {
  clearTimers()
  displayMessage.value = msg
  visible.value = true
  textIn.value = false
  // requestAnimationFrame 防 class 重复触发, 镜像 Live2DPet 真物
  requestAnimationFrame(() => {
    textIn.value = true
  })
  if (autoCloseTime > 0) {
    autoCloseTimer = window.setTimeout(() => fadeOut(), autoCloseTime)
  }
}

function fadeOut() {
  clearTimers()
  textIn.value = false
  // 300ms fade-out 后 hide, 镜像 Live2DPet fade-out 延迟
  fadeTimer = window.setTimeout(() => {
    visible.value = false
    displayMessage.value = ''
  }, 300)
}

defineExpose({ showMessage, fadeOut })

// 父传子 message 变化时, 触发 showMessage (Vue 3 idiom).
watch(() => props.message, (newMsg) => {
  if (newMsg) {
    showMessage(newMsg, 8000)
  }
})

onUnmounted(() => {
  clearTimers()
})
</script>

<style scoped>
/* 镜像 Live2DPet:chat-frame 默认样式 + fade-out/text-in CSS classes. */
.chat-frame {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  transition: opacity 0.3s ease-in-out;
  opacity: 1;
}
.chat-frame.fade-out {
  opacity: 0;
}
.frame-bg {
  max-width: 300px;
  max-height: 160px;
  object-fit: contain;
}
.message {
  position: absolute;
  padding: 0.5em 0.8em;
  font-size: 14px;
  line-height: 1.4;
  max-width: 240px;
  color: var(--bubble-text, #fff);
  text-align: center;
  opacity: 0;
  transition: opacity 0.25s ease-out;
}
.message.text-in {
  opacity: 1;
}
</style>
