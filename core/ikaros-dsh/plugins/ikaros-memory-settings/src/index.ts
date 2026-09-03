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
import { existsSync, readdirSync, statSync, writeFileSync, readFileSync, mkdirSync, unlinkSync } from 'node:fs'
import { createServer, IncomingMessage, ServerResponse } from 'node:http'
import { AddressInfo } from 'node:net'
import { spawn as nodeSpawn, execFile as nodeExecFile } from 'node:child_process'

/**
 * Native Node child_process.spawn wrapper for long-running detached services
 * (e.g. llama-server).  The dsh subprocess Service does NOT set
 * `windowsHide: true` on win32 and `detached` only on non-win32, so spawning a
 * console-subsystem binary like llama-server via that service pops a cmd window
 * that lingers on the desktop for the process's lifetime.  Native spawn with
 * `{ detached: true, stdio: 'ignore', windowsHide: true }` launches the child
 * in its own process group (so killByPid can terminate the tree) and hides the
 * console window.  Returns the PID (or -1 on spawn failure).
 */
function spawnDetachedNoWindow(cmd: string, args: readonly string[], opts: { cwd?: string } = {}): number {
  try {
    const child = nodeSpawn(cmd, [...args], {
      cwd: opts.cwd,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    })
    // Surface any spawn-time errors asynchronously so the caller doesn't hang.
    child.once('error', () => { /* already exited; nothing useful to do */ })
    child.unref()  // let the parent exit independently of the child
    return child.pid ?? -1
  } catch {
    return -1
  }
}

/**
 * Native execFile wrapper for short-lived helper commands (powershell probes,
 * taskkill).  dsh subprocess Service does set stdio:pipe on Windows without
 * windowsHide, which flashes a console window for every helper invocation;
 * using `nodeExecFile` with `windowsHide: true` keeps the desktop clean while
 * still letting us read stdout/stderr.
 */
function execFileCapture(cmd: string, args: readonly string[], opts: { timeoutMs?: number } = {}): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    try {
      nodeExecFile(cmd, [...args], {
        timeout: opts.timeoutMs ?? 30_000,
        windowsHide: true,
        encoding: 'utf8',
      }, (err, stdout, stderr) => {
        if (err && (err as NodeJS.ErrnoException).code === 'ENOENT') {
          resolve({ exitCode: -1, stdout: '', stderr: `command not found: ${cmd}` })
          return
        }
        // err can be { killed, code, signal } for non-zero exits without throwing
        const code = (err as { code?: number | string } | null)?.code
        const exitCode = typeof code === 'number' ? code : (err ? 1 : 0)
        resolve({ exitCode, stdout: stdout ?? '', stderr: stderr ?? '' })
      })
    } catch (e) {
      resolve({ exitCode: -1, stdout: '', stderr: String((e as Error)?.message || e) })
    }
  })
}

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
const IKAROS_MEMORY_MODELS = path.join(IKAROS_ROOT, 'core', 'memory_v5', 'models')
const IKAROS_PORT_EMBEDDING = parseInt(process.env.IKAROS_PORT_EMBEDDING || '8587', 10)

/**
 * Runtime dependency overrides (python / llama-server paths). Loaded once from
 * `<pluginDir>/.runtime.json` at boot and written by /setDependencies at runtime.
 * Resolution order at boot:
 *   1. .runtime.json (if present and well-formed)
 *   2. environment variables IKAROS_PYTHON / IKAROS_LLAMA  (dsh launcher-injected)
 *   3. defaults: <IKAROS_ROOT>/runtime/portable-python/python.exe + llama/b10000-cuda/llama-server.exe
 * Mutable because /setDependencies rewrites the file then updates these in-process
 * so the next startEmbedding/rebuildVectors uses the new paths without a reload.
 */
let IKAROS_PYTHON = ''
let IKAROS_LLAMA = ''
let IKAROS_PYTHON_SOURCE: 'runtime-override' | 'env' | 'default' = 'default'
let IKAROS_LLAMA_SOURCE: 'runtime-override' | 'env' | 'default' = 'default'

function resolveDefaultPython(): string {
  return process.env.IKAROS_PYTHON
    || path.join(IKAROS_ROOT, 'runtime', 'portable-python', 'python.exe')
}
function resolveDefaultLlama(): string {
  return path.join(IKAROS_ROOT, 'runtime', 'llama', 'b10000-cuda', 'llama-server.exe')
}

