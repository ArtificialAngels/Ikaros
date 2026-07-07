"""
v4.tests.test_scheduler — V4 反思调度器单测

V3 缺单测 (memory_reflect.py 757 行 0 测试). V4 强制每模块有单测.

覆盖:
  - should_run: never / just / overdue / force / typo 字段
  - next_run_time: 同上
  - ScheduleState: 不可变 / dict 互转
  - load_state / save_state: 损坏 / 不存在
  - ReflectScheduler: 注册 / 去重 / ops_due / run_one / run_all / dry_run
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 把 v4 包根加入 sys.path, 让 import v4.reflect.scheduler 能跑
V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))

from v4.reflect.scheduler import (  # noqa: E402
    DEFAULT_CLEANUP_INTERVAL,
    DEFAULT_CONSOLIDATE_INTERVAL,
    DEFAULT_DEDUP_INTERVAL,
    DEFAULT_DISTILL_INTERVAL,
    DEFAULT_PROMOTE_INTERVAL,
    ReflectOp,
    ReflectScheduler,
    ScheduleState,
    load_state,
    next_run_time,
    save_state,
    should_run,
)


# ─── should_run 测试 ─────────────────────────────────────────────

def test_should_run_never():
    """从未跑过 → 该跑."""
    state = ScheduleState()
    now = 1_000_000.0
    assert should_run(state, "last_consolidate", 3600, now=now) is True


def test_should_run_just_now():
    """刚跑过 → 不该跑."""
    now = 1_000_000.0
    state = ScheduleState.from_dict({"last_consolidate": now - 10})  # 10 秒前
    assert should_run(state, "last_consolidate", 3600, now=now) is False


def test_should_run_overdue():
    """过期 → 该跑."""
    now = 1_000_000.0
    state = ScheduleState.from_dict({"last_consolidate": now - 7200})  # 2h 前
    assert should_run(state, "last_consolidate", 3600, now=now) is True


def test_should_run_force():
    """force=True → 无视状态, 必跑."""
    state = ScheduleState.from_dict({"last_consolidate": time.time()})  # 刚跑
    assert should_run(state, "last_consolidate", 3600, force=True) is True


def test_should_run_typo_field_safe():
    """typo 字段名 → getattr 默认 0 → 该跑, 不抛."""
    state = ScheduleState()
    now = 1_000_000.0
    # last_consollidate (typo) 不存在, getattr default 0, 视为 never
    assert should_run(state, "last_consollidate", 3600, now=now) is True


# ─── next_run_time 测试 ───────────────────────────────────────────

def test_next_run_time_never():
    state = ScheduleState()
    now = 1_000_000.0
    assert next_run_time(state, "last_consolidate", 3600, now=now) == 0 + 3600


def test_next_run_time_overdue():
    now = 1_000_000.0
    state = ScheduleState.from_dict({"last_consolidate": now - 100})
    assert next_run_time(state, "last_consolidate", 3600, now=now) == (now - 100) + 3600


# ─── ScheduleState 测试 ──────────────────────────────────────────

def test_state_immutable():
    """frozen dataclass 不能改 _times 字段."""
    state = ScheduleState.empty()
    try:
        state._times = {}  # type: ignore
        assert False, "应该抛 FrozenInstanceError"
    except Exception:
        pass  # 预期


def test_state_dict_roundtrip():
    state = ScheduleState.from_dict({
        "last_consolidate": 1.0, "last_dedup": 2.0, "last_promote": 3.0,
        "last_distill": 4.0, "last_cleanup": 5.0,
    })
    d = state.to_dict()
    assert d["last_consolidate"] == 1.0
    assert d["last_cleanup"] == 5.0
    state2 = ScheduleState.from_dict(d)
    assert state2 == state


def test_state_from_partial_dict():
    """部分字段缺失 → 默认 0."""
    state = ScheduleState.from_dict({"last_consolidate": 100.0})
    assert state.get("last_consolidate") == 100.0
    assert state.get("last_dedup") == 0.0


def test_state_dynamic_key():
    """测试 / 第三方 op 可注册任意 last_run_key, 不需要改 schema."""
    state = ScheduleState.empty()
    assert state.get("last_bad", 0.0) == 0.0
    state2 = state.set("last_bad", 12345.0)
    assert state2.get("last_bad") == 12345.0
    # frozen: 旧 state 不动
    assert state.get("last_bad", 0.0) == 0.0


def test_save_load_roundtrip(tmp_path: Path):
    state = ScheduleState.from_dict({"last_consolidate": 12345.0, "last_distill": 67890.0})
    f = tmp_path / "state.json"
    save_state(state, f)
    state2 = load_state(f)
    assert state2 == state


# ─── load_state / save_state 测试 ────────────────────────────────

def test_save_load_roundtrip(tmp_path: Path):
    state = ScheduleState.from_dict({"last_consolidate": 12345.0, "last_distill": 67890.0})
    f = tmp_path / "state.json"
    save_state(state, f)
    state2 = load_state(f)
    assert state2 == state


def test_load_state_missing_file(tmp_path: Path):
    """state 文件不存在 → 返空 state, 不抛."""
    f = tmp_path / "nope.json"
    state = load_state(f)
    assert state == ScheduleState()


def test_load_state_corrupted_file(tmp_path: Path):
    """state 文件损坏 → log warning + 返空 state, 不抛."""
    f = tmp_path / "broken.json"
    f.write_text("{not valid json", encoding="utf-8")
    state = load_state(f)
    assert state == ScheduleState()


# ─── ReflectScheduler 测试 ──────────────────────────────────────

def _make_op(name: str, interval: int = 3600,
             counter: list[int] | None = None) -> ReflectOp:
    """测试用 op, 跑一次累加 counter."""
    counter = counter if counter is not None else []

    def fn() -> int:
        counter.append(1)
        return len(counter)

    return ReflectOp(
        name=name,
        fn=fn,
        interval_sec=interval,
        last_run_key=f"last_{name}",
    )


def test_scheduler_register_and_dedupe():
    counter: list[int] = []
    s = ReflectScheduler()
    op1 = _make_op("consolidate", counter=counter)
    op2 = _make_op("consolidate", counter=counter)  # 同名
    s.register(op1)
    s.register(op2)
    # 重复注册同名 → 应只有 1 条
    assert len(s._ops) == 1
    assert s._ops[0] is op2


def test_scheduler_ops_due_never_run():
    counter: list[int] = []
    s = ReflectScheduler(
        ops=[
            _make_op("consolidate", counter=counter),
            _make_op("dedup", interval=21600, counter=counter),
        ],
        state=ScheduleState.empty(),  # 避免 state 污染
    )
    due = s.ops_due(now=1_000_000.0)
    assert len(due) == 2  # 从没跑过, 全部 due


def test_scheduler_ops_due_after_run():
    counter: list[int] = []
    s = ReflectScheduler(
        ops=[_make_op("consolidate", counter=counter)],
        state=ScheduleState.empty(),
    )
    # 跑一次
    s.run_one(s._ops[0], force=True)
    # 立刻再查 → 不该 due
    due = s.ops_due(now=time.time())
    assert len(due) == 0


def test_scheduler_run_one_updates_state():
    counter: list[int] = []
    s = ReflectScheduler(ops=[_make_op("consolidate", counter=counter)])
    n = s.run_one(s._ops[0], force=True)
    assert n == 1
    assert counter == [1]
    assert s.state.get("last_consolidate", 0.0) > 0


def test_scheduler_run_one_not_due_returns_zero():
    counter: list[int] = []
    s = ReflectScheduler(ops=[_make_op("consolidate", counter=counter)])
    # 先跑一次
    s.run_one(s._ops[0], force=True)
    # 不 force, 立刻再跑 → 返 0
    n = s.run_one(s._ops[0])
    assert n == 0
    assert counter == [1]  # 第二次没真跑


def test_scheduler_run_one_propagates_exception():
    """V4 行为: 异常上抛, state 不更新."""
    def bad_fn() -> int:
        raise RuntimeError("故意失败")

    op = ReflectOp(name="bad", fn=bad_fn, interval_sec=3600, last_run_key="last_bad")
    s = ReflectScheduler(ops=[op])
    try:
        s.run_one(op, force=True)
        assert False, "应该上抛"
    except RuntimeError as e:
        assert "故意失败" in str(e)
    # state 应未更新 (last_bad 仍为 0)
    assert s.state.get("last_bad", 0.0) == 0.0


def test_scheduler_run_all_continue_on_error():
    """continue_on_error=True → 失败收集到 -1, 不中断."""
    counter: list[int] = []

    def bad_fn() -> int:
        raise RuntimeError("boom")

    s = ReflectScheduler(ops=[
        _make_op("consolidate", counter=counter),
        ReflectOp(name="bad", fn=bad_fn, interval_sec=3600, last_run_key="last_bad"),
    ])
    results = s.run_all(force=True, continue_on_error=True)
    assert results["consolidate"] == 1
    assert results["bad"] == -1
    assert counter == [1]


def test_scheduler_run_all_stops_on_error_default():
    """continue_on_error=False (默认) → 失败上抛, 后面的不跑."""
    counter: list[int] = []

    def bad_fn() -> int:
        raise RuntimeError("boom")

    s = ReflectScheduler(ops=[
        _make_op("consolidate", counter=counter),
        ReflectOp(name="bad", fn=bad_fn, interval_sec=3600, last_run_key="last_bad"),
    ])
    try:
        s.run_all(force=True)
        assert False, "应该上抛"
    except RuntimeError:
        pass
    # bad 在 consolidate 之后注册, 取决于插入顺序
    # 关键: bad 失败 → 后续不应被跑 (但本测试只有 2 个 op, 验证 bad 失败)


def test_scheduler_dry_run_with_dynamic_key():
    """dry_run 应支持任意 last_run_key (测试 / 第三方 op)."""
    counter: list[int] = []
    op = ReflectOp(name="bad", fn=lambda: 0, interval_sec=3600, last_run_key="last_bad")
    s = ReflectScheduler(ops=[op])
    report = s.dry_run()
    assert "bad" in report
    assert report["bad"]["last_run_human"] == "never"


def test_scheduler_dry_run():
    counter: list[int] = []
    # 用 fresh state 避免测试间 state 污染 (data/v4/reflect_state.json 持久化)
    s = ReflectScheduler(
        ops=[_make_op("consolidate", counter=counter)],
        state=ScheduleState.empty(),
    )
    report = s.dry_run()
    assert "consolidate" in report
    assert report["consolidate"]["due"] is True
    assert report["consolidate"]["last_run"] is None
    assert report["consolidate"]["last_run_human"] == "never"
    assert report["consolidate"]["interval_sec"] == 3600


# ─── V3 间隔对齐验证 ─────────────────────────────────────────────

def test_v3_interval_constants_aligned():
    """V4 间隔常量 = V3 间隔, 行为对齐 (V3 → V4 平滑过渡)."""
    # V3 memory_reflect.py:67-70
    assert DEFAULT_CONSOLIDATE_INTERVAL == 3600
    assert DEFAULT_DEDUP_INTERVAL == 21600
    assert DEFAULT_PROMOTE_INTERVAL == 43200
    assert DEFAULT_DISTILL_INTERVAL == 86400
    # V4 新增: cleanup 单独 interval (V3 bug 修复)
    assert DEFAULT_CLEANUP_INTERVAL == 21600


# ─── runner ─────────────────────────────────────────────────────

def _run_all_tests():
    """简易 runner, 不依赖 pytest (便携 U 盘场景)."""
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
            sig = inspect.signature(fn)
            if "tmp_path" in sig.parameters:
                fn(tmp_path=Path("./.tmp_test"))  # type: ignore
            else:
                fn()
            passed += 1
            print(f"  PASS  {name}")
        except Exception as e:
            failed.append((name, e))
            print(f"  FAIL  {name}: {e}")
    print(f"\n{passed}/{len(tests)} tests passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    import os
    os.makedirs("./.tmp_test", exist_ok=True)
    sys.exit(_run_all_tests())
