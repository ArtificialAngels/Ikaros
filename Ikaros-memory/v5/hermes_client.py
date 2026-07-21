# 详细说明见 docs/scripts/Ikaros-memory/v5/hermes_client.md
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("ikaros.memory.v5.hermes")

_HERMES_URL = "http://127.0.0.1:9119"
_WS_URL = "ws://127.0.0.1:9119/api/ws"

# 在模块加载时（主线程）预先 import websockets C 扩展。
# 此扩展在 portable-python 下若首次在 daemon 线程里导入会触发 0xC0000005；
# 预加载使其入口在主线程完成，daemon 线程复用时命中 sys.modules 缓存。
try:
    import websockets as _preload_ws  # noqa: F401
except Exception:
    pass

_sessions: dict[str, str] = {}  # {session_name: session_id}
_requests: "queue.Queue[_HermesRequest] | None" = None
_started = False


class _HermesRequest:
    def __init__(self, session_name: str, prompt: str, timeout: float = 120):
        self.session_name = session_name
        self.prompt = prompt
        self.timeout = timeout
        self.result: "queue.Queue[str]" = queue.Queue()


def _scrape_token() -> str:
    """从仪表盘 HTML 抓取 WS token (HTTP GET, 同步)."""
    resp = urllib.request.urlopen(f"{_HERMES_URL}/", timeout=5)
    html = resp.read().decode("utf-8")
    m = re.search(r'__HERMES_SESSION_TOKEN__="([^"]+)"', html)
    if not m:
        raise RuntimeError("Hermes token not found in dashboard HTML")
    return m.group(1)


def start():
    """启动后台 asyncio 线程 (幂等)."""
    global _started, _requests
    if _started:
        return
    _requests = queue.Queue()
    _started = True
    t = threading.Thread(target=_worker, daemon=True, name="hermes-client")
    t.start()
    logger.info("hermes_client: background worker started")


def _worker():
    """后台线程: 维护一条 WS 连接, 处理请求队列."""
    import asyncio
    import websockets

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _run():
        ws = None
        retry_delay = 1
        while True:
            req: Optional[_HermesRequest] = None
            try:
                req = _requests.get(timeout=30)  # type: ignore
            except queue.Empty:
                continue

            try:
                # 确保 WS 连接
                if ws is None:
                    token = _scrape_token()
                    ws = await asyncio.wait_for(
                        websockets.connect(f"{_WS_URL}?token={token}", max_size=2**20),
                        timeout=10,
                    )
                    await asyncio.wait_for(ws.recv(), timeout=5)  # gateway.ready
                    retry_delay = 1

                reply = await _chat_one(ws, req)
                req.result.put(reply)
            except Exception as e:
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = None
                try:
                    req.result.put_nowait(f"(Hermes unavailable: {e})")
                except Exception:
                    pass
                logger.debug("hermes_client: %s, retry in %ds", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)

    loop.run_until_complete(_run())


async def _chat_one(ws, req: _HermesRequest) -> str:
    """单次 Hermes RPC: 找/建 session, prompt.submit, 收集回复."""
    sid = await _find_or_create_session(ws, req.session_name)
    return await _submit_prompt(ws, sid, req.prompt, req.timeout)


async def _find_or_create_session(ws, name: str) -> str:
    """查找已有 session, 找不到就建."""
    if name in _sessions:
        return _sessions[name]

    rid = f"find_{int(time.time()*1000)}"
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": rid, "method": "session.list", "params": {},
    }))
    for _ in range(10):
        raw = await ws.recv()
        d = json.loads(raw)
        if d.get("id") == rid:
            for s in d.get("result", {}).get("sessions", []):
                if s.get("title") == name:
                    sid = s.get("id", "")
                    if sid:
                        _sessions[name] = sid
                        return sid
            break

    # 不存在 → 创建
    rid2 = f"new_{int(time.time()*1000)}"
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": rid2, "method": "session.new",
        "params": {"title": name},
    }))
    for _ in range(10):
        raw = await ws.recv()
        d = json.loads(raw)
        if d.get("id") == rid2:
            sid = d.get("result", {}).get("id", "")
            if sid:
                _sessions[name] = sid
                return sid
            break

    raise RuntimeError(f"Cannot create session '{name}' (Hermes backend may be unavailable)")


async def _submit_prompt(ws, sid: str, text: str, timeout: float) -> str:
    """发送 prompt.submit, 从 event 流收集完整回复."""
    rid = f"chat_{int(time.time()*1000)}"
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": rid, "method": "prompt.submit",
        "params": {"session_id": sid, "text": text},
    }))
    reply = ""
    import asyncio as _asyncio
    deadline = _asyncio.get_event_loop().time() + timeout
    while _asyncio.get_event_loop().time() < deadline:
        remaining = deadline - _asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        raw = await _asyncio.wait_for(ws.recv(), timeout=min(remaining, 30))
        d = json.loads(raw)
        if d.get("id") == rid:
            break
        if d.get("method") == "event":
            p = d.get("params", {})
            t = p.get("type", "")
            if t == "message.delta":
                reply += p.get("delta", "") or ""
            elif t == "completion":
                final = p.get("text", "") or ""
                reply = final if final else reply
                break
            elif t == "error":
                raise RuntimeError(f"Hermes error: {p.get('message', 'unknown')}")
    return reply.strip()


def chat(session_name: str, prompt: str, timeout: float = 120) -> str:
    """同步阻塞: 通过 Hermes :9119 发送 prompt, 返回 LLM 回复.

    若 Hermes 不可用, 返回 "(Hermes unavailable: ...)" 错误字符串,
    调用方可据此 fallback 本地 :8080。
    """
    if not _started:
        start()
    req = _HermesRequest(session_name, prompt, timeout)
    _requests.put(req)  # type: ignore
    try:
        return req.result.get(timeout=timeout + 5)
    except queue.Empty:
        return "(Hermes timeout)"


# ─── 便利函数 ────────────────────────────────────────────────


def reflect(prompt: str, timeout: float = 180) -> str:
    """反思会话 (session: Ikaros-反思)."""
    return chat("Ikaros-反思", prompt, timeout)


def whisper(prompt: str, timeout: float = 120) -> str:
    """内心独白会话 (session: Ikaros-内心独白)."""
    return chat("Ikaros-内心独白", prompt, timeout)
