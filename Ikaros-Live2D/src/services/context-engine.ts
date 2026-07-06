/**
 * context-engine.ts — Active window context awareness.
 *
 * Polls the foreground window every 1.5s via Tauri's `get_active_window` command,
 * classifies it into categories (Coding / Browser / Office / Game / Social / etc.),
 * and notifies the frontend so the pet can react with appropriate expressions.
 */

export type WindowCategory =
  | 'coding'
  | 'browser'
  | 'office'
  | 'game'
  | 'social'
  | 'media'
  | 'terminal'
  | 'file-manager'
  | 'unknown'

export interface WindowContext {
  processName: string
  title: string
  category: WindowCategory
}

type ContextCallback = (ctx: WindowContext) => void

// ─── Classification rules ───
const PROCESS_RULES: Array<[RegExp, WindowCategory]> = [
  // Coding
  [/code/i, 'coding'],
  [/cursor/i, 'coding'],
  [/webstorm/i, 'coding'],
  [/idea/i, 'coding'],
  [/pycharm/i, 'coding'],
  [/rider/i, 'coding'],
  [/goland/i, 'coding'],
  [/clion/i, 'coding'],
  [/phpstorm/i, 'coding'],
  [/rubymine/i, 'coding'],
  [/androidstudio/i, 'coding'],
  [/rstudio/i, 'coding'],
  [/sublime/i, 'coding'],
  [/notepad\+\+/i, 'coding'],
  [/vscodium/i, 'coding'],

  // Browser
  [/chrome/i, 'browser'],
  [/firefox/i, 'browser'],
  [/msedge/i, 'browser'],
  [/brave/i, 'browser'],
  [/opera/i, 'browser'],
  [/vivaldi/i, 'browser'],
  [/safari/i, 'browser'],

  // Office
  [/excel/i, 'office'],
  [/winword/i, 'office'],
  [/powerpnt/i, 'office'],
  [/outlook/i, 'office'],
  [/onenote/i, 'office'],
  [/notion/i, 'office'],
  [/obsidian/i, 'office'],

  // Social
  [/discord/i, 'social'],
  [/slack/i, 'social'],
  [/teams/i, 'social'],
  [/telegram/i, 'social'],
  [/wechat/i, 'social'],
  [/qq/i, 'social'],
  [/dingtalk/i, 'social'],
  [/feishu/i, 'social'],
  [/whatsapp/i, 'social'],

  // Media
  [/spotify/i, 'media'],
  [/vlc/i, 'media'],
  [/potplayer/i, 'media'],
  [/mpc/i, 'media'],
  [/foobar/i, 'media'],

  // Game
  [/steam/i, 'game'],
  [/epicgames/i, 'game'],

  // Terminal
  [/powershell/i, 'terminal'],
  [/cmd\.exe/i, 'terminal'],
  [/wt\.exe/i, 'terminal'],
  [/alacritty/i, 'terminal'],
  [/kitty/i, 'terminal'],
  [/conemu/i, 'terminal'],

  // File manager
  [/explorer/i, 'file-manager'],
  [/totalcmd/i, 'file-manager'],
  [/directoryopus/i, 'file-manager'],
]

const TITLE_RULES: Array<[RegExp, WindowCategory]> = [
  [/ - Visual Studio Code/i, 'coding'],
  [/ - IntelliJ/i, 'coding'],
  [/ - PyCharm/i, 'coding'],
  [/ - WebStorm/i, 'coding'],
  [/ - GoLand/i, 'coding'],
  [/ - CLion/i, 'coding'],
  [/ - Rider/i, 'coding'],
  [/ - Notepad\+\+/i, 'coding'],
  [/ - Sublime/i, 'coding'],
]

function classify(processName: string, title: string): WindowCategory {
  // Try title rules first (more specific)
  for (const [re, cat] of TITLE_RULES) {
    if (re.test(title)) return cat
  }
  // Then process rules
  for (const [re, cat] of PROCESS_RULES) {
    if (re.test(processName)) return cat
  }
  return 'unknown'
}

// ─── Category display info ───
const CATEGORY_INFO: Record<WindowCategory, { emoji: string; label: string }> = {
  coding:       { emoji: '💻', label: '编程中' },
  browser:      { emoji: '🌐', label: '浏览网页' },
  office:       { emoji: '📝', label: '办公中' },
  game:         { emoji: '🎮', label: '游戏中' },
  social:       { emoji: '💬', label: '聊天中' },
  media:        { emoji: '🎵', label: '听音乐' },
  terminal:     { emoji: '🖥️', label: '终端' },
  'file-manager': { emoji: '📁', label: '文件管理' },
  unknown:      { emoji: '🪽', label: '待机' },
}

export function getCategoryInfo(cat: WindowCategory) {
  return CATEGORY_INFO[cat]
}

// ─── Service ───
export class ContextEngine {
  private _intervalId: ReturnType<typeof setInterval> | null = null
  private _running = false
  private _lastCategory: WindowCategory = 'unknown'
  private _lastProcessName = ''
  private _callback: ContextCallback | null = null

  constructor(callback?: ContextCallback) {
    this._callback = callback ?? null
  }

  /** Start polling every 1.5s. */
  start(): void {
    if (this._running) return
    this._running = true
    this._intervalId = setInterval(() => this._poll(), 1500)
    console.log('[ContextEngine] started')
  }

  /** Stop polling. */
  stop(): void {
    this._running = false
    if (this._intervalId !== null) {
      clearInterval(this._intervalId)
      this._intervalId = null
    }
  }

  get currentCategory(): WindowCategory {
    return this._lastCategory
  }

  // ─── Private ───

  private async _poll(): Promise<void> {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      const [processName, title] = await invoke<[string, string]>('get_active_window')

      // Skip if it's our own process
      if (processName.toLowerCase().includes('ikaros') || processName.toLowerCase().includes('icarus')) {
        return
      }

      const category = classify(processName, title)

      // Only notify on category change
      if (category !== this._lastCategory || processName !== this._lastProcessName) {
        this._lastCategory = category
        this._lastProcessName = processName
        const ctx: WindowContext = { processName, title, category }
        if (this._callback) {
          this._callback(ctx)
        }
      }
    } catch (e) {
      // Silently ignore — Tauri API may not be available in browser dev mode
    }
  }
}
