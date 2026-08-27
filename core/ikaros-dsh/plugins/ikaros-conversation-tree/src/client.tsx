// ikaros-conversation-tree —— client 侧 bundle
// 挂载点（dsh slots 体系）：
//   - sidebar.footer.action 侧边栏底部动作区：注册「对话树」入口按钮
// 单击打开独立窗口，双击弹出关闭确认对话框。

import { useSyncExternalStore, useRef, useCallback, useEffect, useState } from 'react'

// Ikaros favicon base64（ikaros-favicon.png, 2.9KB）
const FAVICON_DATA = 'data:image/png;base64,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'

/* ── 模块级共享状态 ── */
let _pluginCtx: any = null
let IkarosURL = 'http://127.0.0.1:48920/'  // fallback，Node 侧启动时 patch 为实际端口

// 等待 IkarosURL 可达（探测 GET /，≤timeoutMs）。返回可达 URL，失败 null
async function waitCtReady(timeoutMs = 10000): Promise<string | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      await fetch(IkarosURL, { mode: 'no-cors', cache: 'no-store', signal: AbortSignal.timeout(1500) })
      return IkarosURL
    } catch {}
    await new Promise((r) => setTimeout(r, 300))
  }
  return null
}

const store = {
  panelOpen: false,
  dialogOpen: false,
  listeners: new Set<() => void>(),
  subscribe(fn: () => void) {
    store.listeners.add(fn)
    return () => { store.listeners.delete(fn) }
  },
  getSnapshot() { return store.panelOpen },
  getDialogSnapshot() { return store.dialogOpen },
  async toggle() {
    if (store.panelOpen) { store.panelOpen = false; store.notify(); return }
    // 打开前先等 CT 可达（避免 iframe 加载白屏）
    const ready = await waitCtReady(8000)
    if (!ready) console.warn('[ikaros-ct] CT not reachable at', IkarosURL, '- opening panel anyway')
    store.panelOpen = true
    store.notify()
  },
  close() { store.panelOpen = false; store.notify() },
  showDialog() { store.dialogOpen = true; store.notify() },
  hideDialog() { store.dialogOpen = false; store.notify() },
  confirmClose() {
    // 仅切回 dsh 视图；对话树服务继续运行（整个窗口关闭时才随之关闭）
    store.panelOpen = false
    store.dialogOpen = false
    store.notify()
  },
  notify() { for (const fn of store.listeners) fn() },
}

