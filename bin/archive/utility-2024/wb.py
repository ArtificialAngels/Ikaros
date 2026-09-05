#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb.py — WorkBuddy (CodeBuddy) CLI 封装
=====================================
让 Ikaros / 子代理进程直接驱动 WorkBuddy，不再需要文件交接（TASK.md 手动转达）。

用法
----
  python bin/wb.py "把 TASK.md 里的任务做掉"            # 单次问答（新会话）
  python bin/wb.py "继续做剩下的部分" --session keep    # 沿用最近一次 wb 会话（延续上下文）
  python bin/wb.py "xxx" --session <uuid>               # 指定会话
  python bin/wb.py "xxx" --model glm-5.2                # 指定模型（默认 hy3，免费）
  python bin/wb.py "xxx" --effort high                  # 调整推理强度（默认 max）
  python bin/wb.py "xxx" --cwd E:/some/project          # 指定工作区（默认 E:/Ikaros）

说明
----
- 底层调用 WorkBuddy 自带 CLI：AppData/Local/Programs/WorkBuddy/resources/
  app.asar.unpacked/cli/bin/codebuddy（v2.115.0+）
- 会话持久化：--session keep 读取 ~/.workbuddy/wb-session-last.txt 复用最近
  一次会话 id（WorkBuddy 侧会按 session-id 持久化上下文，跨进程连续）
- 退出码：0=成功；非 0 见 stderr
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ── WorkBuddy CLI 定位 ──────────────────────────────────────────────────────
_WB_CLI = (
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\PZS0X\AppData\Local"))
    / "Programs" / "WorkBuddy" / "resources" / "app.asar.unpacked"
    / "cli" / "bin" / "codebuddy"
)
_SESSION_MARKER = Path.home() / ".workbuddy" / "wb-session-last.txt"


def _resolve_cli() -> Path:
    if _WB_CLI.exists():
        return _WB_CLI
    # 兜底：PATH 里找 codebuddy / cbc
    for name in ("codebuddy", "cbc"):
        for p in os.environ.get("PATH", "").split(os.pathsep):
            cand = Path(p) / (name + ".exe")
            if cand.exists():
                return cand
    sys.exit(f"[wb] 找不到 WorkBuddy CLI: {_WB_CLI}")


def main() -> int:
    ap = argparse.ArgumentParser(description="WorkBuddy CLI wrapper")
    ap.add_argument("prompt", help="任务描述（支持多行）")
    ap.add_argument("--session", default="new",
                    help="new=新会话 | keep=沿用最近 | <uuid>=指定")
    ap.add_argument("--model", default="hy3",
                    help="模型 id（默认 hy3——免费；codebuddy --model 支持列表）")
    ap.add_argument("--effort", default="max",
                    help="推理强度（默认 max；可选 minimal/low/medium/high/xhigh/max）")
    ap.add_argument("--perm", default="default",
                    help="权限模式（默认 default=需人工批准；编辑文件用 acceptEdits；完全跳过用 bypassPermissions）")
    ap.add_argument("--cwd", default=str(Path.cwd()),
                    help="工作目录（codebuddy 的工作区）")
    ap.add_argument("--timeout", type=int, default=900,
                    help="最大等待秒数（默认 900）")
    args = ap.parse_args()

    cli = _resolve_cli()

    # 会话 id 解析
    session_id = None
    if args.session == "keep":
        if _SESSION_MARKER.exists():
            session_id = _SESSION_MARKER.read_text(encoding="utf-8").strip()
            print(f"[wb] 沿用会话 {session_id}", file=sys.stderr)
        else:
            print("[wb] 无历史会话，改用新会话", file=sys.stderr)
    elif args.session != "new":
        session_id = args.session

    cmd = [str(cli), "-p", args.prompt, "--model", args.model,
           "--effort", args.effort]
    if args.perm != "default":
        cmd += ["--permission-mode", args.perm]
    if session_id:
        cmd += ["--session-id", session_id]
    else:
        # 新会话也生成固定 id，便于 keep 复用
        session_id = f"ikaros-{int(time.time())}"
        cmd += ["--session-id", session_id]
        _SESSION_MARKER.write_text(session_id, encoding="utf-8")

    print(f"[wb] cmd: codebuddy -p (session={session_id}, cwd={args.cwd}, model={args.model})",
          file=sys.stderr)

    # codebuddy 是无扩展名 bash 脚本（shebang node），Windows 下需经 bash 执行
    bash = os.environ.get("BASH", r"C:\Program Files\Git\bin\bash.exe")
    if not Path(bash).exists():
        bash = str(Path(os.environ.get("LOCALAPPDATA", ""))
                   / "Programs" / "WorkBuddy" / "resources" / "app.asar.unpacked"
                   / "cli" / "vendor" / "PortableGit" / "bin" / "bash.exe")
    cmd = [bash, "-lc", " ".join(f"'{c}'" for c in cmd)]

    try:
        proc = subprocess.run(
            cmd, cwd=args.cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[wb] 超时（>{args.timeout}s），任务仍在 WorkBuddy 侧继续",
              file=sys.stderr)
        return 124

    if proc.stdout:
        print(proc.stdout.strip())
    if proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
