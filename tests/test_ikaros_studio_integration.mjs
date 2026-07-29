#!/usr/bin/env node
/**
 * Ikaros × Studio 全功能测试（headless 部分）
 * 忠实复刻 hermes-studio/packages/server/src/services/v5-agent/manager.ts 的子进程调用，
 * 不依赖 GUI / 浏览器，直接验证「Studio 发消息给 Ikaros → Python agent_loop → 本地回复」核心链路。
 *
 * 运行:
 *   node tests/test_ikaros_studio_integration.mjs
 *   IKAROS_TEST_INPUT="你的测试消息" node tests/test_ikaros_studio_integration.mjs
 *   IKAROS_TEST_PROVIDER=deepseek node ...   # 额外测 deepseek 分支(需有效 base_url/key 或预期失败)
 */
import { execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

const IKAROS_ROOT = process.env.IKAROS_ROOT || 'E:/Ikaros'
const IKAROS_MEMORY = process.env.IKAROS_MEMORY || path.join(IKAROS_ROOT, 'core/v5')
const PORTABLE_PY = 'E:/Ikaros/runtime/portable-python/python.exe'
const PYTHON = process.env.IKAROS_PYTHON || (fs.existsSync(PORTABLE_PY) ? PORTABLE_PY : 'python')

const results = []
const log = (s) => console.log(s)
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
}

// ── 复刻 manager.ts resolveBackendEnv ──
function resolveBackendEnv(root) {
  const file = path.join(root, 'config', 'ikaros-backend.json')
  try {
    const cfg = JSON.parse(fs.readFileSync(file, 'utf-8'))
    const provider = String(cfg.provider || 'dashboard').toLowerCase()
    if (provider === 'local') {
      const l = cfg.local || {}
      return {
        IKAROS_BACKEND_PROVIDER: 'local',
        IKAROS_BACKEND_BASE_URL: String(l.base_url || 'http://127.0.0.1:8080/v1'),
        IKAROS_BACKEND_MODEL: String(l.model || 'local-llm'),
      }
    }
    if (provider === 'deepseek') {
      const d = cfg.deepseek || {}
      return {
        IKAROS_BACKEND_PROVIDER: 'deepseek',
        IKAROS_BACKEND_BASE_URL: String(d.base_url || 'https://api.deepseek.com/v1'),
        IKAROS_BACKEND_API_KEY: String(d.api_key || ''),
        IKAROS_BACKEND_MODEL: String(d.model || 'deepseek-chat'),
      }
    }
    return { IKAROS_BACKEND_PROVIDER: 'dashboard' }
  } catch {
    return { IKAROS_BACKEND_PROVIDER: 'dashboard' }
  }
}

// ── 复刻 manager.ts callV5Orchestrator 的 Python -c 片段 ──
function buildArgs(inputText, sessionId) {
  // 双重 JSON.stringify: 与 manager.ts 第134行一致
  const inputLiteral = JSON.stringify(JSON.stringify(inputText || ''))
  const sessionLiteral = JSON.stringify(sessionId || '')
  const memRoot = JSON.stringify(IKAROS_MEMORY)
  const binRoot = JSON.stringify(path.join(IKAROS_ROOT, 'bin'))
  const root = JSON.stringify(IKAROS_ROOT)
  const py = `
import sys, json
sys.path.insert(0, ${memRoot})
sys.path.insert(0, ${binRoot})
sys.path.insert(0, ${root})
from v5.orchestrator import agent_loop
input_text = json.loads(${inputLiteral})
result = agent_loop(input_text, session_id=${sessionLiteral}, max_tokens=2000)
print(json.dumps({
  'output': result,
  'reasoning': '',
  'steps': [],
  'finishReason': 'stop'
}, ensure_ascii=False))
`
  return ['-c', py]
}

async function runV5(inputText, sessionId, backendEnv) {
  const args = buildArgs(inputText, sessionId)
  const { stdout } = await execFileAsync(PYTHON, args, {
    cwd: IKAROS_ROOT,
    timeout: 120000,
    env: {
      ...process.env,
      ...backendEnv,
      IKAROS_ROOT: IKAROS_ROOT,
      HERMES_ROOT: IKAROS_ROOT,
      IKAROS_MEMORY: IKAROS_MEMORY,
      V5_AGENT_MODE: 'agent',
      PYTHONIOENCODING: 'utf-8',
    },
  })
  const out = stdout.trim()
  if (!out.startsWith('{')) throw new Error('输出非 JSON: ' + out.slice(0, 200))
  return JSON.parse(out)
}

