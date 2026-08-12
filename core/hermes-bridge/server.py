#!/usr/bin/env python3
"""Ikaros hermes-bridge — studio 式「0 侵入」包装层 (stdlib-only, 零外部依赖).

WHY
---
Ikaros 把 Hermes 当纯净下游跑 (runtime/hermes-agent 不改性源码). 上游 OpenAI-wire
``/v1/chat/completions`` 原生就流 ``delta.content`` + ``event: hermes.tool.progress``
(工具卡), 但**故意不接 reasoning** (gateway/platforms/api_server.py:4101 注释明示).
上游另一条原生端点 ``/api/sessions/{id}/chat/stream`` (Dashboard 自己用的) 则
原生发 reasoning (``tool.progress(_thinking)``) + content (``assistant.delta``) +
工具生命周期 (``tool.started/completed/failed``), 且接受 ``system_message`` 人格注入、
服务端持久化历史.

本桥让对话树继续调它熟悉的 OpenAI-wire ``/v1/chat/completions`` (零前端改动),
内部驱动 Hermes 原生 session-chat 端点, 用 ``translate.py`` 把原生 SSE 翻译成
对话树方言 (``hermes.reasoning`` / ``hermes.tool.progress`` / OpenAI chunks / ``[DONE]``).

结果: runtime/hermes-agent 工作树保持 100% 纯净; ikaros_v5 的思考/工具体验完全留在 Ikaros 自有代码.

ENDPOINTS
--------
  POST /v1/chat/completions   → 桥接到 Hermes session-chat + 翻译 (替代 api_server/conversation_loop 补丁)
  GET  /health                → 存活探针
  其它 /v1 /api 路径          → 反向代理到原生 Hermes gateway (:8642), 作 drop-in

CONFIG (env)
------------
  HERMES_BRIDGE_HOST   默认 127.0.0.1
  HERMES_BRIDGE_PORT   默认 8650
  HERMES_GATEWAY_URL   默认 http://127.0.0.1:8642  (纯净 Hermes gateway)
  HERMES_BRIDGE_API_KEY  用于 session 创建鉴权; 默认读 API_SERVER_KEY / HERMES_AGENT_KEY
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── 让 translate.py (同目录) 可导入 ──────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from translate import SSETranslator  # noqa: E402

__all__ = ["parse_messages", "derive_conv_id", "safe_session_id", "BridgeHandler"]

# ── 配置 ────────────────────────────────────────────────────────────────
HOST = os.environ.get("HERMES_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_BRIDGE_PORT", "8650"))
GATEWAY = os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642").rstrip("/")
_BRIDGE_KEY = (
    os.environ.get("HERMES_BRIDGE_API_KEY")
    or os.environ.get("API_SERVER_KEY")
    or os.environ.get("HERMES_AGENT_KEY")
    or ""
)

# 会话 id 安全字符集 + 长度上限 (上游 _is_path_unsafe + MAX_SESSION_HEADER_LEN 保守值)
_MAX_SID_LEN = 180


# ── 纯函数: 可单测 ───────────────────────────────────────────────────────
def _flatten_content(content: Any) -> str:
    """把 OpenAI content (str 或 multimodal list) 拍平成文本."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def parse_messages(messages: List[Dict[str, Any]]) -> Tuple[str, str, str]:
    """从 OpenAI messages 抽出 (system, first_user, last_user).

    - system: 所有 role==system 的内容拼接 (人格 / 树域上下文).
    - first_user / last_user: 首条 / 末条 user 文本 (用于稳定会话派生与最新输入).
    """
    system_parts: List[str] = []
    first_user = ""
    last_user = ""
    for m in messages:
        role = m.get("role", "")
        content = _flatten_content(m.get("content", ""))
        if role == "system":
            if content:
                system_parts.append(content)
        elif role == "user":
            if not first_user:
                first_user = content
            last_user = content
    return "\n".join(system_parts), first_user, last_user


def derive_conv_id(system: str, first_user: str) -> str:
    """无显式会话 id 时的回退: 首条 user + system 指纹 (同一会话内恒定)."""
    seed = (system + "\n<<<>>>\n" + first_user).encode("utf-8")
    return "fp_" + hashlib.sha256(seed).hexdigest()[:24]


