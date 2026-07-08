"""
v4.reflect.llm_client — V4 LLM client (DeepSeek V4 flash + 本地 Qwen3-8B)

设计目标:
  - 双轨: 本地小模型 (Qwen3-8B :8080) + cloud 大模型 (DeepSeek V4 flash)
  - 统一接口: 一处定义, 两处实现, 调用方不感知
  - 密钥零接触: API key 只从 os.environ / .env 读, 不写进代码, 不进 git
  - 显式错误: 失败时抛, 不静默

V3 vs V4:
  - V3 memory_reflect.py 只用本地 Qwen3-8B (line 58-62)
  - V4 新增大模型反思 (哥哥 id 158 长线目标), 用 DeepSeek V4 flash
  - 小模型仍然在 (consolidate 提取用, 因为便宜/快)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

# ─── 启动时自动从 .env 读 DEEPSEEK_API_KEY ─────────────────────
# 哥哥 (2026-07-05) K1b 决策: 用 python-dotenv 自动加载
# Hermes Agent .env 在 E:\Ikaros\data\hermes-agent\.env (HERMES_HOME env)
# 优先 HERMES_HOME 路径, 然后 Ikaros 默认, 最后 V4 自己的 .env (允许覆盖)

logger = logging.getLogger("ikaros.memory.v4.llm")

# ─── 路径配置 (与 v4 其他模块一致) ──────────────────────────────

V4_ROOT = Path(__file__).resolve().parent.parent
V4_DATA_DIR = V4_ROOT / "data" / "v4"

# 在路径定义完之后做 dotenv 加载 (按 HERMES_HOME → Ikaros 默认 → V4 顺序)
try:
    from dotenv import load_dotenv
    _env_candidates = [
        Path(os.environ.get("HERMES_HOME", r"E:\Ikaros\data\hermes-agent")) / ".env",
        Path(r"E:\Ikaros\data\hermes-agent") / ".env",
    ]
    for _env in _env_candidates:
        if _env.exists():
            load_dotenv(_env, override=False)
    # V4 自己的 .env 允许覆盖 (override=True, 优先级最高)
    _v4_env = V4_ROOT / ".env"
    if _v4_env.exists():
        load_dotenv(_v4_env, override=True)
except ImportError:
    pass  # dotenv 不可用, 走 os.environ 裸读

# ─── 小模型 (本地 Qwen3-8B) ─────────────────────────────────────

# V3 memory_reflect.py:58-61 风格一致: env var + hardcoded fallback
LOCAL_LLM_URL = os.environ.get(
    "HERMES_LOCAL_LLM_URL",
    "http://127.0.0.1:8080/v1",
).rstrip("/") + "/chat/completions"
LOCAL_LLM_MODEL = os.environ.get("HERMES_LOCAL_LLM_MODEL", "qwen3-8b")
LOCAL_LLM_TIMEOUT = int(os.environ.get("HERMES_LOCAL_LLM_TIMEOUT", "60"))

# ─── 大模型 (DeepSeek V4 flash) ────────────────────────────────

# 哥哥 (2026-07-05) 选定 V4 flash, 验证于 Context7 /websites/api-docs_deepseek
# 端点: https://api.deepseek.com (OpenAI-compatible, 跟 V3 LOCAL_LLM_URL 同形)
DEEPSEEK_BASE_URL = os.environ.get(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
)
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_TIMEOUT = int(os.environ.get("DEEPSEEK_TIMEOUT", "120"))
# 哥哥 key 已设到 Hermes Agent .env (DEEPSEEK_API_KEY=sk-...)
# V4 不直接读 .env, 只从 os.environ 拿 (避免代码进 git)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# ─── 统一接口 ──────────────────────────────────────────────────

ProviderName = Literal["local", "deepseek"]


@dataclass(frozen=True)
class LLMResponse:
    """统一 LLM 响应 (小模型/大模型都返这个)."""
    content: str
    provider: ProviderName
    model: str
    elapsed_sec: float
    raw: dict | None = None  # 原始响应 (调试用)


# ─── 重试 (防御 LLM 偶发 404 / 5xx / 网络抖动) ───────────────
# 哥哥 (2026-07-07) 修 "distill 偶发 404": 大模型/本地偶发 404 不该废掉整轮反思
MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 1.5


def call_llm(
    system: str,
    user: str,
    *,
    provider: ProviderName = "local",
    max_tokens: int = 1024,
    temperature: float = 0.0,
    timeout: int | None = None,
) -> LLMResponse:
    """统一 LLM 调用入口 (带重试 + 退避, 缓解偶发 404/5xx/抖动).

    Args:
        system: system prompt
        user: user prompt
        provider: "local" (Qwen3-8B) 或 "deepseek" (V4 flash)
        max_tokens: 最大输出 token
        temperature: 0 = 确定性, 1 = 创造性
        timeout: 超时秒数, None 用 provider 默认

    Raises:
        RuntimeError: 重试耗尽后仍失败 / API key 缺失 / 解析失败 (显式, 不静默)
    """
    t0 = time.time()
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if provider == "local":
                resp = _call_local(system, user, max_tokens, temperature, timeout or LOCAL_LLM_TIMEOUT)
            elif provider == "deepseek":
                resp = _call_deepseek(system, user, max_tokens, temperature, timeout or DEEPSEEK_TIMEOUT)
            else:
                raise ValueError(f"unknown provider: {provider!r}")
            elapsed = time.time() - t0
            logger.info("LLM call: provider=%s model=%s attempt=%d/%d elapsed=%.2fs len=%d",
                        provider, resp.model, attempt, MAX_RETRIES, elapsed, len(resp.content))
            return resp
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                logger.warning("LLM call attempt %d/%d failed (%s); retry in %.1fs",
                               attempt, MAX_RETRIES, e, RETRY_BACKOFF_SEC)
                time.sleep(RETRY_BACKOFF_SEC)
            else:
                logger.error("LLM call failed after %d attempts: %s", MAX_RETRIES, e)
    elapsed = time.time() - t0
    raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts ({elapsed:.1f}s): {last_err}") from last_err


# ─── 本地 (Qwen3-8B) ──────────────────────────────────────────

def _call_local(system: str, user: str, max_tokens: int,
                temperature: float, timeout: int) -> LLMResponse:
    """调本地 llama-server (OpenAI-compatible)."""
    body = {
        "model": LOCAL_LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    try:
        with httpx.Client(transport=httpx.HTTPTransport(retries=0), timeout=timeout) as client:
            r = client.post(LOCAL_LLM_URL, json=body)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        raise RuntimeError(f"local LLM call failed: {e}") from e

    msg = data["choices"][0]["message"]
    content = msg.get("content", "") or ""
    # Qwen3 思考模式: content 空时回退 reasoning_content
    if not content.strip():
        content = msg.get("reasoning_content", "") or ""
    if not content.strip():
        raise RuntimeError("local LLM returned empty content + reasoning_content")
    return LLMResponse(
        content=content.strip(),
        provider="local",
        model=LOCAL_LLM_MODEL,
        elapsed_sec=0.0,
        raw=data,
    )


# ─── DeepSeek V4 flash ───────────────────────────────────────

def _call_deepseek(system: str, user: str, max_tokens: int,
                   temperature: float, timeout: int) -> LLMResponse:
    """调 DeepSeek V4 flash API.

    哥哥 (2026-07-05) 验证:
      endpoint = https://api.deepseek.com/v1/chat/completions
      model = deepseek-v4-flash
      auth = Bearer ${DEEPSEEK_API_KEY}
      thinking = {"type": "enabled"} (可选, 默认 off)
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set. Set via:\n"
            "  setx DEEPSEEK_API_KEY \"sk-...\"   (Windows)\n"
            "  export DEEPSEEK_API_KEY=\"sk-...\" (Linux/macOS/git-bash)\n"
            "Or add to E:\\Ikaros\\data\\hermes-agent\\.env"
        )

    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/v1/chat/completions"
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        # V4: 显式错误, 不吞
        body_text = e.response.text[:500] if e.response else ""
        raise RuntimeError(
            f"DeepSeek API HTTP {e.response.status_code}: {body_text}"
        ) from e
    except Exception as e:
        raise RuntimeError(f"DeepSeek API call failed: {e}") from e

    try:
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"DeepSeek response shape unexpected: {data}") from e

    if not content.strip():
        raise RuntimeError("DeepSeek returned empty content")
    return LLMResponse(
        content=content.strip(),
        provider="deepseek",
        model=DEEPSEEK_MODEL,
        elapsed_sec=0.0,
        raw=data,
    )


# ─── helpers ──────────────────────────────────────────────────

def has_api_key() -> bool:
    """检查 DEEPSEEK_API_KEY 是否可用 (不返值, 只 bool)."""
    return bool(DEEPSEEK_API_KEY)


def stats() -> dict:
    """返回当前 LLM client 配置 (不泄露 key)."""
    return {
        "local": {
            "url": LOCAL_LLM_URL,
            "model": LOCAL_LLM_MODEL,
            "timeout": LOCAL_LLM_TIMEOUT,
        },
        "deepseek": {
            "base_url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "timeout": DEEPSEEK_TIMEOUT,
            "api_key_set": has_api_key(),
        },
    }
