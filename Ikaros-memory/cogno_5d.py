"""cogno_5d.py — 伊卡洛斯 5 维认知锚 (哥哥 6-28 axiom)

每次对话自动注入 5 维元数据到 system prompt:
  1. 时间     [2026/7/5 08:30]
  2. 设备     [PZS0X@LEGION9]
  3. 地理     [上海/上海/中国]
  4. 情绪推断 [开心/好奇/平静/...]
  5. 上下文   [新对话/第N轮, 上轮:xxx]

总长 < 250 chars (token 经济原则)

用法:
  from cogno_5d import enrich, enrich_reply
  prompt_prefix = enrich(user_text, history)   # → str, 注入 system prompt 头
  tagged = enrich_reply(reply, user_text)       # → str, 给回复加 5D 标签

设计:
  - 从 cloud_chat.py 提取, 变为共享模块
  - hermes-agent/agent/system_prompt.py 可直接 import
  - 记忆系统 ingest 时可调 enrich_reply 附加元数据
  - 失败静默: 任何一维失败 → [未知], 不阻塞 chat
"""

from __future__ import annotations

import json
import os
import socket
import time
from datetime import datetime
from typing import Optional

# ─── 缓存 ───

_geo_cache: Optional[str] = None
_geo_cache_time: float = 0.0
_GEO_TTL = 300  # 5 min

_machine_cache: Optional[str] = None
_machine_cache_time: float = 0.0
_MACHINE_TTL = 300  # 5 min

# 上下文追踪
_turn_counter: int = 0
_last_user_text: str = ""


def reset_context() -> None:
    """重置上下文计数器 (新会话时调用)."""
    global _turn_counter, _last_user_text
    _turn_counter = 0
    _last_user_text = ""


# ─── 维度 1: 时间 ───

def get_time_str() -> str:
    """当前时间 '2026/7/5 08:30'."""
    try:
        now = datetime.now()
        return f"{now.year}/{now.month}/{now.day} {now.hour:02d}:{now.minute:02d}"
    except Exception:
        return "[时间未知]"


# ─── 维度 2: 设备 ───

def get_machine_id() -> str:
    """设备标识 'PZS0X@LEGION9', 5 min TTL."""
    global _machine_cache, _machine_cache_time
    now = time.time()
    if _machine_cache and (now - _machine_cache_time) < _MACHINE_TTL:
        return _machine_cache
    try:
        hostname = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "unknown-pc"
        username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"
        _machine_cache = f"{username}@{hostname}"
    except Exception:
        _machine_cache = "[设备未知]"
    _machine_cache_time = now
    return _machine_cache


# ─── 维度 3: 地理 ───

