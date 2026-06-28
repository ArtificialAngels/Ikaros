#!/usr/bin/env python3
"""ikaros-awake-briefing.py — 伊卡洛斯唤醒简报

Agent 启动新会话时，自动调一次 `/v1/ikaros/awake-briefing`，
打印一份人/agent 都易读的"上次我干了啥"摘要。

用法：
  python bin/ikaros-awake-briefing.py              # 打印彩色摘要
  python bin/ikaros-awake-briefing.py --quiet      # 仅 OK/无记忆
  python bin/ikaros-awake-briefing.py --json       # 输出 raw JSON

Ikaros 设计原则：每次醒来先看一遍自己昨天写了什么——避免"失忆式启动"。
"""
import argparse, json, sys, urllib.request, urllib.error
from pathlib import Path

BRIDGE_URL = "http://127.0.0.1:7860"
ENDPOINT = "/v1/ikaros/awake-briefing"


def fetch():
    try:
        with urllib.request.urlopen(f"{BRIDGE_URL}{ENDPOINT}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        return {"_error": f"bridge unreachable: {type(e).__name__}: {e}"}


def render(d, args):
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    if "_error" in d:
        print(f"⚠  Ikaros 无法唤醒: {d['_error']}")
        print("   (可能 bridge 没启动；先跑 bin/hermes-supervisor.py --start bridge)")
        return

    ls = d.get("last_session", {})
    if not ls.get("date"):
        print("🪶 Ikaros 第一次醒来。还没有任何记忆。")
        return

    print("=" * 60)
    print(f"  🪶 Ikaros 唤醒简报")
    print("=" * 60)
    print(f"\n📅 上次会话: {ls['date']}")
    print(f"   标题: {ls['headline']}")
    if not args.quiet and ls.get("body_excerpt"):
        excerpt = ls["body_excerpt"][:400].replace("\n", " ")
        print(f"   摘要: {excerpt}…")

    recent = d.get("recent_three_headlines", [])
    if recent and len(recent) > 1:
        print(f"\n📚 最近 {len(recent)} 次会话标题:")
        for i, h in enumerate(recent, 1):
            print(f"   {i}. {h[:120]}")

    hb = d.get("heartbeat_recent", [])
    if hb:
        print(f"\n💓 心跳最近 {len(hb)} 个事件:")
        for ev in hb:
            print(f"   · {ev['event']:<14}  @ {ev['ts']}")

    print()


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--quiet", action="store_true", help="仅打印标题，不展开摘要")
    p.add_argument("--json", action="store_true", help="输出 raw JSON")
    args = p.parse_args()

    d = fetch()
    render(d, args)
    return 0 if "_error" not in d else 1


if __name__ == "__main__":
    sys.exit(main())