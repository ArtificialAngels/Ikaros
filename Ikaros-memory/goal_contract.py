"""goal_contract.py — 把自然语言目标扩写成结构化完成合同。

借自 Hermes Agent 的 `hermes_cli/goals.py` 的 `draft_contract` 函数 + `GoalContract`
dataclass（继承上游 MIT 协议）。只借"零件"，不接 Hermes 的 Ralph loop / judge /
GoalManager —— Ikaros 自己的代理任务调度走 cogno_5d 的 5 维状态机，不上 Ralph loop。

合同五字段（直接复用上游语义）：
- outcome: 完成时的单一终态
- verification: 证明 outcome 已达的具体可验证手段（命令 / 测试 / 工件）
- constraints: 不能动 / 不能回归的内容
- boundaries: 范围内允许的文件 / 目录 / 工具
- stop_when: 应当停下并向哥哥请求输入的条件

入口:
    from goal_contract import draft_contract, GoalContract
    contract = draft_contract("写一个自动整理下载文件夹的脚本")
    # contract.outcome / verification / ... 或 None (调不通时退化)

依赖:
    - httpx (推荐) 或 urllib (回退) — 与 bin/cloud_chat.py 走同一套路
    - 环境变量: DEEPSEEK_API_KEY 或 MINIMAX_CN_API_KEY (与 V4 一致)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("ikaros.goal_contract")

# ──────────────────────────────────────────────────────────────────────
# Constants — 抄自 hermes-agent/hermes_cli/goals.py
# ──────────────────────────────────────────────────────────────────────

_CONTRACT_FIELDS = ("outcome", "verification", "constraints", "boundaries", "stop_when")

_CONTRACT_LABELS = {
    "outcome": "Outcome",
    "verification": "Verification",
    "constraints": "Constraints",
    "boundaries": "Boundaries",
    "stop_when": "Stop when blocked",
}

DRAFT_CONTRACT_SYSTEM_PROMPT = (
    "You turn a user's plain-language objective into a structured completion "
    "contract for an autonomous coding agent. The contract has five fields:\n"
    "- outcome: the single end state that must be true when done\n"
    "- verification: the specific test / command / artifact that PROVES the "
    "outcome (must be concrete and checkable)\n"
    "- constraints: what must NOT change or regress\n"
    "- boundaries: which files, dirs, tools, or systems are in scope\n"
    "- stop_when: the condition under which the agent should stop and ask "
    "for human input instead of pushing on\n\n"
    "Infer sensible, specific values from the objective and any project "
    "context implied by it. Prefer concrete verification (a named test "
    "command, a build, a benchmark) over vague phrases. Keep each field to "
    "one or two sentences. If a field genuinely cannot be inferred, use an "
    "empty string for it.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{"outcome": "...", "verification": "...", "constraints": "...", '
    '"boundaries": "...", "stop_when": "..."}'
)


# ──────────────────────────────────────────────────────────────────────
# GoalContract — 抄自 hermes-agent/hermes_cli/goals.py:293
# ──────────────────────────────────────────────────────────────────────

@dataclass
class GoalContract:
    """Optional structured completion contract for a goal.

    Each field is free-form prose the user (or :func:`draft_contract`)
    supplies. Empty fields are omitted everywhere — a goal with no contract
    behaves exactly like a free-form goal.
    """

    outcome: str = ""
    verification: str = ""
    constraints: str = ""
    boundaries: str = ""
    stop_when: str = ""

    def is_empty(self) -> bool:
        return not any(getattr(self, f).strip() for f in _CONTRACT_FIELDS)

    def to_dict(self) -> Dict[str, str]:
        return {f: getattr(self, f) for f in _CONTRACT_FIELDS}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "GoalContract":
        if not isinstance(data, dict):
            return cls()
        return cls(**{f: str(data.get(f) or "").strip() for f in _CONTRACT_FIELDS})

    def render_block(self) -> str:
        """Render non-empty contract fields as a labelled block. Empty
        contract -> empty string (callers skip the section entirely)."""
        lines = []
        for f in _CONTRACT_FIELDS:
            val = getattr(self, f).strip()
            if val:
                lines.append(f"- {_CONTRACT_LABELS[f]}: {val}")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
# API key resolution — 与 bin/cloud_chat.py 一致
# ──────────────────────────────────────────────────────────────────────

def _load_env_file(env_path: Path) -> Dict[str, str]:
    """最小化的 .env 读取, 不依赖 python-dotenv。"""
    out: Dict[str, str] = {}
    try:
        if not env_path.is_file():
            return out
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                out[k] = v
    except Exception as exc:
        logger.debug("goal_contract: .env read failed: %s", exc)
    return out


def _get_api_key_and_base() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model) — 优先 DeepSeek, fallback 到本地 qwen3-8b。

    DeepSeek 擅长结构化 JSON, 比 qwen3-8b 小模型更适合做 contract draft 这种
    一次性辅助调用。本地 qwen3 走 :8080, fallback 时模型名对齐。
    """
    hermes_root = Path(os.environ.get("HERMES_ROOT", r"E:\Ikaros"))
    env_map = _load_env_file(hermes_root / "data" / "hermes-agent" / ".env")
    env_map.update({k: v for k, v in os.environ.items() if k.startswith(("DEEPSEEK_", "MINIMAX_", "OPENAI_"))})

    deepseek_key = env_map.get("DEEPSEEK_API_KEY", "")
    if deepseek_key:
        base = env_map.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        model = env_map.get("DEEPSEEK_DRAFT_MODEL", "deepseek-chat")
        return deepseek_key, base, model

    minimax_key = env_map.get("MINIMAX_CN_API_KEY", "")
    if minimax_key:
        base = env_map.get("MINIMAX_CN_BASE_URL", "https://api.minimaxi.com/v1")
        model = env_map.get("MINIMAX_CN_MODEL", "MiniMax-M3")
        return minimax_key, base, model

    # fallback: 本地 qwen3-8b (已在 :8080)
    return "", "http://127.0.0.1:8080/v1", env_map.get("HERMES_LOCAL_LLM_MODEL", "qwen3-8b")


