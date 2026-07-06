/**
 * useWindowDrag — distinguishes click vs drag using time/distance thresholds.
 * Window dragging is handled by CSS `-webkit-app-region: drag` on the container.
 * This composable only handles hit detection on click (not drag).
 */
import { ref, type Ref } from 'vue'

const CLICK_THRESHOLD_MS = 300
const CLICK_THRESHOLD_PX = 8

export function useWindowDrag(
  live2dRef: Ref<{ hitTest: (x: number, y: number) => string[]; playMotion: (g: string, i: number) => void } | null>,
) {
  const isDragging = ref(false)
  let downX = 0
  let downY = 0
  let downTime = 0

  function onPointerDown(e: PointerEvent) {
    downX = e.screenX
    downY = e.screenY
    downTime = Date.now()
    isDragging.value = false
  }

  function onPointerMove(e: PointerEvent) {
    if (downTime === 0) return
    const dx = e.screenX - downX
    const dy = e.screenY - downY
    if (Math.abs(dx) > CLICK_THRESHOLD_PX || Math.abs(dy) > CLICK_THRESHOLD_PX) {
      isDragging.value = true
    }
  }

  function onPointerUp(e: PointerEvent) {
    const elapsed = Date.now() - downTime
    const dx = Math.abs(e.screenX - downX)
    const dy = Math.abs(e.screenY - downY)
    downTime = 0

    if (!isDragging.value && elapsed < CLICK_THRESHOLD_MS && dx < CLICK_THRESHOLD_PX && dy < CLICK_THRESHOLD_PX) {
      // Click — do hit test
      if (live2dRef.value) {
        const areas = live2dRef.value.hitTest(e.clientX, e.clientY)
        if (areas.includes('Body') || areas.includes('HitArea')) {
          live2dRef.value.playMotion('Tap', Math.floor(Math.random() * 2))
        }
      }
    }
    isDragging.value = false
  }

  function bindEvents(el: HTMLElement) {
    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('pointermove', onPointerMove)
    el.addEventListener('pointerup', onPointerUp)
  }

  function unbindEvents(el: HTMLElement) {
    el.removeEventListener('pointerdown', onPointerDown)
    el.removeEventListener('pointermove', onPointerMove)
    el.removeEventListener('pointerup', onPointerUp)
  }

  return { isDragging, bindEvents, unbindEvents }
}