def _fetch_geo_sync() -> Optional[str]:
    """轻量同步 IP 地理位置查询 (ip-api.com, 免费, 无需 key)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(("ip-api.com", 80))
        sock.sendall(b"GET /json?fields=city,regionName,country HTTP/1.1\r\nHost: ip-api.com\r\nConnection: close\r\n\r\n")
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()
        text = response.decode("utf-8", errors="replace")
        # 提取 JSON body
        idx = text.find("{")
        if idx < 0:
            return None
        data = json.loads(text[idx:])
        city = data.get("city", "")
        region = data.get("regionName", "")
        country = data.get("country", "")
        if city:
            return f"{city}/{region or '-'}/{country or '-'}"
        return None
    except Exception:
        return None


def get_geo_location() -> str:
    """地理位置 '上海/上海/中国', 5 min TTL, 三级 fallback."""
    global _geo_cache, _geo_cache_time
    now = time.time()
    if _geo_cache and (now - _geo_cache_time) < _GEO_TTL:
        return _geo_cache
    try:
        _geo_cache = _fetch_geo_sync()
    except Exception:
        _geo_cache = None
    if not _geo_cache:
        _geo_cache = "未知"
    _geo_cache_time = now
    return _geo_cache


# ─── 维度 4: 情绪推断 ───

_EMOTION_KEYWORDS = {
    "开心": ["哈哈", "呵呵", "开心", "高兴", "太好了", "棒", "厉害", "赞", "牛",
             "喜欢", "爱", "😊", "😄", "🎉", "好耶", "nice", "great", "awesome",
             "wow", "wonderful", "优秀", "漂亮", "完美", "耶", "666"],
    "好奇": ["为什么", "怎么", "如何", "什么", "能不能", "可以吗", "请问",
             "想知道", "好奇", "怎么回事", "why", "how", "what", "是不是"],
    "感谢": ["谢谢", "感谢", "多谢", "辛苦了", "麻烦了", "thank", "thanks",
             "thx", "appreciate", "感恩"],
    "烦躁": ["烦", "讨厌", "崩溃", "又", "怎么又", "受不了", "气死", "靠",
             "妈的", "fuck", "shit", "damn", "wtf", "垃圾", "bug"],
    "悲伤": ["难过", "伤心", "哭", "呜呜", "唉", "哎", "sigh", "sad",
             "失落", "孤独", "寂寞"],
    "平静": [],  # default fallback
}


def infer_emotion(text: str) -> str:
    """关键词匹配情绪推断. 无匹配 → '平静'."""
    if not text:
        return "平静"
    t = text.lower()
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        if emotion == "平静":
            continue
        for kw in keywords:
            if kw in t:
                return emotion
    return "平静"


# ─── 维度 5: 上下文压缩 ───

def compress_context(user_text: str, history: list | None = None) -> str:
    """上下文压缩: 轮次 + 上轮摘要 + 本轮开头, < 50 chars."""
    global _turn_counter, _last_user_text
    _turn_counter += 1
    last = _last_user_text
    _last_user_text = user_text[:80]
    excerpt = user_text[:40]

    if _turn_counter <= 1:
        return "新对话开始"
    elif not last:
        return f"第{_turn_counter}轮, 问: {excerpt}…"
    else:
        return f"第{_turn_counter}轮, 上轮: {last[:30]}…, 本轮: {excerpt}…"


# ─── 组合 API ───

def enrich(user_text: str, history: list | None = None) -> str:
    """返回 ~250 chars 的 5 维 metadata 字符串, 塞 system prompt 头.

    失败静默: 任何一维失败 → [未知], 不抛异常.
    """
    try:
        t = get_time_str()
        m = get_machine_id()
        g = get_geo_location()
        e = infer_emotion(user_text)
        c = compress_context(user_text, history)
        return (
            f"【认知5D】时间:{t} | 设备:{m} | 地理:{g} | 情绪:{e} | 上下文:{c}"
        )
    except Exception:
        return "【认知5D】(获取失败, 静默跳过)"


def enrich_reply(reply: str, user_text: str = "", emotion_after: str = "") -> dict:
    """给回复加 5 维标签, 用于记忆层 ingest.

    Returns: dict with metadata fields (不修改 reply 本身).
    """
    try:
        return {
            "time": get_time_str(),
            "machine": get_machine_id(),
            "geo": get_geo_location(),
            "emotion_user": infer_emotion(user_text) if user_text else "未知",
            "emotion_reply": emotion_after or infer_emotion(reply),
            "context_turn": _turn_counter,
        }
    except Exception:
        return {"time": get_time_str()}


# ─── CLI 测试 ───

if __name__ == "__main__":
    print("=== Cogno 5D Test ===")
    print(f"  时间: {get_time_str()}")
    print(f"  设备: {get_machine_id()}")
    print(f"  地理: {get_geo_location()}")
    print(f"  情绪(开心): {infer_emotion('太棒了！好开心！')}")
    print(f"  情绪(好奇): {infer_emotion('这是怎么回事？')}")
    print(f"  情绪(平静): {infer_emotion('好的，收到')}")
    print(f"  上下文: {compress_context('你好')}")
    print(f"  上下文: {compress_context('你能帮我看看这个bug吗')}")
    print()
    print(f"  enrich: {enrich('哥哥好！今天天气真好')}")
    print(f"  enrich_reply: {enrich_reply('我也觉得开心呢', '今天好开心')}")
