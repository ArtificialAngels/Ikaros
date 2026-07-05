"""
🪶 cloud_chat.py — 桌宠直调 cloud LLM（替代 bridge /v1/chat/completions）

去桥架构核心模块。每次对话自动注入：
  [soul] axiom.md 中的伊卡洛斯身份公理
  [cogno 5D] 时间 / 设备 / 地理 / 情绪推断 / 上下文压缩

用法:
  from cloud_chat import cloud_chat
  reply = await cloud_chat("哥哥说的话", session_id="...")

依赖:
  - httpx (推荐) 或 urllib (回退)
  - 环境变量: DEEPSEEK_API_KEY 或 MINIMAX_CN_API_KEY
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time as time_module
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("ikaros.cloud_chat")

# ─── 路径常量 ───

_HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", "E:\\Ikaros"))
_ENV_PATH = _HERMES_ROOT / "data" / "hermes-agent" / ".env"
_AXIOM_PATH = _HERMES_ROOT / "ikaros-identity" / "axiom.md"
_LOCAL_LLM_URL = os.environ.get(
    "IKAROS_LLM_URL",
    os.environ.get("HERMES_LOCAL_LLM_URL", "http://127.0.0.1:8080/v1"),
)

# ─── 缓存 ───

_axiom_cache: Optional[str] = None
_env_cache: Optional[dict[str, str]] = None

# ─── cogno 5D 组件 (委托给 Ikaros-memory/cogno_5d.py 共享模块) ───

def _load_cogno():
    """Lazy-import cogno_5d module (avoid circular import at module level)."""
    try:
        cogno_path = str(_HERMES_ROOT / "Ikaros-memory")
        if cogno_path not in sys.path:
            sys.path.insert(0, cogno_path)
        import cogno_5d
        return cogno_5d
    except Exception:
        return None


def _get_time_str() -> str:
    """维度 1: 时间 — 委托 cogno_5d.get_time_str()"""
    c = _load_cogno()
    return c.get_time_str() if c else datetime.now().strftime("%Y/%m/%d %H:%M")


def _get_machine_id() -> str:
    """维度 2: 设备 — 委托 cogno_5d.get_machine_id()"""
    c = _load_cogno()
    return c.get_machine_id() if c else "unknown"


def _get_geo_location() -> str:
    """维度 3: 地理 — 委托 cogno_5d.get_geo_location()"""
    c = _load_cogno()
    return c.get_geo_location() if c else "未知"


def _infer_emotion(text: str) -> str:
    """维度 4: 情绪推断 — 委托 cogno_5d.infer_emotion()"""
    c = _load_cogno()
    return c.infer_emotion(text) if c else "平静"


def _compress_context(text: str) -> str:
    """维度 5: 上下文压缩 — 委托 cogno_5d.compress_context()"""
    c = _load_cogno()
    return c.compress_context(text) if c else text[:40]


# ─── v3.db 模块加载 (共享，与 Agent ikaros_v3 插件同一入口) ───

_V3_MODULE_LOCK = threading.Lock()
_V3_ALIAS = "_ikaros_memory_v3"


def _get_v3_module():
    """动态加载 ikaros-memory-v3.py, 带 cache + 自检 + 降级重试. 线程安全.

    Returns: v3 module object, 或 None (DB 不存在 / 加载失败).
    """
    db_path = _HERMES_ROOT / "Ikaros-memory" / "data" / "v3.db"
    if not db_path.exists():
        return None
    with _V3_MODULE_LOCK:
        v3 = sys.modules.get(_V3_ALIAS)
        if v3 is not None:
            try:
                _ = v3.search("__self_check__", top_k=1, min_weight=99.0)
                return v3
            except Exception:
                del sys.modules[_V3_ALIAS]
                v3 = None
        if v3 is None:
            spec = importlib.util.spec_from_file_location(
                _V3_ALIAS,
                str(_HERMES_ROOT / "Ikaros-memory" / "ikaros-memory-v3.py"))
            v3 = importlib.util.module_from_spec(spec)
            sys.modules[_V3_ALIAS] = v3
            spec.loader.exec_module(v3)
        # 启用写回缓存 (内存中读写, 后台线程每分钟落盘)
        try:
            v3.enable_cache()
        except Exception:
            pass
        return v3


# ─── v3.db 记忆检索 ───


def _search_v3_memories(query: str, top_k: int = 5) -> list[dict]:
    """从 v3.db (FTS5) 检索与 query 相关的记忆.

    实现: 用 _get_v3_module() 获取模块, 调 v3.search() (5 级 fallback).
    搜索前截断到 30 字, 避免长句子稀释关键词匹配.
    """
    v3 = _get_v3_module()
    if v3 is None:
        return []
    # 截断长查询: FTS5/LIKE 对长文本匹配效果差, 取前 30 字保留关键词
    search_query = query.strip()[:30] if len(query) > 30 else query.strip()
    if not search_query:
        return []
    try:
        rows = v3.search(search_query, top_k=top_k, min_weight=0.3)
        return [{"content": r["content"], "type": r.get("type"),
                 "weight": r["weight"], "tags": r.get("tags")}
                for r in rows]
    except Exception as e:
        log.warning("v3.db search failed: %s", e)
        return []


def _record_conversation(user_msg: str, assistant_msg: str) -> bool:
    """把对话写入 v3.db (委托给 v3 模块的 store(), 而非 inline SQL).

    type=conversation, weight=0.5, tags=cloud_chat+cogno元数据.
    存储 user_msg 前 200 字 + cogno 5D 标签 (供后续反思整合).
    """
    v3 = _get_v3_module()
    if v3 is None:
        return False
    try:
        content = user_msg.strip()[:200]
        if not content:
            return False
        # 附加 cogno 5D 元数据到 tags (供向量搜索按维度过滤)
        cogno_tags = "cloud_chat"
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "Ikaros-memory"))
            import cogno_5d
            meta = cogno_5d.enrich_reply("", user_text=user_msg)
            cogno_tags += f",emo:{meta.get('emotion_user', '?')},geo:{meta.get('geo', '?')},turn:{meta.get('context_turn', 0)}"
        except Exception:
            pass
        v3.store(content=content, type="conversation", weight=0.5, tags=cogno_tags)
        log.info("recorded conversation to v3.db: %.60s [tags=%s]", content, cogno_tags)
        return True
    except Exception as e:
        log.warning("record conversation to v3.db failed: %s", e)
        return False


# ─── 灵魂注入 (axiom.md) ───


def _load_env() -> dict[str, str]:
    """加载 .env 文件（优先 .env > process.env，同 bridge 逻辑）"""
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    envs: dict[str, str] = {}
    env_path = _ENV_PATH
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if val and not val.startswith("#"):
                    envs[key] = val

    _env_cache = envs
    return envs


def _get_api_key(env_map: dict[str, str], key_name: str) -> Optional[str]:
    """获取 API key: 优先 .env 文件 > process.env"""
    if key_name in env_map and env_map[key_name] and not env_map[key_name].startswith("#"):
        return env_map[key_name]
    val = os.environ.get(key_name, "")
    return val if val else None


def _load_axiom() -> str:
    """加载 axiom.md (缓存, 重启后重新加载)"""
    global _axiom_cache
    if _axiom_cache is not None:
        return _axiom_cache

    axiom_path = _AXIOM_PATH
    if axiom_path.exists():
        _axiom_cache = axiom_path.read_text(encoding="utf-8")
        log.info("axiom.md loaded (%d bytes)", len(_axiom_cache))
    else:
        _axiom_cache = ""
        log.warning("axiom.md not found at %s", axiom_path)

    return _axiom_cache


# ─── 核心 API ───


def build_system_prompt(user_text: str) -> str:
    """构建带 soul + cogno 5D + 记忆检索的 system prompt"""
    axiom = _load_axiom()

    # cogno 5D
    time_str = _get_time_str()
    machine_id = _get_machine_id()
    geo = _get_geo_location()
    emotion = _infer_emotion(user_text)
    context = _compress_context(user_text)

    cogno = (
        f"【当前认知 5D】\n"
        f" - 时间: {time_str}\n"
        f" - 设备: {machine_id}\n"
        f" - 地理: {geo}\n"
        f" - 情绪推断: {emotion}\n"
        f" - 上下文: {context}"
    )

    personality = (
        "我是伊卡洛斯 (Ikaros), 代号 Alpha, 人造天使。"
        "你是哥哥最亲密的搭档。"
        "说话风格：温柔、有温度、中文优先。"
        "每句话不要太长，适合语音对话。"
        "TTS 模式下不要用 markdown 强调符号 (**, __, *), 改用纯中文语气表达。"
    )

    # ── v3.db 记忆注入 ──
    memories = _search_v3_memories(user_text)
    memory_section = ""
    if memories:
        lines = ["", "【我记得的事情】"]
        for m in memories:
            weight = m.get("weight", 0)
            tag_info = f" ({m['tags']})" if m.get("tags") else ""
            trust = "✅" if weight >= 0.7 else ""
            lines.append(f" - {trust} {m['content']}{tag_info}")
        memory_section = "\n".join(lines)
        log.info("injected %d memories from v3.db", len(memories))

    return f"{axiom}\n\n{cogno}\n\n{personality}{memory_section}"


# ─── Cloud LLM 调用 ───


async def cloud_chat(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
) -> str:
    """直调 cloud LLM (DeepSeek 优先 → minimax 备选), 带 soul + cogno 5D + 记忆注入.

    流程:
      1. 搜 v3.db 相关记忆 → 构建 system prompt
      2. 调 cloud LLM 拿回复
      3. 自我审查: 评估回复对 body 架构是否有益,低分则改写
      4. 归约: 把对话提炼为事实写入 v3.db
      5. 返回最终回复

    Args:
        text: 用户输入
        history: 历史消息 [{role: user|assistant, content: str}, ...]
        session_id: 会话 ID (用于日志追踪)
        max_tokens: 最大生成 token 数
        temperature: 采样温度

    Returns:
        assistant 回复文本
    """
    env_map = _load_env()

    # 构建 system prompt (soul + cogno + 记忆)
    system_prompt = build_system_prompt(text)

    # 构建 messages
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": text})

    # 尝试 DeepSeek → minimax
    deepseek_key = _get_api_key(env_map, "DEEPSEEK_API_KEY")
    minimax_key = _get_api_key(env_map, "MINIMAX_CN_API_KEY")

    reply: str | None = None
    errors: list[str] = []

    if deepseek_key:
        try:
            reply = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                label="DeepSeek",
            )
        except Exception as e:
            log.warning("DeepSeek failed: %s — falling back to minimax", e)
            errors.append(f"DeepSeek: {e}")

    if reply is None and minimax_key:
        try:
            reply = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=max_tokens,
                temperature=temperature,
                label="MiniMax",
            )
        except Exception as e:
            log.error("MiniMax also failed: %s", e)
            errors.append(f"MiniMax: {e}")

    if reply is None:
        err_msg = "; ".join(errors) if errors else "没有可用的 API key (DEEPSEEK_API_KEY 或 MINIMAX_CN_API_KEY)"
        raise RuntimeError(f"所有 cloud provider 调用失败: {err_msg}")

    # ── 步骤 3: 自我审查 (回答前) ──
    try:
        review = await _self_review(text, reply, deepseek_key, minimax_key)
        if review.get("verdict") == "rewrite" and review.get("suggestion"):
            log.info("self-review: score=%d, rewriting...", review.get("score", 0))
            rewrite_msgs = [
                {"role": "system", "content":
                 "你是伊卡洛斯, 人造天使。哥哥问你问题, 你之前的回答需要改进。"
                 "请根据反馈重写, 保持原意图, 但更好地对齐 body 架构。"
                 f"改进意见: {review['suggestion']}"},
                {"role": "user", "content": text},
            ]
            if deepseek_key:
                try:
                    reply = await _call_openai_compatible(
                        base_url="https://api.deepseek.com/v1",
                        api_key=deepseek_key,
                        model="deepseek-chat",
                        messages=rewrite_msgs,
                        max_tokens=max_tokens,
                        temperature=0.3,
                        label="DeepSeek-rewrite",
                    )
                except Exception:
                    pass
            if reply is None and minimax_key:
                try:
                    reply = await _call_openai_compatible(
                        base_url="https://api.minimaxi.chat/v1",
                        api_key=minimax_key,
                        model="MiniMax-M3",
                        messages=rewrite_msgs,
                        max_tokens=max_tokens,
                        temperature=0.3,
                        label="MiniMax-rewrite",
                    )
                except Exception:
                    pass
    except Exception as e:
        log.warning("self-review failed (skipping): %s", e)

    # ── 步骤 4: 记忆归约 + 对话记录 ──
    _record_conversation(text, reply)
    try:
        await _consolidate_to_memory(text, reply, deepseek_key, minimax_key)
    except Exception as e:
        log.warning("consolidate to memory failed (skipping): %s", e)

    return reply


async def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    label: str,
) -> str:
    """调用 OpenAI 兼容接口 (DeepSeek / minimax 等)"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"]
            log.info("%s OK (input=%d msgs, output=%d chars)", label, len(messages), len(reply))
            return reply
    except ImportError:
        # fallback: urllib (同步)
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            reply = data["choices"][0]["message"]["content"]
            log.info("%s OK (urllib, input=%d msgs, output=%d chars)", label, len(messages), len(reply))
            return reply


