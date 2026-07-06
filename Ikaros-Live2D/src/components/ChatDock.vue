<template>
  <transition name="chat-dock">
    <div v-if="visible" class="chat-dock" :style="{ transform: `translateX(-50%) scale(${dockScale})`, transformOrigin: 'bottom center' }" @pointerdown.stop @pointerup.stop>
      <!-- Header -->
      <div class="chat-header">
        <span class="chat-title">💬 和伊卡洛斯聊天</span>
        <div class="chat-actions">
          <button class="chat-btn" @click="clearChat" title="清空对话">🗑️</button>
          <button class="chat-btn" @click="$emit('close')" title="关闭">✕</button>
        </div>
      </div>

      <!-- Messages -->
      <div class="chat-messages" ref="messagesRef">
        <div v-for="(msg, i) in messages" :key="i" class="chat-msg" :class="msg.role">
          <div class="msg-avatar">{{ msg.role === 'user' ? '👤' : '🪽' }}</div>
          <div class="msg-bubble">{{ msg.content }}</div>
        </div>
        <div v-if="loading" class="chat-msg assistant">
          <div class="msg-avatar">🪽</div>
          <div class="msg-bubble typing">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>

      <!-- Input -->
      <div class="chat-input-area">
        <input
          ref="inputRef"
          v-model="inputText"
          class="chat-input"
          placeholder="输入消息... (Enter发送)"
          @keydown.enter="sendMessage"
          :disabled="loading"
        />
        <button class="send-btn" @click="sendMessage" :disabled="loading || !inputText.trim()">
          {{ loading ? '...' : '➤' }}
        </button>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { ChatApi, type ChatMessage } from '../services/chat-api'

const props = defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  close: []
  reply: [text: string]
}>()

// Scale dock based on window width
const windowWidth = ref(window.innerWidth)
const dockScale = computed(() => Math.max(0.6, Math.min(1, windowWidth.value / 400)))

function updateWindowWidth() {
  windowWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', updateWindowWidth)
})
onUnmounted(() => {
  window.removeEventListener('resize', updateWindowWidth)
})

const messagesRef = ref<HTMLElement>()
const inputRef = ref<HTMLInputElement>()
const inputText = ref('')
const loading = ref(false)
const messages = ref<Array<{ role: 'user' | 'assistant'; content: string }>>([])

const chatApi = new ChatApi()

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

watch(() => props.visible, (v) => {
  if (v) {
    scrollToBottom()
    nextTick(() => inputRef.value?.focus())
  }
})

watch(messages, () => scrollToBottom(), { deep: true })

async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  loading.value = true
  scrollToBottom()

  try {
    const reply = await chatApi.chat(text)
    messages.value.push({ role: 'assistant', content: reply })
    // Emit reply so App.vue can show bubble + TTS
    emit('reply', reply)
  } catch (e) {
    messages.value.push({ role: 'assistant', content: '（出错了）' })
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

function clearChat() {
  messages.value = []
  chatApi.clearHistory()
}
</script>

<style scoped>
.chat-dock {
  position: fixed;
  bottom: 50px;
  left: 50%;
  transform: translateX(-50%);
  width: 360px;
  max-height: 450px;
  background: rgba(255, 255, 255, 0.97);
  border-radius: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  z-index: 500;
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
  overflow: hidden;
  backdrop-filter: blur(12px);
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  font-size: 13px;
  font-weight: 600;
}

.chat-actions {
  display: flex;
  gap: 4px;
}

.chat-btn {
  background: rgba(255, 255, 255, 0.2);
  border: none;
  color: white;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}
.chat-btn:hover {
  background: rgba(255, 255, 255, 0.35);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  max-height: 300px;
  min-height: 100px;
}

.chat-msg {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  align-items: flex-start;
}
.chat-msg.user {
  flex-direction: row-reverse;
}

.msg-avatar {
  font-size: 20px;
  flex-shrink: 0;
  width: 28px;
  text-align: center;
}

.msg-bubble {
  max-width: 240px;
  padding: 8px 12px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}
.chat-msg.user .msg-bubble {
  background: #667eea;
  color: white;
  border-bottom-right-radius: 4px;
}
.chat-msg.assistant .msg-bubble {
  background: #f0f0f5;
  color: #333;
  border-bottom-left-radius: 4px;
}

/* Typing indicator */
.typing {
  display: flex;
  gap: 4px;
  padding: 12px !important;
}
.typing span {
  width: 6px;
  height: 6px;
  background: #999;
  border-radius: 50%;
  animation: typing-bounce 1.4s infinite ease-in-out;
}
.typing span:nth-child(2) { animation-delay: 0.2s; }
.typing span:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-4px); }
}

.chat-input-area {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid #eee;
  background: rgba(255, 255, 255, 0.95);
}

.chat-input {
  flex: 1;
  border: 1px solid #ddd;
  border-radius: 10px;
  padding: 8px 12px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.2s;
}
.chat-input:focus {
  border-color: #667eea;
}

.send-btn {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border: none;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  cursor: pointer;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: opacity 0.2s;
}
.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Transition */
.chat-dock-enter-active { transition: all 0.3s ease; }
.chat-dock-leave-active { transition: all 0.2s ease; }
.chat-dock-enter-from { opacity: 0; transform: translateX(-50%) translateY(20px); }
.chat-dock-leave-to { opacity: 0; transform: translateX(-50%) translateY(10px); }
</style>
