#!/usr/bin/env python3
"""ikaros-dashboard — Ikaros control panel backend (stdlib only, no third-party deps).

This service is the backend for the Ikaros control panel (launched by
bin/ikaros-control.bat). It does two things:

  1. Monitor — real-time ikaros-monitor.jsonl, V5 affect/thought state, and
     port + process probing for each component.
  2. Control — exposes POST endpoints to start/stop components in-process
     (process lifecycle, env vars, hidden windows, kill logic — all native
     Python, no external launcher).

Launch flags:
  --autostart   Auto-start components per BOOT_PROFILE on launch.
  --open        Open browser to the control panel page on launch.

Endpoints:
  GET  /                  -> index.html (control panel UI)
  GET  /api/components    -> all component live status (JSON)
  GET  /api/log           -> last 200 ikaros-monitor.jsonl events (JSON)
  GET  /api/state         -> V5 affect.json + pending_thought.json (JSON)
  GET  /api/events        -> SSE real-time event stream
  POST /api/components/<id>/<action>   action in {start,stop,restart}
  POST /api/system/<action>            action in {start,stop}  (all components)
  POST /api/shutdown                     stop this control panel service
  GET  /api/hermes/status              -> hermes HEAD / upstream tip / 补丁状态
  POST /api/hermes/check               -> 检查并自动打补丁（cherry-pick 补丁 commit；已打则 no-op）
  POST /api/hermes/update              -> 跑完整脚本（fetch+reset+cherry-pick+LLM兜底）
"""

from __future__ import annotations

import hashlib
import http.server
import json
import logging
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
AFFECT_FILE = HERMES_ROOT / "core/memory_v5" / "data" / "v5" / "affect.json"
PENDING_THOUGHT_FILE = HERMES_ROOT / "core/memory_v5" / "data" / "v5" / "pending_thought.json"
HERE = pathlib.Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"
ASSETS_DIR = HERE / "assets"

# ── Hermes 版本 / Ikaros 补丁 控制（需求 §9：9100 面板更新控制 + 9119 启动预检）──
HERMES_AGENT_DIR = HERMES_ROOT / "core" / "hermes"
HERMES_PATCH_SPEC = HERMES_ROOT / "docs" / "hermes-ikaros-patches.md"
HERMES_PATCH_SCRIPT = HERMES_ROOT / "bin" / "hermes-update-and-patch.py"
POLL_INTERVAL = 0.8  # seconds between file polls for SSE

# 双击启动时不自动拉任何组件，全部手动启停。
BOOT_PROFILE: list[str] = ["local_model", "memory", "neko_group",
                            "hermes_dashboard", "neko_desktop", "qwenpaw"]

# Component registry — 控制面板的「有哪些组件」事实来源。
# `ports` 用 TCP 探测；`markers` 用进程命令行子串匹配。任一命中即视为 running。
COMPONENTS = [
    {"id": "local_model", "name": "本地模型 (Local LLM)", "category": "Backend",
     "desc": "本地大语言模型 :8080（可切换模型）", "ports": [8080],
     "model_kind": "llm", "markers": ["llama-server.exe"]},
    {"id": "memory", "name": "Memory Service", "category": "Backend",
     "desc": "Embedding 向量服务 :8587（可切换模型）", "ports": [8587],
     "model_kind": "embed", "markers": ["ikaros-memory-watchdog.py", "llama-server.exe"]},
    {"id": "neko_group", "name": "N.E.K.O 服务组", "category": "Frontend",
     "desc": "N.E.K.O 后端（前端/记忆/Agent）一键启停，亦可分开控制",
     "group": True, "subcomponents": ["neko", "neko_memory", "neko_agent"],
     "ports": [], "markers": ["main_server", "memory_server", "agent_server"]},
    {"id": "neko_desktop", "name": "N.E.K.O Desktop", "category": "Frontend",
     "desc": "N.E.K.O Electron 桌面壳（独立）", "ports": [],
     "markers": ["N.E.K.O.exe"]},
    {"id": "hermes_dashboard", "name": "Hermes Dashboard", "category": "Frontend",
     "desc": "Hermes 控制台 :9119（LLM 后端 / web UI）", "ports": [9119],
     "markers": ["hermes_cli.main"]},
    {"id": "qwenpaw", "name": "Hermes (猫爪)", "category": "Backend",
     "desc": "猫爪服务器 :8088 — Hermes Agent 驱动", "ports": [8088], "markers": [],
     "panel_url": "http://127.0.0.1:48911/api/agent/openclaw/guide"},
    {"id": "conversation_tree", "name": "对话树面板 (Conversation Tree)", "category": "Frontend",
     "desc": "Explore.poker 风格树形对话面板 :48920（后端 = conversation_tree 引擎）",
     "ports": [48920], "markers": ["conversation-tree"],
     "panel_url": "http://127.0.0.1:48920/"},
    {"id": "herdr", "name": "Herdr 终端编排", "category": "Backend",
     "desc": "coding-agent 终端多路复用器 (headless server，命名管道，无 TCP 端口)",
     "ports": [], "markers": ["herdr.exe"],
     "panel_url": "http://127.0.0.1:48920/"},
    # ── 隐藏子服务：作为 neko 服务组的独立控制项，不单独出现在面板网格 ──
    {"id": "neko", "name": "N.E.K.O Frontend", "category": "Backend",
     "desc": "N.E.K.O :48911 (Chat + Avatar + Ikaros V5)", "ports": [48911],
     "markers": ["main_server"], "hidden": True, "parent_group": "neko_group"},
    {"id": "neko_memory", "name": "N.E.K.O Memory", "category": "Backend",
     "desc": "N.E.K.O memory server :48912", "ports": [48912],
     "markers": ["memory_server"], "hidden": True, "parent_group": "neko_group"},
    {"id": "neko_agent", "name": "N.E.K.O Agent", "category": "Backend",
     "desc": "Agent server :48915 (keyboard/mouse/browser/OpenClaw)", "ports": [48915],
     "markers": ["agent_server"], "hidden": True, "parent_group": "neko_group"},
    {"id": "runtime", "name": "运行时依赖", "category": "依赖",
     "desc": "runtime/ 下的必要二进制（Python / Node / llama / 下载器 / MCP / Herdr）；缺失项提示手动获取",
     "ports": [], "markers": [], "check_only": True},
]
VALID_ACTIONS = {"start", "stop", "restart"}
KNOWN_IDS = {c["id"] for c in COMPONENTS} | {"all"}

# 全局环境（组件启动时使用）
ROOT = HERMES_ROOT
ENV: dict | None = None  # 惰性初始化: 见 _ensure_env()

log = logging.getLogger("core/dashboard")

# ── Process / env helpers (ported from original Rust launcher) ─────

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
    e["IKAROS_HERMES_AGENT"] = s(root / "core/hermes")
    e["IKAROS_HERMES_HOME"] = s(root / "data" / "hermes-agent")
    e["HERMES_ROOT"] = s(root)
    e["HERMES_BIN"] = s(root / "core/hermes" / "venv" / "Scripts" / "hermes.exe")
    e["HERMES_AGENT_CLI_PYTHON"] = s(root / "core/hermes" / "venv" / "Scripts" / "python.exe")
    e["HERMES_AGENT_BRIDGE_PYTHON"] = s(root / "core/hermes" / "venv" / "Scripts" / "python.exe")
    e["HERMES_AGENT_NODE"] = s(root / "runtime" / "node" / "node.exe")
    e["IKAROS_MEMORY"] = s(root / "core/memory_v5")
    e["IKAROS_MEMORY_DATA"] = s(root / "core/memory_v5" / "data")
    e["IKAROS_MEMORY_MODELS"] = s(root / "core/memory_v5" / "models")
    e["IKAROS_MEMORY_SCRIPT"] = s(root / "core/memory_v5" / "v5" / "store.py")
    e["IKAROS_NODE_MODULES"] = s(root / "runtime" / "node" / "node_modules")
    e["IKAROS_RUST"] = s(root / "runtime" / "rust")
    e["IKAROS_NEKO"] = s(root / "core/neko")
    e["IKAROS_NEKO_PYTHON"] = s(root / "core/neko" / ".venv" / "Scripts" / "python.exe")
    e["IKAROS_NEKO_SERVER"] = "app.main_server"  # 模块形式（上游已将入口重构为包 app/main_server）
    e["IKAROS_MODEL_EMBEDDING"] = s(root / "core/memory_v5" / "models" / "nomic-embed-text-v2-moe.f32.gguf")
    e["IKAROS_MODEL_LLM"] = s(root / "core/memory_v5" / "models" / "Qwen_Qwen3-1.7B-Q4_K_M.gguf")
    e["IKAROS_LABEL_EMOTION_PROVIDER"] = os.environ.get("IKAROS_LABEL_EMOTION_PROVIDER", "local")
    e["API_SERVER_KEY"] = os.environ.get("API_SERVER_KEY", "ikaros-gateway-key")

    llama_ver = os.environ.get("IKAROS_LLAMA_VERSION", "b10000-cuda")
    e["IKAROS_LLAMA_VERSION"] = llama_ver
    llama_dir = root / "runtime" / "llama" / llama_ver
    e["IKAROS_LLAMA_DIR"] = s(llama_dir)
    e["IKAROS_LLAMA_SERVER"] = s(llama_dir / "llama-server.exe")
    e["IKAROS_MODEL_EMBEDDING"] = s(root / "core/memory_v5" / "models" / "nomic-embed-text-v2-moe.f32.gguf")

    e["IKAROS_PORT_EMBEDDING"] = "8587"
    e["IKAROS_PORT_LLM"] = "8080"
    e["IKAROS_PORT_BRIDGE"] = "7860"
    e["IKAROS_PORT_LIVE2D_WEBVIEW"] = "8648"
    e["IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL"] = "8649"
    e["IKAROS_PORT_LLAMA"] = "8080"

    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    e["PYTHONPATH"] = s(root) + ";" + s(root / "core/hermes")
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


