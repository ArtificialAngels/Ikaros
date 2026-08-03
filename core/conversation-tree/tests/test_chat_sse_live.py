"""真实 HTTP gateway SSE 集成测试 (R5 隐患清理补充).

M5 的 test_chat_sse.py 用 urlopen 桩回放 SSE, 未覆盖 *真实网络* 路径:
- 真实 socket 上的分块读取 / 连接关闭时机
- Windows HTTP/1.1 keep-alive 抖动 (已知会触发 ConnectionAbortedError: WinError 10053)

本测试起一个本地 ThreadingHTTPServer 桩, 发送完整 SSE 并以 `Connection: close`
干净关闭(规避 keep-alive 抖动), 把 server.HERMES_AGENT_URL 指向它, 端到端验证
_chat_stream_events 的解析(正文/工具/usage)与中段中断回退行为。
"""
from __future__ import annotations

import http.server
import threading
from contextlib import closing

import pytest

from conftest import server  # type: ignore  # noqa: E402


def _make_handler(sse_body: bytes, close_after: bool = True):
    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            # 吞掉请求体(测试不校验), 直接回 SSE
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except Exception:
                length = 0
            if length:
                try:
                    self.rfile.read(length)
                except Exception:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            if close_after:
                # 关键: 关闭 keep-alive, 让客户端读至 EOF 干净退出,
                # 避免 Windows HTTP/1.1 长连接被提前 abort (WinError 10053).
                self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(sse_body)))
            self.end_headers()
            try:
                self.wfile.write(sse_body)
                self.wfile.flush()
            except Exception:
                pass

        def log_message(self, *a):  # 静默
            pass

    return _H


def _start_server(sse_body: bytes):
    """起一个本地桩 server, 返回 (url, shutdown_event, thread)。"""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(sse_body))
    port = httpd.server_address[1]
    evt = threading.Event()

    def _serve():
        while not evt.is_set():
            httpd.handle_request()  # 一次处理一个连接(测试只发一个)

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    return url, evt, t


def _events_to_list(gen):
    return list(gen)


SSE_FULL = (
    "data: {\"choices\":[{\"delta\":{\"content\":\"你好\"}}]}\n\n"
    "data: {\"choices\":[{\"delta\":{\"content\":\"世界\"}}]}\n\n"
    "event: hermes.tool.progress\n"
    "data: {\"tool\":\"web_search\",\"toolCallId\":\"call_1\",\"status\":\"running\",\"label\":\"搜索中\"}\n\n"
    "event: hermes.tool.progress\n"
    "data: {\"tool\":\"web_search\",\"toolCallId\":\"call_1\",\"status\":\"completed\",\"result\":\"搜索结果文本\"}\n\n"
    "data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2}}\n\n"
    "data: [DONE]\n\n"
).encode("utf-8")


def test_live_gateway_sse_parse():
    """真实 socket 上完整 SSE: 解析正文 + 工具事件 + usage 落 collector。"""
    url, evt, t = _start_server(SSE_FULL)
    old = server.HERMES_AGENT_URL
    server.HERMES_AGENT_URL = url
    try:
        collector = {"content": "", "thinking": "", "tool_calls": [],
                     "usage": {}, "warns": []}
        events = _events_to_list(
            server._chat_stream_events([{"role": "user", "content": "hi"}],
                                       "ikaros", None, collector)
        )
        types = [e.get("type") for e in events]
        # 正文两片
        contents = [e["delta"] for e in events if e.get("type") == "content"]
        assert contents == ["你好", "世界"], types
        # 工具 running + completed
        assert any(e.get("type") == "tool_call" and e.get("id") == "call_1" for e in events)
        assert any(e.get("type") == "tool_result" and e.get("ok") is True for e in events)
        # collector 回填
        assert collector["content"] == "你好世界"
        assert collector["usage"].get("completion_tokens") == 2
        # L5: 工具结果按 toolCallId 回填
        tc = next(tc for tc in collector["tool_calls"] if tc.get("id") == "call_1")
        assert tc["result_summary"] == "搜索结果文本"
        # 无降级 warn (gateway 正常)
        assert not any(e.get("type") == "warn" for e in events)
    finally:
        server.HERMES_AGENT_URL = old
        evt.set()
        t.join(timeout=2)


SSE_INTERRUPT_BEFORE_CONTENT = (
    "event: hermes.tool.progress\n"
    "data: {\"tool\":\"web_search\",\"toolCallId\":\"call_2\",\"status\":\"running\",\"label\":\"搜索中\"}\n\n"
    # 故意不发正文就结束(模拟中段中断) —— 但本桩是干净关闭, 走"无正文→降级本地"分支需
    # gateway 真正抛异常; 干净 EOF 时 gateway 正常结束(无正文)→ 触发空响应降级 warn.
).encode("utf-8")


def test_live_gateway_empty_response_falls_back_warn():
    """gateway 正常结束但无正文 → 黄色 warn(空响应降级), 不报错。"""
    url, evt, t = _start_server(SSE_INTERRUPT_BEFORE_CONTENT)
    old = server.HERMES_AGENT_URL
    server.HERMES_AGENT_URL = url
    try:
        collector = {"content": "", "thinking": "", "tool_calls": [],
                     "usage": {}, "warns": []}
        events = _events_to_list(
            server._chat_stream_events([{"role": "user", "content": "hi"}],
                                       "ikaros", None, collector)
        )
        # 至少应有一条 warn (空响应降级)
        assert any(e.get("type") == "warn" for e in events), events
    finally:
        server.HERMES_AGENT_URL = old
        evt.set()
        t.join(timeout=2)
