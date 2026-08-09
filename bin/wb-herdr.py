#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wb-herdr.py — 经 herdr pane 驱动 WorkBuddy (CodeBuddy) 常驻会话
================================================================
herdr 编排层：CodeBuddy 交互会话跑在 herdr pane（w1:p2）里，
本脚本用 herdr CLI 发 prompt / 读输出 / 等状态，不重复冷启动。

用法
----
  python bin/wb-herdr.py "把 TASK.md 里的任务做掉"          # 发任务并等输出
  python bin/wb-herdr.py "继续" --pane w1:p2                # 指定 pane
  python bin/wb-herdr.py "xxx" --read-only                  # 只读当前输出

前置条件
--------
- herdr server 运行中（herdr status 确认）
- CodeBuddy 交互会话已在 pane 中（首次需 pane run 启动 + 信任确认）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_HERDR = Path(r"E:\Ikaros\runtime\herdr\herdr.exe")
_DEFAULT_PANE = "w1:p2"
_SETTLE = 3          # 发送后等待稳定
_POLL = 8            # 轮询间隔
_MAX_WAIT = 600      # 最大等待秒数


def _herdr(*args: str, timeout: int = 60) -> str:
    proc = subprocess.run(
        [str(_HERDR), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)
    return proc.stdout + proc.stderr


def send(pane: str, text: str) -> None:
    _herdr("pane", "send-text", pane, text, timeout=30)
    time.sleep(_SETTLE)
    _herdr("pane", "send-keys", pane, "enter", timeout=30)


def read(pane: str, lines: int = 60) -> str:
    return _herdr("pane", "read", pane, "--source", "recent",
                  "--lines", str(lines), timeout=30)


def wait_settle(pane: str, max_wait: int, tail_marker: str = ">") -> str:
    """轮询直到输出尾部出现提示符（agent 回到空闲态）。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < max_wait:
        last = read(pane, lines=120)
        # CodeBuddy 空闲提示符是独立一行 '>'；working 时尾部是内容
        tail = [l.strip() for l in last.splitlines() if l.strip()]
        if tail and tail[-1] in (">", "> ", ">"):
            return last
        time.sleep(_POLL)
    return last


def main() -> int:
    ap = argparse.ArgumentParser(description="herdr 编排 WorkBuddy")
    ap.add_argument("prompt", nargs="?", default=None, help="任务描述")
    ap.add_argument("--pane", default=_DEFAULT_PANE)
    ap.add_argument("--read-only", action="store_true", help="只读输出不发送")
    ap.add_argument("--timeout", type=int, default=_MAX_WAIT)
    ap.add_argument("--lines", type=int, default=80)
    args = ap.parse_args()

    if args.read_only or args.prompt is None:
        out = read(args.pane, args.lines)
        print(out.strip())
        return 0

    send(args.pane, args.prompt)
    print(f"[wb-herdr] 已发送到 {args.pane}，等待完成（超时 {args.timeout}s）…",
          file=sys.stderr)
    final = wait_settle(args.pane, args.timeout)
    print(final.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
