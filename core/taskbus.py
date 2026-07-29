"""Ikaros 任务事件总线 (herdr ``events.subscribe`` 语义内化).

把 herdr 的「server=唯一真相 + 客户端订阅类型化事件 + 语义状态」模型,
内化进 Ikaros 的多子任务运行时:

- :class:`TaskEvent` —— 版本化的类型化事件 (``v`` / ``type`` / ``ts`` / ``tree`` / ``data``)。
- :class:`EventBus` —— 单进程内线程安全 pub/sub。``subscribe(handler)`` 返回退订函数,
  ``publish(event)`` 广播给所有订阅者; 单个 handler 异常不影响其他订阅者。

零第三方依赖 (标准库)。可被 conversation-tree 的 SSE 端点、9100 面板、Neko 前端、
CodingAgentSupervisor 共用同一总线。

事件协议版本化 (``EVENT_PROTOCOL_VERSION``) 以便向前兼容 —— 对应 herdr 的 ``PROTOCOL_VERSION``。
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "EVENT_PROTOCOL_VERSION",
    "EXEC_STATES",
    "TaskEvent",
    "EventBus",
    "exec_state_event",
]

# 内部事件协议版本 (对应 herdr 的 PROTOCOL_VERSION, 当前 herdr 真实协议 = 17;
# Ikaros 自有事件协议从 1 开始, 与 herdr wire 协议号独立)。
EVENT_PROTOCOL_VERSION = 1

# 节点执行状态: 对齐 herdr idle/working/blocked/done, 扩展 pending/done/unknown
# 供 conversation-tree 节点徽标 + CodingAgentSupervisor 状态机共用。
EXEC_STATES = ("idle", "pending", "working", "blocked", "done", "unknown")

# 常用事件类型 (类型化事件名)
EVENT_NODE_EXEC_STATE = "node.exec_state_changed"
EVENT_NODE_CREATED = "node.created"
EVENT_NODE_MERGED = "node.merged"
EVENT_TASK_BATCH = "task.batch"


@dataclass
class TaskEvent:
    """一条类型化任务事件。

    ``type`` 形如 ``node.exec_state_changed``; ``data`` 是事件负载 (dict);
    ``tree`` 是 persist_key / 任务批次标识 (SSE 订阅方可据此过滤);
    ``ts`` 为 epoch 秒; ``v`` 为事件协议版本。
    """

    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    tree: str = ""
    ts: float = 0.0
    v: int = EVENT_PROTOCOL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v": self.v,
            "type": self.type,
            "ts": self.ts or time.time(),
            "tree": self.tree,
            "data": self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskEvent":
        return cls(
            type=d.get("type", ""),
            data=d.get("data", {}) or {},
            tree=d.get("tree", ""),
            ts=d.get("ts", 0.0),
            v=d.get("v", EVENT_PROTOCOL_VERSION),
        )


# 兼容 JSON 字面量 (dict) 入参: publish 时若为 dict 自动包成 TaskEvent
def _coerce(event: Any) -> TaskEvent:
    if isinstance(event, TaskEvent):
        return event
    if isinstance(event, dict):
        return TaskEvent.from_dict(event)
    raise TypeError(f"event must be TaskEvent or dict, got {type(event)!r}")


Handler = Callable[[TaskEvent], None]


class EventBus:
    """线程安全的进程内事件总线。

    - ``subscribe(handler)`` → 返回退订 callable (幂等, 重复调用安全)。
    - ``publish(event)`` → 广播给所有订阅者; handler 抛异常被吞掉并继续。
    - ``clear()`` → 清空所有订阅者 (测试用)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: List[Handler] = []

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._handlers.remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, event: Any) -> None:
        ev = _coerce(event)
        with self._lock:
            handlers = list(self._handlers)
        for h in handlers:
            try:
                h(ev)
            except Exception:  # 单个订阅者故障不应阻断其他订阅者
                pass

    def __len__(self) -> int:
        with self._lock:
            return len(self._handlers)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


# ── 便捷构造器 ──────────────────────────────────────────────
def exec_state_event(
    tree_key: str,
    node_id: str,
    state: str,
    progress: Optional[float] = None,
    detail: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> TaskEvent:
    """构造一条节点执行状态变更事件 (最常用事件)。"""
    data: Dict[str, Any] = {"node_id": node_id, "exec_state": state}
    if progress is not None:
        data["progress"] = progress
    if detail is not None:
        data["detail"] = detail
    if extra:
        data.update(extra)
    return TaskEvent(type=EVENT_NODE_EXEC_STATE, tree=tree_key, data=data)
