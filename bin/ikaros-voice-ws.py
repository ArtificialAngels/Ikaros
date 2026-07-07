"""ikaros-voice-ws.py — Live2D voice service on :7870/v1/voice/ws.

2026-07-05 (哥哥下指令): bridge :7860 已删 (commit b16c8f8),
:8080 给 Hermes Agent LLM + 记忆 extract 占用. Live2D voice
service 走新端口 :7870 接 cogno_5D + cloud_chat 真物 — 不重复发明.

消息协议 (从 App.vue:onmessage 抽):
  client → server:
    {"action":"start","session_id":"..."}        # 进 session
    {"action":"transcript","text":"哥哥说的话"}    # STT 真物 (client 上已做)
    {"action":"text","text":"..."}                # 纯文本输入
  server → client:
    {"type":"status","message":"thinking"}        # 状态提示
    {"type":"transcription","text":"..."}         # 复述输入
    {"type":"thinking"}                            # 进入思考
    {"type":"done","text":"reply"}                  # LLM reply 真物
    {"type":"error","message":"..."}               # 失败静默
    binary frame: TTS audio bytes                  # edge-tts 真物 WAV

KISS: 单 ws.server 真物, 不抽 cogno_engine, 不抽 chat_engine.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import time
import wave
from typing import Any

# 让 import 找到 cogno_5d + cloud_chat
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(_ROOT, "Ikaros-memory"),
         os.path.join(_ROOT, "bin", "ikaros-desktop-pet")):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("ikaros.voice-ws")

# 7860 端口 (FastAPI bridge) 在 7-04 quest "去桥架构" 删掉, 由
# bin/ikaros-voice-ws.py 接 cogno_5d + cloud_chat 真物 (commit b16c8f8).
# 8080 = Hermes Agent qwen3-8b (LLM / 记忆 extract 复用).
# 8587 = nomic-embed-text (记忆 独占).
# 8648 = EKKOLearnAI/hermes-web-ui 7-5 被哥哥卸了, 现让给 Ikaros-Live2D webview (本服务暂不 bind, 占着等 Live2D Tauri 用).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7870


def _load_cogno_5d():
    """cogno_5D 真物 (Ikaros-memory/cogno_5d.py 7-4 commit)."""
    try:
        from cogno_5d import enrich, enrich_reply, reset_context
        log.info("cogno_5d loaded")
        return enrich, enrich_reply, reset_context
    except Exception as e:
        log.warning("cogno_5d not available: %s", e)

        def _noop_enrich(text, history=None):
            return f"【认知5D】{text[:80]}"

        def _noop_reply(reply, *a, **kw):
            return {"cogno": "[noop]", "reply": reply}

        return _noop_enrich, _noop_reply, lambda: None


def _load_cloud_chat():
    """cloud_chat 真物 (bin/cloud_chat.py)."""
    try:
        from cloud_chat import cloud_chat as _cc
        log.info("cloud_chat loaded")
        return _cc
    except Exception as e:
        log.warning("cloud_chat not available: %s", e)
        return None


async def _tts_edge(text: str) -> bytes | None:
    """edge-tts 真物 (PIL 不需). 返 WAV bytes."""
    if not text.strip():
        return None
    try:
        import edge_tts  # real dep
        voice = "zh-CN-XiaoxiaoNeural"
        buf = io.BytesIO()
        communicate = edge_tts.Communicate(text, voice=voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue() or None
    except Exception as e:
        log.debug("edge-tts failed: %s", e)
        return None


async def _call_llm(prompt: str, cogno_prefix: str) -> str:
    """cloud_chat → :8080 qwen3-8b fallback (镜像 ikaros-repl 真物)."""
    cc = _load_cloud_chat()
    if cc is not None:
        try:
            res = cc(prompt, session_id="ikaros_live2d_ws")
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, dict):
                return res.get("reply") or res.get("content") or str(res)
            return str(res)
        except Exception as e:
            log.warning("cloud_chat call failed: %s", e)
    # Fallback: :8080 qwen3-8b via http.client (urllib absolute-URI bug workaround)
    import json, http.client
    body = json.dumps({
        "model": "qwen3-8b",
        "messages": [
            {"role": "system", "content": "You are Ikaros (人造天使). Reply briefly, 80-120 chars, Chinese."},
            {"role": "user", "content": cogno_prefix + "\n\n" + prompt},
        ],
        "max_tokens": 600,
        "temperature": 0.7,
    }).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=60)
    conn.request(
        "POST", "/v1/chat/completions", body=body,
        headers={"Content-Type": "application/json",
                 "Host": "127.0.0.1:8080",
                 "User-Agent": "ikaros-voice-ws/1.0",
                 "Accept-Encoding": "identity"},
    )
    resp = conn.getresponse()
    if resp.status != 200:
        conn.close()
        return f"(LLM fallback failed HTTP {resp.status})"
    d = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return d["choices"][0]["message"].get("content") or ""


async def _handle_text(ws, payload, enrich, enrich_reply, history):
    """client 发 {"action":"text","text":"..."} 真物链路."""
    user_text = (payload.get("text") or "").strip()
    if not user_text:
        await ws.send(json.dumps({"type": "error",
                                   "message": "empty text"}))
        return

    # 1) push transcription 复述
    await ws.send(json.dumps({"type": "transcription", "text": user_text}))

    # 2) push thinking
    await ws.send(json.dumps({"type": "thinking"}))

    # 3) cogno 5 维 enrich (前 250 chars 真物)
    cogno_prefix = enrich(user_text, history=history)
    if not cogno_prefix or not cogno_prefix.startswith("【认知5D】"):
        cogno_prefix = "【认知5D】 " + cogno_prefix

    # 4) LLM 真答
    t0 = time.time()
    try:
        reply = await _call_llm(user_text, cogno_prefix)
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        await ws.send(json.dumps({"type": "error",
                                   "message": f"LLM: {type(e).__name__}"}))
        return
    dt_ms = int((time.time() - t0) * 1000)

    # 5) cogno enrich_reply (Phase 5 返 dict)
    # 死链6 修复 (2026-07-07, quest 接手): enrich_reply 返回真实字段 dict,
    # 没有 "cogno" key, 旧代码 .get("cogno") 永远 [unknown]。改为拼真实字段。
    try:
        tag = enrich_reply(reply, user_text)
        if isinstance(tag, dict):
            bits = []
            emo = tag.get("emotion_user")
            if emo and emo != "未知":
                bits.append(f"emo:{emo}")
            topic = tag.get("topic")
            if topic:
                bits.append(f"topic:{topic}")
            turn = tag.get("context_turn")
            if turn is not None:
                bits.append(f"turn:{turn}")
            geo = tag.get("geo")
            if geo and geo != "未知":
                bits.append(f"geo:{geo}")
            cogno_meta = " ".join(bits) if bits else "cogno-ok"
        else:
            cogno_meta = str(tag)
    except Exception:
        cogno_meta = "[enrich_reply-failed]"

    # 6) push done + 真物 reply (L84 App.vue:case "done": showBubble(msg.text, 5000))
    await ws.send(json.dumps({
        "type": "done",
        "text": reply,
        "cogno": cogno_meta,
        "ms": dt_ms,
    }))

    # 7) TTS 真物 push (binary frame, App.vue L74 跳过 Blob)
    wav = await _tts_edge(reply)
    if wav:
        try:
            await ws.send(wav)
        except Exception as e:
            log.debug("TTS push failed: %s", e)


async def _serve(ws, path=None):
    """单 ws 真物 handler (App.vue ws.onmessage 抽)."""
    enrich, enrich_reply, reset_context = _load_cogno_5d()
    reset_context()
    history: list[dict] = []
    peer = "?"
    try:
        peer = ws.remote_address[0] if ws.remote_address else "?"
    except Exception:
        pass
    log.info("client connected: %s", peer)

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                # binary up 真物 (TTS audio upload from client? 我们不接 inbound audio,
                # 因为 STT 在 client 上, server 收 text)
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                log.debug("non-json message ignored")
                continue

            action = msg.get("action")
            if action == "start":
                # App.vue: ws.send({action: 'start', session_id})
                await ws.send(json.dumps({
                    "type": "status",
                    "message": f"session {msg.get('session_id', '?')} ready",
                }))
            elif action == "text":
                await _handle_text(ws, msg, enrich, enrich_reply, history)
            elif action == "transcript":
                # 同 text (STT 已在 client 上做了)
                msg["text"] = msg.get("text", "")
                await _handle_text(ws, msg, enrich, enrich_reply, history)
            else:
                await ws.send(json.dumps({
                    "type": "status",
                    "message": f"unknown action {action!r}",
                }))
    except Exception as e:
        log.warning("client disconnected (%s): %s", peer, e)


async def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    import websockets
    log.info("starting voice-ws on ws://%s:%d/v1/voice/ws", host, port)
    async with websockets.serve(_serve, host, port):
        log.info("voice-ws ready")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
