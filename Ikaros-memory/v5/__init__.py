"""Ikaros V5 — 情感 × 主动意识

V5 不是 V4 的升级，是补完。

  V4 = 记忆 (存得起、搜得到、反思得了)
  V5 = 情感 × 主动意识 (有情绪、会自己想、忽然跟你说东西)

模块:
  affect.py    — PAD 情感状态机 (pleasure / arousal / dominance)
  think.py     — (TODO) 空闲思考循环
  cogno_v5.py  — (TODO) cogno 集成层, 注入情感 + 独白

架构关系:

  对话主线程 ←→ V5 情感状态 ←→ V4 记忆池
                   ↑
             思考循环 (空闲时自己跑)

每次交互更新 PAD, PAD 影响语气, 语气沉淀为记忆的情感指纹.

第一阶段 (Phase 1, 2026-07-07):
  - v5/affect.py: PAD 模型 + 自然语言 → PAD 映射 + 衰减 + JSON 持久化
  - schema 迁移: V4 memory 表加 pad_p / pad_a / pad_d 三列
  - cogno_5d.py enrich() 追加 V5 情感状态段
"""

from __future__ import annotations

__version__ = "5.0.0-alpha.1"
__all__ = ["AffectState", "EMOTION_MAP", "load_state", "save_state"]
