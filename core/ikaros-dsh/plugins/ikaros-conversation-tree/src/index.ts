// ikaros-conversation-tree —— Node 侧
// 职责：
//   1) dsh 启动时「立即」确保对话树服务就绪（boot 直启，不等轮询）；
//   2) 看门狗每 3s 兜底：服务崩溃后自动重启；
//   3) 提供 ctx.conversationTree 服务（含动态端口），client 侧经 ctx.get 读取。
// 端口策略：--port 0 由 OS 分配；server.py 向 stdout 输出 PORT=<n>；
//          实际端口写入 tmp/ct-port.json，跨重启恢复。
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { appendFileSync, readFileSync, existsSync, writeFileSync, mkdirSync } from 'node:fs'
// 合并自 ikaros-ct-settings: CT 设置面板的端口 watch + client.js patch
import { apply as applyCtSettings } from './settings/node.js'

export const name = 'ikaros-conversation-tree'

export interface Config {
  /** 对话树服务端口（0=OS 分配） */
  port: number
  /** 便携 Python 绝对路径（cordis.patch.yml 用 !!js 注入 IKAROS_ROOT） */
  python: string
  /** server.py 绝对路径 */
  serverPath: string
  /** 探活超时（ms） */
  probeTimeoutMs: number
}

export const defaultConfig: Config = {
  port: 0,
  python: '',
  serverPath: '',
  probeTimeoutMs: 3000,
}

export interface ConversationTreeStatus {
  healthy: boolean
  lastChecked: number
  url: string
  port: number
}

/** 跨进程端口共享文件（IKAROS_ROOT/tmp/ct-port.json） */
const PORT_FILE = path.join(process.env.IKAROS_ROOT || 'E:/Ikaros', 'tmp', 'ct-port.json')

/** 独立诊断日志（dsh 启动 stdout=DEVNULL, ctx.logger 进黑洞; 写本地文件便于排查） */
const DIAG_LOG = path.join(process.env.IKAROS_ROOT || 'E:/Ikaros', 'data', 'logs', 'ct-plugin.log')
function diag(msg: string): void {
  try {
    mkdirSync(path.dirname(DIAG_LOG), { recursive: true })
    appendFileSync(DIAG_LOG, `[${new Date().toISOString()}] ${msg}\n`, 'utf8')
  } catch {}
}

/** 本插件 client.js 的绝对路径（Node 侧自行写入端口，供 client 侧 fetch） */
const _HERE = path.dirname(fileURLToPath(import.meta.url))
const CLIENT_JS = path.join(_HERE, 'client.js')

function readPortFile(): number | null {
  try {
    if (!existsSync(PORT_FILE)) return null
    const saved = JSON.parse(readFileSync(PORT_FILE, 'utf8'))
    return typeof saved.port === 'number' ? saved.port : null
  } catch { return null }
}

function writePortFile(port: number): void {
  try {
    mkdirSync(path.dirname(PORT_FILE), { recursive: true })
    writeFileSync(PORT_FILE, JSON.stringify({ port }))
  } catch {}
}

