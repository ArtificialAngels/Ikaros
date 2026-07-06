/**
 * vlm-service.ts — Vision Language Model screen analysis service.
 *
 * Captures desktop screenshots via Tauri Rust command (or canvas fallback),
 * sends them to the LLM for visual analysis, and caches results.
 *
 * Inspired by Live2DPet's VLM extractor + screen capture.
 */

export interface VlmResult {
  summary: string         // What's on screen
  category: string        // e.g. "coding", "browsing", "gaming"
  suggestedTopic: string  // What the pet could comment on
  timestamp: number
}

export interface VlmConfig {
  enabled: boolean
  intervalMs: number      // How often to capture (min: 15000)
  maxIntervalMs: number   // Max interval with idle backoff
  minFocusSeconds: number // Min focus time before triggering
  maxScreenshotsPerHour: number
}

type VlmCallback = (result: VlmResult) => void

const DEFAULT_CONFIG: VlmConfig = {
  enabled: false,
  intervalMs: 30000,
  maxIntervalMs: 120000,
  minFocusSeconds: 10,
  maxScreenshotsPerHour: 12,
}

export class VlmService {
  private _config: VlmConfig = { ...DEFAULT_CONFIG }
  private _callback: VlmCallback | null = null
  private _intervalId: ReturnType<typeof setInterval> | null = null
  private _running = false
  private _cache: Map<string, VlmResult> = new Map()
  private _hourlyCount = 0
  private _lastCaptureTime = 0
  private _hourlyResetTimer: ReturnType<typeof setInterval> | null = null
  private _focusStartTime = 0
  private _currentWindow = ''

  constructor(callback?: VlmCallback) {
    this._callback = callback ?? null
  }

  /** Start the capture loop. */
  start(): void {
    if (this._running || !this._config.enabled) return
    this._running = true
    this._scheduleNext()
    // Reset hourly counter every hour
    this._hourlyResetTimer = setInterval(() => { this._hourlyCount = 0 }, 3600000)
    console.log('[VlmService] started, interval:', this._config.intervalMs, 'ms')
  }

  /** Stop the capture loop. */
  stop(): void {
    this._running = false
    if (this._intervalId !== null) {
      clearTimeout(this._intervalId)
      this._intervalId = null
    }
    if (this._hourlyResetTimer !== null) {
      clearInterval(this._hourlyResetTimer)
      this._hourlyResetTimer = null
    }
  }

  /** Update config at runtime. */
  setConfig(config: Partial<VlmConfig>): void {
    if (config.enabled !== undefined) this._config.enabled = config.enabled
    if (config.intervalMs !== undefined) this._config.intervalMs = Math.max(15000, config.intervalMs)
    if (config.maxIntervalMs !== undefined) this._config.maxIntervalMs = config.maxIntervalMs
    if (config.minFocusSeconds !== undefined) this._config.minFocusSeconds = config.minFocusSeconds
    if (config.maxScreenshotsPerHour !== undefined) this._config.maxScreenshotsPerHour = config.maxScreenshotsPerHour

    // Restart if enabled changed
    if (this._config.enabled && !this._running) {
      this.start()
    } else if (!this._config.enabled && this._running) {
      this.stop()
    }
  }

  get config(): Readonly<VlmConfig> {
    return this._config
  }

  get isRunning(): boolean {
    return this._running
  }

  /** Notify VLM service about window focus change. */
  onFocusChange(processName: string): void {
    if (processName !== this._currentWindow) {
      this._currentWindow = processName
      this._focusStartTime = Date.now()
    }
  }

  /** Get cached analysis for a window. */
  getCached(windowKey: string): VlmResult | undefined {
    return this._cache.get(windowKey)
  }

  /** Get all cached results for prompt injection. */
  getRecentAnalyses(count = 3): VlmResult[] {
    const all = Array.from(this._cache.values())
    all.sort((a, b) => b.timestamp - a.timestamp)
    return all.slice(0, count)
  }

  // ─── Private ───

  private _scheduleNext(): void {
    if (!this._running) return
    const now = Date.now()
    const elapsed = now - this._lastCaptureTime
    const delay = Math.max(1000, this._config.intervalMs - elapsed)
    this._intervalId = setTimeout(() => this._capture(), delay)
  }

  private async _capture(): Promise<void> {
    if (!this._running) return
    if (this._hourlyCount >= this._config.maxScreenshotsPerHour) {
      console.log('[VlmService] hourly limit reached, skipping')
      this._scheduleNext()
      return
    }

    // Check focus duration
    const focusElapsed = (Date.now() - this._focusStartTime) / 1000
    if (focusElapsed < this._config.minFocusSeconds) {
      console.log('[VlmService] focus too short, skipping')
      this._scheduleNext()
      return
    }

    try {
      const screenshot = await this._captureScreen()
      if (!screenshot) {
        this._scheduleNext()
        return
      }

      const analysis = await this._analyzeScreen(screenshot)
      if (analysis) {
        this._cache.set(this._currentWindow, analysis)
        this._hourlyCount++
        this._lastCaptureTime = Date.now()
        if (this._callback) {
          this._callback(analysis)
        }
      }
    } catch (e) {
      console.warn('[VlmService] capture failed:', e)
    }

    this._scheduleNext()
  }

  private async _captureScreen(): Promise<string | null> {
    try {
      // Try Tauri Rust command first
      const { invoke } = await import('@tauri-apps/api/core')
      // get_active_window returns (processName, title)
      // For actual screen capture, we'd need a new Rust command
      // Fallback: use canvas screenshot from the live2d adapter
      const result = await invoke<{ success: boolean; data?: string }>('get_screen_capture').catch(() => null)
      if (result?.success && result.data) {
        return result.data
      }
    } catch {
      // Not available
    }
    return null
  }

  private async _analyzeScreen(base64: string): Promise<VlmResult | null> {
    try {
      const resp = await fetch('http://127.0.0.1:8080/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: 'Phi-4-Mini-3.8B-Q4_K_L',
          messages: [
            {
              role: 'system',
              content: '你是一个桌面分析助手。看到屏幕截图后，用1-2句话简短描述用户在做什么，然后用1句话建议桌面宠物可以对用户说什么。输出JSON格式: {"summary":"...", "category":"...", "suggestedTopic":"..."}'
            },
            {
              role: 'user',
              content: [
                { type: 'text', text: '分析这个屏幕截图' },
                { type: 'image_url', image_url: { url: `data:image/jpeg;base64,${base64}` } }
              ]
            }
          ],
          max_tokens: 200,
          temperature: 0.7,
          stream: false,
        }),
        signal: AbortSignal.timeout(15000),
      })

      if (!resp.ok) return null
      const data = await resp.json()
      const content = data.choices?.[0]?.message?.content?.trim()
      if (!content) return null

      // Try JSON parse, fallback to raw text
      try {
        const parsed = JSON.parse(content)
        return {
          summary: parsed.summary || content,
          category: parsed.category || 'unknown',
          suggestedTopic: parsed.suggestedTopic || '',
          timestamp: Date.now(),
        }
      } catch {
        return {
          summary: content,
          category: 'unknown',
          suggestedTopic: content,
          timestamp: Date.now(),
        }
      }
    } catch (e) {
      console.warn('[VlmService] analysis failed:', e)
      return null
    }
  }
}
