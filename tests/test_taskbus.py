"""B2: core/taskbus.py 任务事件总线单元测试.

运行: PYTHONPATH=<repo>/core python -m pytest tests/test_taskbus.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from taskbus import EventBus, exec_state_event, EXEC_STATES, EVENT_PROTOCOL_VERSION  # noqa: E402


def test_subscribe_publish_unsub():
    bus = EventBus()
    got = []
    unsub = bus.subscribe(lambda e: got.append(e))
    assert len(bus) == 1
    bus.publish(exec_state_event("t1", "n1", "working", progress=0.5))
    assert len(got) == 1
    assert got[0].type == "node.exec_state_changed"
    assert got[0].data["node_id"] == "n1"
    assert got[0].data["progress"] == 0.5
    unsub()
    assert len(bus) == 0
    bus.publish(exec_state_event("t1", "n1", "done"))
    assert len(got) == 1  # 退订后不再接收


def test_publish_dict_coerced():
    bus = EventBus()
    got = []
    bus.subscribe(lambda e: got.append(e))
    bus.publish({"type": "x", "tree": "t", "data": {"a": 1}})
    assert got[0].type == "x"
    assert got[0].data == {"a": 1}
    assert got[0].v == EVENT_PROTOCOL_VERSION


def test_handler_exception_isolated():
    bus = EventBus()
    good = []

    def _boom(e):
        raise RuntimeError("boom")

    bus.subscribe(_boom)
    bus.subscribe(lambda e: good.append(e))
    bus.publish(exec_state_event("t", "n", "working"))
    # 第一个订阅者异常不应阻断第二个
    assert len(good) == 1


def test_exec_state_event_shape():
    ev = exec_state_event("tree_x", "node_y", "blocked", detail="wait user")
    d = ev.to_dict()
    assert d["type"] == "node.exec_state_changed"
    assert d["tree"] == "tree_x"
    assert d["data"]["node_id"] == "node_y"
    assert d["data"]["exec_state"] == "blocked"
    assert d["data"]["detail"] == "wait user"
    assert "idle" in EXEC_STATES and "working" in EXEC_STATES
