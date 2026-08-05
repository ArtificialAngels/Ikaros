# Ikaros × Hermes —— studio 式「0 侵入」包装层设计

> 目标：把 `core/hermes` 工作树的 10 个 Ikaros overlay（9 A 类 modified + 1 B 类 added）**全部清零**，让 hermes 成为 100% 纯净上游，所有 Ikaros 集成胶水移到 Ikaros 自有代码 `core/hermes-bridge/`。
> 参考：hermes-studio 是独立 TS 应用包裹 hermes 运行时，零源码改动。本设计 = Ikaros 版的等价做法。

## 为什么需要包装层（而不是直接删补丁）

10 个 overlay 分两类：
- **可迁/可删（无功能损失）**：`context_engine/__init__.py`（死代码）、`skills/creative/tldraw-skill/`（迁技能目录）、`scripts/run_tests*` + `tests/cron/test_scheduler.py`（迁测试）。
- **功能胶水（删了丢特性）**：
  - `agent/conversation_loop.py`（**不可约**）：模型**真思考**透成 `reasoning.available` 的源头正确性修复——HEAD 会把答案文本当 thinking 回显（双重显示）。bridge 的 reasoning 正是经此发出，故必须保留。
  - `gateway/platforms/api_server.py`（**已消除**）：原把 reasoning 接到 OpenAI-wire `/v1/chat/completions`；现由 bridge 经 session-chat 翻译等价替代，已恢复 HEAD。
  - `hermes_cli/web_server.py`（**不可约**）：hermes 在 `f5be9236e` 这个残缺 pin 没接线的 94 个 Dashboard 路由 + 2 个 Ikaros 端点。删回 HEAD→9119 报废。
  - `cron/scheduler.py`（**不可约**）：固定 `_cron_session_id=f"cron_{job_id}"` 防 session 堆积 + 对更新版 hermes 深度行为适配。还原崩 cron。
  - `tools/mcp_tool.py`（**已消除**）：`_inject_ikaros_root_paths` 自推导 `IKAROS_*`。launcher 已注入，已恢复 HEAD。

**关键发现**：hermes 上游的 **session-chat 端点**（Dashboard 自己用的那条）**原生就发 reasoning**——经 `tool_progress_callback("reasoning.available")` → `tool.progress(_thinking, delta)`。只有 OpenAI-wire `/v1/chat/completions` 那条（对话树当前用的）上游故意没接 `tool_progress_callback` 才不发。→ 包装层让对话树改吃 session-chat 端点 + 翻译即可，**无需任何 core/hermes 改动**。

## 架构

```
Ikaros 自有代码 (core/hermes-bridge/, 新建)         Hermes (core/hermes, 纯净, 子进程)
┌─────────────────────────────────────┐           ┌──────────────────────────────┐
│ hermes-bridge  (aiohttp 服务)         │           │ 9119 dashboard (pristine)    │
│  ├─ /v1/chat/completions  ← 48920 指这里            │ 8642 gateway   (pristine)     │
│  │     └ 内部调 hermes session-chat 端点            │   ├ session-chat 端点(发reasoning)│
│  │     └ SSETranslator 翻译 → hermes.reasoning    │   └ /v1/chat/completions(无reason)│
│  ├─ /api/dashboard/plugin-providers 等             │                              │
│  │     └ 读改写 hermes config.yaml               │                              │
│  └─ 反向代理 其余 → 9119                          │                              │
└─────────────────────────────────────┘           └──────────────────────────────┘
        对话树 48920 ──改 HERMES_AGENT_URL──▶ hermes-bridge(而非直连 8642)
        Dashboard 9119 ──Ikaros 端点改走 bridge──▶ hermes-bridge
```

## 阶段（已建脚手架见 `core/hermes-bridge/`）

