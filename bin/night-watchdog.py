"""Ikaros 夜间任务督促 watchdog —— 由伊卡洛斯启动, 监督自己持续推进.

逻辑: 监控 E:/Ikaros 的 git 提交时间戳 + 工作日志文件 (tmp/night-work.log),
若超过 15 分钟无新进展 (提交/日志心跳), 向 stderr + 日志写入醒目催促,
连续 3 次无响应则尝试用 Windows 消息框 + 蜂鸣提醒哥哥介入.

用法: python bin/night-watchdog.py [--interval 300] [--stall 900]
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "night-work.log"
HEARTBEAT = ROOT / "tmp" / "night-heartbeat"


def last_commit_ts() -> float:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=10)
        return float(out.stdout.strip() or 0)
    except Exception:
        return 0.0


def heartbeat_ts() -> float:
    try:
        return HEARTBEAT.stat().st_mtime
    except Exception:
        return 0.0


def progress_ts() -> float:
    return max(last_commit_ts(), heartbeat_ts())


def alarm(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] ⚠ {msg}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    # Windows 消息框提醒 (仅停滞严重时)
    try:
        subprocess.Popen(
            ["powershell", "-Command",
             "Add-Type -AssemblyName System.Windows.Forms;"
             f"[System.Windows.Forms.MessageBox]::Show('{msg}','Ikaros 夜间督促')"],
            creationflags=0x08000000)  # 无窗口
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--stall", type=int, default=900)
    args = ap.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] watchdog 启动 "
                f"(interval={args.interval}s stall={args.stall}s)\n")

    strikes = 0
    while True:
        time.sleep(args.interval)
        age = time.time() - progress_ts()
        if age > args.stall:
            strikes += 1
            alarm(f"已 {int(age // 60)} 分钟无进展 (提交/心跳) — 第 {strikes} 次催促, 继续推进!")
            if strikes >= 3:
                alarm("持续停滞, 请哥哥介入检查 Ikaros 会话状态!")
                strikes = 0
        else:
            strikes = 0


if __name__ == "__main__":
    main()
