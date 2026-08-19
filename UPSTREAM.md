# 上游组件清单 (Upstream Manifest)

> **原则**: 本仓库（云端）只保留 **Ikaros 原生代码 + 配置 + 下列清单/下载/配置脚本**。
> 所有「有上游」的组件（runtime 工具链、各类 MCP、模型权重）**不入库**，
> 统一由 `scripts/fetch-upstreams.py` 拉取、`scripts/setup-native.py` 落地配置。
>
> 拉取后这些目录由 `.gitignore` 排除，不会污染仓库。
>
> **2026-08-18**: N.E.K.O 桌宠与 hermes-agent / hermes-web-ui 上游已随底座退役移除；
> 工作引擎改为 **dsh (deepseek-harness)**，其运行时位于 `runtime/dsh/`（npm 本地安装，不入库）。

## 分类图例
- **git** = 上游是 git 仓库，用 `git clone`（轻量）拉到本地落点，可 `git pull` 更新。
- **release** = 上游只发二进制/压缩包，用 `scripts/fetch-upstreams.py`（gopeed/aria2 + 镜像）下载解压。
- **patched** = 我们在上游基础上打了补丁（落地后需执行补丁步骤，见各条）。
- **ignored** = 已在 `.gitignore` 排除（不入库）。

---

## 1. dsh — DeepSeek Harness（工作引擎）
| 项 | 内容 |
|----|------|
| 上游 | `@deepseek-ai/dsh` (npm) |
| 获取方式 | npm（本地安装到 `runtime/dsh/`）|
| 本地落点 | `runtime/dsh/node_modules/@deepseek-ai/dsh/` |
| 状态 | 独立 profile：`data/dsh/profiles/`；Ikaros overlay：`core/ikaros-dsh/cordis.patch.yml`（memory_v5 MCP + terminal + lsp + persona，路径经 `!!js process.env.IKAROS_ROOT` 推导，0 硬编码）|
| 是否入库 | **否**（`runtime/` 整体忽略）|
| 启动 | `bin/start-dsh-ikaros.bat web`（web :3080）/ `headless`；`bin/restart-dsh-ikaros.ps1` |
| 环境变量 | `IKAROS_DSH` / `IKAROS_DSH_SOURCE` / `IKAROS_DSH_PROFILE` / `IKAROS_DSH_WEB_PORT` / `IKAROS_DSH_OVERLAY`（`bin/ikaros-env.*`）|

---

## 2. runtime/（运行时依赖工具链，全上游）
`runtime/` 整体已在 `.gitignore` 排除。各子组件：

| 子目录 | 组件 | 上游 / 来源 | 类型 |
|--------|------|------------|------|
| `runtime/portable-python/` | Python 3.13 便携版 | python-build-standalone | release |
| `runtime/node/` | Node.js | nodejs.org | release |
| `runtime/dsh/` | DeepSeek Harness (npm) | `@deepseek-ai/dsh` | npm |
| `runtime/deepseek-harness-master/` | dsh 源码参考树 | GitHub zip | release |
| `runtime/rust/` | Rust 工具链 | rust-lang.org | release |
| `runtime/llama/` | llama.cpp (b10000-cuda) | `ggml-org/llama.cpp` | release |
| `runtime/gopeed/` | 下载器 | `GopeedOrg/gopeed` | release |
| `runtime/aria2/` | 下载兜底 | `aria2/aria2` | release |
| `runtime/memos/` | 记忆服务（可选）| `usememos/memos` | release |
| `runtime/everything/` | Everything MCP | `voidtools/everything` | release |
| `runtime/MCPServe/` | 多个 MCP 服务 | 见下「MCP 清单」| git/release |
| `runtime/rpc-server/` | 本地 RPC | 上游/自研 | release |
| `runtime/storage/` | 本地存储桥 | 上游/自研 | release |

> 说明：`runtime/` 是**本地运行时目录**，不应入云端仓库。CI/新机器通过
> `scripts/fetch-upstreams.py` 按 `runtime/` 各组件清单重建。

---

## 3. MCP 清单（位于 `runtime/MCPServe/`）
| 服务 | 包 / 来源 | 版本 | 类型 |
|------|-----------|------|------|
| everything-mcp | `@danielsimonjr/everything-mcp` (npm) | 1.0.1 | npm |
| gitnexus | `gitnexus` (npm) | 1.7.0 | npm |
| context7 | `@upstash/context7-mcp` (npm) | 3.2.2 | npm |
| playwright | `@playwright/mcp` (npm) | 0.0.77 | npm |
| codebase-memory | `github.com/DeusData/codebase-memory-mcp` release | 0.8.1 | release(zip) |

> `codebase-memory` 的 `source_url` 与 `sha256` 见 `core/env/ikaros-paths.json` 的 `mcp.codebase-memory` 段。

---

## 4. 模型权重（多 GB，绝不入库）
| 模型 | 路径 | 来源 |
|------|------|------|
| nomic-embed-text-v1.5 (embedding 权威) | `core/memory_v5/models/nomic-embed-text-v1.5.Q8_0.gguf` | HuggingFace `nomic-ai/nomic-embed-text-v1.5-GGUF` |
| Phi-4-mini (本地 LLM) | `core/memory_v5/models/Phi-4-mini-instruct-Q4_K_M.gguf` | HuggingFace `microsoft/Phi-4-mini-instruct-gguf` |
| 其它 `*.gguf` / `*.onnx_data` | 各处 | 见 `*.gguf` / `*.onnx_data` 全局忽略 |

> 下载统一走 WorkBuddy 技能 `ikaros-fastdl`（底层 gopeed/aria2 + hf-mirror.com / ghproxy 镜像），由 `scripts/fetch-upstreams.py` 解析调用。

---

## 拉取与配置流程
```bat
REM 1) 拉取所有上游（release 下载 + npm），幂等
python scripts/fetch-upstreams.py

REM 2) 落地原生配置（校验 runtime exe、写 ikaros-paths.json、dsh profile env 参考）
python scripts/setup-native.py

REM 3) 启动（控制面板会按需拉起 embed :8587；:8080 懒加载；dsh web :3080 可手动启动）
bin/ikaros-control-panel.bat
```

## 本地忽略汇总（`.gitignore` 已覆盖）
- `runtime/`（运行时与生成物）
- `data/`（生成物与用户数据）
- `*.gguf` / `*.onnx_data`（模型权重）
- `*/target/`（cargo 构建产物，含 `core/env/*/target/`）
- `*.exe` 构建产物、`.venv`、`node_modules`、`__pycache__`、`*.pyc`