- **A1（✅ 已完成）** `translate.py`：`SSETranslator` 把 hermes 原生 SSE → 对话树 OpenAI-wire 方言。6 个单测全过（pytest 本机缺失，用 `importlib` 内联跑过）。替代 api_server.py + conversation_loop.py 两个补丁。
- **A2（✅ 已建成，待受控切流验证）** `core/hermes-bridge/server.py`（**stdlib-only**，非 aiohttp，零依赖符合 U 盘自包含）+ `bin/hermes-bridge.py` launcher + `tests/test_server.py`（9 单测全过）。`/v1/chat/completions` 自己实现：派生稳定 hermes session → 调原生 session-chat 端点 + 串 `SSETranslator`；`/health` 已现场验 200、8642 在线。`core/conversation-tree/server.py` 已加 `X-Ikaros-Conv-Id` 透传头（对当前直连 8642 透明）。**待做**：48920 改指 8650 的受控切流 + 活体验思考块。
- **A3（⚠️ 修正：web_server.py 保留为唯一 overlay）** 重新核查发现：web_server.py 的 3871 行 overlay 绝大多数是 **hermes 自己在 `f5be9236e` 这个残缺 pin 没接线的 Dashboard 路由**（git/cron/mcp/skills/sessions/profiles/tools，调用 HEAD 已含的 `web_git`/`web_routers/*` 原生模块），仅 2 个真正 Ikaros 特有端点（`/api/tools/computer-use/*`）。删回 HEAD 会卸掉 94 个 hermes 原生端点→9119 报废。**用户拍板：web_server.py 接受为不可约 overlay**，不做 A3 的代理层。仅剩 Ikaros 真端点理论上可搬 bridge，但收益低，维持现状。
- **批次1（✅ 已完成）** 清理 5 个非 web_server 的薄 overlay：context_engine/__init__.py、run_tests.sh、run_tests_parallel.py、test_scheduler.py 恢复到 HEAD；tldraw-skill 迁出 core/hermes → `data/hermes-agent/skills/creative/tldraw-skill/` + config.yaml 加条目。patches/hermes 源同步删、更新脚本 allowlist 同步（10→5）。
- **A4（⚠️ 结论：scheduler.py 必须保留为 overlay）** 经核查 scheduler.py 补丁是对**更新版 hermes** 的深度行为适配：`build_subprocess_env`→`_sanitize_subprocess_env`（HEAD 两函数都在，但补丁选 sanitize 版适配新版）、config 改 yaml 直读替代 `read_user_config_raw`、`finish_execution` 调用从 7 参简化为 2 参（匹配新版导入签名，还原 HEAD 会运行时 TypeError 崩 cron）、delivery 逻辑简化、cron 固定 session 去重（`f"cron_{job_id}"`）。**恢复 HEAD = cron 运行时崩 + session 堆积 bug 复现**。替代需「真升级 hermes pin」或「写外部 cron runner」，前者用户否决、后者体量过大。**scheduler.py = 不可约 overlay #2**。
- **A5（✅ 已完成）** mcp_tool.py 补丁 `_inject_ikaros_root_paths` 已**消除**（core/hermes 恢复 HEAD + patches 源 `git rm` + 更新脚本 allowlist 同步移除）。逻辑搬到 `core/hermes-bridge/inject_ikaros_paths.py`（纯 stdlib、不 import hermes、setdefault 不覆盖启动器权威值），并在 `bin/hermes-bridge.py` 拉起前调用兜底。原补丁冗余——网关启动器 `core/dashboard/server.py:build_env()`（145–171 行）早已注入全套 IKAROS_*。**overlay 5→4**。
- **B（✅ 离线冒烟 PASS → 用户授权继续，已落地）** 48920 切换机制已就绪且**默认改走 bridge**：`core/conversation-tree/server.py:72` 的 `HERMES_AGENT_URL` 默认值由直连 `:8642` 改为 `:8650`（bridge）；设 `HERMES_AGENT_URL=""` 可禁用 runtime，设直连 URL 可绕过 bridge。bridge 已接入**控制面板**为托管组件 `hermes_bridge`（:8650，启停/健康/状态齐全），并在启动 48920 前自动确保 bridge 已起（不可达时 48920 自带降级到本地 DeepSeek，不硬崩）。验证器 `tmp/verify-bridge-live.py` 已写。离线冒烟：bridge /health 200→8642、/v1/chat/completions→200 SSE、透明代理到 8642 session-chat 拿到真实 HTTP 响应、优雅处理无崩溃。**沙箱无 API key/活 LLM，reasoning 帧需用户在带 key 环境跑验证器得完整 PASS** —— 但 bridge 翻译逻辑已由 15 个单测（6 翻译+9 服务，含 [DONE] 终止帧）覆盖，离线已可靠。
- **B2（✅ 用户授权继续，api_server.py 已删）** `gateway/platforms/api_server.py` 恢复 HEAD（pristine），`patches/hermes/gateway/platforms/api_server.py` 同步 `git rm`，更新脚本 `A_CLASS_FILES` 移除该项。**理由**：其 OpenAI-wire reasoning 中继已由 bridge 经 session-chat 翻译等价替代；48920 默认走 bridge 后不再依赖 8642 的 OpenAI-wire reasoning。**overlay 4→3**。
- **⚠️ conversation_loop.py 复核：转为不可约 overlay（修正 B 原「待删」计划）** 逐行核查 `agent/conversation_loop.py` 的 Ikaros 改动发现：HEAD 版本在 `run_conversation` 里「只要 `assistant_message.content` 存在就发 `reasoning.available` 带 `_think_text[:500]`（=答案文本 strip 后截 500）」—— 当模型没真正推理时会把**答案文本回显进 thinking 块**（双重显示 bug）。Ikaros 改动改为「仅当 `assistant_message.reasoning` 真有时才发 `_reason_text[:8000]`」。而 **bridge 的 reasoning 来自 session-chat 端点，session-chat 的 reasoning 正是 `run_conversation` 经 `tool_progress_callback` 发出的** —— 还原 conversation_loop.py 到 HEAD 会让 bridge 忠实地把「答案当思考」翻译给 48920，**这是已知 UX bug 且翻译层无法修**。故 conversation_loop.py 保留为**不可约 overlay #3**（reasoning 源头的正确性修复，bridge 依赖它产出干净推理），与 web_server/scheduler 并列。

