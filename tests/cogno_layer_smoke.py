"""cogno_layer Smoke test (v2 rebuild 2026-07-05).

v2: enrich() natural language output, enhanced emotion, activity inference.
Run:  python tests/cogno_layer_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

IKAROS_MEMORY_DIR = Path(__file__).resolve().parent / "core/memory_v5"
sys.path.insert(0, str(IKAROS_MEMORY_DIR))

import unittest

from cogno_5d import (
    enrich,
    enrich_reply,
    reset_context,
    get_time_str,
    get_machine_id,
    get_geo_location,
    infer_emotion,
    infer_activity,
    get_weekday_str,
    compress_context,
)


class TestEmotionBuckets(unittest.TestCase):
    """v2 enhanced emotion: expanded keywords + combo patterns."""

    def test_happy_greeting(self):
        self.assertEqual(infer_emotion("伊卡洛斯，早上好呀！"), "开心")

    def test_calm_short(self):
        self.assertEqual(infer_emotion("A"), "平静")

    def test_calm_continue(self):
        self.assertEqual(infer_emotion("继续"), "平静")

    def test_grateful(self):
        self.assertEqual(infer_emotion("辛苦了，伊卡洛斯"), "感谢")

    def test_curious_question(self):
        self.assertEqual(infer_emotion("为什么这个端点会失败？"), "好奇")

    def test_frustrated_combo(self):
        self.assertEqual(infer_emotion("又报错了，服了"), "烦躁")

    def test_frustrated_card_dead(self):
        self.assertEqual(infer_emotion("桥卡死了"), "烦躁")

    def test_frustrated_still_failing(self):
        self.assertEqual(infer_emotion("为什么还是不行？又失败了"), "烦躁")

    def test_sad_tired(self):
        self.assertEqual(infer_emotion("好累啊不想干了"), "悲伤")


class TestActivityInference(unittest.TestCase):
    """Time-of-day activity inference."""

    def test_late_night(self):
        result = infer_activity(2, 5)
        self.assertIn("深夜", result)

    def test_morning(self):
        result = infer_activity(7, 0)
        self.assertIn("清晨", result)

    def test_afternoon(self):
        result = infer_activity(15, 2)
        self.assertIn("下午", result)

    def test_evening(self):
        result = infer_activity(21, 5)
        self.assertIn("晚上", result)

    def test_weekend_note(self):
        result = infer_activity(14, 6)
        self.assertIn("随意", result)

    def test_weekday_no_note(self):
        result = infer_activity(14, 2)
        self.assertNotIn("随意", result)


class TestWeekdayStr(unittest.TestCase):
    def test_all_days(self):
        expected = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        for i, name in enumerate(expected):
            self.assertEqual(get_weekday_str(i), name)


class TestEnrichFormat(unittest.TestCase):
    """v2: enrich() outputs natural language narrative."""

    def setUp(self):
        reset_context()

    def test_enrich_returns_string(self):
        out = enrich("你好")
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 10)

    def test_enrich_contains_time(self):
        import re
        out = enrich("hi")
        self.assertRegex(out, r"\d{1,2}月\d{1,2}日")

    def test_enrich_contains_activity(self):
        out = enrich("你好")
        self.assertIn("哥哥", out)

    def test_enrich_contains_emotion(self):
        out = enrich("辛苦了哥哥")
        self.assertIn("感谢", out)

    def test_enrich_reasonable_length(self):
        out = enrich("这是一个测试")
        self.assertGreater(len(out), 50)
        self.assertLess(len(out), 500)

    def test_empty_user_is_safe(self):
        out = enrich("")
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 10)


class TestFailureSilent(unittest.TestCase):
    def test_enrich_returns_string_on_empty_history(self):
        self.assertIsInstance(enrich("hi", None), str)
        self.assertIsInstance(enrich("hi", []), str)

    def test_enrich_with_garbage_history(self):
        for h in (None, [], [{}], [{"junk": True}], [{"role": "system"}],
                  [{"role": "user", "content": None}]):
            out = enrich("ok", h)
            self.assertIsInstance(out, str)


class TestEnrichReplyV2(unittest.TestCase):
    def test_returns_dict(self):
        out = enrich_reply("hi")
        self.assertIsInstance(out, dict)
        self.assertGreaterEqual(len(out), 3)

    def test_has_new_fields(self):
        out = enrich_reply("hi", user_text="abc")
        self.assertIn("topic", out)
        self.assertIn("activity", out)
        self.assertIn("weekday", out)


class TestCacheTTL(unittest.TestCase):
    def test_reset_caches(self):
        enrich("seed")
        reset_context()
        out = enrich("after-reset")
        self.assertIsInstance(out, str)

    def test_time_dim_is_datetime(self):
        s = get_time_str()
        import re
        self.assertRegex(s, r"^\d{4}/\d+/\d+\s+\d+:\d+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
