import { ref } from 'vue'

export interface SpeechInputHooks {
  ws: () => WebSocket | null
  onPartial?: (text: string) => void
  onError?: (msg: string) => void
  onStatus?: (active: boolean) => void
}

/**
 * 前端语音采集: getUserMedia → 重采样 16k mono Int16 → WebSocket 二进制帧。
 * 后端 (ikaros-voice-ws :7870) 用本地 vosk 流式识别; 前端用简单能量 VAD,
 * 静音超时后发送 {action:end_utterance} 触发一句话的最终识别并走 LLM/TTS。
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
  let speaking = false
  let lastSpeech = 0
  const SILENCE_MS = 1200
  const RMS_THRESHOLD = 0.012
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
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
        },
      })
    } catch (e: any) {
      error.value = micErrorText(e)
      hooks.onError?.(error.value)
      return
    }
    audioCtx = new AudioContext()
    const targetRate = 16000
    const ratio = audioCtx.sampleRate / targetRate
    source = audioCtx.createMediaStreamSource(stream)
    processor = audioCtx.createScriptProcessor(4096, 1, 1)
    gain = audioCtx.createGain()
    gain.gain.value = 0 // 静音输出, 仅驱动 onaudioprocess, 避免回声

    processor.onaudioprocess = (e: AudioProcessingEvent) => {
      if (muted.value) return
      const input = e.inputBuffer.getChannelData(0)
      // 重采样到 16k mono Int16
      const outLen = Math.floor(input.length / ratio)
      const out = new Int16Array(outLen)
      for (let i = 0; i < outLen; i++) {
        const pos = i * ratio
        const i0 = Math.floor(pos)
        const frac = pos - i0
        const s0 = input[i0] ?? 0
        const s1 = input[i0 + 1] ?? 0
        const s = s0 + (s1 - s0) * frac
        out[i] = Math.max(-1, Math.min(1, s)) * 0x7fff
      }
      // 简单能量 VAD
      let sum = 0
      for (let i = 0; i < input.length; i++) sum += input[i] * input[i]
      const rms = Math.sqrt(sum / input.length)
      const now = Date.now()
      if (rms > RMS_THRESHOLD) {
        speaking = true
        lastSpeech = now
      } else if (speaking && now - lastSpeech > SILENCE_MS) {
        speaking = false
        sendEndUtterance()
      }
      const ws = hooks.ws()
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(out.buffer)
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
    active.value = false
    hooks.onStatus?.(false)
  }

  return { active, error, supported, start, stop, muted }
}
