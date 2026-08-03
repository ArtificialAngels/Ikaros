"""PID 督促循环 — chat 全能面板深度优化监督器。

哥哥指令（2026-08-02）：
  - 用 herdr 编排器 + CLI 干重活，持续督促运行，至少 5 小时，全自动。
  - 不需要过问，默认按设计意图推进，无确认环节。

设计（PID 控制语义）：
  P = 进度偏差  = 剩余任务数 / 总任务数        -> 决定是否派发新任务
  I = 累计空闲  = 自上次派发/完成以来的分钟数  -> 强制督促（防松懈）
  D = 变化率    = 最近窗口内 git 改动行数      -> 高活跃时降低干预强度

派发通道：WorkBuddy CLI（node codebuddy）后台会话模式
  codebuddy --bg --name <task> "<prompt>"   -> 长任务常驻
  codebuddy ps / logs <name> / kill <name>  -> 生命周期管理
herdr 职责：编排器存活监控（ping 失败自动重启 herdr.exe）。

用法：
  python bin/ikaros-drive.py            # 前台跑（调试）
  python bin/ikaros-drive.py --daemon   # 后台常驻（detach）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVE_DIR = ROOT / "data" / "drives" / "chat-panel-v4"
GOAL_FILE = DRIVE_DIR / "goal.md"
CLI_TASKS_FILE = DRIVE_DIR / "cli-tasks.md"
STATE_FILE = DRIVE_DIR / "state.json"
LOG_FILE = DRIVE_DIR / "drive.log"

CODEBUDDY = (
    r"C:\Users\PZS0X\AppData\Local\Programs\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy"
)
HERDR_EXE = ROOT / "runtime" / "herdr" / "herdr.exe"
HERDR_LOG = ROOT / "data" / "logs" / "herdr.log"

# PID 参数
TICK_S = 300            # 每 5 分钟一个 tick
IDLE_TRIGGER_MIN = 12   # 累计空闲超过 12 分钟 -> 强制派发
STALL_MIN = 90          # 活跃任务超过 90 分钟无产出 -> 判定卡死，kill 重派
CHANGE_WINDOW_S = 1800  # D 项窗口 30 分钟
MAX_CONCURRENT = 2      # 最多同时跑 2 个 CLI 任务


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "started_at": datetime.now().isoformat(),
        "tasks": {},          # task_id -> {status, name, started, pid, last_activity}
        "last_dispatch": None,
        "ticks": 0,
        "pid_terms": {"P": 0.0, "I": 0.0, "D": 0.0},
        "events": [],
    }


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_tasks(goal_text: str) -> list[dict]:
    """从 goal.md 解析任务清单（- [ ] T1 xxx / - [x] T1 xxx / C1 xxx）。"""
    tasks = []
    for m in re.finditer(r"- \[([ xX])\]\s*([A-Z]\d+)\s*(.+)", goal_text):
        tasks.append({"id": m.group(2), "done": m.group(1).lower() == "x", "name": m.group(3).strip()})
    return tasks


# ── WorkBuddy CLI 封装 ────────────────────────────────────────────── #
def _cmd(args: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(
            ["node", CODEBUDDY, *args],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return f"<cli error: {exc}>"


def wb_ps() -> list[dict]:
    """解析 `codebuddy ps` 输出。列: PID KIND NAME STATUS CWD STARTED。"""
    out = _cmd(["ps"], timeout=30)
    sessions = []
    for line in out.splitlines():
        parts = line.split()
        # 跳过表头/分隔线
        if len(parts) < 4 or parts[0] == "PID" or set(parts[0]) <= {"-"}:
            continue
        sessions.append({
            "pid": parts[0],
            "kind": parts[1],
            "name": parts[2],
            "state": parts[3],
            "cwd": parts[4] if len(parts) > 4 else "",
        })
    return sessions


def wb_dispatch(task_id: str, prompt: str, cwd: str) -> str:
    out = _cmd(["--bg", "--name", f"pid-{task_id}", "--permission-mode", "bypassPermissions", prompt], timeout=90)
    return out


def wb_kill(task_id: str) -> str:
    return _cmd(["kill", f"pid-{task_id}"], timeout=30)


def wb_logs(task_id: str, tail: int = 20) -> str:
    return _cmd(["logs", f"pid-{task_id}", "--tail", str(tail)], timeout=30)


# ── herdr 监控 ────────────────────────────────────────────────────── #
def herdr_alive() -> bool:
    try:
        import sys as _s
        _s.path.insert(0, str(ROOT / "core"))
        from core.herdr import HerdrClient  # type: ignore
        c = HerdrClient()
        return c.ping().get("type") == "pong"
    except Exception:  # noqa: BLE001
        return False


def herdr_restart() -> None:
    log("herdr 不可达，尝试重启…")
    try:
        subprocess.Popen(
            [str(HERDR_EXE), "--headless"],
            cwd=str(ROOT), stdout=open(HERDR_LOG, "a"), stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"herdr 重启失败: {exc}")


# ── git 活跃度（D 项） ────────────────────────────────────────────── #
def git_change_rate(since_s: int) -> int:
    try:
        r = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )
        lines = r.stdout.strip().splitlines()
        total = 0
        for ln in lines:
            m = re.search(r"(\d+) \+(\d+)", ln)
            if m:
                total += int(m.group(1)) + int(m.group(2))
        return total
    except Exception:  # noqa: BLE001
        return 0


# ── 核心循环 ──────────────────────────────────────────────────────── #
def tick(st: dict) -> None:
    st["ticks"] += 1
    goal_text = GOAL_FILE.read_text(encoding="utf-8") if GOAL_FILE.exists() else ""
    tasks = parse_tasks(goal_text)
    total = max(len(tasks), 1)
    remaining = sum(1 for t in tasks if not t["done"])
    P = remaining / total

    # CLI 派发源：cli-tasks.md（与主 agent 前端工作不冲突）
    cli_text = CLI_TASKS_FILE.read_text(encoding="utf-8") if CLI_TASKS_FILE.exists() else ""
    cli_tasks = parse_tasks(cli_text)
    cli_remaining = sum(1 for t in cli_tasks if not t["done"])

    # 活跃 CLI 会话（unknown=刚启动未就绪, 也算活跃防重复派发）
    sessions = wb_ps()
    active = [s for s in sessions if s.get("state", "").lower() in ("running", "working", "active", "unknown")]

    # I 项：距上次派发/完成的时间
    last = st.get("last_dispatch")
    idle_min = 0.0
    if last:
        try:
            idle_min = (time.time() - datetime.fromisoformat(last).timestamp()) / 60.0
        except (ValueError, TypeError):
            idle_min = 0.0
    I = idle_min

    # D 项：最近窗口变化率
    D = git_change_rate(CHANGE_WINDOW_S)

    st["pid_terms"] = {"P": round(P, 3), "I": round(I, 1), "D": D}
    log(f"tick#{st['ticks']} P={P:.2f} I={I:.0f}min D={D} 前端剩余={remaining} CLI剩余={cli_remaining} 活跃CLI={len(active)}")

    # herdr 存活监控
    if not herdr_alive():
        herdr_restart()

    # 卡死检测：活跃任务超时无产出 -> kill 重派
    for t in tasks:
        tinfo = st["tasks"].get(t["id"])
        if not tinfo or tinfo.get("status") != "running":
            continue
        started = tinfo.get("started")
        if started:
            try:
                el = time.time() - datetime.fromisoformat(started).timestamp()
                if el > STALL_MIN * 60:
                    log(f"[stall] {t['id']} 已运行 {el/60:.0f}min 无产出，kill 重派")
                    wb_kill(t["id"])
                    tinfo["status"] = "stalled"
                    tinfo["note"] = f"stalled after {el/60:.0f}min"
            except (ValueError, TypeError):
                pass

    # 派发决策（从 cli-tasks.md 取任务，串行保护：同 id 不重复派发）
    if cli_remaining > 0 and len(active) < MAX_CONCURRENT:
        # 找第一个 pending 任务（不在 state 或非 running）
        next_task = None
        for t in cli_tasks:
            if t["done"]:
                continue
            tinfo = st["tasks"].get(t["id"], {})
            if tinfo.get("status") in ("running", "dispatched"):
                continue
            next_task = t
            break
        if next_task and (I >= IDLE_TRIGGER_MIN or D < 5):
            prompt = (
                f"你在 Ikaros 项目 (E:\\Ikaros) 中执行子任务 {next_task['id']}："
                f"{next_task['name']}。任务文件 E:\\Ikaros\\data\\drives\\chat-panel-v4\\cli-tasks.md。"
                f"只做这一个任务，完成后总结改动和验证结果，不要提交 git。"
                f"禁止改动 core/conversation-tree/index.html。"
            )
            out = wb_dispatch(next_task["id"], prompt, str(ROOT))
            st["tasks"][next_task["id"]] = {
                "status": "dispatched", "name": next_task["name"],
                "started": datetime.now().isoformat(),
                "dispatch_out": out[:200],
            }
            st["last_dispatch"] = datetime.now().isoformat()
            log(f"[dispatch] {next_task['id']} -> {next_task['name']}")
            save_state(st)
            return

    save_state(st)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true", help="后台常驻")
    ap.add_argument("--once", action="store_true", help="只跑一个 tick")
    args = ap.parse_args()

    DRIVE_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    log(f"PID 督促循环启动 v1 | goal={GOAL_FILE.name} | tick={TICK_S}s")
    if args.once:
        tick(st)
        return
    if args.daemon:
        # 已由调用方 detach；本进程前台跑循环
        pass
    while True:
        try:
            tick(st)
        except Exception as exc:  # noqa: BLE001
            log(f"tick 异常: {exc}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
