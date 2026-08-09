#!/usr/bin/env python3
"""ikaros_* 自救工具集 — Hermes gateway 独立通道 (对话树 :48920 侧直接执行)。

背景 (鸡生蛋问题):
  Ikaros 挂在对话树面板上, MCP 工具全部由 Hermes gateway(:8642) 透出。
  gateway 崩溃 → 工具链全断 → Ikaros 无法诊断 gateway → 只能等人工重启。
  本模块提供 **不经过 gateway 透出** 的自救工具:

  1. ikaros_ps             进程诊断 (tasklist, 关键字过滤)
  2. ikaros_port           端口诊断 (netstat, 已知服务全景 / 单端口)
  3. ikaros_gateway_status 综合健康检查 (:8642 /health + 端口 + 进程 + 日志尾)
  4. ikaros_read_log       日志读取 (白名单路径, tail N 行)
  5. ikaros_read_config    配置读取 (白名单路径, .env 脱敏)
  6. ikaros_restart_gateway 服务控制 (kill :8642 → venv python 拉起 → 轮询就绪)
  7. ikaros_herdr          herdr 命名管道兜底 (ping / pane_list / pane_read / run 白名单)

双挂载:
  A. 降级链 (gateway 挂): core/conversation-tree/server.py 直接 import 本模块,
     把 SCHEMAS 并入 _READONLY_TOOLS, 经 call() 本地执行 —— gateway 挂了照用。
  B. 正常态 (gateway 活): 注册为 hermes MCP server (config.yaml mcp_servers 加
     ikaros-rescue 条目), 由 gateway 以 stdio 子进程挂载, 工具名 ikaros_*。
     实现与 gateway 内部逻辑零耦合, 只是借 MCP 宿主透出。

安全约束:
  - 全部工具只读/白名单命令; 不提供任意 shell (历史 terminal 工具崩过 gateway)。
  - 重启只针对 gateway (:8642), 用 hermes venv python + 完整 env (复刻
    tmp/restart-hermes-stack.py build_env 规范, 不 import 面板模块)。
  - herdr run 走命令白名单 (别名), 不开放任意命令。

环境: 无第三方依赖 (stdlib + 可选 mcp SDK); portable-python 与 hermes venv 均可运行。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("IKAROS_ROOT", r"E:/Ikaros")).resolve()
HERMES = ROOT / "core" / "hermes"
VENV_PY = HERMES / "venv" / "Scripts" / "python.exe"
PP = ROOT / "runtime" / "portable-python"
HERMES_HOME = ROOT / "data" / "hermes-agent"

GATEWAY_URL = "http://127.0.0.1:8642"
GATEWAY_HEALTH = GATEWAY_URL + "/health"
GATEWAY_LOG = ROOT / "tmp" / "gateway8642.log"

# 已知服务端口表 (诊断全景用)
KNOWN_PORTS: dict[int, str] = {
    8642: "Hermes gateway",
    8650: "Hermes bridge (对话树默认通道)",
    48920: "对话树面板",
    9119: "Hermes dashboard",
    8088: "Hermes-Paw 猫爪",
    8080: "本地 LLM (Qwen3-1.7B)",
    8587: "Embedding (nomic)",
    48911: "Neko main",
    48912: "Neko memory",
    48915: "Neko agent",
}

# ── 白名单路径 ──────────────────────────────────────────────────────────
LOG_WHITELIST: dict[str, str] = {
    "gateway": str(GATEWAY_LOG),
    "gateway8642": str(GATEWAY_LOG),
    "dashboard": str(ROOT / "tmp" / "dashboard9119.log"),
    "dashboard9119": str(ROOT / "tmp" / "dashboard9119.log"),
    "bridge": str(ROOT / "data" / "logs" / "hermes-bridge.log"),
    "hermes-bridge": str(ROOT / "data" / "logs" / "hermes-bridge.log"),
    "conversation-tree": str(ROOT / "data" / "logs" / "conversation-tree.log"),
    "hermes-dashboard": str(ROOT / "data" / "logs" / "hermes-dashboard.log"),
    "herdr": str(ROOT / "data" / "logs" / "herdr.log"),
}
CONFIG_WHITELIST: dict[str, str] = {
    "config": str(HERMES_HOME / "config.yaml"),
    "hermes-config": str(HERMES_HOME / "config.yaml"),
    "env": str(HERMES_HOME / ".env"),
    "hermes-env": str(HERMES_HOME / ".env"),
    "pyproject": str(HERMES / "pyproject.toml"),
}
# patches/hermes/ 整目录可读 (工具集规范源)
PATCHES_DIR = ROOT / "patches" / "hermes"

# herdr run 命令白名单 (别名 → 完整命令; 全部为已知恢复路径, 不开放任意命令)
HERDR_RUN_WHITELIST: dict[str, str] = {
    "restart_hermes_stack":
        f'"{PP / "python.exe"}" "{ROOT / "tmp" / "restart-hermes-stack.py"}"',
}

# ── 通用小工具 ──────────────────────────────────────────────────────────

def _decode(data: bytes) -> str:
    """中文 Windows 子进程输出 GBK, 先按 utf-8 再按 gbk 容错解码。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def _run(argv: list[str], timeout: int = 15) -> tuple[int, str]:
    """运行子进程并返回 (rc, 输出)。失败返回 (非0, 错误信息)。"""
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, (_decode(p.stdout) + _decode(p.stderr)).strip()
    except FileNotFoundError as e:
        return 127, f"命令不存在: {e}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时 ({timeout}s)"
    except Exception as e:
        return 1, f"执行失败: {e}"


