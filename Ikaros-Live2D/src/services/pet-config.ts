/**
 * pet-config.ts — localStorage persistence for pet settings.
 */

export interface PetConfig {
  modelPath: string
  modelType: 'live2d' | 'image'
  scale: number
  trackingSensitivity: number
  lipSyncEnabled: boolean
  lipSyncSensitivity: number
  // Emotion
  emotionEnabled: boolean
  emotionDecayRate: number
  // VLM
  vlmEnabled: boolean
  vlmInterval: number
  // PATIENCE
  patienceSeconds: number
  // Anti-repetition
  antiRepEnabled: boolean
}

const STORAGE_KEY = 'ikaros-pet-config'

const DEFAULT_CONFIG: PetConfig = {
  modelPath: '/live2d/hiyori_free_t08/hiyori_free_t08.model3.json',
  modelType: 'live2d',
  scale: 1.0,
  trackingSensitivity: 1.0,
  lipSyncEnabled: true,
  lipSyncSensitivity: 1.0,
  emotionEnabled: true,
  emotionDecayRate: 0.02,
  vlmEnabled: false,
  vlmInterval: 30000,
  patienceSeconds: 30,
  antiRepEnabled: true,
}

export function loadConfig(): PetConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      return { ...DEFAULT_CONFIG, ...JSON.parse(raw) }
    }
  } catch (e) {
    console.warn('[petConfig] load failed:', e)
  }
  return { ...DEFAULT_CONFIG }
}

export function saveConfig(config: PetConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  } catch (e) {
    console.warn('[petConfig] save failed:', e)
  }
}

export function resetConfig(): PetConfig {
  localStorage.removeItem(STORAGE_KEY)
  return { ...DEFAULT_CONFIG }
}
