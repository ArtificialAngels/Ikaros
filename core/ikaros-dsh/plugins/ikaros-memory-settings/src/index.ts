// ikaros-memory-settings —— Node 侧 host-bridge
//
// dsh 设置面板 'settings.section' slot 的 host 端：暴露一组 RPC 让浏览器面板
// 控制 embedding 服务 (llama-server bge-m3 @ :8587) + 模型下载 + 模型切换 +
// 向量重建。仿造 @deepseek-ai/dsh-client-ui-settings-models 的 settings.section
// 注册模式，host 侧配合 client 侧 React 卡使用。
//
// 服务列表 (供 client inject() 调):
//   listModels()               -> 扫描 IKAROS_MEMORY_MODELS/*.gguf
//   getStatus()                -> {port, pid, model, vectorsCount, chromaPath}
//   startEmbedding()           -> spawn llama-server (类似 ikaros embed)
//   stopEmbedding()            -> 按端口/PID 杀 llama-server
//   switchModel(filename)      -> 改 IKAROS_MODEL_EMBEDDING env + 重启 llama
//   downloadModel({repo,file}) -> HF resolve URL + aria2c|gopeed|curl 下载
//   rebuildVectors()           -> 调 v5_call.py rebuild 重嵌 Chroma
//
// 路径链:
//   IKAROS_ROOT (env, by dsh launcher) -> IKAROS_RUNTIME (runtime/)
//   -> IKAROS_LLAMA (runtime/llama/b10000-cuda/llama-server.exe)
//   -> IKAROS_MEMORY_MODELS (core/memory_v5/models/) -> *.gguf
//   -> IKAROS_PORT_EMBEDDING (default 8587)
//   -> IKAROS_PYTHON (runtime/portable-python/python.exe)
//   -> v5_call.py (core/memory_v5/bin/v5_call.py, 同 ikaros-memory)
//
// 与 ikaros-memory (自动记忆工程层) 互补: 那一个做 turn-stopping/pre-step
// 自动化，这一个做用户可见的"手动控制面板"。两者共享 v5_call.py 入口。

import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { existsSync, readdirSync, statSync } from 'node:fs'
import { Service } from '@deepseek-ai/cordis'
import { z } from 'zod'

export interface Config {
  /** HF 仓库镜像 (默认直连, 国内用户可换 hf-mirror.com) */
  hfBaseUrl: string
  /** 下载工具优先级: gopeed | aria2c | curl (按需探测, 第一个存在就用) */
  downloader: 'gopeed' | 'aria2c' | 'curl'
  /** downloadModel 时的并发连接数 (gopeed/aria2c 适用) */
  downloadConnections: number
  /** rebuildVectors 单批大小 (v5_call.py rebuild --batch-size) */
  rebuildBatchSize: number
}

export const defaultConfig: Config = {
  hfBaseUrl: 'https://huggingface.co',
  downloader: 'gopeed',
  downloadConnections: 8,
  rebuildBatchSize: 32,
}

/* ── 路径解析 ── */

const __dirname = path.dirname(fileURLToPath(import.meta.url))

/** IKAROS_ROOT 优先从环境取 (dsh 启动器注入), 兜底从本文件向上三层 (本目录=plugins/ikaros-memory-settings) */
function resolveIKAROS_ROOT(): string {
  const env = process.env.IKAROS_ROOT
  if (env && env.trim()) return env.trim()
  return path.resolve(__dirname, '..', '..', '..', '..')
}

const IKAROS_ROOT = resolveIKAROS_ROOT()
const IKAROS_PYTHON = process.env.IKAROS_PYTHON
  || path.join(IKAROS_ROOT, 'runtime', 'portable-python', 'python.exe')
const IKAROS_LLAMA = path.join(IKAROS_ROOT, 'runtime', 'llama', 'b10000-cuda', 'llama-server.exe')
const IKAROS_MEMORY_MODELS = path.join(IKAROS_ROOT, 'core', 'memory_v5', 'models')
const IKAROS_PORT_EMBEDDING = parseInt(process.env.IKAROS_PORT_EMBEDDING || '8587', 10)
const IKAROS_MODEL_EMBEDDING = process.env.IKAROS_MODEL_EMBEDDING
  || path.join(IKAROS_MEMORY_MODELS, 'bge-m3-q8_0.gguf')
