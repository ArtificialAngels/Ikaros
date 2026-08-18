#!/usr/bin/env python3
"""ikaros-dashboard — Ikaros control panel backend (stdlib only, no third-party deps).

This service is the backend for the Ikaros control panel (launched by
bin/ikaros-control-panel.bat). It does two things:

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
  GET  /api/state         -> V5 affect.json + 自我思考 latest_thought (JSON)
  GET  /api/events        -> SSE real-time event stream
  POST /api/components/<id>/<action>   action in {start,stop,restart}
  POST /api/system/<action>            action in {start,stop}  (all components)
  POST /api/shutdown                     stop this control panel service
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
import functools as _functools
from concurrent.futures import ThreadPoolExecutor
import urllib.parse

# Windows: 隐藏子进程控制台窗口
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
# 让子进程脱离父进程控制台会话：关闭父进程(面板/PowerShell)不影响子进程存活。
# DETACHED_PROCESS 与 CREATE_NEW_CONSOLE 互斥，需单独标志位。
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
DEVNULL = subprocess.DEVNULL

# ── config ─────────────────────────────────────────────────────────────
PORT = 9100
# 项目根：环境变量优先，兜底用脚本位置推导（可整体迁移盘符，不依赖 E:/F: 硬编码）
HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(
    os.environ.get("IKAROS_ROOT")
    or HERE.parent.parent
).resolve()
MONITOR_FILE = ROOT / "data" / "logs" / "ikaros-monitor.jsonl"
AFFECT_FILE = ROOT / "core/memory_v5" / "data" / "v5" / "affect.json"
LATEST_THOUGHT_FILE = ROOT / "core/memory_v5" / "data" / "v5" / "latest_thought.json"
INDEX_HTML = HERE / "index.html"
ASSETS_DIR = HERE / "assets"

POLL_INTERVAL = 0.8  # seconds between file polls for SSE

# 双击启动时不自动拉任何组件，全部手动启停。
BOOT_PROFILE: list[str] = ["local_model", "memory"]

# Component registry — 控制面板的「有哪些组件」事实来源。
# `ports` 用 TCP 探测；`markers` 用进程命令行子串匹配。任一命中即视为 running。
COMPONENTS = [
    {"id": "local_model", "name": "本地模型 (Local LLM)", "category": "Backend",
     "desc": "本地大语言模型 :8080（可切换模型）", "ports": [8080],
     "model_kind": "llm", "markers": ["llama-server.exe"]},
    {"id": "memory", "name": "Memory Service", "category": "Backend",
     "desc": "Embedding 向量服务 :8587（可切换模型）", "ports": [8587],
     "model_kind": "embed", "markers": ["ikaros-memory-watchdog.py", "llama-server.exe"]},
    {"id": "conversation_tree", "name": "对话树面板 (Conversation Tree)", "category": "Frontend",
     "desc": "Explore.poker 风格树形对话面板 :48920（后端 = conversation_tree 引擎）",
     "ports": [48920], "markers": ["conversation-tree"],
     "panel_url": "http://127.0.0.1:48920/"},
    {"id": "dsh", "name": "工作引擎 (DSH)", "category": "Backend",
     "desc": "DeepSeek Harness 底座 :3080 —— Web GUI（--patch 加载 Ikaros overlay: "
             "memory_v5 MCP + 终端 + LSP + persona）；headless 模式跑 one-shot 任务",
     "ports": [3080], "markers": ["dsh"],
     "panel_url": "http://127.0.0.1:3080/"},
    {"id": "herdr", "name": "Herdr 终端编排", "category": "Backend",
     "desc": "coding-agent 终端多路复用器 (headless server，命名管道，无 TCP 端口)",
     "ports": [], "markers": ["herdr.exe"],
     "panel_url": "http://127.0.0.1:48920/"},
    {"id": "runtime", "name": "运行时依赖", "category": "依赖",
     "desc": "runtime/ 下的必要二进制（Python / Node / llama / 下载器 / MCP / Herdr）；缺失项提示手动获取",
     "ports": [], "markers": [], "check_only": True},
]
VALID_ACTIONS = {"start", "stop", "restart"}
KNOWN_IDS = {c["id"] for c in COMPONENTS} | {"all"}

# 全局环境（组件启动时使用）
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
    e["IKAROS_MEMORY"] = s(root / "core/memory_v5")
    e["IKAROS_MEMORY_DATA"] = s(root / "core/memory_v5" / "data")
    e["IKAROS_MEMORY_MODELS"] = s(root / "core/memory_v5" / "models")
    e["IKAROS_MEMORY_SCRIPT"] = s(root / "core/memory_v5" / "store.py")
    e["IKAROS_NODE_MODULES"] = s(root / "runtime" / "node" / "node_modules")
    e["IKAROS_RUST"] = s(root / "runtime" / "rust")
    # omp (pi coding-agent) 便携配置: 配置目录锚定项目 data/omp/agent, 不落 C 盘 ~/.omp
    # PI_CODING_AGENT_DIR 走 path.resolve (绝对路径覆盖); PI_CONFIG_DIR 走 path.join 遇绝对路径不重置, 勿用
    e["IKAROS_OMP_AGENT"] = s(root / "data" / "omp" / "agent")
    e["PI_CODING_AGENT_DIR"] = e["IKAROS_OMP_AGENT"]
    e["IKAROS_MODEL_EMBEDDING"] = s(root / "core/memory_v5" / "models" / "bge-m3-q8_0.gguf")
    e["IKAROS_MODEL_LLM"] = s(root / "core/memory_v5" / "models" / "Phi-4-mini-instruct-Q4_K_M.gguf")
    e["IKAROS_LABEL_EMOTION_PROVIDER"] = os.environ.get("IKAROS_LABEL_EMOTION_PROVIDER", "local")
    # API_SERVER_KEY: 优先取根 .env；
    # 对话树等子进程经此注入同一 key 保持一致性）。
    _api_key = os.environ.get("API_SERVER_KEY", "")
    if not _api_key:
        try:
            _envf = root / ".env"
            if _envf.is_file():
                for _line in _envf.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if _line.startswith("API_SERVER_KEY=") and not _line.startswith("#"):
                        _api_key = _line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except Exception:
            _api_key = ""
    e["API_SERVER_KEY"] = _api_key or "ikaros-gateway-key"

    llama_ver = os.environ.get("IKAROS_LLAMA_VERSION")
    if not llama_ver:
        # 默认按设备 CUDA 能力解析（12.x → b10000-cuda-12.4；13.x → b10000-cuda）
        # 仅当 resolver 判定可安全使用 CUDA build 时才写入子进程环境；
        # 否则留空，让子进程（watchdog）自行兜底（-ngl 0 / CPU build）。
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "llama_resolver", str(HERE.parent.parent / "core" / "env" / "llama_resolver.py"))
            _lr = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_lr)
            _res = _lr.resolve_llama_dir(root)
            if not _res.get("cpu_fallback"):
                llama_ver = _res["version"]
        except Exception:
            llama_ver = None
    e["IKAROS_LLAMA_VERSION"] = llama_ver or ""
    llama_dir = root / "runtime" / "llama" / (llama_ver or "b10000-cuda")
    e["IKAROS_LLAMA_DIR"] = s(llama_dir)
    if llama_ver:
        e["IKAROS_LLAMA_SERVER"] = s(llama_dir / "llama-server.exe")
    # CPU 兜底场景（llama_ver 为空）：不写 IKAROS_LLAMA_SERVER，
    # 让子进程（watchdog）自行 resolver 选择可用 build / -ngl 0。
    e["IKAROS_MODEL_EMBEDDING"] = s(root / "core/memory_v5" / "models" / "bge-m3-q8_0.gguf")

    e["IKAROS_PORT_EMBEDDING"] = "8587"
    e["IKAROS_PORT_LLM"] = "8080"
    e["IKAROS_PORT_BRIDGE"] = "7860"
    e["IKAROS_PORT_LIVE2D_WEBVIEW"] = "8648"
    e["IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL"] = "8649"
    e["IKAROS_PORT_LLAMA"] = "8080"

    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    e["PYTHONPATH"] = s(root)
    e["NODE_PATH"] = s(root / "runtime" / "node" / "node_modules")

    # dsh 工作引擎 overlay（core/ikaros-dsh/cordis.patch.yml）
    e["IKAROS_DSH_PATCH"] = s(root / "core" / "ikaros-dsh" / "cordis.patch.yml")
    e["IKAROS_DSH_PROFILE_DIR"] = s(root / "data" / "dsh" / "profiles")

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
                 flags: int = CREATE_NO_WINDOW,
                 detached: bool = False) -> subprocess.Popen | None:
    """隐藏窗口启动子进程；logfile 非空则把输出重定向到该文件。

    flags 默认为 CREATE_NO_WINDOW。对于需要控制台句柄的进程（如某些后台
    服务初始化时依赖 TTY），可传入 CREATE_NEW_CONSOLE。

    detached=True 时用 CREATE_NO_WINDOW + CREATE_NEW_PROCESS_GROUP 启动：
    隐藏窗口 + 独立进程组。父进程（9100 面板 / 启动它的 PowerShell）关闭后
    子进程照常存活。**不要用 DETACHED_PROCESS**——它只对第一层进程生效，
    目标进程内部再 spawn 的 console 子进程会因无父 console 新建窗口弹窗。
    """
    stdout = stderr = DEVNULL
    if logfile:
        try:
            os.makedirs(os.path.dirname(logfile), exist_ok=True)
            f = open(logfile, "wb")
            stdout = stderr = f
        except Exception:
            log_exception("spawn_hidden open logfile")
    effective_flags = flags
    if detached:
        # detached: CREATE_NO_WINDOW 隐藏窗口 + CREATE_NEW_PROCESS_GROUP 独立进程组。
        # 2026-08-01 23:3x 修正：此前用 DETACHED_PROCESS 只对第一层进程生效——
        # 子进程(console 程序)因父无 console 会新建 console
        # 弹窗(白窗标题=python.exe 路径)。CREATE_NO_WINDOW 是"隐藏窗口"语义，整棵
        # 进程树都继承无窗口属性，不再弹窗；配合 CREATE_NEW_PROCESS_GROUP 使子进程
        # 独立于父进程组，父进程(面板/pythonw)退出不影响服务存活。
        effective_flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        p = subprocess.Popen(
            [cmd, *args],
            env=env,
            cwd=cwd,
            stdin=DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=effective_flags,
        )
        log.debug("spawn: pid=%s cmd=%s flags=%s detached=%s", p.pid, cmd, effective_flags, detached)
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
    default = {"8080": "Phi-4-mini-instruct-Q4_K_M.gguf",
               "8587": "bge-m3-q8_0.gguf"}
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
                "--pooling", "cls", "--alias", "bge-m3"]
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

    model = current_model_for_port(8587) or "bge-m3-q8_0.gguf"
    if not (MODELS_DIR / model).is_file():
        avail = list_models("embed")
        model = avail[0] if avail else model
    spawn_llama_model(8587, model, "embed")
    log.info("[memory] embed model=%s, waiting for :8587...", model)

    # 后台启动 watchdog 做巡查（detached: 脱离面板控制台，关面板/PowerShell 不带走）
    py = str(root / "runtime" / "portable-python" / "python.exe")
    wds = str(root / "bin" / "ikaros-memory-watchdog.py")
    spawn_hidden(py, [wds], env, str(root / "bin"),
                 str(root / "data" / "logs" / "memory-watchdog.log"),
                 detached=True)

    if wait:
        if wait_for_port(8587, 80):
            log.info("[memory] embed :8587 ready")
        else:
            log.warning("[memory] embed :8587 timeout — 可能需要修复 llama-server")


def start_component_local_model(root, env, wait):
    """启动本地模型 (:8080)：加载面板选中的 LLM。默认懒加载，
    面板可显式拉起；若未运行则 agent 调用时热载入。"""
    log.info("[local_model] starting local LLM (:8080)...")
    model = current_model_for_port(8080) or "Phi-4-mini-instruct-Q4_K_M.gguf"
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

def _ttl_cache(seconds: float):
    def deco(fn):
        _store: dict[tuple, tuple] = {}  # args_key -> (t, value)
        @_functools.wraps(fn)
        def wrapper(*a, **kw):
            key = (a, tuple(sorted(kw.items())))
            now = time.time()
            hit = _store.get(key)
            if hit is None or (now - hit[0]) > seconds:
                _store[key] = (now, fn(*a, **kw))
                hit = _store[key]
            return hit[1]
        wrapper.cache_clear = _store.clear
        _CACHE_REGISTRY.add(wrapper)
        return wrapper
    return deco


_CACHE_REGISTRY: set = set()


def _clear_status_caches() -> None:
    """组件启停操作后调用：清空全部 TTL 缓存，下次轮询立刻看到新状态。"""
    for w in list(_CACHE_REGISTRY):
        try:
            w.cache_clear()
        except Exception:
            pass


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
    rt = ROOT / "runtime"
    comps = []
    missing = 0
    for d in RUNTIME_DEPS:
        p = ROOT / d["rel"]
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
        cmd = [sys.executable, str(ROOT / "scripts" / "fetch-upstreams.py"), dep["fetch"]]
        rr = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            creationflags=CREATE_NO_WINDOW)
        if rr.returncode != 0:
            return {"ok": False, "msg": "拉取失败：" + ((rr.stderr or rr.stdout)[-500:])}
        return {"ok": True, "msg": f"已拉取 {dep['name']} → {dep['rel']}"}
    except Exception as e:
        return {"ok": False, "msg": f"拉取异常：{e}"}


def start_component_dsh(root, env, wait):
    """启动工作引擎 DeepSeek Harness (dsh) Web GUI (:3080)。

    经 bin/start-dsh-ikaros.bat web 拉起（--patch 加载 Ikaros overlay:
    memory_v5 MCP + 终端 + LSP + persona）。headless 模式用命令行跑 one-shot 任务。
    """
    log.info("[dsh] starting work-engine web GUI (:3080)...")
    launcher = root / "bin" / "start-dsh-ikaros.bat"
    if not launcher.exists():
        log.error("[dsh] %s not found", launcher)
        return
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    spawn_hidden("cmd.exe", ["/c", str(launcher), "web"], env, cwd=str(root),
                 logfile=str(root / "data" / "logs" / "dsh.log"),
                 detached=True)
    if wait:
        wait_for_port(3080, 60)


def stop_component_dsh(root, env):
    log.info("[dsh] stopping (:3080)...")
    kill_port(3080)
    kill_by_cmdline("dsh")


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
                 logfile=str(root / "data" / "logs" / "conversation-tree.log"),
                 detached=True)
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
                 logfile=str(root / "data" / "logs" / "herdr.log"),
                 detached=True)
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
    return False


@_ttl_cache(5)
def comp_already_up(name: str) -> bool:
    if name == "local_model":
        return tcp_probe(8080)
    if name == "memory":
        return tcp_probe(8587)
    if name == "conversation_tree":
        return tcp_probe(48920)
    if name == "dsh":
        return tcp_probe(3080)
    if name == "herdr":
        return _marker_up("herdr.exe")
    return False


def component_start(name: str, env: dict, wait: bool) -> None:
    _ensure_env()  # 确保 ENV 已初始化
    root = ROOT
    if name == "local_model":
        start_component_local_model(root, env, wait)
    elif name == "memory":
        start_component_memory(root, env, wait)
    elif name == "conversation_tree":
        start_component_conversation_tree(root, env, wait)
    elif name == "dsh":
        start_component_dsh(root, env, wait)
    elif name == "herdr":
        start_component_herdr(root, env, wait)
    elif name == "all":
        start_component_local_model(root, ENV, wait)
        start_component_memory(root, ENV, wait)
        start_component_dsh(root, ENV, wait)
    else:
        log.warning("[component] unknown component: %s", name)


def component_stop(name: str, env: dict) -> None:
    _ensure_env()  # 确保 ENV 已初始化
    root = ROOT
    if name == "local_model":
        stop_component_local_model(root, env)
    elif name == "memory":
        stop_component_memory(root, env)
    elif name == "conversation_tree":
        stop_component_conversation_tree(root, env)
    elif name == "dsh":
        stop_component_dsh(root, env)
    elif name == "herdr":
        stop_component_herdr(root, env)
    elif name == "all":
        stop_component_memory(root, env)
        stop_component_local_model(root, env)
        stop_component_dsh(root, env)
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
# ── V5 状态读取 ──
# (Cron/Kanban 管理已随 Hermes 底座退役移除；任务调度改由 dsh/系统级 cron 承担)


# 缓存 V5 模块导入，避免每次 /api/state 都重新 import 刷屏日志
_v5_modules: dict = {}

def _read_v5_state() -> dict:
    """Read affect.json + Ikaros memory stats."""
    state: dict = {}
    for path, key in [(AFFECT_FILE, "affect")]:
        if path.exists():
            try:
                with open(str(path), "r", encoding="utf-8") as f:
                    state[key] = json.load(f)
            except Exception:
                state[key] = None
        else:
            state[key] = None

    # 自我思考（metacog 实时产出 latest_thought.json；替代已废弃的 pending_thought.json）
    if LATEST_THOUGHT_FILE.exists():
        try:
            with open(str(LATEST_THOUGHT_FILE), "r", encoding="utf-8") as f:
                state["thought"] = json.load(f)
        except Exception:
            state["thought"] = None
    else:
        state["thought"] = None

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


@_ttl_cache(8)
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
            "panel": bool(c.get("panel", False)),
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
                # 复用已并发探测的 port_up_map，避免串行 tcp_probe 拖慢轮询
                sub_cfg = next((x for x in COMPONENTS if x["id"] == sub), None)
                if sub_cfg and sub_cfg.get("ports"):
                    subs[sub] = any(port_up_map.get(p, False) for p in sub_cfg["ports"])
                else:
                    subs[sub] = comp_already_up(sub)
            entry["sub_status"] = subs
            entry["running"] = all(subs.values())
            entry["partial"] = (not all(subs.values())) and any(subs.values())
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
                _clear_status_caches()
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
                _clear_status_caches()
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
                    cwd=str(ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == 'nt' else 0,
                )
                time.sleep(2)
                self.server.shutdown_later()
            threading.Thread(target=_restart, daemon=True).start()
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

    # 进程级代理隔离(方案B): 注入 NO_PROXY=* 让整套 Ikaros 栈不继承系统代理
    # Windows 系统 socks 代理(socks://127.0.0.1:8086 非法, httpx 报 scheme 不支持)。
    # 只影响本面板拉起的子进程, 不动系统代理。
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
