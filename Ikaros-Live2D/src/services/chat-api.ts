/**
 * chat-api.ts — Chat API service for text-based conversation.
 *
 * Sends messages to the local LLM endpoint (or cloud fallback)
 * and returns the response text.
 */

import { llmManager } from './llm-manager'
import { AntiRepetition } from './anti-repetition'
import { getEmotionTone, type Emotion } from './emotion-system'

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
}

const BASE_SYSTEM_PROMPT = `你是伊卡洛斯，一个温柔体贴的桌面宠物助手。你说话简短可爱，偶尔会用一些语气词如"嗯"、"呢"、"哦"。
你会关心用户在做什么，给予鼓励和帮助。保持回复在1-3句话以内。`

export class ChatApi {
  private _history: ChatMessage[] = []
  private _baseUrl: string
  private _antiRep: AntiRepetition
  private _emotion: Emotion = 'neutral'

  constructor(baseUrl = 'http://127.0.0.1:8080') {
    this._baseUrl = baseUrl
    this._antiRep = new AntiRepetition()
    this._rebuildSystem()
  }

  get history(): ReadonlyArray<ChatMessage> {
    return this._history
  }

  /** Clear conversation history (keep system prompt). */
  clearHistory(): void {
    this._rebuildSystem()
  }

  /** Set current emotion for tone injection. */
  setEmotion(emotion: Emotion): void {
    if (this._emotion !== emotion) {
      this._emotion = emotion
      this._rebuildSystem()
    }
  }

  /** Rebuild system prompt with current emotion and anti-repetition hints. */
  private _rebuildSystem(): void {
    let prompt = BASE_SYSTEM_PROMPT

    // Emotion tone
    const tone = getEmotionTone(this._emotion)
    if (tone) {
      prompt += `\n\n${tone}。`
    }

    // Anti-repetition hints
    const avoidance = this._antiRep.getAvoidanceHint()
    if (avoidance) {
      prompt += `\n\n${avoidance}`
    }

    // Replace or add system message at position 0
    if (this._history.length > 0 && this._history[0].role === 'system') {
      this._history[0] = { role: 'system', content: prompt }
    } else {
      this._history.unshift({ role: 'system', content: prompt })
    }
  }

  /** Send a message and get a response. */
  async chat(userMessage: string): Promise<string> {
    this._history.push({ role: 'user', content: userMessage })

    try {
      const model = llmManager.currentModel
      const resp = await fetch(`${this._baseUrl}/v1/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model,
          messages: this._history,
          max_tokens: 200,
          temperature: 0.8,
          stream: false,
        }),
        signal: AbortSignal.timeout(30000),
      })

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`)
      }

      const data = await resp.json()
      const reply = data.choices?.[0]?.message?.content?.trim() || '...'

      this._history.push({ role: 'assistant', content: reply })

      // Track for anti-repetition
      this._antiRep.addResponse(reply)
      // Rebuild system prompt with updated avoidance hints
      this._rebuildSystem()

      return reply
    } catch (e) {
      // If local LLM fails, try cloud fallback
      try {
        return await this._cloudFallback(userMessage)
      } catch {
        const errMsg = '（连接失败，请检查LLM服务是否启动）'
        this._history.push({ role: 'assistant', content: errMsg })
        return errMsg
      }
    }
  }

  private async _cloudFallback(userMessage: string): Promise<string> {
    // Try known cloud models
    const cloudModels = ['MiniMax-M3', 'gpt-4o-mini']
    for (const model of cloudModels) {
      try {
        const resp = await fetch(`${this._baseUrl}/v1/chat/completions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model,
            messages: this._history,
            max_tokens: 200,
            temperature: 0.8,
            stream: false,
          }),
          signal: AbortSignal.timeout(15000),
        })
        if (resp.ok) {
          const data = await resp.json()
          const reply = data.choices?.[0]?.message?.content?.trim()
          if (reply) {
            this._history.push({ role: 'assistant', content: reply })
            return reply
          }
        }
      } catch {
        continue
      }
    }
    throw new Error('All models failed')
  }
}