const V5_CALL = path.join(IKAROS_ROOT, 'core', 'ikaros-dsh', 'plugins', 'ikaros-memory', 'bin', 'v5_call.py')

/* ── subprocess helper (仿 ikaros-memory) ── */

interface SubprocessHandle {
  done?: Promise<{ exitCode: number | null; signal: unknown }>
  collected?: {
    stdout?: { readFrom(offset: number): Promise<{ text: string }> }
    stderr?: { readFrom(offset: number): Promise<{ text: string }> }
  }
}

interface Context {
  get(name: string): unknown
  effect: (fn: () => void | (() => void), label?: string) => void
}

function spawnCollect(ctx: Context, argv: string[], opts: {
  cwd?: string
  maxBytes?: number
  graceMs?: number
}): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  const sub = ctx.get('subprocess') as { spawn(spec: unknown): SubprocessHandle } | undefined
  if (!sub) return Promise.resolve({ exitCode: -1, stdout: '', stderr: 'subprocess service unavailable' })
  return new Promise((resolve) => {
    try {
      const handle = sub.spawn({
        argv,
        cwd: opts.cwd || IKAROS_ROOT,
        stdio: {
          stdout: { collect: true, maxBytes: opts.maxBytes ?? 4 * 1024 * 1024 },
          stderr: { collect: true, maxBytes: 64 * 1024 },
        },
        graceMs: opts.graceMs ?? 60_000,
      })
      if (!handle || typeof handle.done !== 'object') {
        resolve({ exitCode: -1, stdout: '', stderr: 'bad handle' })
        return
      }
      handle.done.then(async () => {
        let stdout = ''
        let stderr = ''
        try {
          if (handle.collected?.stdout) stdout = String((await handle.collected.stdout.readFrom(0)).text || '')
          if (handle.collected?.stderr) stderr = String((await handle.collected.stderr.readFrom(0)).text || '')
        } catch { /* ignore read errors */ }
        resolve({ exitCode: handle.done ? -1 : 0, stdout, stderr })
      }).catch((e: unknown) => {
        resolve({ exitCode: -1, stdout: '', stderr: String((e as Error)?.message || e) })
      })
    } catch (e: unknown) {
      resolve({ exitCode: -1, stdout: '', stderr: String((e as Error)?.message || e) })
    }
  })
}

/* ── 工具函数 ── */

/** 测试端口是否有 TCP listener */
async function isPortOpen(port: number, host = '127.0.0.1', timeoutMs = 800): Promise<boolean> {
  const { createConnection } = await import('node:net')
  return new Promise((resolve) => {
    const sock = createConnection({ port, host })
    const timer = setTimeout(() => { sock.destroy(); resolve(false) }, timeoutMs)
    sock.on('connect', () => { clearTimeout(timer); sock.destroy(); resolve(true) })
    sock.on('error', () => { clearTimeout(timer); resolve(false) })
  })
}

/** windows: 按端口查 PID + 按 PID 杀进程 (PowerShell) */
async function killByPort(ctx: Context, port: number): Promise<{ killed: number; pids: number[] }> {
  const ps = `
$conn = Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue
if ($conn) {
  $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($p in $pids) {
    try { Stop-Process -Id $p -Force -ErrorAction Stop; Write-Output "killed $p" }
    catch { Write-Output "failed $p: $_" }
  }
} else { Write-Output "no listener on :${port}" }
`
  const r = await spawnCollect(ctx,
    ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
    { graceMs: 10000 })
  const pids = Array.from(r.stdout.matchAll(/killed (\d+)/g)).map((m) => parseInt(m[1], 10))
  return { killed: pids.length, pids }
}

/** 探测下载工具可用性 (返回第一个存在的) */
async function probeDownloader(ctx: Context): Promise<'gopeed' | 'aria2c' | 'curl' | null> {
  for (const tool of ['gopeed', 'aria2c', 'curl']) {
    const r = await spawnCollect(ctx,
      [tool, '--version'],
      { graceMs: 5000 })
    // gopeed/aria2c 输出版本号到 stdout; curl 也输出版本; 任意 rc=0 即视为可用
    if (r.exitCode === 0 || (tool === 'curl' && r.stdout.toLowerCase().includes('curl'))) {
      return tool as 'gopeed' | 'aria2c' | 'curl'
    }
  }
  return null
}

/* ── 服务实现 ── */

