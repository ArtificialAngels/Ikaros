// ikaros-memory —— memory_v5 召回/写回插件骨架
//
// 把 hermes 里 ikaros_v5 插件做过的「自动召回 + 自动写回」搬到 dsh：
//   召回（读）：agent/pre-step waterfall 里检索记忆，agent.inject() 注入
//   写回（写）：agent/turn-stopping 里把本轮 user/assistant 写回 memory_v5
//
// 与 cordis.patch.yml 里的 memory-ikaros-v5（mcp-client）互补：
//   mcp-client 暴露 48 个 v5_* 工具给模型「主动」调用；
//   本插件做「自动」注入/写回（对应 hermes 的 prefetch / sync_turn）。
//
// 事件签名（packages/core/agent/src/runtime-types.ts）：
//   'agent/pre-step'(payload{agent,messages,turn,step,signal}, next): Promise<PreStepDecision>
//   'agent/turn-stopping'(payload{agent,turn,signal}): Promise<void> | void

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
}

export const defaultConfig: Config = {
  recallEnabled: true,
  writebackEnabled: true,
  topK: 5,
  minQueryChars: 4,
}

/**
 * 召回决策：复刻 memory_v5 的 should_recall 三级门控（线索词必召回 /
 * 寒暄跳过 / 实质内容召回），避免每轮无差别注入（hermes 的缺陷）。
 */
const RECALL_CUE = /记得|上次|之前|回顾|关于|最近|remember|上次聊|聊过/
const SMALLTALK = /^(你好|谢谢|晚安|ok|好的|嗯|哦|收到|继续|对|是)\b?/i

function shouldRecall(text: string): boolean {
  if (!text) return false
  if (RECALL_CUE.test(text)) return true
  if (SMALLTALK.test(text) && text.length < 20) return false
  return text.trim().length >= 8
}

function extractLastUserText(messages: unknown[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as { role?: string; content?: unknown }
    if (m?.role === 'user') {
      const content = m.content
      if (typeof content === 'string') return content
      if (Array.isArray(content)) {
        for (const block of content) {
          if (typeof block === 'object' && block !== null && (block as { text?: string }).text) {
            return (block as { text: string }).text
          }
        }
      }
    }
  }
  return ''
}

export function apply(ctx: Context, config: Config = defaultConfig) {
  // ── 写回（serial，无 next）──────────────────────────────────────────
  // turn 结束只保证「本轮的 user/assistant 内容已定」，异步写回走
  // ctx.jobs 或 fire-and-forget，绝不阻塞 driver 释放 turn。
  ctx.on('agent/turn-stopping', async ({ agent, turn, signal }) => {
    if (!config.writebackEnabled || signal.aborted) return
    // TODO: 从 agent.session 取本 turn 的最后 user/message 与 assistant/message
    // （surface 事件流里按 turn 过滤），组装 user_content/assistant_content。
    // 然后调 writeMemory()（下方接口），写入 type=conversation。
  })

  // ── 召回（waterfall，必须 next()）───────────────────────────────────
  ctx.on('agent/pre-step', async ({ agent, messages, turn, step, signal }, next) => {
    const decision = await next()
    if (decision.kind !== 'enter') return decision
    if (!config.recallEnabled || signal.aborted) return decision

    const query = extractLastUserText(messages)
    if (!shouldRecall(query) || query.length < config.minQueryChars) return decision

    // 检索失败静默降级，不阻断 step（召回是增强，不是硬依赖）。
    const memory = await recallMemory(query, config.topK).catch(() => null)
    if (memory) {
      // agent.inject() 把记忆作为模型可见上下文排到下一次 admitted request。
      // 备选：直接在 pre-step 返回 { kind:'enter', messages:[...messages, memoryMsg] }
      // 让记忆进入当前 step（但会改变当前请求前缀，破坏 KV cache 复用）。
      agent.inject({
        role: 'user',
        content: [{ type: 'text', text: `[Ikaros 相关记忆]\n${memory}` }],
        // source 标记为记忆来源，渲染/审计可识别（对齐 dsh 的 source 契约）。
      } as never)
    }
    return decision
  })
}

// ── memory_v5 调用接口（实现二选一，见 README）──────────────────────────
//
// 方案 A（推荐）：复用 dsh-mcp-client 已建立的 stdio 连接。
//   若 mcp-client 暴露 programmatic call（ctx 上的 mcp 服务），直接调
//   v5_memory_search / v5_memory_store，零额外进程。
//
// 方案 B（兜底）：独立 child_process 常驻一个 MCP stdio 客户端
//   （或每次 spawn portable-python + 轻量 JSON 桥接脚本）。
//   command = E:\Ikaros\runtime\portable-python\python.exe
//   args    = [E:\Ikaros\core\ikaros-dsh\plugins\ikaros-memory\bin\v5_call.py, <tool>, <json>]
//   v5_call.py 用 FastMCP 的 call_tool 单次调用一个工具，stdout 输出 JSON。

async function recallMemory(query: string, topK: number): Promise<string | null> {
  // TODO: 调 v5_memory_search { query, top_k: topK }
  //       把结果渲染成 "[score] content" 行，空结果返回 null。
  return null
}

async function writeMemory(userContent: string, assistantContent: string): Promise<void> {
  // TODO: 调 v5_memory_store { content: `Q: ...\nA: ...`, type: 'conversation' }
  //       或走 memory_v5 的 store.upsert 合并强化（避免雷同膨胀）。
}
