"""v5.tests.test_memory_api — unified dual-addressing interface tests.

Covers V5MemoryAPI.store / search / get / delete / stats, including the
Ekko-style structured filters (domain / category_path / key) that resolve
via exact tag matching (no ChromaDB required).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

V5_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(V5_ROOT))

from v5.memory_api import V5MemoryAPI

_MARK = f"__v5test_api_{int(time.time())}__"


def test_dual_addressing():
    api = V5MemoryAPI()
    mid = api.store(
        f"{_MARK} structured memory",
        domain="test_dom",
        category_path="self/values",
        key="api_key_1",
        importance=0.8,
    )
    assert mid > 0
    assert len(api.search(domain="test_dom")) > 0
    assert len(api.search(key="api_key_1")) > 0
    assert len(api.search(category_path="self/values")) > 0


def test_get_delete_stats():
    api = V5MemoryAPI()
    mid = api.store(f"{_MARK} removable", domain="del_dom", key="del_k")
    got = api.get(mid)
    assert got is not None and got["id"] == mid
    assert api.delete(mid) is True
    assert isinstance(api.stats(), dict)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