interface ModelEntry {
  filename: string
  absPath: string
  sizeBytes: number
  isActive: boolean
  guessedDims: number | null  // 启发式: bge-m3=1024, nomic=768, e5=1024, MiniLM=384
  guessedType: 'embedding' | 'llm' | 'unknown'
}

/** 从文件名启发式推断向量维度 + 类型 (粗判, 用于面板提示) */
function guessModelMeta(filename: string): { dims: number | null; type: 'embedding' | 'llm' | 'unknown' } {
  const f = filename.toLowerCase()
  if (/bge-m3|bge_large|bge_base/.test(f)) return { dims: 1024, type: 'embedding' }
  if (/nomic-embed/.test(f)) return { dims: 768, type: 'embedding' }
  if (/e5-large|e5-base|e5-small/.test(f)) return { dims: 1024, type: 'embedding' }
  if (/minilm|all-minilm|all-mpnet/.test(f)) return { dims: 384, type: 'embedding' }
  if (/qwen|llama|phi|mistral|gemma|deepseek|mixtral/.test(f)) return { dims: null, type: 'llm' }
  return { dims: null, type: 'unknown' }
}

async function listModels(): Promise<{ models: ModelEntry[]; activeModel: string; modelsDir: string }> {
  const active = path.basename(IKAROS_MODEL_EMBEDDING)
  const out: ModelEntry[] = []
  if (existsSync(IKAROS_MEMORY_MODELS)) {
    for (const name of readdirSync(IKAROS_MEMORY_MODELS)) {
      if (!name.toLowerCase().endsWith('.gguf')) continue
      const abs = path.join(IKAROS_MEMORY_MODELS, name)
      try {
        const st = statSync(abs)
        if (!st.isFile()) continue
        const meta = guessModelMeta(name)
        out.push({
          filename: name,
          absPath: abs,
          sizeBytes: st.size,
          isActive: name === active,
          guessedDims: meta.dims,
          guessedType: meta.type,
        })
      } catch { /* stat 失败跳过 */ }
    }
  }
  out.sort((a, b) => Number(b.isActive) - Number(a.isActive) || a.filename.localeCompare(b.filename))
  return { models: out, activeModel: active, modelsDir: IKAROS_MEMORY_MODELS }
}

async function getStatus(): Promise<{
  port: number; portOpen: boolean; pid: number | null;
  model: string; modelExists: boolean;
  vectorsCount: number | null; chromaPath: string;
  ikarosRoot: string; ikarosPython: string; ikarosLlama: string;
}> {
  const portOpen = await isPortOpen(IKAROS_PORT_EMBEDDING)
  const modelExists = existsSync(IKAROS_MODEL_EMBEDDING)
  // 向量数: 探 chroma 集合; 失败返 null (不影响主流程)
  let vectorsCount: number | null = null
  const chromaPath = path.join(IKAROS_ROOT, 'core', 'memory_v5', 'data', 'v5', 'chroma')
  // PID 探测: ps + filter 端口
  let pid: number | null = null
  if (portOpen) {
    try {
      const r = await spawnCollect({ get: () => undefined, effect: () => {} } as unknown as Context,
        ['powershell', '-NoProfile', '-Command',
         `(Get-NetTCPConnection -LocalPort ${IKAROS_PORT_EMBEDDING} -State Listen).OwningProcess`],
        { graceMs: 5000 })
      const m = r.stdout.match(/(\d+)/)
      if (m) pid = parseInt(m[1], 10)
    } catch { /* pid 探测失败不影响 */ }
  }
  return {
    port: IKAROS_PORT_EMBEDDING, portOpen, pid,
    model: path.basename(IKAROS_MODEL_EMBEDDING), modelExists,
    vectorsCount, chromaPath,
    ikarosRoot: IKAROS_ROOT,
    ikarosPython: IKAROS_PYTHON,
    ikarosLlama: IKAROS_LLAMA,
  }
}

