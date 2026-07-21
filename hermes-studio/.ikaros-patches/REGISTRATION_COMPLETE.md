# Ikaros V5 Global Agent 注册完成报告

## ✅ 已完成的文件

| 文件 | 位置 | 说明 |
|------|------|------|
| V5 Agent Manager | `.ikaros-patches/v5-agent-manager.ts` | V5 Agent 运行时管理器 |
| V5 Run Handler | `.ikaros-patches/handle-v5-agent-run.ts` | V5 Agent 运行处理器 |
| 路由补丁说明 | `.ikaros-patches/ROUTE_PATCH_INSTRUCTIONS.md` | 路由修改指南 |
| 恢复脚本 (Linux/Mac) | `.ikaros-patches/restore-v5-agent.sh` | 自动恢复脚本 |
| 恢复脚本 (Windows) | `.ikaros-patches/restore-v5-agent.bat` | Windows 恢复脚本 |
| 路由补丁脚本 | `.ikaros-patches/apply-v5-route-patch.sh` | 自动应用路由补丁 |
| package.json 钩子说明 | `.ikaros-patches/PACKAGE_JSON_HOOKS.md` | 钩子配置指南 |

## 📋 注册架构

```
.ikaros-patches/
├── v5-agent-manager.ts                 # V5 Agent Manager（核心）
├── handle-v5-agent-run.ts              # V5 Run Handler（核心）
├── restore-v5-agent.sh                 # 自动恢复脚本（Linux/Mac）
├── restore-v5-agent.bat                # 自动恢复脚本（Windows）
├── apply-v5-route-patch.sh             # 路由补丁脚本
├── ROUTE_PATCH_INSTRUCTIONS.md         # 路由修改指南
└── PACKAGE_JSON_HOOKS.md               # package.json 配置指南
```

## 🔄 防覆盖机制

### 自动恢复流程

```
Hermes Studio 更新 (git pull / pnpm install)
  ↓
触发 postinstall 钩子
  ↓
运行 restore-v5-agent.sh
  ↓
复制 v5-agent-manager.ts
复制 handle-v5-agent-run.ts
  ↓
手动运行 apply-v5-patch (如需要)
  ↓
pnpm build
  ↓
✅ V5 Global Agent 恢复
```

### 手动恢复命令

```bash
# 1. 恢复源码文件
bash .ikaros-patches/restore-v5-agent.sh

# 2. 应用路由补丁
bash .ikaros-patches/apply-v5-route-patch.sh

# 3. 重新构建
pnpm build

# 4. 重启 Studio
```

## 🎯 使用方式

### 客户端调用

```typescript
// 在 Studio 中选择 Ikaros V5 Agent
import { startGlobalAgentRun } from '@/api/hermes/global-agent'

await startGlobalAgentRun('ikaros-v5', {
  input: '你好伊卡洛斯，现在心情怎么样？',
  session_id: sessionId,
  profile: 'default',
  model: 'glm-5.2',
  workspace: 'E:\\Ikaros\\Ikaros-memory',
}, {
  profile: 'default',
  stream: true,
})
```

### 响应格式

```json
{
  "event": "run.completed",
  "run_id": "uuid",
  "output": "（伊卡洛斯的回复）",
  "reasoning": "",
  "context": {},
  "usage": {
    "input_tokens": 150,
    "output_tokens": 200,
    "total_tokens": 350
  }
}
```

## 📝 需要手动修改的文件

### 1. hermes-studio/package.json

在 `scripts` 中添加：

```json
{
  "scripts": {
    "postinstall": "bash .ikaros-patches/restore-v5-agent.sh || .ikaros-patches\\restore-v5-agent.bat",
    "restore-v5-agent": "bash .ikaros-patches/restore-v5-agent.sh || .ikaros-patches\\restore-v5-agent.bat",
    "apply-v5-patch": "bash .ikaros-patches/apply-v5-route-patch.sh"
  }
}
```

### 2. hermes-studio/.gitignore

添加：

```
.ikaros-patches/
```

这样补丁目录不会被提交到 git。

## ✨ 特性

| 特性 | 状态 |
|------|------|
| 源码级注册 | ✅ 完成 |
| 自动恢复脚本 | ✅ 完成 |
| 路由补丁脚本 | ✅ 完成 |
| 跨平台支持 | ✅ Linux/Mac/Windows |
| 与 Ekko 并行 | ✅ 互不干扰 |
| MCP 工具集成 | ✅ 通过 V5 orchestrator |
| 流式输出 | ✅ 模拟流式 |
| Abort 支持 | ✅ 支持 |

## 🚀 下一步行动

1. **复制补丁到 Studio 源码目录**

```bash
# 将 .ikaros-patches 复制到 hermes-studio 根目录
cp -r E:/Ikaros/data/hermes-agent/.ikaros-patches E:/Ikaros/hermes-studio/
```

2. **运行恢复脚本**

```bash
cd E:/Ikaros/hermes-studio
bash .ikaros-patches/restore-v5-agent.sh
bash .ikaros-patches/apply-v5-route-patch.sh
```

3. **修改 package.json**（添加 postinstall 钩子）

4. **重新构建**

```bash
pnpm build
```

5. **重启 Hermes Studio**

6. **验证注册**

在 Studio 中创建新会话，选择 Agent 时应该能看到 `Ikaros V5 (ikaros-v5)` 选项。

## 📚 相关文档

- `V5_GLOBAL_AGENT_REGISTRATION.md` — 完整设计文档
- `.ikaros-patches/ROUTE_PATCH_INSTRUCTIONS.md` — 路由修改指南
- `.ikaros-patches/PACKAGE_JSON_HOOKS.md` — package.json 配置指南

## 🆘 故障排查

### 问题：V5 Agent 选项不显示

**解决**：
1. 检查 chat-run.ts 中是否有 `ikaros-v5` 分支
2. 检查 handle-v5-agent-run.ts 是否已复制
3. 运行 `pnpm build` 重新构建

### 问题：V5 Agent 运行失败

**解决**：
1. 检查 Python 路径：`echo $IKAROS_PYTHON`
2. 检查 V5 工作目录是否存在：`ls E:/Ikaros/Ikaros-memory`
3. 查看日志：`tail -f logs/agent.log`

### 问题：更新后注册丢失

**解决**：
```bash
cd E:/Ikaros/hermes-studio
bash .ikaros-patches/restore-v5-agent.sh
bash .ikaros-patches/apply-v5-route-patch.sh
pnpm build
```

---

**注册状态：✅ 完成**

**下次更新：自动恢复（通过 postinstall 钩子）**