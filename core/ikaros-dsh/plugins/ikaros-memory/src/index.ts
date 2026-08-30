// ikaros-memory —— memory_v5 自动记忆插件（借鉴 dsh-auto-memory 的工程层设计）
//
// 分层：memory_v5 (Python, SQLite+chroma 存储/检索) 保持 MCP server 形态（v5_*
// 工具给模型主动调用、被 dsh 复用）；本插件 = harness 进程内的「工程层」,
// 补齐 MCP 形态做不了的「主动」能力：
//   写回（自动沉淀）: agent/turn-stopping -> 取本轮真实对话 -> 提炼 -> 落盘
//   召回（自动注入）: agent/pre-step -> should_recall 门控 -> 检索 -> 记忆快照注入
//   压缩沉淀（新增 2026-08-24）: session/event -> compaction/summary 捕获 -> 落盘
//     (dsh 压缩会话已花 API 生成 checkpoint, 复用成本, 「压缩即沉淀」; 零额外 LLM)
//
// 标准记忆循环（新增 2026-08-30, 见 memory_v5/loop.py + docs/v5-mcp-consolidation.md）:
//   原本散在本文件三处的记忆动作收敛成「一个 phase 一次调用」:
//     agent/pre-step      -> v5_call loop(phase=pre)          身份 + 召回 + 项目经验
//     agent/turn-stopping -> v5_call loop(phase=post)         精力/关系推进 + 反重复语料
//     6h 定时器           -> v5_call loop(phase=maintenance)  反思管线
//   loopEnabled=false 可整体退回旧路径（search / tick 直调）。
//
// 与 cordis.patch.yml 里的 memory-ikaros-v5（mcp-client）互补：
//   mcp-client 暴露 v5_* 工具给模型「主动」调用；
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
// 引用 dsh-agent / dsh-session 类型声明以加载它们的 Events module augmentation
// （agent/pre-step、agent/turn-stopping、session/event 的事件类型）。仅类型侧,
// 编译产物不引入运行时依赖。
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-session'

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
  /**
   * 自动沉淀模式: 'rule' = 纯规则蒸馏 (零成本, 默认);
   * 'subagent' = 调 subagent 提炼 (更智能, 判断值得记什么 + 升格长期价值, 有 API 成本);
   * 'hybrid' = 规则先写 + subagent 定期提炼升格 (推荐折中)。
   */
  writebackMode: 'rule' | 'subagent' | 'hybrid'
  /** subagent 提炼每日上限 (防白烧 API, 借鉴 dsh-auto-memory 8 次/天) */
  subagentDailyMax: number
  /** subagent 提炼冷却（分钟） */
  subagentCooldownMin: number
  /** 是否捕获 dsh 自动压缩(checkpoint)沉淀进 v5（复用 dsh 已花的摘要 API 成本） */
  compactionCaptureEnabled: boolean
  /** 压缩摘要最短长度（字符）：低于此不写（空/无价值的压缩轮） */
  compactionCaptureMinChars: number
  /** 沉淀权重 */
  compactionImportance: number
  /** 是否周期驱动 memory_v5 记忆维护（生命周期 retention/归档 + 反思 op run_all） */
  maintenanceTickEnabled: boolean
  /** 维护触发间隔（毫秒）：默认 6h 对齐 retention/cleanup/promote 的 6h 周期 */
  maintenanceTickMs: number
  /**
   * 是否用标准记忆循环 (memory_v5/loop.py) 驱动每轮的 pre / post / maintenance。
   * false = 退回 2026-08-30 之前的旧路径（pre-step 直调 search、定时器直调 tick）。
   */
  loopEnabled: boolean
  /** loop pre 阶段召回项目记忆时用的项目名 */
  loopProject: string
}

