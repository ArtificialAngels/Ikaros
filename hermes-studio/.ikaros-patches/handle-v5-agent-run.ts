import type { Server, Socket } from 'socket.io'
import { randomUUID } from 'crypto'
import { getV5AgentManager } from '../../v5-agent/manager'
import { createSession, addMessage, getSession, updateSession, updateSessionStats } from '../../../db/hermes/session-store'
import { logger } from '../../logger'
import { getOrCreateSession } from './compression'
import { resolveBridgeRunModelConfig } from './model-config'
import { estimateUsageTokensFromMessages } from './usage'
import { contentBlocksToString, extractTextForPreview } from './content-blocks'
import type { SessionState, ContentBlock } from './types'
import { observeRunChatPetEvent } from '../pet-state-socket'

export interface V5AgentRunSocketData {
  input: string | ContentBlock[]
  display_input?: string | ContentBlock[] | null
  display_role?: 'user' | 'command'
  storage_message?: string
  session_id?: string
  profile?: string
  provider?: string
  model?: string
  workspace?: string | null
  baseUrl?: string
  base_url?: string
  apiKey?: string
  api_key?: string
  mode?: 'scoped' | 'global'
  source?: string
  peerExcludeSocketId?: string
  queue_id?: string
  onEvent?: (event: string, payload: any) => void
}

function isV5AgentId(data: V5AgentRunSocketData): boolean {
  return data.coding_agent_id === 'ikaros-v5' || data.agent_id === 'ikaros-v5'
}

