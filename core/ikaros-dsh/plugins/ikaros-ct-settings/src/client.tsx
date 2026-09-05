// ikaros-ct-settings —— client 侧 bundle (浏览器 React 卡)
//
// 注册 dsh 设置页 'settings.section' slot, id='ikaros-ct-settings', 渲染
// 对话树设置面板。CT 独有设置（主题/权限/布局/粒子/语言）迁移到此。
// 模型/API Key 复用 dsh 已有设置，不在此重复。
//
// 通信：postMessage 双向
//   dsh → CT iframe:
//     { type: 'ikaros-ct-setting', key, value }   改单个设置
//     { type: 'ikaros-ct-get-settings' }            请求当前所有设置
//   CT iframe → dsh:
//     { type: 'ikaros-ct-settings', data }          响应设置查询
//     { type: 'ikaros-ct-setting-changed', key, value }  CT 内部改了设置
//     { type: 'ikaros-ct-open-settings' }           CT 设置按钮点击 → 打开 dsh 设置

import { useEffect, useState, useCallback, useRef } from 'react'

// 启动时由 Node 侧 patch; 若未 patch, fallback :48920
const CT_URL = 'http://127.0.0.1:48920/'

const NS = 'ikaros-ct-settings'

interface CtSettings {
  theme: string        // 'dark' | 'warm' | 'light' | 自定义主题名
  accent: string       // 主题色 hex
  perms: { edit: boolean; fork: boolean; confirm: boolean }
  lang: 'zh' | 'en'
  grid: boolean        // 网格背景
  fx: Record<string, number | boolean>  // 粒子特效参数
}

const DEFAULT_SETTINGS: CtSettings = {
  theme: 'dark',
  accent: '#13E425',
  perms: { edit: true, fork: true, confirm: true },
  lang: 'zh',
  grid: true,
  fx: {},
}

// 主题预设（与 CT 内置主题对应）
const THEME_PRESETS = [
  { id: 'dark', name: '暗夜绿', accent: '#13E425', bg: '#101010' },
  { id: 'warm', name: '暖纸', accent: '#f45f28', bg: '#f2eadb' },
  { id: 'ocean', name: '深海蓝', accent: '#00D4FF', bg: '#0a1628' },
  { id: 'sunset', name: '落日橙', accent: '#FFB020', bg: '#1a0f00' },
  { id: 'sakura', name: '樱花粉', accent: '#FF5DA2', bg: '#1a0a12' },
  { id: 'violet', name: '紫罗兰', accent: '#8B5CF6', bg: '#0f0a1a' },
]

const inject = ['slots', 'locale']

const zh = {
  title: '对话树',
  desc: 'Ikaros 对话树面板的外观与交互设置。通过 postMessage 实时同步到内嵌 CT 窗口。',
  appearance: '外观',
  theme: '颜色主题',
  themeDesc: '选择对话树的全局配色方案。',
  accent: '强调色',
  accentDesc: '自定义品牌强调色（覆盖主题预设）。',
  grid: '网格背景',
  gridDesc: '在画布上显示点阵网格。',
  particles: '粒子特效',
  particlesDesc: '鼠标粒子网络的视觉参数。',
  fxEnabled: '启用粒子',
  fxCount: '粒子数量',
  fxLinkDist: '连线距离',
  fxSpeed: '移动速度',
  fxSize: '粒子大小',
  interaction: '交互',
  permissions: '编辑权限',
  permEdit: '允许编辑消息',
  permEditDesc: '显示消息上的「编辑重发」按钮（创建新分支）。',
  permFork: '允许分支分叉',
  permForkDesc: '右键菜单保留「从此分叉」与编辑重发。',
  permConfirm: '破坏性操作确认',
  permConfirmDesc: '修剪/删除/舍弃/收尾前弹出确认。',
  language: '语言',
  langDesc: '对话树界面显示语言。',
  shortcuts: '快捷键',
  shortcutsDesc: '对话树键盘快捷键（只读）。',
  syncStatus: '同步状态',
  connected: '已连接 CT',
  disconnected: '未连接 CT（iframe 未打开）',
  applying: '应用中…',
  reset: '重置为默认',
  resetConfirm: '确定重置所有对话树设置为默认值？',
}

