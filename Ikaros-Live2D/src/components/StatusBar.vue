<template>
  <div class="status-bar">
    <div class="status-main">{{ emoji }} {{ text }}</div>
    <div v-if="activity" class="status-activity">{{ activity }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * StatusBar.vue — bottom status indicator.
 * `activity` 副行: 实时显示哥哥当前前台窗口/程序 (由 voice-ws 推 {type:activity}).
 */
defineProps<{
  emoji: string
  text: string
  activity?: string
}>()
</script>

<style scoped>
.status-bar {
  position: absolute;
  bottom: 20px;
  background: rgba(0, 0, 0, 0.3);
  color: rgba(255, 255, 255, 0.7);
  padding: 4px 14px;
  border-radius: 12px;
  font-size: 13px;
  font-family: -apple-system, 'Segoe UI', sans-serif;
  backdrop-filter: blur(4px);
  pointer-events: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  max-width: 92vw;
}
.status-main {
  white-space: nowrap;
}
.status-activity {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.55);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
</style>
