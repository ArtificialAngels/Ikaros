"""
v4.tests.test_registry — V4 反思 registry 测试

覆盖:
  - make_default_scheduler 注册 7 个 op
  - 每个 op 的 interval 与 V3 对齐
  - reflect 是 V4 新增 (7d), V3 没有
  - dry-run 报告包含所有 op
  - mock 每个 op 的 fn, 验证 run_all 调用
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))


def test_registry_registers_seven_ops():
    """默认 scheduler 注册 7 个 op (V3 5 个 + V4 reflect + V4 vector_sync)."""
    from v4.reflect.registry import make_default_scheduler
    from v4.reflect.scheduler import ScheduleState
    sched = make_default_scheduler(state=ScheduleState.empty())
    names = [op.name for op in sched._ops]
    assert names == ["consolidate", "dedup", "promote", "distill", "reflect", "cleanup", "vector_sync"]


def test_registry_intervals_align_with_v3():
    """V4 默认 interval = V3 默认 (V3 → V4 平滑过渡)."""
    from v4.reflect.registry import make_default_scheduler
    from v4.reflect.scheduler import (
        DEFAULT_CLEANUP_INTERVAL,
        DEFAULT_CONSOLIDATE_INTERVAL,
        DEFAULT_DEDUP_INTERVAL,
        DEFAULT_DISTILL_INTERVAL,
        DEFAULT_PROMOTE_INTERVAL,
        DEFAULT_REFLECT_INTERVAL,
        ScheduleState,
    )
    sched = make_default_scheduler(state=ScheduleState.empty())
    intervals = {op.name: op.interval_sec for op in sched._ops}
    assert intervals["consolidate"] == DEFAULT_CONSOLIDATE_INTERVAL == 3600
    assert intervals["dedup"] == DEFAULT_DEDUP_INTERVAL == 21600
    assert intervals["promote"] == DEFAULT_PROMOTE_INTERVAL == 43200
    assert intervals["distill"] == DEFAULT_DISTILL_INTERVAL == 86400
    assert intervals["cleanup"] == DEFAULT_CLEANUP_INTERVAL == 21600
    # V4 新增: reflect 7d
    assert intervals["reflect"] == DEFAULT_REFLECT_INTERVAL == 604800


def test_registry_dry_run_includes_all_ops():
    """dry-run 报告覆盖 7 个 op."""
    from v4.reflect.registry import make_default_scheduler
    from v4.reflect.scheduler import ScheduleState
    sched = make_default_scheduler(state=ScheduleState.empty())
    report = sched.dry_run()
    expected = {"consolidate", "dedup", "promote", "distill", "reflect", "cleanup", "vector_sync"}
    assert set(report.keys()) == expected


def test_registry_run_all_with_force_calls_each_op():
    """force=True 时, 所有 op 都跑."""
    from v4.reflect.registry import make_default_scheduler
    from v4.reflect.scheduler import ScheduleState
    sched = make_default_scheduler(state=ScheduleState.empty())
    # 把每个 op 的 fn 换成 mock
    mock_calls = []
    for op in sched._ops:
        original_fn = op.fn
        mock = MagicMock(return_value=42)
        mock_calls.append((op.name, mock))
        # ReflectOp 是 frozen, 不能直接改 fn, 重新构造
        new_op = sched._ops[sched._ops.index(op)].__class__(
            name=op.name, fn=mock, interval_sec=op.interval_sec,
            last_run_key=op.last_run_key,
        )
        sched._ops[sched._ops.index(op)] = new_op

    results = sched.run_all(force=True)
    # 7 个 op 都跑过
    assert len(results) == 7
    for name, mock in mock_calls:
        assert name in results
        assert results[name] == 42
        mock.assert_called_once()


def test_registry_no_op_due_with_fresh_state():
    """新鲜 state 全部 due; 跑过后再查全不 due."""
    from v4.reflect.registry import make_default_scheduler
    from v4.reflect.scheduler import ScheduleState
    sched = make_default_scheduler(state=ScheduleState.empty())
    # 替换所有 fn 为 noop
    for op in sched._ops:
        sched._ops[sched._ops.index(op)] = op.__class__(
            name=op.name, fn=lambda: 0, interval_sec=op.interval_sec,
            last_run_key=op.last_run_key,
        )
    # 跑一次 force=True
    sched.run_all(force=True)
    # 立刻再查 ops_due → 0
    due = sched.ops_due()
    assert len(due) == 0


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    import inspect
    tests = [
        (name, fn)
        for name, fn in globals().items()
        if name.startswith("test_") and callable(fn)
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"  FAIL  {name}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all_tests())
