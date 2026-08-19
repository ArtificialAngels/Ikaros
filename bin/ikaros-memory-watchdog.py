#!/usr/bin/env python3
# 详细说明见 docs/scripts/bin/ikaros-memory-watchdog.md
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
# 用于子服务(llama-server)的创建标志:
# 用 CREATE_NO_WINDOW (0x08000000) 而非 DETACHED_PROCESS:
# DETACHED_PROCESS 内部的 DETACHED_PROCESS 子进程会导致 llama-server
# STATUS_HEAP_CORRUPTION (0xC0000374)。
# CREATE_NO_WINDOW 隐藏控制台窗口但仍提供 CRT 正常运行所需的控制台基础设施。
_SUBPROC_SERVICE = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 项目根：优先环境变量，其次脚本位置推导（可整体迁移盘符，不依赖 E:/F: 硬编码）
_SCRIPT_DIR = Path(__file__).resolve().parent          # bin/
ROOT = Path(os.environ.get("HERMES_ROOT") or os.environ.get("IKAROS_ROOT")
            or (_SCRIPT_DIR.parent)).resolve()
if not (ROOT / "core" / "env").is_dir() and ( _SCRIPT_DIR.parent / "core" / "env").is_dir():
    ROOT = _SCRIPT_DIR.parent
PID_FILE = ROOT / "data" / "logs" / "ikaros-memory-watchdog.pid"
LOG_FILE = ROOT / "data" / "logs" / "ikaros-memory-watchdog.log"
ENDPOINTS_FILE = ROOT / "core/memory_v5" / "data" / "endpoints.json"
HEARTBEAT_FILE = ROOT / "data" / "logs" / "ikaros-heartbeat.jsonl"
_HEARTBEAT_MAX_BYTES = 5 * 1024 * 1024  # 心跳 JSONL 轮转阈值 (2026-08-14)
_HEARTBEAT_BACKUPS = 2                  # 轮转保留的历史份数

# llama-server 二进制：按设备 CUDA 能力自动选择（llama_resolver 统一解析）
try:
    sys.path.insert(0, str(ROOT / "core"))
    from core.env import llama_resolver as _llama_resolver
    _LLAMA_RES = _llama_resolver.resolve_llama_dir(ROOT)
except Exception as _e:  # resolver 失败不致命：回退旧逻辑
    _LLAMA_RES = {"dir": ROOT / "runtime" / "llama" / "b10000-cuda",
                  "version": "b10000-cuda", "cuda": None,
                  "cpu_fallback": False, "reason": f"resolver failed: {_e}"}
LLAMA_BIN = Path(os.environ.get("IKAROS_LLAMA_SERVER", str(_LLAMA_RES["dir"] / "llama-server.exe")))
LLAMA_CPU_FALLBACK = _LLAMA_RES.get("cpu_fallback", False)
LLAMA_SELECT_REASON = _LLAMA_RES.get("reason", "")

# Embedding model (dedicated; never auto-scanned as a chat LLM)
# 2026-08-14: nomic-embed-text-v2-moe 在 llama.cpp b10000 下输出全零 (mask token 缺失);
# nomic-v1.5 中文语义弱; 换 bge-m3 Q8_0 (1024 维, 中英多语言强, 需 --pooling cls)。
EMBED_MODEL = Path(os.environ.get("IKAROS_MODEL_EMBEDDING",
    str(ROOT / "core/memory_v5" / "models" / "bge-m3-q8_0.gguf")))


def _load_model_cfg() -> dict:
    """加载本地 LLM 加载配置 (Ikaros-memory/models/model_config.json)。

    首次运行若配置缺失，model_config 会扫描模型目录、自动选定初始模型并落盘。
    详见 Ikaros-memory/models/model_config.py（参数对应 llama.cpp 官方 flag）。
    """
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "model_config",
            str(ROOT / "core/memory_v5" / "models" / "model_config.py"))
        _mc = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mc)
        return _mc.resolve_model_config()
    except Exception as e:  # 解析失败不致命，回退内置默认
        print(f"[llm] model_config load failed ({e}); using built-in defaults")
        return {"initial_model": "Qwen_Qwen3-1.7B-Q4_K_M.gguf",
                "alias": "local-llm", "host": "127.0.0.1",
                "ctx_size": 8192, "gpu_layers": "auto", "flash_attn": "auto"}


