"""herdr 深度 socket 客户端 —— 协议引擎自测（mock NDJSON 服务器，无需真实二进制）。

模型对齐真实 herdr：
  - 普通 RPC：每条连接处理一个请求，服务器响应后关闭连接。
  - events.subscribe：首响应确认，之后同一连接持续推送事件行。

运行：python core/herdr/_selftest.py
"""

import json
import os
import socket
import sys
import threading
import time
from typing import Any

import socketserver

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from herdr.client import HerdrClient, HerdrProtocolError, StreamTransport


class _TcpConn:
    def __init__(self, sock: socket.socket):
        self._s = sock

    def write(self, data: bytes) -> None:
        self._s.sendall(data)

    def read(self, n: int) -> bytes:
        try:
            return self._s.recv(n)
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def _make_tcp_transport(host: str, port: int):
    def factory(_path: str) -> StreamTransport:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        return StreamTransport(_TcpConn(s))

    return factory


_SUBSCRIBE_EVENTS = [
    {"type": "pane.agent_status_changed", "pane_id": "w1:p1", "agent_status": "working"},
    {"type": "pane.agent_status_changed", "pane_id": "w1:p1", "agent_status": "done"},
]


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline()
        except (OSError, ValueError):
            return
        if not line:
            return
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        method = msg.get("method")
        rid = msg.get("id")
        if method == "events.subscribe":
            self.wfile.write((json.dumps({"id": rid, "result": {"type": "subscribed"}}) + "\n").encode())
            self.wfile.flush()
            for ev in _SUBSCRIBE_EVENTS:
                self.wfile.write((json.dumps(ev) + "\n").encode())
                self.wfile.flush()
                time.sleep(0.05)
            # 保持连接，等待客户端关闭（模拟真实 herdr 长连）
            try:
                self.rfile.readline()
            except Exception:
                pass
            return
        # 普通 RPC：一个响应后关闭
        if method == "ping":
            result: dict = {"type": "pong", "protocol": 17}
        elif method == "session.snapshot":
            result = {"type": "session_snapshot", "snapshot": {"workspaces": [], "agents": []}}
        elif method == "pane.read":
            result = {"type": "pane_read", "text": "mock output"}
        else:
            result = {"type": "ok"}
        self.wfile.write((json.dumps({"id": rid, "result": result}) + "\n").encode())
        self.wfile.flush()
        # 关闭连接（per-connection 模型）


def _run_server(host: str, port: int):
    srv = socketserver.ThreadingTCPServer((host, port), _Handler)
    srv.allow_reuse_address = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def main() -> None:
    host, port = "127.0.0.1", 8731
    srv = _run_server(host, port)
    time.sleep(0.2)
    factory = _make_tcp_transport(host, port)
    c = HerdrClient(socket_path="mock", transport_factory=factory)

    # 1) ping / protocol
    pong = c.ping()
    assert pong["type"] == "pong", pong
    assert c.protocol() == 17, c.protocol()

    # 2) request/response（每请求独立连接）
    snap = c.request("session.snapshot")
    assert snap["type"] == "session_snapshot", snap

    # 3) events.subscribe 长连接事件分发
    got: list[dict] = []
    c.subscribe([{"type": "pane.agent_status_changed", "pane_id": "w1:p1"}], handler=got.append)
    deadline = time.time() + 3
    while len(got) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert [e["agent_status"] for e in got] == ["working", "done"], got
    c.close()

    # 4) pane.read 便捷方法
    r = c.pane_read("w1:p1")
    assert r["type"] == "pane_read", r

    srv.shutdown()
    print("PASS: herdr 深度 socket 客户端协议引擎自测通过")
    print("  - ping / protocol 校验 OK")
    print("  - request/response 每请求独立连接 OK")
    print("  - events.subscribe 长连接事件分发 OK (working -> done)")
    print("  - pane.read 便捷方法 OK")


if __name__ == "__main__":
    main()
