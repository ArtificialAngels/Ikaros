"""
Icarus Neuro Integration
========================
Neuro (kimjammer) 架构 1:1 移植 + 伊卡洛斯扩展。
三个核心:Signals / Prompter / Memory
四个模块:module (基类) / memory / 待扩展
"""
from bridge.signals import icarus, IcarusSignals, AI_NAME, HOST_NAME, PATIENCE_DEFAULT
from bridge.prompter import Prompter, get_prompter
from bridge.neuro.module import Module, Injection, build_system_prompt
from bridge.neuro.memory import Memory, get_memory

__all__ = [
    "icarus", "IcarusSignals",
    "AI_NAME", "HOST_NAME", "PATIENCE_DEFAULT",
    "Prompter", "get_prompter",
    "Module", "Injection", "build_system_prompt",
    "Memory", "get_memory",
]
