# bin/ikaros-repl.py — 真物 CLI 直聊

## 用途（原模块 docstring）
真物 chat loop，直接对接 `cogno_5d` + `cloud_chat` 引擎。哥哥 7-5 说"实时聊天"= 不重发明（不重写云聊、不重写 cogno_5d），只造 thin wrapper 把现成 pipeline 暴露为 STDIN/STDOUT loop。

## 用法
```
E:\Ikaros\portable-python\python.exe bin\ikaros-repl.py
> 哥哥说: 今天天气好
伊卡洛斯: ...
```

## 设计原则
- 直接 `import cloud_chat.cloud_chat`（650 行真物）
- 走 `cogno_5d.enrich(user_text)`（cogno 5 维 Phase 5 已 commit）
- `enrich_reply` 返 dict（Phase 5 已 commit, v3 0.6 真物）
- 失败静默（cogno 任何维失败 → `[未知]`）
- 1 次输入 = 1 次回答（CLI loop，不阻塞）
- 斜杠命令 `/contract <目标>`：把自然语言目标扩写成结构化合同（5 字段），单 LLM 调用，不走 cogno + cloud_chat。

## 关键实现（内联要点）
- `_call_llm`：优先 `cloud_chat`（异步转 sync），回退直接打 `:8080` llama-server（`/v1/chat/completions`，local LLM）。
- `_load_*` 系列：cogno_5d / cloud_chat / goal_contract 均失败静默，返回 dummy，不破坏主链。
- 路径自举：`_ROOT/bin`、`_ROOT/core/memory_v5`、`_ROOT/core/hermes` 注入 sys.path。
