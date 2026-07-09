import { ref } from 'vue'

/**
 * useAudioDevices — 麦克风 / 扬声器设备枚举与选择。
 *
 * 只有前端 webview 能访问 Web Audio 的设备枚举 API，所以设备列表在前端
 * 拿到后通过 Tauri 命令推给 Rust，由 Rust 重建带设备子菜单的托盘菜单；
 * 用户选设备时 Rust emit `mic:<id>` / `speaker:<id>` 回前端，前端套用。
 *
 * - 麦克风：selectedMic 传给 useSpeechInput 的 getUserMedia deviceId。
 * - 扬声器：selectedSpeaker 传给 TTS 播放元素 (`<audio>.setSinkId`)。
 *
 * 选择持久化到 localStorage，重启后仍生效。
 */

const LS_MIC = 'ikaros.selectedMic'
const LS_SPK = 'ikaros.selectedSpeaker'

export interface DeviceInfo {
  deviceId: string
  label: string
}

/** 当前选中的麦克风设备 id；'default' = 系统默认。 */
export const selectedMic = ref<string>(localStorage.getItem(LS_MIC) || 'default')
/** 当前选中的扬声器设备 id；'default' = 系统默认。 */
export const selectedSpeaker = ref<string>(localStorage.getItem(LS_SPK) || 'default')

/** 最近一次枚举到的设备列表（用于气泡显示友好名称）。 */
export const micDevices = ref<DeviceInfo[]>([])
export const speakerDevices = ref<DeviceInfo[]>([])

export function setSelectedMic(id: string) {
  selectedMic.value = id
  try { localStorage.setItem(LS_MIC, id) } catch { /* ignore */ }
}

export function setSelectedSpeaker(id: string) {
  selectedSpeaker.value = id
  try { localStorage.setItem(LS_SPK, id) } catch { /* ignore */ }
}

export function labelForMic(id: string): string {
  if (id === 'default') return '系统默认'
  return micDevices.value.find((d) => d.deviceId === id)?.label || id || '系统默认'
}

export function labelForSpeaker(id: string): string {
  if (id === 'default') return '系统默认'
  return speakerDevices.value.find((d) => d.deviceId === id)?.label || id || '系统默认'
}

/**
 * 枚举音频输入/输出设备。需要麦克风权限才能拿到真实 label；
 * 未授权时 label 为空，调用方可后续（权限授予后）再次枚举以补全。
 */
export async function enumerateAudioDevices(): Promise<{ mics: DeviceInfo[]; speakers: DeviceInfo[] }> {
  try {
    if (!navigator.mediaDevices?.enumerateDevices) {
      return { mics: [], speakers: [] }
    }
    const devices = await navigator.mediaDevices.enumerateDevices()
    const mics: DeviceInfo[] = devices
      .filter((d) => d.kind === 'audioinput' && d.deviceId)
      .map((d) => ({ deviceId: d.deviceId, label: d.label || `麦克风 (${d.deviceId.slice(0, 8)})` }))
    const speakers: DeviceInfo[] = devices
      .filter((d) => d.kind === 'audiooutput' && d.deviceId)
      .map((d) => ({ deviceId: d.deviceId, label: d.label || `扬声器 (${d.deviceId.slice(0, 8)})` }))
    micDevices.value = mics
    speakerDevices.value = speakers
    return { mics, speakers }
  } catch {
    return { mics: [], speakers: [] }
  }
}
