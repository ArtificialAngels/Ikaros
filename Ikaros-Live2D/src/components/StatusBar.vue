<template>
  <div class="status-bar">{{ emoji }} {{ text }}</div>
</template>

<script setup lang="ts">
/**
 * StatusBar.vue — bottom status indicator.
 */
defineProps<{
  emoji: string
  text: string
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
}
</style>