export const defaultConfig: Config = {
  recallEnabled: true,
  writebackEnabled: true,
  topK: 5,
  minQueryChars: 4,
  writebackCooldownMin: 5,
  writebackMinChars: 60,
  injectBudgetChars: 1200,
  writebackMode: 'hybrid',
  subagentDailyMax: 8,
  subagentCooldownMin: 60,
  compactionCaptureEnabled: true,
  compactionCaptureMinChars: 120,
  compactionImportance: 0.6,
  maintenanceTickEnabled: true,
  maintenanceTickMs: 6 * 3600 * 1000,
  loopEnabled: true,
  loopProject: 'ikaros',
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

// ── pre-step 注入幂等性（2026-08-24 修复）────────────────────────────
// dsh 的 NamedEntries.insert 对同名 context 重复注册会 throw（systemPrompt.context
// duplicate name）。旧实现每次 pre-step 都 sp.context({name:'ikaros:memory-recall'})
// 且从不 dispose → 第二轮用户消息后注入永远停留在第一次的旧快照；多 step 工具循环
// 又重复触发检索。修复：
//   1) 每 turn 只注入一次（按 lastRealUserText 指纹去重, 工具循环同 turn 内不重注）
//   2) 注入前先 dispose 旧 context, 再注册新快照（内容变化只击穿快照自身）
let _lastRecallTurnKey = ''
let _recallContextDispose: (() => void) | null = null

/** 生成 recall 注入的 turn 指纹：真实用户文本驻留的 turn 唯一键。 */
function recallTurnKey(turn: number, query: string): string {
  return `${turn}:${query.trim().slice(0, 40)}`
}

// ── compaction 捕获（2026-08-24 新增）────────────────────────────────
// dsh 自动压缩会话时已花 API 让 LLM 生成结构化 checkpoint（compaction/summary
// 事件携带). 本插件在 session/event 层捕获它沉淀进 v5 —— 零额外 LLM 调用,
// 把"压缩即遗忘"变成"压缩即沉淀"。
interface CompactionSummaryEvent {
  type?: string
  summary?: Array<{ type?: string; text?: string }>
  compactionId?: string
  shadowedTokenCount?: number
}

/** 从 compaction/summary 事件提取纯文本 checkpoint。 */
function extractCompactionSnapshot(event: CompactionSummaryEvent): string {
  const blocks = Array.isArray(event.summary) ? event.summary : []
  return blocks
    .filter((b) => b && b.type === 'text' && typeof b.text === 'string')
    .map((b) => (b as { text: string }).text)
    .join('\n')
    .trim()
}

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

// ── v5 桥接调用（常驻 daemon 进程 + JSON 行协议）────────────────────
// 性能优化 (2026-08-19): 原实现每次 spawn 一次性 python 进程 (~1.5s 冷启动),
// 改为 apply 时启动 `v5_call.py --daemon` 常驻进程, stdin/stdout JSON 行通信。
// subprocess Service 确认: {stdin:'pipe'} → handle.stdin (Writable 写);
// {stdout:'pipe'} → handle.stdout (Readable, on('data') 实时读)。
// 热调用 ~30ms (实测), 比一次性快 ~50x。进程崩溃自动重启。
//
// op 全集（v5_call.py 的 _HANDLERS）:
//   search / store / loop = 现役; tick = 已 deprecated (被 loop(phase=maintenance)
//   取代, 保留仅为兼容未重建的旧插件 dist)。
type V5Op = 'search' | 'store' | 'tick' | 'loop'

interface V5Result {
  ok: boolean
  error?: string
  items?: Array<{ id?: number; content?: string; score?: number; type?: string }>
  id?: number
  /** loop op 返回：{phase, ran, skipped, errors, results{step:payload}, elapsed_ms} */
  results?: Record<string, unknown>
  ran?: string[]
  skipped?: string[]
  elapsed_ms?: number
}

interface V5Daemon {
  handle: {
    stdin?: { write(data: string): unknown }
    stdout?: { on(event: 'data', cb: (chunk: unknown) => void): unknown; on(event: 'error', cb: () => void): unknown }
    done?: Promise<unknown>
    terminate?(): void
  }
  buf: string
  queue: Array<{ op: string; args: Record<string, unknown>; resolve: (r: V5Result) => void }>
  draining: boolean
}

let _v5d: V5Daemon | null = null

function ensureV5Daemon(ctx: Context): V5Daemon | null {
  if (_v5d && _v5d.handle && _v5d.handle.stdin) return _v5d
  const sub = ctx.get('subprocess')
  if (sub === undefined) return null
  try {
    const handle = (sub as {
      spawn(spec: unknown): {
        stdin?: { write(data: string): unknown }
        stdout?: { on(event: string, cb: (chunk?: unknown) => void): unknown }
        done?: Promise<unknown>
        terminate?(): void
      }
    }).spawn({
      argv: [PYTHON, V5_CALL, '--daemon'],
      cwd: path.dirname(PYTHON) + '/../..',
      stdio: {
        stdin: 'pipe',
        stdout: 'pipe',
        stderr: 'pipe',
      },
      graceMs: 5000,
    })
    if (!handle || !handle.stdin) return null
    const d: V5Daemon = { handle, buf: '', queue: [], draining: false }
    // 实时读 stdout → 按行解析响应
    handle.stdout?.on('data', (chunk) => {
      d.buf += String(chunk)
      let nl: number
      while ((nl = d.buf.indexOf('\n')) >= 0) {
        const line = d.buf.slice(0, nl).trim()
        d.buf = d.buf.slice(nl + 1)
        if (!line) continue
        const req = d.queue.shift()
        if (!req) continue
        try { req.resolve(JSON.parse(line) as V5Result) }
        catch { req.resolve({ ok: false, error: 'bad daemon response' }) }
      }
    })
    handle.stdout?.on('error', () => { /* 流错误, 下次调用重启 */ })
    // 进程退出 → 拒绝队列, 清引用 (下次自动重启)
    if (handle.done) {
      void handle.done.then(() => {
        const q = _v5d?.queue || []
        _v5d = null
        for (const r of q) r.resolve({ ok: false, error: 'v5 daemon exited' })
      }).catch(() => { _v5d = null })
    }
    _v5d = d
    return d
  } catch {
    return null
  }
}

function callV5(ctx: Context, op: V5Op, args: Record<string, unknown>): Promise<V5Result> {
  return new Promise((resolve) => {
    const d = ensureV5Daemon(ctx)
    if (!d) {
      // 兜底: 常驻不可用 → 一次性进程 (collect 模式, 向后兼容)
      return callV5Once(ctx, op, args).then(resolve)
    }
    d.queue.push({ op, args, resolve })
    void drainV5Daemon(d)
  })
}

async function drainV5Daemon(d: V5Daemon): Promise<void> {
  if (d.draining) return
  d.draining = true
  try {
    while (d.queue.length) {
      const req = d.queue[0]
      // 写请求 (op + json)
      try {
        d.handle.stdin!.write(`${req.op}\t${JSON.stringify(req.args)}\n`)
      } catch {
        d.queue.shift()
        req.resolve({ ok: false, error: 'write failed' })
        continue
      }
      // 等该请求被 on('data') 解析并 resolve (queue 头被 shift)
      // 轮询等待: 若 15s 无响应则超时
      const deadline = Date.now() + 15_000
      while (d.queue[0] === req && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 25))
      }
      if (d.queue[0] === req) {
        d.queue.shift()
        req.resolve({ ok: false, error: 'v5 daemon timeout' })
      }
    }
  } finally {
    d.draining = false
  }
}

