#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes Watchdog — 独立的 auto-restart 守护进程
==============================================

**为什么单独成一个进程**
    supervisor 启动完所有 services 后会立即退出(`hermes-all.bat`
    期望它快速 return 以便关闭窗口)。但服务跑着跑着可能 crash,
    需要一个**永远活着**的进程盯着端口,死了就重启。
    把 watchdog 做成 detached 子进程就完美满足这个需求:
    parent 退出不影响它,服务死了它能救。

**职责**
    1. 每 10s 扫描 `discover_modules()` 里所有 type=service 的模块
    2. 测每个 service 的 port:死了就调 `supervisor --restart <name>` 重启
    3. 自己挂了就完事——反正 supervisor 会重新 spawn 一个

**单例**
    `data/logs/hermes-watchdog.pid` 写自己的 PID。启动时检查:
    - 文件不存在 → 自己是 first,继续
    - 文件存在但 PID 已死 → stale,删除并继续
    - 文件存在且 PID 还活着 → exit 0(避免 fork 多个)

**优雅退出**
    - Ctrl-C → except KeyboardInterrupt → unlink PID 文件 → exit 0
    - taskkill /F /PID <pid> → 立即死,supervisor `cmd_watchdog_stop`
      会清理 PID 文件

**为什么不用 import supervisor**
    supervisor 是个 main-script,直接 import 会触发它的 argparse。
    复制 discover_modules()/check_port() 各 ~30 行,比拆 module 干净。

**用法**
    通常由 `bin/hermes-supervisor.py --start` 自动 detached 启动。
    手动调试:`portable-python\python.exe bin\hermes-watchdog.py`。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# ============================================================
# HERMES_ROOT 解析(同 supervisor,但不依赖 supervisor)
# ============================================================

HERE = Path(__file__).resolve()