# Local LLM 配置：动态解析（IKAROS_MODEL_LLM 环境变量可覆盖配置文件中的选择）
_LLM_CFG = _load_model_cfg()
LLM_MODEL = Path(os.environ.get("IKAROS_MODEL_LLM",
    str(ROOT / "core/memory_v5" / "models" / _LLM_CFG["initial_model"])))

# Ports
EMBED_PORT = 8587
LLM_PORT = 8080  # 本地 LLM 服务端口

# 本地 LLM (:8080) 运行模式: 懒加载 / 按需 (lazy-on-demand)
# 看门狗只「检测端口在不」, 不主动拉起模型、不在启动时加载模型。
# 模型在 agent 第一次调用本地 LLM 时由 ensure_local_llm() 热载入
# (或手动 `llama-help --hotload` 触发)。
# 这与 2026-07-26「V5 剔除小模型」不冲突: V5 认知任务仍走云端 DeepSeek,
# 本地 :8080 仅作为按需可调用的常规 llama 服务 (agent / 本地 chat 等)。
LLM_LAZY = True


def _resolve_llm_model() -> "Path | None":
    """动态解析本地 LLM 模型路径 (每次调用时读取, 不缓存).

    修复 (2026-08-11): 原 LLM_MODEL 为模块级固定值且 env 优先——看门狗被面板
    /旧进程重启时继承旧 env (如 Hermes snap 恢复的 Qwen 路径), 改 config 后不
    生效。新语义:
      - config (model_config.json 的 initial_model) 是权威源 (面板切换/手动
        修改都走这里), 每次 spawn 前重新读取;
      - env IKAROS_MODEL_LLM 仅当 config 模型文件缺失时兜底 (历史兼容)。
      - 2026-08-18 本地 LLM 退役: initial_model 为空串 (显式禁用标记)
        → 返回 None, 调用方 (spawn/start) 判空跳过, 不再回退 Qwen 默认。
    """
    try:
        _fresh = _load_model_cfg()
        if isinstance(_fresh, dict) and "initial_model" in _fresh:
            cfg_model = _fresh["initial_model"]
        else:
            cfg_model = _LLM_CFG.get("initial_model", "Qwen_Qwen3-1.7B-Q4_K_M.gguf")
    except Exception:
        cfg_model = _LLM_CFG.get("initial_model", "Qwen_Qwen3-1.7B-Q4_K_M.gguf")
    if cfg_model == "":  # 显式"无本地 LLM"(2026-08-18 退役标记)
        return None
    cfg_path = Path(ROOT / "core" / "memory_v5" / "models" / cfg_model)
    if cfg_path.exists():
        return cfg_path
    env_val = os.environ.get("IKAROS_MODEL_LLM", "").strip()
    if env_val:
        return Path(env_val)
    return cfg_path


def _build_llm_argv() -> list[str]:
    """构造 llama-server (:8080) 启动参数列表 (含二进制 + 模型 + 服务 flags).

    配置逻辑统一来自 core/memory_v5/models/model_config.py (经 _load_model_cfg 读取)。
    """
    model = _resolve_llm_model()
    if model is None:
        raise FileNotFoundError(
            "local LLM disabled (2026-08-18 退役): model_config.json 的 "
            "initial_model 为空串; 有本地推理需求时放入 gguf 并改回模型名即可恢复")
    ngl = str(_LLM_CFG.get("gpu_layers", "auto"))
    if LLAMA_CPU_FALLBACK:
        # 设备无匹配 CUDA build：强制 CPU 层数，避免 CUDA 初始化崩溃
        ngl = "0"
    return [
        str(LLAMA_BIN),
        "-m", str(model),
        "--host", _LLM_CFG.get("host", "127.0.0.1"),
        "--port", str(LLM_PORT),
        "-c", str(_LLM_CFG.get("ctx_size", 8192)),
        "-ngl", ngl,
        "--flash-attn", _LLM_CFG.get("flash_attn", "auto"),
        "--alias", _LLM_CFG.get("alias", "local-llm"),
        "--cont-batching",
        "--jinja",
    ]


