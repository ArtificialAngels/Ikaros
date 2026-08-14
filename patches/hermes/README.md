# patches/hermes/ — Ikaros 对 hermes-agent 的定制补丁源文件

> **用途**：当 `runtime/hermes-agent` 更新到新版 upstream 后，从这里把补丁文件复制回 hermes 工作树并 commit。
> 放在这里而不是 `runtime/hermes-agent` 下面，是为了防止 hermes `git reset --hard` / `git clean` 时被误删。

## 目录结构

镜像 hermes-agent 仓库的目录布局，补丁文件放在对应位置：

```
patches/hermes/
├── cron/scheduler.py                        # A 类：固定 cron_session_id
├── hermes_cli/web_server.py                  # A 类：动态 context.engine 选项
├── plugins/context_engine/__init__.py        # A 类：list_context_engine_names()
├── plugins/context_engine/ikaros_v5/         # B 类：V5 上下文引擎插件（静态复制）
│   ├── __init__.py
│   └── plugin.yaml
├── plugins/memory/ikaros_v5/                # B 类：V5 记忆 provider 插件（静态复制）
│   ├── __init__.py
│   └── plugin.yaml
├── scripts/run_tests.sh                      # A 类：Windows venv 激活
├── scripts/run_tests_parallel.py             # A 类：Windows venv 探测
├── skills/creative/tldraw-skill/            # B 类：tldraw 技能（静态复制）
│   └── SKILL.md
└── tests/cron/test_scheduler.py             # A 类：cron_session_id 断言
```

## 补丁分类

- **A 类（tracked 文件补丁）**：4 个文件，随 upstream 重打（`cron/scheduler.py`、`hermes_cli/web_server.py`、`agent/conversation_loop.py`、`tests/cron/test_scheduler.py`）。`hermes-update-and-patch.py` 先尝试 3-way 重放，冲突时由 LLM 按 `docs/hermes-ikaros-patches.md` §5 的意图在新代码上重实现。
- **B 类（插件 / 技能目录）**：0 个（已清空；ikaros_v5 外置为 Hermes 用户插件，源在 `patches/hermes/plugins/ikaros_v5/`）。

## 使用方式

```bash
# 更新 hermes 到新版 upstream 并重打补丁
python bin/hermes-update-and-patch.py --apply

# 如果只想想手动恢复补丁（不 fetch upstream）
cp -r patches/hermes/* runtime/hermes-agent/
cd runtime/hermes-agent && git add -A && git commit -m "feat(hermes): apply Ikaros integration patches"
```

## 相关文件

- `docs/hermes-ikaros-patches.md` — 补丁规范（意图、allowlist、验证清单）
- `bin/hermes-update-and-patch.py` — 自动更新 + 打补丁脚本
