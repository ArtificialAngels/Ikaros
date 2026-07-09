"""process_learner.py — 未知进程联网识别 + 持久化学习

监测到 classify() 返回 category='unknown' 的进程时，用云端 LLM（DeepSeek /
MiniMax，本地 Qwen3-8B :8080 兜底）凭其对 Windows 软件的认知识别应用并分类，
然后把映射写回 activity_keywords.PROCESS_OVERRIDES_PATH（process_overrides.json）。
下次启动自动加载，实现"越用越懂哥哥在干嘛"。

隐私约定（重要）:
  * 只把**进程 exe 名**发给 LLM，绝不发送窗口标题 / URL / 文件路径
    （标题可能含账号、文件名、聊天内容等敏感信息）。
  * 隐私黑名单 (KeePass 等) 与自家应用 (ikaros-desktop-pet) 直接跳过，不联网。
  * 仅接受白名单 category，LLM 胡写会被丢弃。

配置:
  IKAROS_LEARN_PROCESSES=0 可关闭本功能（默认开启）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger("ikaros.learner")

# 内嵌 portable-python 不会自动把脚本目录加进 sys.path，这里显式补
_BIN = Path(__file__).resolve().parent
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))

# 仅允许这些类别写回，防止 LLM 污染分类体系
_ALLOWED = {"gaming", "work", "communication", "entertainment", "browser", "private"}

_PROMPT_SYS = (
    "你是一个 Windows 活动监测器的进程识别模块。给定进程可执行文件名"
    "（不含路径、不含窗口标题），请凭你对 Windows 软件的认知判断它属于哪个应用并分类。\n"
    "可分类别（只选一个）：\n"
    "  - work: 办公/开发/设计/IDE/编辑器/数据库/终端等生产力工具\n"
    "  - communication: 聊天/邮件/社交/会议类 IM\n"
    "  - entertainment: 视频/音乐/阅读等消遣\n"
    "  - gaming: 游戏或游戏平台（Steam/Epic 等）\n"
    "  - browser: 网页浏览器\n"
    "  - private: 密码管理器/银行/网银/加密钱包等极度隐私应用\n"
    "  - unknown: 完全无法判断或过于小众\n"
    "严格只输出一行 JSON，不要任何解释或 markdown 代码块：\n"
    '{"app": "应用中文名", "category": "上述类别之一", "subcategory": "细分如 ide/im/video", "confidence": 0.0~1.0}\n'
    "confidence 表示你的把握；不确定就返回 category=unknown。"
)


def _build_messages(proc: str) -> list[dict]:
    return [
        {"role": "system", "content": _PROMPT_SYS},
        {"role": "user", "content": f"进程可执行文件名: {proc}"},
    ]


async def _identify(proc: str) -> dict | None:
    """调 LLM 识别进程。返回 {category, subcategory, canonical} 或 None。"""
    from cloud_chat import (_load_env, _get_api_key,
                            _call_openai_compatible, _call_local_llm)
    env = _load_env()
    deepseek_key = _get_api_key(env, "DEEPSEEK_API_KEY")
    minimax_key = _get_api_key(env, "MINIMAX_API_KEY")
    msgs = _build_messages(proc)
    text = None

    # 1) 云端 LLM（优先，知识最广）
    if deepseek_key:
        try:
            text = await _call_openai_compatible(
                "https://api.deepseek.com/v1", deepseek_key, "deepseek-chat",
                msgs, 200, 0.0, "learn-process")
        except Exception as e:
            logger.debug("deepseek identify failed: %s", e)
    if not text and minimax_key:
        try:
            text = await _call_openai_compatible(
                "https://api.minimaxi.chat/v1", minimax_key, "MiniMax-M3",
                msgs, 200, 0.0, "learn-process")
        except Exception as e:
            logger.debug("minimax identify failed: %s", e)

    # 2) 本地 Qwen3-8B (:8080 兜底，断网/无 key 时也行)
    if not text:
        try:
            text = await _call_local_llm(msgs, max_tokens=200, temperature=0.0)
        except Exception as e:
            logger.debug("local identify failed: %s", e)

    if not text:
        return None

    # 解析 JSON（容错：剥 ``` 代码块 / 抽取第一个 {...}）
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        obj = json.loads(t)
    except Exception:
        m = re.search(r"\{.*?\}", t, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None

    cat = (obj.get("category") or "").strip().lower()
    if cat not in _ALLOWED:
        return None
    conf = float(obj.get("confidence", 0) or 0)
    app = (obj.get("app") or "").strip()
    if cat == "unknown" or not app or conf < 0.6:
        return None
    sub = (obj.get("subcategory") or cat).strip()[:20]
    return {"category": cat, "subcategory": sub, "canonical": app[:40]}


def _run_async(coro):
    """兼容"已在事件循环内"的调用：丢到新线程跑 asyncio.run。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)


def learn_process(proc: str) -> bool:
    """识别并持久化一个未知进程。成功写回返回 True，否则 False。

    proc 仅作为 exe 名使用；窗口标题 / URL 绝不外传。
    """
    if os.environ.get("IKAROS_LEARN_PROCESSES", "1") == "0":
        return False
    if not proc:
        return False
    from activity_keywords import (classify, add_override,
                                   PRIVATE_PROCESS, OWN_APP_PROCESS)
    p = proc.lower().strip()
    # 隐私 / 自家应用：不联网、不学习
    if p in PRIVATE_PROCESS or p in OWN_APP_PROCESS:
        return False
    # 已基本像个进程名（有扩展名或够长），避免把空串/乱码送 LLM
    if "." not in p and len(p) < 3:
        return False
    # 已经能分类就别学了
    if classify(proc, None, None)["category"] != "unknown":
        return False

    try:
        res = _run_async(_identify(p))
    except Exception as e:
        logger.debug("learn_process 识别失败: %s", e)
        return False
    if not res:
        logger.info("进程 %s 未能识别（LLM 无把握或离线），跳过", p)
        return False

    add_override(p, res["category"], res["subcategory"], res["canonical"])
    return True


if __name__ == "__main__":
    import sys as _sys
    for arg in _sys.argv[1:]:
        print(f"{arg} -> learned={learn_process(arg)}")
