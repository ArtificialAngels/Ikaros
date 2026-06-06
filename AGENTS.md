# Hermes Agent — Project Memory Bank

> **Read this first** when picking up the project after a break.
> This file captures the project state, architecture, modification history,
> debugging tips, and the gotchas we hit along the way.

---

## 1. What This Is

A **portable, USB-drive-deployable Hermes Agent** — a hybrid LLM (cloud + local)
with a modern chat UI, designed to run on any Windows PC with zero install.

**One-click UX:** `bin\hermes-all.bat` → browser opens → chat ready.

---

## 2. Architecture

Three processes, each with a single responsibility:

| Port  | Process                | Role                                                           |
|-------|------------------------|----------------------------------------------------------------|
| :8080 | **llama-server**       | LLM engine. OpenAI-compatible HTTP API. Internal — not exposed. |
| :7860 | **Hermes FastAPI**     | Memory + knowledge base + RAG embeddings shim.                  |
| :7870 | **Open WebUI**         | Main chat UI (Vue 3). Browser opens here.                      |

**Data flow:**
```
Browser → :7870 Open WebUI → :8080 llama-server (chat)
                          → :7860 Hermes FastAPI (embeddings/RAG)
```

llama-server only loads **one model at a time** (Open WebUI shows whichever
model llama-server exposes via `--alias`). See §6 for multi-model options.

---

## 3. Project Layout

```
E:\Hermes Agent\
├── .env                          # runtime env vars (API keys, paths)
├── AGENTS.md                     # THIS FILE
├── README.md                     # user-facing docs
├── config\
│   └── hermes.yaml               # main config (LLM providers, memory, KB)
├── hermes\                       # Python package (the agent)
│   ├── __init__.py
│   ├── __main__.py               # `python -m hermes serve` / chat
│   ├── agent.py                  # HermesAgent class
│   ├── config.py                 # config loader (env-aware)
│   ├── llm.py                    # LLM router + OpenAI/Anthropic/MiniMax providers
│   ├── memory.py                 # JSONL memory store + embedder
│   ├── knowledge.py              # markdown KB with chunking
│   ├── skills.py                 # skill registry (time/calc/echo/...)
│   ├── server.py                 # FastAPI: /v1/embeddings, /v1/models, /api/*
│   ├── gpu.py                    # GPU detection (nvidia-smi / Vulkan / WMI)
│   ├── web_dist\                 # legacy React admin SPA (deprecated, see §10)
│   └── scripts\                  # utility scripts (production)
│       ├── bootstrap_openwebui.py   # auto signup + add model on first run
│       ├── import_ollama_blobs.py   # Ollama blob → GGUF converter
│       └── reset_password.py        # admin password reset
├── portable-python\              # embedded Python 3.12.10 + pip deps
│   └── python.exe
├── runtime\                      # llama.cpp binaries
│   ├── llama-server.exe          # CPU
│   ├── llama-server-cuda-12.4.exe  # NVIDIA RTX 20/30/40/50 (driver >= 525)
│   ├── llama-server-cuda-11.8.exe  # older NVIDIA (GTX 900 / old driver)
│   ├── llama-server-vulkan.exe   # AMD / Intel / NVIDIA fallback
│   ├── aria2c.exe                # multi-thread downloader
│   └── *.dll                     # runtime DLLs (cudart, vulkan, etc.)
├── data\
│   ├── models\                   # GGUF files
│   │   ├── Qwen2.5-3B-Instruct-Q4_K_M.gguf   (default)
│   │   ├── Qwen2.5-7B-Instruct-Q4_K_M.gguf
│   │   ├── Qwen1.5-1.8B-Chat-Q4_K_M.gguf
│   │   └── f5ee307a2982.gguf     (Ollama-imported qwen3, 22.8GB)
│   ├── memory\                   # JSONL memory store
│   ├── knowledge\                # markdown KB source
│   ├── openwebui\                # Open WebUI SQLite DB + .webui_secret_key
│   └── logs\                     # hermes.log only (cleaned periodically)
├── bin\                          # user-facing launchers (CRLF line endings!)
│   ├── hermes-all.bat            # ★ MAIN: one-click everything
│   ├── hermes.bat                # CLI agent (`hermes chat`)
│   ├── hermes-web.bat            # just Hermes FastAPI (legacy)
│   ├── hermes-stop.bat           # kill all Hermes processes
│   ├── start-llm.bat             # just llama-server (CPU default)
│   ├── start-llm-smart.bat       # ★ llama-server with auto NGL
│   ├── start-openwebui.bat       # just Open WebUI
│   ├── setup-runtime.bat         # download ALL llama.cpp variants + aria2
│   └── setup-memos.bat           # download memos binary (Linux-only currently)
├── tests\                        # functional test scripts (kept clean)
│   ├── test_hermes.py            # 14-test E2E suite (mock LLM, no GPU needed)
│   └── verify_smart_ngl.py       # verify NGL calculation logic
├── scripts\                      # legacy one-off scripts
├── docker\                       # Docker configs
└── requirements.txt
```

