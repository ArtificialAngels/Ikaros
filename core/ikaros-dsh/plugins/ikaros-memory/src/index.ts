// ikaros-memory —— memory_v5 自动记忆插件（借鉴 dsh-auto-memory 的工程层设计）
//
// 分层：memory_v5 (Python, SQLite+chroma 存储/检索) 保持 MCP server 形态（48 个
// v5_* 工具给模型主动调用、被 dsh + pi 复用）；本插件 = harness 进程内的「工程层」,
// 补齐 MCP 形态做不了的「主动」能力：
//   写回（自动沉淀）: agent/turn-stopping -> 取本轮真实对话 -> 提炼 -> 落盘
//   召回（自动注入）: agent/pre-step -> should_recall 门控 -> 检索 -> 记忆快照注入
//
// 与 cordis.patch.yml 里的 memory-ikaros-v5（mcp-client）互补：
//   mcp-client 暴露 48 个 v5_* 工具给模型「主动」调用；
//   本插件做「自动」注入/写回（对应 hermes 的 prefetch / sync_turn）。
//
// 前缀缓存友好注入（dsh-auto-memory 的核心工程思想）:
//   - 静态纪律 -> systemPrompt.section()  : 字节级稳定, 是 DeepSeek 前缀缓存的锚
//   - 动态快照 -> systemPrompt.context() : user-role 快照, 内容变化只击穿快照本身,
//     不重算整个 system prompt 前缀（agent.inject 会改变请求前缀破坏 KV 复用, 弃用）
//
// dsh API 要点（2026-08-18 实测确认）:
//   - 事件: 'agent/turn-stopping'(payload{agent,turn,signal}) serial;
//           'agent/pre-step'(payload{agent,messages,...}, next) waterfall
//   - 会话消息: agent.session.deriveMessages(): Message[] (surface 折叠的 LLM 历史)
//   - Message.source 是对象 MessageSource, kind: 'user'|'plugin'|'model'|'tool'
//     (dsh-llm message.d.ts) —— 真实用户输入判定: source.kind === 'user';
//     系统注入的 runtime-context / plugin 注入 kind='plugin', 写回时必须排除
//   - subprocess Service: spawn({argv,cwd,stdio:{stdout:{collect:true},stderr:{collect:true}},graceMs})
//     返回 handle.done:Promise<SubprocessOutcome> + handle.collected.stdout.readFrom(0)
//     (不是 EventEmitter, 早期版本误用 .on('close') 导致调用链静默失败)

import { fileURLToPath } from 'node:url'
import path from 'node:path'
import type { Context } from '@deepseek-ai/cordis'

export const name = 'ikaros-memory'

export interface Config {
  /** 是否自动召回注入 */
  recallEnabled: boolean
  /** 是否自动写回 */
  writebackEnabled: boolean
  /** 召回 top_k */
  topK: number
  /** 低于此长度的 query 不召回（对应 memory_v5 的 should_recall 寒暄闸） */
  minQueryChars: number
  /** 自动沉淀冷却（分钟）：连续短轮不要反复写, 默认 5 分钟 */
  writebackCooldownMin: number
  /** 自动沉淀最短轮长（字）：低于此长度不写（寒暄/琐碎） */
  writebackMinChars: number
  /** 注入记忆快照预算（字符） */
  injectBudgetChars: number
}

export const defaultConfig: Config = {
  recallEnabled: true,
  writebackEnabled: true,
  topK: 5,
  minQueryChars: 4,
  writebackCooldownMin: 5,
  writebackMinChars: 60,
  injectBudgetChars: 1200,
}

// ── 常量 ───────────────────────────────────────────────────────────────
/** 本插件文件所在目录（core/ikaros-dsh/plugins/ikaros-memory/） */
const __dirname = path.dirname(fileURLToPath(import.meta.url))
/** v5 桥接脚本 */
const V5_CALL = path.join(__dirname, '..', 'bin', 'v5_call.py')
/** Python 可执行（优先环境, 兜底便携） */
const PYTHON =
  process.env.IKAROS_PYTHON || path.join(__dirname, '..', '..', '..', '..', 'runtime', 'portable-python', 'python.exe')

/** 时钟/冷却 */
let _lastWritebackAt = 0

// ── 召回决策（复刻 memory_v5.should_recall 三级门控）─────────────────
const RECALL_CUE = /记得|上次|之前|回顾|关于|最近|remember|上次聊|聊过/
const SMALLTALK = /^(你好|谢谢|晚安|ok|好的|嗯|哦|收到|继续|对|是)\b/i

function shouldRecall(text: string): boolean {
  if (!text) return false
  if (RECALL_CUE.test(text)) return true
  if (SMALLTALK.test(text) && text.length < 20) return false
  return text.trim().length >= 8
}

