"""
Intelligent routing engine for Hermes Agent.

Decides *per-request* whether a user query should be handled by:

1. **Local model + offline tools** (no internet)
2. **Local model + basic tools** (simple conversation, fast & private)
3. **Cloud model + full tools** (complex tasks needing tool orchestration)

The engine is stateless — it reads the current network status and
user intent each time, then returns a :class:`RoutingDecision`.

Usage::

    from hermes.routing import RoutingEngine, RoutingDecision

    engine = RoutingEngine.from_config()
    decision = engine.decide("帮我写一个 Python 爬虫")

    print(decision.model_source)  # "cloud"
    print(decision.toolsets)      # ["hermes-cloud"]
    print(decision.reason)        # "matched tool trigger: 帮我写"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.routing")

# Default keyword lists (used when config is absent).
_DEFAULT_TOOL_TRIGGERS: list[str] = [
    "帮我写", "帮我查", "帮我做", "帮我找",
    "分析", "总结", "翻译",
    "生成代码", "写代码", "写一个", "创建一个",
    "调试", "debug", "修复", "fix",
    "搜索", "查一下", "查一查",
    "浏览网页", "打开网页", "爬取", "抓取",
    "下载", "上传",
    "部署", "deploy",
    "配置", "configure", "config",
    "git", "docker", "npm", "pip",
    "重构", "refactor", "优化", "optimize",
    "测试", "test",
    "画图", "生成图片", "生成图像",
    "看板", "kanban",
]

_DEFAULT_SIMPLE_TRIGGERS: list[str] = [
    "你好", "嗨", "hello", "hi",
    "谢谢", "感谢", "thanks",
    "什么是", "为什么", "怎么样", "如何",
    "介绍一下", "介绍", "解释一下",
    "你是谁", "你叫什么",
    "天气", "时间", "日期",
    "再见", "拜拜", "bye",
    "哈哈", "嗯", "哦", "好的", "行",
]

# ---- Privacy / local-first triggers ----
# These ALWAYS route to the local model regardless of network status.
# They represent operations that involve user-private data or are
# simple local commands the local model handles well.
_PRIVACY_LOCAL_TRIGGERS: list[str] = [
    # File operations involving user data
    "我的文件", "我的文档", "我的项目",
    "桌面", "下载", "文档",
    "密码", "密钥", "token", "api key",
    "私钥", "证书",
    # Personal info
    "我的名字", "我的地址", "我的电话",
    "个人信息", "隐私",
    # Simple local commands
    "打开文件", "读取文件", "读文件",
    "列出文件", "显示目录",
    "当前目录", "现在路径",
    # Git operations (local)
    "git status", "git diff", "git log",
    "git add", "git commit",
    # Shell execution
    "运行命令", "执行", "终端",
    # Memory operations
    "记住", "保存这个", "记录一下",
    "我的笔记", "我的记忆",
]

# ---- Skill / local execution triggers ----
# These indicate the user wants the local model to execute a skill
# or perform a concrete action (not just chat).
_SKILL_LOCAL_TRIGGERS: list[str] = [
    "skill", "技能",
    "执行", "运行",
    "帮我打开", "帮我查看",
    "检查", "查看文件",
    "修改文件", "编辑文件",
    "创建文件", "新建文件",
    "删除文件",
]

# ---- Public types ----

@dataclass
class RoutingDecision:
    """Result of a routing decision for a single user query."""

    model_source: str          # "local" | "cloud"
    model_profile: str         # e.g. "medium", "large" (key in models.yaml)
    toolsets: list[str]        # list of toolset names
    reason: str                # human-readable explanation
    network_online: bool = True

    # How to reach the model
    route_target: str = ""     # "llama_server" | "cloud_api" | "agent_bridge"
    route_model: str = ""      # actual model name to pass to the target

    # Client can optionally override these
    cloud_provider: str = ""   # e.g. "openai", "anthropic"
    fallback_providers: list[str] = field(default_factory=list)


class RoutingEngine:
    """Per-request routing decision maker.

    Instantiate once at startup; call :meth:`decide` for every user
    message. The engine is fully synchronous and does no I/O beyond
    the cached network check in :mod:`hermes.network`.

    Parameters
    ----------
    config:
        The ``routing`` section from ``hermes.yaml``, pre-parsed to a dict.
        If ``None``, sensible defaults are used.
    """

    # ---- Class-level defaults (immutable) ----

    _DEFAULT_OFFLINE_TOOLSETS: tuple = (
        "terminal", "file", "todo", "memory", "skills", "code_execution",
    )
    _DEFAULT_SIMPLE_TOOLSETS: tuple = (
        "terminal", "file", "todo", "memory", "skills", "web",
    )
    _DEFAULT_CLOUD_TOOLSETS: tuple = ("hermes-cloud",)

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}

        # offline
        off = cfg.get("offline", {})
        self._offline_model_source: str = off.get("model_source", "local")
        self._offline_model_profile: str = off.get("model_profile", "medium")
        self._offline_toolsets: list[str] = off.get(
            "toolsets", list(self._DEFAULT_OFFLINE_TOOLSETS)
        )

        # online → simple
        simple = cfg.get("online", {}).get("simple", {})
        self._simple_model_source: str = simple.get("model_source", "local")
        self._simple_model_profile: str = simple.get("model_profile", "medium")
        self._simple_toolsets: list[str] = simple.get(
            "toolsets", list(self._DEFAULT_SIMPLE_TOOLSETS)
        )

        # online → tool_routing
        tr = cfg.get("online", {}).get("tool_routing", {})
        self._tool_model_source: str = tr.get("model_source", "cloud")
        self._tool_cloud_provider: str = tr.get("cloud_provider", "openai")
        self._tool_fallback_providers: list[str] = tr.get(
            "fallback_providers", ["anthropic", "openrouter"]
        )
        self._tool_toolsets: list[str] = tr.get(
            "toolsets", list(self._DEFAULT_CLOUD_TOOLSETS)
        )

        # intent triggers
        intent = cfg.get("intent", {})
        self._tool_triggers: list[str] = intent.get(
            "tool_triggers", _DEFAULT_TOOL_TRIGGERS
        )
        self._simple_triggers: list[str] = intent.get(
            "simple_triggers", _DEFAULT_SIMPLE_TRIGGERS
        )

    @classmethod
    def from_config(cls, config_path: Optional[str | Path] = None) -> RoutingEngine:
        """Create an engine from the project's ``config/hermes.yaml``.

        This is the recommended constructor for production use.
        """
        try:
            from hermes.config import load_config
            cfg = load_config(config_path)
            routing_cfg = cfg.model_dump().get("routing", {})
            return cls(routing_cfg)
        except Exception:
            logger.warning("failed to load routing config, using defaults", exc_info=True)
            return cls()

    # ---- Public API ----

    def decide(
        self,
        user_query: str,
        *,
        force_online: Optional[bool] = None,
    ) -> RoutingDecision:
        """Determine the best model source and toolsets for *user_query*.

        Priority order:
        1. **Privacy triggers** → always local (no data leaves the device)
        2. **Skill/exec triggers** → local (concrete actions, fast response)
        3. **Network offline** → local + lightweight tools
        4. **Tool triggers** → cloud (complex reasoning needed)
        5. **Simple triggers** → local (fast + private)
        6. **Heuristic** → long/code queries → cloud
        7. **Default** → local
        """
        query_stripped = user_query.strip().lower()

        # === TIER 1: Privacy-sensitive → ALWAYS local ===
        for trigger in _PRIVACY_LOCAL_TRIGGERS:
            if trigger.lower() in query_stripped:
                return RoutingDecision(
                    model_source="local",
                    model_profile=self._simple_model_profile,
                    toolsets=list(self._offline_toolsets),
                    reason=f"privacy trigger: {trigger} — keep data local",
                    network_online=True,
                    route_target="llama_server",
                    route_model="",  # use default local model
                )

        # === TIER 2: Skill / execution → local ===
        for trigger in _SKILL_LOCAL_TRIGGERS:
            if trigger.lower() in query_stripped:
                return RoutingDecision(
                    model_source="local",
                    model_profile=self._simple_model_profile,
                    toolsets=list(self._simple_toolsets),
                    reason=f"skill trigger: {trigger} — execute locally",
                    network_online=True,
                    route_target="llama_server",
                    route_model="",
                )

        # === TIER 3: Network check ===
        online = force_online
        if online is None:
            try:
                from hermes.network import is_online
                online = is_online()
            except Exception:
                logger.debug("network check failed, assuming offline")
                online = False

        if not online:
            return RoutingDecision(
                model_source=self._offline_model_source,
                model_profile=self._offline_model_profile,
                toolsets=list(self._offline_toolsets),
                reason="network offline — local model + lightweight tools",
                network_online=False,
                route_target="llama_server",
                route_model="",
            )

        # === TIER 4: Tool triggers → cloud ===
        for trigger in self._tool_triggers:
            if trigger.lower() in query_stripped:
                return RoutingDecision(
                    model_source=self._tool_model_source,
                    model_profile="auto",
                    toolsets=list(self._tool_toolsets),
                    reason=f"matched tool trigger: {trigger}",
                    network_online=True,
                    route_target="cloud_api",
                    route_model="",  # cloud provider picks default
                    cloud_provider=self._tool_cloud_provider,
                    fallback_providers=list(self._tool_fallback_providers),
                )

        # === TIER 5: Simple triggers → local ===
        for trigger in self._simple_triggers:
            if trigger.lower() in query_stripped:
                return RoutingDecision(
                    model_source=self._simple_model_source,
                    model_profile=self._simple_model_profile,
                    toolsets=list(self._simple_toolsets),
                    reason=f"matched simple trigger: {trigger}",
                    network_online=True,
                    route_target="llama_server",
                    route_model="",
                )

        # === TIER 6: Heuristic → cloud for complex queries ===
        if len(user_query.strip()) > 200 or _looks_like_code_task(user_query):
            return RoutingDecision(
                model_source=self._tool_model_source,
                model_profile="auto",
                toolsets=list(self._tool_toolsets),
                reason="heuristic: long/complex query → cloud tools",
                network_online=True,
                route_target="cloud_api",
                route_model="",
                cloud_provider=self._tool_cloud_provider,
                fallback_providers=list(self._tool_fallback_providers),
            )

        # === TIER 7: Default → local ===
        return RoutingDecision(
            model_source=self._simple_model_source,
            model_profile=self._simple_model_profile,
            toolsets=list(self._simple_toolsets),
            reason="default: local model for simple conversation",
            network_online=True,
            route_target="llama_server",
            route_model="",
        )

    # ---- Inspection helpers ----

    @property
    def offline_toolsets(self) -> list[str]:
        return list(self._offline_toolsets)

    @property
    def simple_toolsets(self) -> list[str]:
        return list(self._simple_toolsets)

    @property
    def cloud_toolsets(self) -> list[str]:
        return list(self._tool_toolsets)


# ---- Internal helpers ----

_CODE_PATTERNS: list[re.Pattern] = [
    re.compile(r"```"),          # code fences
    re.compile(r"def\s+\w+\("),  # function definition
    re.compile(r"import\s+\w+"), # import statement
    re.compile(r"class\s+\w+"),  # class definition
    re.compile(r"npm\s+(install|run|build)"),
    re.compile(r"pip\s+install"),
    re.compile(r"git\s+(clone|commit|push|pull)"),
    re.compile(r"docker\s+(run|build|compose)"),
    re.compile(r"curl\s+"),
    re.compile(r"wget\s+"),
]


def _looks_like_code_task(query: str) -> bool:
    """Return True if the query appears code/tool-related."""
    for pat in _CODE_PATTERNS:
        if pat.search(query):
            return True
    return False
