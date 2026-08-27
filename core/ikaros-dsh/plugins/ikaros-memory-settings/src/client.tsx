// ikaros-memory-settings —— client 侧 bundle (浏览器 React 卡)
//
// 注册 dsh 设置页 'settings.section' slot, id='ikaros-memory-settings', 渲染
// 记忆控制面板 React 卡。仿造 @deepseek-ai/dsh-client-ui-settings-models 的
// settings.section 注册模式: t() 做 i18n, settingsStyle css class, 调
// connection.api.ikarosMemory.* 与 host-bridge 通信 (见 src/index.ts)。
//
// 面板功能:
//   - 状态: 端口 / PID / 模型 / 向量数 / 模型存在
//   - 启停 embedding: 调 api.ikarosMemory.{startEmbedding,stopEmbedding}
//   - 模型列表 (扫描 core/memory_v5/models/*.gguf) + 切换 (switchModel)
//   - 下载: HF repo + filename, 调 api.ikarosMemory.downloadModel
//   - 重建向量: rebuildVectors (长任务, 显示进度文本)
//
// i18n: NS='ikaros-memory-settings' 注册到 ctx.locale (zh + en)。

import { useEffect, useState, useCallback, useRef } from 'react'

const NS = 'ikaros-memory-settings'

interface ModelEntry {
  filename: string
  absPath: string
  sizeBytes: number
  isActive: boolean
  guessedDims: number | null
  guessedType: 'embedding' | 'llm' | 'unknown'
}

interface Status {
  port: number; portOpen: boolean; pid: number | null
  model: string; modelExists: boolean
  vectorsCount: number | null; chromaPath: string
  ikarosRoot: string; ikarosPython: string; ikarosLlama: string
}

interface HostApi {
  listModels: () => Promise<{ models: ModelEntry[]; activeModel: string; modelsDir: string }>
  getStatus: () => Promise<Status>
  startEmbedding: () => Promise<{ ok: boolean; message: string; pid: number | null }>
  stopEmbedding: () => Promise<{ ok: boolean; killed: number; pids: number[]; message: string }>
  switchModel: (filename: string) => Promise<{ ok: boolean; message: string }>
  downloadModel: (args: { repo: string; filename: string }) => Promise<{ ok: boolean; message: string; tool: string | null; outPath: string }>
  rebuildVectors: () => Promise<{ ok: boolean; message: string; stdout: string }>
}

// cordis-loader 通过 'inject' 数组声明 client 卡依赖的服务 (settings.section 路由需要 slots/locale/connection/settingsScope/settingsSchema/remote 全套)
const inject = ['slots', 'locale', 'connection', 'remote', 'settingsScope', 'settingsSchema']

const zh = {
  title: '记忆系统',
  desc: '控制 Ikaros 记忆系统的本地 embedding 服务 (llama-server bge-m3 @ :8587)、模型下载与切换、向量重建。所有数据本地运行, 无云端依赖。',
  status: '服务状态',
  port: '端口',
  portOpen: '在线',
  portClosed: '离线',
  pid: 'PID',
  model: '当前模型',
  modelMissing: '文件不存在',
  vectorsCount: '向量数',
  llamaPath: 'llama-server',
  pythonPath: 'Python',
  startEmbed: '启动 embedding',
  stopEmbedding: '停止 embedding',
  refresh: '刷新',
  modelsSection: '模型管理',
  switch: '切换',
  switchConfirm: '切换会重启 llama-server, 当前嵌入会话会断。',
  active: '当前',
  size: '大小',
  dims: '维度',
  type: '类型',
  downloadSection: '下载模型 (HuggingFace)',
  repo: 'HF 仓库',
  repoPh: 'BAAI/bge-m3 或 nomic-ai/nomic-embed-text-v1.5-GGUF',
  filename: '文件名',
  filenamePh: '例如 bge-m3-q8_0.gguf 或 nomic-embed-text-v1.5.f16.gguf',
  download: '下载',
  rebuildSection: '向量重建',
  rebuild: '重嵌 Chroma 向量',
  rebuildDesc: '按当前模型重新嵌入所有记忆 (耗时, 取决于向量数)',
  rebuilding: '重建中…',
  noApi: '未连接 host-bridge (api.ikarosMemory 不可用)',
  embRunning: 'llama-server 已运行',
  embStopped: 'llama-server 未运行',
}

