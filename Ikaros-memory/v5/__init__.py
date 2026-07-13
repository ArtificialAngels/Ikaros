"""Ikaros V5.1 — Unified Memory × Emotion × Self-Cognition

  V5.1 = V4 记忆引擎 + V5 情感/自我认知 + 反思子系统 (全面合并)
  
  持久层: v4.db (SQLite + FTS5 + ChromaDB 向量索引)
  反思引擎: v5/reflect/ (consolidate/dedup/promote/distill/reflect/cleanup)
  情感引擎: v5/affect (PAD) + v5/vitality (精力) + v5/drivers (混沌/ECA/AIS)
  自我认知: v5/self_model (持久自我) + v5/metacog (元认知/哲思)
  主动意识: v5/think (统一思考循环) + v5/proactive (主动搭话) + v5/care (关怀)
  关系叙事: v5/relationship (亲密度) + v5/narrative (月度叙事) + v5/dissonance (认知失调)
  路由任务: v5/router (对话/任务分类) + v5/task_runner (后台执行)

模块:
  affect.py          — PAD 情感状态机
  self_model.py      — 持久自我模型 ("我是谁")
  metacog.py         — 元认知循环 (LLM 反思 + 哲学探索)
  think.py           — 统一思考循环 (15min 节拍)
  drivers.py         — LorenzPAD / ECAGrid / AISDetectorSet (算法库)
  router.py          — 对话/任务分类
  task_runner.py     — 后台任务执行 + 提醒
  proactive.py       — 主动搭话
  care.py            — 关怀检测
  relationship.py    — 亲密度模型
  narrative.py       — 月度自我叙事
  dissonance.py      — 认知失调检测
  emotional_memory.py — 情感因果记忆
  vitality.py        — 精力模型
  self_discovery.py  — 自主架构发现
  reflect/           — 反思子系统 (scheduler/registry/consolidate/distill/llm_client)
"""

from __future__ import annotations

__version__ = "5.1.0"
__all__ = ["AffectState", "EMOTION_MAP", "load_state", "save_state"]
