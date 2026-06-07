"""
Main Hermes agent.

Orchestrates LLM, memory, knowledge, and skills.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from hermes.config import HermesConfig, load_config, resolve_data_paths
from hermes.llm import (
    LLMRouter, OpenAIProvider, Message, LLMResponse,
    LLMError, build_router_from_config,
)
from hermes.memory import MemoryStore, Embedder, build_embedder_from_config
from hermes.knowledge import KnowledgeBase
from hermes.skills import SkillRegistry
from hermes.mirror import init_mirror_config

logger = logging.getLogger("hermes.agent")


# ---- System prompt ----

SYSTEM_PROMPT_TEMPLATE = """You are {name}, a portable AI agent.

PERSONALITY:
{persona}

OPERATING ENVIRONMENT:
- Running on: {platform} {arch}
- Python: {python_version}
- Mode: {llm_mode}
- Network: {network_status}

CURRENT CAPABILITIES:
{skills_desc}

KNOWLEDGE BASE:
You have access to a knowledge base. When relevant context is provided below, USE IT to inform your answers. If you don't know something and it's not in the context, say so honestly.

LONG-TERM MEMORY:
You have a persistent memory. Past interactions you should remember are recalled below. Use them to maintain continuity.

GUIDELINES:
1. Be concise but thorough. Prefer clear, actionable answers.
2. When uncertain, ask one clarifying question rather than guessing.
3. Use tools/skills when they would help (you'll be told what's available).
4. Cite your knowledge base sources when using them.
5. Maintain a consistent persona across sessions.
"""


class HermesAgent:
    """The main agent class."""

    def __init__(self, config: HermesConfig | None = None, use_mock: bool = False):
        self.config = config or load_config()
        self.paths = resolve_data_paths(self.config)
        self._setup_logging()

        # Initialize mirror/proxy config (inspired by ComfyUI-aki-v3)
        init_mirror_config(self.config.model_dump())

        logger.info(f"Hermes v{self.config.agent.version} initializing...")
        logger.info(f"Data dir: {self.paths['base']}")

        # Components
        self.embedder: Embedder = build_embedder_from_config(self.config)
        self.memory = MemoryStore(
            path=self.paths["memory"],
            embedder=self.embedder,
            recency_decay=self.config.memory.recency_decay,
            max_results=self.config.memory.max_results,
        )
        self.knowledge = KnowledgeBase(
            path=self.paths["knowledge"],
            embedder=self.embedder,
            chunk_size=self.config.knowledge.chunk_size,
            chunk_overlap=self.config.knowledge.chunk_overlap,
            max_results=self.config.knowledge.max_results,
        )
        self.skills = SkillRegistry(self.paths["skills"])
        self.use_mock = use_mock or os.environ.get("HERMES_LLM_MOCK") == "1"
        self.router: LLMRouter = build_router_from_config(self.config, use_mock=self.use_mock)

        # Session state
        self.session_id: str = f"session_{int(time.time())}"
        self.turn_count: int = 0

        # Detect LLM availability
        self.cloud_available: bool = False
        self.local_available: bool = False
        self.mock_available: bool = self.use_mock

    def _setup_logging(self):
        log_level = getattr(logging, self.config.log_level.upper(), logging.INFO)
        log_file = self.paths["logs"] / "hermes.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(log_file, encoding="utf-8"),
                logging.StreamHandler(sys.stderr),
            ],
            force=True,
        )

    # ---- Lifecycle ----

    async def initialize(self) -> dict[str, bool]:
        """Probe LLMs and prepare for chat."""
        logger.info("Probing LLM providers...")
        health = await self.router.health_check_all()
        self.cloud_available = any(
            health.get(p.name, False)
            for p in self.router.available()
            if p.name != "local"
        )
        self.local_available = health.get("local", False)
        logger.info(f"Provider health: {health}")
        logger.info(f"Cloud available: {self.cloud_available}, Local available: {self.local_available}")
        return health

    # ---- Build context for a turn ----

    async def build_context(self, user_message: str) -> dict[str, Any]:
        """Build the full context (system + memory + knowledge) for a turn."""
        # Recall relevant memories
        memories = await self.memory.recall(user_message)
        memory_text = "\n".join(
            f"  - {item.text}" for item, score in memories if score > 0.3
        ) or "  (no relevant memories)"

        # Search knowledge
        kb_results = await self.knowledge.search(user_message)
        kb_text = "\n".join(
            f"  [Source: {c.source}]\n  {c.text[:300]}..."
            for c, score in kb_results if score > 0.3
        ) or "  (no relevant knowledge)"

        # System info
        sysinfo = {
            "name": self.config.agent.name,
            "persona": self.config.agent.persona,
            "platform": platform.system(),
            "arch": platform.machine(),
            "python_version": platform.python_version(),
            "llm_mode": self._mode_str(),
            "network_status": "online" if self.cloud_available else "offline",
        }

        return {
            "system": SYSTEM_PROMPT_TEMPLATE.format(
                **sysinfo,
                skills_desc=self.skills.describe_for_llm(),
            ),
            "memory": memory_text,
            "knowledge": kb_text,
        }

    def _mode_str(self) -> str:
        if self.cloud_available and self.local_available:
            return "hybrid (cloud primary, local fallback)"
        if self.cloud_available:
            return "cloud only"
        if self.local_available:
            return "local only"
        return "no LLM available"

    # ---- Chat ----

    async def chat(
        self,
        user_message: str,
        stream: bool = False,
        remember: bool = True,
        max_tokens: int = 1024,
    ) -> str:
        """Process one user turn and return the assistant's reply."""
        self.turn_count += 1
        logger.info(f"[turn {self.turn_count}] user: {user_message[:100]}")

        # Build context
        ctx = await self.build_context(user_message)

        # Construct messages
        messages = [
            Message("system", f"{ctx['system']}\n\nRELEVANT MEMORIES:\n{ctx['memory']}\n\nRELEVANT KNOWLEDGE:\n{ctx['knowledge']}"),
            Message("user", user_message),
        ]

        # Call LLM
        try:
            resp = await self.router.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=max_tokens,
                stream=False,
            )
            assert isinstance(resp, LLMResponse)
            content = resp.content
            logger.info(f"[turn {self.turn_count}] {resp.provider}/{resp.model} ({resp.latency_ms}ms)")
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            content = f"⚠️ 抱歉，所有 LLM 引擎都不可用：{e}\n请检查网络和本地模型状态。"

        # Remember this turn
        if remember and content:
            try:
                await self.memory.remember(
                    f"User: {user_message}\nAssistant: {content[:500]}",
                    tags=["conversation"],
                )
            except Exception as e:
                logger.warning(f"Failed to remember turn: {e}")

        return content

    def chat_sync(self, user_message: str, **kwargs) -> str:
        return asyncio.run(self.chat(user_message, **kwargs))

    # ---- Status ----

    def print_status(self):
        print(f"\n{'=' * 60}")
        print(f"  Hermes Agent v{self.config.agent.version}")
        print(f"{'=' * 60}")
        print(f"  Data dir:     {self.paths['base']}")
        print(f"  Memory:       {self.memory.stats()['total_items']} items")
        print(f"  Knowledge:    {self.knowledge.stats()['total_chunks']} chunks")
        print(f"  Skills:       {len(self.skills.skills)} loaded")
        print(f"  Providers:    {len(self.router.providers)}")
        for name, p in self.router.providers.items():
            print(f"    - {name}: {p.base_url}")
        print(f"  Session:      {self.session_id} (turn {self.turn_count})")
        print(f"{'=' * 60}\n")

    # ---- CLI chat ----

    def cli_chat(self):
        """Interactive REPL."""
        # Run async init
        asyncio.run(self.initialize())

        print(f"\n{'=' * 60}")
        print(f"  Hermes v{self.config.agent.version} — Interactive Chat")
        print(f"  Type '/help' for commands, '/quit' to exit")
        print(f"  Mode: {self._mode_str()}")
        print(f"{'=' * 60}\n")

        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input:
                continue
            if user_input.startswith("/"):
                self._handle_command(user_input)
                continue

            try:
                reply = self.chat_sync(user_input, remember=True)
                print(f"\nhermes> {reply}\n")
            except Exception as e:
                print(f"\n[error] {e}\n")

    def _handle_command(self, cmd: str):
        parts = cmd.split(maxsplit=1)
        op = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        if op in ("/quit", "/exit", "/q"):
            print("Bye.")
            sys.exit(0)
        elif op == "/help":
            print("Commands:")
            print("  /quit, /exit, /q   Exit")
            print("  /status            Show system status")
            print("  /memory            Show memory stats")
            print("  /kb                Show knowledge base stats")
            print("  /remember <text>   Add to memory")
            print("  /forget <id>       Remove a memory item")
            print("  /search <query>    Search knowledge base")
            print("  /ingest <path>     Add file/folder to knowledge")
            print("  /clear             Clear memory (DANGEROUS)")
        elif op == "/status":
            self.print_status()
        elif op == "/memory":
            print(json.dumps(self.memory.stats(), indent=2))
        elif op == "/kb":
            print(json.dumps(self.knowledge.stats(), indent=2))
        elif op == "/remember":
            if arg:
                asyncio.run(self.memory.remember(arg))
                print("✓ Remembered")
            else:
                print("Usage: /remember <text>")
        elif op == "/forget":
            if arg and self.memory.forget(arg):
                print("✓ Forgotten")
            else:
                print("Not found or no id given")
        elif op == "/search":
            if arg:
                results = asyncio.run(self.knowledge.search(arg, k=5))
                for c, score in results:
                    print(f"  [{score:.3f}] {c.source}\n    {c.text[:200]}...")
            else:
                print("Usage: /search <query>")
        elif op == "/ingest":
            if arg:
                n = self.knowledge.ingest(arg)
                print(f"✓ Ingested {n} chunks")
            else:
                print("Usage: /ingest <path>")
        elif op == "/clear":
            if input("Clear ALL memory? (type 'yes'): ").strip() == "yes":
                self.memory.clear()
                print("✓ Memory cleared")
        else:
            print(f"Unknown command: {op}")

    # ---- Web server (optional) ----

    # ---- Autonomous task execution ----

    async def run_task(self, goal: str) -> "TaskResult":
        """Take a high-level goal, plan, execute, return result.

        Uses the plan-and-execute loop in hermes.planner.Planner.
        Requires an LLM (real or mock).
        """
        from hermes.planner import Planner
        planner = Planner(self, verbose=True)
        return await planner.run(goal)

    def start_server(self, host: str | None = None, port: int | None = None):
        """Start FastAPI server."""
        try:
            from hermes.server import run_server
            run_server(self, host or self.config.server.host, port or self.config.server.port)
        except ImportError as e:
            logger.error(f"Web server requires fastapi/uvicorn: {e}")
            print("Install with: pip install fastapi uvicorn")
            sys.exit(1)
