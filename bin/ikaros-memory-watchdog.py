#!/usr/bin/env python3
"""🪶 ikaros-memory-watchdog.py — 记忆服务看门狗 (2026-07-04)

管理记忆服务 (统一架构):
  1. Embedding (:8587) — nomic-embed-text, 供 v4 记忆库语义搜索
  2. LLM (:8080) — Qwen3-1.7B, 供 V5 后台节 token 任务 (reflect/compress/think)

启动后:
  - 启动 embedding + LLM 服务
  - 每 10 秒巡检端口, 死则重启
  - 写 PID 文件, 支持 --stop 安全停止

用法:
  python bin/ikaros-memory-watchdog.py          # 启动 (后台: start /B)
  python bin/ikaros-memory-watchdog.py --stop   # 停止
  python bin/ikaros-memory-watchdog.py --status # 状态查询

端点播报 (写入 JSON, 供其他组件读取):
  data/icarus-memory/endpoints.json
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Windows: DETACHED_PROCESS 脱离父控制台 (避免父死子随).
_SUBPROC_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)

ROOT = Path(os.environ.get("HERMES_ROOT", "E:\\Ikaros"))
PID_FILE = ROOT / "data" / "logs" / "ikaros-memory-watchdog.pid"
LOG_FILE = ROOT / "data" / "logs" / "ikaros-memory-watchdog.log"
ENDPOINTS_FILE = ROOT / "Ikaros-memory" / "data" / "endpoints.json"
HEARTBEAT_FILE = ROOT / "data" / "logs" / "ikaros-heartbeat.jsonl"

# llama-server binary (from env var or default)
LLAMA_BIN = Path(os.environ.get("IKAROS_LLAMA_SERVER",
    str(ROOT / "runtime" / "llama" / "b10000-cuda" / "llama-server.exe")))

# Model paths (from env vars or defaults)
EMBED_MODEL = Path(os.environ.get("IKAROS_MODEL_EMBEDDING",
    str(ROOT / "Ikaros-memory" / "models" / "nomic-embed-text-v2-moe.f32.gguf")))
LLM_MODEL = Path(os.environ.get("IKAROS_MODEL_LLM",
    str(ROOT / "Ikaros-memory" / "models" / "Qwen_Qwen3-1.7B-Q4_K_M.gguf")))

# Ports
EMBED_PORT = 8587
LLM_PORT = 8080  # Qwen3-1.7B for V5 background tasks

# Skip LLM (:8080) this run if env var set.
# Used for "lighter" launches (e.g. ikaros-start.bat --no-llm).
# Any of: "1" / "true" / "yes" / "on" enables skip; empty / "0" / "false"
# does NOT skip.
SKIP_LLM = os.environ.get("IKAROS_SKIP_LLM", "").strip().lower() in (
    "1", "true", "yes", "on")

CHECK_INTERVAL = 10  # patrol interval (seconds)
PORT_TIMEOUT = 30    # wait for port ready timeout (seconds)
REFLECT_INTERVAL = 1800  # 30min between memory reflection cycles

log = print  # 启动阶段用 print; watchdog 循环用 _log


class MemoryWatchdog:
    """记忆服务看门狗 — 启动 + 巡检 embedding, 检测 LLM 健康."""

    def __init__(self):
        self._procs: dict[str, subprocess.Popen | None] = {
            "embed": None,
            "llm": None,
        }
        self._running = False
        self._last_reflect = time.time()  # last memory reflection timestamp
        # (init to now so the first reflect doesn't fire until REFLECT_INTERVAL later,
        #  avoiding resource-storm-on-cold-start that crashes the watchdog.)

    # ─── 端口工具 ────────────────────────────────────

    @staticmethod
    def _port_alive(port: int) -> bool:
        """TCP 端口是否在监听."""
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

    @staticmethod
    def _wait_port(port: int, timeout: int = PORT_TIMEOUT) -> bool:
        """等待端口就绪, 超时返回 False."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if MemoryWatchdog._port_alive(port):
                return True
            time.sleep(1)
        return False

    @staticmethod
    def _health_ok(port: int, timeout: int = 3) -> bool:
        """llama-server /health 返回 200 才代表模型已加载、可服务.

        用 http.client 直连 (不走 urllib opener / proxy 链),
        避免 watchdog 在 launch-hidden.vbs / --detach 环境下
        urllib.request.urlopen 误判超时或被代理拦截.
        404 = 该 build 无 /health 端点 → 退化为仅查端口, 不误杀.
        503/500/连接错 = 未就绪或已坏 → 返 False (触发重启/等待).
        """
        import http.client
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            status = resp.status
            resp.read()
            conn.close()
            if status == 404:
                return True  # 无 /health 端点, 退化为仅查端口
            return status == 200
        except Exception:
            return False

    @staticmethod
    def _wait_health(port: int, timeout: int = 120) -> bool:
        """等待 /health 200 (模型真正就绪), 超时返回 False.
        加载中 /health 返 503 → 持续等待, 不会误判死亡."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if MemoryWatchdog._health_ok(port):
                return True
            time.sleep(1)
        return False

    @staticmethod
    def _service_ok(port: int) -> bool:
        """端口在 + /health 200 才算真活."""
        return MemoryWatchdog._port_alive(port) and MemoryWatchdog._health_ok(port)

    # ─── 启动 ────────────────────────────────────────

    def _start_embed(self) -> bool:
        """启动 embedding llama-server (:8587)."""
        if self._port_alive(EMBED_PORT):
            _log("[embed] :8587 already listening, skip")
            return True
        if not LLAMA_BIN.exists():
            _log("[embed] FATAL: llama-server not found: %s", LLAMA_BIN)
            return False
        if not EMBED_MODEL.exists():
            _log("[embed] FATAL: model not found: %s", EMBED_MODEL)
            return False

        _log("[embed] starting: %s ...", EMBED_MODEL.name)
        self._procs["embed"] = subprocess.Popen(
            [
                str(LLAMA_BIN),
                "-m", str(EMBED_MODEL),
                "--host", "127.0.0.1",
                "--port", str(EMBED_PORT),
                "-c", "4096",
                "-ngl", "99",
                "--embeddings",
                "--pooling", "mean",
                "--alias", "nomic-embed-text-v2-moe",
                "--cont-batching",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_SUBPROC_DETACHED,
        )
        ok = self._wait_health(EMBED_PORT)
        _log("[embed] %s (%s)", "OK" if ok else "FAIL", EMBED_PORT)
        return ok

    def _start_llm(self) -> bool:
        """启动/检测 llama-server (:8080) — Qwen3-1.7B for V5 background tasks."""
        if self._port_alive(LLM_PORT):
            _log("[llm] :8080 already listening, skip")
            return True
        if not LLAMA_BIN.exists():
            _log("[llm] FATAL: llama-server not found: %s", LLAMA_BIN)
            return False
        if not LLM_MODEL.exists():
            _log("[llm] FATAL: model not found: %s", LLM_MODEL)
            return False

        _log("[llm] starting: %s ...", LLM_MODEL.name)
        self._procs["llm"] = subprocess.Popen(
            [
                str(LLAMA_BIN),
                "-m", str(LLM_MODEL),
                "--host", "127.0.0.1",
                "--port", str(LLM_PORT),
                "-c", "4096",
                "-ngl", "99",
                "--alias", "qwen3-1.7b",
                "--cont-batching",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_SUBPROC_DETACHED,
        )
        ok = self._wait_health(LLM_PORT, timeout=120)
        _log("[llm] %s (%s)", "OK" if ok else "FAIL", LLM_PORT)
        return ok

    def start_all(self) -> bool:
        """启动记忆服务 (embedding + 检测 LLM)."""
        # ── 动作日志: watchdog 启动/重启都是关键 action ──
        import importlib.util as _ilu, sys as _sys
        if "_action_log" not in _sys.modules:
            _spec = _ilu.spec_from_file_location(
                "_action_log",
                str(ROOT / "bin" / "ikaros-action-log.py"))
            _al = _ilu.module_from_spec(_spec)
            _sys.modules["_action_log"] = _al
            _spec.loader.exec_module(_al)
        else:
            _al = _sys.modules["_action_log"]

        with _al.action("start_all memory services",
                        action="watchdog.start_all",
                        target="embedding+llm",
                        who="Ikaros (watchdog)",
                        why="memory watchdog init/restart") as _a:
            _log("=== Starting memory services (unified architecture) ===")
            ok_embed = self._start_embed()
            if SKIP_LLM:
                _log("[llm] SKIP_LLM set — not starting :8080 this run")
                ok_llm = False
            else:
                ok_llm = self._start_llm()
            self._write_endpoints(ok_embed, ok_llm)
            all_ok = ok_embed  # LLM 不强制要求 (embed 必须)
            if ok_embed and ok_llm:
                _log("=== Embedding + LLM both started ===")
            elif ok_embed:
                _log("=== Embedding started, LLM failed ===")
            else:
                _log("=== Embedding failed ===")
            _a.done(result="ok" if all_ok else "fail",
                    completion_pct=100 if all_ok else 50,
                    notes=f"embed={'ok' if ok_embed else 'fail'} llm_8080={'ok' if ok_llm else 'fail'}")
            return all_ok

    # ─── 巡检 ────────────────────────────────────────

    def _maybe_reflect(self, *, force: bool = False) -> None:
        """Periodically trigger memory reflection cycle (self-evolution).

        Runs every REFLECT_INTERVAL seconds. Imports v5.reflect.registry and
        runs the V4 reflection scheduler (consolidate/dedup/promote/distill/
        reflect/cleanup). continue_on_error=True: one failing op (e.g. missing
        DeepSeek key for the 7d reflect) does not block the rest.

        force=True: skip interval check and run ALL ops immediately
        (used on startup so reflection fires once before the timer begins,
        avoiding the cold-start gap where ops sit idle for REFLECT_INTERVAL).
        """
        now = time.time()
        if not force and (now - self._last_reflect) < REFLECT_INTERVAL:
            return
        self._last_reflect = now
        try:
            sys.path.insert(0, str(ROOT / "Ikaros-memory"))
            import importlib
            # V5.1:
            vr = importlib.import_module("v5.reflect.registry")
            sched = vr.make_default_scheduler()
            results = sched.run_all(force=force, continue_on_error=True)
            _log("[reflect] v4 cycle complete (force=%s): %s", force, results)
        except Exception as e:
            _log("[reflect] v4 cycle failed (non-fatal): %s", e)

    def _check_and_restart(self) -> None:
        """巡检 embedding + LLM (端口+/health) + 定期记忆反思.

        用 _service_ok (端口在 + /health 200) 替代裸 _port_alive,
        避免僵尸监听器 (端口绑了但服务崩) 被误报 OK.
        """
        embed_alive = self._service_ok(EMBED_PORT)
        if not embed_alive:
            _log("[heartbeat] embed :8587 DEAD (port/health), restarting...")
            self._start_embed()
            embed_alive = self._service_ok(EMBED_PORT)
        else:
            _log("[heartbeat] embed :8587 OK (port+health)")

        if SKIP_LLM:
            # 不加载 qwen3 模式: 仅反映 :8080 当前状态, 绝不重启 LLM
            llm_alive = self._port_alive(LLM_PORT)
            _log("[heartbeat] llm :8080 SKIP (SKIP_LLM set) — not restarting")
        else:
            llm_alive = self._service_ok(LLM_PORT)
            if not llm_alive:
                _log("[heartbeat] llm :8080 DEAD (port/health), restarting...")
                self._start_llm()
                llm_alive = self._service_ok(LLM_PORT)
            else:
                _log("[heartbeat] llm :8080 OK (port+health)")

        self._write_endpoints(embed_alive, llm_alive)
        self._emit_heartbeat(embed_alive, llm_alive)

        # 定期跑记忆反思周期 (只在 LLM 可用时)
        if llm_alive:
            self._maybe_reflect()

    def _emit_heartbeat(self, embed_alive: bool, llm_alive: bool) -> None:
        """写入心跳 JSONL, 加入 ikaros 统一心跳体系."""
        try:
            import json
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            ev = {
                "event": "memory_watchdog",
                "ts": time.time(),
                "embed_port": EMBED_PORT,
                "llm_port": LLM_PORT,
                "embed_alive": embed_alive,
                "llm_alive": llm_alive,
                "all_ok": embed_alive and llm_alive,
            }
            with open(str(HEARTBEAT_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception as e:
            _log("[heartbeat] JSONL write failed: %s", e)

    def run_loop(self) -> None:
        """巡检主循环."""
        self._running = True
        _log("=== Watchdog loop started (interval=%ds) ===", CHECK_INTERVAL)
        while self._running:
            time.sleep(CHECK_INTERVAL)
            try:
                self._check_and_restart()
            except Exception as e:
                _log("[heartbeat] ERROR: %s", e)

    def stop(self) -> None:
        """停止巡检循环."""
        self._running = False

    # ─── 端点播报 ────────────────────────────────────

    def _write_endpoints(self, embed_ok: bool, llm_ok: bool) -> None:
        """写入 endpoints.json, 供其他组件读取记忆服务状态."""
        try:
            ENDPOINTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "embedding": {
                    "url": f"http://127.0.0.1:{EMBED_PORT}/v1",
                    "port": EMBED_PORT,
                    "alive": embed_ok,
                    "model": "nomic-embed-text",
                },
                "llm": {
                    "url": f"http://127.0.0.1:{LLM_PORT}/v1",
                    "port": LLM_PORT,
                    "alive": llm_ok,
                    "model": "qwen3-1.7b",
                    "note": "Managed by memory watchdog for V5 background tasks",
                },
                "updated_at": time.time(),
            }
            ENDPOINTS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _log("[endpoints] write failed: %s", e)


# ─── 独立工具函数 ───────────────────────────────────


def _setup_log():
    """配置日志: 写文件 + 终端.

    Returns: callable _log(fmt, *args, level=INFO) — 兼容 _log("msg") 和 _log.info("msg").
    """
    import logging
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("memory-watchdog")
    logger.setLevel(logging.INFO)

    # 防止重复 handler (--detach + detach 内再 fork 时 logger 全局, 否则重复)
    if logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)

    # 文件 handler
    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(fh)

    # 终端 handler (启动阶段可见)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[watchdog] %(message)s"))
    logger.addHandler(ch)

    # wrapper: _log("msg") or _log("fmt %s", arg1) → logger.info(formatted)
    #          也暴露 .info / .warning / .error 等 logger 方法
    class _LogWrapper:
        def __init__(self, lg): self._lg = lg
        def __call__(self, fmt, *args, level=None):
            msg = fmt % args if args else fmt
            (level or self._lg.info)(msg)
        def __getattr__(self, name):
            return getattr(self._lg, name)

    return _LogWrapper(logger)


_log = lambda *a, **kw: None  # noqa: E731  # 启动前占位


def cmd_start():
    """启动记忆看门狗 (前台, 持续巡检)."""
    global _log
    _log = _setup_log()

    wd = MemoryWatchdog()
    ok = wd.start_all()
    if not ok:
        _log("Some services failed to start, continuing watchdog anyway...")

    # 写 PID
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    # 注册 SIGTERM/SIGINT 优雅退出
    def _on_signal(signum, _frame):
        _log("Received signal %d, shutting down watchdog...", signum)
        wd.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # startup: run ALL reflection ops once immediately (force=True),
    # so the system doesn't sit idle for REFLECT_INTERVAL on cold start.
    _log("[reflect] startup: running initial full reflection cycle...")
    wd._maybe_reflect(force=True)
    _log("[reflect] startup: initial cycle done, beginning %ds timer",
         REFLECT_INTERVAL)

    try:
        wd.run_loop()
    except KeyboardInterrupt:
        _log("Watchdog stopped by user.")
        wd.stop()


def cmd_stop():
    """停止记忆看门狗 (发 SIGTERM + 清扫 llama-server)."""
    print("[watchdog] Stopping memory services...")
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            print(f"  watchdog PID {pid} terminated")
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)
    else:
        print("  watchdog PID file not found (may already be stopped)")

    # 清扫残留的 llama-server (ikaros-sleep 也会做, 但这里确保)
    for name in ("llama-server.exe", "llama-server-cuda-13.3.exe"):
        subprocess.run(
            ["taskkill", "/F", "/IM", name, "/T"],
            capture_output=True,
        )
    print("  llama-server instances cleaned up")

    # 清除端点文件
    if ENDPOINTS_FILE.exists():
        ENDPOINTS_FILE.unlink()
    print(f"  {ENDPOINTS_FILE.name} removed")

    print("[watchdog] Done.")


def cmd_status():
    """查询记忆服务状态 (端口 + /health)."""
    import socket

    def _check(port: int) -> str:
        if not MemoryWatchdog._port_alive(port):
            return "✗ DEAD"
        if MemoryWatchdog._health_ok(port):
            return "✓ ALIVE (port+health)"
        return "⚠ PORT-ONLY (health failed)"

    print("=== Memory Services Status (Unified Architecture) ===")
    print(f"  Embedding (:8587): {_check(EMBED_PORT)}")
    print(f"  LLM       (:8080): {_check(LLM_PORT)} (qwen3-1.7b)")

    if PID_FILE.exists():
        pid_str = PID_FILE.read_text(encoding="utf-8").strip()
        alive = False
        try:
            # Windows: os.kill(pid, 0) 不可靠 (WinError 87), 用 ctypes OpenProcess
            import ctypes
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid_str))
            if h:
                ec = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(h, ctypes.byref(ec)):
                    alive = (ec.value == STILL_ACTIVE)
                kernel32.CloseHandle(h)
        except Exception:
            pass
        print(f"  Watchdog PID {pid_str}: {'✓ running' if alive else '✗ dead (stale PID file)'}")
    else:
        print("  Watchdog: not running")

    if ENDPOINTS_FILE.exists():
        ep = json.loads(ENDPOINTS_FILE.read_text(encoding="utf-8"))
        embed_alive = ep.get("embedding", {}).get("alive", "?")
        llm_alive = ep.get("llm", {}).get("alive", "?")
        print(f"  Endpoints file: embedding={embed_alive}/llm={llm_alive}")
    else:
        print("  Endpoints file: not found")

    print()


def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "--stop":
            cmd_stop()
            return
        elif cmd == "--status":
            cmd_status()
            return
        elif cmd == "--detach":
            # 后台启动 (Windows 用 subprocess.CREATE_NEW_PROCESS_GROUP, 自己 detach)
            import subprocess
            here = Path(__file__).resolve().parent
            print(f"[watchdog] Detaching... log: {LOG_FILE}")
            log_f = open(LOG_FILE, "ab", buffering=0)
            proc = subprocess.Popen(
                [sys.executable, str(here / "ikaros-memory-watchdog.py")],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                ),
                close_fds=True,
            )
            print(f"[watchdog] Detached PID: {proc.pid}")
            print(f"[watchdog] Tail: {sys.executable} -m tail -f '{LOG_FILE}'")
            return
        else:
            print(f"Unknown command: {cmd}")
            print("Usage: python bin/ikaros-memory-watchdog.py [--stop|--status|--detach]")
            sys.exit(1)

    cmd_start()


if __name__ == "__main__":
    main()
