/**
 * anti-repetition.ts — Detect and prevent repetitive AI responses.
 *
 * Caches the last N responses, analyzes them for structural patterns,
 * and generates avoidance hints for the system prompt.
 *
 * Ported from Live2DPet's DesktopPetSystem repetition detection.
 */

export interface RepetitionAnalysis {
  /** Repeated topics to avoid. */
  topics: string[]
  /** Repeated speech habits (e.g. rhetorical questions). */
  habits: string[]
}

interface CachedResponse {
  text: string
  timestamp: number
  analysis: RepetitionAnalysis | null
}

type AnalysisCallback = (analysis: RepetitionAnalysis) => void

export class AntiRepetition {
  private _pool: CachedResponse[] = []
  private _poolTTL: number = 120000  // 2 min expiry
  private _maxPoolSize = 10
  private _analysisCallback: AnalysisCallback | null = null

  constructor(callback?: AnalysisCallback) {
    this._analysisCallback = callback ?? null
  }

  /** Add a response to the pool and analyze it. */
  addResponse(text: string): void {
    this._prune()
    const entry: CachedResponse = { text, timestamp: Date.now(), analysis: null }
    this._pool.push(entry)
    while (this._pool.length > this._maxPoolSize) this._pool.shift()

    // Fire-and-forget structural pattern detection
    this._analyzeStructural(entry)
  }

  /** Get avoidance hints from recent pool analyses. */
  getAvoidanceHint(): string {
    this._prune()
    const analyses = this._pool
      .filter(e => e.analysis)
      .map(e => e.analysis!)
    if (analyses.length === 0) return ''

    const parts: string[] = []

    // Collect all topics to avoid
    const topics = [...new Set(analyses.flatMap(a => a.topics || []))]
    if (topics.length > 0) {
      parts.push(`最近已经聊过这些话题，请避免重复: ${topics.join('、')}`)
    }

    // Collect all speech habits to avoid
    const habits = [...new Set(analyses.flatMap(a => a.habits || []))]
    if (habits.length > 0) {
      parts.push(`请避免以下说话习惯: ${habits.join('、')}`)
    }

    return parts.join('\n')
  }

  /** Structural pattern detection (fast, no LLM). */
  detectStructuralPatterns(texts: string[]): string[] {
    if (texts.length < 2) return []
    const patterns: string[] = []

    // Check for repeated question marks (rhetorical questions)
    const questionCount = texts.filter(r => r.includes('？') || r.includes('?')).length
    if (questionCount >= 2) patterns.push('避免连续使用反问句')

    // Check for repeated opening words (first 2 chars)
    const openings = texts.map(r => r.slice(0, 2))
    if (openings.length >= 2 && new Set(openings).size === 1) {
      patterns.push('避免总是用相同的词语开头')
    }

    // Check for repeated sentence endings (last 4 chars)
    const endings = texts.map(r => {
      const clean = r.replace(/[。！？…\s]+$/, '')
      return clean.slice(-4)
    })
    if (endings.length >= 2 && new Set(endings).size === 1) {
      patterns.push('避免相同的结尾句式')
    }

    // Check for similar response length (all within ±20% of mean)
    if (texts.length >= 3) {
      const lengths = texts.map(r => r.length)
      const mean = lengths.reduce((a, b) => a + b, 0) / lengths.length
      const allSimilar = mean > 0 && lengths.every(l => Math.abs(l - mean) / mean <= 0.2)
      if (allSimilar) patterns.push('避免回复长度千篇一律')
    }

    // Check for exclamation overuse
    const exclCount = texts.filter(r => r.includes('！') || r.includes('!')).length
    if (exclCount >= 3) patterns.push('避免过度使用感叹号')

    // Check for ellipsis overuse
    const ellipsisCount = texts.filter(r => r.includes('…') || r.includes('...')).length
    if (ellipsisCount >= 3) patterns.push('避免过度使用省略号')

    return patterns
  }

  /** Clear the pool. */
  clear(): void {
    this._pool = []
  }

  /** Get recent responses. */
  getRecentResponses(count = 5): string[] {
    return this._pool.slice(-count).map(e => e.text)
  }

  // ─── Private ───

  private _prune(): void {
    const now = Date.now()
    this._pool = this._pool.filter(e => now - e.timestamp < this._poolTTL)
  }

  /** Fire-and-forget: analyze structural patterns in the response. */
  private _analyzeStructural(entry: CachedResponse): void {
    // Use local pattern detection (fast, no API call)
    const recentTexts = this.getRecentResponses(4) // includes current entry
    const habits = this.detectStructuralPatterns(recentTexts)

    // Extract potential topics: look for nouns and key phrases
    const topics = this._extractTopics(entry.text)

    if (topics.length > 0 || habits.length > 0) {
      entry.analysis = { topics, habits }
      if (this._analysisCallback) {
        this._analysisCallback(entry.analysis)
      }
    }
  }

  /** Simple keyword-based topic extraction. */
  private _extractTopics(text: string): string[] {
    // Common Chinese topic markers
    const topicPatterns = [
      /关于(.{1,6})/g,
      /聊聊(.{1,6})/g,
      /提到(.{1,6})/g,
      /说到(.{1,6})/g,
      /讨论(.{1,6})/g,
    ]

    const topics: string[] = []
    for (const pattern of topicPatterns) {
      let match
      while ((match = pattern.exec(text)) !== null) {
        if (match[1] && match[1].length >= 1) {
          topics.push(match[1])
        }
      }
    }
    return [...new Set(topics)]
  }
}