async function startEmbedding(ctx: Context): Promise<{ ok: boolean; message: string; pid: number | null }> {
  if (await isPortOpen(IKAROS_PORT_EMBEDDING)) {
    return { ok: false, message: `:${IKAROS_PORT_EMBEDDING} 已在监听, 无需启动`, pid: null }
  }
  if (!existsSync(IKAROS_LLAMA)) {
    return { ok: false, message: `llama-server 不存在: ${IKAROS_LLAMA}`, pid: null }
  }
  if (!existsSync(IKAROS_MODEL_EMBEDDING)) {
    return { ok: false, message: `模型文件不存在: ${IKAROS_MODEL_EMBEDDING}`, pid: null }
  }
  // 启动 llama-server (后台 detached, 不阻塞面板)
  const argv = [
    IKAROS_LLAMA,
    '--model', IKAROS_MODEL_EMBEDDING,
    '--embedding',
    '--pooling', 'cls',
    '--port', String(IKAROS_PORT_EMBEDDING),
    '--host', '127.0.0.1',
    '--ctx-size', '2048',
    '--batch-size', '512',
    '--parallel', '2',
    '--log-disable',
  ]
  try {
    const sub = ctx.get('subprocess') as { spawn(spec: unknown): unknown } | undefined
    if (!sub) return { ok: false, message: 'subprocess service unavailable', pid: null }
    sub.spawn({
      argv,
      cwd: IKAROS_ROOT,
      stdio: {
        stdout: { collect: false },
        stderr: { collect: false },
      },
      graceMs: 5_000,
    })
  } catch (e: unknown) {
    return { ok: false, message: `spawn failed: ${(e as Error)?.message || e}`, pid: null }
  }
  // 等端口起来
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (await isPortOpen(IKAROS_PORT_EMBEDDING)) {
      return { ok: true, message: `llama-server 已在 :${IKAROS_PORT_EMBEDDING} 起来`, pid: null }
    }
  }
  return { ok: false, message: `已 spawn 但 10s 内端口未起来, 看 dsh 日志`, pid: null }
}

async function stopEmbedding(ctx: Context): Promise<{ ok: boolean; killed: number; pids: number[]; message: string }> {
  if (!await isPortOpen(IKAROS_PORT_EMBEDDING)) {
    return { ok: false, killed: 0, pids: [], message: `:${IKAROS_PORT_EMBEDDING} 未监听, 无需停止` }
  }
  const { killed, pids } = await killByPort(ctx, IKAROS_PORT_EMBEDDING)
  return {
    ok: killed > 0,
    killed,
    pids,
    message: killed > 0 ? `已 kill ${killed} 个进程 (PID ${pids.join(', ')})` : 'kill 失败, 看 stderr',
  }
}

async function switchModel(ctx: Context, filename: string): Promise<{ ok: boolean; message: string }> {
  const target = path.join(IKAROS_MEMORY_MODELS, filename)
  if (!existsSync(target)) {
    return { ok: false, message: `模型不存在: ${target}` }
  }
  const meta = guessModelMeta(filename)
  if (meta.type !== 'embedding') {
    return { ok: false, message: `所选模型看起来是 ${meta.type} (非 embedding), 请确认` }
  }
  // 写 .env 临时 + ikaros-env.bat (Windows) + ikaros-env.sh (bash): 用单次
  // 环境脚本无法热生效 → 仅修改文件 + 重启 llama (后续启动会读)
  // 简化: 写 .env.embedding_active = filename, ikarosctl 读它
  const envFile = path.join(IKAROS_ROOT, '.env.embedding_active')
  try {
    const { writeFileSync } = await import('node:fs')
    writeFileSync(envFile, `IKAROS_MODEL_EMBEDDING=${target}\n`, { encoding: 'utf-8' })
  } catch (e: unknown) {
    return { ok: false, message: `写 ${envFile} 失败: ${(e as Error)?.message || e}` }
  }
  // 重启 llama
  if (await isPortOpen(IKAROS_PORT_EMBEDDING)) {
    await killByPort(ctx, IKAROS_PORT_EMBEDDING)
    await new Promise((r) => setTimeout(r, 1500))
  }
  const r = await startEmbedding(ctx)
  return { ok: r.ok, message: `${r.message}; 已写 ${envFile}, 下次启动将用 ${filename}` }
}

