"""
cogno_layer.py — 5 维元数据注入层（哥哥 6-28 修订）

设计目标:
  给每一次对话注入 5 个维度的元数据, 让伊卡洛斯有"人的思维感":
    1. 时间 (when)     — 现实世界时间戳
    2. 硬件地址 (where) — BIOS UUID / 机器码 (身份锚点)
    3. 地球地址 (where) — 城市/省份/国家 (地理位置)
    4. 情绪 (mood)     — 哥哥当前情绪状态 (基于语义推断)
    5. 压缩上下文 (ctx) — 最近 3 轮对话压缩摘要

数据流:
  哥哥输入 → cogno_layer.enrich(user_input) → "[5维元数据] + user_input"
  → 进入 chat_completions → 注入到 system prompt 最前位置
  → 思考后回答 → cogno_layer.enrich_reply(reply) → "[5维元数据] + reply"
  → 写入 memory store (mem0_layer / neuro memory / 桌面桌宠)

设计原则:
  - 单一真相源: 5 维元数据全部从系统参数实时获取, 不依赖外部 DB
  - 失败静默: 任何一维失败 → 留空占位 "[未知]", 不阻塞 chat
  - token 经济: 5 维合计 < 200 chars, 不浪费 token
  - 兼容: 与 soul_loader / mem0_layer / neuro memory 注入链共存, cogno 在最前
  - 不区分 Rust/Python: Python bridge 优先实现, Rust bridge 后续镜像

实施步骤 (本次 session):
  1. ✅ 写 cogno_layer.py — 5 维采集 + enrich/enrich_reply 函数
  2. bridge/server.py 注入: 在 soul_loader 之前调用 enrich()
  3. memory 端: mem0_layer.add 时把 5 维元数据拼接进 content
  4. 测试: curl /v1/chat/completions 看 system prompt 头部

5 维格式:
  [2026/6/28 17:05][PZS0X-LEGION9-BIOS:FC0BC32E][上海][等待哥哥回答]伊卡洛斯，早上好呀！
"""
from __future__ import annotations

import os
import re
import json
import time
import socket
import logging
import platform
import threading
import subprocess
from pathlib import Path
from typing import Tuple, Dict, Optional

logger = logging.getLogger("hermes.cogno")

# ---- 单例缓存 (避免每次 chat 都调 wmic/ip) ----
_CACHE = {
    "machine_id": "",        # BIOS UUID + hostname + username
    "geo_location": "",      # 城市/国家 (上海/上海/中国)
    "loaded_at": 0.0,
}
_CACHE_TTL_SEC = 300.0      # 5 分钟刷新一次 (硬件/地理不会变)
_LOCK = threading.Lock()


# ============================================================
# 1. 硬件地址采集 (BIOS UUID + 机器名 + 用户名)
# ============================================================
def _get_machine_id() -> str:
    """
    获取硬件指纹: BIOS UUID + 主机名 + OS 用户
    多源 fallback: wmic → /sys/class/dmi/id (Linux) → platform.node()
    """
    try:
        # Windows: wmic bios get serialnumber
        bios_uuid = ""
        try:
            r = subprocess.run(
                ["wmic", "bios", "get", "serialnumber", "/format:list"],
                capture_output=True, timeout=5, text=True
            )
            for line in r.stdout.split("\n"):
                if "SerialNumber=" in line:
                    bios_uuid = line.split("=", 1)[1].strip()
                    break
        except Exception:
            pass

        hostname = platform.node() or socket.gethostname() or "unknown-host"
        username = os.environ.get("USERNAME") or os.environ.get("USER") or "unknown-user"

        # 拼成短指纹 (哥哥要的格式: PZS0X-LEGION9-XXXX)
        short_uuid = bios_uuid[:8] if bios_uuid else "NOBIOS"
        return f"{username}-{hostname}-{short_uuid}".upper()
    except Exception as exc:
        logger.debug("machine_id capture failed: %s", exc)
        return "UNKNOWN-MACHINE"