/* ── 全屏对话树视图（同窗口切换）：完整页面覆盖整个 dsh 窗口 ── */
function TreeView() {
  const open = useSyncExternalStore(store.subscribe, store.getSnapshot)
  // 监听 iframe 内左上角 I 徽标的双击信号 → 弹关闭确认框；主题色变化 → 按钮跟随
  useEffect(() => {
    if (!open) return
    const onMsg = (ev: MessageEvent) => {
      const d = ev.data
      if (!d || typeof d !== 'object') return
      if (d.type === 'ikaros-ct-close-dialog') store.showDialog()
      else if (d.type === 'ikaros-ct-theme' && typeof d.color === 'string') {
        document.documentElement.style.setProperty('--ct-brand', d.color)
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [open])
  if (!open) return null
  return (
    <div
      style={{
        position: 'fixed', inset: 0,
        zIndex: 100000, background: 'var(--dsw-alias-bg-base, #0d1117)',
      }}
    >
      <iframe
        style={{ border: 'none', width: '100%', height: '100%' }}
        src={IkarosURL}
        title="Ikaros 对话树"
      />
    </div>
  )
}

/* ── 关闭确认对话框 ── */
function CloseDialog() {
  const open = useSyncExternalStore(store.subscribe, store.getDialogSnapshot)
  if (!open) return null

  return (
    <>
      <style>{`
        /* ✓ 按钮光斑变量组（粉/蓝/紫/绿） */
        @property --ct-rg-1-x { syntax: '<percentage>'; inherits: true; initial-value: 0%; }
        @property --ct-rg-1-y { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        @property --ct-rg-2-x { syntax: '<percentage>'; inherits: true; initial-value: 20%; }
        @property --ct-rg-2-y { syntax: '<percentage>'; inherits: true; initial-value: 70%; }
        @property --ct-rg-3-x { syntax: '<percentage>'; inherits: true; initial-value: 70%; }
        @property --ct-rg-3-y { syntax: '<percentage>'; inherits: true; initial-value: 20%; }
        @property --ct-rg-4-x { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        @property --ct-rg-4-y { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        /* ✕ 按钮光斑变量组（青/橙/玫红/黄），与 ✓ 独立互不覆盖 */
        @property --ct-cg-1-x { syntax: '<percentage>'; inherits: true; initial-value: 100%; }
        @property --ct-cg-1-y { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        @property --ct-cg-2-x { syntax: '<percentage>'; inherits: true; initial-value: 80%; }
        @property --ct-cg-2-y { syntax: '<percentage>'; inherits: true; initial-value: 30%; }
        @property --ct-cg-3-x { syntax: '<percentage>'; inherits: true; initial-value: 30%; }
        @property --ct-cg-3-y { syntax: '<percentage>'; inherits: true; initial-value: 80%; }
        @property --ct-cg-4-x { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        @property --ct-cg-4-y { syntax: '<percentage>'; inherits: true; initial-value: 50%; }
        /* 内高光/外发光主色：随光斑游走同步换色（颜色溢出感） */
        @keyframes ct-fade-in {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        /* ✓ 光斑游走（慢速 8s，各光斑独立不规则移动，更随机） */
        @keyframes ct-rg-flow-a {
          8%  { --ct-rg-2-x: 15%;  --ct-rg-2-y: 85%; }
          22% { --ct-rg-3-x: 88%;  --ct-rg-3-y: 12%; }
          38% { --ct-rg-1-x: 62%;  --ct-rg-1-y: 78%; }
          56% { --ct-rg-4-x: 25%;  --ct-rg-4-y: 30%; }
          74% { --ct-rg-3-x: 40%;  --ct-rg-3-y: 65%; }
          92% { --ct-rg-2-x: 80%;  --ct-rg-2-y: 45%; }
        }
        /* ✕ 光斑游走（慢速 11s，节奏相位不同于 ✓） */
        @keyframes ct-cg-flow-b {
          10% { --ct-cg-1-x: 85%; --ct-cg-1-y: 20%; }
          28% { --ct-cg-4-x: 20%; --ct-cg-4-y: 75%; }
          46% { --ct-cg-2-x: 70%; --ct-cg-2-y: 30%; }
          64% { --ct-cg-3-x: 35%; --ct-cg-3-y: 85%; }
          82% { --ct-cg-1-x: 45%; --ct-cg-1-y: 60%; }
        }
        /* 无自发光；光斑漫游由 flow 动画提供 */
        .ct-dialog-overlay {
          position: fixed; inset: 0; z-index: 2147483647;
          backdrop-filter: blur(12px) saturate(1.2);
          -webkit-backdrop-filter: blur(12px) saturate(1.2);
          background: rgba(0,0,0,0.25);
          animation: ct-fade-in 0.25s ease-out;
        }
        /* 高斯模糊玻璃 + 无边框；内发光即流光延伸（彩虹色更浅） */
        .ct-dialog-btn {
          position: fixed; top: 43%;
          width: 112px; height: 112px; border-radius: 50%;
          cursor: pointer; border: none; padding: 0;
          font-family: 'Comic Sans MS', 'Chalkboard SE', 'Segoe UI', cursive;
          font-size: 50px; font-weight: 700; line-height: 1;
          display: flex; align-items: center; justify-content: center;
          backdrop-filter: blur(20px) saturate(1.4);
          -webkit-backdrop-filter: blur(20px) saturate(1.4);
          text-shadow: 0 0 10px rgba(0,0,0,0.35);
          transition: transform 0.15s ease, filter 0.25s ease;
        }
        /* ✓ 主题色（与对话树 "I" 按钮同步，由 iframe 广播 --ct-brand） */
        .ct-dialog-btn-confirm {
          left: 50%; top: 50%; transform: translate(-50%, -50%);
          /* 水平三分：左按钮中心在 100vw/3 处 */
          left: calc(100vw / 3);
          color: var(--ct-brand, #7c5cff);
          background:
            radial-gradient(circle at var(--ct-rg-1-x) var(--ct-rg-1-y), rgba(255,105,180,0.55), transparent 62%),
            radial-gradient(circle at var(--ct-rg-2-x) var(--ct-rg-2-y), rgba(65,105,225,0.55), transparent 68%),
            radial-gradient(circle at var(--ct-rg-3-x) var(--ct-rg-3-y), rgba(153,102,204,0.55), transparent 74%),
            radial-gradient(circle at var(--ct-rg-4-x) var(--ct-rg-4-y), rgba(50,205,50,0.5), transparent 80%),
            rgba(255,255,255,0.07);
          animation-name: ct-rg-flow-a;
          animation-duration: 8s;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
          animation-direction: alternate;
        }
        .ct-dialog-btn-cancel {
          /* 水平三分：右按钮中心在 100vw*2/3 处；垂直以圆心锚定 */
          left: calc(100vw * 2 / 3); top: 50%; transform: translate(-50%, -50%);
          /* ✕ 低饱和度中性浅色（去红） */
          color: color-mix(in srgb, var(--ct-brand, #7c5cff) 22%, #dcdce0);
          background:
            radial-gradient(circle at var(--ct-cg-1-x) var(--ct-cg-1-y), rgba(0,200,255,0.55), transparent 62%),
            radial-gradient(circle at var(--ct-cg-2-x) var(--ct-cg-2-y), rgba(255,140,0,0.55), transparent 68%),
            radial-gradient(circle at var(--ct-cg-3-x) var(--ct-cg-3-y), rgba(255,20,147,0.55), transparent 74%),
            radial-gradient(circle at var(--ct-cg-4-x) var(--ct-cg-4-y), rgba(255,215,0,0.5), transparent 80%),
            rgba(255,255,255,0.07);
          animation-name: ct-cg-flow-b;
          animation-duration: 11s;
          animation-timing-function: ease-in-out;
          animation-iteration-count: infinite;
          animation-direction: alternate;
        }
        /* hover：按钮的光更亮（无动画切换，保持流动） */
        .ct-dialog-btn:hover {
          transform: translate(-50%, -50%) scale(1.05);
          filter: brightness(1.3) saturate(1.15);
        }
        .ct-dialog-btn:active { transform: translate(-50%, -50%) scale(0.95); }
      `}</style>
      <div className="ct-dialog-overlay">
        {/* ✓ 确认返回 dsh（对话树服务不关闭，窗口整个关闭时才随之结束） */}
        <button type="button" className="ct-dialog-btn ct-dialog-btn-confirm" onClick={store.confirmClose} title="返回到 dsh">✓</button>
        {/* ✕ 取消（留在对话树） */}
        <button type="button" className="ct-dialog-btn ct-dialog-btn-cancel" onClick={store.hideDialog} title="取消">✕</button>
      </div>
    </>
  )
}

/* ── 双击检测 hook ── */
function useDoubleClick(onSingle: () => void, onDouble: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onClick = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
      return  // 第二下 click → 不触发 single，交给 onDoubleClick
    }
    timer.current = setTimeout(() => {
      timer.current = null
      onSingle()
    }, 250)
  }, [onSingle])
  const onDoubleClick = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
    onDouble()
  }, [onDouble])
  return { onClick, onDoubleClick }
}

