"""Emotion tools for Ikaros V5.
"""
from __future__ import annotations

from v5.tools.utils import safe_tool, dumps, local_llm_available, answer


@safe_tool
def v5_analyze_emotion(text: str, *, force_update: bool = False) -> str:
    """Update Ikaros's PAD emotion state from a piece of text.

    If :8080 is reachable, use the LLM to map text to PAD; otherwise
    fall back to a lightweight rule-based estimator.
    Returns the new PAD state as JSON with a mood label.
    """
    from v5.affect import AffectState

    state = AffectState.load()
    if local_llm_available():
        from v5.tools.utils import V5_ROOT, _LOCAL_LLM_HOST, _LOCAL_LLM_PORT
        import urllib.request, json, urllib.error
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": "你是一个情感分析师，将中文文本映射到PAD三维情感空间。只返回三个浮点数，以空格分隔，例如：0.5 0.2 -0.1。"},
                {"role": "user", "content": text}
            ],
            "max_tokens": 50,
            "temperature": 0.0,
            "stream": False,
        }
        try:
            req = urllib.request.Request(
                f"http://{_LOCAL_LLM_HOST}:{_LOCAL_LLM_PORT}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                reply = data["choices"][0]["message"]["content"]
                parts = reply.strip().split()
                p = float(parts[0]) if len(parts) > 0 else state.pleasure
                a = float(parts[1]) if len(parts) > 1 else state.arousal
                d = float(parts[2]) if len(parts) > 2 else state.dominance
                state.update(p, a, d, source="llm")
        except Exception:
            state.update_from_text(text, source="rule")
    else:
        state.update_from_text(text, source="rule")
    state.save()
    return answer(
        f"情感已更新：愉悦{state.pleasure:.2f} 激活{state.arousal:.2f} 掌控{state.dominance:.2f}",
        {
            "pleasure": state.pleasure,
            "arousal": state.arousal,
            "dominance": state.dominance,
            "mood_label": state.to_prompt(),
        }
    )


@safe_tool
def v5_emotion_status() -> str:
    """Return the current PAD emotion state (no external dependency).

    Includes a brief mood label generated from the PAD values.
    """
    from v5.affect import AffectState

    state = AffectState.load()
    return answer(
        f"当前情感：愉悦{state.pleasure:.2f} 激活{state.arousal:.2f} 掌控{state.dominance:.2f}",
        {
            "pleasure": state.pleasure,
            "arousal": state.arousal,
            "dominance": state.dominance,
            "mood_label": state.to_prompt(),
            "last_updated": getattr(state, "last_updated", None),
        }
    )


@safe_tool
def v5_emotion_label(text: str, *, fallback: str = "平静") -> str:
    """Return 1-2 emotion tags for the text.

    If :8080 is available, ask the LLM for tags; otherwise fall back
    to a very lightweight keyword matcher.
    """
    if local_llm_available():
        from v5.tools.utils import _LOCAL_LLM_HOST, _LOCAL_LLM_PORT
        import urllib.request, json, urllib.error
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": "你是一个情感标签器，将中文文本映射到1-2个情感标签。只返回标签，以空格分隔。例如：开心 平静"},
                {"role": "user", "content": text}
            ],
            "max_tokens": 30,
            "temperature": 0.3,
            "stream": False,
        }
        try:
            req = urllib.request.Request(
                f"http://{_LOCAL_LLM_HOST}:{_LOCAL_LLM_PORT}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tags = data["choices"][0]["message"]["content"].strip().split()
                method = "llm"
        except Exception:
            tags = [fallback]
            method = "rule"
    else:
        tags = [fallback]
        method = "rule"
    return dumps({"tags": tags, "method": method})