const en = {
  title: 'Conversation Tree',
  desc: 'Appearance and interaction settings for the Ikaros conversation tree panel. Synced to the embedded CT iframe via postMessage.',
  appearance: 'Appearance',
  theme: 'Color Theme',
  themeDesc: 'Choose the global color scheme for the conversation tree.',
  accent: 'Accent Color',
  accentDesc: 'Custom brand accent color (overrides theme preset).',
  grid: 'Grid Background',
  gridDesc: 'Show dot grid on the canvas.',
  particles: 'Particle Effects',
  particlesDesc: 'Visual parameters for the mouse particle network.',
  fxEnabled: 'Enable Particles',
  fxCount: 'Particle Count',
  fxLinkDist: 'Link Distance',
  fxSpeed: 'Move Speed',
  fxSize: 'Particle Size',
  interaction: 'Interaction',
  permissions: 'Edit Permissions',
  permEdit: 'Allow Message Editing',
  permEditDesc: 'Show "Edit & Resend" button on messages (creates new branch).',
  permFork: 'Allow Branch Forking',
  permForkDesc: 'Keep "Fork from here" and edit-resend in context menu.',
  permConfirm: 'Destructive Action Confirmation',
  permConfirmDesc: 'Confirm before prune/delete/abandon/conclude.',
  language: 'Language',
  langDesc: 'Conversation tree UI display language.',
  shortcuts: 'Keyboard Shortcuts',
  shortcutsDesc: 'Conversation tree keyboard shortcuts (read-only).',
  syncStatus: 'Sync Status',
  connected: 'CT Connected',
  disconnected: 'CT Not Connected (iframe not open)',
  applying: 'Applying…',
  reset: 'Reset to Defaults',
  resetConfirm: 'Reset all conversation tree settings to defaults?',
}

const SHORTCUTS = [
  { action: '发送消息', keys: 'Shift + Enter' },
  { action: '输入框内换行', keys: 'Enter' },
  { action: '命令面板', keys: 'Ctrl + K' },
  { action: '聚焦输入框', keys: 'Ctrl + F' },
  { action: '停止生成 / 关闭弹窗', keys: 'Esc' },
  { action: '树导航（选择）', keys: '↑ ↓ ← →' },
  { action: '跳转选中节点', keys: 'Enter（非输入态）' },
  { action: '重命名选中节点', keys: 'F2' },
  { action: '删除选中节点', keys: 'Del' },
]

// 找到 CT iframe 元素
function findCtIframe(): HTMLIFrameElement | null {
  // ikaros-conversation-tree 插件的全屏 iframe 没有特定 id/class,
  // 通过 src 匹配 CT_URL 前缀
  const iframes = document.querySelectorAll('iframe')
  for (const f of Array.from(iframes)) {
    if (f.src && f.src.startsWith(CT_URL.replace(/\/$/, ''))) return f as HTMLIFrameElement
  }
  return null
}

function postToCt(msg: Record<string, unknown>): void {
  const iframe = findCtIframe()
  if (iframe?.contentWindow) {
    iframe.contentWindow.postMessage(msg, '*')
  }
}