---

## 4. Components

### hermes/server.py
- FastAPI app
- Key endpoints: `/health` (JSON status), `/v1/embeddings`, `/v1/models`, `/api/chat`,
  `/api/sessions`, `/api/memory`, `/api/skills`, `/api/task` (autonomous plan-execute)
- **Hash-based embeddings** at `/v1/embeddings` — used by Open WebUI RAG
  (search quality is poor but it boots without a real embedding model)
- **Autonomous task API** at `/api/task` — POST `{goal, wait}` triggers the Planner
  (sync returns full result, async returns `task_id` to poll at `GET /api/task/{id}`)

### hermes/llm.py
- `LLMRouter` with fallback chain
- Providers: `OpenAIProvider` (covers OpenAI, llama-server, MiniMax via
  OpenAI-compat), `AnthropicProvider`, `MockProvider`
- MiniMax config in `hermes.yaml` is `provider: openai` with MiniMax base URL

### Open WebUI (0.9.6)
- Sits at :7870
- Talks to llama-server at :8080 (chat) + Hermes at :7860 (embeddings)
- **First-run bootstrap** auto-creates admin + adds default model
  (see `hermes/scripts/bootstrap_openwebui.py`)
- Admin creds (default): `admin@hermes.local` / `hermes123`

### llama-server (b9503)
- `--alias qwen2.5-3b-instruct` makes the model id clean (default
  returns filename like `Qwen2.5-3B-Instruct-Q4_K_M.gguf`)
- `--n-gpu-layers N` controls GPU offload: 0=CPU, 99=full GPU, N=hybrid
- See `bin\start-llm-smart.bat` for auto NGL calculation

---

## 5. Key Decisions & Why

| Decision                                 | Why                                                         |
|------------------------------------------|-------------------------------------------------------------|
| Open WebUI as main UI                    | Mature, feature-rich, better than llama-server's plain HTML |
| `--alias qwen2.5-3b-instruct`            | Avoid filename-based model id mismatch with Open WebUI       |
| Hash-based embeddings (RAG shim)         | Avoid downloading 100MB+ embedding model just to boot OW   |
| One launcher `hermes-all.bat`            | User experience: one double-click = everything             |
| Auto-bootstrap admin on first run        | Avoid manual signup step for first-time users              |
| Disable Ollama (`ENABLE_OLLAMA_API=false`) | Prevent OW from auto-detecting user's system Ollama       |
| Smart NGL (auto offload calculation)      | Support loading models larger than VRAM (e.g. 22GB on 8GB) |
| Bundle all llama.cpp variants             | Portable — works on any GPU (NVIDIA/AMD/Intel)             |
| Skip CPU when VRAM full of weights       | Hybrid offload with <5 layers = full CPU is faster         |
| CRLF line endings for all .bat files      | cmd.exe does NOT parse LF-only files (bug: truncates paths)  |