interface RuntimeOverrides {
  python?: string
  llama?: string
}

const RUNTIME_OVERRIDES_FILE = path.join(
  IKAROS_ROOT, 'core', 'ikaros-dsh', 'plugins', 'ikaros-memory-settings', '.runtime.json'
)

function loadRuntimeOverrides(): RuntimeOverrides {
  try {
    if (!existsSync(RUNTIME_OVERRIDES_FILE)) return {}
    const raw = readFileSync(RUNTIME_OVERRIDES_FILE, 'utf-8')
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object') return parsed as RuntimeOverrides
  } catch { /* 解析失败视为无 overrides */ }
  return {}
}

function saveRuntimeOverrides(o: RuntimeOverrides): void {
  try {
    if (!existsSync(path.dirname(RUNTIME_OVERRIDES_FILE))) {
      mkdirSync(path.dirname(RUNTIME_OVERRIDES_FILE), { recursive: true })
    }
    writeFileSync(RUNTIME_OVERRIDES_FILE, JSON.stringify(o, null, 2) + '\n', 'utf-8')
  } catch (e) {
    throw new Error(`写 ${RUNTIME_OVERRIDES_FILE} 失败: ${(e as Error).message}`)
  }
}

function deleteRuntimeOverrides(): void {
  try {
    if (existsSync(RUNTIME_OVERRIDES_FILE)) unlinkSync(RUNTIME_OVERRIDES_FILE)
  } catch { /* 文件不存在或权限问题, 不阻断主流程 */ }
}

function initDependencyPaths(): void {
  const overrides = loadRuntimeOverrides()
  if (overrides.python && overrides.python.trim()) {
    IKAROS_PYTHON = overrides.python.trim()
    IKAROS_PYTHON_SOURCE = 'runtime-override'
  } else if (process.env.IKAROS_PYTHON && process.env.IKAROS_PYTHON.trim()) {
    IKAROS_PYTHON = process.env.IKAROS_PYTHON.trim()
    IKAROS_PYTHON_SOURCE = 'env'
  } else {
    IKAROS_PYTHON = resolveDefaultPython()
    IKAROS_PYTHON_SOURCE = 'default'
  }
  if (overrides.llama && overrides.llama.trim()) {
    IKAROS_LLAMA = overrides.llama.trim()
    IKAROS_LLAMA_SOURCE = 'runtime-override'
  } else {
    IKAROS_LLAMA = resolveDefaultLlama()
    IKAROS_LLAMA_SOURCE = 'env'  // 默认路径也归到 env 范畴, 因为 IKAROS_ROOT 是 env 注入的
  }
}

initDependencyPaths()

/** Default embedding model: ikaros 主推 bge-m3 (1024维, 多语种, 与 v5_call.py 默认对齐) */
const DEFAULT_MODEL_REPO = 'BAAI/bge-m3'
const DEFAULT_MODEL_FILE = 'bge-m3-q8_0.gguf'
const DEFAULT_MODEL_PATH = path.join(IKAROS_MEMORY_MODELS, DEFAULT_MODEL_FILE)
let IKAROS_MODEL_EMBEDDING = process.env.IKAROS_MODEL_EMBEDDING
  || (existsSync(DEFAULT_MODEL_PATH) ? DEFAULT_MODEL_PATH : DEFAULT_MODEL_PATH)
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

/** windows: 按端口查 PID + 杀进程树 (taskkill /T /F) */
async function killByPort(ctx: Context, port: number): Promise<{ killed: number; pids: number[] }> {
  // 1) 查端口对应 PID (NET 解析更快, 无副作用)
  const probe = await execFileCapture('powershell', [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    `(Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique) -join ','`,
  ], { timeoutMs: 5000 })
  const pids = probe.stdout.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n) && n > 0)
  if (pids.length === 0) {
    return { killed: 0, pids: [] }
  }
  // 2) taskkill /T /F 每个 PID, 杀整棵进程树 (含 llama-server 的 CUDA/GPU helper)
  let killed = 0
  for (const pid of pids) {
    const r = await execFileCapture('taskkill', ['/PID', String(pid), '/T', '/F'], { timeoutMs: 5000 })
    if (r.exitCode === 0) {
      killed++
    }
  }
  // 3) 等端口彻底释放 (最长 5s)
  for (let i = 0; i < 10; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (!await isPortOpen(port)) break
  }
  return { killed, pids }
}