/** 兜底: 一次性进程调用 (collect 模式, 无 daemon 时用) */
function callV5Once(ctx: Context, op: V5Op, args: Record<string, unknown>): Promise<V5Result> {
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
function renderMemorySnapshot(items: V5Result['items'] = [], budget = 1200): string {
  if (!items || !items.length) return ''
  const lines = ['[Ikaros 相关记忆]']
  let budgetLeft = budget
  for (const it of items) {
    if (budgetLeft <= 0) break
    const c = String(it.content || '').slice(0, 300)
    if (!c) continue
    lines.push(`- ${c}`)
    budgetLeft -= c.length
  }
  return lines.join('\n')
}

/**
 * 渲染标准记忆循环 pre 阶段的产出去注入 system prompt。
 *
 * loop(phase=pre) 的 results 形状（见 memory_v5/loop.py）:
 *   { recall: {context, hits, budget_tokens, ...}, project: {count, items:[{kind,content}]},
 *     identity: {...} }
 * identity 是给模型「我是谁」的姿态锚，不进快照（静态纪律段已覆盖，且它每轮内容相同 ——
 * 变化会击穿前缀缓存）。这里只取会随 query 变化的 recall / project 两段做动态快照。
 *
 * 无命中时返回 ''（不注册 context）—— 空快照注入会白占 context 窗口并击穿缓存。
 */
function renderLoopPreSnapshot(results: unknown, budget = 1200): string {
  const r = (results ?? {}) as {
    recall?: { context?: unknown }
    project?: { count?: number; items?: Array<{ kind?: unknown; content?: unknown }> }
  }
  const parts: string[] = []
  // v5_recall 无命中时的占位串, 别把"(无相关记忆)"这类噪声注入进去
  const ctxText = typeof r.recall?.context === 'string' ? r.recall.context.trim() : ''
  if (ctxText && ctxText !== '(无相关记忆)') parts.push(`[Ikaros 相关记忆]\n${ctxText}`)
  const items = Array.isArray(r.project?.items) ? (r.project?.items ?? []) : []
  if (items.length) {
    const lines = items
      .map((it) => `- [${String(it.kind ?? '?')}] ${String(it.content ?? '').slice(0, 160)}`)
      .filter((l) => l.length > 6)
    if (lines.length) {
      parts.push(`[项目经验 · ${String(r.project?.count ?? lines.length)} 条]\n${lines.join('\n')}`)
    }
  }
  if (!parts.length) return ''
  return parts.join('\n\n').slice(0, budget)
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

// ── subagent 提炼（C 阶段: 自动沉淀升级）─────────────────────────────
// 借鉴 dsh-auto-memory: 每轮对话结束后用 subagent 判断"值得记什么",
// 产出 [LOG]/[NOTE]/[USER] 三段式提炼。有 API 成本 → 限流 (每日上限 + 冷却),
// subagent 不可用/失败时回退规则蒸馏 (distillTurn)。
let _subagentCount = 0
let _subagentDate = ''
let _lastSubagentAt = 0

function _resetSubagentDaily(today: string): void {
  if (_subagentDate !== today) {
    _subagentDate = today
    _subagentCount = 0
  }
}

interface SubagentExtract {
  log: string[]      // 今日日志要点
  note: string[]     // 项目长期笔记 (决策/架构)
  user: string[]     // 用户级规则/偏好
}

/**
 * 调 subagent 提炼本轮对话。返回三段式提炼; subagent 不可用/失败返回 null。
 * 用 ctx.subagents.start(name, {prompt, parent, signal}) — SubagentStartRequest。
 */
async function subagentExtract(
  ctx: Context,
  agent: unknown,
  signal: AbortSignal | undefined,
  user: string,
  assistant: string,
): Promise<SubagentExtract | null> {
  const subs = ctx.get('subagents')
  if (subs === undefined || typeof (subs as { start?: unknown }).start !== 'function') return null
  try {
    const promptText = [
      '你是 Ikaros 的记忆沉淀员。根据下面的对话，判断是否有值得长期记住的内容。',
      '规则:',
      '- 寒暄/琐碎/临时信息(搜索结果、临时路径、工具报错) → 全部输出 (无)',
      '- 完成实质工作(改代码/修bug/写文档/重构/技术选型/用户约定) → 提炼要点',
      '- [LOG] 今日工作日志要点(一句话一条); [NOTE] 项目长期价值(决策/架构/坑, 跨会话有用);',
      '  [USER] 用户级规则/偏好(跨项目)',
      '- 没有价值的类别留空(不输出该段)',
      '- 只输出结构化结果, 不要解释',
      '',
      '[对话]',
      `用户: ${user.slice(0, 800)}`,
      `助手: ${assistant.slice(0, 1500)}`,
    ].join('\n')
    const result = await (subs as {
      start(name: string, req: { prompt: unknown[]; parent: unknown; signal?: unknown; label?: string }): Promise<{
        result?: { structured?: unknown; text?: string }
      }>
    }).start('default', {
      prompt: [{ type: 'text', text: promptText }],
      parent: agent,
      signal,
      label: 'ikaros-memory-consolidate',
    })
    const text = result?.result?.text || ''
    if (!text || text.includes('(无)')) return null
    // 解析 [LOG]/[NOTE]/[USER] 段
    const out: SubagentExtract = { log: [], note: [], user: [] }
    let section: 'log' | 'note' | 'user' | null = null
    for (const raw of text.split('\n')) {
      const l = raw.trim()
      if (l === '[LOG]') { section = 'log'; continue }
      if (l === '[NOTE]') { section = 'note'; continue }
      if (l === '[USER]') { section = 'user'; continue }
      if (section && l.startsWith('- ')) out[section].push(l.slice(2).trim().slice(0, 300))
    }
    if (!out.log.length && !out.note.length && !out.user.length) return null
    return out
  } catch { return null }
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
        if (!pair.user || !pair.assistant) return
        if (pair.user.length + pair.assistant.length < config.writebackMinChars) return

        // ── subagent 提炼 (mode=subagent|hybrid, 限流) ──
        const today = new Date().toISOString().slice(0, 10)
        _resetSubagentDaily(today)
        const canSubagent =
          config.writebackMode !== 'rule' &&
          _subagentCount < config.subagentDailyMax &&
          now - _lastSubagentAt >= config.subagentCooldownMin * 60_000
        if (canSubagent) {
          _subagentCount++
          _lastSubagentAt = now
          const ex = await subagentExtract(ctx, agent, signal, pair.user, pair.assistant)
          if (ex) {
            // NOTE → 项目笔记 (type=decision 语义, 走 v5 结构化标签)
            for (const n of ex.note) {
              await callV5(ctx, 'store', {
                content: n,
                memory_type: 'decision',
                tags: ['source:dsh', 'v5_kind:decision'],
                importance: 0.7,
              })
            }
            // USER → 用户级偏好
            for (const u of ex.user) {
              await callV5(ctx, 'store', {
                content: u,
                memory_type: 'preference',
                tags: ['source:dsh'],
                importance: 0.8,
              })
            }
            // LOG → 对话日志
            if (ex.log.length) {
              await callV5(ctx, 'store', {
                content: ex.log.join('\n'),
                memory_type: 'conversation',
                tags: ['source:dsh'],
                importance: 0.6,
              })
            }
            _lastWritebackAt = Date.now()
            return
          }
          // subagent 返回空 → 落回规则蒸馏
        }

        // ── 规则蒸馏兜底 (mode=rule|hybrid 且 subagent 未用/失败) ──
        if (config.writebackMode === 'subagent') return // subagent 模式失败不落规则
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

  // ── 1b) 标准记忆循环 · post 阶段（2026-08-30 新增）─────────────────
  // 与上面的自动沉淀 (1) **分开注册**, 因为两者的触发条件不同:
  //   写回有 5 分钟冷却 + 最短轮长闸 (防连续短轮反复写库);
  //   post 阶段的精力/关系推进 + 反重复语料记录是**每轮**的状态推进,
  //   被写回的防抖连带跳过就会出现"聊了 50 轮但关系一次都没推进"的欠账。
  // 失败静默, 不干扰 turn 释放。
  ctx.on('agent/turn-stopping', ({ agent, signal }) => {
    if (!config.loopEnabled || signal.aborted) return
    void (async () => {
      try {
        const session = (agent as { session?: { deriveMessages(): unknown[] } } | null)?.session
        if (!session || typeof session.deriveMessages !== 'function') return
        const pair = extractTurnPair(session.deriveMessages())
        if (!pair.assistant) return
        await callV5(ctx, 'loop', {
          phase: 'post',
          query: pair.user ?? '',
          response: pair.assistant,
          session_id: 'dsh',
          character: 'ikaros',
        })
      } catch { /* 循环失败静默, 不干扰会话 */ }
    })()
  })

  // ── 2) 召回（waterfall, 必须 next()）───────────────────────────────
  ctx.on('agent/pre-step', async ({ agent, turn, messages, signal }, next) => {
    const decision = await next()
    if (decision.kind !== 'enter') return decision
    if (!config.recallEnabled || signal.aborted) return decision

    const query = lastRealUserText(messages)
    if (!query || !shouldRecall(query) || query.length < config.minQueryChars) return decision

    // ── 幂等：同一用户 turn（含多 step 工具循环）+ 同一 query 只注入一次 ──
    // 2026-08-24 修复: 旧实现每个 pre-step 都 sp.context({name:'ikaros:memory-recall'})
    // 且从不 dispose → NamedEntries 同名重复注册 throw（被吞）→ 第二轮用户消息后记忆
    // 注入永远停在第一次的旧快照; 且同 turn 多 step 重复检索浪费。
    const turnKey = recallTurnKey(turn, query)
    if (turnKey === _lastRecallTurnKey) return decision
    _lastRecallTurnKey = turnKey

    // 2026-08-30: 默认走标准记忆循环的 pre 阶段 —— 一次调用跑完
    // 身份锚定 + 预算感知召回 + 项目经验召回 (memory_v5/loop.py)。
    // 相比旧 callV5('search'): 召回带 token 预算与跨轮去重 (recall_ledger),
    // 且顺带带回项目轨的 decision/pitfall/convention。
    // 检索/循环失败静默降级, 不阻断 step（召回是增强, 不是硬依赖）。
    let snapshot = ''
    if (config.loopEnabled) {
      const loop = await callV5(ctx, 'loop', {
        phase: 'pre',
        query,
        session_id: 'dsh',
        project: config.loopProject,
        include_dsh_only: true,
      }).catch(() => null)
      if (loop?.ok) snapshot = renderLoopPreSnapshot(loop.results, config.injectBudgetChars)
    } else {
      const memory = await callV5(ctx, 'search', { query, top_k: config.topK }).catch(() => null)
      if (memory?.ok && memory.items?.length) {
        snapshot = renderMemorySnapshot(memory.items, config.injectBudgetChars)
      }
    }
    if (snapshot) {
      // 注入走 systemPrompt.context() —— user-role 快照, 前缀缓存友好
      try {
        const sp = ctx.get('systemPrompt')
        if (sp && typeof (sp as { context?: unknown }).context === 'function') {
          // 先 dispose 旧 context 再注册新快照（同名重复注册会 throw; dispose 后内容
          // 变化只击穿快照自身, 不重算整个 system prompt 前缀 → KV 复用不受损）
          if (_recallContextDispose) {
            try { _recallContextDispose() } catch { /* noop */ }
            _recallContextDispose = null
          }
          const dispose = (sp as { context(opts: unknown): () => void }).context({
            name: 'ikaros:memory-recall',
            order: 2_000_000,
            text: () => snapshot,
          } as never)
          _recallContextDispose = dispose
          ctx.effect(() => () => {
            try { _recallContextDispose?.() } catch { /* noop */ }
            _recallContextDispose = null
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

  // ── 3) compaction 捕获（2026-08-24 新增）───────────────────────────
  // dsh 压缩会话(compaction-basic)时已花 API 让 LLM 生成结构化 checkpoint
  // (compaction/summary 事件携带)。本插件在 session/event 层捕获它沉淀进 v5:
  //   - 零额外 LLM 调用 —— 复用 dsh 压缩摘要成本
  //   - "压缩即遗忘" → "压缩即沉淀": 被替换掉的历史转为长期记忆, 不随 checkpoint 丢失
  // 事件时序: compaction/start → compaction/summary → compaction/end(+user/message 替换)
  ctx.on('session/event', (session, event) => {
    if (!config.compactionCaptureEnabled) return
    const ev = event as unknown as CompactionSummaryEvent | null
    if (!ev || ev.type !== 'compaction/summary') return
    try {
      const snapshot = extractCompactionSnapshot(ev)
      if (snapshot.length < config.compactionCaptureMinChars) return
      // 静默失败, 不干扰 session 事件流（fire-and-forget）
      void callV5(ctx, 'store', {
        content: snapshot.slice(0, 3000),
        memory_type: 'conversation',
        tags: ['source:dsh', 'v5_kind:dsh-compaction'],
        importance: config.compactionImportance,
      }).catch(() => null)
    } catch { /* 捕获失败静默: 记忆是增强, 不硬依赖 */ }
  })

  // ── 4) 记忆维护定时器（2026-08-24 新增）────────────────────────────
  // memory_v5 的 reflect scheduler 是纯触发式（仅 v5_reflect_run_op 工具能调），
  // 2026-08-19 集中看门狗退役后无自动触发源 → 生命周期(retention/promote/archive)
  // 静默停摆多日（实测 long_term=0, <AGENTS.md 期望 563）。本插件用 dsh 的 ctx.interval
  // 周期驱动 —— 万物皆插件, 零 runtime 侵入。纯算法无 LLM 成本。
  if (config.maintenanceTickEnabled) {
    try {
      const timer = ctx.get('timer') as { interval?(cb: () => void, ms: number): { dispose?(): void } | (() => void) } | undefined
      if (timer && typeof timer.interval === 'function') {
        const handle = timer.interval(() => {
          // 静默: 维护失败不干扰会话
          // 2026-08-30: 统一走标准记忆循环的 maintenance 阶段（tick op 已 deprecated）。
          // 维护步自身带 6h 冷却（loop.py maintenance.reflect interval_sec=21600）,
          // 定时器重复触发或重启后连跑都不会空转。
          if (config.loopEnabled) {
            void callV5(ctx, 'loop', { phase: 'maintenance' }).catch(() => null)
          } else {
            void callV5(ctx, 'tick', {}).catch(() => null)
          }
        }, config.maintenanceTickMs)
        const dispose = typeof handle === 'function' ? handle : () => { try { handle.dispose?.() } catch { /* noop */ } }
        ctx.effect(() => () => { try { dispose() } catch { /* noop */ } }, 'ikaros-memory: maintenance tick')
      }
    } catch { /* timer/interval 不可用则静默（维护是增强, 不硬依赖） */ }
  }
}
