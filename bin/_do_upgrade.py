#!/usr/bin/env python3
"""
_do_upgrade.py — one-shot webui portable upgrade 0.6.15 -> 0.6.17.

This script intentionally does NOT depend on supervisor / watchdog being sane.
It detects and kills:
  - watchdog process (PID file in data/logs/hermes-watchdog.pid)
  - any node.exe running dist/server/index.js (the webui)
  - any hermes-supervisor / hermes-watchdog python processes
Then runs npm install, then starts a new webui directly (NOT under supervisor).

Manual run (DO NOT run inside Hermes agent — it kills webui which kills the
agent you are talking through):

  cd E:\\Hermes Agent
  .\\portable-python\\python.exe .\\bin\\_do_upgrade.py

After it returns, the user runs:

  .\\portable-python\\python.exe .\\bin\\hermes-supervisor.py --start
  .\\portable-python\\python.exe .\\bin\\hermes-supervisor.py --watchdog
"""
import os
import sys
import time
import subprocess
from pathlib import Path

# === HERMES_ROOT resolution (mirror watchdog/supervisor) ===
HERE = Path(__file__).resolve()
env_root = os.environ.get("HERMES_ROOT", "").strip()
if env_root:
    HERMES_ROOT = Path(env_root).resolve()
else:
    # shell out to hermes-root.py resolve
    # HERE = .../bin/_do_upgrade.py → project root is HERE.parent.parent
    project_root = HERE.parent.parent
    r = subprocess.run(
        [str(project_root / "portable-python" / "python.exe"),
         str(project_root / "bin" / "hermes-root.py"),
         "resolve"],
        capture_output=True, text=True, timeout=10
    )
    HERMES_ROOT = Path(r.stdout.strip()).resolve()

NODE_EXE = HERMES_ROOT / "portable-python" / "node.exe"
NODE_BIN = HERMES_ROOT / "runtime" / "node23" / "node.exe"
WEBUI_DIR = HERMES_ROOT / "runtime" / "node23" / "node_modules" / "hermes-web-ui"
NPM_CLI = HERMES_ROOT / "runtime" / "node23" / "node_modules" / "npm" / "bin" / "npm-cli.js"
LOG_DIR = HERMES_ROOT / "data" / "logs"
WEBUI_PID = LOG_DIR / "webui.pid"
WATCHDOG_PID = LOG_DIR / "hermes-watchdog.pid"
WEBUI_PORT = 8649
LOCK_FILE = LOG_DIR / "upgrading.lock"

NEW_VERSION = os.environ.get("UPGRADE_TARGET_VERSION", "0.6.17")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def kill_pid(pid, name=""):
    if not pid:
        return False
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, text=True
        )
        ok = r.returncode == 0
        if name:
            log(f"  kill {name} (pid {pid}) -> {'OK' if ok else r.stderr.strip()}")
        return ok
    except Exception as e:
        log(f"  kill {name} ERR {e}")
        return False

def find_webui_pids():
    """Find all node.exe processes whose command line contains 'hermes-web-ui/dist/server/index.js'.

    Use wmic to be reliable — tasklist /v output encoding varies across Windows
    versions and may strip or mangle long command lines.
    """
    pids = []
    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name='node.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10
        )
        for line in r.stdout.splitlines():
            if "hermes-web-ui" in line and "dist" in line and "server" in line and "index.js" in line:
                # CSV format: Node,CommandLine,ProcessId
                parts = line.strip().split(",")
                if len(parts) >= 3:
                    try:
                        pids.append(int(parts[-1]))
                    except ValueError:
                        pass
    except Exception as e:
        log(f"  find_webui_pids ERR {e}")
    return pids

