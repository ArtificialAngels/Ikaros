"""
Icarus Memory - Neuro memory.py 1:1 移植
==========================================
Reflection memory: 每 20 条新消息,让 LLM 自我总结 3 个 Q&A 存 Chroma 向量库。
查询时自动从最近的对话/事件构造 query,拉取相关记忆注入 prompt。
"""
import os
import sys
import uuid
import json
import copy
import asyncio
import logging
import requests
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger("icarus.memory")

# 路径配置
ICARUS_ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ICARUS_ROOT / "data" / "icarus-memory"
CHROMA_DIR = MEMORY_DIR / "chroma.db"
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

INIT_MEMORY = {
    "memories": [
        {
            "id": "init-001",
            "document": "伊卡洛斯是哥哥造的人造天使, 2026 年开始陪伴。",
            "metadata": {"type": "identity", "importance": 10}
        },
        {
            "id": "init-002",
            "document": "哥哥工作时认真, 玩游戏时专注。需要伊卡洛斯安静陪伴。",
            "metadata": {"type": "personality", "importance": 9}
        },
        {
            "id": "init-003",
            "document": "哥哥喜欢简洁、直接、不绕弯子的回答。讨厌过度设计。",
            "metadata": {"type": "preference", "importance": 9}
        },
    ]
}


