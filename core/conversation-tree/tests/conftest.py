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
import socket
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

# 2026-09-05: 预加载 _dsh_shared 并注册到 sys.modules, 确保 server.py 加载时
# `import _dsh_shared` 拿到的是同一份模块对象 (避免缓存刷新不生效).
_DSH_PATH = _HERE.parent / "_dsh_shared.py"
_dsh_spec = importlib.util.spec_from_file_location("_dsh_shared", _DSH_PATH)
dsh_shared = importlib.util.module_from_spec(_dsh_spec)
sys.modules["_dsh_shared"] = dsh_shared
_dsh_spec.loader.exec_module(dsh_shared)

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
    yield
    # 还原 (避免 fixture 间互相干扰)
    server._tree = saved["tree"]
    server._retriever = saved["retriever"]
    server._sessions = saved["sessions"]
    server._active_session_id = saved["active_id"]


@pytest.fixture
def http_server(tmp_data_dir, patched_store, reset_state):
    """起一个真实 ThreadingHTTPServer 在 port 0 (随机端口), 返回 base_url.

    测试用 urllib 直接打 HTTP, 覆盖完整请求-响应链路 (Handler + server.py 模块状态).
    每个 ensure_tree() 都会用已 patch 的 tmp_data_dir / MockStore.

    2026-09-05: DSH_HOME 由调用方 (test_dsh_base 的 fixture) 通过 monkeypatch 设定,
    本 fixture 不动 env (避免覆盖调用方的配置).
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
#
# Why raw socket (not urllib): urllib.request.urlopen('http://host:port/path')
# sends an absolute URI in the request line (`GET http://host:port/path HTTP/1.1`).
# BaseHTTPRequestHandler's self.path then contains the FULL URI, not the path,
# so every `if path == '/api/sessions/create'` branch misses and the server
# returns 404. This bit 45 conversation-tree tests on 2026-09-05; see
# `test_boot_renders.py::_sock_get` for the original fix.
#
# A real browser sends a relative path (`GET /api/sessions/create HTTP/1.1`).
# We mimic that here so tests exercise the same code path that the browser
# does in production.


def _parse_response(raw: bytes) -> tuple[int, bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    return status, body


def _dechunk(body: bytes) -> bytes:
    """Decode a chunked Transfer-Encoding body.

    Server uses Transfer-Encoding: chunked for SSE (see server.py::_send_sse).
    Format: `<hex-size>\\r\\n<data>\\r\\n` ... `0\\r\\n\\r\\n` terminator. Standard
    clients (curl, urllib, browsers) handle this automatically; raw sockets
    don't, so we strip chunk headers here. Returns the decoded body.
    """
    out = bytearray()
    i = 0
    while i < len(body):
        # Find the next \\r\\n (end of chunk-size line)
        crlf = body.find(b"\r\n", i)
        if crlf == -1:
            # Malformed; return what we have
            out.extend(body[i:])
            break
        size_str = body[i:crlf].decode("latin-1", errors="replace").strip()
        if not size_str:
            # Empty line — skip
            i = crlf + 2
            continue
        try:
            chunk_size = int(size_str, 16)
        except ValueError:
            # Not a chunk-size line; bail out
            out.extend(body[i:])
            break
        if chunk_size == 0:
            # Terminator; we're done
            break
        data_start = crlf + 2
        out.extend(body[data_start : data_start + chunk_size])
        # Skip past data + trailing \\r\\n
        i = data_start + chunk_size + 2
    return bytes(out)


def _raw_http(
    method: str,
    base_url: str,
    path: str,
    body: bytes = b"",
    extra_headers: dict | None = None,
) -> tuple[int, bytes]:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    headers = {
        "Host": f"{host}:{port}",
        "Connection": "close",  # request connection close so server EOFs
        "Content-Length": str(len(body)),
    }
    if extra_headers:
        headers.update(extra_headers)
    raw = f"{method} {path} HTTP/1.1\r\n"
    for k, v in headers.items():
        raw += f"{k}: {v}\r\n"
    raw += "\r\n"
    s = socket.socket()
    # Short timeout — server should respond in <2s for non-streaming, <5s for SSE.
    s.settimeout(5.0)
    s.connect((host, port))
    s.sendall(raw.encode("latin-1") + body)
    buf = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            # Stop reading once we have the full response:
            # 1) HTTP/1.1 with Content-Length: header bytes match
            # 2) Chunked transfer: terminator `\r\n0\r\n\r\n` at line start
            head, _, _ = buf.partition(b"\r\n\r\n")
            cl_match = None
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    try:
                        cl_match = int(line.split(b":", 1)[1].strip())
                    except (ValueError, IndexError):
                        cl_match = None
                    break
            is_chunked = b"transfer-encoding: chunked" in head.lower()
            if cl_match is not None and not is_chunked:
                # Body starts after first \r\n\r\n; total = head_len + 4 + cl_match
                if len(buf) >= len(head) + 4 + cl_match:
                    break
            elif is_chunked and (b"\r\n0\r\n\r\n" in buf):
                break
    except socket.timeout:
        import os as _os
        _os.write(2, f"[raw http] TIMEOUT at {port} {method} {path} after 5s, got {len(buf)} bytes\n".encode())
        if buf:
            _os.write(2, f"  head: {buf[:200]!r}\n".encode())
        # Fall through to finally: s.close() triggers server-side BrokenPipe
        # which the server's _send_sse handler logs and exits gracefully.
    finally:
        # CRITICAL: shut down the write side immediately. BaseHTTPRequestHandler
        # on HTTP/1.1 keeps the socket open by default (keep-alive) and
        # ignores our `Connection: close` request header. If we only close()
        # the socket, the server might not see EOF before our recv() timeout.
        # shutdown(SHUT_WR) signals "no more data" and the server's next
        # read returns '' which triggers connection close.
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        s.close()
    # Parse status + headers
    status, raw_body = _parse_response(buf)
    # Dechunk if needed
    # (Check the header from buf; we already partitioned it away so
    # peek at the first 4KB of buf.)
    if b"transfer-encoding: chunked" in buf[:4096].lower():
        raw_body = _dechunk(raw_body)
    return status, raw_body


def http_get(base_url: str, path: str):
    """GET <base_url><path>, 返回 (status, parsed_json_or_text)."""
    status, body_bytes = _raw_http("GET", base_url, path)
    body = body_bytes.decode("utf-8", errors="replace")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def http_post(base_url: str, path: str, payload: dict | None = None):
    """POST JSON, 返回 (status, parsed_json_or_text)."""
    data = json.dumps(payload or {}).encode("utf-8")
    status, body_bytes = _raw_http(
        "POST",
        base_url,
        path,
        body=data,
        extra_headers={"Content-Type": "application/json"},
    )
    body = body_bytes.decode("utf-8", errors="replace")
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body