def _ensure_env() -> dict:
    """惰性初始化 ENV（在 child_env/build_env 定义后调用）。"""
    global ENV
    if ENV is None or not ENV.get("IKAROS_ROOT"):
        ENV = child_env(build_env(ROOT), {"NO_PROXY": "*", "no_proxy": "*"})
    return ENV


def spawn_hidden(cmd: str, args: list, env: dict, cwd: str | None = None,
                 logfile: str | None = None,
                 flags: int = CREATE_NO_WINDOW) -> subprocess.Popen | None:
    """隐藏窗口启动子进程；logfile 非空则把输出重定向到该文件。

    flags 默认为 CREATE_NO_WINDOW。对于需要控制台句柄的进程（如某些后台
    服务初始化时依赖 TTY），可传入 CREATE_NEW_CONSOLE。
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


def kill_image_wait(name: str, timeout: float = 5.0) -> int:
    """强杀所有同名进程并等待确认终止。

    与 ``kill_image`` 的区别：
    - kill_image 只发 kill 信号，不等待进程实际退出
    - kill_image_wait 发 kill 后在 timeout 秒内轮询 tasklist，确认全部终止后才返回

    Returns:
        实际杀死的进程数
    """
    count = 0
    import time as _time
    # 第一轮：发 kill 信号
    subprocess.run(["taskkill", "/F", "/IM", name, "/T"], stdout=DEVNULL, stderr=DEVNULL,
                   creationflags=CREATE_NO_WINDOW)
    # 第二轮：等待所有进程实际退出（Zombie 预防）
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            remaining = r.stdout.count(name)
        except Exception:
            remaining = 0
        if remaining == 0:
            break
        _time.sleep(0.3)
    # 计算实际杀死的数量
    try:
        r2 = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        still_alive = r2.stdout.count(name)
    except Exception:
        still_alive = 0
    if still_alive > 0:
        log.warning("kill_image_wait: %d %s process(es) still alive after %.1fs timeout",
                     still_alive, name, timeout)
    return max(0, count - still_alive)


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


def open_browser(url: str) -> None:
    try:
        subprocess.run(["cmd", "/c", "start", "", url],
                       creationflags=CREATE_NO_WINDOW, stdout=DEVNULL, stderr=DEVNULL)
    except Exception:
        log_exception("open_browser")


def log_exception(ctx: str) -> None:
    log.exception(ctx)


# ── 组件启停实现（单一事实来源，移植自 Rust start/stop_component_*）─────

# ── 模型切换（:8080 LLM / :8587 Embedding）────────────────────────────
MODELS_DIR = ROOT / "core/memory_v5" / "models"
PANEL_MODELS_PATH = ROOT / "data" / "config" / "panel_models.json"
_EMBEDDING_HINTS = ("embed", "nomic", "e5", "bge", "bce", "gte", "voyage", "rerank")


def _is_embedding_name(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _EMBEDDING_HINTS)


def list_models(kind: str) -> list[str]:
    """扫描 models 目录，按 kind(llm/embed) 返回可用 .gguf 文件名。"""
    if not MODELS_DIR.is_dir():
        return []
    files = [p.name for p in sorted(MODELS_DIR.glob("*.gguf")) if p.is_file()]
    if kind == "embed":
        return [f for f in files if _is_embedding_name(f)]
    return [f for f in files if not _is_embedding_name(f)]


def load_panel_models() -> dict:
    default = {"8080": "Qwen_Qwen3-1.7B-Q4_K_M.gguf",
               "8587": "nomic-embed-text-v2-moe.f32.gguf"}
    if PANEL_MODELS_PATH.is_file():
        try:
            d = json.loads(PANEL_MODELS_PATH.read_text(encoding="utf-8"))
            default.update({str(k): v for k, v in d.items() if isinstance(v, str)})
        except Exception:
            pass
    return default


def save_panel_models(d: dict) -> None:
    try:
        PANEL_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PANEL_MODELS_PATH.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log_exception("save_panel_models")


def current_model_for_port(port: int) -> str:
    return load_panel_models().get(str(port), "")


def _sync_llm_model_config(model_name: str) -> None:
    """同步 :8080 的选择到 v5 model_config.json，使 v5 懒加载保持一致。"""
    cfg_path = MODELS_DIR / "model_config.json"
    cfg: dict = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["initial_model"] = model_name
    for k, v in {"alias": "local-llm", "host": "127.0.0.1", "port": 8080,
                 "ctx_size": 8192, "gpu_layers": "auto", "flash_attn": "auto",
                 "cont_batching": True, "jinja": True}.items():
        cfg.setdefault(k, v)
    try:
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log_exception("sync_llm_model_config")


def spawn_llama_model(port: int, model_name: str, kind: str) -> bool:
    """启动 llama-server 加载指定模型。kind: 'llm' | 'embed'。"""
    llama_bin = str(ROOT / "runtime" / "llama" / "b10000-cuda" / "llama-server.exe")
    if not os.path.exists(llama_bin):
        log.error("[llama] llama-server not found: %s", llama_bin)
        return False
    mp = str(MODELS_DIR / model_name)
    if not os.path.exists(mp):
        log.error("[llama] model not found: %s", mp)
        return False
    cmd = [llama_bin, "-m", mp, "--host", "127.0.0.1", "--port", str(port)]
    if kind == "embed":
        cmd += ["-c", "4096", "-ngl", "99", "--embeddings",
                "--pooling", "mean", "--alias", "nomic-embed-text-v2-moe"]
    else:
        ctx, ngl = 8192, "auto"
        mcfg = MODELS_DIR / "model_config.json"
        if mcfg.is_file():
            try:
                _c = json.loads(mcfg.read_text(encoding="utf-8"))
                ctx = _c.get("ctx_size", 8192)
                ngl = _c.get("gpu_layers", "auto")
            except Exception:
                pass
        cmd += ["-c", str(ctx), "-ngl", str(ngl), "--flash-attn", "auto",
                "--alias", "local-llm", "--cont-batching", "--jinja"]
    try:
        p = subprocess.Popen(cmd, stdout=DEVNULL, stderr=DEVNULL,
                             creationflags=CREATE_NO_WINDOW)
        log.info("[llama] spawned PID=%s port=%s model=%s", p.pid, port, model_name)
        return True
    except Exception as e:
        log.error("[llama] spawn failed: %s", e)
        return False


def switch_model(port: int, model_name: str) -> tuple[bool, str]:
    """切换某端口的模型：记录选择，若运行中则重启为新模型。"""
    if port not in (8080, 8587):
        return False, "unknown port"
    kind = "llm" if port == 8080 else "embed"
    if model_name not in list_models(kind):
        return False, f"model not available: {model_name}"
    d = load_panel_models()
    d[str(port)] = model_name
    save_panel_models(d)
    if kind == "llm":
        _sync_llm_model_config(model_name)
    was_up = tcp_probe(port)
    kill_port(port)
    if was_up:
        spawn_llama_model(port, model_name, kind)
        log.info("[switch] %s restarted with %s", port, model_name)
    else:
        log.info("[switch] %s selection -> %s (lazy, not running)", port, model_name)
    return True, "ok"


def start_component_memory(root, env, wait):
    """启动 Memory Service：直接 spawn 选中的 embedding llama-server (:8587)，
    并后台启动 watchdog 做健康巡查。Embedding 模型可在面板切换。"""
    log.info("[memory] starting embedding (:8587) + watchdog...")

    model = current_model_for_port(8587) or "nomic-embed-text-v2-moe.f32.gguf"
    if not (MODELS_DIR / model).is_file():
        avail = list_models("embed")
        model = avail[0] if avail else model
    spawn_llama_model(8587, model, "embed")
    log.info("[memory] embed model=%s, waiting for :8587...", model)

    # 后台启动 watchdog 做巡查
    py = str(root / "runtime" / "portable-python" / "python.exe")
    wds = str(root / "bin" / "ikaros-memory-watchdog.py")
    spawn_hidden(py, [wds], env, str(root / "bin"),
                 str(root / "data" / "logs" / "memory-watchdog.log"))

    if wait:
        if wait_for_port(8587, 80):
            log.info("[memory] embed :8587 ready")
        else:
            log.warning("[memory] embed :8587 timeout — 可能需要修复 llama-server")


def start_component_local_model(root, env, wait):
    """启动本地模型 (:8080)：加载面板选中的 LLM。默认懒加载，
    面板可显式拉起；若未运行则 agent 调用时热载入。"""
    log.info("[local_model] starting local LLM (:8080)...")
    model = current_model_for_port(8080) or "Qwen_Qwen3-1.7B-Q4_K_M.gguf"
    if not (MODELS_DIR / model).is_file():
        avail = list_models("llm")
        model = avail[0] if avail else model
    if not model:
        log.error("[local_model] no LLM model available")
        return
    spawn_llama_model(8080, model, "llm")
    if wait:
        if wait_for_port(8080, 90):
            log.info("[local_model] :8080 ready")
        else:
            log.warning("[local_model] :8080 timeout")


def stop_component_memory(root, env):
    log.info("[memory] stopping watchdog + :8587...")
    py = str(root / "runtime" / "portable-python" / "python.exe")
    wds = str(root / "bin" / "ikaros-memory-watchdog.py")
    run_child(py, [wds, "--stop"], env, str(root / "bin"), False)
    kill_port(8587)
    kill_by_cmdline("ikaros-memory-watchdog.py")


def stop_component_local_model(root, env):
    log.info("[local_model] stopping (:8080)...")
    kill_port(8080)


def start_component_neko(root, env, wait):
    log.info("[neko] starting N.E.K.O frontend (:48911)...")
    neko_dir = root / "core/neko"
    if not neko_dir.exists():
        log.error("[neko] core/neko directory not found: %s", neko_dir)
        return
    py = str(neko_dir / ".venv" / "Scripts" / "python.exe")
    server = "app.main_server"  # 上游已将 main_server.py 重构为包 app/main_server
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    # NEKO_STORAGE_ANCHOR_ROOT: 重定向 Neko 的本地状态目录到项目内可写路径
    # 默认 $LOCALAPPDATA/N.E.K.O/state 被系统反勒索保护拦截
    neko_env = dict(env)
    neko_env["NEKO_STORAGE_ANCHOR_ROOT"] = str(root / "tmp" / "neko-state")
    spawn_hidden(py, ["-m", server], neko_env, str(neko_dir),
                 str(root / "data" / "logs" / "neko.log"))
    if wait:
        wait_for_port(48911, 60)


def stop_component_neko(root, env):
    log.info("[neko] stopping (:48911)...")
    kill_port(48911)
    kill_by_cmdline("main_server")



def start_component_neko_memory(root, env, wait):
    log.info("[neko_memory] starting N.E.K.O memory server (:48912)...")
    neko_dir = root / "core/neko"
    if not neko_dir.exists():
        log.error("[neko_memory] core/neko not found: %s", neko_dir)
        return
    py = str(neko_dir / ".venv" / "Scripts" / "python.exe")
    server = "app.memory_server"  # 上游已将 memory_server.py 重构为包
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    neko_env = dict(env)
    neko_env["NEKO_STORAGE_ANCHOR_ROOT"] = str(root / "tmp" / "neko-state")
    spawn_hidden(py, ["-m", server], neko_env, str(neko_dir), str(root / "data" / "logs" / "neko-memory.log"))
    if wait:
        wait_for_port(48912, 60)


def start_component_neko_agent(root, env, wait):
    log.info("[neko_agent] starting N.E.K.O agent server (:48915)...")
    neko_dir = root / "core/neko"
    if not neko_dir.exists():
        log.error("[neko_agent] core/neko not found: %s", neko_dir)
        return
    py = str(neko_dir / ".venv" / "Scripts" / "python.exe")
    server = "app.agent_server"  # 上游已将 agent_server.py 重构为包
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    neko_env = dict(env)
    neko_env["NEKO_STORAGE_ANCHOR_ROOT"] = str(root / "tmp" / "neko-state")
    spawn_hidden(py, ["-m", server], neko_env, str(neko_dir),
                 str(root / "data" / "logs" / "neko-agent.log"))


def stop_component_neko_agent(root, env):
    log.info("[neko_agent] stopping (:48915)...")
    kill_port(48915)
    kill_by_cmdline("agent_server")


def stop_component_neko_memory(root, env):
    log.info("[neko_memory] stopping (:48912)...")
    kill_port(48912)
    kill_by_cmdline("memory_server")


def start_component_neko_group(root, env, wait):
    """一键启动 neko 服务组（前端 + 记忆 + Agent）。"""
    log.info("[neko_group] starting all neko sub-services...")
    start_component_neko(root, env, wait)
    start_component_neko_memory(root, env, wait)
    start_component_neko_agent(root, env, wait)


def stop_component_neko_group(root, env):
    """停止 neko 服务组全部子服务。"""
    log.info("[neko_group] stopping all neko sub-services...")
    stop_component_neko(root, env)
    stop_component_neko_agent(root, env)
    stop_component_neko_memory(root, env)


def start_component_hermes_dashboard(root, env, wait):
    """启动 Hermes 控制台 (:9119)。

    必须用 hermes 自有的 venv python（hermes_cli 装在里面），且把 core/hermes
    顶层目录加进 PYTHONPATH —— hermes_cli 是 editable 安装外独立存在的顶层包，
    仅靠 venv site-packages 还解析不到它（之前用 portable-python 跑直接
    ModuleNotFoundError）。core/hermes/venv/Scripts/hermes.exe 是 uv 生成的废脚本
    （canonicalize script path 失败），同样不能用。
    """
    # ── 启动前补丁预检（需求 §9：没打就补上）──
    try:
        pre = ensure_hermes_patch_applied()
        if pre["ok"]:
            log.info("[hermes_dashboard] 启动前补丁预检：%s", pre["msg"])
        else:
            log.warning("[hermes_dashboard] 启动前补丁预检未自动完成：%s（仍继续启动）",
                        pre["msg"])
    except Exception:
        log_exception("hermes patch precheck")
    log.info("[hermes_dashboard] starting Hermes console (:9119)...")
    hermes_dir = root / "core" / "hermes"
    venv_py = hermes_dir / "venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        log.error("[hermes_dashboard] venv python not found: %s", venv_py)
        return
    cwd = str(root)
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    child_env = dict(env or {})
    # hermes_cli 在 core/hermes 顶层, 必须显式加入 sys.path
    hermes_root_str = str(hermes_dir)
    existing = child_env.get("PYTHONPATH", "")
    paths = [p for p in existing.split(";") if p]
    if hermes_root_str not in paths:
        paths.insert(0, hermes_root_str)
    child_env["PYTHONPATH"] = ";".join(paths)
    # 让 Hermes 内的 Ikaros V5 记忆提供方准确定位 IKAROS_ROOT
    # （core/hermes/plugins/memory/ikaros_v5 依赖此变量定位 E:/Ikaros/core/memory_v5）。
    # 不设时回退到 __file__.parents[5]，但显式设置更稳、避免静默“unavailable”。
    child_env["IKAROS_ROOT"] = str(root)
    log.info("[hermes_dashboard] use venv python=%s, PYTHONPATH=%s, IKAROS_ROOT=%s",
             venv_py, child_env["PYTHONPATH"], child_env["IKAROS_ROOT"])
    spawn_hidden(str(venv_py), ["-m", "hermes_cli.main", "dashboard", "--no-open"], child_env, cwd,
                 str(root / "data" / "logs" / "hermes-dashboard.log"))
    if wait:
        # Hermes 控制台启动时要自动构建 web UI（npm install + vite），hermes 更新后
        # 首次构建常 >40s；轮询是端口一通就提前返回，180s 只是上限，避免面板在
        # 构建完成前误判“启动失败”（进程其实仍在后台构建并随后绑定 9119）。
        wait_for_port(9119, 180)


def stop_component_hermes_dashboard(root, env):
    log.info("[hermes_dashboard] stopping (:9119)...")
    kill_port(9119)
    kill_by_cmdline("hermes_cli.main")


# ── Hermes 版本 / Ikaros 补丁 控制（需求 §9）──────────────────────────
def _git_hermes(args):
    """在 core/hermes 仓库内跑 git（隐藏窗口）。"""
    return subprocess.run(
        ["git", *args], cwd=str(HERMES_AGENT_DIR),
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )


def _parse_spec_pointers() -> "tuple[str, str] | None":
    """从 spec §0 解析 (upstream_tip, ikaros_commit)。"""
    if not HERMES_PATCH_SPEC.exists():
        return None
    import re
    t = HERMES_PATCH_SPEC.read_text(encoding="utf-8")
    up = re.search(r"\*\*Upstream tip\*\*[^\n]*?`([0-9a-f]{6,40})`", t)
    ik = re.search(r"\*\*Ikaros 补丁提交\*\*[^\n]*?`([0-9a-f]{6,40})`", t)
    if not up or not ik:
        return None
    return up.group(1), ik.group(1)


def _hermes_git_healthy() -> bool:
    """core/hermes 的 .git 是否健康（有 refs/heads/main 且 rev-parse 不 fallback 到父仓库）。

    当 refs/ 目录被删时，git 会向上爬到父仓库 E:\\Ikaros，导致所有 hermes git
    命令实际操作的是主仓库——必须提前检测并短路，否则补丁检测会误报。
    """
    # 1. refs/heads/main 必须存在（文件或 packed-refs）
    refs_dir = HERMES_AGENT_DIR / ".git" / "refs" / "heads"
    packed = HERMES_AGENT_DIR / ".git" / "packed-refs"
    if not refs_dir.exists() and not packed.exists():
        return False
    # 2. rev-parse 返回的 HEAD 不能是主仓库的 commit（用 toplevel 检测）
    toplevel = _git_hermes(["rev-parse", "--show-toplevel"]).stdout.strip()
    if toplevel and pathlib.Path(toplevel).resolve() != HERMES_AGENT_DIR.resolve():
        return False  # git 爬到了父仓库
    return True


def _hermes_patch_present() -> bool:
    """Ikaros 集成补丁是否已就位：grep HEAD 历史里的补丁提交（覆盖原提交 /
    分层 cherry-pick / 重打新提交 三种来源，比 is-ancestor 更稳）。"""
    out = _git_hermes(["log", "--oneline", "--grep",
                       "apply Ikaros integration patches", "HEAD"]).stdout
    return bool(out.strip())


def hermes_patch_status() -> dict:
    """返回 hermes 版本 / Ikaros 补丁状态，供 9100 面板显示与启动预检共用。"""
    res = {"head": None, "upstream_tip": None, "patch_applied": None,
           "dirty": False, "detail": "", "repo_healthy": True}

    # ── 前置检测：git 仓库完整性 ──
    if not _hermes_git_healthy():
        res["repo_healthy"] = False
        res["patch_applied"] = None
        res["dirty"] = False
        res["detail"] = ("hermes git 仓库损坏（refs/ 目录丢失），"
                         "需等网络恢复后重新 fetch upstream + 重打补丁。"
                         "工作树文件完好，不影响运行。")
        log.warning("[hermes] git repo damaged (refs/ missing) — "
                    "patch detection disabled, working tree intact")
        return res

    head = _git_hermes(["rev-parse", "--short", "HEAD"]).stdout.strip()
    if not head:
        res["repo_healthy"] = False
        res["detail"] = "无法读取 hermes HEAD（仓库异常？）"
        return res
    res["head"] = head
    ptr = _parse_spec_pointers()
    if ptr:
        res["upstream_tip"] = ptr[0]
    present = _hermes_patch_present()
    res["patch_applied"] = present
    res["detail"] = "Ikaros 补丁已就位" if present else "未打 Ikaros 补丁（建议补上）"
    # 工作树散落文件（允许本地 config.yaml）
    out = _git_hermes(["status", "--porcelain"]).stdout
    bad = [l for l in out.splitlines() if l.strip()
           and not (l[:2] == "??" and l[3:].strip() == "config.yaml")]
    res["dirty"] = bool(bad)
    return res


def ensure_hermes_patch_applied() -> dict:
    """启动 9119 前调用：若 Ikaros 补丁未打，直接把补丁 commit cherry-pick 到当前
    HEAD（轻量、不 fetch / 不 reset，适用「更新把补丁冲掉」常见场景）。
    冲突则 abort 并告警，但不阻塞启动。"""
    st = hermes_patch_status()
    if not st.get("repo_healthy"):
        return {"ok": True, "applied": False, "skipped": True,
                "msg": "hermes git 仓库损坏，跳过补丁检测（工作树完好，不影响运行）",
                "detail": st["detail"]}
    if st.get("patch_applied"):
        return {"ok": True, "applied": True,
                "msg": "Ikaros 补丁已就位", "detail": st["detail"]}
    ptr = _parse_spec_pointers()
    if not ptr:
        return {"ok": False, "applied": False,
                "msg": "找不到 spec 基线指针，无法自动补丁", "detail": st["detail"]}
    ikaros_commit = ptr[1]
    # 补丁 commit 对象是否存在于当前仓库（refs 损坏后对象可能也被 gc）
    obj_check = _git_hermes(["cat-file", "-t", ikaros_commit])
    if obj_check.returncode != 0:
        return {"ok": True, "applied": False, "skipped": True,
                "msg": (f"补丁 commit {ikaros_commit[:8]} 不在本地对象库中"
                        "（仓库历史丢失），需等网络恢复后重新 fetch + 打补丁。"
                        "工作树文件完好，不影响运行。"),
                "detail": obj_check.stderr.strip()[-200:]}
    log.info("[hermes_dashboard] 补丁缺失，启动前自动补上（cherry-pick %s）...",
             ikaros_commit[:8])
    rc = _git_hermes(["cherry-pick", ikaros_commit])
    if rc.returncode == 0:
        log.info("[hermes_dashboard] 已自动补上 Ikaros 补丁")
        return {"ok": True, "applied": True,
                "msg": "已自动补上 Ikaros 补丁", "detail": ""}
    _git_hermes(["cherry-pick", "--abort"])
    msg = ("自动补丁冲突（upstream 大改），请手动运行 "
           "bin/hermes-update-and-patch.py --apply")
    log.warning("[hermes_dashboard] %s", msg)
    tail = (rc.stderr or rc.stdout)[-400:]
    return {"ok": False, "applied": False, "msg": msg, "detail": tail}


def run_hermes_update_and_patch() -> dict:
    """9100「更新并打补丁」按钮：跑完整脚本（fetch+reset+cherry-pick+LLM兜底+
    验证+更新§0）。"""
    if not HERMES_PATCH_SCRIPT.exists():
        return {"ok": False, "msg": "找不到 bin/hermes-update-and-patch.py"}
    py = HERMES_AGENT_DIR / "venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = pathlib.Path(sys.executable)
    try:
        proc = subprocess.run(
            [str(py), str(HERMES_PATCH_SCRIPT), "--apply"],
            cwd=str(HERMES_ROOT), capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "更新超时（>15min），请检查网络 / 手动运行脚本"}
    out = (proc.stdout + "\n" + proc.stderr)[-2000:]
    st = hermes_patch_status()
    return {"ok": proc.returncode == 0,
            "msg": out,
            "patch_applied": st.get("patch_applied")}


# ── 上游仓库：存在性检查 + 浅克隆(最快通道) + 版本落后检测（hermes / neko）──
# 基础通道统一为浅克隆 git clone --depth 1 --filter blob:none（不拉历史，避免全量包）。
# 镜像前缀走环境变量 IKAROS_GIT_MIRROR；留空=直连 GitHub。设置示例：
#   set IKAROS_GIT_MIRROR=https://ghproxy.net/
GIT_MIRROR = (os.environ.get("IKAROS_GIT_MIRROR") or "").rstrip("/")
UPSTREAM_REPOS = {
    "hermes": {
        "name": "Hermes Agent",
        "url": "https://github.com/NousResearch/hermes-agent",
        "branch": "main",
        "local": HERMES_ROOT / "core" / "hermes",
    },
    "neko": {
        "name": "N.E.K.O",
        "url": "https://github.com/Project-N-E-K-O/N.E.K.O",
        "branch": "main",
        "local": HERMES_ROOT / "core" / "neko",
    },
}
_UPSTREAM_CACHE: dict = {}          # name -> {upstream_version, checked_at, error}
_UPSTREAM_TTL = 600                # 上游版本缓存 10 分钟（避免每次轮询打 GitHub）


def _mirror_url(url: str) -> str:
    return (GIT_MIRROR + "/" + url) if GIT_MIRROR else url


def _git_in(dir_path, args, **kw):
    return subprocess.run(["git", *args], cwd=str(dir_path),
                          capture_output=True, text=True,
                          creationflags=CREATE_NO_WINDOW, **kw)


# 内容检查：.git 存在但关键入口文件缺失 → 视为「内容不完整」（空克隆/部分拉取）
_CONTENT_MARKERS = {
    "neko": "app/main_server/__main__.py",
    "hermes": "hermes_cli/web_server.py",
}


def local_repo_version(name: str) -> dict:
    """本地仓库版本：最新 tag（按版本排序）或短 HEAD；含 dirty 标记与内容完整性。"""
    d = UPSTREAM_REPOS[name]["local"]
    if not (d / ".git").is_dir():
        return {"present": False, "version": None, "tag": None,
                "commit": None, "dirty": False, "content_ok": False, "error": "未克隆"}
    # 内容完整性：关键入口文件是否存在
    marker = _CONTENT_MARKERS.get(name)
    content_ok = True
    if marker:
        content_ok = (d / marker).is_file()
    out = _git_in(d, ["tag", "--sort=-v:refname"]).stdout.strip()
    tag = out.splitlines()[0] if out else None
    commit = _git_in(d, ["rev-parse", "--short", "HEAD"]).stdout.strip() or None
    # 允许本地 config.yaml 未跟踪（与 hermes_patch_status 一致）
    st = _git_in(d, ["status", "--porcelain"]).stdout
    bad = [l for l in st.splitlines() if l.strip()
           and not (l[:2] == "??" and l[3:].strip() == "config.yaml")]
    dirty = bool(bad)
    return {"present": True, "version": tag or commit, "tag": tag,
            "commit": commit, "dirty": dirty, "content_ok": content_ok, "error": ""}


def upstream_latest_tag(name: str) -> dict:
    """git ls-remote --tags 取上游最新语义版本 tag（仅列引用，网络开销极小）。"""
    url = _mirror_url(UPSTREAM_REPOS[name]["url"])
    try:
        rr = subprocess.run(["git", "ls-remote", "--tags", url],
                            capture_output=True, text=True, timeout=30,
                            creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if rr.returncode != 0:
        return {"ok": False, "error": (rr.stderr or "").strip()[:200] or "ls-remote 失败"}
    tags = []
    for line in rr.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.endswith("^{}"):
            continue
        tags.append(ref.split("/")[-1])
    if not tags:
        return {"ok": True, "tag": None, "tags": []}

    def vkey(t):
        m = re.search(r"(\d+(?:\.\d+)+)", t)
        return [int(x) for x in m.group(1).split(".")] if m else [0]

    tags_sorted = sorted(tags, key=vkey)
    return {"ok": True, "tag": tags_sorted[-1], "tags": tags_sorted}


def _ver_tuple(v):
    if not v:
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", v)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def refresh_upstream_versions(names=None, force=False):
    """后台刷新上游版本缓存（网络：git ls-remote）。"""
    names = names or list(UPSTREAM_REPOS)
    now = time.time()
    for name in names:
        cur = _UPSTREAM_CACHE.get(name)
        if (not force and cur and "checked_at" in cur
                and (now - cur["checked_at"] < _UPSTREAM_TTL)):
            continue
        res = upstream_latest_tag(name)
        entry = {"checked_at": now}
        if res.get("ok"):
            entry["upstream_version"] = res.get("tag")
            entry["error"] = ""
        else:
            entry["upstream_version"] = (cur or {}).get("upstream_version")
            entry["error"] = res.get("error", "")
        _UPSTREAM_CACHE[name] = entry


def repo_status(name: str) -> dict:
    """汇总：存在性 + 本地版本 + 缓存的上游版本 + 是否落后。"""
    spec = UPSTREAM_REPOS[name]
    local = local_repo_version(name)
    cache = _UPSTREAM_CACHE.get(name, {})
    upstream_version = cache.get("upstream_version")
    checking = "checked_at" not in cache
    behind = None
    if local.get("tag") and upstream_version:
        lt, ut = _ver_tuple(local["tag"]), _ver_tuple(upstream_version)
        if lt and ut:
            behind = lt < ut
    if not local.get("present"):
        status = "missing"
    elif not local.get("content_ok", True):
        status = "incomplete"
    elif checking:
        status = "checking"
    elif behind is True:
        status = "behind"
    elif behind is False:
        status = "latest"
    else:
        status = "unknown"
    return {
        "name": spec["name"], "present": local.get("present", False),
        "content_ok": local.get("content_ok", True),
        "local_version": local.get("version"), "local_tag": local.get("tag"),
        "local_commit": local.get("commit"), "dirty": local.get("dirty", False),
        "upstream_version": upstream_version, "behind": behind,
        "status": status, "checking": checking,
        "upstream_error": cache.get("error", ""),
        "url": spec["url"], "branch": spec["branch"],
    }


def clone_repo(name: str) -> dict:
    """不存在则浅克隆到本地落点（最快通道：浅克隆 + 可选镜像前缀）。"""
    spec = UPSTREAM_REPOS[name]
    d = spec["local"]
    if (d / ".git").is_dir():
        return {"ok": True, "already": True, "msg": f"{spec['name']} 已克隆，无需重复"}
    if d.exists() and any(d.iterdir()):
        return {"ok": False, "already": False,
                "msg": f"{d} 已存在非 git 目录，为避免覆盖已跳过（请手动清理后重试）"}
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth", "1", "--filter", "blob:none",
               "--tags", "-b", spec["branch"], _mirror_url(spec["url"]), str(d)]
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            creationflags=CREATE_NO_WINDOW)
        if rr.returncode != 0:
            return {"ok": False, "already": False,
                    "msg": "克隆失败：" + ((rr.stderr or rr.stdout)[-500:])}
        refresh_upstream_versions([name], force=True)
        return {"ok": True, "already": False, "msg": f"已克隆 {spec['name']} → {d}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "already": False,
                "msg": "克隆超时（>10min），请检查网络或镜像"}
    except Exception as e:
        return {"ok": False, "already": False, "msg": f"克隆异常：{e}"}


def pull_repo(name: str) -> dict:
    """已存在则拉取最新；不存在则克隆。Hermes 含 Ikaros 补丁，不强行 ff 拉取。"""
    spec = UPSTREAM_REPOS[name]
    d = spec["local"]
    if not (d / ".git").is_dir():
        return clone_repo(name)
    if name == "hermes":
        # Hermes 打了 Ikaros 集成补丁，ff-only pull 必冲突；提示走专用更新流程
        return {"ok": True, "already": True,
                "msg": "Hermes 已安装且含 Ikaros 补丁；更新请点「更新并打补丁」"}
    try:
        rr = subprocess.run(["git", "pull", "--ff-only"], cwd=str(d),
                            capture_output=True, text=True, timeout=300,
                            creationflags=CREATE_NO_WINDOW)
        subprocess.run(["git", "fetch", "--tags"], cwd=str(d),
                       capture_output=True, text=True, timeout=120,
                       creationflags=CREATE_NO_WINDOW)
        refresh_upstream_versions([name], force=True)
        if rr.returncode != 0:
            return {"ok": False,
                    "msg": "拉取失败（非快进，可能本地有改动）："
                           + ((rr.stderr or rr.stdout)[-400:])}
        return {"ok": True, "msg": f"{spec['name']} 已拉取最新"}
    except Exception as e:
        return {"ok": False, "msg": f"拉取异常：{e}"}


# ── 运行时依赖检查（runtime/ 目录下的必要二进制）─────────────────────
# type:
#   always  — 核心依赖，缺失必须提示手动获取（仓库无自动下载 URL）
#   fetch   — 可由 scripts/fetch-upstreams.py 真实拉取（MCP 等）
#   optional— 可选组件，缺失仅提示
RUNTIME_DEPS = [
    {"key": "python",   "name": "Portable Python", "rel": "runtime/portable-python/python.exe",
     "type": "always",  "hint": "从 Ikaros 发布包解压 portable-python 到 runtime/portable-python/（或运行 scripts/setup-native.py 引导）"},
    {"key": "node",     "name": "Node.js",         "rel": "runtime/node/node.exe",
     "type": "always",  "hint": "从发布包解压 node 到 runtime/node/"},
    {"key": "llama",    "name": "llama.cpp (CUDA)", "rel": "runtime/llama/b10000-cuda/llama-server.exe",
     "type": "always",  "hint": "从发布包解压 llama/b10000-cuda 到 runtime/llama/"},
    {"key": "gopeed",   "name": "gopeed 下载器",    "rel": "runtime/gopeed/gopeed-web.exe",
     "type": "always",  "hint": "从发布包解压 gopeed 到 runtime/gopeed/"},
    {"key": "aria2",    "name": "aria2 下载器",     "rel": "runtime/aria2/aria2c.exe",
     "type": "always",  "hint": "从发布包解压 aria2 到 runtime/aria2/"},
    {"key": "mcpserve", "name": "MCP Serve",        "rel": "runtime/MCPServe",
     "type": "fetch",   "fetch": "mcp-codebase-memory",
     "hint": "点「拉取」运行 scripts/fetch-upstreams.py mcp-codebase-memory"},
    {"key": "herdr",    "name": "Herdr 终端编排",   "rel": "runtime/herdr/herdr.exe",
     "type": "optional", "hint": "可选：从发布包解压 herdr 到 runtime/herdr/"},
]


def runtime_status() -> dict:
    """检查 runtime/ 目录与必要二进制是否存在；缺失项给手动提示。"""
    rt = HERMES_ROOT / "runtime"
    comps = []
    missing = 0
    for d in RUNTIME_DEPS:
        p = HERMES_ROOT / d["rel"]
        ok = p.exists()
        if not ok:
            missing += 1
        comps.append({
            "key": d["key"], "name": d["name"], "rel": d["rel"],
            "type": d["type"], "ok": ok, "hint": d["hint"],
            "fetch": d.get("fetch"),
        })
    return {
        "runtime_dir_exists": rt.is_dir(),
        "components": comps,
        "missing": missing,
        "total": len(comps),
        "fetchable": [c["key"] for c in comps if c["type"] == "fetch"],
    }


def runtime_fetch(key: str) -> dict:
    """对可由 fetch-upstreams.py 拉取的依赖执行下载。"""
    dep = next((d for d in RUNTIME_DEPS if d["key"] == key), None)
    if not dep:
        return {"ok": False, "msg": f"未知依赖: {key}"}
    if dep.get("type") != "fetch" or not dep.get("fetch"):
        return {"ok": False, "msg": "该依赖无自动下载，请手动获取：" + (dep.get("hint") or "")}
    try:
        cmd = [sys.executable, str(HERMES_ROOT / "scripts" / "fetch-upstreams.py"), dep["fetch"]]
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            creationflags=CREATE_NO_WINDOW)
        if rr.returncode != 0:
            return {"ok": False, "msg": "拉取失败：" + ((rr.stderr or rr.stdout)[-500:])}
        return {"ok": True, "msg": f"已拉取 {dep['name']} → {dep['rel']}"}
    except Exception as e:
        return {"ok": False, "msg": f"拉取异常：{e}"}


def neko_desktop_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq N.E.K.O.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        return "N.E.K.O.exe" in out
    except Exception:
        return False


def start_component_neko_desktop(root, env, wait):
    """启动 N.E.K.O Electron 桌面壳（GUI 程序，需可见窗口，不隐藏）。"""
    log.info("[neko_desktop] launching N.E.K.O desktop shell...")
    exe = root / "core/neko" / "N.E.K.O.exe"
    if not exe.exists():
        log.error("[neko_desktop] %s not found", exe)
        return
    # 先清僵尸：等待所有残留进程实际终止，避免重复积累
    killed = kill_image_wait("N.E.K.O.exe", timeout=5.0)
    if killed:
        log.info("[neko_desktop] cleaned %d stale process(es)", killed)
    try:
        p = subprocess.Popen([str(exe)], env=dict(env), stdin=DEVNULL)
        log.info("[neko_desktop] launched pid=%s", p.pid)
    except Exception as e:
        log.error("[neko_desktop] failed to launch: %s", e)
    time.sleep(2)
    if neko_desktop_running():
        log.info("[neko_desktop] started")
    else:
        log.warning("[neko_desktop] may not have started")


def stop_component_neko_desktop(root, env):
    log.info("[neko_desktop] stopping...")
    killed = kill_image_wait("N.E.K.O.exe", timeout=5.0)
    if killed:
        log.info("[neko_desktop] stopped %d process(es)", killed)


def start_component_qwenpaw(root, env, wait):
    """启动猫爪服务器 (:8088)。
    默认拉起项目内 Hermes-Paw 桥 (bin/hermes_paw_bridge.py): 伪装成 QwenPaw,
    内部用 Hermes Agent 执行 Neko 的猫爪指令 (无需安装原生 QwenPaw)。
    若设了 QWENPAW_CMD 则优先用它 (兼容原生 QwenPaw 服务)。"""
    log.info("[qwenpaw] starting Cat-Paw server (:8088)...")
    cmd = (os.environ.get("QWENPAW_CMD") or (env or {}).get("QWENPAW_CMD") or "").strip()
    if not cmd:
        bridge = os.path.join(root, "bin", "hermes_paw_bridge.py")
        hermes_py = (
            os.environ.get("HERMES_AGENT_PYTHON")
            or r"E:\Ikaros\core\hermes\venv\Scripts\python.exe"
        )
        if os.path.exists(bridge):
            cmd = hermes_py + " " + bridge
            log.info("[qwenpaw] QWENPAW_CMD 未设, 默认拉起 Hermes-Paw 桥 (复用 Hermes Agent)")
        else:
            log.warning(
                "[qwenpaw] 桥不存在且 QWENPAW_CMD 未设, 请在宿主机手动启动猫爪服务 (:8088)"
            )
            return
    # 构造子进程环境: Hermes Agent 模型名 + hermes 根 + 端口
    # 模型名优先级: 环境变量 HERMES_PAW_MODEL > panel_models.json 的 "8088"
    #             > 默认 deepseek-v4-flash（不再写死，可在面板配置里覆盖）。
    child_env = dict(env or {})
    _panel_models = load_panel_models()
    resolved_model = (
        os.environ.get("HERMES_PAW_MODEL")
        or (isinstance(_panel_models, dict) and _panel_models.get("8088"))
        or "deepseek-v4-flash"
    )
    child_env["HERMES_PAW_MODEL"] = resolved_model
    # base_url 透传: 默认不设置 -> Hermes Agent 用自身默认 provider；
    # 若配置了则走指定 OpenAI 兼容网关。
    resolved_base_url = (
        os.environ.get("HERMES_PAW_BASE_URL")
        or (isinstance(_panel_models, dict) and _panel_models.get("8088_base_url"))
        or ""
    )
    if resolved_base_url:
        child_env["HERMES_PAW_BASE_URL"] = resolved_base_url
    child_env.setdefault("HERMES_AGENT_ROOT", r"E:\Ikaros\core\hermes")
    child_env.setdefault("HERMES_PAW_PORT", "8088")
    parts = cmd.split()
    try:
        p = subprocess.Popen(
            parts, env=child_env, stdin=DEVNULL,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log.info("[qwenpaw] launched: %s (pid=%s)", cmd, p.pid)
    except Exception as e:
        log.error("[qwenpaw] failed to launch %s: %s", cmd, e)
        return
    if wait:
        wait_for_port(8088, 60)


def stop_component_qwenpaw(root, env):
    log.info("[qwenpaw] stopping (:8088)...")
    kill_port(8088)


def start_component_conversation_tree(root, env, wait):
    """启动对话树面板后端 (:48920)，便携 Python 跑 core/conversation-tree/server.py。"""
    log.info("[conversation_tree] starting panel server (:48920)...")
    py = str(root / "runtime" / "portable-python" / "python.exe")
    server = root / "core" / "conversation-tree" / "server.py"
    if not server.exists():
        log.error("[conversation_tree] %s not found", server)
        return
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    spawn_hidden(py, [str(server), "--port", "48920"], env, cwd=str(root),
                 logfile=str(root / "data" / "logs" / "conversation-tree.log"))
    if wait:
        wait_for_port(48920, 30)


def stop_component_conversation_tree(root, env):
    log.info("[conversation_tree] stopping (:48920)...")
    kill_port(48920)
    kill_by_cmdline("conversation-tree")


def start_component_herdr(root, env, wait):
    """启动 herdr headless server（命名管道，无 TCP 端口）。"""
    log.info("[herdr] starting headless server...")
    binp = root / "runtime" / "herdr" / "herdr.exe"
    if not binp.exists():
        log.error("[herdr] binary not found: %s", binp)
        return
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    spawn_hidden(str(binp), ["server"], env, cwd=str(root / "data" / "logs"),
                 logfile=str(root / "data" / "logs" / "herdr.log"))
    if wait:
        time.sleep(3)
        if _marker_up("herdr.exe"):
            log.info("[herdr] server up")
        else:
            log.warning("[herdr] server not detected after start")


def stop_component_herdr(root, env):
    """停止 herdr server（SKILL.md 警告：别从活跃会话里误停 server）。"""
    log.info("[herdr] stopping server...")
    binp = str(root / "runtime" / "herdr" / "herdr.exe")
    run_child(binp, ["server", "stop"], env, str(root / "data" / "logs"), False)
    kill_by_cmdline("herdr.exe")
    log.info("[herdr] stop signaled")


def comp_running(name: str) -> bool:
    if name == "local_model":
        return tcp_probe(8080)
    if name == "memory":
        return tcp_probe(8587)
    if name == "neko_group":
        return tcp_probe(48911) and tcp_probe(48912) and tcp_probe(48915)
    return False


def comp_already_up(name: str) -> bool:
    if name == "local_model":
        return tcp_probe(8080)
    if name == "memory":
        return tcp_probe(8587)
    if name == "neko":
        return tcp_probe(48911)
    if name == "neko_memory":
        return tcp_probe(48912)
    if name == "neko_agent":
        return tcp_probe(48915)
    if name == "hermes_dashboard":
        return tcp_probe(9119)
    if name == "neko_desktop":
        return neko_desktop_running()
    if name == "qwenpaw":
        return tcp_probe(8088)
    if name == "conversation_tree":
        return tcp_probe(48920)
    if name == "herdr":
        return _marker_up("herdr.exe")
    if name == "neko_group":
        return tcp_probe(48911) and tcp_probe(48912) and tcp_probe(48915)
    return False


def component_start(name: str, env: dict, wait: bool) -> None:
    _ensure_env()  # 确保 ENV 已初始化
    root = ROOT
    if name == "local_model":
        start_component_local_model(root, env, wait)
    elif name == "memory":
        start_component_memory(root, env, wait)
    elif name == "neko_group":
        start_component_neko_group(root, env, wait)
    elif name == "neko":
        start_component_neko(root, env, wait)
    elif name == "neko_memory":
        start_component_neko_memory(root, env, wait)
    elif name == "neko_agent":
        start_component_neko_agent(root, env, wait)
    elif name == "hermes_dashboard":
        start_component_hermes_dashboard(root, env, wait)
    elif name == "neko_desktop":
        start_component_neko_desktop(root, env, wait)
    elif name == "qwenpaw":
        start_component_qwenpaw(root, env, wait)
    elif name == "conversation_tree":
        start_component_conversation_tree(root, env, wait)
    elif name == "herdr":
        start_component_herdr(root, env, wait)
    elif name == "all":
        start_component_local_model(root, ENV, wait)
        start_component_memory(root, ENV, wait)
        start_component_neko_group(root, ENV, wait)
        start_component_hermes_dashboard(root, ENV, wait)
        start_component_neko_desktop(root, ENV, wait)
        start_component_qwenpaw(root, ENV, wait)
    else:
        log.warning("[component] unknown component: %s", name)


def component_stop(name: str, env: dict) -> None:
    _ensure_env()  # 确保 ENV 已初始化
    root = ROOT
    if name == "local_model":
        stop_component_local_model(root, env)
    elif name == "memory":
        stop_component_memory(root, env)
    elif name == "neko_group":
        stop_component_neko_group(root, env)
    elif name == "neko":
        stop_component_neko(root, env)
    elif name == "neko_memory":
        stop_component_neko_memory(root, env)
    elif name == "neko_agent":
        stop_component_neko_agent(root, env)
    elif name == "hermes_dashboard":
        stop_component_hermes_dashboard(root, env)
    elif name == "neko_desktop":
        stop_component_neko_desktop(root, env)
    elif name == "qwenpaw":
        stop_component_qwenpaw(root, env)
    elif name == "conversation_tree":
        stop_component_conversation_tree(root, env)
    elif name == "herdr":
        stop_component_herdr(root, env)
    elif name == "all":
        stop_component_neko_group(root, env)
        stop_component_neko_desktop(root, env)
        stop_component_hermes_dashboard(root, env)
        stop_component_memory(root, env)
        stop_component_local_model(root, env)
        stop_component_qwenpaw(root, env)
    else:
        log.warning("[component] unknown component: %s", name)


def boot_profile() -> None:
    """Auto-start components per BOOT_PROFILE on control panel launch."""
    env = _ensure_env()
    for cid in BOOT_PROFILE:
        try:
            if comp_already_up(cid):
                log.info("boot: %s already running, skipping", cid)
                continue
            log.info("boot: starting %s ...", cid)
            component_start(cid, env, False)
        except Exception:
            log_exception(f"boot {cid} failed")
        time.sleep(1)
    log.info("boot: done")


def run_component_action(name: str, action: str) -> bool:
    """原生派发 start/stop/restart。后台线程执行，避免阻塞 HTTP 请求。"""
    if name not in KNOWN_IDS or action not in VALID_ACTIONS:
        return False

    def _do():
        try:
            env = _ensure_env()
            if action == "restart":
                component_stop(name, env)
                time.sleep(2)
                component_start(name, env, False)
            elif action == "start":
                component_start(name, env, False)
            elif action == "stop":
                component_stop(name, env)
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


# 缓存 V5 模块导入，避免每次 /api/state 都重新 import 刷屏日志
_v5_modules: dict = {}

def _read_v5_state() -> dict:
    """Read affect.json + latest_thought.json + Ikaros memory stats."""
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

    # Ikaros V5 memory stats — 首次导入后缓存
    try:
        if "v5_store" not in _v5_modules:
            import sys as _sys
            memory_root = str(ROOT / "core/memory_v5")
            if memory_root not in _sys.path:
                _sys.path.insert(0, memory_root)
            from memory_v5.store import stats as v5_stats
            from memory_v5.validation import registry_summary
            from memory_v5.entity_graph import entity_graph_stats
            _v5_modules["v5_store"] = v5_stats
            _v5_modules["v5_validation"] = registry_summary
            _v5_modules["v5_entity_graph"] = entity_graph_stats
        state["memory"] = _v5_modules["v5_store"]()
        state["entity_graph"] = _v5_modules["v5_entity_graph"]()
        state["validation"] = _v5_modules["v5_validation"]()
    except Exception:
        state["memory"] = None
        state["entity_graph"] = None
        state["validation"] = None

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


def _marker_up(substr: str) -> bool:
    """进程命令行是否包含 substr（herdr 等无 TCP 端口的组件用此探测）。"""
    return any(substr.lower() in pc for pc in _running_command_lines())


def get_component_statuses() -> list[dict]:
    """Probe every component and return its live status.

    端口探测改为并发 (线程池), 总耗时约等于单次 tcp_probe 超时而非逐端口累加,
    避免 down 端口多时请求被拖到 6s+ 且在并发访问下雪崩。
    """
    procs = _running_command_lines()
    # 收集所有待探测端口并并发探查
    all_ports: list[int] = []
    for c in COMPONENTS:
        all_ports.extend(c["ports"])
    port_up_map: dict[int, bool] = {}
    if all_ports:
        with ThreadPoolExecutor(max_workers=min(32, len(all_ports))) as ex:
            port_up_map = dict(zip(all_ports, ex.map(tcp_probe, all_ports)))
    result: list[dict] = []
    for c in COMPONENTS:
        ports_up = [p for p in c["ports"] if port_up_map.get(p, False)]
        marker_hits = [m for m in c["markers"] if any(m.lower() in pc for pc in procs)]
        running = bool(ports_up) or bool(marker_hits)
        entry = {
            "id": c["id"],
            "name": c["name"],
            "category": c["category"],
            "desc": c["desc"],
            "ports": c["ports"],
            "ports_up": ports_up,
            "running": running,
            "marker_hits": marker_hits,
            "detail": ("端口 " + ",".join(str(p) for p in ports_up) + " 在线") if ports_up
                      else ("进程: " + ", ".join(marker_hits) if marker_hits else "未运行"),
            "panel_url": c.get("panel_url", ""),
            "group": bool(c.get("group", False)),
            "subcomponents": c.get("subcomponents", []),
            "hidden": bool(c.get("hidden", False)),
            "parent_group": c.get("parent_group", ""),
        }
        # 模型切换信息（local_model / memory）
        if c.get("model_kind"):
            kind = c["model_kind"]
            port = c["ports"][0]
            entry["model_kind"] = kind
            entry["current_model"] = current_model_for_port(port)
            entry["models"] = list_models(kind)
        # 服务组：聚合子组件运行状态
        if c.get("group"):
            subs = {}
            for sub in c["subcomponents"]:
                subs[sub] = comp_already_up(sub)
            entry["sub_status"] = subs
            entry["running"] = all(subs.values())
            entry["partial"] = (not all(subs.values())) and any(subs.values())
        # Hermes 版本 / 补丁状态（需求 §9）
        if c["id"] == "hermes_dashboard":
            try:
                entry["hermes_patch"] = hermes_patch_status()
            except Exception:
                log_exception("hermes_patch_status")
        # 上游仓库存在性 + 版本落后检测（hermes / neko 克隆与版本检查）
        if c["id"] in ("hermes_dashboard", "neko_group"):
            try:
                entry["repo"] = repo_status(
                    "hermes" if c["id"] == "hermes_dashboard" else "neko")
            except Exception:
                log_exception("repo_status")
        # 运行时依赖检查（runtime/ 目录下的必要二进制）
        if c["id"] == "runtime":
            try:
                entry["runtime"] = runtime_status()
            except Exception:
                log_exception("runtime_status")
        result.append(entry)
    return result


# ── SSE helpers ────────────────────────────────────────────────────────

def _sse_event(wfile, data: dict, event: str | None = None) -> None:
    """Write one SSE event to *wfile*."""
    payload = json.dumps(data, ensure_ascii=False)
    if event:
        wfile.write(f"event: {event}\n".encode())
    wfile.write(f"data: {payload}\n\n".encode())
    wfile.flush()


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
        # 自动给 CSS 链接注入内容哈希, 强制浏览器在文件变动时刷新(避免旧 CSS 缓存导致"改了没变化")
        css_file = ASSETS_DIR / "dashboard.css"
        if css_file.is_file():
            try:
                h = hashlib.md5(css_file.read_bytes()).hexdigest()[:10]
                body = body.replace(b"/assets/dashboard.css",
                                     ("/assets/dashboard.css?v=" + h).encode("utf-8"))
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # 关键: HTML 文档必须带 no-cache, 否则浏览器启发式缓存旧文档 —
        # 旧文档里的 CSS ?v= 哈希不变, 导致 CSS 永不重载, 表现为"改了没变化"。
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _STATIC_CT = {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }

    def _send_static(self, rel_path: str) -> None:
        """Serve files under core/dashboard/assets/ (path-traversal guarded)."""
        if ".." in rel_path or rel_path.startswith("/") or rel_path.startswith("\\"):
            self.send_error(400, "bad path")
            return
        asset_dir = HERE / "assets"
        target = (asset_dir / rel_path).resolve()
        try:
            target.relative_to(asset_dir.resolve())
        except ValueError:
            self.send_error(403, "forbidden")
            return
        if not target.is_file():
            self.send_error(404, "not found")
            return
        ctype = self._STATIC_CT.get(target.suffix.lower(), "application/octet-stream")
        try:
            with open(str(target), "rb") as f:
                body = f.read()
        except OSError as e:
            self.send_error(500, str(e))
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
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
        elif path.startswith("/assets/"):
            self._send_static(path[len("/assets/"):])
        elif path == "/api/components":
            self._send_json(get_component_statuses())
        elif path == "/api/models":
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                port = int(qs.get("port", ["0"])[0])
            except (ValueError, IndexError):
                port = 0
            if port not in (8080, 8587):
                self._send_json({"ok": False, "msg": "invalid or missing port"}, status=400)
                return
            kind = "llm" if port == 8080 else "embed"
            self._send_json({
                "ok": True,
                "port": port,
                "kind": kind,
                "current": current_model_for_port(port),
                "models": list_models(kind),
            })
        elif path == "/api/log":
            events = _read_tail(200)
            self._send_json(events)
        elif path == "/api/state":
            state = _read_v5_state()
            self._send_json(state)
        elif path == "/api/events":
            self._handle_sse()
        elif path == "/api/hermes/status":
            self._send_json(hermes_patch_status())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        # /api/models  → 切换指定端口的模型（POST {port, model}）
        if len(parts) >= 2 and parts[0] == "api" and parts[1] == "models":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                body = {}
            try:
                port = int(body.get("port", 0))
            except (TypeError, ValueError):
                port = 0
            model = str(body.get("model", ""))
            if port not in (8080, 8587):
                self._send_json({"ok": False, "msg": "invalid or missing port"}, status=400)
                return
            if not model:
                self._send_json({"ok": False, "msg": "missing model"}, status=400)
                return
            ok, msg = switch_model(port, model)
            self._send_json({"ok": ok, "msg": msg, "port": port, "model": model})
            return

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

        # /api/restart → respawn the dashboard itself
        if len(parts) >= 2 and parts[0] == "api" and parts[1] == "restart":
            self._send_json({"ok": True, "msg": "控制面板正在重启"})
            def _restart():
                """Spawn a fresh dashboard, then stop this one."""
                py = sys.executable
                script = str(HERE / "server.py")
                log.info("[dashboard] respawning: %s %s", py, script)
                subprocess.Popen(
                    [py, script],
                    env=_ensure_env(),
                    cwd=str(HERMES_ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0,
                )
                time.sleep(2)
                self.server.shutdown_later()
            threading.Thread(target=_restart, daemon=True).start()
            return

        # /api/hermes/<action>  (status | check | update)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "hermes":
            sub = parts[2]
            if sub == "status":
                self._send_json(hermes_patch_status()); return
            if sub == "check":
                self._send_json(ensure_hermes_patch_applied()); return   # 检查并自动打补丁
            if sub == "update":
                self._send_json(run_hermes_update_and_patch()); return
            self._send_json({"ok": False, "msg": "unknown hermes action"}, status=400)
            return

        # /api/repo/<name>/<action>  (status | clone | pull)
        # name ∈ {hermes, neko}；status=强制刷新上游版本, clone=缺失则克隆, pull=拉取/克隆
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "repo":
            name = parts[2]
            action = parts[3]
            if name not in UPSTREAM_REPOS:
                self._send_json({"ok": False, "msg": "unknown repo"}, status=400)
                return
            if action == "status":
                refresh_upstream_versions([name], force=True)
                self._send_json({"ok": True, **repo_status(name)})
                return
            if action == "clone":
                self._send_json(clone_repo(name))
                return
            if action == "pull":
                self._send_json(pull_repo(name))
                return
            self._send_json({"ok": False, "msg": "unknown repo action"}, status=400)
            return

        # /api/runtime/status          (POST) -> runtime_status()
        # /api/runtime/fetch/<key>     (POST) -> runtime_fetch(key)
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "runtime":
            if parts[2] == "status":
                self._send_json(runtime_status())
                return
            if parts[2] == "fetch" and len(parts) >= 4:
                self._send_json(runtime_fetch(parts[3]))
                return
            self._send_json({"ok": False, "msg": "unknown runtime action"}, status=400)
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
    _ensure_env()  # 确保 ENV 在 serve 前初始化
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

    # 进程级代理隔离(方案B): 注入 NO_PROXY=* 让整套 Ikaros 栈 + dev Neko 不继承
    # Windows 系统 socks 代理(socks://127.0.0.1:8086 非法, httpx 报 scheme 不支持)。
    # 只影响本面板拉起的子进程, 不动系统代理, Steam 官方 Neko 零影响。
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

    # 启动即检查 hermes / neko 上游版本（需求：启动时检查版本号是否落后）
    threading.Thread(target=refresh_upstream_versions, daemon=True).start()

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