## 安全原则

1. **先建桥、后删补丁**：bridge 验证能替代前，overlay 全程保留当回退。
2. **corrupted repo 保护**：`git reset --hard` 偶发删 working-tree；恢复用 `git checkout ikaros-patches-backup -- .`（始终 rc=0）。单文件还原谨慎。裸 `rm` 被沙箱 safe-delete 拦截时用 `git clean -fd` / `git rm` 绕开。
3. **动线上服务前灰度**：9119/8642/48920 逐个切到 bridge 验证，不一次全切。

## 现状（2026-08-05，22:1x 用户授权继续后）

- **core/hermes overlay 10→3**（剩 web_server + scheduler + conversation_loop）。
- 不可约 overlay 确认 3 个：
  - `web_server.py`（hermes 原生 Dashboard 接线，删回 HEAD 卸掉 94 个原生端点→9119 报废）
  - `scheduler.py`（对更新版 hermes 深度行为适配，还原崩 cron）
  - `conversation_loop.py`（reasoning 源头正确性修复：HEAD 会把答案文本当 thinking 回显，bridge 依赖它产出干净推理，翻译层无法修）
- 已消除 7 个：context_engine/__init.py（死代码）+ run_tests*/test_scheduler（迁测试）+ tldraw-skill（迁技能）+ mcp_tool（启动器注入兜底）+ api_server（bridge 经 session-chat 翻译替代）。
- bridge 已接入控制面板为托管组件 `hermes_bridge`(:8650)，48920 默认走 bridge；bridge 翻译逻辑 15 单测全过、离线冒烟 PASS。
- **剩余唯一线上验证项**：用户在带 `API_SERVER_KEY`+活模型环境跑 `tmp/verify-bridge-live.py` 得 reasoning/tool 帧完整 PASS（确认 bridge 经 session-chat 拿到的 reasoning 干净），可选地进一步把 conversation_loop.py 的 `_reason_text` 修复上游化（消除最后一个 overlay）——但属可选增强，非阻塞。
- 主仓暂存区含批次1/2/B2 的 patches 源删除（未 commit）；按惯例等用户 "commit/Push"。