function CtSettingsCard(props: { t: (k: string) => string }) {
  const t = props.t
  const [settings, setSettings] = useState<CtSettings>(DEFAULT_SETTINGS)
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [accentInput, setAccentInput] = useState(DEFAULT_SETTINGS.accent)
  const reqIdRef = useRef(0)

  // 监听 CT iframe 的 postMessage
  useEffect(() => {
    const onMsg = (ev: MessageEvent) => {
      const d = ev.data
      if (!d || typeof d !== 'object') return
      if (d.type === 'ikaros-ct-settings' && d.data) {
        setSettings((prev) => ({ ...prev, ...d.data }))
        if (d.data.accent) setAccentInput(d.data.accent)
        setConnected(true)
      } else if (d.type === 'ikaros-ct-setting-changed' && d.key) {
        setSettings((prev) => {
          const next = { ...prev }
          if (d.key === 'perms' && typeof d.value === 'object') {
            next.perms = { ...prev.perms, ...d.value }
          } else if (d.key === 'fx' && typeof d.value === 'object') {
            next.fx = { ...prev.fx, ...d.value }
          } else {
            ;(next as any)[d.key] = d.value
          }
          return next
        })
        if (d.key === 'accent' && typeof d.value === 'string') setAccentInput(d.value)
      } else if (d.type === 'ikaros-ct-ready') {
        setConnected(true)
        // CT 就绪后请求当前设置
        postToCt({ type: 'ikaros-ct-get-settings', reqId: ++reqIdRef.current })
      }
    }
    window.addEventListener('message', onMsg)
    // 初始探测：如果 iframe 已存在，请求设置
    const probe = setTimeout(() => {
      postToCt({ type: 'ikaros-ct-get-settings', reqId: ++reqIdRef.current })
    }, 500)
    return () => {
      window.removeEventListener('message', onMsg)
      clearTimeout(probe)
    }
  }, [])

  const applySetting = useCallback((key: keyof CtSettings, value: any) => {
    setSettings((prev) => ({ ...prev, [key]: value }))
    postToCt({ type: 'ikaros-ct-setting', key, value })
  }, [])

  const applyPerm = useCallback((perm: 'edit' | 'fork' | 'confirm', value: boolean) => {
    setSettings((prev) => ({ ...prev, perms: { ...prev.perms, [perm]: value } }))
    postToCt({ type: 'ikaros-ct-setting', key: 'perms', value: { [perm]: value } })
  }, [])

  const applyFx = useCallback((fxKey: string, value: number | boolean) => {
    setSettings((prev) => ({ ...prev, fx: { ...prev.fx, [fxKey]: value } }))
    postToCt({ type: 'ikaros-ct-setting', key: 'fx', value: { [fxKey]: value } })
  }, [])

  const handleAccentApply = useCallback(() => {
    applySetting('accent', accentInput)
  }, [accentInput, applySetting])

  const handleReset = useCallback(() => {
    if (!confirm(t('resetConfirm'))) return
    setBusy('reset')
    setSettings(DEFAULT_SETTINGS)
    setAccentInput(DEFAULT_SETTINGS.accent)
    postToCt({ type: 'ikaros-ct-setting', key: 'reset', value: true })
    setTimeout(() => setBusy(null), 300)
  }, [t])

  const Toggle = ({ on, onClick, disabled }: { on: boolean; onClick: () => void; disabled?: boolean }) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      style={{
        width: 40, height: 22, borderRadius: 11, border: 'none', cursor: 'pointer',
        background: on ? 'var(--dsw-alias-brand-primary, #4d6bfe)' : 'var(--dsw-alias-border-l2, #30363d)',
        position: 'relative', transition: 'background 150ms ease', flex: 'none',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span style={{
        position: 'absolute', top: 2, left: on ? 20 : 2, width: 18, height: 18, borderRadius: '50%',
        background: '#fff', transition: 'left 150ms ease', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
      }} />
    </button>
  )

  return (
    <div style={{ maxWidth: 720, color: 'var(--dsw-alias-label-primary)', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <style>{`
        .ct-set-section { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border: 1px solid var(--dsw-alias-border-l2); border-radius: 12px; background: var(--dsw-alias-bg-floating); }
        .ct-set-section h3 { margin: 0 0 4px; font-size: 14px; font-weight: 500; color: var(--dsw-alias-label-primary); }
        .ct-set-section p { margin: 0; font-size: 12px; color: var(--dsw-alias-label-tertiary); line-height: 18px; }
        .ct-set-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid var(--dsw-alias-border-divider, #21262d); gap: 12px; }
        .ct-set-row:last-child { border-bottom: none; }
        .ct-set-row-label { font-size: 13px; font-weight: 500; color: var(--dsw-alias-label-primary); }
        .ct-set-row-desc { font-size: 11px; color: var(--dsw-alias-label-tertiary); margin-top: 2px; }
        .ct-set-btn { height: 30px; padding: 0 12px; border: 1px solid var(--dsw-alias-border-l2, #30363d); border-radius: 8px; background: var(--dsw-alias-button-elevated-fill, #21262d); color: var(--dsw-alias-label-primary, #e6edf3); cursor: pointer; font: inherit; font-size: 12px; transition: background 120ms ease; }
        .ct-set-btn:hover:not(:disabled) { background: var(--dsw-alias-button-elevated-hover, #30363d); }
        .ct-set-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .ct-set-btn--primary { background: var(--dsw-alias-brand-primary, #4d6bfe); color: #fff; border-color: var(--dsw-alias-brand-primary, #4d6bfe); }
        .ct-set-btn--primary:hover:not(:disabled) { background: var(--dsw-alias-brand-primary-hover, #3d56d4); }
        .ct-set-btn--danger { background: var(--dsw-alias-state-error-primary, #b91c1c); color: #fff; border-color: var(--dsh-alias-state-error-primary, #b91c1c); }
        .ct-theme-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 8px; }
        .ct-theme-card { padding: 10px; border-radius: 8px; border: 2px solid var(--dsw-alias-border-l2, #30363d); cursor: pointer; transition: all 120ms ease; background: var(--dsw-alias-bg-floating-2, rgba(255,255,255,0.02)); text-align: center; }
        .ct-theme-card:hover { border-color: var(--dsw-alias-border-l1, #444c56); }
        .ct-theme-card.active { border-color: var(--dsw-alias-brand-primary, #4d6bfe); background: color-mix(in srgb, var(--dsw-alias-brand-primary, #4d6bfe) 8%, transparent); }
        .ct-theme-swatch { width: 100%; height: 32px; border-radius: 6px; margin-bottom: 6px; }
        .ct-theme-name { font-size: 11px; font-weight: 500; color: var(--dsw-alias-label-primary); }
        .ct-set-input { padding: 6px 10px; border: 1px solid var(--dsw-alias-border-l2, #30363d); border-radius: 8px; background: var(--dsw-alias-bg-input, transparent); color: var(--dsw-alias-label-primary, #e6edf3); font: inherit; font-size: 12px; font-family: ui-monospace, monospace; width: 100px; }
        .ct-set-input:focus { outline: none; border-color: var(--dsw-alias-brand-primary, #4d6bfe); }
        .ct-set-slider { flex: 1; max-width: 200px; accent-color: var(--dsw-alias-brand-primary, #4d6bfe); }
        .ct-set-value { font-size: 11px; color: var(--dsw-alias-label-tertiary); min-width: 40px; text-align: right; font-family: ui-monospace, monospace; }
        .ct-shortcut-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--dsw-alias-border-divider, #21262d); font-size: 12px; }
        .ct-shortcut-row:last-child { border-bottom: none; }
        .ct-shortcut-keys { font-family: ui-monospace, monospace; font-size: 11px; padding: 2px 8px; border-radius: 4px; background: var(--dsw-alias-bg-floating-2, rgba(255,255,255,0.05)); color: var(--dsw-alias-label-secondary); }
        .ct-badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 500; }
        .ct-badge--ok { background: color-mix(in srgb, var(--dsw-alias-state-success-primary, #10b981) 18%, transparent); color: var(--dsw-alias-state-success-primary, #10b981); }
        .ct-badge--err { background: color-mix(in srgb, var(--dsw-alias-state-error-primary, #b91c1c) 18%, transparent); color: var(--dsw-alias-state-error-primary, #b91c1c); }
      `}</style>

      <p>{t('desc')}</p>

      {/* 同步状态 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
        <span className={`ct-badge ${connected ? 'ct-badge--ok' : 'ct-badge--err'}`}>
          {connected ? t('connected') : t('disconnected')}
        </span>
        <span style={{ color: 'var(--dsw-alias-label-tertiary)' }}>CT URL: {CT_URL}</span>
      </div>

      {/* ── 外观 ── */}
      <div className="ct-set-section">
        <h3>{t('appearance')} — {t('theme')}</h3>
        <p>{t('themeDesc')}</p>
        <div className="ct-theme-grid">
          {THEME_PRESETS.map((theme) => (
            <div
              key={theme.id}
              className={`ct-theme-card ${settings.theme === theme.id ? 'active' : ''}`}
              onClick={() => { applySetting('theme', theme.id); applySetting('accent', theme.accent); setAccentInput(theme.accent) }}
            >
              <div className="ct-theme-swatch" style={{ background: `linear-gradient(135deg, ${theme.bg} 0%, ${theme.accent}33 100%)`, border: `1px solid ${theme.accent}44` }} />
              <div className="ct-theme-name">{theme.name}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 强调色 + 网格 */}
      <div className="ct-set-section">
        <h3>{t('appearance')} — {t('accent')} & {t('grid')}</h3>
        <div className="ct-set-row">
          <div>
            <div className="ct-set-row-label">{t('accent')}</div>
            <div className="ct-set-row-desc">{t('accentDesc')}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="color" value={accentInput} onChange={(e) => setAccentInput(e.target.value)} style={{ width: 36, height: 30, border: 'none', background: 'transparent', cursor: 'pointer', padding: 0 }} />
            <input className="ct-set-input" value={accentInput} onChange={(e) => setAccentInput(e.target.value)} />
            <button className="ct-set-btn ct-set-btn--primary" onClick={handleAccentApply} disabled={busy !== null}>{busy === 'accent' ? t('applying') : 'OK'}</button>
          </div>
        </div>
        <div className="ct-set-row">
          <div>
            <div className="ct-set-row-label">{t('grid')}</div>
            <div className="ct-set-row-desc">{t('gridDesc')}</div>
          </div>
          <Toggle on={settings.grid} onClick={() => applySetting('grid', !settings.grid)} />
        </div>
      </div>

      {/* ── 粒子特效 ── */}
      <div className="ct-set-section">
        <h3>{t('particles')}</h3>
        <p>{t('particlesDesc')}</p>
        <div className="ct-set-row">
          <div className="ct-set-row-label">{t('fxEnabled')}</div>
          <Toggle on={settings.fx.enabled !== false} onClick={() => applyFx('enabled', settings.fx.enabled === false)} />
        </div>
        {[
          { key: 'count', label: t('fxCount'), min: 10, max: 200, step: 5, def: 60 },
          { key: 'linkDist', label: t('fxLinkDist'), min: 50, max: 300, step: 10, def: 120 },
          { key: 'speed', label: t('fxSpeed'), min: 0.1, max: 3, step: 0.1, def: 0.8 },
          { key: 'size', label: t('fxSize'), min: 1, max: 6, step: 0.5, def: 2 },
        ].map((s) => (
          <div key={s.key} className="ct-set-row">
            <div className="ct-set-row-label" style={{ minWidth: 100 }}>{s.label}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
              <input
                type="range" className="ct-set-slider"
                min={s.min} max={s.max} step={s.step}
                value={(settings.fx[s.key] as number) ?? s.def}
                onChange={(e) => applyFx(s.key, parseFloat(e.target.value))}
              />
              <span className="ct-set-value">{((settings.fx[s.key] as number) ?? s.def).toFixed(s.step < 1 ? 1 : 0)}</span>
            </div>
          </div>
        ))}
      </div>

      {/* ── 编辑权限 ── */}
      <div className="ct-set-section">
        <h3>{t('interaction')} — {t('permissions')}</h3>
        {([
          { key: 'edit' as const, label: t('permEdit'), desc: t('permEditDesc') },
          { key: 'fork' as const, label: t('permFork'), desc: t('permForkDesc') },
          { key: 'confirm' as const, label: t('permConfirm'), desc: t('permConfirmDesc') },
        ]).map((p) => (
          <div key={p.key} className="ct-set-row">
            <div>
              <div className="ct-set-row-label">{p.label}</div>
              <div className="ct-set-row-desc">{p.desc}</div>
            </div>
            <Toggle on={settings.perms[p.key]} onClick={() => applyPerm(p.key, !settings.perms[p.key])} />
          </div>
        ))}
      </div>

      {/* ── 语言 ── */}
      <div className="ct-set-section">
        <h3>{t('language')}</h3>
        <p>{t('langDesc')}</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className={`ct-set-btn ${settings.lang === 'zh' ? 'ct-set-btn--primary' : ''}`}
            onClick={() => applySetting('lang', 'zh')}
          >简体中文</button>
          <button
            className={`ct-set-btn ${settings.lang === 'en' ? 'ct-set-btn--primary' : ''}`}
            onClick={() => applySetting('lang', 'en')}
          >English</button>
        </div>
      </div>

      {/* ── 快捷键（只读） ── */}
      <div className="ct-set-section">
        <h3>{t('shortcuts')}</h3>
        <p>{t('shortcutsDesc')}</p>
        {SHORTCUTS.map((s, i) => (
          <div key={i} className="ct-shortcut-row">
            <span>{s.action}</span>
            <span className="ct-shortcut-keys">{s.keys}</span>
          </div>
        ))}
      </div>

      {/* 重置 */}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="ct-set-btn ct-set-btn--danger" onClick={handleReset} disabled={busy !== null}>
          {busy === 'reset' ? t('applying') : t('reset')}
        </button>
      </div>
    </div>
  )
}

function apply(ctx: any) {
  ctx.effect(() => {
    const locale = ctx.locale
    if (locale && typeof locale.register === 'function') {
      locale.register(NS, { zh, en })
    }
  }, 'ikaros-ct-settings: locale')

  const t = ctx.locale.bind(NS)

  /* ── Phase 2: 监听 CT 的「打开设置」请求, 尝试打开 dsh 设置面板 ── */
  const openDshSettings = () => {
    // 策略1: DOM 点击设置按钮 (aria-label / title / class 匹配)
    const candidates = Array.from(document.querySelectorAll<HTMLElement>(
      'button[aria-label*="etting"], button[aria-label*="设置"], button[title*="etting"], button[title*="设置"], [class*="settings"] button, [class*="Settings"] button'
    ))
    for (const btn of candidates) {
      if (btn.offsetParent !== null) { btn.click(); return true }
    }
    // 策略2: 键盘快捷键 Ctrl+, (dsh 常见设置快捷键)
    try {
      const ev = new KeyboardEvent('keydown', { key: ',', code: 'Comma', ctrlKey: true, bubbles: true })
      document.dispatchEvent(ev)
      return true
    } catch { return false }
  }

  ctx.effect(() => {
    const onMsg = (ev: MessageEvent) => {
      const d = ev.data
      if (!d || typeof d !== 'object') return
      if (d.type === 'ikaros-ct-open-settings') {
        const ok = openDshSettings()
        if (ok) {
          // 通知 CT: dsh 已打开设置, 取消 CT 内部模态框 fallback
          const iframe = findCtIframe()
          iframe?.contentWindow?.postMessage({ type: 'ikaros-ct-settings-opened' }, '*')
          // 同时尝试定位到对话树分区 (延迟等待设置面板渲染)
          setTimeout(() => {
            const section = document.querySelector<HTMLElement>('[data-settings-section="ikaros-ct-settings"], #ikaros-ct-settings')
            section?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }, 300)
        }
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, 'ikaros-ct-settings: open-settings bridge')

  /* ── Phase 4: dsh-CT 联动（会话同步 / toast 桥接 / 主题名存储） ── */

  // 注入 toast 动画 keyframes
  if (!document.getElementById('ikaros-ct-toast-style')) {
    const style = document.createElement('style')
    style.id = 'ikaros-ct-toast-style'
    style.textContent = '@keyframes ctToastIn{from{opacity:0;transform:translateX(-50%) translateY(10px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}'
    document.head.appendChild(style)
  }

  // 简单 DOM toast（dsh 未暴露 toast API 时的 fallback）
  const showDshToast = (msg: string, type: 'info' | 'success' | 'error' = 'info') => {
    const toast = document.createElement('div')
    toast.style.cssText = `
      position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
      padding:10px 20px;border-radius:10px;font-size:13px;z-index:2147483647;
      background:var(--dsw-alias-bg-floating,#1c2128);color:var(--dsw-alias-label-primary,#e6edf3);
      border:1px solid var(--dsw-alias-border-l2,#30363d);box-shadow:0 8px 24px rgba(0,0,0,0.4);
      animation:ctToastIn .2s ease-out;pointer-events:none;
    `
    toast.textContent = msg
    document.body.appendChild(toast)
    setTimeout(() => {
      toast.style.transition = 'opacity .3s ease, transform .3s ease'
      toast.style.opacity = '0'
      toast.style.transform = 'translateX(-50%) translateY(10px)'
      setTimeout(() => toast.remove(), 350)
    }, 2500)
  }

  // 尝试获取 dsh 当前会话信息（graceful: 服务不存在则返回 null）
  const getDshSession = (): { name: string | null; id: string | null } => {
    try {
      // 尝试常见 dsh session 服务名
      const candidates = ['session', 'sessions', 'currentSession', 'sessionProjection']
      for (const name of candidates) {
        try {
          const svc = (ctx as any).get?.(name)
          if (svc) {
            const id = svc.currentId ?? svc.id ?? svc.sessionId ?? null
            const name = svc.name ?? svc.title ?? svc.label ?? null
            if (id || name) return { name, id }
          }
        } catch { /* continue */ }
      }
    } catch { /* ignore */ }
    return { name: null, id: null }
  }

  // 会话同步：dsh 会话变化时通知 CT
  let lastSessionId: string | null = null
  const checkSessionChange = () => {
    const sess = getDshSession()
    if (sess.id && sess.id !== lastSessionId) {
      lastSessionId = sess.id
      postToCt({ type: 'ikaros-ct-session-switch', session: sess })
    }
  }

  ctx.effect(() => {
    const onMsg = (ev: MessageEvent) => {
      const d = ev.data
      if (!d || typeof d !== 'object') return

      // CT 请求当前 dsh 会话信息
      if (d.type === 'ikaros-ct-get-session') {
        const sess = getDshSession()
        postToCt({ type: 'ikaros-ct-session-info', session: sess, reqId: d.reqId })
      }
      // CT toast 转发到 dsh
      else if (d.type === 'ikaros-ct-toast' && d.message) {
        showDshToast(String(d.message), d.type === 'error' ? 'error' : d.type === 'success' ? 'success' : 'info')
      }
      // 增强主题同步：同时存储主题名
      else if (d.type === 'ikaros-ct-theme') {
        if (d.theme) {
          document.documentElement.style.setProperty('--ct-theme-name', String(d.theme))
        }
        if (d.color) {
          document.documentElement.style.setProperty('--ct-brand', String(d.color))
        }
      }
    }
    window.addEventListener('message', onMsg)

    // 会话变化轮询（dsh 未暴露 session change event 时的 fallback）
    const sessionTimer = setInterval(checkSessionChange, 2000)

    return () => {
      window.removeEventListener('message', onMsg)
      clearInterval(sessionTimer)
    }
  }, 'ikaros-ct-settings: phase4 linkage')

  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'ikaros-ct-settings',
    order: 60,
    label: () => t('title'),
    inject: () => ({ t }),
  }, CtSettingsCard))
}

export { apply, inject }
export default { apply, inject }
