/**
 * llm-manager.ts — LLM model discovery, selection, and persistence.
 *
 * Fetches available models from the local llama-server (:8080),
 * knows about cloud model names, and persists the current selection.
 *
 * Ported from modules/model_manager/llm_manager.py.
 */

export interface LlmModel {
  id: string
  cloud: boolean
}

const STORAGE_KEY = 'ikaros-llm-model'
const LLM_BASE_URL = 'http://127.0.0.1:8080/v1'

// Known cloud model names (not served locally)
const KNOWN_CLOUD_MODELS = [
  'MiniMax-M3',
  'DeepSeek-V3',
  'DeepSeek-R1',
  'GPT-4o',
  'Claude-3.5-Sonnet',
]

class LlmManager {
  private _models: LlmModel[] = []
  private _currentModel: string = ''
  private _fetching = false

  constructor() {
    // Load persisted model
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        this._currentModel = saved
      }
    } catch {
      // ignore
    }
    if (!this._currentModel) {
      this._currentModel = 'Phi-4-Mini-3.8B-Q4_K_L'
    }
  }

  /** Get the currently selected model. */
  get currentModel(): string {
    return this._currentModel
  }

  /** Get all known models. */
  get models(): LlmModel[] {
    return [...this._models]
  }

  /** Whether a fetch is in progress. */
  get isFetching(): boolean {
    return this._fetching
  }

  /** Fetch models from local server + add known cloud models. */
  async fetchModels(): Promise<LlmModel[]> {
    if (this._fetching) return this._models
    this._fetching = true

    try {
      const resp = await fetch(`${LLM_BASE_URL}/models`, { signal: AbortSignal.timeout(3000) })
      if (resp.ok) {
        const data = await resp.json()
        const localModels: LlmModel[] = (data.data || []).map((m: any) => ({
          id: m.id,
          cloud: false,
        }))

        // Merge with cloud models
        const localIds = new Set(localModels.map(m => m.id))
        const cloudModels: LlmModel[] = KNOWN_CLOUD_MODELS
          .filter(id => !localIds.has(id))
          .map(id => ({ id, cloud: true }))

        this._models = [...localModels, ...cloudModels]
      }
    } catch {
      // Server unreachable — just show cloud models
      this._models = KNOWN_CLOUD_MODELS.map(id => ({ id, cloud: true }))
    } finally {
      this._fetching = false
    }

    // If current model not in list, pick first local
    if (this._currentModel && !this._models.find(m => m.id === this._currentModel)) {
      const firstLocal = this._models.find(m => !m.cloud)
      if (firstLocal) {
        this._currentModel = firstLocal.id
      }
    }

    return this._models
  }

  /** Select a model. */
  selectModel(modelId: string): void {
    this._currentModel = modelId
    try {
      localStorage.setItem(STORAGE_KEY, modelId)
    } catch {
      // ignore
    }
    console.log(`[LLM] selected model: ${modelId}`)
  }

  /** Check if a model is a cloud model. */
  isCloudModel(modelId: string): boolean {
    return KNOWN_CLOUD_MODELS.includes(modelId)
  }

  /** Get models filtered by cloud/local. */
  getCloudModels(): LlmModel[] {
    return this._models.filter(m => m.cloud)
  }

  getLocalModels(): LlmModel[] {
    return this._models.filter(m => !m.cloud)
  }
}

// Singleton
export const llmManager = new LlmManager()