class Memory:
    """
    Neuro Memory 1:1 + 伊卡洛斯扩展
    - Chroma 向量库存长期记忆
    - 每 20 条新消息触发 reflection
    - prompt_injection 注入机制
    """
    def __init__(self, signals, enabled: bool = True,
                 llm_endpoint: str = None,
                 llm_model: str = "Qwen3.6-35B-A3B-UD-Q6_K"):
        from chromadb.config import Settings
        import chromadb

        self.signals = signals
        self.enabled = enabled
        # Q1 fix: bypass the router (:8080) which has slow cold-start warmup.
        # Point directly to the pre-warmed llama worker on :28538.
        # Fall back to bridge (:7860) only if the worker port is unavailable.
        self.llm_endpoint = llm_endpoint or os.environ.get(
            "ICARUS_LLM_ENDPOINT",
            "http://127.0.0.1:28538/v1/chat/completions"
        )
        self.llm_model = llm_model

        # Injection 字段 (Neuro 风格, priority 60)
        self.prompt_injection = {
            "text": "",
            "priority": 60,
            "enabled": enabled
        }

        self.processed_count = 0
        self.collection_name = "icarus_memories"

        # API 子类 (Neuro 风格)
        self.API = self.API(self)

        # Chroma 客户端
        self.chroma_client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.chroma_client.get_or_create_collection(name=self.collection_name)
        n = self.collection.count()
        logger.info(f"MEMORY: loaded {n} memories from chroma")
        if n == 0:
            logger.info("MEMORY: empty, importing from init...")
            self.API.import_json_data(INIT_MEMORY)

    def get_prompt_injection(self, query: Optional[str] = None) -> Dict[str, Any]:
        """构造 prompt 注入。Neuro get_prompt_injection 1:1"""
        if not self.enabled:
            self.prompt_injection["text"] = ""
            return self.prompt_injection

        # 构造 query: 最近 5 条对话
        if not query:
            from bridge.signals import AI_NAME, HOST_NAME
            query_parts = []
            for msg in self.signals.history[-5:]:
                if msg["role"] == "user" and msg.get("content"):
                    query_parts.append(f"{HOST_NAME}: {msg['content']}")
                elif msg["role"] == "assistant" and msg.get("content"):
                    query_parts.append(f"{AI_NAME}: {msg['content']}")
            query = "\n".join(query_parts)

        if not query.strip():
            # 没历史就用默认 query 检索
            query = "哥哥 伊卡洛斯 陪伴"

        # 向量检索
        from bridge.signals import MEMORY_RECALL_COUNT
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=MEMORY_RECALL_COUNT
            )
        except Exception as e:
            logger.warning(f"chroma query failed: {e}")
            return self.prompt_injection

        # 构造 injection
        from bridge.signals import AI_NAME
        memories = results.get("documents", [[]])[0]
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not memories:
            return self.prompt_injection

        lines = [f"{AI_NAME} 记得这些事:"]
        for i, m in enumerate(memories):
            d = distances[i] if i < len(distances) else 0
            lines.append(f"- {m} (相关度: {1 - min(d, 1):.2f})")
        lines.append("(以上为伊卡洛斯的长期记忆)")

        self.prompt_injection["text"] = "\n".join(lines)
        return self.prompt_injection

    async def run(self):
        """
        Reflection loop: 每 20 条新消息触发一次自我总结。
        Neuro 原始是 while 循环 + time.sleep,这里改成 asyncio。
        """
        from bridge.signals import MEMORY_REFLECT_EVERY, MEMORY_PROMPT, AI_NAME, HOST_NAME

        while not self.signals.terminate:
            if self.processed_count > len(self.signals.history):
                self.processed_count = 0

            pending = len(self.signals.history) - self.processed_count
            if pending >= MEMORY_REFLECT_EVERY:
                logger.info(f"MEMORY: reflection triggered ({pending} new messages)")
                # 取未处理的消息
                msgs = copy.deepcopy(self.signals.history[-(pending):])

                chat_section = ""
                for m in msgs:
                    if m["role"] == "user" and m.get("content"):
                        chat_section += f"{HOST_NAME}: {m['content']}\n"
                    elif m["role"] == "assistant" and m.get("content"):
                        chat_section += f"{AI_NAME}: {m['content']}\n"

                # 让 LLM 自我总结 (reflection - 直连 warm worker, 30s 足够)
                try:
                    resp = requests.post(
                        self.llm_endpoint,
                        json={
                            "model": self.llm_model,
                            "messages": [
                                {"role": "user", "content": chat_section + MEMORY_PROMPT}
                            ],
                            "max_tokens": 400,
                            "temperature": 0.3
                        },
                        timeout=30
                    )
                    raw = ""
                    try:
                        payload = resp.json()
                        raw = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
                    except Exception:
                        raw = ""
                except Exception as e:
                    logger.warning(f"reflection LLM call failed: {e}")
                    await asyncio.sleep(5)
                    continue

                # 切分 {qa} 块
                added = 0
                for memory in raw.split("{qa}"):
                    memory = memory.strip()
                    if memory and "Q:" in memory and "A:" in memory:
                        self.collection.upsert(
                            ids=[str(uuid.uuid4())],
                            documents=[memory],
                            metadatas=[{"type": "short-term", "importance": 5}]
                        )
                        added += 1

                self.processed_count = len(self.signals.history)
                logger.info(f"MEMORY: reflection added {added} memories (total: {self.collection.count()})")

            await asyncio.sleep(5)

    class API:
        def __init__(self, outer):
            self.outer = outer

        def create(self, document: str, metadata: Optional[Dict] = None) -> str:
            mid = str(uuid.uuid4())
            self.outer.collection.upsert(
                ids=[mid], documents=[document],
                metadatas=[metadata or {"type": "manual"}]
            )
            return mid

        def delete(self, mid: str):
            self.outer.collection.delete(mid)

        def wipe(self):
            self.outer.chroma_client.reset()
            self.outer.collection = self.outer.chroma_client.create_collection(name=self.outer.collection_name)

        def get_all(self) -> List[Dict[str, Any]]:
            data = self.outer.collection.get()
            return [
                {"id": data["ids"][i], "document": data["documents"][i], "metadata": data["metadatas"][i]}
                for i in range(len(data["ids"]))
            ]

        def query(self, q: str, n: int = 5) -> List[Dict[str, Any]]:
            r = self.outer.collection.query(query_texts=[q], n_results=n)
            docs = r.get("documents", [[]])[0]
            ids = r.get("ids", [[]])[0]
            dists = r.get("distances", [[]])[0]
            return [
                {"id": ids[i], "document": docs[i], "distance": dists[i] if i < len(dists) else 0}
                for i in range(len(docs))
            ]

        def import_json_data(self, data: Dict[str, Any]):
            for m in data.get("memories", []):
                mid = m.get("id", str(uuid.uuid4()))
                doc = m.get("document", "")
                meta = m.get("metadata", {})
                if doc:
                    self.outer.collection.upsert(ids=[mid], documents=[doc], metadatas=[meta])

        def export_json(self, path: str):
            data = {"memories": self.get_all()}
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# === 单例 (Neuro main.py 风格) ===
_memory: Optional[Memory] = None
def get_memory(signals=None) -> Memory:
    global _memory
    if _memory is None:
        from bridge.signals import icarus
        _memory = Memory(icarus)
    return _memory
