#!/usr/bin/env python3
"""ikaros-dashboard — Ikaros 控制面板后端（仅用标准库，无第三方依赖）。

本服务是 `ikaros control`（现在由 bin/ikaros-control.bat 直接拉起）的后端。做两件事：

  1. 监控 — 实时读取 ikaros-monitor.jsonl、V5 情感/思考状态，以及对各组件的
     端口 + 进程探测。
  2. 控制 — 暴露 POST 接口，直接在本进程内启动/停止各组件（进程生命周期、环境
     变量、隐藏窗口、强杀逻辑全部搬自原 Rust 启动器 ikaros.exe，现由 Python 原生
     实现，不再依赖外部 ikaros.exe）。

启动参数：
  --autostart   启动后按 BOOT_PROFILE 自动拉起「ikaros 核心 + hermes」，不碰
                语音桥 / 桌宠 / 各种前端。
  --open        启动后自动打开浏览器到控制面板页面。

端点：
  GET  /                  → index.html（控制面板 UI）
  GET  /api/components    → 全部组件实时状态（JSON）
  GET  /api/log           → ikaros-monitor.jsonl 最近 200 条事件（JSON）
  GET  /api/state         → V5 affect.json + pending_thought.json（JSON）
  GET  /api/events        → SSE 实时事件流
  POST /api/components/<id>/<action>   action ∈ {start,stop,restart}
  POST /api/system/<action>            action ∈ {start,stop}  (全部组件)
  POST /api/shutdown                    停止本控制面板服务
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

# Windows: 隐藏子进程控制台窗口
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
DEVNULL = subprocess.DEVNULL

# ── config ─────────────────────────────────────────────────────────────
PORT = 9100
HERMES_ROOT = pathlib.Path(
    os.environ.get("IKAROS_ROOT")
    or os.environ.get("HERMES_ROOT")
    or "E:\\Ikaros"
).resolve()
MONITOR_FILE = HERMES_ROOT / "data" / "logs" / "ikaros-monitor.jsonl"
AFFECT_FILE = HERMES_ROOT / "Ikaros-memory" / "data" / "v5" / "affect.json"
PENDING_THOUGHT_FILE = HERMES_ROOT / "Ikaros-memory" / "data" / "v5" / "pending_thought.json"
HERE = pathlib.Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
POLL_INTERVAL = 0.8  # seconds between file polls for SSE

# 双击启动时不自动拉任何组件，全部手动启停。
BOOT_PROFILE: list[str] = []

# Component registry — 控制面板的「有哪些组件」事实来源。
# `ports` 用 TCP 探测；`markers` 用进程命令行子串匹配。任一命中即视为 running。
COMPONENTS = [
    {"id": "memory", "name": "记忆服务", "category": "后端",
     "desc": "Embedding :8587 + 本地 LLM :8080", "ports": [8587, 8080],
     "markers": ["ikaros-memory-watchdog.py", "llama-server.exe"]},
    {"id": "voice", "name": "语音桥", "category": "后端",
     "desc": "Voice WS :7870 (STT / TTS)", "ports": [7870],
     "markers": ["ikaros-voice-ws.py"]},
    {"id": "think", "name": "自思考循环", "category": "后端",
     "desc": "V5 元认知 think.py --watch", "ports": [],
     "markers": ["think.py"]},
    {"id": "pet", "name": "桌宠", "category": "前端",
     "desc": "Live2D 桌宠 (Tauri)", "ports": [],
     "markers": ["ikaros-desktop-pet.exe"]},
    {"id": "dashboard", "name": "Hermes 面板", "category": "前端",
     "desc": "Hermes Dashboard :9119", "ports": [9119],
     "markers": []},
    {"id": "studio", "name": "Studio", "category": "前端",
     "desc": "Hermes Studio :8649", "ports": [8649, 8647],
     "markers": []},
    {"id": "desktop", "name": "Hermes 桌面", "category": "前端",
     "desc": "Hermes Desktop 应用", "ports": [],
     "markers": ["Hermes.exe"]},
    {"id": "screen", "name": "屏幕监控", "category": "监控",
     "desc": "本地活动监测", "ports": [],
     "markers": ["screen-activity-monitor.ps1"]},
    {"id": "soul", "name": "灵魂同步", "category": "监控",
     "desc": "V5 → SOUL.md 一次性同步", "ports": [],
     "markers": ["ikaros-soul-sync.py"]},
]
KNOWN_IDS = {c["id"] for c in COMPONENTS} | {"all"}
VALID_ACTIONS = {"start", "stop", "restart"}

# 全局环境（组件启动时使用）
ROOT = HERMES_ROOT
ENV: dict = {}

log = logging.getLogger("ikaros-dashboard")

# ── Studio local-update state (driven by POST /api/studio/update) ──
# Script is registered under Ikaros-environment and resolved relative to HERMES_ROOT
# (no hardcoded drive letter), per project convention.
STUDIO_UPDATE_SCRIPT = HERMES_ROOT / "Ikaros-environment" / "studio-local-update.bat"
STUDIO_UPDATE_LOG = HERMES_ROOT / "data" / "logs" / "studio-update.log"
studio_update_lock = threading.Lock()
studio_updating = False
studio_update_lines: list[str] = []


# ── 进程 / 环境辅助（移植自原 Rust 启动器）─────────────────────────────

def build_env(root: pathlib.Path) -> dict:
    """镜像 Rust build_env()：构造 Ikaros 全套环境变量。"""
    pp = root / "runtime" / "portable-python"
    e: dict = {}
    s = lambda p: str(p)
    e["IKAROS_ROOT"] = s(root)
    e["IKAROS_PYTHON"] = s(pp / "python.exe")
    e["IKAROS_RUNTIME"] = s(root / "runtime")
    e["IKAROS_NODE"] = s(root / "runtime" / "node" / "node.exe")
    e["IKAROS_DATA"] = s(root / "data")
    e["IKAROS_BIN"] = s(root / "bin")
    e["IKAROS_CONFIG"] = s(root / "config")
    e["IKAROS_MODULES"] = s(root / "modules")
    e["IKAROS_LOGS"] = s(root / "data" / "logs")
    e["IKAROS_HERMES_AGENT"] = s(root / "hermes-agent")
    e["IKAROS_HERMES_HOME"] = s(root / "data" / "hermes-agent")
    e["IKAROS_STUDIO"] = s(root / "hermes-studio")
    e["IKAROS_STUDIO_LOGS"] = s(root / "data" / "logs" / "hermes-studio")
    e["IKAROS_STUDIO_DATA"] = s(root / "data" / "hermes-studio")
    e["HERMES_BIN"] = s(root / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
    e["HERMES_AGENT_CLI_PYTHON"] = s(root / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    e["HERMES_AGENT_BRIDGE_PYTHON"] = s(root / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    e["HERMES_AGENT_NODE"] = s(root / "runtime" / "node" / "node.exe")
    e["IKAROS_MEMORY"] = s(root / "Ikaros-memory")
    e["IKAROS_MEMORY_DATA"] = s(root / "Ikaros-memory" / "data")
    e["IKAROS_MEMORY_MODELS"] = s(root / "Ikaros-memory" / "models")
    e["IKAROS_MEMORY_SCRIPT"] = s(root / "Ikaros-memory" / "v4" / "store.py")
    e["IKAROS_LIVE2D"] = s(root / "Ikaros-Live2D")
    e["IKAROS_NODE_MODULES"] = s(root / "runtime" / "node" / "node_modules")
    e["IKAROS_RUST"] = s(root / "runtime" / "rust")

    llama_ver = os.environ.get("IKAROS_LLAMA_VERSION", "b10000-cuda")
    e["IKAROS_LLAMA_VERSION"] = llama_ver
    llama_dir = root / "runtime" / "llama" / llama_ver
    e["IKAROS_LLAMA_DIR"] = s(llama_dir)
    e["IKAROS_LLAMA_SERVER"] = s(llama_dir / "llama-server.exe")
    e["IKAROS_MODEL_EMBEDDING"] = s(root / "Ikaros-memory" / "models" / "nomic-embed-text-v2-moe.f32.gguf")

    e["IKAROS_PORT_EMBEDDING"] = "8587"
    e["IKAROS_PORT_LLM"] = "8080"
    e["IKAROS_PORT_BRIDGE"] = "7860"
    e["IKAROS_PORT_LIVE2D_WEBVIEW"] = "8648"
    e["IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL"] = "8649"
    e["IKAROS_PORT_LLAMA"] = "8080"

    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    e["PYTHONPATH"] = s(root) + ";" + s(root / "hermes-agent")
    e["NODE_PATH"] = s(root / "runtime" / "node" / "node_modules")

    # HERMES_* 兼容
    e["HERMES_ROOT"] = s(root)
    e["HERMES_HOME"] = s(root / "data" / "hermes-agent")
    e["HERMES_PYTHON"] = e["IKAROS_PYTHON"]
    e["HERMES_RUNTIME"] = e["IKAROS_RUNTIME"]
    e["HERMES_AGENT_ROOT"] = e["IKAROS_HERMES_AGENT"]

    # PATH: 把项目目录前置到继承的 PATH 之上
    path_parts = [
        e["IKAROS_RUST"] + "\\bin",
        e["IKAROS_LLAMA_DIR"],
        e["IKAROS_RUNTIME"],
        e["IKAROS_RUNTIME"] + "\\node",
        s(pp / "Scripts"),
        s(pp),
    ]
    old = os.environ.get("PATH", "")
    if old:
        path_parts.append(old)
    e["PATH"] = ";".join(path_parts)
    return e


def child_env(base: dict, overrides: dict | None = None) -> dict:
    """镜像 Rust child_env()：os.environ + base + overrides。"""
    m = dict(os.environ)
    m.update(base)
    if overrides:
        m.update(overrides)
    return m


def spawn_hidden(cmd: str, args: list, env: dict, cwd: str | None = None,
                 logfile: str | None = None,
                 flags: int = CREATE_NO_WINDOW) -> subprocess.Popen | None:
    """隐藏窗口启动子进程；logfile 非空则把输出重定向到该文件。

    flags 默认为 CREATE_NO_WINDOW。对于需要控制台句柄的进程（如 think.py
    里 hermes_client 初始化时依赖 TTY），可传入 CREATE_NEW_CONSOLE。
    """
    stdout = stderr = DEVNULL
    if logfile:
        try:
            os.makedirs(os.path.dirname(logfile), exist_ok=True)
            f = open(logfile, "wb")
            stdout = stderr = f
        except Exception:
            log_exception("spawn_hidden open logfile")
    try:
        p = subprocess.Popen(
            [cmd, *args],
            env=env,
            cwd=cwd,
            stdin=DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        log.debug("spawn: pid=%s cmd=%s flags=%s", p.pid, cmd, flags)
        return p
    except Exception as exc:
        log.error("spawn_hidden failed: %s %s -> %s", cmd, args, exc)
        return None


def run_child(cmd: str, args: list, env: dict, cwd: str | None = None,
              visible: bool = False) -> int:
    """运行子进程并返回退出码；visible=False 时丢弃输出并隐藏窗口。"""
    try:
        r = subprocess.run(
            [cmd, *args],
            env=env,
            cwd=cwd,
            stdout=None if visible else DEVNULL,
            stderr=None if visible else DEVNULL,
            creationflags=0 if visible else CREATE_NO_WINDOW,
        )
        return r.returncode or 0
    except Exception as e:
        log.error("run_child failed: %s %s -> %s", cmd, args, e)
        return 1


def kill_port(port: int) -> None:
    """按端口强杀监听进程（netstat + taskkill）。"""
    try:
        r = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        out = r.stdout or ""
    except Exception:
        return
    for line in out.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            pid = line.split()[-1].strip()
            if pid.isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid], stdout=DEVNULL, stderr=DEVNULL,
                               creationflags=CREATE_NO_WINDOW)


def kill_image(name: str) -> None:
    subprocess.run(["taskkill", "/F", "/IM", name, "/T"], stdout=DEVNULL, stderr=DEVNULL,
                   creationflags=CREATE_NO_WINDOW)


def kill_by_cmdline(substr: str) -> None:
    """强杀命令行包含 substr 的所有进程（Windows 上 SIGTERM 不可靠，用 taskkill）。"""
    safe = substr.replace("'", "''")
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $null -ne $_.CommandLine -and "
        "$_.CommandLine -like '*%s*' } | ForEach-Object { taskkill.exe /F /T /PID $($_.ProcessId) 2>$null }"
        % safe
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], stdout=DEVNULL, stderr=DEVNULL,
                   creationflags=CREATE_NO_WINDOW)


def wait_for_port(port: int, timeout: int) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if tcp_probe(port):
            return True
        time.sleep(2)
    return False


def wait_for_file(path: pathlib.Path, timeout: int) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if path.exists():
            return True
        time.sleep(2)
    return False


def pet_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ikaros-desktop-pet.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        return "ikaros-desktop-pet.exe" in out
    except Exception:
        return False


def ensure_pet_junction(root: pathlib.Path) -> None:
    nm = root / "Ikaros-Live2D" / "node_modules"
    target = root / "runtime" / "node" / "node_modules"
    if not target.exists():
        return
    if nm.exists():
        subprocess.run(["cmd", "/c", "rmdir", str(nm)], stdout=DEVNULL, stderr=DEVNULL)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(nm), str(target)], stdout=DEVNULL, stderr=DEVNULL)


def open_browser(url: str) -> None:
    try:
        subprocess.run(["cmd", "/c", "start", "", url],
                       creationflags=CREATE_NO_WINDOW, stdout=DEVNULL, stderr=DEVNULL)
    except Exception:
        log_exception("open_browser")


def log_exception(ctx: str) -> None:
    log.exception(ctx)


# ── 组件启停实现（单一事实来源，移植自 Rust start/stop_component_*）─────

def start_component_memory(root, env, wait):
    log.info("[memory] starting watchdog (Embedding :8587 + LLM :8080)...")
    wd = str(root / "runtime" / "portable-python" / "python.exe")
    wds = str(root / "bin" / "ikaros-memory-watchdog.py")
    spawn_hidden(wd, [wds, "--detach"], env, None,
                 str(root / "data" / "logs" / "memory-watchdog.log"))
    if wait:
        ep = root / "Ikaros-memory" / "data" / "endpoints.json"
        if wait_for_file(ep, 80):
            log.info("[memory] endpoints ready")
        else:
            log.warning("[memory] timeout waiting for endpoints.json")


def stop_component_memory(root, env):
    log.info("[memory] stopping watchdog...")
    wd = str(root / "runtime" / "portable-python" / "python.exe")
    wds = str(root / "bin" / "ikaros-memory-watchdog.py")
    run_child(wd, [wds, "--stop"], env, None, False)
    kill_port(8587)
    kill_port(8080)
    kill_by_cmdline("ikaros-memory-watchdog.py")


def start_component_voice(root, env, wait):
    log.info("[voice] starting Voice WS (:7870)...")
    wd = str(root / "runtime" / "portable-python" / "python.exe")
    vw = str(root / "bin" / "ikaros-voice-ws.py")
    spawn_hidden(wd, [vw], env, None, str(root / "data" / "logs" / "voice-ws.log"))
    if wait:
        wait_for_port(7870, 40)


def stop_component_voice(root, env):
    log.info("[voice] stopping (:7870)...")
    kill_port(7870)


def start_component_think(root, env, wait):
    log.info("[think] starting V5 self-think loop...")
    # 用 hermes-agent venv Python（CPython 3.12.10）而非 portable-python。
    # portable-python 有 C 扩展兼容性 bug，导入 v5.think 后会 0xC0000005。
    wd = str(root / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    think = str(root / "Ikaros-memory" / "v5" / "think.py")
    p = spawn_hidden(wd, [think, "--watch"], env, None,
                     str(root / "data" / "logs" / "think.log"),
                     flags=CREATE_NO_WINDOW)
    if p is None:
        log.error("[think] spawn failed")


def stop_component_think(root, env):
    log.info("[think] stopping...")
    kill_by_cmdline("think.py")


def start_component_pet(root, env, wait):
    if not tcp_probe(7870):
        log.info("[pet] Voice WS not up, starting it first...")
        start_component_voice(root, env, True)
    ensure_pet_junction(root)
    pet = root / "Ikaros-Live2D" / "src-tauri" / "target" / "release" / "ikaros-desktop-pet.exe"
    if not pet.exists():
        log.error("[pet] %s not found. Build: cd Ikaros-Live2D && npx tauri build", pet)
        return
    kill_image("ikaros-desktop-pet.exe")
    try:
        p = subprocess.Popen([str(pet)], env=dict(env), stdin=DEVNULL)
        log.info("[pet] launched pid=%s", p.pid)
    except Exception as e:
        log.error("[pet] failed to launch: %s", e)
    time.sleep(2)
    if pet_running():
        log.info("[pet] started")
    else:
        log.warning("[pet] may not have started")


def stop_component_pet(root, env):
    log.info("[pet] stopping...")
    kill_image("ikaros-desktop-pet.exe")


def start_component_dashboard(root, env, do_open=True):
    cenv = env  # 不设 HERMES_SERVE_HEADLESS，让 hermes 用默认行为
    if tcp_probe(9119):
        if do_open:
            open_browser("http://127.0.0.1:9119")
        return
    hermes = env["HERMES_BIN"]
    log.info("Starting Hermes Dashboard on :9119...")
    spawn_hidden(hermes, ["dashboard", "--port", "9119", "--no-open"],
                 cenv, None, str(root / "data" / "logs" / "dashboard.log"))
    if wait_for_port(9119, 60) and do_open:
        open_browser("http://127.0.0.1:9119")


def stop_component_dashboard(root, env):
    log.info("[dashboard] stopping (:9119)...")
    kill_port(9119)


def start_component_studio(root, env, do_open=True):
    studio = root / "hermes-studio"
    studio_logs = root / "data" / "logs" / "hermes-studio"
    studio_logs.mkdir(parents=True, exist_ok=True)
    cenv = child_env(env, {})
    cenv["PATH"] = str(root / "runtime" / "node") + ";" + env["PATH"]
    cenv["NODE_PATH"] = ""
    cenv["HERMES_WEB_UI_HOME"] = str(root / "data" / "hermes-studio")
    if not (studio / "node_modules").exists():
        log.info("[install] node_modules missing - running npm install (several minutes)...")
        run_child("cmd", ["/c", "npm", "install"], cenv, str(studio), False)
    log.info("Starting Hermes Studio (dev) on http://127.0.0.1:8649 ...")
    logfile = str(studio_logs / "hermes-studio.log")
    if pathlib.Path(logfile).exists():
        try:
            os.remove(logfile)
        except Exception:
            pass
    spawn_hidden("cmd", ["/c", "npm", "run", "dev"], cenv, str(studio), logfile)
    if wait_for_port(8649, 180):
        if do_open:
            open_browser("http://127.0.0.1:8649")
    else:
        log.warning("[studio] did not respond on :8649 within timeout (may still be starting)")


def stop_component_studio(root, env):
    log.info("[studio] stopping (:8647/:8648/:8649)...")
    kill_port(8647)
    kill_port(8648)
    kill_port(8649)
    kill_image("node.exe")


def start_component_desktop(root, env):
    cenv = child_env(env, {})
    cenv["HERMES_HOME"] = str(root / "data" / "hermes-agent")
    cenv["HERMES_DESKTOP_HERMES_ROOT"] = str(root / "hermes-agent")
    cenv["HERMES_DESKTOP_PYTHON"] = str(root / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    cenv["HERMES_DESKTOP_CWD"] = str(root)
    cenv["HERMES_DESKTOP_USER_DATA_DIR"] = str(root / "data" / "hermes-agent" / "desktop")
    cenv["PATH"] = (
        "{r}\\runtime\\node;{r}\\runtime\\portable-python;"
        "{r}\\hermes-agent\\venv\\Scripts;{old}"
    ).format(r=str(root), old=env["PATH"])
    desktop_exe = (
        root / "hermes-agent" / "apps" / "desktop" / "release" / "win-unpacked" / "Hermes.exe"
    )
    if not desktop_exe.exists():
        log.error("[FATAL] Hermes Desktop not found: %s", desktop_exe)
        return
    (root / "data" / "hermes-agent" / "logs").mkdir(parents=True, exist_ok=True)
    log.info("Hermes Desktop launched.")
    spawn_hidden(str(desktop_exe), [], cenv, None,
                 str(root / "data" / "hermes-agent" / "logs" / "desktop-stdout.log"))


def stop_component_desktop(root, env):
    log.info("[desktop] stopping...")
    kill_image("Hermes.exe")


def start_component_screen(root, env):
    log.info("[screen] starting screen activity monitor...")
    script = str(root / "bin" / "screen-activity-monitor.ps1")
    run_child("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "start"],
              env, None, False)


def stop_component_screen(root, env):
    log.info("[screen] stopping...")
    kill_by_cmdline("screen-activity-monitor.ps1")


def start_component_soul(root, env):
    log.info("[soul] syncing V5 soul to Hermes...")
    wd = str(root / "runtime" / "portable-python" / "python.exe")
    soul = str(root / "bin" / "ikaros-soul-sync.py")
    rc = run_child(wd, [soul, "--once"], env, None, False)
    if rc == 0:
        log.info("[soul] SOUL.md synced")
    else:
        log.warning("[soul] soul-sync failed (non-fatal)")


def stop_component_soul(root, env):
    # 一次性同步，无常驻进程可停
    pass


def comp_running(name: str) -> bool:
    if name == "memory":
        return tcp_probe(8587) or tcp_probe(8080)
    if name == "voice":
        return tcp_probe(7870)
    if name == "pet":
        return pet_running()
    return False


def comp_already_up(name: str) -> bool:
    """boot 时判断该组件是否已运行，避免重复拉起。"""
    if name in ("memory", "voice"):
        return comp_running(name)
    if name == "pet":
        return pet_running()
    if name == "studio":
        return tcp_probe(8649) or tcp_probe(8647)
    if name == "dashboard":
        return tcp_probe(9119)
    if name == "think":
        procs = _running_command_lines()
        return any("think.py" in ln for ln in procs)
    return False


def component_start(name: str, env: dict, wait: bool) -> None:
    root = ROOT
    if name == "memory":
        start_component_memory(root, env, wait)
    elif name == "voice":
        start_component_voice(root, env, wait)
    elif name == "think":
        start_component_think(root, env, wait)
    elif name == "pet":
        start_component_pet(root, env, wait)
    elif name == "dashboard":
        start_component_dashboard(root, env)
    elif name == "studio":
        start_component_studio(root, env)
    elif name == "desktop":
        start_component_desktop(root, env)
    elif name == "screen":
        start_component_screen(root, env)
    elif name == "soul":
        start_component_soul(root, env)
    elif name == "all":
        # 幂等：已运行的组件跳过，避免重复拉起 watchdog / llama-server
        if comp_running("memory"):
            log.info("[memory] already running, skip")
        else:
            start_component_memory(root, env, wait)
        if comp_running("voice"):
            log.info("[voice] already running, skip")
        else:
            start_component_voice(root, env, wait)
        start_component_think(root, env, wait)
        if comp_running("pet"):
            log.info("[pet] already running, skip")
        else:
            start_component_pet(root, env, wait)
        # 前端按需启动
        if not comp_already_up("dashboard"):
            start_component_dashboard(root, env, do_open=False)
        if not comp_already_up("studio"):
            start_component_studio(root, env, do_open=False)
        if not comp_already_up("desktop"):
            start_component_desktop(root, env)
        start_component_screen(root, env)
        start_component_soul(root, env)
    else:
        log.warning("[component] unknown component: %s", name)


def component_stop(name: str, env: dict) -> None:
    root = ROOT
    if name == "memory":
        stop_component_memory(root, env)
    elif name == "voice":
        stop_component_voice(root, env)
    elif name == "think":
        stop_component_think(root, env)
    elif name == "pet":
        stop_component_pet(root, env)
    elif name == "dashboard":
        stop_component_dashboard(root, env)
    elif name == "studio":
        stop_component_studio(root, env)
    elif name == "desktop":
        stop_component_desktop(root, env)
    elif name == "screen":
        stop_component_screen(root, env)
    elif name == "soul":
        stop_component_soul(root, env)
    elif name == "all":
        # 先停前端，再停后端
        stop_component_dashboard(root, env)
        stop_component_studio(root, env)
        stop_component_desktop(root, env)
        stop_component_screen(root, env)
        stop_component_pet(root, env)
        stop_component_think(root, env)
        stop_component_voice(root, env)
        stop_component_memory(root, env)
    else:
        log.warning("[component] unknown component: %s", name)


def boot_profile() -> None:
    """双击 control 时按 BOOT_PROFILE 自动拉起（仅核心 + hermes）。"""
    for cid in BOOT_PROFILE:
        try:
            if comp_already_up(cid):
                log.info("boot: %s 已在运行，跳过", cid)
                continue
            log.info("boot: 启动 %s ...", cid)
            if cid == "memory":
                start_component_memory(ROOT, ENV, True)
            elif cid == "studio":
                start_component_studio(ROOT, ENV, do_open=False)
            elif cid == "soul":
                start_component_soul(ROOT, ENV)
            else:
                component_start(cid, ENV, False)
        except Exception:
            log_exception(f"boot {cid} failed")
        time.sleep(1)
    log.info("boot: 完成（仅 ikaros 核心 + hermes，未启动语音桥/桌宠/各种前端）")


def run_component_action(name: str, action: str) -> bool:
    """原生派发 start/stop/restart。后台线程执行，避免阻塞 HTTP 请求。"""
    if name not in KNOWN_IDS or action not in VALID_ACTIONS:
        return False

    def _do():
        try:
            if action == "restart":
                component_stop(name, ENV)
                time.sleep(2)
                component_start(name, ENV, False)
            elif action == "start":
                component_start(name, ENV, False)
            elif action == "stop":
                component_stop(name, ENV)
        except Exception:
            log_exception(f"action {name}/{action} failed")

    threading.Thread(target=_do, daemon=True).start()
    return True


# ── file → in-memory cache ────────────────────────────────────────────
_log_cache: list[dict] = []
_log_cache_lock = threading.Lock()
_log_file_pos: int = 0  # bytes we've already read from MONITOR_FILE


def _normalize(entry: dict) -> dict:
    """Normalize old-format (type) and new-format (kind) entries into a
    uniform shape for the front‑end."""
    kind = entry.get("kind") or entry.get("type", "unknown")
    text = entry.get("text", "")
    ts = entry.get("ts", time.time())

    kind_map = {
        "user_msg": "user_msg",
        "assistant_msg": "assistant_msg",
        "thought": "thought",
        "status": "status",
        "state": "state",
        "stt": "stt",
    }
    display_kind = kind_map.get(kind, kind)

    return {
        "kind": kind,
        "display_kind": display_kind,
        "text": text,
        "ts": ts,
        "session_id": entry.get("session_id", ""),
        "mood": entry.get("mood", ""),
        "intensity": entry.get("intensity"),
        "raw": entry,
    }


def _reload_cache() -> None:
    """Reload file position → tail into _log_cache."""
    global _log_file_pos
    if not MONITOR_FILE.exists():
        return
    with _log_cache_lock:
        try:
            with open(str(MONITOR_FILE), "rb") as f:
                f.seek(_log_file_pos)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        _log_cache.append(_normalize(raw))
                    except json.JSONDecodeError:
                        continue
                _log_file_pos = f.tell()
            if len(_log_cache) > 500:
                _log_cache[:] = _log_cache[-500:]
        except Exception:
            log_exception("reload_cache")


def _read_tail(count: int = 200) -> list[dict]:
    """Return last *count* normalized events."""
    _reload_cache()
    with _log_cache_lock:
        return _log_cache[-count:]


def _read_v5_state() -> dict:
    """Read affect.json + pending_thought.json."""
    state: dict = {}
    for path, key in [(AFFECT_FILE, "affect"), (PENDING_THOUGHT_FILE, "thought")]:
        if path.exists():
            try:
                with open(str(path), "r", encoding="utf-8") as f:
                    state[key] = json.load(f)
            except Exception:
                state[key] = None
        else:
            state[key] = None
    return state


# ── component status probing ──────────────────────────────────────────

def tcp_probe(port: int) -> bool:
    """Return True if a TCP server is listening on 127.0.0.1:*port*."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def _running_command_lines() -> list[str]:
    """Return lower-cased command lines of all running processes (PowerShell)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, errors="replace", timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
        return [ln.strip().lower() for ln in (out.stdout or "").splitlines() if ln.strip()]
    except Exception:
        log_exception("running_command_lines")
        return []


def get_component_statuses() -> list[dict]:
    """Probe every component and return its live status."""
    procs = _running_command_lines()
    result: list[dict] = []
    for c in COMPONENTS:
        ports_up = [p for p in c["ports"] if tcp_probe(p)]
        marker_hits = [m for m in c["markers"] if any(m.lower() in pc for pc in procs)]
        running = bool(ports_up) or bool(marker_hits)
        if ports_up:
            detail = "端口 " + ",".join(str(p) for p in ports_up) + " 在线"
        elif marker_hits:
            detail = "进程: " + ", ".join(marker_hits)
        else:
            detail = "未运行"
        result.append({
            "id": c["id"],
            "name": c["name"],
            "category": c["category"],
            "desc": c["desc"],
            "ports": c["ports"],
            "ports_up": ports_up,
            "running": running,
            "marker_hits": marker_hits,
            "detail": detail,
        })
    return result


# ── SSE helpers ────────────────────────────────────────────────────────

def _sse_event(wfile, data: dict, event: str | None = None) -> None:
    """Write one SSE event to *wfile*."""
    payload = json.dumps(data, ensure_ascii=False)
    if event:
        wfile.write(f"event: {event}\n".encode())
    wfile.write(f"data: {payload}\n\n".encode())
    wfile.flush()


# ── Studio local update (fork-safe git pull + reinstall + reapply + restart) ──

def run_studio_local_update() -> None:
    """Run the Ikaros-environment/studio-local-update.bat in a background thread,
    capture its output line-by-line into studio_update_lines, then restart Studio
    so the pulled/patched code takes effect."""
    global studio_updating, studio_update_lines
    with studio_update_lock:
        if studio_updating:
            return
        studio_updating = True
    studio_update_lines = []
    try:
        if not STUDIO_UPDATE_SCRIPT.exists():
            studio_update_lines.append(
                f"ERROR: update script not found: {STUDIO_UPDATE_SCRIPT}")
            return
        STUDIO_UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        # run the bat with node on PATH (so npm works)
        cenv = dict(os.environ)
        cenv["PATH"] = str(HERMES_ROOT / "runtime" / "node") + os.pathsep + cenv.get("PATH", "")
        proc = subprocess.Popen(
            ["cmd", "/c", str(STUDIO_UPDATE_SCRIPT)],
            cwd=str(HERMES_ROOT),
            env=cenv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            studio_update_lines.append(line)
            try:
                with open(STUDIO_UPDATE_LOG, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        proc.wait()
        studio_update_lines.append(f"[exit code {proc.returncode}]")
        # restart Studio so the pulled/patched code takes effect
        try:
            component_stop("studio", build_env(HERMES_ROOT))
        except Exception as e:  # noqa: BLE001
            studio_update_lines.append(f"[warn] stop studio: {e}")
        time.sleep(3)
        try:
            component_start("studio", build_env(HERMES_ROOT))
        except Exception as e:  # noqa: BLE001
            studio_update_lines.append(f"[warn] start studio: {e}")
        studio_update_lines.append("DONE")
        try:
            with open(STUDIO_UPDATE_LOG, "a", encoding="utf-8") as f:
                f.write("DONE\n")
        except OSError:
            pass
    finally:
        with studio_update_lock:
            studio_updating = False


# ── HTTP request handler ───────────────────────────────────────────────

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    # silence per-request logs from stdlib
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        if not INDEX_HTML.exists():
            self.send_error(404, "index.html not found")
            return
        with open(str(INDEX_HTML), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self) -> None:
        """SSE endpoint — long‑poll tailing the JSONL file."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        _sse_event(self.wfile, {"status": "connected"}, event="connected")

        last_count = 0
        last_ping = time.time()
        try:
            while not self.server._stop:
                _reload_cache()
                with _log_cache_lock:
                    new_entries = _log_cache[last_count:]
                    current_count = len(_log_cache)

                if new_entries:
                    for entry in new_entries:
                        _sse_event(self.wfile, entry, event="monitor")
                    last_count = current_count
                    last_ping = time.time()
                else:
                    if time.time() - last_ping > 15:
                        _sse_event(self.wfile, {"ts": time.time()}, event="ping")
                        last_ping = time.time()

                time.sleep(POLL_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception:
            log_exception("SSE handler")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._send_html()
        elif path == "/api/components":
            self._send_json(get_component_statuses())
        elif path == "/api/log":
            events = _read_tail(200)
            self._send_json(events)
        elif path == "/api/state":
            state = _read_v5_state()
            self._send_json(state)
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/studio/update" or path == "/api/studio/update/log":
            with studio_update_lock:
                self._send_json({
                    "lines": list(studio_update_lines),
                    "updating": studio_updating,
                })
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        # /api/components/<id>/<action>
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "components":
            cid = parts[2]
            action = parts[3] if len(parts) > 3 else "start"
            if run_component_action(cid, action):
                self._send_json({
                    "ok": True, "id": cid, "action": action,
                    "msg": f"{cid} {action} 已派发",
                })
            else:
                self._send_json({"ok": False, "msg": f"未知组件: {cid}"}, status=404)
            return

        # /api/system/<action>  (start|stop → all components)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "system":
            action = parts[2]
            if action in ("start", "stop"):
                run_component_action("all", action)
                self._send_json({"ok": True, "action": action,
                                 "msg": f"全部组件 {action} 已派发"})
            else:
                self._send_json({"ok": False, "msg": "未知系统动作"}, status=400)
            return

        # /api/shutdown  → stop this control panel
        if len(parts) >= 2 and parts[0] == "api" and parts[1] == "shutdown":
            self._send_json({"ok": True, "msg": "控制面板正在关闭"})
            threading.Thread(target=self.server.shutdown_later, daemon=True).start()
            return

        # /api/studio/update  → run local fork update (git pull + reinstall + reapply + restart)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "studio" and parts[2] == "update":
            with studio_update_lock:
                if studio_updating:
                    self._send_json({"ok": False, "msg": "Studio 更新进行中，请稍候"})
                    return
            threading.Thread(target=run_studio_local_update, daemon=True).start()
            self._send_json({"ok": True, "msg": "已启动 Studio 本地更新"})
            return

        self.send_error(404)

    do_PUT = do_DELETE = lambda s: s.send_error(405)


# ── threaded server ────────────────────────────────────────────────────

class ThreadedSSEServer(http.server.ThreadingHTTPServer):
    """HTTP server with a stop flag for SSE threads."""
    _stop: bool = False

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.daemon_threads = True

    def shutdown_later(self) -> None:
        self._stop = True
        self.shutdown()


def _find_free_port(start: int = PORT) -> int:
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"no free port in [{start}, {start+100})")


