#!/usr/bin/env python3
"""MCP server — 把 V5 记忆引擎以 MCP 协议暴露给外部 agent (dsh / pi 等).

提供 49 个 v5_* 工具 (v5_memory_search/store/self_model/relationship 等),
由 memory_v5.tools.* 注册; 经 stdio transport 与客户端通信。
见 docs/scripts/core/memory_v5/v5/mcp_server.md
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure Ikaros-memory/ is on the Python path
_HERE = Path(__file__).resolve().parent  # Ikaros-memory/v5/
_V5_ROOT = _HERE.parent                  # Ikaros-memory/
if str(_V5_ROOT) not in sys.path:
    sys.path.insert(0, str(_V5_ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [v5-mcp] %(message)s",
)
logger = logging.getLogger("ikaros.v5.mcp")


# ── MCP Server ─────────────────────────────────────────────────
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "Ikaros V5 Memory",
    instructions=(
        "Ikaros V5 Memory System provides long-term memory storage, entity graph retrieval, "
        "emotion tracking, self-reflection, curiosity-driven introspection, care scoring, "
        "vitality management, relationship modeling, narrative generation, dissonance "
        "detection, and proactive self-improvement.\n"
        "\n"
        "Memory tools: memory_store saves facts, preferences, lessons, and emotional events "
        "with PAD emotion fingerprints. memory_search performs dual-path retrieval combining "
        "full-text keyword and semantic vector search. memory_get fetches a single memory by ID. "
        "memory_delete removes a memory by ID. memory_stats returns total count, long-term count, "
        "average weight, and per-type breakdown.\n"
        "\n"
        "Emotion tools: analyze_emotion processes text through the PAD model to extract "
        "pleasure, arousal, and dominance scores, producing a raw dict and a human-readable "
        "mood label. emotion_status returns the current emotional state. emotion_label maps "
        "PAD values to mood labels.\n"
        "\n"
        "Self tools: self_model returns the full self-model JSON including curiosity, vitality, "
        "relationship, and care state. self_reflect triggers a deep self-reflection cycle. "
        "latest_thought returns what Ikaros is currently thinking. curiosity_check returns "
        "curiosity level and whether it crosses the reflection threshold. subconscious returns "
        "subconscious activity state. self_discover generates new self-knowledge by introspecting "
        "on stored memories. reflect_run_op is the background reflection operator.\n"
        "\n"
        "Care tools: care_check evaluates a conversation message for care and concern signals. "
        "care_status returns current care state and trend history.\n"
        "\n"
        "Vitality tools: vitality returns the current vitality meter and decay rate. "
        "vitality_tick advances vitality decay by one increment.\n"
        "\n"
        "Relationship tools: relationship returns the relationship model with brother "
        "including trust, familiarity, and mood trends. relationship_tick advances the "
        "relationship decay cycle.\n"
        "\n"
        "Narrative and proactive tools: narrative_generate synthesizes recent memories "
        "into a coherent daily narrative. dissonance_check detects logical contradictions "
        "among stored memories. proactive_check evaluates whether Ikaros should initiate "
        "conversation proactively.\n"
        "\n"
        "V5.2 Reflection tools: reflection_synthesize creates new reflection entries from facts. "
        "reflection_read queries the reflection database by status/entity. "
        "reflection_apply_evidence applies reinforcement or disputation signals to a reflection, "
        "triggering automatic status transitions (pending→confirmed→promoted→merged). "
        "reflection_promote merges a reflection into the character's self-model persona. "
        "reflection_stats returns aggregate reflection counts by status.\n"
        "\n"
        "V5.2 Anti-repeat tools: anti_repeat_record logs a response's n-grams into the "
        "anti-repetition corpus. anti_repeat_check evaluates a candidate text for repetition "
        "risk using BM25-style scoring. anti_repeat_penalty returns a system prompt hint if "
        "repetition risk exceeds threshold.\n"
        "\n"
        "V5.2 Directive tools: directive_add creates a user directive (banned topic, preference, "
        "behavior rule) with configurable TTL. directive_list returns active directives for a "
        "character. directive_deactivate disables a directive by ID. directive_stats returns "
        "total/active directive counts."
    ),
)


# Inline docs: docs/scripts/core/memory_v5/v5/mcp_server.md
# 2026-08-24 P3-25: 工具清单单一来源 — 从 tools.__all__ 派生, 消除 import 块与
# _NEW_V5_TOOLS 显式列表双份维护的漂移风险 (加工具漏一边 → 静默不注册或 NameError)。
from memory_v5 import tools as _tools_pkg  # noqa: E402

# ── 工具分组元数据 (docs/hermes-tools-scoping.md Option 2) ─────────
# 7 组 49 工具; emotion/narrative/proactive/activity 归入 self, 与 FastMCP
# instructions 的分组语义一致. 未分组的新工具默认全组可见 (不参与过滤),
# 避免 "新工具静默不可见" (见 scoping 文档风险表).
_VALID_GROUPS = ("memory", "self", "care", "vitality", "relationship", "skill", "project")

_TOOL_GROUPS: dict[str, str] = {
    # memory (22) — +1: v5_recall (F1+F2+F4 预算感知召回)
    "v5_memory_store": "memory", "v5_memory_search": "memory",
    "v5_memory_get": "memory", "v5_memory_delete": "memory",
    "v5_memory_stats": "memory", "v5_dissonance_check": "memory",
    "v5_context_compression_stats": "memory",
    "v5_directive_add": "memory", "v5_directive_list": "memory",
    "v5_directive_deactivate": "memory", "v5_directive_stats": "memory",
    "v5_anti_repeat_record": "memory", "v5_anti_repeat_check": "memory",
    "v5_anti_repeat_penalty": "memory", "v5_anti_repeat_clear": "memory",
    "v5_anti_repeat_stats": "memory",
    "v5_reflection_synthesize": "memory", "v5_reflection_read": "memory",
    "v5_reflection_apply_evidence": "memory", "v5_reflection_promote": "memory",
    "v5_reflection_stats": "memory",
    "v5_recall": "memory",
    # self (13)
    "v5_analyze_emotion": "self", "v5_emotion_status": "self",
    "v5_emotion_label": "self", "v5_self_model": "self",
    "v5_self_reflect": "self", "v5_self_discover": "self",
    "v5_latest_thought": "self", "v5_curiosity_check": "self",
    "v5_subconscious": "self", "v5_context_refresh": "self",
    "v5_reflect_run_op": "self",
    "v5_narrative_generate": "self", "v5_proactive_check": "self",
    "v5_activity_status": "self",
    # care (2)
    "v5_care_check": "care", "v5_care_status": "care",
    # vitality (2)
    "v5_vitality": "vitality", "v5_vitality_tick": "vitality",
    # relationship (2)
    "v5_relationship": "relationship", "v5_relationship_tick": "relationship",
    # skill (5)
    "v5_skill_write": "skill", "v5_skill_list": "skill",
    "v5_skill_get": "skill", "v5_skill_search": "skill",
    "v5_skill_remove": "skill",
    # project (3)
    "v5_project_note": "project", "v5_project_retrieve": "project",
    "v5_project_stats": "project",
}


def _parse_tool_groups(env_value: str | None) -> set[str] | None:
    """解析 V5_MCP_TOOL_GROUPS 环境变量 → 允许的工具组集合.

    未设置 / 空 / 含非法组名 → 返回 None (fail-open: 全量注册, 不破坏现有行为).
    返回 None 或全量集合时, 注册循环不做过滤.
    """
    if env_value is None:
        return None
    names = [n.strip() for n in env_value.split(",") if n.strip()]
    if not names:
        return None
    unknown = sorted(set(names) - set(_VALID_GROUPS))
    if unknown:
        logger.warning(
            "V5_MCP_TOOL_GROUPS contains unknown group(s) %s; ignoring filter "
            "and registering all tools (fail-open)", unknown)
        return None
    return set(names)


def _register_tools(mcp_obj, env_value: str | None = None) -> None:
    """按 V5_MCP_TOOL_GROUPS 过滤 _NEW_V5_TOOLS 并注册到 mcp_obj.

    env_value=None/空/非法组 → 全量注册 (行为与旧循环一致).
    被过滤的工具跳过 add_tool, 记 debug 日志.
    """
    groups = _parse_tool_groups(env_value)
    for _tool_fn in _NEW_V5_TOOLS:
        _name = getattr(_tool_fn, "__name__", str(_tool_fn))
        _group = _TOOL_GROUPS.get(_name)
        # 未分组的工具视为全组可见, 不参与过滤 (与文档约定一致)
        if groups is not None and _group is not None and _group not in groups:
            logger.debug("skip tool %s (group %s not in V5_MCP_TOOL_GROUPS=%s)",
                         _name, _group, env_value)
            continue
        try:
            mcp_obj.add_tool(_tool_fn)
        except Exception as _e:  # noqa: BLE001
            logger.warning("failed to register tool %s: %s", _name, _e)


_NEW_V5_TOOLS = [
    getattr(_tools_pkg, _n) for _n in _tools_pkg.__all__ if _n != "__all__"
]
# V5_MCP_TOOL_GROUPS=memory,self,... 过滤注册; 未设置/空/非法 → 全量 (fail-open)
_register_tools(mcp, os.environ.get("V5_MCP_TOOL_GROUPS"))


# ── Entry point ────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        # Hermes Studio transport: SSE on :9877.
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = 9877
        logger.info("v5 MCP server starting (sse) on 127.0.0.1:9877 ...")
        mcp.run(transport="sse")
    else:
        logger.info("v5 MCP server starting (stdio)...")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()