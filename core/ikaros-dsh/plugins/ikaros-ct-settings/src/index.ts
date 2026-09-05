// ikaros-ct-settings —— Node 侧
// 职责：
//   1) 提供 ctx.ctSettings 服务（CT 端口/健康状态，供 client 侧读取）
//   2) 启动时 patch client.js 中的 CT URL（动态端口，仿 ikaros-conversation-tree）
//   3) 监听 CT 服务端口文件 tmp/ct-port.json，端口漂移时重新 patch
import { readFileSync, writeFileSync, existsSync, watch } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'ikaros-ct-settings'

export interface CtSettingsStatus {
  url: string
  port: number
  portFile: string
}

const IKAROS_ROOT = process.env.IKAROS_ROOT || 'E:/Ikaros'
const PORT_FILE = path.join(IKAROS_ROOT, 'tmp', 'ct-port.json')

const _HERE = path.dirname(fileURLToPath(import.meta.url))
const CLIENT_JS = path.join(_HERE, 'client.js')

function readPort(): number | null {
  try {
    if (!existsSync(PORT_FILE)) return null
    const saved = JSON.parse(readFileSync(PORT_FILE, 'utf8'))
    return typeof saved.port === 'number' ? saved.port : null
  } catch { return null }
}

function patchClientJs(port: number): void {
  try {
    const src = readFileSync(CLIENT_JS, 'utf8')
    const patched = src.replace(/http:\/\/127\.0\.0\.1:\d+\//g, `http://127.0.0.1:${port}/`)
    if (patched !== src) {
      writeFileSync(CLIENT_JS, patched, 'utf8')
    }
  } catch {}
}

const apply = (ctx: any) => {
  const initialPort = readPort() ?? 48920
  const status: CtSettingsStatus = {
    url: `http://127.0.0.1:${initialPort}/`,
    port: initialPort,
    portFile: PORT_FILE,
  }

  ctx.effect(() => ctx.provide('ctSettings', status))

  // 启动时 patch 一次
  patchClientJs(status.port)

  // 监听端口文件变化（CT 服务重启/漂移时自动 patch）
  let watcher: ReturnType<typeof watch> | null = null
  try {
    watcher = watch(path.dirname(PORT_FILE), (eventType, filename) => {
      if (filename && path.basename(filename) === 'ct-port.json') {
        const p = readPort()
        if (p && p !== status.port) {
          status.port = p
          status.url = `http://127.0.0.1:${p}/`
          patchClientJs(p)
          ctx.logger?.info?.(`[ikaros-ct-settings] CT port drifted to :${p}, client.js patched`)
        }
      }
    })
  } catch {}

  ctx.effect(() => {
    return () => {
      if (watcher) { try { watcher.close() } catch {} }
    }
  })

  ctx.logger?.info?.(`[ikaros-ct-settings] plugin ready, CT @ ${status.url}`)
}

export { apply }
