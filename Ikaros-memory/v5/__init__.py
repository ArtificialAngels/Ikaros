"""Ikaros V5 — 情感 × 主动意识

  V4 = 记忆 (存得起、搜得到、反思得了)
  V5 = 情感 × 主动意识 (有情绪、会自己想、主动交付)

模块:
  affect.py     — PAD 情感状态机
  think.py      — Lorenz 混沌 + ECA 主题 → 内联独白
  drivers.py    — LorenzPAD / ECAGrid / AISDetectorSet (算法库)
  router.py     — 对话/任务分类 + 本地 LLM 优化
  task_runner.py — 后台调云 LLM + 完成提醒 + 有空/没空
"""

from __future__ import annotations

__version__ = "5.0.0-beta.2"
__all__ = ["AffectState", "EMOTION_MAP", "load_state", "save_state"]
