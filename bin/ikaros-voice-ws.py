"""ikaros-voice-ws.py — Live2D voice service on :7870/v1/voice/ws.

2026-07-05 (哥哥下指令): bridge :7860 已删 (commit b16c8f8),
:8080 给 Hermes Agent LLM + 记忆 extract 占用. Live2D voice
service 走新端口 :7870 接 cogno_5D + cloud_chat 真物 — 不重复发明.

消息协议 (从 App.vue:onmessage 抽):
  client → server:
    {"action":"start","session_id":"..."}        # 进 session
    {"action":"transcript","text":"哥哥说的话"}    # STT 真物 (client 上已做)
    {"action":"text","text":"..."}                # 纯文本输入
    {"action":"look"}                              # 让 pet 看屏幕 (Layer3: 截图+视觉LLM, 配置门控)
  server → client:
    {"type":"status","message":"thinking"}        # 状态提示
    {"type":"transcription","text":"..."}         # 复述输入
    {"type":"thinking"}                            # 进入思考
    {"type":"done","text":"reply"}                  # LLM reply 真物
    {"type":"activity","state":...,"phrase":...}   # 前台活动变化推送 (N.E.K.O 主动搭话触发)
    {"type":"screen","desc":"..."}                 # look 动作: 屏幕视觉描述 (未配置视觉模型时 desc=null)
    {"type":"error","message":"..."}               # 失败静默
    binary frame: TTS audio bytes                  # Hermes Agent 内置 TTS (mp3) 或 edge-tts 兜底

KISS: 单 ws.server 真物, 不抽 cogno_engine, 不抽 chat_engine.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
import wave
from typing import Any

# 让 import 找到 cogno_5d + cloud_chat + ikaros_monitor
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(_ROOT, "Ikaros-memory"),
         os.path.join(_ROOT, "bin", "ikaros-desktop-pet"),
         os.path.join(_ROOT, "bin")):
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

# 已连接 pet webview 集合 (用于活动状态变化广播)
_CLIENTS: set = set()


def _activity_payload(snap: dict) -> dict:
    """把 monitor 快照转成推送给 pet 的 activity 消息 (隐私安全).

    private 状态只给中性句, 绝不附带进程名/标题/路径等细节。
    """
    state = snap.get("activity_state", "idle")
    if state == "private":
        phrase = "哥哥在使用一个隐私应用"
        canonical = None
        category = "private"
    else:
        try:
            from ikaros_monitor import activity_phrase
            phrase = activity_phrase(snap) or ""
        except Exception:
            phrase = ""
        canonical = snap.get("canonical")
        category = snap.get("category")
    return {
        "type": "activity",
        "state": state,
        "phrase": phrase,
        "canonical": canonical,
        "category": category,
        "idle_seconds": snap.get("idle_seconds"),
        "cpu_avg_30s": snap.get("cpu_avg_30s"),
        "ts": snap.get("timestamp"),
    }


async def _activity_broadcaster():
    """每 5s 检查前台活动, 状态变化时推送给所有已连 pet webview。

    对应 N.E.K.O 的 UserActivityTracker 主动搭话触发: 用户切换应用 /
    进入游戏 / 离开键盘时, pet 立刻感知并可能主动搭话。
    """
    try:
        from ikaros_monitor import get_monitor
    except Exception as e:
        log.warning("activity broadcaster: ikaros_monitor 不可用: %s", e)
        return
    mon = get_monitor()
    mon.start()
    last_state = None
    while True:
        await asyncio.sleep(5)
        try:
            if not _CLIENTS:
                last_state = None  # 客户端全掉线, 重连后重推
                continue
            snap = mon.snapshot()
            if not snap.get("os_signals_available"):
                continue
            state = snap.get("activity_state")
            if state == last_state:
                continue
            last_state = state
            # V5 #3: 事件驱动觉醒 — 活动状态变化触发内心独白
            try:
                from v5.think import on_activity_change
                on_activity_change(
                    state,
                    activity_phrase=snap.get("phrase", ""),
                    category=snap.get("category", ""),
                )
            except Exception:
                pass
            payload = _activity_payload(snap)
            dead = []
            for ws in list(_CLIENTS):
                try:
                    await ws.send(json.dumps(payload))
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _CLIENTS.discard(ws)
            log.debug("activity broadcast -> %s (%d clients)", state, len(_CLIENTS))
        except Exception as e:
            log.debug("activity broadcaster tick failed: %s", e)


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
    """edge-tts 直接异步流式合成 (无 subprocess 开销).

    返回 MP3 bytes。这是当前最快的 TTS 路径，直接在当前进程调 edge_tts.
    """
    if not text.strip():
        return None
    try:
        import edge_tts
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


async def _tts_sentence_stream(reply: str, ws) -> int:
    """句级流水线 TTS: 按标点切句 → 逐句合成 → 逐句推送给前端。

    延迟优化: 不等整段回复, 第一句识别完成后立即开始合成。
    前端需要支持逐句接收 (type=tts_chunk, index=N, total=M)。

    Returns: 成功合成的句子数。
    """
    if not reply.strip():
        return 0
    # 切句
    import re
    sentences = re.split(r'(?<=[。！？；…\.\!\?\;\n])', reply)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        sentences = [reply]
    if len(sentences) == 1:
        # 单句: 直接合成
        audio = await _tts_edge(reply)
        if audio:
            await ws.send(audio)
            return 1
        return 0

    # 多句: 逐句合成 + 逐句推送
    # 用 asyncio.gather 并发合成 (最多 2 句并发, 避免被微软限流)
    import asyncio as _asyncio
    sem = _asyncio.Semaphore(2)
    sent_count = 0

    async def _synth_one(idx: int, sent: str) -> tuple[int, bytes | None]:
        async with sem:
            return idx, await _tts_edge(sent)

    tasks = [_synth_one(i, s) for i, s in enumerate(sentences)]
    for coro in _asyncio.as_completed(tasks):
        idx, audio = await coro
        if audio:
            try:
                await ws.send(audio)
                sent_count += 1
            except Exception:
                break
    return sent_count


async def _call_llm(prompt: str, cogno_prefix: str, on_delta=None) -> str:
    """cloud_chat → :8080 qwen3-8b fallback (镜像 ikaros-repl 真物).

    on_delta: 流式回调 (逐 token). 传了就启用 cloud_chat 流式首字上屏。
    """
    cc = _load_cloud_chat()
    if cc is not None:
        try:
            res = cc(prompt, session_id="ikaros_live2d_ws", on_delta=on_delta)
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


async def _handle_text(ws, payload, enrich, enrich_reply, history,
                      state=None, my_id=0, emotion="", event=""):
    """client 发 {"action":"text"/"transcript"/"end_utterance"} 真物链路。

    state/my_id 用于 barge-in: 若本 utterance 已被更新的会话取代,
    则丢弃过期回复, 不再下发 done/TTS。emotion/event 为 SenseVoice
    检测到的用户语气/事件标签, 注入 LLM 上下文。
    """
    user_text = (payload.get("text") or "").strip()
    if not user_text:
        await ws.send(json.dumps({"type": "error",
                                   "message": "empty text"}))
        return
    # 已被打断 → 丢弃过期回复
    if state is not None and state.get("utt_id", 0) != my_id:
        log.debug("utterance %s superseded before STT, drop", my_id)
        return

    # 1) push transcription 复述
    await ws.send(json.dumps({"type": "transcription", "text": user_text}))

    # 2) push thinking
    await ws.send(json.dumps({"type": "thinking"}))

    # 3) cogno 5 维 enrich (前 250 chars 真物) + 用户语气/事件上下文
    cogno_prefix = enrich(user_text, history=history)
    if not cogno_prefix or not cogno_prefix.startswith("【认知5D】"):
        cogno_prefix = "【认知5D】 " + cogno_prefix
    if emotion or event:
        bits = []
        if emotion:
            bits.append(f"用户语气:{emotion}")
        if event:
            bits.append(f"事件:{event}")
        cogno_prefix += "\n[" + " | ".join(bits) + "]"

    # 4) LLM 真答 (流式: 首 token 即推 delta, 气泡逐字上屏)
    t0 = time.time()
    _stream_parts: list[str] = []
    _first_delta_sent = False

    async def _on_delta(chunk: str):
        nonlocal _first_delta_sent
        if state is not None and state.get("utt_id", 0) != my_id:
            return  # 已被打断, 丢弃后续增量
        _stream_parts.append(chunk)
        is_first = not _first_delta_sent
        _first_delta_sent = True
        await ws.send(json.dumps({
            "type": "delta",
            "text": chunk,
            "is_first": is_first,
        }))

    try:
        reply = await _call_llm(user_text, cogno_prefix, on_delta=_on_delta)
    except Exception as e:
        log.warning("LLM call failed: %s", e)
        await ws.send(json.dumps({"type": "error",
                                   "message": f"LLM: {type(e).__name__}"}))
        return
    dt_ms = int((time.time() - t0) * 1000)

    # 流式未触发 (无 on_delta 支持 / 降级路径) → 用完整 reply 兜底推首块
    if not _first_delta_sent and reply:
        await ws.send(json.dumps({
            "type": "delta", "text": reply, "is_first": True,
        }))

    # 又被打断? (LLM 调用期间可能来了新 utterance)
    if state is not None and state.get("utt_id", 0) != my_id:
        log.debug("utterance %s superseded after LLM, drop", my_id)
        return

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
        "emotion": emotion,
        "event": event,
    }))

    # 7) TTS: 句级流水线 (不等整段, 第一句识别即开始合成)
    asyncio.ensure_future(_tts_sentence_stream(reply, ws))


async def _handle_look(ws, enrich, enrich_reply, history, state=None, my_id=0):
    """Layer 3: 用户让 pet 看屏幕 (截图 + 视觉 LLM, 配置门控).

    仅当用户显式触发 (前端发 {action:"look"}) 时才截图, 隐私安全。
    未配置 IKAROS_VISION_* 时返回提示, 不报错。
    """
    try:
        from ikaros_monitor import get_monitor
        mon = get_monitor()
        loop = asyncio.get_running_loop()
        # refresh_screen_desc 含网络调用 (视觉 LLM), 丢到线程避免阻塞事件循环
        desc = await loop.run_in_executor(None, mon.refresh_screen_desc)
    except Exception as e:
        log.warning("screen capture failed: %s", e)
        desc = None

    if not desc:
        await ws.send(json.dumps({
            "type": "screen",
            "desc": None,
            "message": "视觉模型未配置或截图失败 (需设置 IKAROS_VISION_MODEL/BASE_URL/API_KEY)",
        }))
        return

    # 推回屏幕描述 (pet 可展示 / 用表情反应)
    await ws.send(json.dumps({"type": "screen", "desc": desc}))

    # 让 LLM 基于看到的画面接一句话
    if state is not None and state.get("utt_id", 0) != my_id:
        return
    try:
        cogno_prefix = enrich("（看到屏幕）", history=history)
        if not cogno_prefix or not cogno_prefix.startswith("【认知5D】"):
            cogno_prefix = "【认知5D】 " + cogno_prefix
        cogno_prefix += "\n[屏幕内容] " + desc
        reply = await _call_llm("我刚才看了一眼你的屏幕，说说你看到了什么。", cogno_prefix)
        if state is not None and state.get("utt_id", 0) != my_id:
            return
        await ws.send(json.dumps({
            "type": "done", "text": reply, "cogno": "screen-look", "ms": 0,
        }))
        audio = await _tts_edge(reply)
        if audio:
            try:
                await ws.send(audio)
            except Exception:
                pass
    except Exception as e:
        log.warning("look LLM failed: %s", e)


_VOSK_MODEL = None  # 惰性加载的本地离线 STT 模型


def _get_vosk_model():
    """惰性加载本地 vosk 中文模型 (离线 STT, 语音不出本机)。

    模型目录默认 E:/Ikaros/data/models/vosk-model-small-cn-0.15，
    可用环境变量 IKAROS_VOSK_MODEL 覆盖。缺失则返回 None (降级)。
    """
    global _VOSK_MODEL
    if _VOSK_MODEL is not None:
        return _VOSK_MODEL
    try:
        from vosk import Model
    except Exception as e:
        log.warning("vosk not installed: %s", e)
        return None
    model_dir = os.environ.get("IKAROS_VOSK_MODEL")
    if not model_dir:
        model_dir = os.path.join(
            os.environ.get("IKAROS_DATA_MODELS", os.path.join(_ROOT, "data", "models")),
            "vosk-model-small-cn-0.15",
        )
    if not os.path.isdir(model_dir):
        log.warning("vosk model not found at %s", model_dir)
        return None
    try:
        _VOSK_MODEL = Model(model_dir)
        log.info("vosk model loaded: %s", model_dir)
    except Exception as e:
        log.warning("vosk model load failed: %s", e)
        return None
    return _VOSK_MODEL


_SENSEVOICE = None  # 惰性加载的高精度离线 STT (sherpa-onnx SenseVoice)

# SenseVoice 输出 text 自带语言/情绪/事件标签, 如 "<|zh|><|NEUTRAL|><|Speech|>你好"
# (sherpa-onnx 不会自动剥离, 只把 emotion/event 作为独立字段; text 仍含前缀标签).
# 这些标签若直接进 chat 会污染 LLM 上下文 → 终句前必须剥掉 (A1 修复丢分④).
_SV_TAG_RE = re.compile(r"<\|[^|]*\|>")


def _strip_sv_tags(text: str) -> str:
    """剥掉 SenseVoice 的 <|zh|><|NEUTRAL|><|Speech|> 类标签并规整空白."""
    if not text:
        return ""
    text = _SV_TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_sensevoice():
    """惰性加载本地 sherpa-onnx SenseVoice 高精度离线 STT。

    中文多语种, 自带情绪/事件标签 + ITN 逆文本规整, 精度远高于 vosk
    small-cn。模型目录默认 E:/Ikaros/data/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue
    (model.onnx fp32 + model.int8.onnx int8 + tokens.txt), 可用 IKAROS_SENSEVOICE_DIR 覆盖。
    默认优先 fp32 (model.onnx, 精度最高, 但多占 ~2GB RAM), 缺失时回退 int8。
    缺失/不可用时返回 None (降级到 vosk) —— 借鉴自 exProject/MewCo-AI/asr.py。
    """
    global _SENSEVOICE
    if _SENSEVOICE is not None:
        return _SENSEVOICE
    try:
        import sherpa_onnx  # noqa: F401
    except Exception as e:
        log.debug("sherpa_onnx not installed: %s", e)
        return None
    if not hasattr(sherpa_onnx.OfflineRecognizer, "from_sense_voice"):
        log.debug("sherpa_onnx lacks from_sense_voice")
        return None
    model_dir = os.environ.get("IKAROS_SENSEVOICE_DIR")
    if not model_dir:
        model_dir = os.path.join(
            os.environ.get("IKAROS_DATA_MODELS", os.path.join(_ROOT, "data", "models")),
            "sherpa-onnx-sense-voice-zh-en-ja-ko-yue",
        )
    if not os.path.isdir(model_dir):
        log.info("SenseVoice model not found at %s (降级 vosk)", model_dir)
        return None
    # C1: 默认优先 fp32 (精度高), 缺失时回退 int8 (省内存)
    model_file = os.path.join(model_dir, "model.onnx")
    if not os.path.isfile(model_file):
        model_file = os.path.join(model_dir, "model.int8.onnx")
    tokens = os.path.join(model_dir, "tokens.txt")
    if not (os.path.isfile(model_file) and os.path.isfile(tokens)):
        log.warning("SenseVoice model files incomplete in %s", model_dir)
        return None
    try:
        _SENSEVOICE = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model_file,
            tokens=tokens,
            use_itn=True,
            num_threads=max(1, (os.cpu_count() or 2) - 1),
        )
        log.info("SenseVoice loaded: %s", model_dir)
    except Exception as e:
        log.warning("SenseVoice load failed: %s", e)
        return None
    return _SENSEVOICE


def _sensevoice_recognize(pcm: bytes):
    """用 SenseVoice 对一段 16k mono Int16 PCM 做识别。

    返回 (text, emotion, event)。失败时返回 ("", "", "")。
    emotion/event 为 SenseVoice 内置标签(如 HAPPY / Laughter)。
    """
    sv = _get_sensevoice()
    if sv is None or not pcm:
        return "", "", ""
    try:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = sv.create_stream()
        stream.accept_waveform(16000, audio)
        sv.decode_stream(stream)
        res = json.loads(str(stream.result))
        # A1: 剥掉 <|zh|><|NEUTRAL|><|Speech|> 等标签, 避免污染 chat 文本
        text = _strip_sv_tags(res.get("text") or "")
        # emotion/event 同理做防御性清洗 (可能带 <|...|> 或含多个标签)
        emotion = (res.get("emotion") or "").replace("<|", "").replace("|>", "").strip()
        event = (res.get("event") or "").replace("<|", "").replace("|>", "").strip()
        return text, emotion, event
    except Exception as e:
        log.debug("SenseVoice recognize failed: %s", e)
        return "", "", ""


# C2: 可选 Whisper 后端 (sherpa-onnx Whisper). 仅当模型目录存在时启用,
# 否则保持 None/False 不阻塞, 回退 SenseVoice. 本地高精度, 中文口音/专有名词
# 更稳, 但模型较大 (~1.5GB), 需自行下载到 IKAROS_WHISPER_DIR 或
# data/models/sherpa-onnx-whisper-* 目录. 用 False 哨兵缓存"探测过且不存在".
_WHISPER = None


def _get_whisper():
    """惰性加载本地 sherpa-onnx Whisper 高精度离线 STT (可选).

    优先级高于 SenseVoice (口音/专有名词更稳), 但模型重 (~1.5GB) 且需自行
    下载, 故仅当模型目录存在时启用; 否则返回 False 哨兵, 不重复探测。
    """
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER if _WHISPER is not False else None
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        _WHISPER = False
        return None
    if not hasattr(sherpa_onnx.OfflineRecognizer, "from_whisper"):
        _WHISPER = False
        return None
    model_dir = os.environ.get("IKAROS_WHISPER_DIR")
    if not model_dir:
        import glob
        base = os.environ.get(
            "IKAROS_DATA_MODELS", os.path.join(_ROOT, "data", "models")
        )
        cand = sorted(glob.glob(os.path.join(base, "sherpa-onnx-whisper-*")))
        model_dir = cand[0] if cand else None
    if not model_dir or not os.path.isdir(model_dir):
        _WHISPER = False
        return None
    enc = os.path.join(model_dir, "encoder.int8.onnx")
    if not os.path.isfile(enc):
        enc = os.path.join(model_dir, "encoder.onnx")
    dec = os.path.join(model_dir, "decoder.int8.onnx")
    if not os.path.isfile(dec):
        dec = os.path.join(model_dir, "decoder.onnx")
    tokens = os.path.join(model_dir, "tokens.txt")
    if not (os.path.isfile(enc) and os.path.isfile(dec) and os.path.isfile(tokens)):
        log.debug("Whisper model incomplete in %s (跳过)", model_dir)
        _WHISPER = False
        return None
    try:
        _WHISPER = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=enc,
            decoder=dec,
            tokens=tokens,
            language="zh",
            num_threads=max(1, (os.cpu_count() or 2) - 1),
        )
        log.info("Whisper loaded: %s", model_dir)
    except Exception as e:
        log.warning("Whisper load failed: %s", e)
        _WHISPER = False
        return None
    return _WHISPER


def _whisper_recognize(pcm: bytes) -> str:
    """用 Whisper 对一段 16k mono Int16 PCM 做识别. 失败/未启用返回 ''."""
    wh = _get_whisper()
    if wh is None or not pcm:
        return ""
    try:
        import numpy as np
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = wh.create_stream()
        stream.accept_waveform(16000, audio)
        wh.decode_stream(stream)
        res = json.loads(str(stream.result))
        return _strip_sv_tags(res.get("text") or "")
    except Exception as e:
        log.debug("Whisper recognize failed: %s", e)
        return ""


async def _serve(ws, path=None):
    """单 ws 真物 handler (App.vue ws.onmessage 抽).

    音频链路: 前端 getUserMedia → 16k mono Int16 PCM → 二进制帧 → 本函数
    用本地 vosk 流式识别 → {type:partial} 实时回显 → 前端 VAD 发
    {action:end_utterance} → 取 final → 走 _handle_text (LLM+TTS)。
    """
    enrich, enrich_reply, reset_context = _load_cogno_5d()
    reset_context()
    history: list[dict] = []
    peer = "?"
    try:
        peer = ws.remote_address[0] if ws.remote_address else "?"
    except Exception:
        pass
    log.info("client connected: %s", peer)
    _CLIENTS.add(ws)

    # 每连接状态: vosk 流式识别器 + 原始 PCM 缓冲(SenseVoice 终句精修) +
    # utterance id (barge-in 过期判定) + inflight (当前在生成/播放的 utt id)
    st = {
        "rec": None,            # 当前会话的 vosk KaldiRecognizer
        "pcm": bytearray(),     # 累积的 16k mono Int16 PCM
        "utt_id": 0,
        "inflight": None,
    }

    def _new_utt():
        st["utt_id"] += 1
        my_id = st["utt_id"]
        # 不再强制打断旧 TTS: 前端按队列顺序逐个播完,
        # 新音频自然排在旧音频之后 (满足"按顺序逐个播放完成")。
        st["inflight"] = my_id
        return my_id

    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                # 二进制帧 = 前端采集的 16k mono Int16 PCM (STT 在 server 本地做)
                # A2 修复丢分③: 音频预处理(去DC+RMS归一+噪声门)只用于 vosk 流式
                # partial 回声; SenseVoice 终句识别用【原始 PCM】, 避免预处理把词间
                # 静音放大/噪声门泄漏/短帧不连续等畸变带入最终识别信号.
                prep = _get_audio_prep()
                processed = prep.process(raw) if prep else raw
                model = _get_vosk_model()
                if model is not None:
                    if st["rec"] is None:
                        from vosk import KaldiRecognizer
                        st["rec"] = KaldiRecognizer(model, 16000)
                    # 同步调用: KaldiRecognizer 非线程安全, 音频帧小(≈256ms)不阻塞循环
                    try:
                        st["rec"].AcceptWaveform(processed)
                        partial = json.loads(st["rec"].PartialResult()).get("partial", "")
                    except Exception as e:
                        log.debug("AcceptWaveform failed: %s", e)
                        partial = ""
                    if partial:
                        await ws.send(json.dumps({"type": "partial", "text": partial}))
                # 累积【原始】PCM, 供 SenseVoice 终句高精度精修 (不经预处理)
                st["pcm"].extend(raw)
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
                # 推送当前活动状态给 pet (表情/主动搭话依据)
                try:
                    from ikaros_monitor import get_monitor
                    snap = get_monitor().snapshot()
                    if snap.get("os_signals_available"):
                        await ws.send(json.dumps(_activity_payload(snap)))
                except Exception:
                    pass
                sv = _get_sensevoice()
                wh = _get_whisper()
                if _get_vosk_model() is None and sv is None and wh is None:
                    await ws.send(json.dumps({
                        "type": "stt_status",
                        "status": "unavailable",
                        "message": "本地语音识别未就绪",
                    }))
                elif wh is not None:
                    await ws.send(json.dumps({
                        "type": "stt_status",
                        "status": "ready",
                        "message": "高精度语音识别已就绪 (Whisper)",
                    }))
                elif sv is not None:
                    await ws.send(json.dumps({
                        "type": "stt_status",
                        "status": "ready",
                        "message": "高精度语音识别已就绪 (SenseVoice)",
                    }))
                else:
                    await ws.send(json.dumps({
                        "type": "stt_status",
                        "status": "ready",
                        "message": "本地语音识别已就绪 (vosk)",
                    }))
            elif action in ("text", "transcript"):
                # 纯文本 / 备用: STT 已在 client 上做
                if action == "transcript":
                    msg["text"] = msg.get("text", "")
                my_id = _new_utt()
                await _handle_text(
                    ws, msg, enrich, enrich_reply, history,
                    state=st, my_id=my_id,
                )
            elif action == "look":
                # Layer 3: 用户让 pet 看屏幕 (截图 + 视觉 LLM, 配置门控)
                my_id = _new_utt()
                await _handle_look(
                    ws, enrich, enrich_reply, history,
                    state=st, my_id=my_id,
                )
            elif action == "end_utterance":
                # 前端 VAD 判定一句话结束 → 高精度 final 识别 + 情绪/事件标签
                # 重置音频预处理器噪声门状态 (每句语音结束后)
                _ap = _get_audio_prep()
                if _ap:
                    _ap.reset()
                my_id = _new_utt()
                vosk_final = ""
                if st["rec"] is not None:
                    try:
                        vosk_final = json.loads(
                            st["rec"].FinalResult()
                        ).get("text", "").strip()
                    except Exception:
                        vosk_final = ""
                    st["rec"] = None
                # C2: 优先级 Whisper > SenseVoice > vosk final
                text, emotion, event = "", "", ""
                wh_text = _whisper_recognize(bytes(st["pcm"]))
                if wh_text:
                    text = wh_text
                else:
                    text, emotion, event = _sensevoice_recognize(bytes(st["pcm"]))
                st["pcm"] = bytearray()
                final_text = text if text else vosk_final
                if not final_text:
                    continue
                if emotion or event:
                    await ws.send(json.dumps({
                        "type": "emotion", "emotion": emotion, "event": event,
                    }))
                await _handle_text(
                    ws, {"text": final_text}, enrich, enrich_reply, history,
                    state=st, my_id=my_id, emotion=emotion, event=event,
                )
            else:
                await ws.send(json.dumps({
                    "type": "status",
                    "message": f"unknown action {action!r}",
                }))
    except Exception as e:
        log.warning("client disconnected (%s): %s", peer, e)
    finally:
        _CLIENTS.discard(ws)


# 音频预处理 (移植自 N.E.K.O 思路)
_AUDIO_PREP = None


def _get_audio_prep():
    global _AUDIO_PREP
    if _AUDIO_PREP is None:
        try:
            from bin.audio_preprocessor import AudioPreprocessor
            _AUDIO_PREP = AudioPreprocessor(sample_rate=16000)
            log.info("AudioPreprocessor ready (DC removal + RMS norm + noise gate + limiter)")
        except Exception as e:
            log.warning("AudioPreprocessor not available: %s", e)
            _AUDIO_PREP = False  # sentinel
    return _AUDIO_PREP if _AUDIO_PREP is not False else None


async def main(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    import websockets
    # 启动本地活动监测 (前台进程/空闲/CPU/截图), 供 cogno_5d + pet 使用
    try:
        from ikaros_monitor import get_monitor
        get_monitor().start()
        log.info("SystemMonitor started")
    except Exception as e:
        log.warning("SystemMonitor 启动失败 (降级): %s", e)
    # 活动状态变化广播给已连 pet webview (N.E.K.O 主动搭话触发)
    asyncio.ensure_future(_activity_broadcaster())

    # 预热: 后台加载 SenseVoice + Vosk 模型 (避免首次对话等待)
    def _warm_stt():
        t0 = time.time()
        sv = _get_sensevoice()
        if sv is not None:
            # 预创建 stream + 跑一次空推理预热 sherpa-onnx 内部图
            try:
                import numpy as np
                dummy = np.zeros(16000, dtype=np.float32)  # 1s 静音
                stream = sv.create_stream()
                stream.accept_waveform(16000, dummy)
                sv.decode_stream(stream)
                log.info("SenseVoice warmed up (%.1fs)", time.time() - t0)
            except Exception:
                log.info("SenseVoice initialized (%.1fs)", time.time() - t0)
        vosk_m = _get_vosk_model()
        if vosk_m is not None:
            log.info("Vosk model ready")
    import threading
    threading.Thread(target=_warm_stt, daemon=True, name="stt-warm").start()

    log.info("starting voice-ws on ws://%s:%d/v1/voice/ws", host, port)
    async with websockets.serve(_serve, host, port):
        log.info("voice-ws ready")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
