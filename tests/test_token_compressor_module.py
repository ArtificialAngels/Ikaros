"""
test_token_compressor_module.py — V5 token_compressor 模块沙箱验证 (不依赖 hermes)

目标: 在离线/未装 llmlingua 的真实环境(当前 venv 无 llmlingua)下, 验证
  - 规则压缩可用且不崩
  - compress_text(quality="auto") 在 llmlingua 不可用时正确回退规则
  - compress_old_rounds / compress_retrieval_block / enforce_budget 行为正确

运行:
  core/hermes/venv/Scripts/python.exe tests/test_token_compressor_module.py
"""
from __future__ import annotations

import os
import sys
import unittest

# 把 E:/Ikaros/core 加进 sys.path, 使 memory_v5 可作为顶层包导入
_CORE = os.path.join(os.path.dirname(__file__), "..", "core")
sys.path.insert(0, os.path.abspath(_CORE))

from memory_v5.extensions import token_compressor as tc  # noqa: E402


def _llmlingua_state() -> str:
    try:
        import llmlingua  # noqa: F401
        return "installed"
    except Exception:
        return "absent(fallback)"


class TestTokenCompressorModule(unittest.TestCase):

    def test_est_tokens(self):
        self.assertEqual(tc.est_tokens(""), 0)
        self.assertGreater(tc.est_tokens("你好世界"), 0)

    def test_rule_compress_short_circuits_short_text(self):
        # 短文本(<=40字)不走压缩, 原样返回
        short = "记住用户叫小明"
        self.assertEqual(tc.rule_compress(short), short)

    def test_rule_compress_reduces_and_keeps_head_tail(self):
        long_text = (
            "好的。\n" * 5
            + "用户在2024年搬到了上海，从事人工智能相关工作，喜欢科幻小说。"
            + "这部分是中间冗余填充内容，用于测试中段压缩是否会保留首尾关键信息。"
            + "最近一次对话用户提到想学习 Rust 编程语言并计划在下个季度开始。"
        )
        out = tc.rule_compress(long_text, ratio=0.5)
        self.assertTrue(len(out) <= len(long_text))
        # 头尾关键信息应保留
        self.assertIn("上海", out)
        self.assertIn("Rust", out)

    def test_rule_compress_removes_filler(self):
        text = "好的。\n嗯。\n用户喜欢猫"
        out = tc.rule_compress(text)
        self.assertNotIn("好的。", out)
        self.assertIn("用户喜欢猫", out)

    def test_compress_text_auto_falls_back_to_rule(self):
        # 当前环境无 llmlingua -> auto 必须回退规则且不抛异常
        text = "用户偏好在晚上工作，讨厌被频繁打断，最近迷上了机械键盘。"
        out = tc.compress_text(text, quality="auto", ratio=0.7)
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) <= len(text) + 4)  # 允许 … 占位

    def test_compress_text_rule_explicit(self):
        text = "x" * 300
        out = tc.compress_text(text, quality="rule", ratio=0.5)
        self.assertLessEqual(len(out), len(text))

    def test_compress_old_rounds_preserves_tail(self):
        rounds = [
            {"role": "user", "content": "a" * 200, "score": 0.1},
            {"role": "assistant", "content": "b" * 200, "score": 0.1},
            {"role": "user", "content": "c" * 200, "score": 0.1},
            {"role": "user", "content": "RECENT_IMPORTANT_QUESTION", "score": 0.9},
            {"role": "assistant", "content": "RECENT_IMPORTANT_ANSWER", "score": 0.9},
        ]
        out = tc.compress_old_rounds(rounds, tail_keep=2, ratio=0.4)
        self.assertEqual(len(out), 5)
        # tail 两条原样
        self.assertEqual(out[3]["content"], "RECENT_IMPORTANT_QUESTION")
        self.assertEqual(out[4]["content"], "RECENT_IMPORTANT_ANSWER")
        # 旧轮被标记压缩且变短
        self.assertTrue(out[0].get("_compressed"))
        self.assertLessEqual(len(out[0]["content"]), 200)

    def test_compress_retrieval_block_keeps_high_score_full(self):
        results = [
            {"content": "用户的核心偏好：喜欢在安静环境工作", "score": 0.95},
            {"content": "x" * 400, "score": 0.2},  # 低相关长内容 -> 应被压缩
        ]
        out = tc.compress_retrieval_block(results, max_chars_per_item=150)
        self.assertEqual(len(out), 2)
        # 高相关原样保留
        self.assertEqual(out[0]["content"], "用户的核心偏好：喜欢在安静环境工作")
        self.assertFalse(out[0].get("_compressed"))
        # 低相关被压缩且不超过预算
        self.assertTrue(out[1].get("_compressed"))
        self.assertLessEqual(len(out[1]["content"]), 150)

    def test_enforce_budget_keeps_highest_score(self):
        blocks = [
            {"content": "low", "score": 0.1},
            {"content": "high", "score": 0.9},
            {"content": "mid", "score": 0.5},
        ]
        kept = tc._enforce_budget(blocks, budget_tokens=10)
        scores = {b["score"] for b in kept}
        self.assertIn(0.9, scores)  # 最高分必留

    def test_enforce_budget_texts(self):
        texts = ["a" * 100, "b" * 100, "c" * 100]
        kept = tc.enforce_budget(texts, budget_tokens=150)
        self.assertLess(len(kept), 3)  # 预算不足以容纳全部


if __name__ == "__main__":
    print("llmlingua state:", _llmlingua_state())
    unittest.main(verbosity=2)