def _spawn_llm_server() -> subprocess.Popen:
    """Spawn llama-server (:8080) 为独立进程。

    用 CREATE_NO_WINDOW 脱离父控制台存活 (避免 STATUS_HEAP_CORRUPTION, 见顶部注释),
    父进程退出后服务仍常驻, 由看门狗做端口巡检。不追踪到 self._procs。
    """
    if not LLAMA_BIN.exists():
        raise FileNotFoundError(
            f"llama-server not found: {LLAMA_BIN} "
            f"(选择原因: {LLAMA_SELECT_REASON or 'env/默认'})")
    argv = _build_llm_argv()  # 内部 resolve 模型并判空 (None → FileNotFoundError)
    model = Path(argv[argv.index("-m") + 1])
    if not model.exists():
        raise FileNotFoundError(f"local LLM model not found: {model}")
    return subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_SUBPROC_SERVICE,
    )


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
        # CFG/Exploit Protection 崩溃 — 计数+退避, 避免无限快速重启
        self._crash_counts: dict[str, int] = {"embed": 0, "llm": 0}
        self._crash_last_ts: dict[str, float] = {"embed": 0, "llm": 0}
        self._crash_disabled: dict[str, bool] = {"embed": False, "llm": False}
        self._CRASH_BACKOFF_INTERVAL = 60  # 60s 内连续崩溃才累积
        self._CRASH_LIMIT = 5              # 5 次后标记禁用
        self._CFG_FLAG = ROOT / "data" / ".llama_cfg_crashed"

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
                "--pooling", "cls",
                "--alias", "bge-m3",
                "--cont-batching",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_SUBPROC_SERVICE,
        )
        ok = self._wait_health(EMBED_PORT)
        _log("[embed] %s (%s)", "OK" if ok else "FAIL", EMBED_PORT)
        return ok

    def _start_llm(self) -> bool:
        """启动 llama-server (:8080) — 本地 LLM。

        注意: 看门狗默认**不再主动调用**本方法 (LLM_LAZY=True, 懒加载模式)。
        本方法保留供热载入路径 ensure_local_llm() / 手动触发复用。
        """
        if self._port_alive(LLM_PORT):
            _log("[llm] :8080 already listening, skip")
            return True
        model = _resolve_llm_model()
        if model is None:
            _log("[llm] local LLM 已退役 (2026-08-18), 跳过启动 (:8080)")
            return False
        _log("[llm] starting: %s ...", model.name)
        try:
            self._procs["llm"] = _spawn_llm_server()
        except Exception as e:
            _log("[llm] FATAL: %s", e)
            return False
        ok = self._wait_health(LLM_PORT, timeout=120)
        _log("[llm] %s (%s)", "OK" if ok else "FAIL", LLM_PORT)
        return ok

    def start_all(self) -> bool:
        """启动记忆服务 (embedding + 被动检测 LLM :8080).

        LLM 为懒加载模式: 看门狗不主动拉起, 仅记录端口现状。
        模型在 agent 调用本地 LLM 时由 ensure_local_llm() 热载入。
        """
        # ── 动作日志: watchdog 启动/重启都是关键 action ──
        import importlib.util as _ilu, sys as _sys
        if "_action_log" not in _sys.modules:
            _spec = _ilu.spec_from_file_location(
                "_action_log",
                str(ROOT / "core" / "memory_v5" / "scripts" / "ikaros-action-log.py"))
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
            # LLM (:8080) 懒加载: 看门狗只检测端口, 不拉起模型
            ok_llm = self._port_alive(LLM_PORT)
            if ok_llm:
                _log("[llm] :8080 already listening (lazy mode — not started by watchdog)")
            else:
                _log("[llm] :8080 not running — lazy/on-demand; "
                     "will hot-load on agent call / `llama-help --hotload`")
            self._write_endpoints(ok_embed, ok_llm)
            all_ok = ok_embed  # LLM 不强制要求 (embed 必须)
            if ok_embed and ok_llm:
                _log("=== Embedding + LLM both up ===")
            elif ok_embed:
                _log("=== Embedding started, LLM not loaded (lazy) ===")
            else:
                _log("=== Embedding failed ===")
            _a.done(result="ok" if all_ok else "fail",
                    completion_pct=100 if all_ok else 50,
                    notes=f"embed={'ok' if ok_embed else 'fail'} llm_8080={'up' if ok_llm else 'lazy-down'}")
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
            sys.path.insert(0, str(ROOT / "core"))
            import importlib
            # V5.1: 2026-08-14 修复——原 `v5.reflect.registry` 是改名前的旧包名,
            # 包已迁 memory_v5, 该 import 自改名后一直 ModuleNotFoundError 被
            # 外层吞掉, 全部反思 op (含 promote/cleanup/vector_sync) 静默停摆。
            # 另: make_default_scheduler 已按决策 A 停用 LLM 生成类 op。
            vr = importlib.import_module("memory_v5.reflect.registry")
            sched = vr.make_default_scheduler()
            results = sched.run_all(force=force, continue_on_error=True)
            _log("[reflect] v4 cycle complete (force=%s): %s", force, results)
        except Exception as e:
            _log("[reflect] v4 cycle failed (non-fatal): %s", e)

    def _crash_track(self, name: str, alive: bool, reason: str = "") -> bool:
        """跟踪启动失败, 带退避机制. 返回 False 表示已禁用."""
        if alive:
            self._crash_counts[name] = 0
            return True
        now = time.time()
        if now - self._crash_last_ts[name] > self._CRASH_BACKOFF_INTERVAL:
            self._crash_counts[name] = 1
        else:
            self._crash_counts[name] += 1
        self._crash_last_ts[name] = now
        if self._crash_counts[name] >= self._CRASH_LIMIT:
            self._crash_disabled[name] = True
            # 2026-08-14: 写入真实失败原因而非 CFG 样板文案
            # (8/13 曾因模型文件缺失被误标 "CFG killing ntdll.dll", 误导排查).
            self._CFG_FLAG.write_text(
                f"llama-server ({name}) failed {self._crash_counts[name]} times in a row, "
                f"auto-restart disabled.\n"
                f"Last failure reason: {reason or 'unknown (see watchdog log)'}\n"
                f"Fix: restore the model/binary, then DELETE this flag file to re-enable.\n",
                encoding="utf-8",
            )
            _log("[%s] failed %d times, disabled auto-restart (flag: %s, reason: %s)",
                 name, self._crash_counts[name], self._CFG_FLAG, reason or "unknown")
            return False
        return True

    def _check_and_restart(self) -> None:
        """巡检 embedding + LLM (port+/health) + CFG crash backoff."""
        if not self._crash_disabled.get("embed", False):
            embed_alive = self._service_ok(EMBED_PORT)
            if not embed_alive:
                _log("[heartbeat] embed :8587 DEAD (port/health), restarting...")
                if not EMBED_MODEL.exists():
                    reason = f"embed model file missing: {EMBED_MODEL}"
                elif not LLAMA_BIN.exists():
                    reason = f"llama-server missing: {LLAMA_BIN}"
                else:
                    reason = "spawn/health failed (see watchdog log)"
                ok = self._start_embed()
                embed_alive = self._crash_track("embed", ok, reason)
            else:
                self._crash_track("embed", True)
                _log("[heartbeat] embed :8587 OK (port+health)")
        else:
            embed_alive = False
            _log("[heartbeat] embed :8587 SKIP (CFG crash limit)")

        # ── LLM (:8080) 懒加载巡检: 只看端口/health, 不拉起、不重启模型 ──
        llm_alive = self._port_alive(LLM_PORT)
        llm_healthy = self._health_ok(LLM_PORT) if llm_alive else False
        if llm_alive:
            self._crash_track("llm", True)  # 存活, 不计入崩溃
            _log("[heartbeat] llm :8080 OK (port%s; lazy mode — watchdog will NOT "
                 "auto-start/restart)", "+health" if llm_healthy else "-only")
        else:
            self._crash_track("llm", True)  # 懒加载的 downtime 不计入崩溃
            _log("[heartbeat] llm :8080 DOWN (lazy mode — watchdog does NOT auto-start; "
                 "load via agent call to local LLM or `llama-help --hotload`)")

        self._write_endpoints(embed_alive, llm_alive)
        self._emit_heartbeat(embed_alive, llm_alive)
        if llm_alive:
            self._maybe_reflect()

    def _emit_heartbeat(self, embed_alive: bool, llm_alive: bool) -> None:
        """写入心跳 JSONL, 加入 ikaros 统一心跳体系."""
        try:
            import json
            HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_heartbeat()
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

    def _rotate_heartbeat(self) -> None:
        """心跳 JSONL 轮转 (2026-08-14): 超过阈值时压栈改名, 只保留 _HEARTBEAT_BACKUPS 份.

        此前纯追加无轮转, ikaros-heartbeat.jsonl 曾涨到 25MB (~0.86MB/天) 无限增长.
        """
        try:
            if (not HEARTBEAT_FILE.exists()
                    or HEARTBEAT_FILE.stat().st_size < _HEARTBEAT_MAX_BYTES):
                return
            for i in range(_HEARTBEAT_BACKUPS - 1, 0, -1):
                src = HEARTBEAT_FILE.with_suffix(f".{i}.jsonl")
                dst = HEARTBEAT_FILE.with_suffix(f".{i + 1}.jsonl")
                if src.exists():
                    dst.unlink(missing_ok=True)
                    src.replace(dst)
            HEARTBEAT_FILE.replace(HEARTBEAT_FILE.with_suffix(".1.jsonl"))
            _log("[heartbeat] rotated %s (%.1f MB)",
                 HEARTBEAT_FILE.name, _HEARTBEAT_MAX_BYTES / (1024 * 1024))
        except Exception as e:
            _log("[heartbeat] rotate failed: %s", e)

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
                    "model": "bge-m3",
                },
                "llm": {
                    "url": f"http://127.0.0.1:{LLM_PORT}/v1",
                    "port": LLM_PORT,
                    "alive": llm_ok,
                    "model": _LLM_CFG.get("alias", "local-llm"),
                    "note": "lazy/on-demand — loaded on agent call or `llama-help --hotload`; "
                            "watchdog only monitors the port, does NOT auto-start",
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


def ensure_local_llm(timeout: int = 180) -> bool:
    """热载入本地 LLM (:8080): 已起且 /health 200 则直接返回; 否则 detached spawn
    llama-server 并等待 /health 200。

    看门狗只做端口巡检、不调用本函数。本函数由以下路径触发:
      - agent 调用本地 LLM 时 (llm_client.call_llm(provider="local") → _call_local)
      - 手动 `llama-help --hotload`

    并发保护: 用 data/logs/.llama-hotload.lock 占位防重复 spawn; 若已有热载入进行中,
    本次不再 spawn, 直接等待端口就绪。

    Returns: True=模型已就绪可服务; False=拉起失败。
    """
    if MemoryWatchdog._port_alive(LLM_PORT) and MemoryWatchdog._health_ok(LLM_PORT):
        return True

    lock = ROOT / "data" / "logs" / ".llama-hotload.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    owned = False
    try:
        if lock.exists():
            # 已有热载入进行中: 不重复 spawn, 直接等端口
            _log("[llm-hotload] concurrent hot-load detected, waiting for :8080")
        else:
            lock.write_text(str(os.getpid()), encoding="utf-8")
            owned = True
            try:
                _spawn_llm_server()
            except Exception as e:
                _log("[llm-hotload] spawn failed: %s", e)
                return False
        ok = MemoryWatchdog._wait_health(LLM_PORT, timeout=timeout)
        _log("[llm-hotload] %s (%s)", "OK" if ok else "FAIL", LLM_PORT)
        return ok
    finally:
        if owned and lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass


def _setup_log():
    """配置日志: 写文件 + 终端.

    Returns: callable _log(fmt, *args, level=INFO) — 兼容 _log("msg") 和 _log.info("msg").
    """
    import logging
    import logging.handlers  # RotatingFileHandler (2026-08-14 主日志轮转)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("memory-watchdog")
    logger.setLevel(logging.INFO)

    # 防止重复 handler (--detach + detach 内再 fork 时 logger 全局, 否则重复)
    if logger.handlers:
        for h in list(logger.handlers):
            logger.removeHandler(h)

    # 文件 handler — 2026-08-14: RotatingFileHandler (5MB, 保留 2 份)
    # 此前纯 FileHandler 追加, ikaros-memory-watchdog.log 曾涨到 29MB (~2.7MB/天).
    fh = logging.handlers.RotatingFileHandler(
        str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    # RotatingFileHandler 按 stream.tell() 判轮转; 追加模式下新进程 tell()=0,
    # 既存的大文件不会立即轮转 → 手动触发一次 (2026-08-14)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > fh.maxBytes:
        try:
            fh.doRollover()
        except OSError:
            pass
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
    print(f"  LLM       (:8080): {_check(LLM_PORT)} (local-llm)")

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
            # 2026-08-14: 子进程 stdout 不再继承 log_f 句柄 —— 该句柄无
            # FILE_SHARE_DELETE, 会锁住日志文件导致 RotatingFileHandler 的
            # doRollover rename 失败。子进程自身经 RotatingFileHandler 写日志,
            # stdout 仅冗余 (DEVNULL 即可)。
            proc = subprocess.Popen(
                [sys.executable, str(here / "ikaros-memory-watchdog.py")],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