async function downloadModel(
  ctx: Context,
  cfg: Config,
  args: { repo: string; filename: string },
): Promise<{ ok: boolean; message: string; tool: string | null; outPath: string }> {
  const { repo, filename } = args
  if (!repo || !filename) return { ok: false, message: 'repo + filename 必填', tool: null, outPath: '' }
  if (!/^[\w.\-/]+$/.test(repo) || !/^[\w.\-]+$/.test(filename)) {
    return { ok: false, message: 'repo 或 filename 含非法字符 (只允许字母/数字/点/连字符/斜杠)', tool: null, outPath: '' }
  }
  const url = `${cfg.hfBaseUrl.replace(/\/+$/, '')}/${repo}/resolve/main/${filename}`
  const out = path.join(IKAROS_MEMORY_MODELS, filename)
  if (!existsSync(IKAROS_MEMORY_MODELS)) {
    return { ok: false, message: `models 目录不存在: ${IKAROS_MEMORY_MODELS}`, tool: null, outPath: out }
  }
  const tool = await probeDownloader(ctx)
  if (!tool) {
    return { ok: false, message: '未探测到 gopeed / aria2c / curl 任一可用下载工具', tool: null, outPath: out }
  }
  let argv: string[]
  switch (tool) {
    case 'gopeed':
      argv = ['gopeed', '-u', url, '-o', out, '--connections', String(cfg.downloadConnections)]
      break
    case 'aria2c':
      argv = ['aria2c', '--no-conf', '-x', String(cfg.downloadConnections), '-s', String(cfg.downloadConnections),
              '-d', IKAROS_MEMORY_MODELS, '-o', filename, url]
      break
    case 'curl':
      argv = ['curl', '-L', '-f', '--retry', '3', '-C', '-', '-o', out, url]
      break
  }
  const r = await spawnCollect(ctx, argv, { graceMs: 30 * 60_000, maxBytes: 32 * 1024 })
  if (r.exitCode === 0 && existsSync(out)) {
    return { ok: true, message: `下载成功 (${tool}): ${out}`, tool, outPath: out }
  }
  return { ok: false, message: `下载失败 (${tool} rc=${r.exitCode}): ${r.stderr.slice(0, 300)}`, tool, outPath: out }
}

async function rebuildVectors(ctx: Context, cfg: Config): Promise<{ ok: boolean; message: string; stdout: string }> {
  if (!existsSync(V5_CALL)) {
    return { ok: false, message: `v5_call.py 不存在: ${V5_CALL}`, stdout: '' }
  }
  const argv = [IKAROS_PYTHON, V5_CALL, 'rebuild', '--batch-size', String(cfg.rebuildBatchSize)]
  const r = await spawnCollect(ctx, argv, { graceMs: 60 * 60_000, maxBytes: 64 * 1024 })
  return {
    ok: r.exitCode === 0,
    message: r.exitCode === 0 ? '向量重建成功' : `重建失败 (rc=${r.exitCode})`,
    stdout: r.stdout.slice(-2000),
  }
}

/* ── apply: 暴露服务 ── */
// dsh 0.1.1-rc.2 标准: class extends Service —— 静态 Config 定义 schema,
// constructor 用 ctx.inject(...) 拉取依赖 + 注册 host-bridge 端点.
// 客户端通过 ctx.get('connection').api.ikarosMemory 调到本类实例.

export class IkarosMemorySettingsService extends Service {
  // Service base class 注入 ctx (cascaded loader 加载时构造调 super(ctx, name))
  // host 端方法通过 cordis service 反射暴露到 client.connection.api.ikarosMemory
  // dsh-agent-default-model 的 class Service 走 `super(ctx, 'agentDefaultModel')` 同样模式.
  static Config = z.object({})  // cordis 4 强制 Config schema 校验 (resolveConfig: runtime.Config["~standard"].validate), 空 object schema 接受任何 config

  // 客户端 RPC 端点 (通过 host-bridge 序列化暴露)
  listModels = () => listModels()
  getStatus = () => getStatus()
  startEmbedding = () => startEmbedding((this as any).ctx as unknown as Context)
  stopEmbedding = () => stopEmbedding((this as any).ctx as unknown as Context)
  switchModel = (filename: string) => switchModel((this as any).ctx as unknown as Context, filename)
  downloadModel = (args: { repo: string; filename: string }) => downloadModel((this as any).ctx as unknown as Context, (this as any).ctx?.config as unknown as Config, args)
  rebuildVectors = () => rebuildVectors((this as any).ctx as unknown as Context, (this as any).ctx?.config as unknown as Config)
}

// 客户端卡通过 'name: name' (d33ec60 同模式) 解析 — 这里 dsh-loader 拿 npm 包
// 'name: name' 作 dsh-cordis-client-runner 解析锚; service 命名空间来自 patch.yml 的
// `id: ikarosMemory`, 所以 host-bridge 暴露为 connection.api.ikarosMemory.
export default IkarosMemorySettingsService