---

## 6. Multi-Model Loading

llama-server is **single-model per process**. Three options:

1. **Switch model** — kill llama-server, restart with different `--model`:
   ```bat
   set MODEL=E:/Hermes Agent/data/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf
   bin\hermes-all.bat
   ```

2. **Multiple llama-server instances** on different ports (8080, 8081, 8082),
   add each as separate OpenAI endpoint in Open WebUI's `Connections`.
   Resource-hungry but lets you hot-swap.

3. **Ollama-compatible import** — run `python hermes/scripts/import_ollama_blobs.py`
   to convert Ollama `sha256-XXXXX` blobs to `.gguf` files in `data\models\`.

For the 22.8GB `f5ee307a2982.gguf` (qwen3) on 8GB VRAM, smart NGL
calculates ~16 layers on GPU + rest on CPU. Works, but slow.

---

## 7. Common Gotchas (READ THIS BEFORE EDITING!)

### Windows / cmd.exe
- **CRLF for .bat files!** LF-only → cmd can't parse → paths with spaces
  get truncated, scripts fail silently. **Always run:**
  ```powershell
  $c = Get-Content file.bat -Raw
  [System.IO.File]::WriteAllText(file.bat, $c -replace "`r`n","`n" -replace "`n","`r`n", [System.Text.UTF8Encoding]::new($false))
  ```
  After every bat edit, verify: `CR=NN, LF=NN` (must be equal).

- **`cmd /c "path with space"`** — truncates at the space. Workarounds:
  - `cmd /c "bat.bat" arg` (bat is relative, run from its dir)
  - Wrap the whole command in outer quotes
  - Or invoke from a wrapper bat

- **`for /f "tokens=*" %%V in ('cmd with --flag=value,flag2')`** — the comma
  breaks the parser. Use `usebackq` + backticks:
  ```bat
  for /f "usebackq tokens=*" %%V in (`cmd --flag=value`) do ...
  ```
  Or wrap in PowerShell to avoid cmd parsing entirely.

- **`set /a` is 32-bit signed integer.** For files > 2GB, use PowerShell:
  ```bat
  for /f "tokens=*" %%S in (`powershell -NoProfile -Command "$f=(Get-Item -LiteralPath '%FILE%').Length; [int][math]::Floor($f/1MB)"`) do set "MB=%%S"
  ```

### Open WebUI
- **`WEBUI_AUTH=false` blocks startup if users exist** (security guard).
  Just don't set it — auth is fine, OW shows signup form for first user.

- **First user signup auto-becomes admin** via `ENABLE_INITIAL_ADMIN_SIGNUP=true`.

- **Email must have valid format** (`user@domain.tld`). `user@local` is rejected.

- **OW defaults to `ENABLE_OLLAMA_API=true`** → auto-detects system Ollama.
  Set to `false` to force OW to use only our llama-server.

- **OW's bootstrap.log** at `hermes\data\logs\bootstrap.log` shows if admin
  creation / model add succeeded.

### llama-server
- **Model id from `/v1/models` defaults to the filename** (ugly).
  Use `--alias clean-name` to override.

- **Single model per process.** To switch, restart with different `--model`.

### hermes (Python)
- **Config env expansion**: `os.path.expandvars` doesn't support bash
  `${VAR:-default}` syntax. `hermes/config.py` has custom regex
  `_ENV_VAR_RE` for this — don't replace with plain `expandvars`.

- **`.env` loading**: config.py searches in cwd, parent, then `hermes/`
  package parent dir (absolute path). Works regardless of cwd.

---

## 8. Modification Log (chronological)

| When        | Change                                                              |
|-------------|---------------------------------------------------------------------|
| Day 1       | Built hermes package: config, llm router, memory, KB, skills, server |
| Day 1       | Embedded portable-python 3.12.10 + llama.cpp CPU b9503 + 3 GGUF     |
| Day 1       | Built React admin SPA → `hermes/web_dist` (now deprecated)           |
| Day 1       | Wrote `bin\hermes-all.bat` v1 (vulnerable to path issues)            |
| Day 2       | Fixed hermes-all.bat CRLF issue (was LF-only, paths truncated)        |
| Day 2       | Added `--alias qwen2.5-3b-instruct` to llama-server                  |
| Day 2       | Integrated Open WebUI 0.9.6: install + bootstrap + config            |
| Day 2       | Added `/v1/embeddings` shim in hermes/server.py (hash vectors)        |
| Day 2       | Discovered: OW shows system Ollama models (we want only ours)        |
| Day 3       | Fixed: `ENABLE_OLLAMA_API=false` in hermes-all.bat + start-openwebui.bat |
| Day 3       | Created `hermes/scripts/bootstrap_openwebui.py` (auto signup + add model) |
| Day 3       | Created `setup-runtime.bat` (download ALL llama.cpp variants, aria2 16-thread) |
| Day 3       | Added `start-llm-smart.bat` (auto NGL based on model size + VRAM)    |
| Day 3       | Imported user's Ollama blob (qwen3 22.8GB) → `f5ee307a2982.gguf`    |
| Day 3       | Project cleanup: trashed 1-off test scripts, kept 2 functional tests |
| Day 3       | THIS FILE created                                                 |
| 2026-06-06  | **llama.cpp b9503 → b9538 upgrade** (Qwen3 MoE / Qwen3.5 MoE support) |
| 2026-06-06  | Cleaned 22.29GB `Qwen3.6.incompatible-b9503.gguf` (model+llama.cpp upgrade obsoletes) |
| 2026-06-06  | Trashed `hermes/web_dist/` (React admin SPA) — server.py uses HTML_FALLBACK now |
| 2026-06-06  | Trashed `scripts/` (6/3 legacy) — bin/ replaced all |
| 2026-06-06  | Switched default model: `Qwen2.5-7B-Instruct` → `Qwen3.5-35B-A3B-Q4_K_M` (20.5GB MoE) |
| 2026-06-06  | Verified Qwen3.5-35B-A3B works: n_params=34.66B, n_ctx_train=262144, chat "Hello! How can I assist you today?" OK |
| 2026-06-06  | Studied ComfyUI-aki-v3 for inspiration (see §13 Roadmap below)        |
| 2026-06-06  | **A**: `bin/hermes-models.py` CLI 多模型切换器 (list/switch/download/gopeed) — parses GGUF v3 header (arch, ctx_len, n_tensors) |
| 2026-06-06  | **A**: `hermes/gguf.py` module — extracted GGUF v2/v3 header parser, reused by CLI + web UI |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher` page — web UI for model switching (replaces deprecated `web_dist/` admin SPA) |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/switch` (POST) — async subprocess runs `switch-model.bat`, returns when done |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/download` (POST) — creates gopeed-web task via Python communication bridge |
| 2026-06-06  | **A**: Integrated `gopeed-web` (89MB single exe) into `runtime/gopeed-web.exe` as the Python communication bridge for downloads |
| 2026-06-06  | **B**: `hermes/firstrun.py` + `bin/hermes-firstrun.bat` — detects NVIDIA/AMD/Vulkan, downloads cudart via gopeed-web if missing, graceful CPU fallback |
| 2026-06-06  | **B**: `hermes-firstrun.bat` wired into `hermes-all.bat` as Step 0 (idempotent check, doesn't block startup) |
| 2026-06-06  | **C**: `hermes/doctor.py` + `bin/hermes-doctor.bat` — 8-section health report (runtime, models, GPU, services, gopeed, python, disk, env) |
| 2026-06-06  | **D**: `hermes/gopeed_client.py` — gopeed-web API client (urllib only, no deps). gopeed-web API differs from desktop gopeed (POST body wrapped in `req`, response `data` is task_id string, opts at `meta.opts`) |
| 2026-06-06  | Memory: 120s bash timeout, gopeed+file-lock download check, gopeed-web API quirks, GGUF v3 type table |
| 2026-06-06  | **CRITICAL BUGFIX**: `hermes-stop.bat` v1 used `taskkill /IM llama-server.exe` literal — but the actual binary is `llama-server-cuda-12.4.exe`. Old stop left **stale llama-server processes holding VRAM** (one PID survived 20+ hours, working set -1140MB = leaked kernel handles). v2 fix: use `llama-server*` wildcard + PowerShell-based kill for clean output. |
| 2026-06-06  | Also fixed: all `bin\*.bat` files were **LF-only** (Edit tool had stripped CRs), causing cmd.exe to mis-parse multi-line `powershell -Command` blocks (visible as random "X 不是内部或外部命令" noise). Restored CRLF on all 9 bat files. |

---

## 9. Setup Flow (clean install from scratch)

```bash
# 1. Download all llama.cpp variants + aria2 (one-time, ~280MB)
bin\setup-runtime.bat

# 2. Run it
bin\hermes-all.bat
# → browser opens at :7870
# → first time: signup form → create admin → chat
# → subsequent: auto login as admin@hermes.local / hermes123
```

To switch default model, edit `hermes-all.bat` line `set "MODEL=..."` (line ~13).

To install a different GGUF, drop it in `data\models\`, then either:
- Restart `bin\hermes-all.bat` after editing the MODEL line, OR
- Use Ollama's `import_ollama_blobs.py` to convert `sha256-XXXX` blobs

---

## 10. Debugging

### Log files
- `hermes\data\logs\hermes.log` — Hermes FastAPI + bootstrap.log
- `hermes\data\openwebui\logs\server.log` — Open WebUI (created by OW at runtime)
- Each launcher writes to its own window (visible in title bar)

### Common issues
| Symptom                                  | Cause / Fix                                |
|------------------------------------------|--------------------------------------------|
| bat flashes and exits                     | LF line endings → convert to CRLF          |
| `'E:\Hermes' is not recognized`           | Space in path + bad cmd /c invocation      |
| llama-server OOM                          | Model > VRAM → NGL=0 (CPU only)            |
| Open WebUI shows wrong models              | System Ollama detected → set ENABLE_OLLAMA_API=false |
| Bootstrap "invalid api key"               | MiniMax key not activated on their platform |
| Open WebUI "Model '' was not found"        | Model id mismatch → use `--alias`          |
| Bootstrap stuck waiting for OW             | OW DB corrupt → wipe `hermes\data\openwebui\webui.db` |

### Reset to clean state
```bash
# Wipe Open WebUI data (loses chat history, recreates admin on next start)
mavis-trash "E:\Hermes Agent\hermes\data\openwebui\webui.db"

# Reset admin password
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\hermes\scripts\reset_password.py"

# Run E2E test (no GPU needed)
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\tests\test_hermes.py"

# Verify NGL math
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\tests\verify_smart_ngl.py"
```

### Verify GPU is actually used
Open a separate terminal:
```bash
nvidia-smi
```
Look for `python.exe` or `llama-server.exe` row → check **GPU-Util** column.
If 0% → CPU mode, no GPU offload.

---

## 11. Testing

| Test                          | Purpose                                      | When to run        |
|-------------------------------|----------------------------------------------|---------------------|
| `tests\test_hermes.py`        | 17 E2E checks (mock LLM, no GPU)            | After major changes |
| `tests\verify_smart_ngl.py`  | Verify NGL math for all models               | After bat changes   |
| `bin\hermes-all.bat` e2e      | Real LLM full pipeline                       | Before commits      |

`test_hermes.py` uses `HERMES_LLM_MOCK=1` so it runs without GPU/LLM.

---

## 12. Known Limitations / TODO

- **GPU is RTX 3070 8GB** — fits 7B Q4_K_M, partial offload for 22GB qwen3
- **MiniMax API key not activated** — returns 2049 invalid
- **llama-server is single-model** — multi-model needs multi-instance
- **Hash embeddings are placeholders** — RAG quality is poor (until user runs `bin\install-embeddings.bat`)
- **`web_dist\` (React admin) is deprecated** — kept for legacy
- **memos binary not bundled** — `setup-memos.bat` is placeholder

## 13. Roadmap: 1+2+4 Plan (in progress)

User confirmed priorities: **4 (KB) → 1 (embeddings) → 2A (autonomous tasks)** + native skill marketplace.

### ✅ 4. Knowledge Base management — DONE
- `hermes/scripts/rebuild_kb.py` — wipes `index.jsonl` + `sources/`, re-ingests `data/knowledge/*.md` with sane limits
- Per-doc cap: `--max-chunks 1000`
- Result: 256k bloated chunks → 13 clean chunks (5 files, all with embeddings)
- Runtime add: planned (TODO: `hermes kb add <path>` CLI)

### ⚙️ 1. Real embeddings — FRAMEWORK DONE, MODEL OPTIONAL
- `hermes/embeddings.py` — `SBERTEmbedder` (sentence-transformers) + `HashEmbedderFallback`
- `hermes/server.py` `/v1/embeddings` uses the new factory; auto-falls back to hash
- `bin\install-embeddings.bat` — installs sentence-transformers + downloads all-MiniLM-L6-v2 (~330MB)
  - **Not run yet** — user's internet is 137KB/s, big downloads are slow
  - When user has fast internet: `bin\install-embeddings.bat` (interactive, asks confirm)
  - Sets `HERMES_EMBEDDER=auto` (default) — uses sbert if installed, hash if not

### ✅ 2A. Autonomous task execution — DONE
- `hermes/planner.py` — `Planner` class with `TaskStep` / `TaskResult` dataclasses
- Loop: LLM generates JSON plan → execute skills one by one → on failure, replan → summarize
- CLI: `hermes task "<goal>" --mock --json` (use `--mock` to test without LLM)
- HTTP: `POST /api/task` (sync or async with task_id polling via `GET /api/task/{id}`)
- `hermes agent.run_task(goal)` method wraps the planner
- Constants: `MAX_REPLANS=3`, `MAX_STEPS=20` (prevent runaway)
- 17/17 tests pass (planner tested with mock)
- Real LLM test still pending (user needs to run with `bin\start-llm-smart.bat` first)
- Wrapper: `bin\hermes-task.bat "<goal>"` for one-liner use
- Health probe: `GET /health` returns `{status, version, cloud_available, local_available, mode}`

Original sketch (from planning):
```python
async def plan_and_execute(self, goal: str) -> str:
    plan = await self.llm.plan(goal, available_skills=self.skills.list())
    for step in plan:
        try:
            result = await self._execute_step(step)
        except Exception as e:
            plan = await self.llm.replan(goal, plan, step, e)
    return summary
```

Actual implementation lives in `hermes/planner.py`, much richer (replan on step failure, error recovery, JSON parsing with tolerance for non-strict LLM output).

### ✅ Skill marketplace — FRAMEWORK DONE
- `hermes/scripts/install_skill.py`:
  - `list` — show installed + registry
  - `install <name|url>` — download + verify SHA256 + safety check
  - `remove <name>` — uninstall
  - `publish <name> <url> --sha ... --desc ...` — add to registry
- Registry: `hermes/data/skills/registry.json` (JSON list of {name, url, sha256, desc})
- User can curate the registry themselves (or set up a public GitHub repo)



---

## 13. Conversation Reference

This project was built across one long session on 2026-06-04/05. Key
turns (in Mavis conversation memory if picked up later):
- Built portable framework (Day 1)
- Fixed hermes-all.bat CRLF bug
- Integrated Open WebUI (overcame: missing __main__, RAG embedder,
  model id mismatch, Ollama auto-detect)
- Smart NGL launcher (overcame: 32-bit int overflow, nvidia-smi in for/f)
- Memory bank + cleanup (this file)
