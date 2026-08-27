# ikaros-memory-settings

dsh 设置页（Settings → 记忆系统）的 Ikaros 控制面板。

## 功能

- 服务状态：端口 / PID / 当前模型 / 向量数
- 启停 embedding：调 llama-server (bge-m3 q8_0) @ :8587
- 模型管理：扫描 `core/memory_v5/models/*.gguf`，切换当前模型（自动重启 llama）
- 下载模型：HuggingFace 直链 → 落 `core/memory_v5/models/`（gopeed/aria2c/curl 自适应）
- 向量重建：调 `v5_call.py rebuild` 重嵌 Chroma

## 安装

```bash
# 1) 编译
cd core/ikaros-dsh/plugins/ikaros-memory-settings
node scripts/build.mjs

# 2) 装配到 dsh profile
cd ~/.dsh/profiles/web
pnpm remove @ikaros/dsh-ikaros-memory-settings
pnpm add file:"E:/Ikaros/core/ikaros-dsh/plugins/ikaros-memory-settings"

# 3) 重启 dsh (manifest 缓存)
ikaros dsh restart
```

## 架构（仿造 `@deepseek-ai/dsh-client-ui-settings-models`）

```
core/ikaros-dsh/cordis.patch.yml 注册此插件 → dsh-client-modules 加载
  ↓
host 侧 (src/index.ts):
  ctx.effect 注入 'ikaros-memory-settings:host' cordis 服务
    含 listModels / getStatus / startEmbedding / stopEmbedding /
       switchModel / downloadModel / rebuildVectors 7 个 RPC
  ↓
client 侧 (src/client.tsx):
  ctx.slots.inject('settings.section', 注册 React 卡)
  inject: ({ api }) => ({ api, t })  ← 从 connection.api.ikarosMemory 拿 host-bridge
```

## 与 ikaros-memory 的关系

- **ikaros-memory**（自动记忆工程层）：turn-stopping 自动沉淀 + pre-step 召回注入 + compaction 捕获 + 6h maintenance tick
- **ikaros-memory-settings**（手动控制面板）：用户在 dsh 设置里可见、可控

两者共享同一套 v5_call.py 入口 + 同一套 llama-server，但作用域不同。