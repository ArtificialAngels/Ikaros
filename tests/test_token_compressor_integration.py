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
_PLUGIN_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "hermes-agent", "plugins", "ikaros_v5"))
sys.path.insert(0, _HERMES)   # for `agent` package
sys.path.insert(0, _CORE)     # for `memory_v5` package

# 外置插件 (2026-08-04 起) 不在 hermes 仓库内：直接按文件路径加载，
# 避免 core/hermes/plugins 的 regular package 遮蔽 runtime 的 namespace plugins。
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "ikaros_v5_memory_provider",
    os.path.join(_PLUGIN_DIR, "memory_provider.py"))
assert _spec is not None and _spec.loader is not None
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
IkarosV5MemoryProvider = _mod.IkarosV5MemoryProvider  # noqa: E402


def _make_provider(fake_results):
    p = IkarosV5MemoryProvider()
    p._v5_loaded = True
    p._v5_root = Path(tempfile.mkdtemp(prefix="v5_sandbox_"))  # affect.json 不存在 -> 跳过
    # 检索引擎已从 FTS5(store.search) 升级为 unified_retrieve(三路融合)：
    # 注入点随引擎变更，返回 dict 列表 {content, score}
    p._v5_unified = lambda q, top_k=5: fake_results
    p._v5_stats = lambda: {"total": 100}
    return p


class TestHermesIntegration(unittest.TestCase):

    def _messages(self):
        return [{"role": "user", "content": "你一般喜欢在什么环境下工作？"}]

    def test_enhanced_path_keeps_high_score_compresses_long(self):
        results = [
            {"content": "用户喜欢在安静独立的环境工作，讨厌被频繁打断", "score": 0.95},
            {"content": "x" * 400, "score": 0.2},                       # 低相关长内容 -> 压缩
            {"content": "用户最近开始学习 Rust 编程语言", "score": 0.80},  # 高相关 -> 原样
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
            {"content": "用户喜欢在安静独立的环境工作", "score": 0.95},
            {"content": "y" * 400, "score": 0.2},
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
