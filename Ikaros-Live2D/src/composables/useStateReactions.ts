/**
 * useStateReactions — maps WebSocket states to Live2D motions/params.
 */
import { watch, type Ref } from 'vue'
import type { Live2DAdapter } from '../services/live2d-adapter'

interface StateReaction {
  motionGroup: string
  motionIndex: number
  paramOverrides: Record<string, number>
  enableLipSync: boolean
  cssFilter?: string
}

const STATE_REACTIONS: Record<string, StateReaction> = {
  idle: {
    motionGroup: 'Idle',
    motionIndex: 0,
    paramOverrides: {},
    enableLipSync: false,
  },
  listening: {
    motionGroup: 'Idle',
    motionIndex: 1,
    paramOverrides: { ParamEyeLOpen: 1, ParamEyeROpen: 1 },
    enableLipSync: false,
  },
  thinking: {
    motionGroup: 'Sleepy',
    motionIndex: 0,
    paramOverrides: { ParamAngleZ: -5, ParamBodyAngleX: -8 },
    enableLipSync: false,
  },
  speaking: {
    motionGroup: 'Tap',
    motionIndex: 0,
    paramOverrides: {},
    enableLipSync: true,
  },
  happy: {
    motionGroup: 'Happy',
    motionIndex: 0,
    paramOverrides: { ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamCheek: 1 },
    enableLipSync: false,
  },
  sleepy: {
    motionGroup: 'Sleepy',
    motionIndex: 1,
    paramOverrides: { ParamEyeLOpen: 0.3, ParamEyeROpen: 0.3, ParamBreath: 0.8 },
    enableLipSync: false,
    cssFilter: 'brightness(0.7) saturate(0.6)',
  },
}

export function useStateReactions(
  state: Ref<string>,
  adapter: Ref<Live2DAdapter | null>,
  containerEl: Ref<HTMLElement | null>,
) {
  let currentReaction: StateReaction | null = null

  function applyReaction(reaction: StateReaction) {
    if (!adapter.value) return

    // Play motion
    adapter.value.playMotion(reaction.motionGroup, reaction.motionIndex)

    // Apply param overrides (these persist until next state change)
    for (const [name, value] of Object.entries(reaction.paramOverrides)) {
      adapter.value.setParam(name, value)
    }

    // Apply CSS filter to container
    if (containerEl.value) {
      containerEl.value.style.filter = reaction.cssFilter || ''
    }

    currentReaction = reaction
  }

  function clearReaction() {
    if (!adapter.value || !currentReaction) return
    // Reset overridden params to defaults
    for (const name of Object.keys(currentReaction.paramOverrides)) {
      adapter.value.setParam(name, 0)
    }
    if (containerEl.value) {
      containerEl.value.style.filter = ''
    }
    currentReaction = null
  }

  // Watch state changes and apply reactions
  watch(state, (newState) => {
    clearReaction()
    const reaction = STATE_REACTIONS[newState]
    if (reaction) {
      // Small delay to let previous motion finish
      setTimeout(() => applyReaction(reaction), 100)
    }
  })

  return {
    isLipSyncEnabled: () => currentReaction?.enableLipSync ?? false,
    clearReaction,
  }
}