def _tail(path: str | Path, lines: int) -> str:
    """读取文件末尾 lines 行 (cap 500)。"""
    lines = max(1, min(int(lines), 500))
    try:
        p = Path(path)
        data = p.read_bytes()
        text = _decode(data)
        parts = text.splitlines()
        return "\n".join(parts[-lines:]) if parts else "(空日志)"
    except FileNotFoundError:
        return f"(日志不存在: {path})"
    except Exception as e:
        return f"(读日志失败: {e})"


def _redact(text: str) -> str:
    """脱敏: 把 KEY=VALUE 形式的值打码 (避免把 API key 喂给模型)。"""
    return re.sub(r"(?i)^(\s*[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*)(.+)$",
                  lambda m: m.group(1) + "***", text, flags=re.M)


# ── 1. 进程诊断 ─────────────────────────────────────────────────────────

def _run_ps(pattern: str = "") -> str:
    rc, out = _run(["tasklist", "/FO", "CSV"])
    if rc != 0:
        return out or "(tasklist 失败)"
    lines = out.splitlines()
    if len(lines) < 2:
        return "(无进程输出)"
    # CSV 解析: "name","pid","session","#","mem"
    rows = []
    for ln in lines[1:]:
        try:
            csv = next(iter(__import__("csv").reader([ln])))
            if len(csv) < 2:
                continue
            rows.append((csv[0], csv[1], csv[-1]))
        except Exception:
            continue
    pat = (pattern or "").strip().lower()
    if pat:
        rows = [r for r in rows if pat in r[0].lower() or pat == r[1]]
    if not rows:
        return f"(未找到匹配进程: {pattern or '*'} )"
    head = f"共 {len(rows)} 个进程" + (f" (过滤: {pattern})" if pat else "") + "\n"
    body = "\n".join(f"PID={r[1]:<8} {r[0]:<24} 内存={r[2]}" for r in rows[:60])
    more = f"\n...(共 {len(rows)} 行, 已截断)" if len(rows) > 60 else ""
    return head + body + more


# ── 2. 端口诊断 ─────────────────────────────────────────────────────────

def _parse_listeners() -> dict[int, int]:
    """返回 {port: pid}, 从 netstat -ano 提取 LISTENING 行。"""
    rc, out = _run(["netstat", "-ano"], timeout=20)
    if rc != 0:
        return {}
    result: dict[int, int] = {}
    for ln in out.splitlines():
        if "LISTENING" not in ln:
            continue
        parts = ln.split()
        if len(parts) < 5:
            continue
        addr = parts[1]
        if addr.startswith("0.0.0.0:") or addr.startswith("127.0.0.1:") or addr.startswith("[::]:"):
            port_s = addr.rsplit(":", 1)[-1]
            if port_s.isdigit() and parts[-1].isdigit():
                result[int(port_s)] = int(parts[-1])
    return result


