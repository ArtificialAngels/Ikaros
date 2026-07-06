<template>
  <div class="live2d-wrapper" ref="wrapperRef">
    <canvas ref="canvasRef" class="live2d-canvas"></canvas>
  </div>
</template>

<script setup lang="ts">
/**
 * Live2DCanvas.vue — PixiJS + Live2D rendering container.
 * Exposes init/resize to parent via defineExpose.
 */
import { ref, onMounted, onUnmounted } from 'vue'
import { useLive2D } from '../composables/useLive2D'

const wrapperRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLCanvasElement>()

const { modelLoaded, adapter, init, resize: adapterResize, destroy, updateTracking, playMotion, hitTest, setParam, screenshot,
  setModelList, getAllModelNames, getCurrentModelName, getCurrentModelIndex, getModelCount,
  nextModel, prevModel, switchToModel, nextTexture, setModelScale, getModelScale, toggleHitFrames, isHitFramesVisible,
  setExpression, revertExpression, getExpressions
} = useLive2D()

onMounted(async () => {
  if (!canvasRef.value) return
  try {
    await init(canvasRef.value)
  } catch (e) {
    console.error('[Live2DCanvas] init failed:', e)
  }
})

onUnmounted(() => {
  destroy()
})

// Handle window resize
function handleResize() {
  if (wrapperRef.value) {
    adapterResize(window.innerWidth, window.innerHeight)
  }
}

window.addEventListener('resize', handleResize)
onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

defineExpose({
  modelLoaded,
  adapter,
  updateTracking,
  playMotion,
  hitTest,
  setParam,
  screenshot,
  resize: adapterResize,
  handleResize,
  // Model list
  setModelList,
  getAllModelNames,
  getCurrentModelName,
  getCurrentModelIndex,
  getModelCount,
  nextModel,
  prevModel,
  switchToModel,
  nextTexture,
  setModelScale,
  getModelScale,
  toggleHitFrames,
  isHitFramesVisible,
  // Expression
  setExpression,
  revertExpression,
  getExpressions,
})
</script>

<style scoped>
.live2d-wrapper {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: transparent;
  pointer-events: auto;
  -webkit-app-region: no-drag;
}

.live2d-canvas {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: transparent;
}
</style>
