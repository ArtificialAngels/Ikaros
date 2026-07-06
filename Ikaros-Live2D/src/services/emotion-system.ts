/**
 * emotion-system.ts — Emotion engine for Ikaros Desktop Pet.
 *
 * Inspired by Live2DPet's EmotionSystem.
 * Emotions auto-decay over time and are triggered by external events
 * (WebSocket done, idle timeout, user interaction).
 * Current emotion influences chat tone via prompt injection.
 */

export type Emotion =
  | 'neutral'
  | 'happy'
  | 'sad'
  | 'surprised'
  | 'angry'
  | 'shy'
  | 'sleepy'
  | 'excited'
  | 'curious'

export interface EmotionState {
  current: Emotion
  intensity: number    // 0.0 - 1.0
  decayRate: number    // points per second
  lastTrigger: number  // timestamp
}

interface EmotionConfig {
  decayRate: number
  minIntensity: number
  maxIntensity: number
}

type EmotionCallback = (emotion: Emotion, intensity: number) => void

const DEFAULT_CONFIG: EmotionConfig = {
  decayRate: 0.02,      // 2% per second
  minIntensity: 0.0,
  maxIntensity: 1.0,
}

// Emotion -> tone hint for system prompt
const EMOTION_TONE: Record<Emotion, string> = {
  neutral:   '用平常的语气说话',
  happy:     '用开心活泼的语气说话，适当使用感叹号和语气词',
  sad:       '用略带忧郁、温柔的语气说话',
  surprised: '用惊讶、好奇的语气说话',
  angry:     '用略带不满但克制的语气说话',
  shy:       '用害羞、轻声细语的语气说话',
  sleepy:    '用慵懒、犯困的语气说话，适当打哈欠',
  excited:   '用兴奋、充满活力的语气说话',
  curious:   '用好奇、探究的语气说话',
}

export function getEmotionTone(emotion: Emotion): string {
  return EMOTION_TONE[emotion]
}

export class EmotionSystem {
  private _state: EmotionState = {
    current: 'neutral',
    intensity: 0.0,
    decayRate: DEFAULT_CONFIG.decayRate,
    lastTrigger: Date.now() / 1000,
  }
  private _callback: EmotionCallback | null = null
  private _intervalId: ReturnType<typeof setInterval> | null = null
  private _running = false
  private _config: EmotionConfig = { ...DEFAULT_CONFIG }
  private _blocked = false // block emotion changes (e.g. during expression)

  constructor(callback?: EmotionCallback) {
    this._callback = callback ?? null
  }

  /** Start the decay loop (every 500ms). */
  start(): void {
    if (this._running) return
    this._running = true
    this._intervalId = setInterval(() => this._tick(), 500)
    console.log('[EmotionSystem] started, decay rate:', this._config.decayRate)
  }

  /** Stop the decay loop. */
  stop(): void {
    this._running = false
    if (this._intervalId !== null) {
      clearInterval(this._intervalId)
      this._intervalId = null
    }
  }

  /** Block emotion from changing (e.g. during manual expression). */
  block(): void { this._blocked = true }
  unblock(): void { this._blocked = false }

  get currentEmotion(): Emotion {
    return this._state.current
  }

  get intensity(): number {
    return this._state.intensity
  }

  get isBlocked(): boolean {
    return this._blocked
  }

  /**
   * Trigger an emotion with given intensity boost.
   * The triggered emotion becomes current (if intensity > current).
   */
  trigger(emotion: Emotion, boost = 0.5): void {
    if (this._blocked) return

    const prevEmotion = this._state.current
    const prevIntensity = this._state.intensity

    // If already this emotion, boost intensity
    if (this._state.current === emotion) {
      this._state.intensity = Math.min(
        this._config.maxIntensity,
        this._state.intensity + boost
      )
    } else {
      // Switch with boost (only if boost > current intensity)
      const newIntensity = Math.min(this._config.maxIntensity, boost)
      if (newIntensity > this._state.intensity + 0.1) {
        this._state.current = emotion
        this._state.intensity = newIntensity
      }
    }

    this._state.lastTrigger = Date.now() / 1000

    if (this._state.current !== prevEmotion || Math.abs(this._state.intensity - prevIntensity) > 0.05) {
      this._emit()
    }
  }

  /** Force set emotion (bypass intensity check). */
  force(emotion: Emotion, intensity = 0.8): void {
    this._state.current = emotion
    this._state.intensity = Math.min(this._config.maxIntensity, intensity)
    this._state.lastTrigger = Date.now() / 1000
    this._emit()
  }

  /** Reset to neutral. */
  reset(): void {
    this._state.current = 'neutral'
    this._state.intensity = 0.0
    this._emit()
  }

  /** Update config. */
  setConfig(config: Partial<EmotionConfig>): void {
    if (config.decayRate !== undefined) {
      this._config.decayRate = Math.max(0.001, Math.min(0.1, config.decayRate))
      this._state.decayRate = this._config.decayRate
    }
    if (config.minIntensity !== undefined) this._config.minIntensity = config.minIntensity
    if (config.maxIntensity !== undefined) this._config.maxIntensity = config.maxIntensity
  }

  /** Get current tone hint for LLM prompt injection. */
  get toneHint(): string {
    if (this._state.intensity < 0.15) return ''
    return getEmotionTone(this._state.current)
  }

  // ─── Private ───

  private _tick(): void {
    if (!this._running) return
    const now = Date.now() / 1000
    const elapsed = now - this._state.lastTrigger

    // Decay: reduce intensity over time
    if (this._state.intensity > this._config.minIntensity) {
      this._state.intensity = Math.max(
        this._config.minIntensity,
        this._state.intensity - this._config.decayRate * (elapsed * 2) // ×2 because 500ms tick
      )

      // If intensity dropped below threshold, revert to neutral
      if (this._state.intensity < 0.05 && this._state.current !== 'neutral') {
        this._state.current = 'neutral'
        this._state.intensity = 0.0
        this._emit()
      }
    }

    this._state.lastTrigger = now
  }

  private _emit(): void {
    if (this._callback) {
      try {
        this._callback(this._state.current, this._state.intensity)
      } catch (e) {
        console.debug('[EmotionSystem] callback error:', e)
      }
    }
  }
}