def _run_port(port: str = "") -> str:
    listeners = _parse_listeners()
    if port:
        p = int(port)
        if p in listeners:
            name = KNOWN_PORTS.get(p, "")
            return (f":{p} 正在监听 (PID {listeners[p]})" +
                    (f" — {name}" if name else ""))
        return f":{p} 未监听" + (f" — {KNOWN_PORTS.get(p, '')}".rstrip(" —"))
    if not listeners:
        return "(netstat 无 LISTENING 输出)"
    rows = []
    for p, pid in sorted(listeners.items()):
        if p in KNOWN_PORTS:
            rows.append(f":{p:<6} PID={pid:<7} {KNOWN_PORTS[p]}")
    return "\n".join(rows) if rows else "(已知服务端口均未监听)"


# ── 3. 综合健康检查 ─────────────────────────────────────────────────────

def _http_get(url: str, timeout: float = 2.5) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(500).decode("utf-8", "replace")
            return resp.status, body[:300]
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _run_gateway_status() -> str:
    lines = ["── Hermes gateway (:8642) 健康检查 ──"]
    code, body = _http_get(GATEWAY_HEALTH)
    if 0 < code < 500:
        lines.append(f"GET /health → {code}  (进程活着)")
    else:
        lines.append(f"GET /health → {code}  (不可达) {body}")
    listeners = _parse_listeners()
    gw_pid = listeners.get(8642)
    lines.append(f"端口 :8642 {'监听 PID ' + str(gw_pid) if gw_pid else '未监听'}")
    br_pid = listeners.get(8650)
    lines.append(f"端口 :8650 (bridge) {'监听 PID ' + str(br_pid) if br_pid else '未监听'}")
    # 进程侧验证 (tasklist 找 gateway 相关 python)
    rc, out = _run(["tasklist", "/FO", "CSV"])
    if rc == 0:
        pats = [l for l in out.splitlines()[1:]
                if "python.exe" in l or "hermes" in l.lower()]
        lines.append(f"python/hermes 进程数: {len(pats)}")
    lines.append(f"gateway 日志: {GATEWAY_LOG}")
    tail = _tail(GATEWAY_LOG, 3)
    lines.append("日志尾部:\n" + ("\n".join("  " + t for t in tail.splitlines())))
    return "\n".join(lines)


# ── 4. 日志读取 ─────────────────────────────────────────────────────────

def _run_read_log(log: str, lines: int = 150) -> str:
    key = (log or "").strip().lower()
    if key in LOG_WHITELIST:
        path = LOG_WHITELIST[key]
    else:
        # 允许直接给绝对路径, 但必须落在白名单路径集合内 (防任意文件读取)
        try:
            cand = Path(log).resolve()
        except Exception:
            return f"(非法日志标识: {log}; 可用: {', '.join(LOG_WHITELIST)})"
        allowed = {str(Path(v).resolve()) for v in LOG_WHITELIST.values()}
        if str(cand) not in allowed:
            return f"(拒绝: 不在日志白名单; 可用标识: {', '.join(LOG_WHITELIST)})"
        path = str(cand)
    return _tail(path, lines)


# ── 5. 配置读取 ─────────────────────────────────────────────────────────

def _run_read_config(config: str) -> str:
    key = (config or "").strip().lower()
    if key in CONFIG_WHITELIST:
        path = CONFIG_WHITELIST[key]
    elif key:
        # 允许 patches/hermes/ 目录下任意相对/绝对路径 (工具集规范源)
        try:
            p = Path(config)
            if not p.is_absolute():
                p = PATCHES_DIR / p
            p = p.resolve()
            if PATCHES_DIR.resolve() not in p.parents:
                return f"(拒绝: 路径不在 patches/hermes/ 下; 可用: {', '.join(CONFIG_WHITELIST)})"
            path = str(p)
        except Exception:
            return f"(非法配置路径: {config}; 可用: {', '.join(CONFIG_WHITELIST)})"
    else:
        path = CONFIG_WHITELIST["config"]
    try:
        data = Path(path).read_bytes()
        if len(data) > 200_000:
            data = data[:200_000]
            text = _decode(data) + "\n...(超过 200KB, 已截断)"
        else:
            text = _decode(data)
        text = _redact(text)
        return f"── {path} ({len(data)} bytes) ──\n{text}"
    except FileNotFoundError:
        return f"(配置不存在: {path})"
    except Exception as e:
        return f"(读配置失败: {e})"


# ── 6. 服务控制: 重启 gateway ───────────────────────────────────────────