/* ── sidebar.footer.action 入口按钮 ── */
function SidebarButton({ wide }: { wide?: boolean }) {
  // 单击切换；双击在按钮上不再触发关闭确认框（关闭确认只由对话树页面内双击 "I" 触发）
  const { onClick, onDoubleClick } = useDoubleClick(
    () => store.toggle(),
    () => {},
  )
  // 彩蛋：偶尔触发 IKAROS 文字彩虹流动（随机小概率）
  const [rainbow, setRainbow] = useState(false)
  useEffect(() => {
    if (!wide) return
    let t: ReturnType<typeof setTimeout> | null = null
    const iv = setInterval(() => {
      if (Math.random() < 0.28) {
        setRainbow(true)
        if (t) clearTimeout(t)
        t = setTimeout(() => setRainbow(false), 2600)
      }
    }, 9000)
    return () => { clearInterval(iv); if (t) clearTimeout(t) }
  }, [wide])
  if (!wide) {
    // 窄侧边栏：仅图标
    return (
      <button
        type="button"
        onClick={onClick}
        onDoubleClick={onDoubleClick}
        style={{
          cursor: 'pointer', border: '1px solid var(--dsw-alias-border-l2, #30363d)',
          background: 'var(--dsw-alias-button-elevated-fill, #21262d)',
          color: 'var(--dsw-alias-label-primary, #e6edf3)', borderRadius: 8,
          padding: 6, width: 36, height: 34,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          font: '13px/1.4 var(--dsh-font-sans, system-ui)', flex: 'none',
        }}
      >
        <img src={FAVICON_DATA} alt="" style={{ width: 18, height: 18 }} />
      </button>
    )
  }
  // 宽侧边栏：logo 居左，IKAROS 全大写 / 思源 / 字间距 / 主题色 / 彩蛋彩虹
  return (
    <>
      <style>{`
        .ct-sidebar-btn {
          cursor: pointer;
          border: 1px solid var(--dsw-alias-border-l2, #30363d);
          background: var(--dsw-alias-button-elevated-fill, #21262d);
          border-radius: 14px;
          white-space: nowrap; flex: none;
          display: flex; align-items: center; justify-content: center;
          width: 100%; height: 38px; position: relative;
          transition: background 0.15s ease, transform 0.1s ease, border-color 0.15s ease;
        }
        .ct-sidebar-btn:hover {
          background: var(--dsw-alias-button-floating-hover, #2d333b);
          border-color: var(--dsw-alias-border-l3, #3d444d);
        }
        .ct-sidebar-btn:active { transform: scale(0.98); }
        .ct-sidebar-btn:focus-visible {
          outline: 2px solid var(--dsw-alias-brand-primary, #4d6bfe);
          outline-offset: 2px;
        }
        .ct-brand-text {
          color: var(--ct-brand, #7c5cff);   /* 对话树主题色 */
          font-family: 'Source Han Sans SC','Source Han Sans','Noto Sans SC','Microsoft YaHei',sans-serif;
          font-weight: 700; letter-spacing: 0.12em;
        }
        .ct-brand-text.ct-rainbow {
          background: linear-gradient(90deg,#f48fb1,#8ab4f8,#7c5cff,#4dd91f,#ffd54f,#f48fb1);
          background-size: 300% 100%;
          -webkit-background-clip: text; background-clip: text;
          -webkit-text-fill-color: transparent; color: transparent;
          animation: ct-brand-flow 1.6s linear infinite;
        }
        @keyframes ct-brand-flow { to { background-position: 300% 0; } }
      `}</style>
      <button
        type="button"
        className="ct-sidebar-btn"
        onClick={onClick}
        onDoubleClick={onDoubleClick}
      >
        <img
          src={FAVICON_DATA}
          alt=""
          style={{
            position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)',
            width: 20, height: 20,
          }}
        />
        <span className={'ct-brand-text' + (rainbow ? ' ct-rainbow' : '')}>IKAROS</span>
      </button>
    </>
  )
}

/* ── 插件装配 ── */
const inject = ['slots']

function apply(ctx: any) {
  _pluginCtx = ctx
  ctx.slots?.inject?.('sidebar.footer.action', () =>
    ctx.slots.register(
      { name: 'sidebar.footer.action', id: 'ikaros-conversation-tree', order: 110, label: '对话树' },
      SidebarButton,
    ),
  )
  // 全屏对话树视图（同窗口切换）
  ctx.slots?.inject?.('shell.overlay', () =>
    ctx.slots.register(
      { name: 'shell.overlay', id: 'ikaros-conversation-tree-view', order: 8000, label: '对话树视图' },
      TreeView,
    ),
  )
  // 关闭确认对话框（层级最高）
  ctx.slots?.inject?.('shell.overlay', () =>
    ctx.slots.register(
      { name: 'shell.overlay', id: 'ikaros-conversation-tree-dialog', order: 9999, label: '对话树关闭确认' },
      CloseDialog,
    ),
  )
}

export { apply, inject }
export default { apply, inject }