// ── 消息提取 ──────────────────────────────────────────────────────────
interface AnyMsg {
  role?: string
  content?: unknown
  source?: { kind?: string } | undefined
}

function extractText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    for (const block of content) {
      if (typeof block === 'object' && block !== null && (block as { text?: string }).text) {
        return (block as { text: string }).text
      }
    }
  }
  return ''
}

/** 真实用户消息判定：MessageSource.kind === 'user'（dsh-llm message.d.ts）。 */
function isRealUser(m: AnyMsg | undefined | null): boolean {
  if (!m || m.role !== 'user') return false
  const src = m.source
  if (!src || typeof src !== 'object') return false
  return src.kind === 'user'
}

function lastRealUserText(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as AnyMsg
    if (isRealUser(m)) {
      const t = extractText(m.content)
      if (t) return t
    }
  }
  return ''
}

/** 从派生消息历史取「最后真实 user + 其后的 assistant」对话片段（跳过系统注入）。 */
function extractTurnPair(messages: unknown[]): { user: string; assistant: string } {
  let assistant = ''
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as AnyMsg
    const t = extractText(m?.content)
    if (m?.role === 'assistant') {
      if (t) assistant = t
    } else if (isRealUser(m)) {
      return { user: t, assistant }
    }
  }
  return { user: '', assistant: '' }
}

// ── v5 桥接调用（dsh subprocess Service：collect/done API）────────────
interface V5Result {
  ok: boolean
  error?: string
  items?: Array<{ id?: number; content?: string; score?: number; type?: string }>
  id?: number
}

function callV5(ctx: Context, op: 'search' | 'store', args: Record<string, unknown>): Promise<V5Result> {
  return new Promise((resolve) => {
    const sub = ctx.get('subprocess')
    if (sub === undefined) return resolve({ ok: false, error: 'subprocess unavailable' })
    const timer = ctx.get('timer')
    let to: (() => void) | null = null
    const finish = (r: V5Result) => {
      if (to) { try { to() } catch { /* noop */ } }
      resolve(r)
    }
    try {
      const handle = (sub as {
        spawn(spec: unknown): {
          done: Promise<{ exitCode: number | null; signal: unknown }>
          collected?: {
            stdout?: { readFrom(offset: number): Promise<{ text: string }> }
            stderr?: { readFrom(offset: number): Promise<{ text: string }> }
          }
        }
      }).spawn({
        argv: [PYTHON, V5_CALL, op, JSON.stringify(args)],
        cwd: path.dirname(PYTHON) + '/../..',
        stdio: {
          stdout: { collect: true, maxBytes: 1024 * 1024 },
          stderr: { collect: true, maxBytes: 64 * 1024 },
        },
        graceMs: 5000,
      })
      if (!handle || typeof handle.done !== 'object') return finish({ ok: false, error: 'bad handle' })
      handle.done
        .then(async () => {
          try {
            let out = ''
            let err = ''
            try {
              const r = handle.collected?.stdout
              if (r && typeof r.readFrom === 'function') out = String((await r.readFrom(0)).text || '')
              const e = handle.collected?.stderr
              if (e && typeof e.readFrom === 'function') err = String((await e.readFrom(0)).text || '')
            } catch { /* 读取失败忽略 */ }
            const last = out.trim().split('\n').pop() || ''
            try { finish(JSON.parse(last) as V5Result) }
            catch { finish({ ok: false, error: 'bad output: ' + err.slice(0, 200) }) }
          } catch (e2) { finish({ ok: false, error: String((e2 as Error)?.message || e2) }) }
        })
        .catch((e) => finish({ ok: false, error: 'spawn err: ' + String((e as Error)?.message || e) }))
    } catch (e) { finish({ ok: false, error: String((e as Error)?.message || e) }) }
    if (timer && typeof (timer as { timeout?: unknown }).timeout === 'function') {
      to = (timer as { timeout(cb: () => void, ms: number): () => void }).timeout(
        () => finish({ ok: false, error: 'v5_call timeout (15s)' }),
        15_000,
      )
    }
  })
}

// ── 渲染注入内容 ──────────────────────────────────────────────────────
function renderMemorySnapshot(items: V5Result['items'] = []): string {
  if (!items || !items.length) return ''
  const lines = ['[Ikaros 相关记忆]']
  let budget = 1200
  for (const it of items) {
    if (budget <= 0) break
    const c = String(it.content || '').slice(0, 300)
    if (!c) continue
    lines.push(`- ${c}`)
    budget -= c.length
  }
  return lines.join('\n')
}