def _build_env() -> dict:
    """复刻 tmp/restart-hermes-stack.py build_env(): gateway 启动所需完整 env。

    必须合并 os.environ (否则 HOME/USERPROFILE 缺失 → hermes Path.home() 崩溃);
    必须显式设 HERMES_HOME / IKAROS_* / PYTHONPATH / API_SERVER_KEY。
    """
    e: dict = dict(os.environ)
    e["IKAROS_ROOT"] = str(ROOT)
    e["IKAROS_PYTHON"] = str(PP / "python.exe")
    e["IKAROS_RUNTIME"] = str(ROOT / "runtime")
    e["IKAROS_DATA"] = str(ROOT / "data")
    e["IKAROS_BIN"] = str(ROOT / "bin")
    e["IKAROS_HERMES_AGENT"] = str(HERMES)
    e["IKAROS_HERMES_HOME"] = str(HERMES_HOME)
    e["HERMES_ROOT"] = str(ROOT)
    e["HERMES_HOME"] = str(HERMES_HOME)
    e["HERMES_BIN"] = str(HERMES / "venv" / "Scripts" / "hermes.exe")
    e["HERMES_AGENT_CLI_PYTHON"] = str(VENV_PY)
    e["HERMES_AGENT_BRIDGE_PYTHON"] = str(VENV_PY)
    e["IKAROS_MEMORY"] = str(ROOT / "core" / "memory_v5")
    e["IKAROS_MEMORY_DATA"] = str(ROOT / "core" / "memory_v5" / "data")
    e["IKAROS_MEMORY_SCRIPT"] = str(ROOT / "core" / "memory_v5" / "v5" / "store.py")
    e["HERMES_TUI_DIR"] = str(HERMES / "ui-tui")
    e["PYTHONPATH"] = str(ROOT) + ";" + str(HERMES)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    key = ""
    envf = HERMES_HOME / ".env"
    if envf.is_file():
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("API_SERVER_KEY=") and not line.startswith("#"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    e["API_SERVER_KEY"] = key or "ikaros-gateway-key"
    path_parts = [str(ROOT / "runtime" / "rust" / "bin"),
                  str(ROOT / "runtime" / "llama" / "b10000-cuda"),
                  str(ROOT / "runtime"), str(ROOT / "runtime" / "node"),
                  str(PP / "Scripts"), str(PP)]
    old = e.get("PATH", "")
    if old:
        path_parts.append(old)
    e["PATH"] = ";".join(path_parts)
    return e


def _kill_port(port: int) -> list[str]:
    killed: list[str] = []
    listeners = _parse_listeners()
    pid = listeners.get(port)
    if pid:
        try:
            os.kill(pid, 9)
            killed.append(f":{port} PID {pid} (SIGKILL)")
            # 等旧进程完全消失, 确保其文件句柄 (auth.lock/state.db 等) 已释放,
            # 否则新 gateway 可能因抢锁 PermissionError 早期退出
            _wait_pid_gone(pid, timeout=12.0)
        except Exception as ex:
            killed.append(f":{port} PID {pid} 杀不掉: {ex}")
    return killed


def _wait_pid_gone(pid: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
        gone = ("没有运行" in out or "No tasks" in out or "找不到" in out
                or f"{pid}" not in out)
        if gone:
            return True
        time.sleep(0.5)
    return False


def _run_restart_gateway() -> str:
    if not VENV_PY.is_file():
        return f"(hermes venv python 缺失: {VENV_PY})"
    out: list[str] = []
    # 1) 杀旧进程 (占住 :8642 的 PID) 并等其彻底退出
    killed = _kill_port(8642)
    out.extend(killed or ["(未发现占用 :8642 的进程)"])
    time.sleep(1)
    # 2) 用 hermes venv python 拉起 gateway (cwd=core/hermes); 早退则重试一次
    log = GATEWAY_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    env = _build_env()
    proc = None
    for attempt in (1, 2):
        try:
            fh = open(log, "a", encoding="utf-8", errors="replace")
        except OSError:
            fh = open(os.devnull, "w")
        try:
            proc = subprocess.Popen(
                [str(VENV_PY), "-m", "hermes_cli.main", "gateway", "run", "--replace"],
                cwd=str(HERMES), env=env, stdout=fh, stderr=subprocess.STDOUT,
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "DETACHED_PROCESS", 0)),
            )
        except Exception as e:
            return "\n".join(out) + f"\ngateway 拉起失败: {e}"
        out.append(f"第 {attempt} 次拉起 gateway (PID {proc.pid})")
        # 前 10s 内若进程即退 (常见: 与垂死旧进程抢 auth.lock), 重试
        early_dead = None
        for _ in range(20):
            if _http_get(GATEWAY_HEALTH, timeout=1.0)[0] in (200, 404):
                break
            if proc.poll() is not None:
                early_dead = proc.returncode
                break
            time.sleep(0.5)
        if early_dead is None:
            break
        out.append(f"进程 10s 内退出 (code={early_dead}), 重试...")
        time.sleep(2)
    # 3) 轮询健康 (冷启动 MCP 初始化 1-8 分钟; HTTP 就绪即视为进程恢复)
    if _wait_health(300):
        out.append(":8642 健康检查通过 (HTTP 就绪; MCP 工具集仍在加载, 数分钟内全量可用)")
        return "\n".join(out)
    if proc is not None and proc.poll() is not None:
        out.append(f"gateway 进程提前退出 (code={proc.returncode}) — 见 {log}")
    else:
        out.append(f"gateway 进程存活但 300s 内 :8642 未就绪 — 见 {log}")
        out.append("建议: 用 ikaros_gateway_status 复查状态, 或用 ikaros_read_log 看启动日志")
    return "\n".join(out)