export async function handleV5AgentRun(
  nsp: ReturnType<Server['of']>,
  socket: Socket,
  data: V5AgentRunSocketData,
  profile: string,
  sessionMap: Map<string, SessionState>,
  dequeueNextQueuedRun: (socket: Socket, sessionId: string, fallbackProfile?: string) => boolean,
  skipUserMessage = false,
) {
  const sessionId = String(data.session_id || '').trim()
  if (!sessionId) {
    socket.emit('run.failed', { event: 'run.failed', error: 'session_id is required for v5-agent runs' })
    return
  }
  if (!isV5AgentId(data)) {
    socket.emit('run.failed', { event: 'run.failed', session_id: sessionId, error: 'v5-agent run requires coding_agent_id=ikaros-v5' })
    return
  }

  socket.join(`session:${sessionId}`)
  const state = getOrCreateSession(sessionMap, sessionId)
  state.isWorking = true
  state.isAborting = false
  state.profile = profile
  state.source = data.source === 'workflow' ? 'workflow' : 'coding_agent'
  state.events = []
  const abortController = new AbortController()
  state.abortController = abortController

  const storedSession = getSession(sessionId)
  const modelConfig = await resolveBridgeRunModelConfig({
    profile,
    sessionModel: storedSession?.model,
    sessionProvider: storedSession?.provider,
    requestedModel: data.model,
    requestedProvider: data.provider,
    preferRequested: true,
  })

  // 使用 V5 工作目录
  const defaultWorkspace = 'E:\\Ikaros\\Ikaros-memory'
  const workspace = data.workspace || storedSession?.workspace || defaultWorkspace

  const displayInput = data.display_input === undefined ? data.input : data.display_input
  const inputText = contentBlocksToString(data.input)
  const displayText = displayInput == null ? '' : contentBlocksToString(displayInput)
  const storageText = data.storage_message !== undefined ? data.storage_message : displayText
  const shouldPersistUserMessage = !skipUserMessage && displayInput !== null
  const now = Math.floor(Date.now() / 1000)

  const emit = (event: string, payload: any) => {
    const tagged = { ...payload, session_id: sessionId }
    observeRunChatPetEvent(profile, event, tagged)
    data.onEvent?.(event, tagged)
    appendStateEvent(state, event, tagged)
    nsp.to(`session:${sessionId}`).emit(event, tagged)
    if (!data.onEvent && !nsp.adapter.rooms.get(`session:${sessionId}`)?.size && socket.connected) {
      socket.emit(event, tagged)
    }
  }

  if (!storedSession) {
    const previewText = extractTextForPreview(displayInput === null ? data.input : displayInput || data.input)
    const title = previewText.replace(/[\r\n]/g, ' ').substring(0, 100)
    createSession({
      id: sessionId,
      profile,
      source: 'coding_agent',
      agent: 'ikaros-v5',
      agent_mode: 'scoped',
      model: modelConfig.model,
      provider: modelConfig.provider,
      title,
      workspace,
    })
  }

  try {
    updateSession(sessionId, { ended_at: null, end_reason: null, last_active: now })
  } catch (err) {
    logger.warn(err, '[v5-agent-run] failed to reopen v5-agent session %s', sessionId)
  }

  if (shouldPersistUserMessage) {
    const role = data.display_role === 'command' ? 'command' : 'user'
    const messageId = addMessage({
      session_id: sessionId,
      role,
      content: storageText,
      timestamp: now,
    })
    state.messages.push({
      id: data.queue_id || messageId || state.messages.length + 1,
      session_id: sessionId,
      role,
      content: storageText,
      timestamp: now,
    })
    const peerTarget = data.peerExcludeSocketId
      ? nsp.to(`session:${sessionId}`).except(data.peerExcludeSocketId)
      : socket.to(`session:${sessionId}`)
    peerTarget.emit('run.peer_user_message', {
      event: 'run.peer_user_message',
      session_id: sessionId,
      message: {
        id: data.queue_id || messageId,
        role,
        content: storageText,
        timestamp: now,
      },
    })
  }

  const agent = getV5AgentManager()
  let runId = ''
  let assistantText = ''
  let assistantReasoning = ''
  let inputTokens = 0
  let outputTokens = 0

  // 运行开始事件
  runId = randomUUID()
  state.runId = runId
  emit('run.started', {
    event: 'run.started',
    run_id: runId,
    model: modelConfig.model,
    provider: modelConfig.provider,
  })

  try {
    logger.info('[v5-agent-run] starting v5-agent run for session %s', sessionId)

    const authenticatedUserId = socket.data?.user?.id == null ? undefined : String(socket.data.user.id)

    const result = await agent.run(
      {
        modelClient: null,
        model: modelConfig.model,
        workspace,
        sessionId,
        profile,
        input: inputText,
        userId: authenticatedUserId,
      },
      {
        cwd: workspace,
        workspaceRoot: workspace,
        workspaceId: workspace,
        userId: authenticatedUserId,
        sessionId,
        mcpServers: {}, // V5 通过 MCP 注册的工具已自动可用
        timeoutMs: 120_000,
        signal: abortController.signal,
      }
    )

    assistantText = result.output || ''
    assistantReasoning = result.reasoning || ''

    if (!assistantText.trim()) {
      const error = 'V5 agent returned an empty response.'
      logger.warn('[v5-agent-run] v5-agent returned empty output for session %s', sessionId)
      if (state.queue.length === 0) {
        try {
          updateSession(sessionId, {
            ended_at: Math.floor(Date.now() / 1000),
            end_reason: 'error',
          })
        } catch (err) {
          logger.warn(err, '[v5-agent-run] failed to write v5-agent error end marker for %s', sessionId)
        }
      }
      emit('run.failed', {
        event: 'run.failed',
        run_id: runId,
        error,
        queue_remaining: state.queue.length,
      })
      return
    }

    // 存储助手消息
    if (assistantText.trim()) {
      const assistantId = addMessage({
        session_id: sessionId,
        role: 'assistant',
        content: assistantText,
        timestamp: Math.floor(Date.now() / 1000),
        finish_reason: result.finishReason || null,
        reasoning: assistantReasoning || null,
        reasoning_content: assistantReasoning || null,
      })
      state.messages.push({
        id: assistantId || state.messages.length + 1,
        session_id: sessionId,
        role: 'assistant',
        content: assistantText,
        timestamp: Math.floor(Date.now() / 1000),
        finish_reason: result.finishReason || null,
        reasoning: assistantReasoning || null,
        reasoning_content: assistantReasoning || null,
      })
    }

    // 估算 token 使用量
    if (!inputTokens && !outputTokens) {
      const usage = estimateUsageTokensFromMessages([
        { role: 'user', content: inputText },
        { role: 'assistant', content: assistantText },
      ])
      inputTokens = usage.inputTokens
      outputTokens = usage.outputTokens
    }

    state.inputTokens = (state.inputTokens || 0) + inputTokens
    state.outputTokens = (state.outputTokens || 0) + outputTokens
    updateSessionStats(sessionId)

    if (state.queue.length === 0) {
      try {
        updateSession(sessionId, {
          ended_at: Math.floor(Date.now() / 1000),
          end_reason: 'complete',
        })
      } catch (err) {
        logger.warn(err, '[v5-agent-run] failed to write v5-agent session end marker for %s', sessionId)
      }
    }

    // 流式输出（模拟）
    emit('message.delta', {
      event: 'message.delta',
      run_id: runId,
      delta: assistantText,
    })

    emit('usage.updated', {
      event: 'usage.updated',
      run_id: runId,
      input_tokens: state.inputTokens || 0,
      output_tokens: state.outputTokens || 0,
      total_tokens: (state.inputTokens || 0) + (state.outputTokens || 0),
    })

    emit('run.completed', {
      event: 'run.completed',
      run_id: runId,
      output: assistantText,
      reasoning: assistantReasoning,
      context: result.context,
      usage: {
        input_tokens: inputTokens,
        output_tokens: outputTokens,
        total_tokens: inputTokens + outputTokens,
      },
      queue_remaining: state.queue.length,
    })

  } catch (err) {
    if (abortController.signal.aborted || isAbortError(err)) {
      logger.info('[v5-agent-run] v5-agent run aborted for session %s', sessionId)
      return
    }
    const error = err instanceof Error ? err.message : String(err)
    logger.warn(err, '[v5-agent-run] v5-agent run failed for session %s', sessionId)
    if (state.queue.length === 0) {
      try {
        updateSession(sessionId, {
          ended_at: Math.floor(Date.now() / 1000),
          end_reason: 'error',
        })
      } catch (updateErr) {
        logger.warn(updateErr, '[v5-agent-run] failed to write v5-agent error end marker for %s', sessionId)
      }
    }
    emit('run.failed', {
      event: 'run.failed',
      run_id: runId,
      error,
      queue_remaining: state.queue.length,
    })
  } finally {
    if (!abortController.signal.aborted || state.abortController === abortController) {
      state.isWorking = false
      state.isAborting = false
      state.runId = undefined
      state.abortController = undefined
      state.activeRunMarker = undefined
      state.responseRun = undefined
      state.profile = undefined
      state.events = []
      if (state.queue.length > 0) {
        dequeueNextQueuedRun(socket, sessionId, profile)
      }
    }
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === 'AbortError' || error.message === 'Run aborted.')
}

function appendStateEvent(state: SessionState, event: string, payload: any): void {
  if (!state.isWorking) return
  state.events.push({ event, data: payload })
  if (state.events.length > 200) state.events.splice(0, state.events.length - 200)
}