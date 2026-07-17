# bin/ikaros-action-log.py — 伊卡洛斯动作日志

## 用途（原模块 docstring，哥哥 7-3 19:20 拍板）
解决「hermes 网关死没死我不知道」的盲区。5 维 + 状态 + 完成度。

## 5 维（谁/做啥/何时/在哪/为啥）
- who: Ikaros / Quest / user
- what: 动作名（process.kill / file.write / …）
- when: 时间戳
- where: target 路径/pid
- why: 触发原因（用户原话/auto）

## 状态字段
intent / action / target / result(ok|fail|timeout|stuck) / duration_ms / exit_code / complete / completion_pct / started_at / ended_at

## 关键约束
每条 action 必须有 START 和 END 两条 log。没有 END = 卡死/崩溃。

## 用法
```
# CLI
python bin/ikaros-action-log.py start --intent "启 llama-server" --action process.start --target :8589 --why "用户拍"
python bin/ikaros-action-log.py end --id <action_id> --result ok --duration-ms 1200
# Python（推荐）
from bin.ikaros_action_log import action, action_done
with action("kill llama-server", action="process.kill", target=pid, why="cleanup") as a:
    process.kill(pid); a.done(result="ok")
# 看孤儿（没 END 的 action = 卡死）
python bin/ikaros-action-log.py orphans
```
日志文件：`data/logs/ikaros-actions.jsonl`。`_now_iso()` 用 ISO 8601 + 北京时区。
