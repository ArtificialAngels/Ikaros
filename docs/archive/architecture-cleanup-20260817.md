# Ikaros 架构梳理与目录清理方案（2026-08-17）

> 目的：梳理现状 → 规范层级 → 合并同类 → 清理垃圾。
> 原则：**只读分析先行；删除前先归档；关键备份（v5.db 系列）一律不动；分批小量执行。**
> 扫描时间：2026-08-17 22:00。

---

## 1. 现状总览（体积全景）

| 目录 | 体积 | 性质 | 结论 |
|---|---|---|---|
| `runtime/` | 21 G | 便携工具链（python/node/bun/hermes-agent/llama.cpp，gitignore） | 保留 |
| `data/` | 9.7 G | 用户态数据（models/hermes-agent/v5.db/omp，gitignore） | 保留 |
| `core/` | 4.2 G | 核心源码；**其中 `core/memory_v5/models/` 占 4.1 G（GGUF 模型）** | 保留，模型目录见 §5 |
| `apps/` | 4.1 G | neko 前端全家桶（node_modules 等） | 保留 |
| `tmp/` | 1.1 G | 临时区（约定「临时文件→tmp/」） | §4 治理 |
| `home/` | 146 M | **死目录**：omp 便携化迁移残留（.omp/natives 145M），无任何代码引用 | **归档删除** |
| `graphify-out/` | 17 M | **一次性产物**（8/1 graphify 运行输出；`graph_export.py` 实际写 `core/memory_v5/data/v5/graphify-out/`，与此无关） | **归档删除** |
| `assets/` | 16 M | 品牌图（Artificialangel 同族 3 张） | 合并目标 |
| `output/` | 5.1 M | 截图/导出区（wb-* 为近期活跃产物） | 保留 |
| `patches/` | 1.7 M | hermes 补丁规范源（主仓 git 跟踪） | 保留 |
| `docs/ bin/ tests/ tools/ scripts/ config/ deploy/` | < 4 M | 源码/文档/脚本 | 保留 |
| `logs/` | 4 K | 空壳（仅 0 字节 dashboard_start.log） | 保留作日志落点 |
| `downloads/` | 4 K | 空壳 | 保留 |
| `home_ascii/ home_utf8/` | 0 | 空死目录 | **删除** |
| 根目录品牌图 ×6 | 28 M | Artificialangel.icns/ico/png/jpg×2/mini/source | **合并进 assets/**（ico 例外见 §3） |

## 2. 根目录文件逐项分类

| 文件 | 现状 | 处置 |
|---|---|---|
| `Artificialangel.icns/.png/.jpg/.mini.jpg/.source.png` ×5 | 与 `assets/` 同族重复 | **→ `assets/`** |
| `Artificialangel.ico` | 被 `desktop.ini` 引用作目录图标 | **留根目录** |
| `cad_mcp.log`（392 K） | CAD MCP 运行日志散落根目录 | **→ `logs/`** |
| `scan_recent.py` | 一次性 tmp 扫描工具 | **→ `tools/`** |
| `TASK.md`（6.7 K） | 48920 面板任务交接，已完成 | **→ `docs/archive/`**（或删，见确认项） |
| `overview.md`（2 K） | 48920 主题完成概览，已完成 | **→ `docs/archive/`**（或删，见确认项） |
| `AGENTS.md CLAUDE.md UPSTREAM.md README.md` | 架构/协作文档 | 保留 |
| `.env .env.example .dash_token pytest.ini requirements.txt Dockerfile desktop.ini` | 配置 | 保留 |
| `hermes.cmd` | CLI 包装器 | 保留，⚠️ 引用 `core\env\ikaros-env.bat`（env 双份问题见 §5） |
| `git* .github .githooks` | 版本控制 | 保留 |
| `.claude .codebuddy .workbuddy .gitnexus(96M) .pytest_cache .openfang .qoderignore` | 工具/IDE 配置 | 保留 |
| `.cad_mcp/`（workspace.db，8/14 仍在更新） | CAD MCP 工作区 | 保留 |
| `.playwright-mcp/`（350 K） | 8/12 一次性浏览器测试日志/页面 dump | **归档**（见确认项） |

## 3. 同类合并清单

1. **品牌图 → `assets/`**：根目录 5 张 + assets 已有 3 张，合并后 assets 为唯一品牌图落点（ico 因 desktop.ini 引用留根）。
2. **日志集中 → `logs/`**：`cad_mcp.log` 入 `logs/`；`data/logs/`、`core/data/logs/` 与根 `logs/` 三处并存的问题只记录不合并（涉及代码路径，风险高）。
3. **env 双份（需决策）**：`bin/ikaros-env.bat`（8/14，AGENTS.md 认定的 shell 权威源）vs `core/env/ikaros-env.bat`（8/12，被 `hermes.cmd`、`core/env/init.bat/ps1` 引用，变量集不同）。**默认不动**，仅归档记录；若合并须先同步两份内容再改引用。
4. **tmp 双份**：根 `tmp/`（1.1 G）与 `data/tmp/`（3.9 M，chattree 上传缓存）——功能重叠，默认不动 data/tmp（有活数据）。

## 4. tmp/ 治理（1.1 G，分三类）

**🔒 关键备份 —— 永久保留（迁移到 `tmp/keep/` 集中保管）：**
- `backup-v5del-20260731-094553/`（v5.db 7/31 备份，合并源）
- `v5-merged-prep/`（8/14 合并前 applied-backup）
- `v5.before-narrative-cleanup-*.db`、`v5.before-dedup-*.db`（8/14 清理前快照）
- `mem_backup_20260724/`（36 M）、`_mem_backup/`、`chroma_backup/`（13 M）、`mem-compare/`（15 M）

**📦 hermes 事件遗留 —— 可归档后删（总 ~570 M，hermes 迁移已 8/12 验证完成）：**
- `hermes-corrupt-git-backup/`（330 M）、`hermes-clean/`（163 M）、`hermes-c-stray-backup-20260804/`（56 M）
- `hermes-worktree-junk-20260814-081051/`、`merge3b/`（7.8 M）、`venv.broken-quarantine-20260729/`（16 M）

**🗑 一次性调试产物 —— 可清（总 ~370 M）：**
- `pptr/`（190 M）、`neko-state/`（149 M）、`kuzuv5test/`（29 M）、`discarded_20260724/`（6.9 M）、`quarantine-midx/`（4.9 M）
- `convtree-fusion/`、`ct-rebuild/`、`dsh-better-sidebar-probe/`、`ik/`、`ai-runtime-refs/`
- 散件：`_*.py/_*.js` 一次性脚本、`app.asar`（9 M）、`Artificialangel*.ico` 备份、`8099-*.html/css`、`_gpu_e2e.wav` 等

**⏳ 近期活跃（保留）：** `ct-verify-20260817/`、`ct-bubble-20260817/`、`ct-collision-all-20260817/`（均今天）

## 5. 架构规范建议（本轮不动手，仅记录）

1. **模型目录分裂**：`core/memory_v5/models/`（4.1 G，nomic embed 1.8 G + Phi-4 2.4 G）与 `data/models/`（按模型分目录）并存。规范上模型应统一 `data/models/`；迁移需同步 `model_config.json`/`panel_models.json`/加载代码路径，**风险高，待专门一轮处理**。
2. **env 权威源收敛**：`bin/ikaros-env.sh|bat` ↔ `data/hermes-agent/.env` 已是双权威，`core/env/` 整套（init/ikaros-env/ikaros-paths）与之重复，且 `ikaros-paths.json` 已知陈旧。建议后续以 `bin/` 为唯一 shell 源，`core/env/` 降级为兼容层。
3. **logs 三处并存**：根 `logs/`、`data/logs/`、`core/data/logs/`，建议后续统一到 `data/logs/` 或根 `logs/`。
4. **面板 models 同步**：`data/config/panel_models.json` 与模型实际位置需一致（8/14 已同步 Phi-4，随模型迁移一并复核）。

## 6. 执行计划（确认后按批执行）

| 批次 | 内容 | 方式 |
|---|---|---|
| P1 | 根目录文件迁移：品牌图×5→assets/、cad_mcp.log→logs/、scan_recent.py→tools/ | `git mv` 跟踪项 / `mv` 未跟踪项 |
| P2 | 死目录处理：`home/`→归档→删、`home_ascii/ home_utf8/`→删、`graphify-out/`→归档→删 | 归档到 `tmp/archive-20260817/`，验证后删除 |
| P3 | 文档归档：`TASK.md`、`overview.md` → `docs/archive/`（可选） | mv |
| P4 | tmp 治理：keep 迁移 + hermes 遗留归档 + 一次性产物清理 | 分批，每批 ≤10 项，删前列表确认 |

> 所有删除动作先 `mv` 进归档区，确认服务正常（:9100/:8642/:48920 健康）后再清理归档区；关键备份（v5.db 系列）全程只读不动。

---

## 7. 执行结果（2026-08-17 23:10 已完成）

用户确认：**全量 P1-P4** + TASK.md/overview.md 归档。

### ✅ 已完成

| 项 | 结果 |
|---|---|
| 品牌图合并 | 根目录 5 张 → `assets/`（同名覆盖为 8/8 20:27 精修版，哈希不同已核对；ico 因 desktop.ini 引用留根）；`bin/make_ico_artificial.py`、`probe_ico_sizes.py`、`probe_png512_only.py` 的 SRC 路径已同步为 `E:/Ikaros/assets/...` |
| 日志归位 | `cad_mcp.log` → `logs/` |
| 脚本归位 | `scan_recent.py` → `tools/` |
| 文档归档 | `TASK.md` → `docs/archive/TASK-48920-ui-20260809.md`；`overview.md` → `docs/archive/overview-48920-theme-20260806.md` |
| 死目录 | `home/`(146M)、`home_ascii/`、`home_utf8/`、`graphify-out/`(17M)、`.playwright-mcp/` 全部删除 |
| tmp 治理 | hermes 遗留（corrupt-git-backup 330M / clean 163M / c-stray 56M 等共 577M）+ 一次性产物（pptr 190M / kuzuv5test 29M 等共 294M）+ 散件 515 项全部清除；**tmp 1.3G → 250M（释放 ≈1.05G）** |
| 服务健康 | 清理后 :9100(200) / :48920(200) / :8587(415 正常) / :8642(health ok v0.20.0) 全部正常；conversation-tree 前端 `/assets/Artificialangel.ico` 完好 |

### ⏸ 未完成（预期内）

| 项 | 原因 |
|---|---|
| `tmp/archive-20260817/hermes-era/venv.broken-quarantine-20260729/`（17M） | 锁死 DLL（2026-07-29 已知问题），沙箱安全闸拦删；留着无害，重启后可清 |
| `tmp/app.asar`（9M） | 被进程占用（Device busy），未移走；可后续处理 |
| `tmp/check_chroma.py`、`debug_bench.py`、`debug_embed*.py` | 归档循环后新出现的调试文件（疑似某服务运行期写入），本轮未动 |

### 🔒 确认保留（未受影响）

- 全部 v5 备份：`backup-v5del-20260731-094553/`、`v5-merged-prep/`、`v5.before-*.db`、`backup-20260731-074439/`、`chroma_backup/`、`mem_backup_20260724/`、`mem-compare/`、`_mem_backup/`
- **`tmp/neko-state/`（149M）⚠️ 疑为 N.E.K.O 活数据**（character_cards/live2d/mmd/cloudsave），身份待确认，本轮绝不碰
- 今日活跃：`ct-bubble-20260817/`、`ct-collision-all-20260817/`、`ct-fix2-20260817/`、`ct-verify-20260817/`

### 📋 git 变更（未提交，等用户指令）

- `D` 根目录 5 品牌图 / `M` assets 同名 2 张 / `??` assets 新增 3 张
- `D` overview.md / `D` scan_recent.py / `??` tools/scan_recent.py
- TASK.md 未跟踪（直接 mv）
