# Ikaros Runtime Dependencies

`runtime/` 目录（约 17GB）不纳入 Git 版本控制。全新克隆后需按本清单下载并放置各组件。

## 目录结构

```
runtime/
├── portable-python/      # Python 3.12.10 (主运行时, ~6.3GB 含 site-packages)
├── node/                 # Node.js v26.3.0 (~888MB)
├── git/                  # Git for Windows 2.54.0 (~391MB)
├── rust/                 # Rust toolchain 1.96.1 (~635MB)
├── llama/                # llama.cpp b10000 + CUDA DLLs (~2.9GB)
├── dsh/                  # DeepSeek Harness (@deepseek-ai/dsh 0.1.1-rc.2, ~194MB)
├── deepseek-harness-master/  # dsh 源码仓库 (~815MB, 开发用)
├── MCPServe/             # MCP 服务集合 (~4.3GB)
│   ├── gitnexus/         #   GitNexus MCP (~2.65GB)
│   ├── codebase-memory/  #   代码库记忆 MCP (~1.5GB)
│   ├── context7/         #   Context7 文档 MCP (~20MB)
│   ├── playwright/       #   Playwright 浏览器 MCP (~17MB)
│   ├── graphify/         #   代码图谱 MCP (~21MB)
│   ├── nx-master/        #   Nx MCP (~107MB)
│   ├── best-cad-mcp-master/  # CAD MCP (~2MB)
│   └── blender-mcp-main/ # Blender MCP (~1MB)
├── memos/                # Memos 笔记服务 (~72MB)
├── gopeed/               # Gopeed 下载管理器 (~89MB)
├── aria2/                # aria2 1.37.0 命令行下载 (~5MB)
├── everything/           # Everything 搜索工具 (~0.2MB)
├── rpc-server/           # Ikaros RPC 服务 (~0.1MB)
└── storage/              # 运行时存储
```

## 下载来源

| 组件 | 版本 | 来源 |
|---|---|---|
| Python (embeddable) | 3.12.10 | https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip |
| Node.js | 26.3.0 | https://nodejs.org/dist/v26.3.0/node-v26.3.0-win-x64.zip |
| Git for Windows | 2.54.0 | https://github.com/git-for-windows/git/releases |
| Rust | 1.96.1 | https://rustup.rs / https://forge.rust-lang.org/infra/other-installation-methods.html |
| llama.cpp | b10000 (47a39665e) | https://github.com/ggml-org/llama.cpp/releases |
| dsh | 0.1.1-rc.2 | `npm install @deepseek-ai/dsh@0.1.1-rc.2` |
| dsh 源码 | master | https://github.com/deepseek-ai/deepseek-harness |
| aria2 | 1.37.0 | https://github.com/aria2/aria2/releases |
| Memos | latest | https://github.com/usememos/memos/releases |
| Gopeed | latest | https://github.com/GopeedLab/gopeed/releases |
| Everything | latest | https://www.voidtools.com/ |

## Python 依赖

主 Python (`portable-python/`) 的 site-packages 包含 Ikaros 全部 Python 依赖。全新安装后执行：

```bat
runtime\portable-python\python.exe -m pip install -r requirements.txt
```

核心依赖包括：`chromadb`, `openai`, `httpx`, `fastapi`, `uvicorn`, `pydantic`, `numpy`, `sentence-transformers`, `ezdxf` 等。

## MCP 服务

MCPServe/ 下各服务需独立安装。dsh 的 `cordis.patch.yml` 中注册了各 MCP 的启动命令。关键服务：

- **gitnexus**: Git 知识图谱 MCP（Node.js，需 `pnpm install`）
- **codebase-memory**: 代码库向量记忆 MCP（含 embedding 模型）
- **context7**: 在线文档检索 MCP
- **playwright**: 浏览器自动化 MCP

## 注意事项

1. `runtime/` 总计约 17GB，确保磁盘有足够空间
2. `portable-python/` 含完整 site-packages（约 6GB），非裸 embeddable 包
3. `llama/` 含 CUDA 运行时 DLL，GPU 推理必需
4. `MCPServe/gitnexus/` 和 `codebase-memory/` 体积较大，含 node_modules 和模型文件
5. 所有路径在 `bin/ikaros-env.ps1` 中统一配置
