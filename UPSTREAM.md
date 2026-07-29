# 上游组件清单 (Upstream Manifest)

> **原则**: 本仓库（云端）只保留 **Ikaros 原生代码 + 配置 + 下列清单/下载/配置脚本**。
> 所有「有上游」的组件（neko 前端、hermes、runtime 工具链、各类 MCP）**不入库**，
> 统一由 `scripts/fetch-upstreams.py` 拉取、`scripts/setup-native.py` 落地配置。
>
> 拉取后这些目录由 `.gitignore` 排除，不会污染仓库。

## 分类图例
- **git** = 上游是 git 仓库，用 `git clone`（轻量）拉到本地落点，可 `git pull` 更新。
- **release** = 上游只发二进制/压缩包，用 `scripts/fetch-upstreams.py`（gopeed/aria2 + 镜像）下载解压。
- **patched** = 我们在上游基础上打了补丁（落地后需执行补丁步骤，见各条）。
- **ignored** = 已在 `.gitignore` 排除（不入库）。

---

## 1. N.E.K.O（桌宠前端 / 多形态 Avatar）
| 项 | 内容 |
|----|------|
| 上游 URL | `https://github.com/Project-N-E-K-O/N.E.K.O` |
| 许可 | Apache-2.0 |
| 获取方式 | git（轻量 `--depth 1 --filter=blob:none`）|
| 本地落点 | `core/neko/` |
| 状态 | 干净上游，本仓库**无本地补丁**（集成通过独立桥 `bin/ikaros-neko-bridge.py` + 启动脚本 `bin/neko-start.bat` 完成，不修改 neko 源码）|
| 是否入库 | **否**（`.gitignore`: `core/neko/`）|
| 我们的集成点 | `bin/neko-start.bat`(后端+桌面)、`bin/neko-stop.bat`、`core/dashboard/server.py`(组件登记)、`bin/ikaros-control-panel.bat`(复用其 electron 二进制)；人格同步已弃用(`v5-sync-persona.py` 删除,neko 原生读取 persona) |
| 环境变量 | `IKAROS_NEKO` / `IKAROS_NEKO_PYTHON` / `IKAROS_NEKO_SERVER`（`core/env/ikaros-paths.json` 同步）|

> 桌面壳 `N.E.K.O.exe` 与构建产物 `.venv`/`static`/`resources` 也一并排除（已在 `.gitignore`）。

---

## 2. Hermes Agent（Agent 核心 / CLI）
| 项 | 内容 |
|----|------|
| 上游 URL | `https://github.com/NousResearch/hermes-agent` |
| 获取方式 | git 或 release |
| 本地落点 | `hermes-agent/` |
| 状态 | **已在 `.gitignore` 排除**（从未入库）。`hermes-agent/config.yaml` 为本地生成、已忽略。|
| 是否入库 | **否** |
| 我们的集成点 | `config/hermes.yaml`(原生配置)、`bin/hermes_paw_bridge.py`(猫爪桥)、`bin/ikaros-soul-sync.py`(SOUL 生成,由 V5 每轮对话自同步重写 `SOUL.md`);独立 `:8642` 网关 `hermes-api-server.py` 已删除(其 `hermes` provider 现别名到 Dashboard) |

---

## 3. Hermes Web UI（Web 界面，可选）
| 项 | 内容 |
|----|------|
| 上游 URL | `https://github.com/EKKOLearnAI/hermes-web-ui` |
| 获取方式 | git |
| 本地落点 | `data/webui-new/app/`（`HERMES_WEB_UI_HOME` 隔离）|
| 状态 | 已在历史集成说明中记录（见 `docs/`）；`data/` 不入库。|
| 是否入库 | **否** |

---

## 4. runtime/（运行时依赖工具链，全上游）
`runtime/` 整体已在 `.gitignore` 排除。各子组件：

| 子目录 | 组件 | 上游 / 来源 | 类型 |
|--------|------|------------|------|
| `runtime/portable-python/` | Python 3.13 便携版 | python-build-standalone | release |
| `runtime/node/` | Node.js | nodejs.org | release |
| `runtime/rust/` | Rust 工具链 | rust-lang.org | release |
| `runtime/llama/` | llama.cpp (b10000-cuda) | `ggml-org/llama.cpp` | release |
| `runtime/gopeed/` | 下载器 | `GopeedOrg/gopeed` | release |
| `runtime/aria2/` | 下载兜底 | `aria2/aria2` | release |
| `runtime/qdrant/` | 向量库（可选）| `qdrant/qdrant` | release |
| `runtime/memos/` | 记忆服务（可选）| `usememos/memos` | release |
| `runtime/everything/` | Everything MCP | `voidtools/everything` | release |
| `runtime/MCPServe/` | 多个 MCP 服务 | 见下「MCP 清单」| git/release |
| `runtime/rpc-server/` | 本地 RPC | 上游/自研 | release |
| `runtime/storage/` | 本地存储桥 | 上游/自研 | release |

> 说明：`runtime/` 是**本地运行时目录**，不应入云端仓库。CI/新机器通过
> `scripts/fetch-upstreams.py` 按 `runtime/` 各组件清单重建。

---

## 5. MCP 清单（位于 `runtime/MCPServe/`）
| 服务 | 包 / 来源 | 版本 | 类型 |
|------|-----------|------|------|
| everything-mcp | `@danielsimonjr/everything-mcp` (npm) | 1.0.1 | npm |
| gitnexus | `gitnexus` (npm) | 1.7.0 | npm |
| context7 | `@upstash/context7-mcp` (npm) | 3.2.2 | npm |
| playwright | `@playwright/mcp` (npm) | 0.0.77 | npm |
| codebase-memory | `github.com/DeusData/codebase-memory-mcp` release | 0.8.1 | release(zip) |

> `codebase-memory` 的 `source_url` 与 `sha256` 见 `core/env/ikaros-paths.json` 的 `mcp.codebase-memory` 段。

---

## 6. 模型权重（多 GB，绝不入库）
| 模型 | 路径 | 来源 |
|------|------|------|
| Qwen3-1.7B (本地 LLM) | `core/v5/models/Qwen_Qwen3-1.7B-Q4_K_M.gguf` | HuggingFace `Qwen/Qwen3-1.7B` (GGUF) |
| nomic embed v2 MoE | `core/v5/models/nomic-embed-text-v2-moe.{f16,f32}.gguf` | HuggingFace `nomic-ai/nomic-embed-text-v2-moe` |
| 其它 `*.gguf` / `*.onnx_data` | 各处 | 见 `*.gguf` / `*.onnx_data` 全局忽略 |

> 下载统一走 `bin/ikaros-fastdl.py`（带 hf-mirror.com / ghproxy 镜像）。

---

## 拉取与配置流程
```bat
REM 1) 拉取所有上游（git clone + release 下载），幂等
python scripts/fetch-upstreams.py

REM 2) 落地原生配置（写 neko 环境变量/桥、生成 hermes config、校验 runtime exe）
python scripts/setup-native.py

REM 3) 启动（控制面板会按需拉起 embed :8587；:8080 懒加载）
bin/ikaros-control.bat
```

## 本地忽略汇总（`.gitignore` 已覆盖）
- `core/neko/`（整个上游桌宠树）
- `runtime/` / `hermes-agent/` / `data/`（运行时与生成物）
- `*.gguf` / `*.onnx_data`（模型权重）
- `*/target/`（cargo 构建产物，含 `core/env/*/target/`）
- `*.exe` 构建产物、`.venv`、`node_modules`、`__pycache__`、`*.pyc`