def main():
    global ENV
    # 保底日志：确保 detached 模式下崩了也有输出可查
    logdir = ROOT / "data" / "logs"
    try:
        logdir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    handlers = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    handlers.append(logging.FileHandler(str(logdir / "control-panel.log"), encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
    )
    autostart = "--autostart" in sys.argv
    do_open = "--open" in sys.argv

    ENV = child_env(build_env(ROOT), {})

    # 单例：先清掉旧面板（原 Rust 启动器也这么做）
    try:
        kill_port(PORT)
        time.sleep(1)
    except Exception:
        log_exception("kill_port at startup")

    try:
        port = int(os.environ.get("IKAROS_CONTROL_PORT") or PORT)
    except ValueError:
        port = PORT
    try:
        server = ThreadedSSEServer(("0.0.0.0", port), DashboardHandler)
    except OSError:
        port = _find_free_port(port + 1)
        server = ThreadedSSEServer(("0.0.0.0", port), DashboardHandler)

    log.info("ikaros 控制面板监听 http://127.0.0.1:%d", port)
    log.info("  /                  → 控制面板 UI")
    log.info("  /api/components    → 实时组件状态 (JSON)")
    log.info("  /api/log           → 最近 200 条事件 (JSON)")
    log.info("  /api/state         → V5 情感 + 思考状态 (JSON)")
    log.info("  /api/events        → SSE 实时流")
    if autostart:
        log.info("  --autostart: 启动后自动拉起 %s", BOOT_PROFILE)
    print(f"\n  🪶 Ikaros 控制面板 → http://127.0.0.1:{port}\n")

    if autostart:
        threading.Thread(target=boot_profile, daemon=True).start()
    if do_open:
        def _open():
            time.sleep(1.5)
            open_browser(f"http://127.0.0.1:{port}")
        threading.Thread(target=_open, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server._stop = True
        server.shutdown()
        log.info("server stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        try:
            (ROOT / "data" / "logs").mkdir(parents=True, exist_ok=True)
            with open(str(ROOT / "data" / "logs" / "control-panel-crash.log"), "a", encoding="utf-8") as f:
                traceback.print_exc(file=f)
        except Exception:
            traceback.print_exc()