/** 探测下载工具可用性 (返回第一个存在的) */
async function probeDownloader(_ctx: Context): Promise<'gopeed' | 'aria2c' | 'curl' | null> {
  for (const tool of ['gopeed', 'aria2c', 'curl']) {
    const r = await execFileCapture(tool, ['--version'], { timeoutMs: 5000 })
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
  // PID 探测: powershell Get-NetTCPConnection, native execFile + windowsHide (不弹窗)
  let pid: number | null = null
  if (portOpen) {
    const r = await execFileCapture('powershell', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
      `(Get-NetTCPConnection -LocalPort ${IKAROS_PORT_EMBEDDING} -State Listen).OwningProcess`,
    ], { timeoutMs: 5000 })
    const m = r.stdout.match(/(\d+)/)
    if (m) pid = parseInt(m[1], 10)
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
  // 启动 llama-server —— 用 Node 原生 spawn + detached + windowsHide + stdio ignore,
  // 绕开 dsh subprocess Service 在 Windows 上不设 windowsHide / detached 的缺陷
  // (那个会弹 cmd 窗口且窗口一直驻留桌面直到进程退出).
  const argv = [
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
  const pid = spawnDetachedNoWindow(IKAROS_LLAMA, argv, { cwd: IKAROS_ROOT })
  if (pid <= 0) {
    return { ok: false, message: 'spawn llama-server 失败 (见 dsh stderr)', pid: null }
  }
  // 等端口起来
  for (let i = 0; i < 20; i++) {
    await new Promise((r) => setTimeout(r, 500))
    if (await isPortOpen(IKAROS_PORT_EMBEDDING)) {
      return { ok: true, message: `llama-server 已在 :${IKAROS_PORT_EMBEDDING} 起来 (pid ${pid})`, pid }
    }
  }
  return { ok: false, message: `已 spawn (pid ${pid}) 但 10s 内端口未起来, 看 llama 日志`, pid }
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
  const r = await execFileCapture(argv![0], argv!.slice(1), { timeoutMs: 30 * 60_000 })
  if (r.exitCode === 0 && existsSync(out)) {
    return { ok: true, message: `下载成功 (${tool}): ${out}`, tool, outPath: out }
  }
  return { ok: false, message: `下载失败 (${tool} rc=${r.exitCode}): ${r.stderr.slice(0, 300)}`, tool, outPath: out }
}

async function rebuildVectors(_ctx: Context, cfg: Config): Promise<{ ok: boolean; message: string; stdout: string }> {
  if (!existsSync(V5_CALL)) {
    return { ok: false, message: `v5_call.py 不存在: ${V5_CALL}`, stdout: '' }
  }
  const r = await execFileCapture(IKAROS_PYTHON, [V5_CALL, 'rebuild', '--batch-size', String(cfg.rebuildBatchSize)], { timeoutMs: 60 * 60_000 })
  return {
    ok: r.exitCode === 0,
    message: r.exitCode === 0 ? '向量重建成功' : `重建失败 (rc=${r.exitCode})`,
    stdout: r.stdout.slice(-2000),
  }
}

async function downloadDefaultModel(
  ctx: Context,
  cfg: Config,
): Promise<{ ok: boolean; message: string; tool: string | null; outPath: string; alreadyExists: boolean }> {
  const target = path.join(IKAROS_MEMORY_MODELS, DEFAULT_MODEL_FILE)
  if (existsSync(target)) {
    return { ok: true, message: `默认模型已存在: ${target}`, tool: null, outPath: target, alreadyExists: true }
  }
  const r = await downloadModel(ctx, cfg, { repo: DEFAULT_MODEL_REPO, filename: DEFAULT_MODEL_FILE })
  return { ...r, alreadyExists: false }
}

interface DependenciesInfo {
  python: string
  llama: string
  pythonExists: boolean
  llamaExists: boolean
  pythonSource: 'runtime-override' | 'env' | 'default'
  llamaSource: 'runtime-override' | 'env' | 'default'
  hasOverride: boolean
  overrideFile: string
}

async function getDependencies(): Promise<DependenciesInfo> {
  return {
    python: IKAROS_PYTHON,
    llama: IKAROS_LLAMA,
    pythonExists: existsSync(IKAROS_PYTHON),
    llamaExists: existsSync(IKAROS_LLAMA),
    pythonSource: IKAROS_PYTHON_SOURCE,
    llamaSource: IKAROS_LLAMA_SOURCE,
    hasOverride: existsSync(RUNTIME_OVERRIDES_FILE),
    overrideFile: RUNTIME_OVERRIDES_FILE,
  }
}

async function setDependencies(
  args: { python?: string; llama?: string; reset?: boolean },
): Promise<{ ok: boolean; message: string; info: DependenciesInfo }> {
  const overrides = args.reset ? {} : { ...loadRuntimeOverrides() }
  if (args.reset) {
    overrides.python = undefined
    overrides.llama = undefined
    // reset = 真正清空文件 (否则 hasOverride 一直 true, 前端"重置"按钮永远可点)
    deleteRuntimeOverrides()
  } else {
    if (args.python !== undefined) {
      const v = args.python.trim()
      if (!v) {
        return { ok: false, message: 'Python 路径不能为空', info: await getDependencies() }
      }
      if (!existsSync(v)) {
        return { ok: false, message: `Python 路径不存在: ${v}`, info: await getDependencies() }
      }
      overrides.python = v
    }
    if (args.llama !== undefined) {
      const v = args.llama.trim()
      if (!v) {
        return { ok: false, message: 'llama-server 路径不能为空', info: await getDependencies() }
      }
      if (!existsSync(v)) {
        return { ok: false, message: `llama-server 路径不存在: ${v}`, info: await getDependencies() }
      }
      overrides.llama = v
    }
    // 过滤空值后写文件
    const clean: RuntimeOverrides = {}
    if (overrides.python) clean.python = overrides.python
    if (overrides.llama) clean.llama = overrides.llama
    if (Object.keys(clean).length === 0) {
      deleteRuntimeOverrides()
    } else {
      saveRuntimeOverrides(clean)
    }
  }
  // 重算 in-process 路径, 立刻生效 (下次 startEmbedding/rebuildVectors 用新值)
  initDependencyPaths()
  return { ok: true, message: '依赖已更新', info: await getDependencies() }
}

/* ── apply: 暴露 HTTP API + port 文件 ── */
// dsh 0.1.1-rc.2 host-bridge (dsh-host-apiproxy) 只硬编码 5 个 namespace
// (llm/settings/events/host/credentials) — class extends Service + super(ctx, 'xxx')
// 注册的 service 不会被自动序列化为 connection.api.xxx, 除非 patch 它的硬编码
// 仿兄弟 commit d33ec60 (ikaros-conversation-tree) 模式: Node 侧独立
// HTTP server 暴露 RPC 端点, client 侧 fetch 调用 (URL 通过 patch client.js 注入).

const IKAROS_MEMORY_API_PORT = parseInt(process.env.IKAROS_MEMORY_API_PORT || '19001', 10)
const IKAROS_MEMORY_API_HOST = '127.0.0.1'
const API_PORT_FILE = path.join(IKAROS_ROOT, 'tmp', 'ikaros-memory-api-port.json')
const API_CLIENT_JS = path.join(
  IKAROS_ROOT, 'core', 'ikaros-dsh', 'plugins', 'ikaros-memory-settings', 'dist', 'client.js'
)

function patchClientJs(port: number): void {
  try {
    if (!existsSync(API_CLIENT_JS)) return
    const src = readFileSync(API_CLIENT_JS, 'utf-8')
    // 替换 client.tsx 里的 IkarosMemoryAPI 占位字符串
    const patched = src.replace(
      /const\s+IkarosMemoryAPI\s*=\s*[`'"][^`'"]*[`'"]/,
      `const IkarosMemoryAPI = 'http://${IKAROS_MEMORY_API_HOST}:${String(port)}'`,
    )
    if (patched !== src) writeFileSync(API_CLIENT_JS, patched, 'utf-8')
  } catch { /* patch 失败不影响 server, client fallback 用占位 URL */ }
}

async function readJsonBody(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  if (chunks.length === 0) return {}
  try { return JSON.parse(Buffer.concat(chunks).toString('utf-8')) } catch { return {} }
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

function apply(ctx: Context, config: Config = defaultConfig) {
  // 1) 起 HTTP server (127.0.0.1:IKAROS_MEMORY_API_PORT) 暴露 RPC
  const server = createServer(async (req, res) => {
    try {
      const pathname = new URL(req.url ?? '/', `http://${IKAROS_MEMORY_API_HOST}`).pathname
      const method = req.method ?? 'GET'
      // CORS for browser localhost dsh (defensive — same origin anyway)
      res.setHeader('Access-Control-Allow-Origin', '*')
      if (method === 'OPTIONS') {
        // CORS preflight: 浏览器 fetch POST + Content-Type: application/json
        // 必须看到 Allow-Headers 才能放行实际请求, 否则整个调用被拒 (报
        // "host-bridge unreachable"). Allow-Headers 列出 Content-Type (非简单头).
        res.writeHead(204, {
          'Access-Control-Allow-Methods': 'POST,GET,OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        })
        res.end()
        return
      }
      if (method !== 'POST') { sendJson(res, 405, { error: 'method not allowed' }); return }

      const body = await readJsonBody(req)
      const args = (body && typeof body === 'object' ? (body as Record<string, unknown>) : {}) as Record<string, unknown>

      let result: unknown
      switch (pathname) {
        case '/listModels':           result = await listModels(); break
        case '/getStatus':            result = await getStatus(); break
        case '/startEmbedding':       result = await startEmbedding(ctx); break
        case '/stopEmbedding':        result = await stopEmbedding(ctx); break
        case '/switchModel':          result = await switchModel(ctx, String(args.filename || '')); break
        case '/downloadModel':        result = await downloadModel(ctx, config, { repo: String(args.repo || ''), filename: String(args.filename || '') }); break
        case '/downloadDefaultModel': result = await downloadDefaultModel(ctx, config); break
        case '/rebuildVectors':       result = await rebuildVectors(ctx, config); break
        case '/getDependencies':      result = await getDependencies(); break
        case '/setDependencies':      result = await setDependencies(args as { python?: string; llama?: string; reset?: boolean }); break
        default:                      sendJson(res, 404, { error: 'not found' }); return
      }
      sendJson(res, 200, { ok: true, result })
    } catch (e: unknown) {
      sendJson(res, 500, { ok: false, error: String((e as Error)?.message || e) })
    }
  })

  server.listen(IKAROS_MEMORY_API_PORT, IKAROS_MEMORY_API_HOST, () => {
    const addr = server.address() as AddressInfo | null
    const port = addr?.port ?? IKAROS_MEMORY_API_PORT
    // 写 port 文件 (跟 ct-port.json 同样模式)
    try {
      if (!existsSync(path.dirname(API_PORT_FILE))) mkdirSync(path.dirname(API_PORT_FILE), { recursive: true })
      writeFileSync(API_PORT_FILE, JSON.stringify({ port, host: IKAROS_MEMORY_API_HOST, pid: process.pid, startedAt: Date.now() }), 'utf-8')
    } catch { /* 不影响主流程 */ }
    // patch 客户端 dist 里的 IkarosMemoryAPI URL
    patchClientJs(port)
    // eslint-disable-next-line no-console
    console.log(`[ikaros-memory-settings] host-bridge HTTP :${String(port)} ready (pid ${String(process.pid)})`)
  })

  // 2) 资源清理: 卸载插件时关 server, 删 port 文件
  ctx.effect(() => () => {
    try { server.close() } catch { /* noop */ }
    try { if (existsSync(API_PORT_FILE)) unlinkSync(API_PORT_FILE) } catch { /* noop */ }
  }, 'ikaros-memory-settings: HTTP server cleanup')
}

// 客户端卡通过 'name: name' (d33ec60 同模式) 解析 — dsh-loader 拿 npm 包作
// dsh-cordis-client-runner 解析锚; Node 侧不通过 connection.api 暴露服务,
// 而是独立 HTTP server 暴露 RPC (仿 ikaros-conversation-tree 模式).
export default apply
export { apply }