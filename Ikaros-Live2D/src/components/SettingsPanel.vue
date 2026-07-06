<template>
  <Teleport to="body">
    <div v-if="visible" class="settings-overlay" @click.self="close">
      <div class="settings-panel" :style="{ transform: `scale(${panelScale})`, transformOrigin: 'center center' }">
        <div class="settings-header">
          <span>设置</span>
          <button class="close-btn" @click="close">✕</button>
        </div>

        <div class="settings-body">
          <!-- Model scale -->
          <div class="setting-group">
            <label>模型缩放</label>
            <div class="slider-row">
              <input type="range" min="0.3" max="2.0" step="0.1" v-model.number="localConfig.scale" @change="save" />
              <span class="value">{{ localConfig.scale.toFixed(1) }}x</span>
            </div>
          </div>

          <!-- Tracking sensitivity -->
          <div class="setting-group">
            <label>鼠标追踪灵敏度</label>
            <div class="slider-row">
              <input type="range" min="0" max="2.0" step="0.1" v-model.number="localConfig.trackingSensitivity" @change="save" />
              <span class="value">{{ localConfig.trackingSensitivity.toFixed(1) }}</span>
            </div>
          </div>

          <!-- Lip sync -->
          <div class="setting-group">
            <label>口型同步</label>
            <div class="toggle-row">
              <label class="toggle">
                <input type="checkbox" v-model="localConfig.lipSyncEnabled" @change="save" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- Lip sync sensitivity -->
          <div class="setting-group" v-if="localConfig.lipSyncEnabled">
            <label>口型幅度</label>
            <div class="slider-row">
              <input type="range" min="0.2" max="2.0" step="0.1" v-model.number="localConfig.lipSyncSensitivity" @change="save" />
              <span class="value">{{ localConfig.lipSyncSensitivity.toFixed(1) }}</span>
            </div>
          </div>

          <!-- PATIENCE -->
          <div class="setting-group">
            <label>主动说话间隔 (秒)</label>
            <div class="slider-row">
              <input type="range" min="15" max="300" step="5" v-model.number="localConfig.patienceSeconds" @change="save" />
              <span class="value">{{ localConfig.patienceSeconds }}s</span>
            </div>
          </div>

          <!-- Emotion toggle -->
          <div class="setting-group">
            <label>情绪系统</label>
            <div class="toggle-row">
              <label class="toggle">
                <input type="checkbox" v-model="localConfig.emotionEnabled" @change="save" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- Emotion decay rate -->
          <div class="setting-group" v-if="localConfig.emotionEnabled">
            <label>情绪衰减速度</label>
            <div class="slider-row">
              <input type="range" min="0.005" max="0.1" step="0.005" v-model.number="localConfig.emotionDecayRate" @change="save" />
              <span class="value">{{ (localConfig.emotionDecayRate * 100).toFixed(1) }}%/s</span>
            </div>
          </div>

          <!-- Anti-repetition toggle -->
          <div class="setting-group">
            <label>防重复检测</label>
            <div class="toggle-row">
              <label class="toggle">
                <input type="checkbox" v-model="localConfig.antiRepEnabled" @change="save" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- VLM toggle -->
          <div class="setting-group">
            <label>VLM 截屏分析</label>
            <div class="toggle-row">
              <label class="toggle">
                <input type="checkbox" v-model="localConfig.vlmEnabled" @change="save" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>

          <!-- VLM interval -->
          <div class="setting-group" v-if="localConfig.vlmEnabled">
            <label>截屏间隔 (秒)</label>
            <div class="slider-row">
              <input type="range" min="15" max="120" step="5" v-model.number="localConfig.vlmInterval" @change="save" />
              <span class="value">{{ (localConfig.vlmInterval / 1000).toFixed(0) }}s</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * SettingsPanel.vue — pet configuration panel.
 */
import { reactive, watch, computed, onMounted, onUnmounted, ref } from 'vue'
import type { PetConfig } from '../services/pet-config'
import { saveConfig } from '../services/pet-config'

const props = defineProps<{
  visible: boolean
  config: PetConfig
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:config', config: PetConfig): void
}>()

const localConfig = reactive<PetConfig>({ ...props.config })

// Scale panel based on window size
const windowWidth = ref(window.innerWidth)
const panelScale = computed(() => Math.max(0.6, Math.min(1, windowWidth.value / 400)))

function updateWindowWidth() {
  windowWidth.value = window.innerWidth
}

onMounted(() => {
  window.addEventListener('resize', updateWindowWidth)
})
onUnmounted(() => {
  window.removeEventListener('resize', updateWindowWidth)
})

// Sync from parent
watch(() => props.config, (newConfig) => {
  Object.assign(localConfig, newConfig)
}, { deep: true })

function save() {
  saveConfig(localConfig)
  emit('update:config', { ...localConfig })
}

function close() {
  emit('close')
}
</script>

<style scoped>
.settings-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0, 0, 0, 0.5);
  z-index: 9998;
  display: flex;
  align-items: center;
  justify-content: center;
  -webkit-app-region: no-drag;
}

.settings-panel {
  background: rgba(25, 25, 35, 0.98);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  width: 320px;
  max-height: 80vh;
  overflow-y: auto;
  backdrop-filter: blur(16px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
  color: rgba(255, 255, 255, 0.9);
  -webkit-app-region: no-drag;
}

.settings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  font-size: 15px;
  font-weight: 600;
}

.close-btn {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.5);
  cursor: pointer;
  font-size: 16px;
  padding: 4px;
}

.close-btn:hover {
  color: white;
}

.settings-body {
  padding: 12px 16px;
}

.setting-group {
  margin-bottom: 16px;
}

.setting-group > label {
  display: block;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.slider-row input[type="range"] {
  flex: 1;
  height: 4px;
  -webkit-appearance: none;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 2px;
  outline: none;
}

.slider-row input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #6cf;
  cursor: pointer;
}

.value {
  font-size: 13px;
  color: #6cf;
  min-width: 36px;
  text-align: right;
}

.toggle-row {
  display: flex;
  align-items: center;
}

.toggle {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 22px;
}

.toggle input {
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.2);
  border-radius: 11px;
  transition: 0.3s;
}

.toggle-slider:before {
  content: '';
  position: absolute;
  height: 16px;
  width: 16px;
  left: 3px;
  bottom: 3px;
  background: white;
  border-radius: 50%;
  transition: 0.3s;
}

.toggle input:checked + .toggle-slider {
  background: #6cf;
}

.toggle input:checked + .toggle-slider:before {
  transform: translateX(18px);
}
</style>