def _resolve_hermes_root(here: Path) -> Path:
    env_root = os.environ.get("HERMES_ROOT", "").strip()
    if env_root:
        p = Path(env_root).resolve()
        if (p / "portable-python" / "python.exe").is_file():
            return p
    resolver = here.parent / "hermes-root.py"
    py = here.parent / "portable-python" / "python.exe"
    if resolver.is_file() and py.is_file():
        try:
            r = subprocess.run(
                [str(py), str(resolver), "resolve"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                p = Path(r.stdout.strip()).resolve()
                if p.is_dir():
                    return p
        except Exception:
            pass
    return here.parent.parent


HERMES_ROOT = _resolve_hermes_root(HERE)
MODULES_DIR = HERMES_ROOT / "modules"
LOG_DIR = HERMES_ROOT / "data" / "logs"
PYTHON_EXE = HERMES_ROOT / "portable-python" / "python.exe"
SUPERVISOR = HERMES_ROOT / "bin" / "hermes-supervisor.py"

PID_FILE = LOG_DIR / "hermes-watchdog.pid"

# watchdog 周期(秒)。太短 → CPU;太长 → 重启延迟。
INTERVAL_S = 10
# 重启同一个 service 的最短间隔(防止 start.ps1 自身 bug 导致疯狂重启)。
RESTART_COOLDOWN_S = 30

# ============================================================
# 数据模型(supervisor 的精简版,只读 type/port/host)
# ============================================================


@dataclass
class Module:
    name: str
    path: Path
    type: str
    port: Optional[int] = None
    host: str = "127.0.0.1"


def discover_modules() -> Dict[str, Module]:
    """扫描 modules/*/module.json,只关心 watchdog 需要的字段。"""
    modules: Dict[str, Module] = {}
    if not MODULES_DIR.is_dir():
        return modules
    for entry in sorted(MODULES_DIR.iterdir()):
        if not entry.is_dir():
            continue
        json_path = entry / "module.json"
        if not json_path.is_file():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        net = data.get("network") or {}
        modules[data.get("name", entry.name)] = Module(
            name=data.get("name", entry.name),
            path=entry,
            type=data.get("type", "service"),
            port=net.get("port"),
            host=net.get("host", "127.0.0.1"),
        )
    return modules


def check_port(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """非阻塞 TCP 探活。"""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


# ============================================================
# 单例检查
# ============================================================


def claim_singleton() -> bool:
    """如果已有 watchdog 在跑,返回 False(自己应退出)。否则写 PID 文件并返回 True。

    关键陷阱:supervisor `cmd_watchdog_start` 在 Popen watchdog 之后立刻写
    PID_FILE = proc.pid(即 watchdog 自己的 PID)。watchdog 启动后 claim_singleton()
    读 PID_FILE 会读到自己的 PID,如果天真地 tasklist-check → 找到自己 → 误判
    "another instance running" → 立刻退出,PID_FILE 残留一个死 PID。

    修复:先把 `old_pid == os.getpid()` 的情况跳过 alive check(那要么是
    supervisor 帮我预写的,要么是上次 watchdog 死后留下的 stale,
    两种情况都直接覆盖)。
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if PID_FILE.is_file():
        try:
            old_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = None
        if old_pid is not None and old_pid != os.getpid() and os.name == "nt":
            # 真有别人在跑才检查 liveness
            try:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old_pid}", "/NH"],
                    capture_output=True, text=True, timeout=5,
                )
                if str(old_pid) in r.stdout:
                    print(f"[watchdog] another instance running (pid {old_pid}); exiting.",
                          file=sys.stderr)
                    return False
            except Exception:
                pass
        # stale 或 self → 删除重建
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_singleton() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


# ============================================================
# 重启服务
# ============================================================


def restart_service(name: str) -> bool:
    """通过 supervisor --restart 重启单个 service。"""
    if not SUPERVISOR.is_file():
        print(f"[watchdog] supervisor not found: {SUPERVISOR}", file=sys.stderr)
        return False
    try:
        r = subprocess.run(
            [str(PYTHON_EXE), str(SUPERVISOR), "--restart", name],
            cwd=str(HERMES_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        # 记录输出到日志
        log_path = LOG_DIR / "hermes-watchdog.log"
        with log_path.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            f.write(f"\n[{ts}] restart {name} rc={r.returncode}\n")
            if r.stdout:
                f.write("--- stdout ---\n" + r.stdout + "\n")
            if r.stderr:
                f.write("--- stderr ---\n" + r.stderr + "\n")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"[watchdog] restart {name} timeout", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[watchdog] restart {name} error: {e}", file=sys.stderr)
        return False


# ============================================================
# 主循环
# ============================================================


def run() -> int:
    if not claim_singleton():
        return 0

    print(f"[watchdog] started (pid {os.getpid()})", flush=True)
    print(f"[watchdog] interval: {INTERVAL_S}s  cooldown: {RESTART_COOLDOWN_S}s",
          flush=True)
    print(f"[watchdog] supervising: {HERMES_ROOT}", flush=True)

    # name -> 最后一次重启的 timestamp(冷却用)
    last_restart: Dict[str, float] = {}

    try:
        while True:
            time.sleep(INTERVAL_S)
            modules = discover_modules()
            for name, m in modules.items():
                if m.type != "service" or not m.port:
                    continue
                if check_port(m.host, m.port, timeout_s=1.0):
                    continue
                # 冷却:同一 service 短时间内别反复重启(start.ps1 自身坏了的话)
                now = time.time()
                if now - last_restart.get(name, 0) < RESTART_COOLDOWN_S:
                    continue
                print(f"[watchdog] {name} (:{m.port}) DOWN — restarting",
                      flush=True)
                last_restart[name] = now
                if restart_service(name):
                    print(f"[watchdog] {name} restarted OK", flush=True)
                else:
                    print(f"[watchdog] {name} restart FAILED (see log)",
                          flush=True)
    except KeyboardInterrupt:
        print("[watchdog] KeyboardInterrupt — exiting", flush=True)
    finally:
        release_singleton()
    return 0


if __name__ == "__main__":
    sys.exit(run())