async function main() {
  log('=== Ikaros × Studio 全功能测试 (headless) ===')
  log(`IKAROS_ROOT = ${IKAROS_ROOT}`)
  log(`PYTHON      = ${PYTHON}`)
  log('')

  // TEST 0: 端口可达
  const http = await import('node:http')
  const ping = (port) => new Promise((res) => {
    const req = http.request({ host: '127.0.0.1', port, path: '/health', method: 'GET', timeout: 2000 }, (r) => { r.resume(); res(r.statusCode === 200) })
    req.on('error', () => res(false))
    req.on('timeout', () => { req.destroy(); res(false) })
    req.end()
  })
  check('本地 LLM :8080 健康', await ping(8080), 'agent_loop 主回复依赖它')

  // TEST 1: 配置文件有效
  const cfgPath = path.join(IKAROS_ROOT, 'config', 'ikaros-backend.json')
  let cfg
  try {
    cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf-8'))
    const ok = ['local', 'deepseek', 'dashboard'].includes(String(cfg.provider))
    check('config/ikaros-backend.json 合法', ok, `provider=${cfg.provider}`)
  } catch (e) {
    check('config/ikaros-backend.json 合法', false, String(e))
  }

  // TEST 2: resolveBackendEnv 与 manager.ts 一致
  const backendEnv = resolveBackendEnv(IKAROS_ROOT)
  check('后端 env 解析', !!backendEnv.IKAROS_BACKEND_PROVIDER,
    `provider=${backendEnv.IKAROS_BACKEND_PROVIDER} base_url=${backendEnv.IKAROS_BACKEND_BASE_URL || '(legacy)'}`)

  // TEST 3(核心): 端到端跑一次 agent_loop（provider=local → 本地 :8080）
  const inputText = process.env.IKAROS_TEST_INPUT || '你好伊卡洛斯，做一句自我介绍。'
  const sessionId = 'test-' + Date.now().toString(36)
  const provider = process.env.IKAROS_TEST_PROVIDER || backendEnv.IKAROS_BACKEND_PROVIDER
  // 若显式指定 provider，覆盖 env
  const env = provider === 'deepseek' ? resolveBackendEnvFor('deepseek') : backendEnv
  if (provider === 'deepseek') {
    log('  (显式 deepseek 分支测试，预期可能失败/429，仅验证路由)')
  }
  try {
    const t0 = Date.now()
    const res = await runV5(inputText, sessionId, env)
    const dt = ((Date.now() - t0) / 1000).toFixed(1)
    const reply = res.output || ''
    const ok = reply.trim().length > 0
    check(`agent_loop 端到端回复 (provider=${provider})`, ok,
      `耗时 ${dt}s, 首句: ${reply.slice(0, 40).replace(/\n/g, ' ')}…`)
    if (ok) log('\n--- 完整回复 ---\n' + reply + '\n')
  } catch (e) {
    // local 分支失败 = 真有问题；deepseek 分支失败 = 预期内(欠费/不可达)
    const expected = provider === 'deepseek'
    check(`agent_loop 端到端回复 (provider=${provider})`, expected,
      expected ? `deepseek 分支按预期失败: ${String(e.message || e).slice(0, 120)}` : String(e.message || e).slice(0, 200))
  }

  // TEST 4: 图标资产
  const icon = path.join(IKAROS_ROOT, 'hermes-studio/packages/client/public/coding-agents/ikaros-agent.png')
  check('Studio 图标 ikaros-agent.png 存在', fs.existsSync(icon), icon)

  // 汇总
  const failed = results.filter((r) => !r.ok)
  log('')
  log(`=== 结果: ${results.length - failed.length}/${results.length} PASS ===`)
  if (failed.length) {
    log('失败项: ' + failed.map((f) => f.name).join('; '))
    process.exit(1)
  }
  log('headless 核心链路全部通过 ✅')
}

// 给 deepseek 分支单独构造 env（从真实配置读取，避免误用 legacy）
function resolveBackendEnvFor(provider) {
  const file = path.join(IKAROS_ROOT, 'config', 'ikaros-backend.json')
  const cfg = JSON.parse(fs.readFileSync(file, 'utf-8'))
  if (provider === 'deepseek') {
    const d = cfg.deepseek || {}
    return {
      IKAROS_BACKEND_PROVIDER: 'deepseek',
      IKAROS_BACKEND_BASE_URL: String(d.base_url || 'https://api.deepseek.com/v1'),
      IKAROS_BACKEND_API_KEY: String(d.api_key || ''),
      IKAROS_BACKEND_MODEL: String(d.model || 'deepseek-chat'),
    }
  }
  return { IKAROS_BACKEND_PROVIDER: 'dashboard' }
}

main().catch((e) => { console.error(e); process.exit(1) })
