"""
Icarus NotebookLM - notebooklm-py 0.7.2 集成
============================================
伊卡洛斯用 Google NotebookLM 做研究/写报告/生成 podcast/quiz。
需要 `notebooklm login` 一次性认证。
"""
import os
import sys
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("icarus.notebooklm")

# 路径常量
NOTEBOOKLM_HOME = os.environ.get("NOTEBOOKLM_HOME", os.path.expanduser("~/.notebooklm"))


class NotebookLMUnavailable(Exception):
    """NotebookLM 未认证 / 库未装"""
    pass


def is_available() -> bool:
    """检查 NotebookLM 是否就绪 (认证 + 库)"""
    try:
        import notebooklm  # noqa
    except ImportError:
        return False
    # 检查 storage_state
    storage = os.path.join(NOTEBOOKLM_HOME, "storage_state.json")
    return os.path.exists(storage)


async def create_notebook(title: str) -> Dict[str, Any]:
    """建一个 notebook"""
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated. Run: notebooklm login")
    from notebooklm import NotebookLMClient
    async with await NotebookLMClient.from_storage() as client:
        nb = await client.notebooks.create(title)
        return {
            "id": nb.id,
            "title": nb.title,
            "sources_count": 0,
            "url": f"https://notebooklm.google.com/notebook/{nb.id}",
        }


async def list_notebooks() -> List[Dict[str, Any]]:
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated")
    from notebooklm import NotebookLMClient
    async with await NotebookLMClient.from_storage() as client:
        nbs = await client.notebooks.list()
        return [{"id": nb.id, "title": nb.title} for nb in nbs]


async def add_url(notebook_id: str, url: str) -> Dict[str, Any]:
    """给 notebook 加 URL 源"""
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated")
    from notebooklm import NotebookLMClient
    async with await NotebookLMClient.from_storage() as client:
        src = await client.sources.add_url(notebook_id, url)
        return {
            "id": src.id,
            "title": src.title if hasattr(src, "title") else url,
            "status": "processing" if not src.is_ready else "ready",
        }


async def add_text(notebook_id: str, text: str, title: str = "pasted text") -> Dict[str, Any]:
    """加纯文本源"""
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated")
    from notebooklm import NotebookLMClient
    async with await NotebookLMClient.from_storage() as client:
        src = await client.sources.add_text(notebook_id, text, title=title)
        return {"id": src.id, "title": title, "status": "processing"}


async def ask(notebook_id: str, question: str) -> Dict[str, Any]:
    """问问题, 返回 NotebookLM RAG 答案 + 引用"""
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated")
    from notebooklm import NotebookLMClient
    async with await NotebookLMClient.from_storage() as client:
        result = await client.chat.ask(notebook_id, question)
        return {
            "answer": result.answer if hasattr(result, "answer") else str(result),
            "references": [
                {
                    "source_id": getattr(ref, "source_id", ""),
                    "citation": getattr(ref, "citation_number", ""),
                }
                for ref in (result.references if hasattr(result, "references") else [])
            ],
        }


async def generate_audio(notebook_id: str, length: str = "default") -> Dict[str, Any]:
    """生成 audio overview (podcast 形式)"""
    if not is_available():
        raise NotebookLMUnavailable("notebooklm not authenticated")
    from notebooklm import NotebookLMClient, AudioLength
    length_map = {
        "short": AudioLength.SHORT,
        "default": AudioLength.DEFAULT,
        "long": AudioLength.LONG,
    }
    async with await NotebookLMClient.from_storage() as client:
        artifact = await client.artifacts.generate_audio(
            notebook_id, length=length_map.get(length, AudioLength.DEFAULT)
        )
        return {
            "id": artifact.id,
            "type": "audio",
            "status": "generating" if not artifact.is_ready else "ready",
        }


# === Neuro 风格 Module ===
from bridge.neuro.module import Module


class NotebookLMModule(Module):
    """
    伊卡洛斯的"研究笔记本"模块
    - notebooklm_unavailable 时降级 (不抛错)
    - prompt_injection 告诉 LLM 它可以用 notebooklm
    """
    def __init__(self, signals, enabled: bool = True):
        super().__init__(signals, enabled)
        self.prompt_injection.text = (
            "伊卡洛斯可以用 NotebookLM 做深度研究 (需 `notebooklm login` 认证):\n"
            "  - notebook_create(title) - 建新笔记本\n"
            "  - notebook_add_url(id, url) - 加 URL 源\n"
            "  - notebook_add_text(id, text) - 加文本源\n"
            "  - notebook_ask(id, question) - RAG 问答, 带引用\n"
            "  - notebook_generate_audio(id) - 生成播客\n"
            "哥哥说'研究一下 X'/'做一份报告'/'总结这几篇文章' 时用。"
        )
        self.prompt_injection.priority = 75
        self._available = is_available()

    async def run(self):
        """心跳检查 NotebookLM 状态"""
        while not self.signals.terminate:
            new_status = is_available()
            if new_status != self._available:
                logger.info(f"NotebookLM availability changed: {self._available} -> {new_status}")
                self._available = new_status
                self.signals.sio_queue.append({
                    "type": "notebooklm_status",
                    "available": self._available
                })
            await asyncio.sleep(30)

    class API:
        def __init__(self, outer):
            self.outer = outer

        def is_available(self) -> bool:
            return self.outer._available

        def status(self) -> Dict[str, Any]:
            return {
                "available": is_available(),
                "home": NOTEBOOKLM_HOME,
                "storage_exists": os.path.exists(os.path.join(NOTEBOOKLM_HOME, "storage_state.json")),
            }

        async def create(self, title: str) -> Dict[str, Any]:
            return await create_notebook(title)

        async def list(self) -> List[Dict[str, Any]]:
            return await list_notebooks()

        async def add_url(self, notebook_id: str, url: str) -> Dict[str, Any]:
            return await add_url(notebook_id, url)

        async def add_text(self, notebook_id: str, text: str, title: str = "pasted") -> Dict[str, Any]:
            return await add_text(notebook_id, text, title)

        async def ask(self, notebook_id: str, question: str) -> Dict[str, Any]:
            return await ask(notebook_id, question)

        async def audio(self, notebook_id: str, length: str = "default") -> Dict[str, Any]:
            return await generate_audio(notebook_id, length)


def outer_available(outer):
    return outer._available


# === CLI 测试 ===
if __name__ == "__main__":
    import asyncio
    mod = NotebookLMModule(signals=None)
    print("status:", mod.API.status())
    if mod.API.is_available():
        async def test():
            nbs = await list_notebooks()
            print("notebooks:", nbs)
        asyncio.run(test())
    else:
        print("not authenticated, run: notebooklm login")
