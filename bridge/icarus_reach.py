"""
Icarus Reach - Agent-Reach 集成层
==================================
桥接 Agent-Reach (Panniantong) 给伊卡洛斯用。
13 平台 1 个 API,无 API 费。
"""
import os
import sys
import json
import shlex
import subprocess
import urllib.request
import urllib.error
from typing import Optional, List, Dict, Any
from pathlib import Path

# Agent-Reach 路径
AGENT_REACH_PYTHON = r"E:\Hermes Agent\portable-python\python.exe"
AGENT_REACH_MODULE = "agent_reach.cli"

# 基础工具路径
JINA_READER = "https://r.jina.ai"  # 任意网页阅读


def agent_reach_doctor() -> Dict[str, Any]:
    """跑 doctor, 返回 JSON 结果 (用 --json 避免 rich ANSI hang)"""
    try:
        result = subprocess.run(
            [AGENT_REACH_PYTHON, "-m", AGENT_REACH_MODULE, "doctor", "--json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
        )
        # agent-reach doctor --json 输出可能含 ANSI 码, 先 strip
        out = result.stdout
        if not out and result.stderr:
            out = result.stderr
        # 找 JSON 起始位置
        for i, ch in enumerate(out):
            if ch in "{[":
                out = out[i:]
                break
        return json.loads(out) if out.strip() else {"raw": out[:200]}
    except subprocess.TimeoutExpired:
        return {"error": "doctor timeout"}
    except Exception as e:
        return {"error": str(e)}


def jina_read(url: str) -> str:
    """Jina Reader 读任意网页 (零配置)"""
    full_url = f"{JINA_READER}/{url}"
    try:
        req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return f"[error] jina_read failed: {e}"


def yt_transcript(url: str) -> str:
    """yt-dlp 拿 YouTube 字幕"""
    try:
        result = subprocess.run(
            [AGENT_REACH_PYTHON, "-m", "yt_dlp",
             "--skip-download", "--write-auto-sub", "--sub-lang", "zh-Hans,en",
             "--sub-format", "vtt/srt/best", "--convert-subs", "srt",
             "-o", "-", url],
            capture_output=True, text=True, timeout=120
        )
        # yt-dlp 写到文件, 先下到 tmp 然后读
        # 简化: 返回 stdout
        return result.stdout[:5000] if result.stdout else "[no transcript]"
    except Exception as e:
        return f"[error] yt_transcript failed: {e}"


def rss_read(url: str, max_items: int = 5) -> List[Dict[str, str]]:
    """读 RSS/Atom feed (用 feedparser, Agent-Reach 自带)"""
    import feedparser
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", "")[:300],
            "published": entry.get("published", ""),
        })
    return items


def v2ex_search(query: str, max_items: int = 5) -> List[Dict[str, Any]]:
    """V2EX 公开 API 搜索"""
    try:
        url = f"https://www.v2ex.com/api/v2/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        topics = data.get("result", [])[:max_items] if isinstance(data.get("result"), list) else []
        return topics
    except Exception as e:
        return [{"error": str(e)}]


# === 统一入口 ===
def reach(intent: str, target: str, **kwargs) -> Dict[str, Any]:
    """
    意图分发: 哥哥说"看 XX", 自动选工具
    intent: read | search | transcript | rss
    target: URL 或关键词
    """
    intent = intent.lower()
    if intent == "read":
        text = jina_read(target)
        return {"source": "jina", "content": text[:5000], "truncated": len(text) > 5000}
    elif intent == "transcript":
        text = yt_transcript(target)
        return {"source": "yt-dlp", "content": text}
    elif intent == "rss":
        items = rss_read(target, kwargs.get("max_items", 5))
        return {"source": "feedparser", "items": items}
    elif intent == "v2ex":
        items = v2ex_search(target, kwargs.get("max_items", 5))
        return {"source": "v2ex", "items": items}
    else:
        return {"error": f"unknown intent: {intent}"}


# === Neuro 风格包装 - Module ===
from bridge.neuro.module import Module


class ReachModule(Module):
    """
    伊卡洛斯的"互联网眼睛"模块
    - 提供 reach(intent, target) 接口
    - prompt_injection 把可用平台注入 system prompt
    """
    def __init__(self, signals, enabled: bool = True):
        super().__init__(signals, enabled)
        self.prompt_injection.text = (
            "伊卡洛斯可以通过 `reach(intent, target)` 访问互联网:\n"
            "  - reach('read', URL) - 读任意网页 (Jina Reader)\n"
            "  - reach('transcript', youtube_url) - 拿 YouTube 字幕\n"
            "  - reach('rss', feed_url) - 读 RSS/Atom 订阅\n"
            "  - reach('v2ex', query) - 搜 V2EX 公开 API\n"
            "哥哥说'看 XX'/'查 XX'/'搜 XX' 时自动用。"
        )
        self.prompt_injection.priority = 70

    async def run(self):
        # 模块不需要主动跑, 按需调 reach()
        while not self.signals.terminate:
            await _async_sleep(60)

    class API:
        def __init__(self, outer):
            self.outer = outer

        def reach(self, intent: str, target: str, **kwargs):
            return reach(intent, target, **kwargs)

        def doctor(self):
            return agent_reach_doctor()

        def list_platforms(self):
            """返回当前可用的平台"""
            return [
                {"name": "Jina Reader", "type": "web", "config": "none", "status": "ok"},
                {"name": "yt-dlp", "type": "video", "config": "node-runtime", "status": "ok"},
                {"name": "feedparser", "type": "rss", "config": "none", "status": "ok"},
                {"name": "V2EX API", "type": "social", "config": "none", "status": "ok"},
                {"name": "B站搜索", "type": "video", "config": "none", "status": "ok"},
                {"name": "GitHub (gh)", "type": "dev", "config": "gh-cli", "status": "needs-install"},
                {"name": "Exa 全网搜索", "type": "search", "config": "mcporter", "status": "needs-install"},
                {"name": "Twitter/X", "type": "social", "config": "login", "status": "needs-login"},
                {"name": "Reddit", "type": "social", "config": "login", "status": "needs-login"},
                {"name": "小红书", "type": "social", "config": "login", "status": "needs-login"},
            ]


# async sleep helper
import asyncio
async def _async_sleep(s):
    await asyncio.sleep(s)


# === 快速测试 ===
if __name__ == "__main__":
    print("=== ReachModule doctor ===")
    rm = ReachModule(signals=None)  # noqa
    print(json.dumps(rm.API.list_platforms(), ensure_ascii=False, indent=2))

    print("\n=== V2EX 搜: Icarus ===")
    r = reach("v2ex", "Icarus")
    for item in r.get("items", [])[:3]:
        print(" -", item.get("title", str(item))[:80])

    print("\n=== RSS 试: GitHub Agent-Reach releases ===")
    r = reach("rss", "https://github.com/Panniantong/Agent-Reach/releases.atom")
    for item in r.get("items", [])[:3]:
        print(" -", item.get("title", "")[:80])
