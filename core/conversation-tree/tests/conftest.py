"""pytest 共享 fixtures: 加载 server.py + MockStore + tmp_path 重定向 + 状态重置.

关键点:
- server.py 不是包内模块 (core/conversation-tree/ 无 __init__.py), 用 importlib
  按文件路径加载为 ``ct_server`` 模块.
- V5_DATA_DIR 在两处生效: ``server.V5_DATA_DIR`` (sessions.json + 拓扑 JSON 检查)
  和 ``memory_v5.conversation_tree.V5_DATA_DIR`` (ConversationTree 默认 data_dir,
  写拓扑 JSON). 两处都要重定向到 tmp_path, 否则测试会污染真实 data/v5/ 目录.
- v5s (memory_v5.store) 的 store/get_batch/search/delete 全部替换成 MockStore,
  测试不依赖真实 SQLite/Chroma.
- 每个测试前重置 server 模块级状态 (_tree/_retriever/_sessions/_active_session_id),
  避免跨测试污染.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_CORE = _HERE.parent.parent  # E:/Ikaros/core
sys.path.insert(0, str(_CORE))

# ── 加载 server.py 为独立模块 (非包, 无 __init__) ──
_SERVER_PATH = _HERE.parent / "server.py"
_spec = importlib.util.spec_from_file_location("ct_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

# 引入 memory_v5.conversation_tree, 便于 patch V5_DATA_DIR
import memory_v5.conversation_tree as ct  # noqa: E402


# ── MockStore: 内存 V5 store 后端, 接口与 store.py 一致 ──
class MockStore:
    """模拟 memory_v5.store 的 store/get_batch/search/delete.

    返回 memory_v5.store.Memory 对象 (get_batch) 以兼容 _load_str 的 .content 访问.
    """

    def __init__(self):
        from memory_v5.store import Memory
        self._Memory = Memory
        self.mem: dict[int, str] = {}
        self.counter = 0

    def store(self, content: str, type: str = "conversation",
              weight: float = 0.6, tags: str = "", **kwargs) -> int:
        self.counter += 1
        self.mem[self.counter] = content
        return self.counter

    def get_batch(self, memory_ids: list[int]) -> dict:
        Memory = self._Memory
        out = {}
        for mid in memory_ids:
            if mid in self.mem:
                out[mid] = Memory(
                    id=mid, content=self.mem[mid], type="conversation",
                    tags="", weight=0.6, access_count=0, last_accessed=0.0,
                    created=0.0, short_term=True, long_term=False,
                )
        return out

    def search(self, query: str, top_k: int = 10, **kwargs):
        Memory = self._Memory
        q = query.lower()
        out = []
        for mid, content in self.mem.items():
            if q in content.lower():
                out.append(Memory(
                    id=mid, content=content, type="conversation", tags="",
                    weight=0.6, access_count=0, last_accessed=0.0,
                    created=0.0, short_term=True, long_term=False,
                ))
        return out[:top_k]

    def delete(self, memory_id: int) -> bool:
        return self.mem.pop(memory_id, None) is not None


# ─────────────── fixtures ───────────────

@pytest.fixture
def mock_store():
    """每个测试一个独立 MockStore, 避免跨测试 id 冲突."""
    return MockStore()


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """把 V5_DATA_DIR + SESSIONS_FILE 重定向到 tmp_path, 避免污染真实数据目录.

    返回 tmp_path (Path).
    """
    # 重定向两处 V5_DATA_DIR:
    #   1) server.V5_DATA_DIR (server.py 内部用其构造 sessions.json 路径 + 拓扑检查)
    #   2) ct.V5_DATA_DIR (ConversationTree 默认 data_dir, 写拓扑 JSON)
    monkeypatch.setattr(server, "V5_DATA_DIR", tmp_path)
    monkeypatch.setattr(ct, "V5_DATA_DIR", tmp_path)
    # SESSIONS_FILE 在 server.py 模块加载时已基于原 V5_DATA_DIR 算出, 故单独 patch
    monkeypatch.setattr(server, "SESSIONS_FILE", tmp_path / "sessions.json")
    return tmp_path


@pytest.fixture
def patched_store(monkeypatch, mock_store):
    """把 server.v5s 的 4 个函数替换成 MockStore 方法."""
    monkeypatch.setattr(server.v5s, "store", mock_store.store)
    monkeypatch.setattr(server.v5s, "get_batch", mock_store.get_batch)
    monkeypatch.setattr(server.v5s, "search", mock_store.search)
    monkeypatch.setattr(server.v5s, "delete", mock_store.delete)
    return mock_store


@pytest.fixture
def reset_state():
    """重置 server 模块级状态, 测试间无残留.

    ensure_tree() 会按 _tree 是否为 None 决定是否重建, 故每次都强制清空.
    """
    # 保存原状态
    saved = {
        "tree": server._tree,
        "retriever": server._retriever,
        "sessions": list(server._sessions),
        "active_id": server._active_session_id,
    }
    # 清空
    server._tree = None
    server._retriever = None
    server._sessions = []
    server._active_session_id = None
    # supervisor 单例可能也已构造, 重置让下个测试惰性重建
    server._supervisor = None
    yield
    # 还原 (避免 fixture 间互相干扰)
    server._tree = saved["tree"]
    server._retriever = saved["retriever"]
    server._sessions = saved["sessions"]
    server._active_session_id = saved["active_id"]
    server._supervisor = None


@pytest.fixture
def http_server(tmp_data_dir, patched_store, reset_state):
    """起一个真实 ThreadingHTTPServer 在 port 0 (随机端口), 返回 base_url.

    测试用 urllib 直接打 HTTP, 覆盖完整请求-响应链路 (Handler + server.py 模块状态).
    每个 ensure_tree() 都会用已 patch 的 tmp_data_dir / MockStore.
    """
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=5)


# ── HTTP 小工具: 让测试代码更紧凑 ──

def http_get(base_url: str, path: str):
    """GET <base_url><path>, 返回 (status, parsed_json_or_text)."""
    with urllib.request.urlopen(base_url + path) as resp:
        body = resp.read().decode("utf-8")
        status = resp.status
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def http_post(base_url: str, path: str, payload: dict | None = None):
    """POST JSON, 返回 (status, parsed_json_or_text)."""
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        base_url + path, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        status = e.code
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body