const en = {
  title: 'Memory System',
  desc: 'Control the local embedding service (llama-server bge-m3 @ :8587), download/switch models, rebuild Chroma vectors. Fully local, no cloud dependency.',
  status: 'Service Status',
  port: 'Port',
  portOpen: 'Online',
  portClosed: 'Offline',
  pid: 'PID',
  model: 'Current Model',
  modelMissing: 'file missing',
  vectorsCount: 'Vectors',
  llamaPath: 'llama-server',
  pythonPath: 'Python',
  startEmbed: 'Start embedding',
  stopEmbedding: 'Stop embedding',
  refresh: 'Refresh',
  modelsSection: 'Models',
  switch: 'Switch',
  switchConfirm: 'Switching restarts llama-server; current embedding session will drop.',
  active: 'Active',
  size: 'Size',
  dims: 'Dims',
  type: 'Type',
  downloadSection: 'Download Model (HuggingFace)',
  repo: 'HF Repo',
  repoPh: 'BAAI/bge-m3 or nomic-ai/nomic-embed-text-v1.5-GGUF',
  filename: 'Filename',
  filenamePh: 'e.g. bge-m3-q8_0.gguf or nomic-embed-text-v1.5.f16.gguf',
  download: 'Download',
  rebuildSection: 'Vector Rebuild',
  rebuild: 'Rebuild Chroma Vectors',
  rebuildDesc: 'Re-embed everything with the current model (slow, depends on vector count)',
  rebuilding: 'Rebuilding…',
  noApi: 'host-bridge not connected (api.ikarosMemory unavailable)',
  embRunning: 'llama-server is running',
  embStopped: 'llama-server is stopped',
}

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function MemorySettingsCard(props: { api: unknown; t: (k: string) => string }) {
  const api = props.api as { ikarosMemory?: HostApi } | null
  const t = props.t
  const host = api?.ikarosMemory

  // 没 host-bridge (e.g. host 端 class Service 还没 mount 时) — 用本地兜底空数据
  const [status, setStatus] = useState<Status | null>(null)
  const [models, setModels] = useState<ModelEntry[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string>('')
  const [downloadRepo, setDownloadRepo] = useState<string>('BAAI/bge-m3')
  const [downloadFile, setDownloadFile] = useState<string>('bge-m3-q8_0.gguf')

  const refresh = useCallback(async () => {
    if (!host) return
    setBusy('refresh')
    setMsg('')
    try {
      const [s, m] = await Promise.all([host.getStatus(), host.listModels()])
      setStatus(s); setModels(m.models)
    } catch (e) {
      setMsg(`刷新失败: ${(e as Error)?.message || e}`)
    } finally { setBusy(null) }
  }, [host])

  useEffect(() => { void refresh() }, [refresh])

  const handleStart = async () => {
    if (!host) return; setBusy('start'); setMsg('')
    try {
      const r = await host.startEmbedding(); setMsg(r.message)
      await refresh()
    } catch (e) { setMsg(`启动失败: ${(e as Error)?.message || e}`) }
    finally { setBusy(null) }
  }

  const handleStop = async () => {
    if (!host) return; setBusy('stop'); setMsg('')
    try {
      const r = await host.stopEmbedding(); setMsg(r.message)
      await refresh()
    } catch (e) { setMsg(`停止失败: ${(e as Error)?.message || e}`) }
    finally { setBusy(null) }
  }

  const handleSwitch = async (filename: string) => {
    if (!host) return; setBusy('switch:' + filename); setMsg('')
    if (!confirm(t('switchConfirm'))) { setBusy(null); return }
    try {
      const r = await host.switchModel(filename); setMsg(r.message)
      await refresh()
    } catch (e) { setMsg(`切换失败: ${(e as Error)?.message || e}`) }
    finally { setBusy(null) }
  }

  const handleDownload = async () => {
    if (!host) return; setBusy('download'); setMsg('')
    if (!downloadRepo || !downloadFile) { setMsg('请填 repo 与 filename'); setBusy(null); return }
    try {
      const r = await host.downloadModel({ repo: downloadRepo, filename: downloadFile })
      setMsg(r.message); await refresh()
    } catch (e) { setMsg(`下载失败: ${(e as Error)?.message || e}`) }
    finally { setBusy(null) }
  }

  const handleRebuild = async () => {
    if (!host) return; setBusy('rebuild'); setMsg(t('rebuilding'))
    try {
      const r = await host.rebuildVectors(); setMsg(r.message)
    } catch (e) { setMsg(`重建失败: ${(e as Error)?.message || e}`) }
    finally { setBusy(null) }
  }

  if (!host) {
    return <div className="ikarosMemSettings__section"><p className="ikarosMemSettings__noApi">{t('noApi')}</p></div>
  }

  return (
    <div className="ikarosMemSettings__root">
      <style>{`
        .ikarosMemSettings__root { max-width: 720px; color: var(--dsw-alias-label-primary); display: flex; flex-direction: column; gap: 16px; }
        .ikarosMemSettings__section { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border: 1px solid var(--dsw-alias-border-l2); border-radius: 12px; background: var(--dsw-alias-bg-floating); }
        .ikarosMemSettings__section h3 { margin: 0 0 4px; font-size: 14px; font-weight: 500; color: var(--dsw-alias-label-primary); }
        .ikarosMemSettings__section p { margin: 0; font-size: 12px; color: var(--dsw-alias-label-tertiary); line-height: 18px; }
        .ikarosMemSettings__row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .ikarosMemSettings__kv { display: flex; gap: 6px; font-size: 12px; }
        .ikarosMemSettings__kv dt { color: var(--dsw-alias-label-tertiary); min-width: 80px; }
        .ikarosMemSettings__kv dd { margin: 0; color: var(--dsw-alias-label-primary); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
        .ikarosMemSettings__btn { height: 32px; padding: 0 14px; border: 1px solid var(--dsw-alias-border-l2); border-radius: 8px; background: var(--dsw-alias-button-primary-fill); color: var(--dsw-alias-label-primary-foreground); cursor: pointer; font: inherit; font-size: 13px; }
        .ikarosMemSettings__btn:hover:not(:disabled) { background: var(--dsw-alias-button-primary-fill-hover, var(--dsw-alias-button-primary-fill)); }
        .ikarosMemSettings__btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .ikarosMemSettings__btn--secondary { background: var(--dsw-alias-button-elevated-fill, #21262d); color: var(--dsw-alias-label-primary, #e6edf3); }
        .ikarosMemSettings__btn--danger { background: var(--dsw-alias-state-error-primary, #b91c1c); }
        .ikarosMemSettings__input { width: 100%; padding: 7px 10px; border: 1px solid var(--dsw-alias-border-l2); border-radius: 8px; background: var(--dsw-alias-bg-input, transparent); color: var(--dsw-alias-label-primary); font: inherit; font-size: 13px; }
        .ikarosMemSettings__badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 500; }
        .ikarosMemSettings__badge--ok { background: color-mix(in srgb, var(--dsw-alias-state-success-primary, #10b981) 18%, transparent); color: var(--dsw-alias-state-success-primary, #10b981); }
        .ikarosMemSettings__badge--err { background: color-mix(in srgb, var(--dsw-alias-state-error-primary, #b91c1c) 18%, transparent); color: var(--dsw-alias-state-error-primary, #b91c1c); }
        .ikarosMemSettings__modelRow { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border: 1px solid var(--dsw-alias-border-l2); border-radius: 8px; }
        .ikarosMemSettings__modelRow--active { border-color: var(--dsw-alias-brand-primary, #4d6bfe); background: color-mix(in srgb, var(--dsw-alias-brand-primary, #4d6bfe) 6%, transparent); }
        .ikarosMemSettings__msg { font-size: 12px; padding: 6px 10px; border-radius: 6px; background: var(--dsw-alias-bg-floating, #1c2128); color: var(--dsw-alias-label-secondary); white-space: pre-wrap; word-break: break-all; }
      `}</style>

      <p>{t('desc')}</p>

      {/* ── 服务状态 ── */}
      <div className="ikarosMemSettings__section">
        <h3>{t('status')}</h3>
        {status ? (
          <>
            <div className="ikarosMemSettings__kv">
              <dt>{t('port')}</dt>
              <dd>:{status.port}</dd>
              <dd>
                <span className={`ikarosMemSettings__badge ${status.portOpen ? 'ikarosMemSettings__badge--ok' : 'ikarosMemSettings__badge--err'}`}>
                  {status.portOpen ? t('portOpen') : t('portClosed')}
                </span>
              </dd>
            </div>
            {status.pid !== null && <div className="ikarosMemSettings__kv"><dt>{t('pid')}</dt><dd>{status.pid}</dd></div>}
            <div className="ikarosMemSettings__kv"><dt>{t('model')}</dt><dd>{status.model}{!status.modelExists ? ` (${t('modelMissing')})` : ''}</dd></div>
            {status.vectorsCount !== null && <div className="ikarosMemSettings__kv"><dt>{t('vectorsCount')}</dt><dd>{status.vectorsCount}</dd></div>}
            <div className="ikarosMemSettings__kv"><dt>{t('pythonPath')}</dt><dd title={status.ikarosPython}>{status.ikarosPython.length > 50 ? '…' + status.ikarosPython.slice(-47) : status.ikarosPython}</dd></div>
            <div className="ikarosMemSettings__kv"><dt>{t('llamaPath')}</dt><dd title={status.ikarosLlama}>{status.ikarosLlama.length > 50 ? '…' + status.ikarosLlama.slice(-47) : status.ikarosLlama}</dd></div>
          </>
        ) : (
          <p>…</p>
        )}
        <div className="ikarosMemSettings__row">
          <button className="ikarosMemSettings__btn ikarosMemSettings__btn--secondary" onClick={refresh} disabled={busy === 'refresh'}>
            {t('refresh')}
          </button>
          {status?.portOpen
            ? <button className="ikarosMemSettings__btn ikarosMemSettings__btn--danger" onClick={handleStop} disabled={busy !== null}>{t('stopEmbedding')}</button>
            : <button className="ikarosMemSettings__btn" onClick={handleStart} disabled={busy !== null}>{t('startEmbed')}</button>
          }
        </div>
      </div>

      {/* ── 模型管理 ── */}
      <div className="ikarosMemSettings__section">
        <h3>{t('modelsSection')}</h3>
        {(models && models.length > 0) ? (
          models.map((m => (
            <div key={m.filename} className={`ikarosMemSettings__modelRow ${m.isActive ? 'ikarosMemSettings__modelRow--active' : ''}`}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{m.filename} {m.isActive && <span className="ikarosMemSettings__badge ikarosMemSettings__badge--ok">{t('active')}</span>}</div>
                <div style={{ fontSize: 11, color: 'var(--dsw-alias-label-tertiary)', display: 'flex', gap: 12 }}>
                  <span>{t('size')}: {fmtSize(m.sizeBytes)}</span>
                  {m.guessedDims && <span>{t('dims')}: {m.guessedDims}</span>}
                  <span>{t('type')}: {m.guessedType}</span>
                </div>
              </div>
              {!m.isActive && (
                <button className="ikarosMemSettings__btn ikarosMemSettings__btn--secondary" onClick={() => handleSwitch(m)} disabled={busy !== null}>
                  {t('switch')}
                </button>
              )}
            </div>
          )))
        ) : (
          <p>暂无 .gguf 模型 (用下方下载入口拉取)</p>
        )}
      </div>

      {/* ── 下载 (HF) ── */}
      <div className="ikarosMemSettings__section">
        <h3>{t('downloadSection')}</h3>
        <div className="ikarosMemSettings__row">
          <input className="ikarosMemSettings__input" placeholder={t('repoPh')} value={downloadRepo} onChange={(e) => setDownloadRepo(e.target.value)} />
        </div>
        <div className="ikarosMemSettings__row">
          <input className="ikarosMemSettings__input" placeholder={t('filenamePh')} value={downloadFile} onChange={(e) => setDownloadFile(e.target.value)} />
        </div>
        <div className="ikarosMemSettings__row">
          <button className="ikarosMemSettings__btn" onClick={handleDownload} disabled={busy !== null}>{t('download')}</button>
        </div>
      </div>

      {/* ── 向量重建 ── */}
      <div className="ikarosMemSettings__section">
        <h3>{t('rebuildSection')}</h3>
        <p>{t('rebuildDesc')}</p>
        <div className="ikarosMemSettings__row">
          <button className="ikarosMemSettings__btn ikarosMemSettings__btn--danger" onClick={handleRebuild} disabled={busy !== null}>
            {busy === 'rebuild' ? t('rebuilding') : t('rebuild')}
          </button>
        </div>
      </div>

      {msg && <div className="ikarosMemSettings__msg">{msg}</div>}
    </div>
  )
}

function apply(ctx: any) {
  ctx.effect(() => {
    const locale = ctx.locale
    if (locale && typeof locale.register === 'function') {
      locale.register(NS, { zh, en })
    }
  }, 'ikaros-memory-settings: locale')

  const t = ctx.locale.bind(NS)

  // settings.section 的 owner props 只有 { close } (dsh-client-ui-settings-general 渲染层决定),
  // 不携带 api。host-bridge 通过 ctx.get('connection').api.ikarosMemory 获取 (仿 settings-models
  // 调 api.llm.providers 模式)。
  const connection = ctx.get('connection') as { api: { ikarosMemory?: HostApi } } | undefined
  const api = connection?.api
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: 'ikaros-memory-settings',
    order: 50, // settings-models 在 10; 我们在它之后 (同 nav 内的独立 tab, 还是独立 entry 由 settings shell 决定)
    label: () => t('title'),
    inject: () => ({ api, t }),
  }, MemorySettingsCard))
}

export { apply, inject }
export default { apply, inject }