# Ikaros dsh 插件

Ikaros 工作引擎的两个独立 dsh (DeepSeek Harness) 插件，依据 dsh 插件标准开发。

## 插件列表

### 1. @ikaros/dsh-conversation-tree（对话树）

**路径**：`core/ikaros-dsh/plugins/ikaros-conversation-tree/`

**功能**：
- Node 侧：server.py 看门狗（探活/拉起/崩溃自动重启，动态端口）
- Node 侧：CT 端口文件 watch + client.js URL patch
- Client 侧：sidebar 入口按钮 + shell.overlay 全屏 iframe 面板
- Client 侧：dsh 设置面板 CT 6 分区（主题/权限/布局/粒子/语言），postMessage 与 iframe 双向同步

**依赖**：`core/conversation-tree/`（Python aiohttp 服务 + 单页 index.html）

### 2. @ikaros/dsh-ikaros-memory（记忆系统 v5）

**路径**：`core/ikaros-dsh/plugins/ikaros-memory/`

**功能**：
- Node 侧：自动记忆工程层（pre-step 召回注入 / turn-stopping 沉淀写回 / compaction 捕获 / 6h 维护循环）
- Node 侧：embedding 模型管理 HTTP RPC（:19001，启动/切换/下载/重建向量）
- Client 侧：dsh 设置面板记忆控制卡（embedding 模型管理 UI）
- bin/v5_call.py：Node ↔ Python 桥接（常驻 daemon + JSON 行协议）

**依赖**：`core/memory_v5/`（Python 记忆系统，SQLite + Chroma 向量存储）

## 安装

### 前置条件
- dsh (DeepSeek Harness) 已安装，`dsh` 命令在 PATH 中
- IKAROS_ROOT 环境变量指向 Ikaros 项目根目录
- 便携 Python：`$IKAROS_ROOT/runtime/portable-python/python.exe`

### 安装命令

```bash
# 安装对话树插件
dsh plugin --profile web add file:E:/Ikaros/core/ikaros-dsh/plugins/ikaros-conversation-tree

# 安装记忆系统插件
dsh plugin --profile web add file:E:/Ikaros/core/ikaros-dsh/plugins/ikaros-memory
```

> `dsh plugin --profile web add` 会转发到 profile 目录的 pnpm，等价于：
> `cd ~/.dsh/profiles/web && pnpm add file:<path>`

### 注册到 cordis.patch.yml

安装后需在 profile 的 `cordis.patch.yml` 中注册插件：

```yaml
- insert:
    # 对话树
    - id: ikaros-conversation-tree
      name: '@ikaros/dsh-conversation-tree'
      config:
        port: 48920
        python: !!js 'process.env.IKAROS_ROOT + "/runtime/portable-python/python.exe"'
        serverPath: !!js 'process.env.IKAROS_ROOT + "/core/conversation-tree/server.py"'
        probeTimeoutMs: 3000

    # 记忆系统
    - id: ikaros-memory
      name: '@ikaros/dsh-ikaros-memory'
      config: {}
```

### 重启 dsh

```bash
dsh --profile web restart
# 或通过 Ikaros 启动器
ikaros dsh restart
```

## 构建

```bash
# 构建对话树插件
cd core/ikaros-dsh/plugins/ikaros-conversation-tree
npm run build

# 构建记忆系统插件
cd core/ikaros-dsh/plugins/ikaros-memory
npm run build
```

构建产物在各插件的 `dist/` 目录：
- `dist/index.js` — Node 侧（cordis 插件入口）
- `dist/client.js` — Client 侧（`window.__ModuleLoader__.load` 格式，dsh client-modules 协议）

## 架构说明

### 插件标准遵循
- **Node 侧**：`export function apply(ctx, config)` — cordis 插件标准入口
- **Client 侧**：`export const inject = ['slots']` + `export function apply(ctx)` — dsh client 插件标准
- **Client bundle**：esbuild 打包为 `window.__ModuleLoader__.load({id, factory})` 格式
- **配置**：`package.json` 的 `dsh.client` 字段声明平台和注入点
- **外部依赖**：react / cordis / dsh-client-* 在 bundle 时标记为 external，运行时由 dsh 提供

### 与 Ikaros 核心的关系
- 插件是 dsh 进程内的 Node/Client 代码
- Python 核心（conversation-tree / memory_v5）通过子进程 / HTTP / MCP 与插件通信
- 路径全部通过 `IKAROS_ROOT` 环境变量推导，无硬编码盘符
