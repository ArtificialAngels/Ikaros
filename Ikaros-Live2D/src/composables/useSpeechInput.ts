import { ref } from 'vue'
import { selectedMic } from './useAudioDevices'

export interface SpeechInputHooks {
  ws: () => WebSocket | null
  onPartial?: (text: string) => void
  onError?: (msg: string) => void
  onStatus?: (active: boolean) => void
}

/**
 * 前端语音采集: getUserMedia → 重采样 16k mono Int16 → WebSocket 二进制帧。
 * 后端 (ikaros-voice-ws :7870) 用本地 SenseVoice/Whisper 终句高精度识别;
 * 前端用自适应能量 VAD, 静音超时后发送 {action:end_utterance} 触发一句话的最终识别。
 *
 * B1: 优先用 new AudioContext({sampleRate:16000}) 让浏览器原生重采样
 *     (高质量 sinc, 无混叠), 不 honoring 时回退线性插值。
 * B2: 自适应噪声底 + 迟滞双阈值 + 尾静挂起, 改善句边界。
 */
export function useSpeechInput(hooks: SpeechInputHooks) {
  const active = ref(false)
  const error = ref('')
  const supported = ref(
    typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia,
  )

  let stream: MediaStream | null = null
  let audioCtx: AudioContext | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let processor: ScriptProcessorNode | null = null
  let gain: GainNode | null = null
  // B2: 自适应 VAD 状态
  let speaking = false
  let lastSpeech = 0
  let noiseFloor = 0.01 // 长期低能量帧的指数滑动均值 (噪声底估计)
  const SILENCE_MS = 1200 // tail silence before end-of-utterance
  const RMS_ABS_MIN = 0.010 // moderate floor (was 0.008 → 0.015, now 0.010)
  const muted = ref(false)

  function micErrorText(e: any): string {
    if (e?.name === 'NotAllowedError') return '麦克风权限被拒绝，请在系统/应用设置中允许'
    if (e?.name === 'NotFoundError') return '未检测到麦克风设备'
    if (e?.name === 'SecurityError') return '不安全上下文，无法访问麦克风'
    return '麦克风启动失败: ' + (e?.message || e)
  }

  async function start() {
    error.value = ''
    if (!supported.value) {
      error.value = '当前环境不支持麦克风采集'
      hooks.onError?.(error.value)
      return
    }
    try {
      // 降噪三件套: 浏览器自带 WebRTC 降噪/回声消除/自动增益, 低成本显著提升
      // 远场/环境噪声下的识别率 (此前是裸 {audio:true}, 无处理)
      const audioConstraints: MediaTrackConstraints = {
        noiseSuppression: true,
        echoCancellation: true,
        autoGainControl: true,
      }
      // 指定麦克风设备（右键菜单选择；'default' 用系统默认）
      if (selectedMic.value && selectedMic.value !== 'default') {
        audioConstraints.deviceId = { exact: selectedMic.value }
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
    } catch (e: any) {
      error.value = micErrorText(e)
      hooks.onError?.(error.value)
      return
    }
    // B1: 优先创建 16k AudioContext, 让浏览器原生重采样 (高质量 sinc, 无混叠)
    audioCtx = new AudioContext({ sampleRate: 16000 })
    const nativeRate = audioCtx.sampleRate // 浏览器可能不 honor 16k, 回退判断
    const useNative = nativeRate === 16000
    source = audioCtx.createMediaStreamSource(stream)
    processor = audioCtx.createScriptProcessor(4096, 1, 1)
    gain = audioCtx.createGain()
    gain.gain.value = 0 // 静音输出, 仅驱动 onaudioprocess, 避免回声

    processor.onaudioprocess = (e: AudioProcessingEvent) => {
      if (muted.value) return
      const input = e.inputBuffer.getChannelData(0) // 原生 16k 时为 16k 信号
      let pcm16: Int16Array
      if (useNative) {
        // B1: 无需重采样, 直接量化为 16k mono Int16
        pcm16 = new Int16Array(input.length)
        for (let i = 0; i < input.length; i++) {
          pcm16[i] = Math.max(-1, Math.min(1, input[i])) * 0x7fff
        }
      } else {
        // B1 回退: 线性插值重采样到 16k (仅在不 honor 16k 时触发)
        const ratio = nativeRate / 16000
        const outLen = Math.floor(input.length / ratio)
        pcm16 = new Int16Array(outLen)
        for (let i = 0; i < outLen; i++) {
          const pos = i * ratio
          const i0 = Math.floor(pos)
          const frac = pos - i0
          const s0 = input[i0] ?? 0
          const s1 = input[i0 + 1] ?? 0
          const s = s0 + (s1 - s0) * frac
          pcm16[i] = Math.max(-1, Math.min(1, s)) * 0x7fff
        }
      }
      // B2: 自适应 VAD — 在 (16k) 缓冲上算 RMS
      let sum = 0
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
      const rms = Math.sqrt(sum / input.length)
      const now = Date.now()
      // 噪声底: 仅当能量明显低于当前底时缓慢下探, 避免被短暂静音拉低
      if (rms < noiseFloor * 3.0) { // moderate: was 2.5 → 4.0, now 3.0
        noiseFloor = noiseFloor * 0.999 + rms * 0.001
      }
      const startTh = Math.max(RMS_ABS_MIN, noiseFloor * 2.0) // moderate: was 1.8 → 2.5, now 2.0
      const stopTh = Math.max(RMS_ABS_MIN * 0.5, noiseFloor * 1.3)
      if (rms > startTh) {
        speaking = true
        lastSpeech = now
      } else if (speaking && now - lastSpeech > SILENCE_MS) {
        speaking = false
        noiseFloor = Math.max(RMS_ABS_MIN, noiseFloor * 0.95) // 句尾下探重置, 下次更灵敏
        sendEndUtterance()
      }
      const ws = hooks.ws()
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(pcm16.buffer)
      }
    }

    source.connect(processor)
    processor.connect(gain)
    gain.connect(audioCtx.destination)
    active.value = true
    hooks.onStatus?.(true)
  }

  function sendEndUtterance() {
    const ws = hooks.ws()
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'end_utterance' }))
    }
  }

  function stop() {
    try {
      processor?.disconnect()
    } catch {}
    try {
      gain?.disconnect()
    } catch {}
    try {
      source?.disconnect()
    } catch {}
    try {
      audioCtx?.close()
    } catch {}
    if (stream) {
      stream.getTracks().forEach((t) => t.stop())
      stream = null
    }
    processor = null
    gain = null
    source = null
    audioCtx = null
    speaking = false
    noiseFloor = 0.01
    active.value = false
    hooks.onStatus?.(false)
  }

  return { active, error, supported, start, stop, muted }
}