def _wait_health(timeout: float = 300.0) -> bool:
    """轮询 :8642 /health 就绪. 冷启动 MCP 初始化较慢 (通常 1-8 分钟),
    默认等 300s; 超时由调用方给出可操作提示 (复查 / 看日志)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, _ = _http_get(GATEWAY_HEALTH, timeout=2.0)
        if 0 < code < 500:
            return True
        time.sleep(2)
    return False


def _run_restart_gateway() -> str:
    if not VENV_PY.is_file():
        return f"(hermes venv python 缺失: {VENV_PY})"
    out: list[str] = []
    # 1) 杀旧进程 (占住 :8642 的 PID)
    killed = _kill_port(8642)
    out.extend(killed or ["(未发现占用 :8642 的进程)"])
    time.sleep(1)
    # 2) 用 hermes venv python 拉起 gateway (cwd=core/hermes)
    log = GATEWAY_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    env = _build_env()
    try:
        fh = open(log, "a", encoding="utf-8", errors="replace")
    except OSError:
        fh = open(os.devnull, "w")
    try:
        proc = subprocess.Popen(
            [str(VENV_PY), "-m", "hermes_cli.main", "gateway", "run", "--replace"],
            cwd=str(HERMES), env=env, stdout=fh, stderr=subprocess.STDOUT,
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "DETACHED_PROCESS", 0)),
        )
    except Exception as e:
        return "\n".join(out) + f"\ngateway 拉起失败: {e}"
    out.append(f"已拉起 gateway (PID {proc.pid})")
    # 3) 轮询健康 (冷启动 MCP 初始化 1-8 分钟; HTTP 就绪即视为进程恢复)
    if _wait_health(300):
        out.append(":8642 健康检查通过 (HTTP 就绪; MCP 工具集仍在加载, 数分钟内全量可用)")
        return "\n".join(out)
    if proc.poll() is not None:
        out.append(f"gateway 进程提前退出 (code={proc.returncode}) — 见 {log}")
    else:
        out.append(f"gateway 进程存活但 300s 内 :8642 未就绪 — 见 {log}")
        out.append("建议: 用 ikaros_gateway_status 复查状态, 或用 ikaros_read_log 看启动日志")
    return "\n".join(out)


# ── 7. herdr 命名管道兜底 ───────────────────────────────────────────────

def _herdr_client(timeout: float = 10.0):
    """构造 HerdrClient; 失败抛异常由上层转错误信息。"""
    sys.path.insert(0, str(ROOT))
    from core.herdr import HerdrClient  # noqa: PLC0415
    return HerdrClient(timeout=timeout)


def _run_herdr(action: str, pane_id: str = "", command: str = "", lines: int = 120) -> str:
    act = (action or "ping").strip().lower()
    try:
        c = _herdr_client()
        if act in ("ping", "health", "status"):
            r = c.ping()
            return json.dumps(r, ensure_ascii=False, indent=2)
        if act in ("list", "pane_list"):
            r = c.pane_list()
            return json.dumps(r, ensure_ascii=False, indent=2, default=str)
        if act in ("read", "pane_read"):
            if not pane_id:
                return "(herdr read 需要 pane_id)"
            r = c.pane_read(pane_id, lines=int(lines))
            return json.dumps(r, ensure_ascii=False, indent=2, default=str)
        if act in ("run", "exec", "pane_run"):
            if not pane_id:
                return "(herdr run 需要 pane_id; 用 pane_list 先看可用 pane)"
            cmd = (command or "").strip()
            full = HERDR_RUN_WHITELIST.get(cmd)
            if full is None:
                return (f"(命令不在白名单: {cmd!r}; 可用别名: "
                        f"{', '.join(HERDR_RUN_WHITELIST)})")
            r = c.pane_send_text(pane_id, full + "\r\n")
            return json.dumps(r, ensure_ascii=False, default=str) + \
                f"\n(已在 pane {pane_id} 执行 {cmd})"
        return f"(未知 action: {act}; 可用: ping | list | read | run)"
    except Exception as e:
        return f"(herdr 通道不可用: {type(e).__name__}: {e})"


# ── OpenAI 格式 schema + 分派 (对话树降级链用) ──────────────────────────

SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "ikaros_ps",
        "description": "进程诊断: 列出本机进程 (按关键字过滤, 如 gateway/hermes/python)。"
                       "当需要确认 hermes gateway 进程是否存活、找 PID 时调用。",
        "parameters": {"type": "object",
                       "properties": {"pattern": {"type": "string",
                                                  "description": "进程名/关键字过滤, 留空=全部"}}}}},
    {"type": "function", "function": {
        "name": "ikaros_port",
        "description": "端口诊断: 查指定端口是否在监听 (如 8642=gateway), 或列出全部已知服务端口状态。"
                       "当 gateway 疑似挂掉、需要确认 :8642 是否 LISTENING 时调用。",
        "parameters": {"type": "object",
                       "properties": {"port": {"type": "string",
                                               "description": "端口号, 留空=已知服务全景"}}}}},
    {"type": "function", "function": {
        "name": "ikaros_gateway_status",
        "description": "综合健康检查: :8642 /health + 端口监听 + 进程数 + 日志尾部, 一键判定"
                       "Hermes gateway 是否存活及故障线索。诊断 gateway 故障时优先调用。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "ikaros_read_log",
        "description": "读取 Hermes 相关运行日志尾部 (白名单): gateway / dashboard / bridge / "
                       "conversation-tree / herdr。gateway 日志确切路径 "
                       "E:/Ikaros/tmp/gateway8642.log。定位崩溃原因时调用。",
        "parameters": {"type": "object",
                       "properties": {
                           "log": {"type": "string",
                                   "description": "日志标识: gateway | dashboard | bridge | "
                                                  "conversation-tree | herdr"},
                           "lines": {"type": "integer", "description": "读取行数, 默认150"}}}}},
    {"type": "function", "function": {
        "name": "ikaros_read_config",
        "description": "读取 hermes 配置文件 (白名单): config=hermes config.yaml, env=hermes .env "
                       "(自动脱敏), pyproject, 或 patches/hermes/ 下补丁文件。诊断配置问题时调用。",
        "parameters": {"type": "object",
                       "properties": {"config": {"type": "string",
                                                 "description": "配置标识: config | env | pyproject "
                                                                "或 patches/hermes/ 下路径"}}}}},
    {"type": "function", "function": {
        "name": "ikaros_restart_gateway",
        "description": "重启 Hermes gateway (:8642): 杀掉占用进程 → 用 hermes venv python 拉起"
                       "`-m hermes_cli.main gateway run --replace` (cwd=core/hermes, 完整 env) "
                       "→ 轮询 /health 就绪。当 gateway 挂了需要恢复工具链时调用。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "ikaros_herdr",
        "description": "herdr 命名管道兜底通道 (不依赖 gateway): ping 测连通 / list 列 pane / "
                       "read 读 pane 输出 / run 在白名单命令中选一条在 pane 执行 "
                       "(别名 restart_hermes_stack=重启 hermes 整栈)。当 MCP 全断时这是最后保底。",
        "parameters": {"type": "object",
                       "properties": {
                           "action": {"type": "string",
                                      "description": "ping | list | read | run"},
                           "pane_id": {"type": "string", "description": "read/run 需要 pane_id"},
                           "command": {"type": "string",
                                       "description": "run 的别名: restart_hermes_stack"},
                           "lines": {"type": "integer", "description": "read 行数, 默认120"}}}}},
]

_CALLABLES: dict[str, callable] = {
    "ikaros_ps": lambda a: _run_ps(str(a.get("pattern", ""))),
    "ikaros_port": lambda a: _run_port(str(a.get("port", ""))),
    "ikaros_gateway_status": lambda a: _run_gateway_status(),
    "ikaros_read_log": lambda a: _run_read_log(str(a.get("log", "")),
                                               int(a.get("lines", 150) or 150)),
    "ikaros_read_config": lambda a: _run_read_config(str(a.get("config", ""))),
    "ikaros_restart_gateway": lambda a: _run_restart_gateway(),
    "ikaros_herdr": lambda a: _run_herdr(str(a.get("action", "ping")),
                                         str(a.get("pane_id", "")),
                                         str(a.get("command", "")),
                                         int(a.get("lines", 120) or 120)),
}


def call(name: str, args: dict) -> dict:
    """对话树降级链分派入口: 返回 {ok, result} (与 server.py 既有契约一致)。"""
    fn = _CALLABLES.get(name)
    if fn is None:
        return {"ok": False, "result": f"未知自救工具: {name}"}
    try:
        result = fn(args or {})
        return {"ok": True, "result": str(result)}
    except Exception as e:
        return {"ok": False, "result": f"{name} 执行失败: {type(e).__name__}: {e}"}


# ── MCP server 入口 (正常态: gateway 挂载 ikaros-rescue) ────────────────

def _mcp_main() -> None:
    """FastMCP stdio server; 与 SCHEMAS 同名工具, 由 gateway mcp_servers 拉起。"""
    from mcp.server.fastmcp import FastMCP  # noqa: PLC0415

    mcp = FastMCP(
        "ikaros-rescue",
        instructions=(
            "Ikaros gateway 自救工具集 (独立于 gateway 自身运行)。提供进程/端口诊断、"
            "gateway 日志与配置读取、gateway 重启、herdr 管道兜底。"
            "当 gateway 崩溃、工具链中断、需要自行诊断并恢复时优先使用这些工具。"
        ),
    )

    @mcp.tool()
    def ikaros_ps(pattern: str = "") -> str:
        """进程诊断 (tasklist, 关键字过滤)。"""
        return _run_ps(pattern)

    @mcp.tool()
    def ikaros_port(port: str = "") -> str:
        """端口诊断 (netstat, 已知服务全景/单端口)。"""
        return _run_port(port)

    @mcp.tool()
    def ikaros_gateway_status() -> str:
        """Hermes gateway (:8642) 综合健康检查。"""
        return _run_gateway_status()

    @mcp.tool()
    def ikaros_read_log(log: str, lines: int = 150) -> str:
        """读取 Hermes 运行日志尾部 (白名单: gateway/dashboard/bridge/conversation-tree/herdr)。"""
        return _run_read_log(log, lines)

    @mcp.tool()
    def ikaros_read_config(config: str) -> str:
        """读取 hermes 配置 (白名单: config/env/pyproject 或 patches/hermes/ 下文件)。"""
        return _run_read_config(config)

    @mcp.tool()
    def ikaros_restart_gateway() -> str:
        """重启 Hermes gateway (:8642): kill → venv python 拉起 → 轮询 /health。"""
        return _run_restart_gateway()

    @mcp.tool()
    def ikaros_herdr(action: str = "ping", pane_id: str = "",
                     command: str = "", lines: int = 120) -> str:
        """herdr 命名管道兜底 (ping/list/read/run 白名单)。"""
        return _run_herdr(action, pane_id, command, lines)

    mcp.run()


if __name__ == "__main__":
    # MCP stdio 模式 (gateway 拉起); 也可 `python rescue_tools.py selftest [--restart]` 自查
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _allow_restart = "--restart" in sys.argv
        for name in SCHEMAS:
            n = name["function"]["name"]
            if n == "ikaros_restart_gateway" and not _allow_restart:
                print(f"[{n}] (skip: 会杀真实 gateway; 加 --restart 显式执行)")
                continue
            try:
                r = call(n, {})
                print(f"[{n}] ok={r['ok']} result={str(r['result'])[:200]!r}", flush=True)
            except Exception as e:
                print(f"[{n}] EXC {type(e).__name__}: {e}", flush=True)
    else:
        _mcp_main()