const apply = (ctx: any, config: Config) => {
  const cfgPort = config.port || defaultConfig.port
  const python = config.python
  const serverPath = config.serverPath

  const status: ConversationTreeStatus = {
    healthy: false,
    lastChecked: 0,
    url: `http://127.0.0.1:${readPortFile() ?? cfgPort}`,
    port: readPortFile() ?? cfgPort,
  }

  ctx.effect(() => ctx.provide('conversationTree', status))

  // ── 端口确定后，通过直接改写 client.js 注入端口 ──
  const patchClientJs = (port: number) => {
    try {
      const src = readFileSync(CLIENT_JS, 'utf8')
      const patched = src.replace(/http:\/\/127\.0\.0\.1:\d+\//, `http://127.0.0.1:${port}/`)
      if (patched !== src) {
        writeFileSync(CLIENT_JS, patched, 'utf8')
        ctx.logger?.info?.(`[ikaros-conversation-tree] client.js 已更新为 :${port}`)
      }
    } catch (e) {
      ctx.logger?.warn?.('[ikaros-conversation-tree] 更新 client.js 失败:', String(e))
    }
  }

  let child: ReturnType<typeof spawn> | null = null
  let probeTimer: NodeJS.Timeout | null = null
  let probes = 0

  const adoptPort = (p: number): void => {
    actualPort = p
    status.port = p
    status.url = `http://127.0.0.1:${p}`
    writePortFile(p)
    patchClientJs(p)  // 端口确定后更新 client.js 中的 fallback URL
  }

  let actualPort = status.port

  /** 探活指定端口；成功则采纳该端口并更新状态 */
  const probeOne = async (checkPort?: number, timeoutMs?: number): Promise<boolean> => {
    const p = checkPort ?? actualPort
    try {
      const ac = new AbortController()
      const timer = setTimeout(() => ac.abort(), timeoutMs ?? config.probeTimeoutMs ?? 3000)
      const res = await fetch(`http://127.0.0.1:${p}/api/state`, { signal: ac.signal })
      clearTimeout(timer)
      status.lastChecked = Date.now()
      if (res.ok) {
        status.healthy = true
        adoptPort(p)
        return true
      }
    } catch {
      status.healthy = false
      status.lastChecked = Date.now()
    }
    return false
  }

  /** 拉起 server.py 子进程；stdout 捕获 PORT=<n> 采纳实际端口 */
  const startServer = (): boolean => {
    diag(`[startServer] called child=${child?.pid ?? 'null'} exitCode=${child?.exitCode ?? 'n/a'} python=${python} serverPath=${serverPath}`)
    if (child && child.exitCode === null) return true  // 已在拉起中（或刚 spawn 未退出）
    if (!python || !serverPath) {
      ctx.logger?.warn?.('[ikaros-conversation-tree] python/serverPath 未配置，无法拉起对话树服务')
      diag('[startServer] ABORT: python or serverPath is empty')
      return false
    }
    // 端口策略：优先恢复上次端口（ct-port.json）→ 端口跨重启稳定；
    // 仅当该端口被占用（server.py 绑定失败自动降级 0）或首次启动（无端口文件）才由 OS 随机分配
    const targetPort = readPortFile() ?? cfgPort
    diag(`[startServer] spawn python=${python} serverPath=${serverPath} targetPort=${targetPort}`)
    ctx.logger?.info?.(`[ikaros-conversation-tree] 拉起对话树服务: ${python} ${serverPath} --port ${targetPort}`)
    child = spawn(python, [serverPath, '--port', String(targetPort)], {
      cwd: path.dirname(serverPath),
      stdio: ['ignore', 'pipe', 'inherit'],
      windowsHide: true,
    })
    diag(`[startServer] spawn returned pid=${child.pid}`)
    let stdoutBuf = ''
    child.stdout?.on('data', (chunk: Buffer) => {
      stdoutBuf += chunk.toString()
      diag(`[stdout] ${chunk.toString().replace(/\n/g, '\\n').slice(0, 200)}`)
      const m = stdoutBuf.match(/PORT=(\d+)/)
      if (m) {
        adoptPort(parseInt(m[1], 10))
        diag(`[adoptPort] ${m[1]} 写入 ct-port.json 与 client.js`)
        ctx.logger?.info?.(`[ikaros-conversation-tree] 对话树服务运行在端口 ${actualPort}`)
      }
    })
    child.on('error', (err: Error) => {
      diag(`[child.on(error)] ${String(err?.message ?? err)}`)
      ctx.logger?.warn?.('[ikaros-conversation-tree] 拉起失败:', String(err?.message ?? err))
      child = null
      status.healthy = false
    })
    child.on('exit', (code: number | null) => {
      diag(`[child.on(exit)] code=${code} signal=${(child as any)?.signalCode ?? 'n/a'}`)
      ctx.logger?.info?.(`[ikaros-conversation-tree] 服务进程退出 code=${code}`)
      child = null
      status.healthy = false
    })
    return true
  }

  // ── dsh 启动直启路径：立即确保服务就绪，不等看门狗轮询 ──
  // 1) 已知端口(上次会话/配置)快速探活(~800ms)；活着=复用外部或既有实例 → adoptPort 同步端口
  // 2) 不通 → 立即 spawn 自己的实例
  void (async () => {
    diag(`[boot-iiFE] start IKAROS_ROOT=${process.env.IKAROS_ROOT ?? '<unset>'} python=${python} serverPath=${serverPath} cfgPort=${cfgPort} readPortFile=${readPortFile()}`)
    const known = [readPortFile(), cfgPort].filter((p): p is number => !!p && p > 0)
    for (const p of [...new Set(known)]) {
      const ok = await probeOne(p, 800)
      diag(`[boot-iife] probe :${p} ok=${ok}`)
      if (ok) {
        ctx.logger?.info?.(`[ikaros-conversation-tree] 复用运行中的对话树服务 :${p}`)
        adoptPort(p)  // 同步端口到 status + ct-port.json + client.js（外部实例场景关键）
        diag(`[boot-iife] reused :${p}`)
        return
      }
    }
    diag(`[boot-iife] no live instance found, calling startServer()...`)
    startServer()
  })()

  /** 看门狗 tick：兜底恢复（崩溃重启 / 端口漂移追踪） */
  const tick = async (): Promise<void> => {
    probes++
    const ok = await probeOne()
    if (!ok) {
      // 当前端口不通 → 尝试端口文件里的新端口（外部实例漂移场景）
      const saved = readPortFile()
      if (saved && saved !== actualPort) {
        const ok2 = await probeOne(saved)
        if (ok2) return scheduleNext()
      }
      // 都不通且没有自己拉起的实例 → 拉起
      if (!child) startServer()
      else if (probes % 6 === 0) ctx.logger?.warn?.('[ikaros-conversation-tree] 服务未就绪，持续探活中…')
    }
    scheduleNext()
  }

  const scheduleNext = (): void => {
    if (probeTimer) clearTimeout(probeTimer)
    probeTimer = setTimeout(tick, 3000)
  }

  ctx.effect(() => {
    tick()
    return () => {
      if (probeTimer) clearTimeout(probeTimer)
      probeTimer = null
      // 只清理自己拉起的子进程；复用的外部实例不动
      if (child) {
        try { child.kill() } catch {}
        child = null
      }
    }
  })

  ctx.logger?.info?.(`[ikaros-conversation-tree] 插件就绪, 目标 ${status.url}`)

  // 合并: CT 设置面板 (端口 watch + client.js patch)
  applyCtSettings(ctx)
}

export { apply }