/** 静态纪律（不随状态变化）→ system prompt 的字节稳定锚。 */
function renderStaticRules(): string {
  return [
    '[Ikaros 记忆纪律 — 固定]',
    '- 记忆系统 = memory_v5 (v5.db 唯一真相源 + chroma 向量)。工具为 mcp__ikaros-v5__* 前缀。',
    '- 完成实质性工作后：调 mcp__ikaros-v5__v5_memory_store 显式落盘（type=conversation 走 upsert 合并强化）。',
    '- 项目决策/坑/约定 → mcp__ikaros-v5__v5_project_note（kind=decision|pitfall|convention）。',
    '- 开工先检索：历史上下文先用 mcp__ikaros-v5__v5_memory_search / v5_project_retrieve 回顾。',
    '- 记忆操作结果要在正文可见（用户能看到的回复文本里说明存了什么/查到了什么）。',
    '- 自动沉淀由插件兜底：每轮结束背景写回，不依赖你自觉；寒暄/琐碎轮跳过。',
    '- 记忆仅作补充，不替代正常回复与交付物。',
  ].join('\n')
}

// ── 自动沉淀提炼（零 subagent 成本：规则蒸馏）────────────────────────
function distillTurn(user: string, assistant: string): string | null {
  const u = user.trim()
  const a = assistant.trim()
  if (!u || !a) return null
  if (u.length + a.length < 60) return null // 寒暄/琐碎
  const cleaned = a.replace(/^(好的|没问题|收到|可以|让我|我来|好的，|嗯，)/, '').trim()
  if (cleaned.length < 20) return null
  return `Q: ${u.slice(0, 500)}\nA: ${cleaned.slice(0, 1200)}`
}

export function apply(ctx: Context, config: Config = defaultConfig) {
  // ── 0) 前缀缓存友好的注入 ──────────────────────────────────────────
  try {
    const sp = ctx.get('systemPrompt')
    if (sp && typeof (sp as { section?: unknown }).section === 'function') {
      const dispose = (sp as { section(opts: unknown): () => void }).section({
        name: 'ikaros:memory-rules',
        order: 1_000_000,
        text: () => renderStaticRules(),
      } as never)
      ctx.effect(() => () => {
        try { dispose() } catch { /* noop */ }
      }, 'ikaros-memory: dispose section')
    }
  } catch { /* systemPrompt 不可用则静默（仅注入缺失, 不影响主链） */ }

  // ── 1) 写回（自动沉淀, serial, fire-and-forget 不阻塞 turn 释放）───
  ctx.on('agent/turn-stopping', ({ agent, signal }) => {
    if (!config.writebackEnabled || signal.aborted) return
    // 冷却: 与上轮写回间隔 < cooldownMin 则跳过（连续短轮防抖）
    const now = Date.now()
    if (now - _lastWritebackAt < config.writebackCooldownMin * 60_000) return
    void (async () => {
      try {
        const session = (agent as { session?: { deriveMessages(): unknown[] } } | null)?.session
        if (!session || typeof session.deriveMessages !== 'function') return
        const msgs = session.deriveMessages()
        const pair = extractTurnPair(msgs)
        const distilled = distillTurn(pair.user, pair.assistant)
        if (!distilled) return
        const res = await callV5(ctx, 'store', {
          content: distilled,
          memory_type: 'conversation',
          tags: ['source:dsh'],
          importance: 0.6,
        })
        if (res.ok) _lastWritebackAt = Date.now()
      } catch { /* 写回失败静默, 不干扰会话 */ }
    })()
  })

  // ── 2) 召回（waterfall, 必须 next()）───────────────────────────────
  ctx.on('agent/pre-step', async ({ agent, messages, signal }, next) => {
    const decision = await next()
    if (decision.kind !== 'enter') return decision
    if (!config.recallEnabled || signal.aborted) return decision

    const query = lastRealUserText(messages)
    if (!query || !shouldRecall(query) || query.length < config.minQueryChars) return decision

    // 检索失败静默降级, 不阻断 step（召回是增强, 不是硬依赖）。
    const memory = await callV5(ctx, 'search', { query, top_k: config.topK }).catch(() => null)
    if (memory?.ok && memory.items?.length) {
      const snapshot = renderMemorySnapshot(memory.items)
      if (!snapshot) return decision
      // 注入走 systemPrompt.context() —— user-role 快照, 前缀缓存友好
      try {
        const sp = ctx.get('systemPrompt')
        if (sp && typeof (sp as { context?: unknown }).context === 'function') {
          const dispose = (sp as { context(opts: unknown): () => void }).context({
            name: 'ikaros:memory-recall',
            order: 2_000_000,
            text: () => snapshot,
          } as never)
          ctx.effect(() => () => {
            try { dispose() } catch { /* noop */ }
          }, 'ikaros-memory: dispose recall context')
        } else {
          // 兜底: 无 systemPrompt.context 时退回 agent.inject
          const inj = (agent as { inject?: (msg: unknown) => void } | null)?.inject
          if (inj) inj({ role: 'user', content: { type: 'text', text: snapshot } })
        }
      } catch { /* 注入失败静默 */ }
    }
    return decision
  })
}