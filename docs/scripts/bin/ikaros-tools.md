# bin/ikaros-tools.py — 带动作日志的工具函数

## 用途（原模块 docstring，Rule 11 / 2026-07-03 升级）
集中包装「带动作日志」的工具函数。所有 process / file / terminal 动作都通过这里，自动写 5 维 action log。每个动作带 start + end + completion_pct，异常 → 写 fail。

## 用法（Python）
```
from bin.ikaros_tools import ik_kill, ik_start, ik_run, ik_write
ik_kill(pid=1234, why="停 llama-server :8587")
ik_start("llama-server.exe", args=["-m", "..."], why="重启 :8587")
ik_run(["cmd.exe", "/c", "echo hi"], why="test")
ik_write("E:/Ikaros/data/foo.txt", "hi", why="记一下")
```
## 用法（CLI）
```
python bin/ikaros-tools.py kill --pid 1234 --why "..."
python bin/ikaros-tools.py start --bin llama-server.exe --args "-m ... --port 8587" --why "..."
python bin/ikaros-tools.py run --cmd "ls -la" --why "..."
```
## 设计
- 不阻塞 caller（kill / run / write 都立刻返）
- 失败 / 卡死 → log warning + 写 fail
- 5 维 who=伊卡洛斯/Ikaros
- portable：走 E:/Ikaros 内部路径
- `_get_action_log()` 单例延迟 import `ikaros-action-log.py`（避免循环）
