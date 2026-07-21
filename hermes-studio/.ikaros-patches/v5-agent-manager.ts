import { randomUUID } from 'crypto'
import { execFile } from 'child_process'
import { promisify } from 'util'
import type { SessionState } from '../hermes/run-chat/types'

const execFileAsync = promisify(execFile)

export interface V5AgentOptions {
  modelClient: unknown
  model: string
  workspace: string
  sessionId: string
  profile: string
  input?: string
  userId?: string
}

export interface V5AgentContext {
  cwd: string
  workspaceRoot: string
  workspaceId: string
  userId?: string
  sessionId: string
  mcpServers: Record<string, unknown>
  timeoutMs: number
  signal: AbortSignal
}

export interface V5AgentRunResult {
  output: string
  reasoning?: string
  steps: Array<{
    type: string
    message?: unknown
    result?: { content: string; error?: string }
    toolName?: string
    toolCallId?: string
  }>
  context?: unknown
  runId: string
  finishReason?: string
  usage?: {
    inputTokens: number
    outputTokens: number
  }
}

export class V5AgentManager {
  private activeAgents = new Map<string, AbortController>()

  async run(options: V5AgentOptions, context: V5AgentContext): Promise<V5AgentRunResult> {
    const runId = randomUUID()
    const controller = new AbortController()
    this.activeAgents.set(options.sessionId, controller)

    try {
      // 调用 V5 orchestrator
      const result = await this.callV5Orchestrator(options, context, controller.signal)

      return {
        ...result,
        runId,
      }
    } finally {
      this.activeAgents.delete(options.sessionId)
    }
  }

  abort(sessionId: string): boolean {
    const controller = this.activeAgents.get(sessionId)
    if (controller) {
      controller.abort()
      this.activeAgents.delete(sessionId)
      return true
    }
    return false
  }

  private async callV5Orchestrator(
    options: V5AgentOptions,
    context: V5AgentContext,
    signal: AbortSignal
  ): Promise<Partial<V5AgentRunResult>> {
    // 确定 Python 解释器路径
    const pythonPath = process.env.IKAROS_PYTHON || 'python'

    // 构建参数
    const inputEscaped = JSON.stringify(JSON.stringify(options.input || ''))
    const args = [
      '-c',
      `
import sys
import json
sys.path.insert(0, '${options.workspace}')
from v5.orchestrator import agent_loop

input_text = json.loads(${inputEscaped})
result = agent_loop(input_text, mode='agent', cwd='${context.cwd}')
print(json.dumps({
  'output': result,
  'reasoning': '',
  'steps': [],
  'finishReason': 'stop'
}, ensure_ascii=False))
`
    ]

    try {
      const { stdout, stderr } = await execFileAsync(pythonPath, args, {
        cwd: context.cwd,
        timeout: context.timeoutMs,
        signal: signal as any,
        env: {
          ...process.env,
          IKAROS_ROOT: options.workspace,
          HERMES_ROOT: options.workspace,
        },
      })

      // 解析输出
      const output = stdout.trim()
      if (output.startsWith('{')) {
        return JSON.parse(output)
      }

      return {
        output,
        steps: [],
        finishReason: 'stop',
      }
    } catch (err: any) {
      if (signal.aborted) {
        throw new Error('V5 agent run aborted')
      }
      if (err.killed && err.signal === 'SIGTERM') {
        throw new Error('V5 agent run aborted')
      }
      throw new Error(`V5 orchestrator failed: ${err.message || String(err)}`)
    }
  }

  getActiveSessionIds(): string[] {
    return Array.from(this.activeAgents.keys())
  }

  isActive(sessionId: string): boolean {
    return this.activeAgents.has(sessionId)
  }
}

let activeV5Manager: V5AgentManager | null = null

export function getV5AgentManager(): V5AgentManager {
  if (!activeV5Manager) {
    activeV5Manager = new V5AgentManager()
  }
  return activeV5Manager
}

export function shutdownV5AgentManager(): void {
  if (activeV5Manager) {
    for (const sessionId of activeV5Manager.getActiveSessionIds()) {
      activeV5Manager.abort(sessionId)
    }
    activeV5Manager = null
  }
}