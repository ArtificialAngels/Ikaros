#!/usr/bin/env python3
"""Launcher for the pure Hermes gateway (:8642) — 启动归属 (2026-08-14).

问题背景: 面板重构后没有组件负责拉起 :8642 gateway; 机器重启后无人启动它,
对话树 hermes 模式会静默降级本地 DeepSeek (bridge 空转).

本脚本提供 start/status/stop 三态, 供 9100 面板组件或手动使用:
    python bin/hermes-gateway.py [start|status|stop]

启动方式: 用 Hermes 自有 venv 的 python 跑 `hermes_cli.main gateway run --replace`
(detached, 无窗口). API_SERVER_KEY 从 $HERMES_HOME/.env (data/hermes-agent/.env) 读取,
gateway 启动 guard 会拒绝 <16 字符/占位符 key, 必须真实密钥.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("IKAROS_ROOT") or Path(__file__).resolve().parent.parent)
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or ROOT / "data" / "hermes-agent")
VENV_PY = ROOT / "runtime" / "hermes-agent" / "venv" / "Scripts" / "python.exe"
GATEWAY_PORT = 8642
PID_FILE = HERMES_HOME / "gateway.pid"
_SUBPROC_SERVICE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _load_api_key() -> str:
    """从 $HERMES_HOME/.env 读 API_SERVER_KEY (64 hex), 为空则拒绝启动."""
    env_file = HERMES_HOME / ".env"
    key = os.environ.get("API_SERVER_KEY", "").strip()
    if not key and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_SERVER_KEY=") and not line.startswith("#"):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if len(key) < 16 or key in ("ikaros-gateway-key", "your-api-key"):
        sys.stderr.write(f"[gateway] FATAL: valid API_SERVER_KEY required "
                         f"(got {len(key)} chars); read from {env_file}\n")
        sys.exit(2)
    return key


def _env() -> dict:
    env = dict(os.environ)
    env["HERMES_HOME"] = str(HERMES_HOME)
    env["API_SERVER_KEY"] = _load_api_key()
    env["PYTHONPATH"] = str(ROOT) + ";" + str(ROOT / "runtime" / "hermes-agent")
    return env


def _alive() -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", GATEWAY_PORT)) == 0


def cmd_start() -> None:
    if _alive():
        print(f"[gateway] :{GATEWAY_PORT} already up")
        return
    if not VENV_PY.is_file():
        sys.stderr.write(f"[gateway] FATAL: venv python not found: {VENV_PY}\n")
        sys.exit(2)
    key = _load_api_key()
    print(f"[gateway] starting hermes gateway :{GATEWAY_PORT} (detached)...")
    proc = subprocess.Popen(
        [str(VENV_PY), "-m", "hermes_cli.main", "gateway", "run", "--replace"],
        cwd=str(ROOT), env=_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_SUBPROC_SERVICE,
    )
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(json.dumps({"pid": proc.pid, "port": GATEWAY_PORT,
                                    "api_key_len": len(key), "ts": time.time()}),
                        encoding="utf-8")
    for _ in range(30):
        if _alive():
            print(f"[gateway] OK :{GATEWAY_PORT} (pid {proc.pid})")
            return
        time.sleep(1)
    sys.stderr.write("[gateway] started but not healthy within 30s "
                     "(check data/logs/hermes-dashboard.log)\n")
    sys.exit(1)


def cmd_status() -> None:
    print(f"[gateway] :{GATEWAY_PORT} {'UP' if _alive() else 'down'}")
    if PID_FILE.is_file():
        try:
            info = json.loads(PID_FILE.read_text(encoding="utf-8"))
            print(f"[gateway] last launch: pid={info.get('pid')} "
                  f"key_len={info.get('api_key_len')}")
        except Exception:
            pass


def cmd_stop() -> None:
    if not _alive():
        print(f"[gateway] :{GATEWAY_PORT} already down")
        return
    pid = None
    if PID_FILE.is_file():
        try:
            pid = json.loads(PID_FILE.read_text(encoding="utf-8")).get("pid")
        except Exception:
            pass
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[gateway] SIGTERM -> {pid}")
        except OSError:
            pass
    time.sleep(2)
    if _alive():
        print(f"[gateway] still up, force killing :{GATEWAY_PORT}...")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
    print(f"[gateway] :{GATEWAY_PORT} stopped" if not _alive() else "[gateway] still up")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        cmd_start()
    elif cmd == "status":
        cmd_status()
    elif cmd == "stop":
        cmd_stop()
    else:
        sys.stderr.write(f"usage: {sys.argv[0]} [start|status|stop]\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