def safe_session_id(conv_id: str) -> str:
    """把任意会话 id 消毒成 Hermes 允许的 session id (alnum/_/-, 限长)."""
    keep = [c for c in conv_id if c.isalnum() or c in ("_", "-")]
    sid = "ikaros-" + "".join(keep)[: _MAX_SID_LEN - len("ikaros-")]
    return sid or ("ikaros-" + str(int(time.time())))


def dispatch_native(evt: str, raw: str, translator: SSETranslator) -> List[bytes]:
    """纯函数: 把一帧原生 (event, data-json) 交给翻译器, 返回翻译后 wire 帧.

    从 stream_session_chat 抽出以便离线单测 (不依赖真实网络).
    """
    try:
        payload_obj = json.loads(raw)
    except Exception:
        return []
    return translator.feed(evt or "", payload_obj)


def _auth_header() -> Dict[str, str]:
    if _BRIDGE_KEY:
        return {"Authorization": f"Bearer {_BRIDGE_KEY}"}
    return {}


# ── 会话确保 ─────────────────────────────────────────────────────────────
# ⚠️ 上游会话行的 ``model`` 字段是**持久化的钉死值**: 创建时不传 model 会落到
# 兜底名 "hermes-agent" (api_server._handle_create_session: ``model =
# body.get("model") or self._model_name``), 之后每次 session-chat 即使请求带
# model=hermes 命中 model_routes, ``_create_agent`` 里 session-persisted model
# (hermes-agent) 也优先于 route → 实际执行 opencode-go/hermes-agent → 401 →
# 空响应 → 48920 降级 (2026-08-05 根因定位).
# 因此: 创建会话必须显式传 model (与 chat/stream 请求一致), 且对存量
# "hermes-agent" 坏会话 DELETE 重建 (上游支持 DELETE /api/sessions/{id}).
def _delete_session(sid: str) -> None:
    """DELETE /api/sessions/{sid} — 自愈坏会话用 (静默容错)."""
    try:
        req = urllib.request.Request(
            f"{GATEWAY}/api/sessions/{sid}",
            headers=_auth_header(),
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as e:
        sys.stderr.write(f"[bridge] delete session {sid} failed: {e}\n")


def ensure_session(sid: str, system: str, model: str = "hermes") -> None:
    """GET 存在则校验/自愈, 否则 POST 创建 (带 model, 防兜底名钉死).

    - GET 200 且 session.model 是兜底名 "hermes-agent" → DELETE 重建 (对话历史由
      V5 store 持有, 会话行重建无碍; 否则该会话后续每次请求都被 hermes-agent
      抢先 → 401).
    - 创建失败 (409 已存在 / 503 db 不可用) 静默容错.
    """
    get_url = f"{GATEWAY}/api/sessions/{sid}"
    try:
        req = urllib.request.Request(get_url, headers=_auth_header())
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                raw = r.read().decode("utf-8", "replace")
                sess_model = ""
                try:
                    sess_model = (json.loads(raw).get("session") or {}).get("model") or ""
                except Exception:
                    pass
                if sess_model == "hermes-agent":
                    # 坏会话: 上游持久化模型会压过 model_routes route → 删掉重建
                    sys.stderr.write(
                        f"[bridge] session {sid} pinned to fallback model "
                        f"'hermes-agent'; deleting to rebuild with model={model}\n"
                    )
                    _delete_session(sid)
                return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            # 403/401 等鉴权问题: 创建同样会失败, 交给下方; 这里不打断.
            pass
    except Exception:
        pass

    create = {"id": sid, "model": model}
    if system:
        create["system_prompt"] = system
    create_url = f"{GATEWAY}/api/sessions"
    try:
        req = urllib.request.Request(
            create_url,
            data=json.dumps(create).encode("utf-8"),
            headers={**_auth_header(), "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return  # 并发创建, 已存在
    except Exception as e:
        # session 创建失败: chat/stream 会 404; 让上行错误透出, 不在此吞掉.
        sys.stderr.write(f"[bridge] ensure_session failed for {sid}: {e}\n")


# ── 原生 SSE → 翻译帧 生成器 ──────────────────────────────────────────────
def stream_session_chat(
    sid: str,
    message: str,
    system: str,
    model: str,
    translator: SSETranslator,
) -> Iterable[bytes]:
    """调 Hermes 原生 session-chat 端点, 逐帧翻译为对话树方言.

    原生事件词汇 (gateway/platforms/api_server.py:_handle_session_chat_stream):
      assistant.delta       → {"delta": "<text>"}                 content
      tool.progress         → {"tool_name":"_thinking","delta":}  REASONING
      tool.started          → {"tool_name","preview","args"}      工具 running
      tool.completed        → {"tool_name","preview","args"}      工具 completed
      tool.failed           → {"tool_name","preview","args"}      工具 failed
      run.started/message.started/run.completed/done → 控制事件 (translator 丢弃)
    """
    url = f"{GATEWAY}/api/sessions/{sid}/chat/stream"
    payload: Dict[str, Any] = {"message": message, "stream": True, "model": model}
    if system:
        payload["system_message"] = system
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**_auth_header(), "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    evt: Optional[str] = None
    data_lines: List[str] = []
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    this_evt = evt
                    evt = None
                    for frame in dispatch_native(this_evt or "", raw, translator):
                        yield frame
                continue
            if line.startswith("event:"):
                evt = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())


# ── HTTP handler ──────────────────────────────────────────────────────────
class BridgeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ikaros-hermes-bridge/0.1"

    # 静音默认访问日志, 仅错误走 stderr
    def log_message(self, fmt: str, *args: Any) -> None:
        if " 200 " not in (fmt % args if args else fmt):
            sys.stderr.write("[bridge] " + (fmt % args) + "\n")

    # ---- helpers ----
    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, frame: bytes) -> None:
        self.wfile.write(f"{len(frame):x}\r\n".encode("utf-8") + frame + b"\r\n")
        self.wfile.flush()

    def _write_chunk_end(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    # ---- routes ----
    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") in ("/health", ""):
            self._send_json({"status": "ok", "gateway": GATEWAY})
            return
        self._proxy_pass("GET")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/chat/completions":
            self._handle_chat_completions()
            return
        self._proxy_pass("POST")

    # ---- chat/completions 桥接 ----
    def _handle_chat_completions(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._send_json({"error": {"message": f"bad request: {e}"}}, status=400)
            return

        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            self._send_json({"error": {"message": "missing messages"}}, status=400)
            return

        system, first_user, last_user = parse_messages(messages)
        if not last_user:
            self._send_json({"error": {"message": "no user message"}}, status=400)
            return

        conv_id = self.headers.get("X-Ikaros-Conv-Id") or derive_conv_id(system, first_user)
        sid = safe_session_id(conv_id)
        model = body.get("model") or "hermes"

        try:
            ensure_session(sid, system, model)
        except Exception as e:
            sys.stderr.write(f"[bridge] ensure_session error: {e}\n")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Hermes-Session-Id", sid)
        self.end_headers()

        translator = SSETranslator()
        try:
            for frame in stream_session_chat(sid, last_user, system, model, translator):
                self._write_chunk(frame)
        except urllib.error.HTTPError as e:
            # 上游 4xx/5xx：透出错误帧（不再静默空 [DONE]，让 48920 诊断可见）
            msg = f"gateway upstream HTTP {e.code}: {e.reason}"
            sys.stderr.write(f"[bridge] {msg}\n")
            payload = json.dumps(
                {"error": msg, "type": "upstream_http_error", "code": e.code}
            ).encode("utf-8")
            self._write_chunk(b"event: error\r\ndata: " + payload + b"\r\n\r\n")
        except Exception as e:
            msg = f"gateway upstream error: {type(e).__name__}: {e}"
            sys.stderr.write(f"[bridge] {msg}\n")
            payload = json.dumps(
                {"error": msg, "type": "upstream_error"}
            ).encode("utf-8")
            self._write_chunk(b"event: error\r\ndata: " + payload + b"\r\n\r\n")
        finally:
            for frame in translator.finish():
                self._write_chunk(frame)
            self._write_chunk_end()
            self.close_connection = True

    # ---- 通用反向代理到原生 gateway (drop-in 兜底) ----
    def _proxy_pass(self, method: str) -> None:
        url = GATEWAY + self.path
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            data = self.rfile.read(length) if length else None
        except Exception:
            data = None
        fwd = {k: v for k, v in self.headers.items()
               if k.lower() not in ("host", "content-length", "transfer-encoding")}
        try:
            req = urllib.request.Request(url, data=data, headers=fwd, method=method)
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in ("transfer-encoding", "connection"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            try:
                payload = e.read()
            except Exception:
                payload = b""
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except Exception as e:
            self._send_json({"error": {"message": f"bridge proxy error: {e}"}}, status=502)


def main() -> None:
    server = http.server.ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    sys.stderr.write(f"[bridge] listening on http://{HOST}:{PORT} -> gateway {GATEWAY}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
