/**
 * useLive2D composable — Vue 3 wrapper around Live2DAdapter.
 */
import { ref, type Ref } from 'vue'
import { Live2DAdapter } from '../services/live2d-adapter'

export function useLive2D() {
  const modelLoaded = ref(false)
  const adapter: Ref<Live2DAdapter | null> = ref(null)

  // Buffer for model list (set before adapter is ready)
  let _pendingModelList: { name: string; path: string }[] | null = null
  let _pendingModelIndex = 0

  async function init(canvasEl: HTMLCanvasElement, modelPath?: string): Promise<void> {
    const a = new Live2DAdapter()
    await a.init(canvasEl)

    const path = modelPath || '/live2d/hiyori_free_t08/hiyori_free_t08.model3.json'
    await a.loadModel(path)

    adapter.value = a
    modelLoaded.value = true

    // Apply pending model list if it was set before adapter was ready
    if (_pendingModelList) {
      a.setModelList(_pendingModelList, _pendingModelIndex)
      _pendingModelList = null
    }

    console.log('[useLive2D] initialized')
  }

  function playMotion(group: string, index: number): void {
    adapter.value?.playMotion(group, index)
  }

  function setParam(name: string, value: number): void {
    adapter.value?.setParam(name, value)
  }

  function hitTest(x: number, y: number): string[] {
    return adapter.value?.hitTest(x, y) || []
  }

  function updateTracking(x: number, y: number): void {
    adapter.value?.updateTracking(x, y)
  }

  function screenshot(): string | null {
    return adapter.value?.screenshot() || null
  }

  function resize(w: number, h: number): void {
    adapter.value?.resize(w, h)
  }

  function destroy(): void {
    adapter.value?.destroy()
    adapter.value = null
    modelLoaded.value = false
  }

  // Model list management
  function setModelList(list: { name: string; path: string }[], currentIndex = 0): void {
    if (adapter.value) {
      adapter.value.setModelList(list, currentIndex)
    } else {
      // Buffer the list until adapter is ready
      _pendingModelList = list
      _pendingModelIndex = currentIndex
    }
  }

  function getAllModelNames(): string[] {
    return adapter.value?.getAllModelNames() || []
  }

  function getCurrentModelName(): string {
    return adapter.value?.getCurrentModelName() || 'Unknown'
  }

  function getCurrentModelIndex(): number {
    return adapter.value?.getCurrentModelIndex() ?? -1
  }

  function getModelCount(): number {
    return adapter.value?.getModelCount() ?? 0
  }

  async function nextModel(): Promise<void> {
    await adapter.value?.nextModel()
  }

  async function prevModel(): Promise<void> {
    await adapter.value?.prevModel()
  }

  async function switchToModel(index: number): Promise<void> {
    await adapter.value?.switchToModel(index)
  }

  function nextTexture(): void {
    adapter.value?.nextTexture()
  }

  function setModelScale(multiplier: number): void {
    adapter.value?.setModelScale(multiplier)
  }

  function getModelScale(): number {
    return adapter.value?.getModelScale() ?? 1.0
  }

  function toggleHitFrames(): boolean {
    return adapter.value?.toggleHitFrames() ?? false
  }

  function isHitFramesVisible(): boolean {
    return adapter.value?.isHitFramesVisible() ?? false
  }

  // Expression system
  function setExpression(name: string): void {
    adapter.value?.setExpression(name)
  }

  function revertExpression(): void {
    adapter.value?.revertExpression()
  }

  function getExpressions(): string[] {
    return adapter.value?.getExpressions() || []
  }

  return {
    modelLoaded,
    adapter,
    init,
    playMotion,
    setParam,
    hitTest,
    updateTracking,
    screenshot,
    resize,
    destroy,
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
  }
}