def main():
    log(f"HERMES_ROOT = {HERMES_ROOT}")
    log(f"WEBUI_DIR   = {WEBUI_DIR}")
    log(f"NEW_VERSION = {NEW_VERSION}")
    log("")

    # 1. 写 lock（防止 watchdog / 别的 supervisor 重新拉 webui）
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(f"upgrading {time.time()}\n", encoding="utf-8")
    log(f"[1/6] wrote lock: {LOCK_FILE}")

    # 2. 杀 watchdog（按 PID 文件 + 搜进程命令行）
    log("[2/6] killing watchdog")
    if WATCHDOG_PID.exists():
        try:
            wd = int(WATCHDOG_PID.read_text(encoding="utf-8").strip())
            kill_pid(wd, "watchdog-via-pidfile")
        except ValueError:
            pass
        try:
            WATCHDOG_PID.unlink()
        except OSError:
            pass
    # 兜底：搜 python 进程命令行含 hermes-watchdog
    r = subprocess.run(
        ["wmic", "process", "where",
         "name='python.exe'",
         "get", "ProcessId,CommandLine", "/format:csv"],
        capture_output=True, text=True, timeout=10
    )
    for line in r.stdout.splitlines():
        if "hermes-watchdog" in line.lower():
            parts = line.strip().split(",")
            if len(parts) >= 2:
                try:
                    pid = int(parts[-1])
                    kill_pid(pid, "watchdog-via-wmic")
                except ValueError:
                    pass
    time.sleep(1)

    # 3. 杀 webui node.exe 进程
    log("[3/6] killing webui node.exe")
    for pid in find_webui_pids():
        kill_pid(pid, "webui-node")
    time.sleep(2)

    # 4. 验证 webui 真的没进程占用
    log("[4/6] verifying webui dir is unlocked")
    locktest = WEBUI_DIR / ".locktest"
    try:
        locktest.write_text("test", encoding="utf-8")
        locktest.unlink()
        log("  webui dir writable ✓")
    except OSError as e:
        log(f"  webui dir STILL LOCKED: {e}")
        log("  aborting. Manually: find any node.exe still using it and kill it.")
        return 1

    # 5. 跑 npm install
    log(f"[5/6] npm install -g hermes-web-ui@{NEW_VERSION} --prefix {HERMES_ROOT / 'runtime' / 'node23'}")
    log("  (this may take 30-90s)")
    r = subprocess.run(
        [str(NODE_BIN), str(NPM_CLI), "install", "-g", f"hermes-web-ui@{NEW_VERSION}",
         "--prefix", str(HERMES_ROOT / "runtime" / "node23")],
        cwd=str(HERMES_ROOT),
        capture_output=True, text=True, timeout=600
    )
    print(r.stdout[-2000:] if r.stdout else "(no stdout)")
    if r.stderr:
        print("--- stderr ---")
        print(r.stderr[-1000:])
    if r.returncode != 0:
        log(f"  npm install FAILED rc={r.returncode}")
        log("  keep lock file so user can debug; exiting.")
        return r.returncode
    # 验证版本
    pkg = WEBUI_DIR / "package.json"
    if pkg.exists():
        v = pkg.read_text(encoding="utf-8", errors="replace").split('"version":')[1].split(",")[0].strip().strip('"')
        log(f"  installed version: {v}")

    # 6. 启动新 webui
    log(f"[6/6] starting new webui on :{WEBUI_PORT}")
    webui_home = HERMES_ROOT / "data" / "webui"
    webui_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HERMES_WEB_UI_HOME"] = str(webui_home)
    env["PORT"] = str(WEBUI_PORT)
    env["NODE_ENV"] = "production"
    proc = subprocess.Popen(
        [str(NODE_BIN), str(WEBUI_DIR / "dist" / "server" / "index.js")],
        env=env,
        cwd=str(HERMES_ROOT),
        stdout=open(LOG_DIR / "webui.log", "a", encoding="utf-8", buffering=1),
        stderr=open(LOG_DIR / "webui.err", "a", encoding="utf-8", buffering=1),
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    log(f"  new webui pid: {proc.pid}")
    WEBUI_PID.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(3)
    # 健康检查
    r = subprocess.run(
        ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
         "--max-time", "3", f"http://127.0.0.1:{WEBUI_PORT}/health"],
        capture_output=True, text=True
    )
    code = r.stdout.strip() or r.stderr.strip()
    log(f"  health: http://127.0.0.1:{WEBUI_PORT}/health -> {code}")

    # 删 lock（5 分钟后，让 watchdog 拉起 supervisor 链可以恢复）
    # 保留 30s 让新 webui 站稳
    time.sleep(2)
    try:
        LOCK_FILE.unlink()
        log(f"  removed lock: {LOCK_FILE}")
    except OSError:
        pass

    log("")
    log("=== upgrade done ===")
    log("next step (manual):")
    log("  .\\portable-python\\python.exe .\\bin\\hermes-supervisor.py --start")
    log("  .\\portable-python\\python.exe .\\bin\\hermes-supervisor.py --watchdog")
    return 0

if __name__ == "__main__":
    sys.exit(main())
