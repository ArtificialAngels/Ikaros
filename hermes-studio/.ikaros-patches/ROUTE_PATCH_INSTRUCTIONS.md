# V5 Agent 路由注册补丁说明

## 文件：packages/server/src/routes/hermes/chat-run.ts

在 `run` 事件处理器中添加 V5 分支：

### 原有代码位置（约 450-500 行）：

```typescript
socket.on('run', async (payload: unknown) => {
  const data = payload as RunRequest

  // 检查是否是 Ekko Agent
  if (data.coding_agent_id === 'ekko-agent' || data.agent_id === 'ekko-agent') {
    await handleEkkoAgentRun(nsp, socket, data, profile, sessionMap, dequeueNextQueuedRun)
    return
  }

  // 原有的 Hermes Chat 逻辑...
  await handleHermesChatRun(...)
})
```

### 添加 V5 分支后的代码：

```typescript
socket.on('run', async (payload: unknown) => {
  const data = payload as RunRequest

  // 检查是否是 V5 Agent（在 Ekko 之前检查）
  if (data.coding_agent_id === 'ikaros-v5' || data.agent_id === 'ikaros-v5') {
    await handleV5AgentRun(nsp, socket, data, profile, sessionMap, dequeueNextQueuedRun)
    return
  }

  // 检查是否是 Ekko Agent
  if (data.coding_agent_id === 'ekko-agent' || data.agent_id === 'ekko-agent') {
    await handleEkkoAgentRun(nsp, socket, data, profile, sessionMap, dequeueNextQueuedRun)
    return
  }

  // 原有的 Hermes Chat 逻辑...
  await handleHermesChatRun(...)
})
```

### 需要添加的导入：

```typescript
import { handleV5AgentRun } from '../../services/hermes/run-chat/handle-v5-agent-run'
```

## 文件：packages/server/src/index.ts

在导入部分添加 V5 关闭处理：

```typescript
import { shutdownV5AgentManager } from './services/v5-agent/manager'

// 在 shutdown 钩子中添加
process.on('beforeExit', () => {
  shutdownV5AgentManager()
})

process.on('SIGTERM', () => {
  shutdownV5AgentManager()
})

process.on('SIGINT', () => {
  shutdownV5AgentManager()
})
```