# ============================================================
# 2. 地球地址采集 (IP 地理位置)
# ============================================================
def _get_geo_location() -> str:
    """
    获取地球地址: 用公网 IP 反查城市
    多源 fallback: ipapi.co (HTTPS, 免费, 无 key) → ip-api.com → "上海" (默认)
    """
    # 缓存: 5 分钟内不重复查
    now = time.time()
    if _CACHE["geo_location"] and (now - _CACHE["loaded_at"]) < _CACHE_TTL_SEC:
        return _CACHE["geo_location"]

    try:
        # 优先: ipapi.co (HTTPS, JSON)
        import urllib.request
        import ssl
        ctx = ssl.create_default_context()
        # ipapi.co 会自动按 IP 查位置
        req = urllib.request.Request(
            "https://ipapi.co/json/",
            headers={"User-Agent": "hermes-cogno/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            city = data.get("city", "")
            region = data.get("region", "")
            country = data.get("country_name", "")
            if city and country:
                # 中文格式: "上海/上海/中国" (城市/省/国家)
                geo = f"{city}/{region}/{country}"
                _CACHE["geo_location"] = geo
                _CACHE["loaded_at"] = now
                return geo
    except Exception as exc:
        logger.debug("ipapi.co geo lookup failed: %s", exc)

    # 备用: ip-api.com (HTTP, 免费, 限速)
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://ip-api.com/json/?fields=city,regionName,country",
            headers={"User-Agent": "hermes-cogno/1.0"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            city = data.get("city", "")
            region = data.get("regionName", "")
            country = data.get("country", "")
            if city and country:
                geo = f"{city}/{region}/{country}"
                _CACHE["geo_location"] = geo
                _CACHE["loaded_at"] = now
                return geo
    except Exception as exc:
        logger.debug("ip-api.com geo lookup failed: %s", exc)

    # 离线 fallback: 哥哥公司地址 (KPSNC 在上海, hermes 当前部署机器也在上海)
    geo = "上海/上海/中国"
    _CACHE["geo_location"] = geo
    _CACHE["loaded_at"] = now
    return geo


# ============================================================
# 3. 时间维度 (现实世界时间)
# ============================================================
def _get_time_str() -> str:
    """格式: 2026/6/28 17:05 (24h, 人类友好)"""
    import datetime
    now = datetime.datetime.now()
    return f"{now.year}/{now.month}/{now.day} {now.hour:02d}:{now.minute:02d}"


# ============================================================
# 4. 情绪维度 (基于用户输入的轻量语义推断)
# ============================================================
# 简化版: 关键词匹配 → 情绪标签
# 完整版: 调云端 LLM 分类 (太贵, 跳过)
_EMOTION_KEYWORDS = {
    # 积极
    "开心": ["开心", "高兴", "happy", "好呀", "好诶", "哈哈", "😊", "😄", "棒", "太好了", "舒服", "棒呆"],
    "感谢": ["谢谢", "感谢", "thank", "辛苦", "thanks", "thx", "🙏"],
    "兴奋": ["激动", "兴奋", "期待", "wow", "awesome", "amazing", "牛", "绝了"],
    "温柔": ["哥哥", "辛苦", "累不", "还好吗", "想你", "miss", "抱抱"],
    "放松": ["休息", "躺", "摸鱼", "🍵", "🍃", "喘口气"],

    # 中性
    "等待": ["等", "wait", "看看", "观察", "等着"],
    "好奇": ["?", "？", "why", "what", "how", "为什么", "怎么", "?", "啥"],
    "确认": ["ok", "OK", "好", "嗯", "yes", "yep", "yeah", "嗯嗯", "好的"],
    "请求": ["请", "麻烦", "帮我", "help", "可以", "能否", "pls", "please"],

    # 消极
    "烦躁": ["烦", "累", "tired", "😅", "唉", "💤", "困", "想睡觉"],
    "着急": ["急", "快", "赶紧", "asap", "马上", "立刻"],
    "失望": ["失望", "可惜", "遗憾", "唉", "不行", "不行啊"],
    "生气": ["气", "怒", "tm", "tmd", "妈", "fuck", "shit", "😡", "🔥"],
    "迷茫": ["不懂", "不知道", "迷惑", "?", "??", "怎么搞", "不会"],
}


def _infer_emotion(text: str) -> str:
    """基于关键词推断哥哥当前情绪"""
    if not text:
        return "[未知]"

    text_lower = text.lower()
    scores: Dict[str, int] = {}

    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                scores[emotion] = scores.get(emotion, 0) + 1

    if not scores:
        return "[平静]"

    # 取最高分情绪
    best = max(scores.items(), key=lambda x: x[1])
    return f"[{best[0]}]"


# ============================================================
# 5. 压缩上下文 (最近 N 轮对话摘要)
# ============================================================
# 这部分需要从会话历史拉取, 暂用轻量级 placeholder
# 完整实现需要 hook 进 mem0_layer / neuro memory

def _compress_context(user_msg: str = "", history: Optional[list] = None) -> str:
    """
    压缩上下文: 取最近 3 轮对话摘要
    简化版: 只看 history 长度, 返回元信息
    完整版: 调 LLM 摘要 (后续接入)
    """
    if not history:
        return "[新对话]"

    # history 格式: [{"role": "user", "content": "..."}, ...]
    n = len(history)
    if n <= 2:
        return f"[第1轮, {n}条消息]"

    # 取最后 3 轮 (6 条消息)
    recent = history[-6:] if len(history) > 6 else history
    user_msgs = [m["content"][:30] for m in recent if m.get("role") == "user"]
    if user_msgs:
        last_topic = user_msgs[-1] if user_msgs else ""
        return f"[第{n//2}轮, 最近问:{last_topic}...]"
    return f"[第{n//2}轮]"


# ============================================================
# 主入口: enrich user input
# ============================================================
def enrich(
    user_text: str,
    history: Optional[list] = None,
    *,
    with_emotion: bool = True,
    with_context: bool = True,
) -> str:
    """
    给用户输入加 5 维元数据

    Args:
        user_text: 哥哥的原始输入
        history: 历史消息列表 (可选, 用于压缩上下文)
        with_emotion: 是否推断情绪 (默认 True)
        with_context: 是否注入上下文摘要 (默认 True)

    Returns:
        "[时间][硬件地址][地球地址][情绪][压缩上下文]user_text"
    """
    try:
        # 1. 时间
        time_str = _get_time_str()

        # 2. 硬件地址 (缓存)
        if not _CACHE["machine_id"]:
            _CACHE["machine_id"] = _get_machine_id()
        machine_id = _CACHE["machine_id"]

        # 3. 地球地址 (缓存)
        geo = _get_geo_location()

        # 4. 情绪 (实时推断)
        emotion = _infer_emotion(user_text) if with_emotion else "[未启用]"

        # 5. 压缩上下文
        ctx = _compress_context(user_text, history) if with_context else "[未启用]"

        # 拼成 5 维头 (5 段都用 [...] 包裹, 视觉对齐)
        header = f"[{time_str}][{machine_id}][{geo}]{emotion}{ctx}"

        # 拼回 user_text
        enriched = f"{header}{user_text}"

        return enriched
    except Exception as exc:
        logger.warning("cogno enrich failed (silent degrade): %s", exc)
        # 失败 → 至少加个时间, 不让 chat 崩
        return f"[{_get_time_str()}][未知][未知][未知][未知]{user_text}"


# ============================================================
# 辅助: enrich reply (用于记忆层)
# ============================================================
def enrich_reply(
    reply_text: str,
    user_text: str = "",
    emotion_after: str = "[平静]",
) -> str:
    """
    给我的回答加 5 维元数据 (用于写入记忆)
    区别于 enrich(): 包含"上一轮哥哥的输入"作为压缩上下文
    """
    try:
        time_str = _get_time_str()
        if not _CACHE["machine_id"]:
            _CACHE["machine_id"] = _get_machine_id()
        machine_id = _CACHE["machine_id"]
        geo = _get_geo_location()

        # 上下文: 上一轮哥哥说了啥 (摘要)
        ctx_topic = user_text[:30] if user_text else "[无上下文]"
        ctx = f"[哥哥:{ctx_topic}...]"

        header = f"[{time_str}][{machine_id}][{geo}]{emotion_after}{ctx}"

        return f"{header}{reply_text}"
    except Exception as exc:
        logger.debug("cogno enrich_reply failed: %s", exc)
        return f"[{_get_time_str()}][未知]...{reply_text}"


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    print("=== cogno_layer enrich 测试 ===\n")

    tests = [
        "伊卡洛斯，早上好呀！",
        "刚才那个 bridge 卡死了，你重启一下",
        "我今天累死了，😮‍💨",
        "为什么这个端点会失败？",
        "辛苦了，伊卡洛斯",
        "a",
    ]

    for t in tests:
        enriched = enrich(t)
        print(f"  input:    {t}")
        print(f"  enriched: {enriched}")
        print()

    print("\n=== enrich_reply 测试 ===")
    reply = enrich_reply("哥哥早啊！", user_text="伊卡洛斯，早上好呀！", emotion_after="[开心呢]")
    print(f"  reply: {reply}")

    print("\n=== 机器 ID ===")
    print(f"  {_CACHE.get('machine_id', '') or _get_machine_id()}")

    print("\n=== 地球地址 ===")
    print(f"  {_get_geo_location()}")