"""
test_token_compressor_integration.py — token_compressor 接入 hermes 插件后的沙箱验证

验证点:
  1) 增强路径: on_pre_compress 用 compress_retrieval_block 替代 text[:150] 硬截断
     - 高相关记忆原样保留
     - 低相关长记忆被压缩(<=150 字符), 而非整条 400 字硬截断
  2) 回退路径: 当 compress_retrieval_block 抛异常时, 自动回退原始 text[:150] 硬截断, 不崩

运行:
  core/hermes/venv/Scripts/python.exe tests/test_token_compressor_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core"))
_HERMES = os.path.join(_CORE, "hermes")
sys.path.insert(0, _HERMES)   # for `agent` package
sys.path.insert(0, _CORE)     # for `memory_v5` package

from plugins.memory.ikaros_v5 import IkarosV5MemoryProvider  # noqa: E402


class _FakeMem:
    """模拟 memory_v5.store.search 返回的对象(.content / .weight)"""

    def __init__(self, content: str, weight: float):
        self.content = content
        self.weight = weight


def _make_provider(fake_results):
    p = IkarosV5MemoryProvider()
    p._v5_loaded = True
    p._v5_root = Path(tempfile.mkdtemp(prefix="v5_sandbox_"))  # affect.json 不存在 -> 跳过
    p._v5_search = lambda q, top_k=5: fake_results
    p._v5_stats = lambda: {"total": 100}
    return p


class TestHermesIntegration(unittest.TestCase):

    def _messages(self):
        return [{"role": "user", "content": "你一般喜欢在什么环境下工作？"}]

    def test_enhanced_path_keeps_high_score_compresses_long(self):
        results = [
            _FakeMem("用户喜欢在安静独立的环境工作，讨厌被频繁打断", 0.95),
            _FakeMem("x" * 400, 0.2),                       # 低相关长内容 -> 压缩
            _FakeMem("用户最近开始学习 Rust 编程语言", 0.80),  # 高相关 -> 原样
        ]
        p = _make_provider(results)
        out = p.on_pre_compress(self._messages())

        self.assertIn("[Ikaros 相关记忆]", out)
        self.assertIn("[Ikaros 记忆库] 共 100 条记录", out)
        # 高相关原样保留
        self.assertIn("安静独立", out)
        self.assertIn("Rust", out)
        # 低相关的 400 字长串被压缩(不应原样出现)
        self.assertNotIn("x" * 400, out)
        # 低相关行压缩后内容 <= 150 字符
        for line in out.split("\n"):
            if line.startswith("[0.20]"):
                content = line[len("[0.20]"):].strip()
                self.assertLessEqual(len(content), 150,
                                     f"低相关记忆未压缩: {len(content)} 字符")

    def test_fallback_when_compressor_raises(self):
        import memory_v5.extensions.token_compressor as tcm
        results = [
            _FakeMem("用户喜欢在安静独立的环境工作", 0.95),
            _FakeMem("y" * 400, 0.2),
        ]
        p = _make_provider(results)

        orig = tcm.compress_retrieval_block

        def _boom(*a, **k):
            raise RuntimeError("simulated token_compressor failure")

        try:
            tcm.compress_retrieval_block = _boom
            out = p.on_pre_compress(self._messages())   # 不应抛异常
        finally:
            tcm.compress_retrieval_block = orig

        self.assertIn("[Ikaros 相关记忆]", out)
        # 回退路径: 低相关长内容走 text[:150] 硬截断
        self.assertNotIn("y" * 400, out)
        for line in out.split("\n"):
            if line.startswith("[0.20]"):
                content = line[len("[0.20]"):].strip()
                self.assertLessEqual(len(content), 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)