# ──────────────────────────────────────────────────────────────────────
# JSON extraction — 抄自 hermes-agent/hermes_cli/goals.py:1045
# ──────────────────────────────────────────────────────────────────────

_JSON_OBJ_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    """Best-effort: 抽第一个 JSON object, 兼容 reasoning 模型在 JSON 前的胡言乱语。"""
    if not raw:
        return None
    raw = raw.strip()
    # 直接尝试整段是 JSON
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 退而求其次: 找第一个 { ... } 子串
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────
# HTTP call — 走 httpx 或 urllib, 与 cloud_chat.py 同款
# ──────────────────────────────────────────────────────────────────────

def _call_llm_sync(messages: list[dict], *, base_url: str, api_key: str, model: str, max_tokens: int, timeout: float) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
    except ImportError:
        pass
    except Exception as exc:
        logger.info("goal_contract: httpx call failed (%s)", exc)
        return ""

    # urllib 回退
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        logger.info("goal_contract: urllib call failed (%s)", exc)
        return ""


# ──────────────────────────────────────────────────────────────────────
# Main API
# ──────────────────────────────────────────────────────────────────────

def draft_contract(objective: str, *, timeout: float = 30.0, max_tokens: int = 1024) -> Optional[GoalContract]:
    """把自然语言目标扩写成结构化合同。

    抄自 `hermes-agent/hermes_cli/goals.py:draft_contract`, 但绕开 Hermes 的
    auxiliary_client (它依赖 agent 包的内部状态), 改走 DeepSeek / MiniMax 直连
    或本地 :8080 qwen3。失败返回 None —— 调用方应退化到裸 free-form goal,
    不要把 draft 失败当成阻塞错误。
    """
    objective = (objective or "").strip()
    if not objective:
        return None

    api_key, base_url, model = _get_api_key_and_base()
    if not api_key and "127.0.0.1" not in base_url:
        logger.info("goal_contract: no API key + no local LLM, skip draft")
        return None

    messages = [
        {"role": "system", "content": DRAFT_CONTRACT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Objective:\n{objective[:4000]}"},
    ]
    raw = _call_llm_sync(messages, base_url=base_url, api_key=api_key, model=model, max_tokens=max_tokens, timeout=timeout)
    if not raw:
        return None

    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        logger.debug("goal_contract: reply was not JSON: %r", raw[:200])
        return None
    contract = GoalContract.from_dict(data)
    return None if contract.is_empty() else contract


# ──────────────────────────────────────────────────────────────────────
# CLI — 哥哥手动试一下用
# ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    obj = " ".join(sys.argv[1:]) or "写一个自动整理下载文件夹的 Python 脚本, 每周日晚上跑一次"
    print(f"[goal_contract] objective: {obj}\n")
    c = draft_contract(obj)
    if c is None:
        print("[goal_contract] draft failed (no API key / parse error / empty result)")
        sys.exit(1)
    print(c.render_block())