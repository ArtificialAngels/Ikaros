/**
 * neuro.ts — PATIENCE-based proactive speech system.
 *
 * Monitors interaction activity. If no interaction happens for `patience` seconds,
 * triggers a "bored" state so the pet proactively says something.
 *
 * Ported from neuro_client.py (去桥版).
 */

export type NeuroState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'happy' | 'sleepy' | 'bored'

export interface NeuroStatus {
  state: NeuroState
  patience: number
  lastActive: number
  timeSinceLast: number
}

type NeuroCallback = (status: NeuroStatus) => void

export class NeuroService {
  private _patience: number = 30.0 // seconds before bored
  private _lastActive: number = Date.now() / 1000
  private _lastPatienceWarning: number = 0
  private _aiState: NeuroState = 'idle'
  private _callback: NeuroCallback | null = null
  private _intervalId: ReturnType<typeof setInterval> | null = null
  private _running = false

  constructor(callback?: NeuroCallback) {
    this._callback = callback ?? null
  }

  /** Start the 1Hz PATIENCE check loop. */
  start(): void {
    if (this._running) return
    this._running = true
    this._intervalId = setInterval(() => this._poll(), 1000)
    console.log('[Neuro] started, patience =', this._patience, 's')
  }

  /** Stop the polling loop. */
  stop(): void {
    this._running = false
    if (this._intervalId !== null) {
      clearInterval(this._intervalId)
      this._intervalId = null
    }
  }

  /** Push a state change from external source (audio_engine, WebSocket, etc). */
  setState(state: string): void {
    const normalized = state.toLowerCase() as NeuroState
    this._aiState = normalized
    this._patienceReset()
    this._emitStatus()
  }

  /** Set the PATIENCE threshold in seconds. */
  setPatience(seconds: number): boolean {
    this._patience = Math.max(5, Math.min(600, seconds))
    console.log(`[Neuro] patience set to ${this._patience}s`)
    return true
  }

  /** Manually trigger PATIENCE — make the pet speak proactively. */
  triggerPatience(): boolean {
    this._lastActive = 0 // force timeout
    this._poll()
    return true
  }

  /** Reset the activity timer (e.g. on user interaction). */
  resetSignals(): boolean {
    this._patienceReset()
    return true
  }

  /** Current patience threshold. */
  get patience(): number {
    return this._patience
  }

  /** Current AI state. */
  get aiState(): NeuroState {
    return this._aiState
  }

  /** Seconds since last activity. */
  get timeSinceLast(): number {
    return Date.now() / 1000 - this._lastActive
  }

  /** Get current status snapshot. */
  getStatus(): NeuroStatus {
    return {
      state: this._aiState,
      patience: this._patience,
      lastActive: this._lastActive,
      timeSinceLast: this.timeSinceLast,
    }
  }

  // ─── Private ───

  private _patienceReset(): void {
    this._lastActive = Date.now() / 1000
  }

  private _poll(): void {
    if (!this._running) return
    try {
      const now = Date.now() / 1000
      const elapsed = now - this._lastActive

      if (elapsed > this._patience) {
        // PATIENCE timeout — emit bored state (but not more than once per 30s)
        if (now - this._lastPatienceWarning > 30) {
          this._lastPatienceWarning = now
          this._aiState = 'sleepy'
          this._emitStatus()
        }
      } else if (elapsed < 5) {
        // Recent activity — keep alive
        if (this._aiState !== 'bored') {
          this._emitStatus()
        }
      }
    } catch (e) {
      console.debug('[Neuro] poll error:', e)
    }
  }

  private _emitStatus(): void {
    if (this._callback) {
      try {
        this._callback(this.getStatus())
      } catch (e) {
        console.debug('[Neuro] callback error:', e)
      }
    }
  }
}
