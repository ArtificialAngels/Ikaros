"""
Memos (usememos/memos) integration for Hermes.

Memos is a self-hosted, Markdown-native note-taking app
(https://github.com/usememos/memos). It provides a REST API that
we use as an OPTIONAL memory/knowledge backend.

If memos is running (locally at :5230 or remote), Hermes can:
- Save conversations as memos
- Search memos for context
- Browse memories in memos' web UI

This module is a client; the memos server is separate.
Run ``bin\\setup-memos.bat`` (or download from usememos.com) to install.
"""
from __future__ import annotations
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any
import httpx

logger = logging.getLogger("hermes.memos")


class MemosClient:
    """
    Async-friendly client for the memos REST API.

    API reference: https://usememos.com/docs/api
    """

    def __init__(self, base_url: str = "http://127.0.0.1:5230", token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.AsyncClient(timeout=30.0)
        self._available = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def available(self) -> bool:
        return self._available

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def health_check(self) -> bool:
        """Check if memos is reachable."""
        try:
            r = await self._client.get(f"{self.base_url}/api/v1/ping")
            self._available = r.status_code == 200
            if self._available:
                logger.info(f"memos is available at {self.base_url}")
            return self._available
        except Exception as e:
            self._available = False
            logger.debug(f"memos health check failed: {e}")
            return False

    async def list_memos(self, limit: int = 20, offset: int = 0) -> list[dict]:
        """List recent memos."""
        if not self._available:
            return []
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/memos",
                params={"limit": limit, "offset": offset, "rowStatusNanos": "NORMAL"},
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            return data.get("memos", [])
        except Exception as e:
            logger.warning(f"list_memos failed: {e}")
            return []

    async def create_memo(self, content: str, visibility: str = "PRIVATE") -> dict | None:
        """Create a new memo. Returns the memo dict or None on failure."""
        if not self._available:
            return None
        try:
            payload = {"content": content, "visibility": visibility}
            r = await self._client.post(
                f"{self.base_url}/api/v1/memos",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"create_memo failed: {e}")
            return None

    async def get_memo(self, memo_id: str) -> dict | None:
        """Get a memo by ID."""
        if not self._available:
            return None
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/memos/{memo_id}",
                headers=self._headers(),
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"get_memo({memo_id}) failed: {e}")
            return None

    async def delete_memo(self, memo_id: str) -> bool:
        """Delete a memo."""
        if not self._available:
            return False
        try:
            r = await self._client.delete(
                f"{self.base_url}/api/v1/memos/{memo_id}",
                headers=self._headers(),
            )
            return r.status_code in (200, 204)
        except Exception as e:
            logger.warning(f"delete_memo failed: {e}")
            return False

    async def search_memos(self, query: str, limit: int = 10) -> list[dict]:
        """
        Search memos by content. Memos has FTS5-based search.
        """
        if not self._available:
            return []
        try:
            # Memos search endpoint
            r = await self._client.get(
                f"{self.base_url}/api/v1/memos",
                params={
                    "filter": f'content.contains("{query}")',
                    "limit": limit,
                },
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
            memos = data.get("memos", [])
            return memos
        except Exception as e:
            logger.warning(f"search_memos failed: {e}")
            return []

    async def close(self):
        await self._client.aclose()


# ---- Memory backend that uses Memos ----

class MemosMemoryBackend:
    """
    Adapter that makes MemosClient look like a memory backend
    compatible with Hermes's memory system.

    Falls back to local JSONL if memos is unavailable.
    """

    def __init__(self, memos_client: MemosClient, local_path: Path):
        self.memos = memos_client
        self.local_path = local_path
        self.local_path.mkdir(parents=True, exist_ok=True)
        self.local_file = local_path / "memory.jsonl"
        # Local items cache
        self._local_items: list[dict] = []
        self._load_local()

    def _load_local(self):
        if not self.local_file.exists():
            return
        self._local_items = []
        with open(self.local_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        self._local_items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    def _save_local(self):
        with open(self.local_file, "w", encoding="utf-8") as f:
            for it in self._local_items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

    async def remember(self, text: str, tags: list[str] | None = None) -> str:
        """Save to memos (primary) + local JSONL (backup)."""
        tags = tags or []
        # Try memos
        if self.memos.available:
            content = text
            if tags:
                content = f"<!-- tags: {','.join(tags)} -->\n" + content
            memo = await self.memos.create_memo(content)
            if memo:
                return memo.get("name", memo.get("id", "unknown"))

        # Fallback: local
        item = {
            "id": str(uuid.uuid4()),
            "text": text,
            "tags": tags,
            "created_at": time.time(),
        }
        self._local_items.append(item)
        self._save_local()
        return item["id"]

    async def recall(self, query: str, k: int = 5) -> list[tuple[dict, float]]:
        """Search memos first, fall back to local keyword match."""
        results = []

        # Try memos search
        if self.memos.available:
            memos = await self.memos.search_memos(query, limit=k)
            for m in memos:
                content = m.get("content", "")
                # Simple relevance score: count query term occurrences
                score = sum(1 for w in query.lower().split() if w in content.lower()) / max(len(query.split()), 1)
                results.append(({
                    "id": m.get("name", "?"),
                    "text": content,
                    "source": "memos",
                    "created_at": m.get("createTime", time.time()),
                }, score))

        # Also search local
        for it in self._local_items:
            text = it.get("text", "")
            score = sum(1 for w in query.lower().split() if w in text.lower()) / max(len(query.split()), 1)
            if score > 0:
                results.append((it, score))

        # Sort by score
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:k]

    def stats(self) -> dict:
        return {
            "backend": "memos + local" if self.memos.available else "local only",
            "memos_available": self.memos.available,
            "local_items": len(self._local_items),
        }


# ---- CLI helper ----

async def main():
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5230"
    client = MemosClient(base)
    ok = await client.health_check()
    print(f"memos @ {base}: {'OK' if ok else 'NOT AVAILABLE'}")
    if ok:
        memos = await client.list_memos(limit=5)
        print(f"Recent memos: {len(memos)}")
        for m in memos:
            content = m.get("content", "")[:60].replace("\n", " ")
            print(f"  - {content}...")
    await client.close()
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
