import path from 'path'
import { promises as fs } from 'fs'

// Ikaros 后端配置 (由 Studio 设置页读取/写入).
// 配置落在 Ikaros 项目自身的 config/ikaros-backend.json, 与 Hermes profile 配置解耦.

const DEFAULT_CONFIG = {
  provider: 'local',
  local: { base_url: 'http://127.0.0.1:8080/v1', model: 'local-llm' },
  deepseek: { base_url: 'https://api.deepseek.com/v1', api_key: '', model: 'deepseek-chat' },
}

function configPath(): string {
  const root = process.env.IKAROS_ROOT || process.cwd()
  return path.join(root, 'config', 'ikaros-backend.json')
}

// Hermes 凭据 (.env) 路径, 用于双向同步 DeepSeek key
function hermesEnvPath(): string {
  const root = process.env.IKAROS_ROOT || process.cwd()
  return path.join(root, 'data', 'hermes-agent', '.env')
}

// 读取 Hermes .env, 返回 KEY=VALUE 的 map (不导出值)
async function readHermesEnv(): Promise<{ map: Map<string, string> }> {
  const map = new Map<string, string>()
  let content = ''
  try {
    content = await fs.readFile(hermesEnvPath(), 'utf-8')
  } catch {
    return { map }
  }
  for (const line of content.split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/)
    if (!m) continue
    let val = m[2]
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1)
    }
    map.set(m[1], val)
  }
  return { map }
}

// 把 DeepSeek key 同步写回 Hermes .env (保留其他行, 原子写).
// 空值不写回, 避免误清空 Hermes 凭据.
async function syncHermesDeepseekKey(value: string): Promise<void> {
  if (!value) return
  const file = hermesEnvPath()
  let lines: string[] = []
  try {
    lines = (await fs.readFile(file, 'utf-8')).split(/\r?\n/)
  } catch {
    lines = []
  }
  const out: string[] = []
  let replaced = false
  for (const line of lines) {
    if (/^\s*DEEPSEEK_API_KEY\s*=/.test(line)) {
      out.push(`DEEPSEEK_API_KEY=${value}`)
      replaced = true
    } else {
      out.push(line)
    }
  }
  if (!replaced) out.push(`DEEPSEEK_API_KEY=${value}`)
  const tmp = `${file}.${process.pid}.tmp`
  await fs.writeFile(tmp, out.join('\n'), 'utf-8')
  await fs.rename(tmp, file)
}

function normalize(cfg: any): any {
  const provider = String(cfg?.provider || 'local').toLowerCase()
  const local = cfg?.local || {}
  const deepseek = cfg?.deepseek || {}
  return {
    provider,
    local: {
      base_url: String(local.base_url || 'http://127.0.0.1:8080/v1'),
      model: String(local.model || 'local-llm'),
    },
    deepseek: {
      base_url: String(deepseek.base_url || 'https://api.deepseek.com/v1'),
      api_key: String(deepseek.api_key || ''),
      model: String(deepseek.model || 'deepseek-chat'),
    },
  }
}

export async function getIkarosBackend(ctx: any) {
  try {
    const file = configPath()
    let cfg: any
    try {
      cfg = JSON.parse(await fs.readFile(file, 'utf-8'))
    } catch {
      cfg = DEFAULT_CONFIG
    }
    const norm = normalize(cfg)
    // 继承: studio 自身 json 的 key 为空时, 从 Hermes .env 回填显示
    if (!norm.deepseek.api_key) {
      try {
        const { map } = await readHermesEnv()
        const inherited = map.get('DEEPSEEK_API_KEY')
        if (inherited) norm.deepseek.api_key = inherited
      } catch {
        // 忽略: 继承失败则保持空
      }
    }
    ctx.body = norm
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message }
  }
}

export async function putIkarosBackend(ctx: any) {
  const body = ctx.request.body as any
  if (!body || typeof body !== 'object') {
    ctx.status = 400
    ctx.body = { error: 'Missing backend config' }
    return
  }
  try {
    const normalized = normalize(body)
    const file = configPath()
    await fs.mkdir(path.dirname(file), { recursive: true })
    const tmp = `${file}.${process.pid}.tmp`
    await fs.writeFile(tmp, JSON.stringify(normalized, null, 2), 'utf-8')
    await fs.rename(tmp, file)
    // 双向同步: 把 studio 的 deepseek key 写回 Hermes .env (非空时)
    try {
      await syncHermesDeepseekKey(normalized.deepseek.api_key)
    } catch {
      // 不影响主保存
    }
    ctx.body = { success: true, config: normalized }
  } catch (err: any) {
    ctx.status = 500
    ctx.body = { error: err.message }
  }
}
