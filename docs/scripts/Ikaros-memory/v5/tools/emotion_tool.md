# emotion_tool.py

> 源文件：`Ikaros-memory/v5/tools/emotion_tool.py`

v5.tools.emotion_tool — 3 emotion tools.

  v5_analyze_emotion(text)   -> apply a PAD event + optional causal record
  v5_emotion_status()        -> current PAD state (no external dependency)
  v5_emotion_label(text)     -> 1-2 emotion tags (LLM, falls back to rule)

All return JSON strings; all are wrapped with @safe_tool so they never raise.