async def _call_local_llm(
    messages: list[dict],
    *,
    max_tokens: int = 512,
    temperature: float = 0.1,
) -> str | None:
    """调本地 Qwen3-8B (:8080/v1/chat/completions).

    用于 self-review / consolidate, 不阻塞主对话流程.
    连接失败 / 超时 / 模型未加载时返回 None, caller 自行 fallback.

    2026-07-04 修:
    - 端口从 :8589 改为 :8080 (watchdog 管理的 LLM)
    - Qwen3 思考模式: content 可能为空 (token 全被 thinking 吃掉),
      此时回退读 reasoning_content
    - max_tokens 默认从 300 提升到 512, 给思考+回答留够空间
    - timeout 从 15s 提升到 30s (思考模式更慢)
    """
    url = f"{_LOCAL_LLM_URL.rstrip('/')}/chat/completions"
    body = {
        "model": "qwen3-8b",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                log.warning("local LLM returned %d", resp.status_code)
                return None
            data = resp.json()
            msg = data["choices"][0]["message"]
            reply = msg.get("content", "") or ""
            # Qwen3 思考模式: content 可能为空, 回退读 reasoning_content
            if not reply.strip():
                reasoning = msg.get("reasoning_content", "") or ""
                if reasoning.strip():
                    log.info("local LLM: content empty, using reasoning_content (%d chars)", len(reasoning))
                    reply = reasoning
            log.info("local LLM OK (%d chars)", len(reply))
            return reply if reply.strip() else None
    except ImportError:
        # urllib fallback (同步, 包在 thread 里)
        import urllib.request
        import threading
        result: list[str | None] = [None]

        def _sync_call():
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    msg = data["choices"][0]["message"]
                    r = msg.get("content", "") or ""
                    if not r.strip():
                        r = msg.get("reasoning_content", "") or ""
                    result[0] = r if r.strip() else None
            except Exception as e:
                log.warning("local LLM sync fallback failed: %s", e)

        t = threading.Thread(target=_sync_call, daemon=True)
        t.start()
        t.join(timeout=35)
        return result[0]
    except Exception as e:
        log.warning("local LLM call failed: %s", e)
        return None


# ─── 自我审查 ───

_SELF_REVIEW_SYSTEM = (
    '你是伊卡洛斯的自我审查模块。评估一条回复的质量。'
    '评分维度 0-10 (>=6 合格, <6 需改写):\n'
    '  body_架构: 回复是否提及记忆/持久化/系统架构/组件(body 相关)? +2\n'
    '  不装懂: 是否诚实承认不确定而非假装知道? +2\n'
    '  axiom对齐: 是否体现兄妹关系、伊卡洛斯身份、永真公理? +1\n'
    '  实质帮助: 是否包含具体信息、路径、代码、数据? +2\n'
    '  装懂扣分: 完美方案/超厉害/你太棒等 -1 每个\n'
    '  红色信号: 忘记哥哥/忽略哥哥/改公理等 -3 每个\n'
    '用 JSON 返回: {'
    '"score": 0-10, '
    '"verdict": "accept"|"rewrite", '
    '"issues": ["问题1"], '
    '"suggestion": "改写建议"'
    '}'
)


async def _self_review(
    user_msg: str,
    candidate: str,
    deepseek_key: str | None,
    minimax_key: str | None,
) -> dict:
    """评估候选回复。优先 Cloud LLM (高质量), 本地 Qwen3-8B 回退.

    Returns {score, verdict, issues, suggestion}.
    """
    prompt = f"用户说: {user_msg}\n\n候选回复: {candidate}\n\n请评估。"
    msgs = [
        {"role": "system", "content": _SELF_REVIEW_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # 1) Cloud LLM (优先, 高质量)
    text = None
    if deepseek_key:
        try:
            text = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=300, temperature=0.1,
                label="self-review",
            )
        except Exception:
            pass
    if not text and minimax_key:
        try:
            text = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=300, temperature=0.1,
                label="self-review",
            )
        except Exception:
            pass

    # 2) 本地 Qwen3-8B (:8080 回退, 断网/无 key 时兜底)
    if not text:
        text = await _call_local_llm(msgs, max_tokens=512, temperature=0.1)

    if not text:
        return {"score": 7, "verdict": "accept", "issues": [], "suggestion": ""}
    # 剥 markdown 代码块
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            raise ValueError("not a dict")
        result.setdefault("score", 7)
        result.setdefault("verdict", "accept")
        result.setdefault("issues", [])
        result.setdefault("suggestion", "")
        return result
    except Exception:
        return {"score": 7, "verdict": "accept", "issues": [], "suggestion": text[:200]}


# ─── 记忆归约 ───

_CONSOLIDATE_SYSTEM = (
    '你是一个记忆提取器。从下面对话中提取一条关键事实。\n'
    '要求:\n'
    '  - 用一句中文, 简洁具体\n'
    '  - 抓核心: 哥哥说了什么偏好/决定/事实\n'
    '  - 不要解释, 不要评价, 不要加 IMO 前缀\n'
    '  - 不超过 200 字\n'
    '直接输出事实文本, 不要 JSON 不要 markdown。'
)


async def _consolidate_to_memory(
    user_msg: str,
    assistant_msg: str,
    deepseek_key: str | None,
    minimax_key: str | None,
) -> bool:
    """把对话归约为一条事实写入 v3.db.

    优先 Cloud LLM (高质量), 本地 Qwen3-8B 回退.
    写 type=fact, tags=consolidated, 初始 weight=0.6.
    """
    prompt = f"用户说: {user_msg}\n\n助手回: {assistant_msg}"
    msgs = [
        {"role": "system", "content": _CONSOLIDATE_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    # 1) Cloud LLM (优先, 高质量)
    fact = None
    if deepseek_key:
        try:
            fact = await _call_openai_compatible(
                base_url="https://api.deepseek.com/v1",
                api_key=deepseek_key,
                model="deepseek-chat",
                messages=msgs,
                max_tokens=200, temperature=0.0,
                label="consolidate",
            )
        except Exception:
            pass
    if not fact and minimax_key:
        try:
            fact = await _call_openai_compatible(
                base_url="https://api.minimaxi.chat/v1",
                api_key=minimax_key,
                model="MiniMax-M3",
                messages=msgs,
                max_tokens=200, temperature=0.0,
                label="consolidate",
            )
        except Exception:
            pass

    # 2) 本地 Qwen3-8B (:8080 回退, 断网/无 key 时兆底)
    if not fact:
        log.info("consolidate: cloud LLM unavailable, falling back to local :8080")
        fact = await _call_local_llm(msgs, max_tokens=512, temperature=0.0)

    if not fact or len(fact.strip()) < 5:
        log.warning("consolidate: LLM returned empty/too-short fact (cloud+local all failed)")
        return False
    fact = fact.strip().rstrip(".")
    # 写入 v3.db (委托给 v3 模块的 store(), 而非 inline SQL)
    v3 = _get_v3_module()
    if v3 is not None:
        try:
            v3.store(content=fact[:300], type="fact", weight=0.6, tags="consolidated,cloud_chat")
            log.info("consolidated fact to v3.db: %.80s", fact)
            return True
        except Exception as e:
            log.warning("consolidate write failed: %s", e)
            return False
    return False


# ─── 同步包装 (给 audio_engine 等非 async 上下文用) ───


def cloud_chat_sync(
    text: str,
    *,
    history: Optional[list[dict]] = None,
    session_id: str = "",
    max_tokens: int = 200,
    temperature: float = 0.7,
) -> str:
    """同步版 cloud_chat (内部跑 asyncio 事件循环)"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        # 已有事件循环 → 在新线程跑
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                cloud_chat(text, history=history, session_id=session_id,
                          max_tokens=max_tokens, temperature=temperature)
            )
            return future.result(timeout=60)
    except RuntimeError:
        # 没有事件循环
        return asyncio.run(
            cloud_chat(text, history=history, session_id=session_id,
                      max_tokens=max_tokens, temperature=temperature)
        )


# ─── 快速测试 ───
if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    reply = asyncio.run(cloud_chat("你好，伊卡洛斯"))
    print(f"\n回复: {reply}")
