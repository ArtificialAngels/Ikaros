"""Ikaros <-> herdr 深度集成包（Path B：二进制引擎 + 原生模型）。

本包不把 herdr 当黑盒 CLI 调用，而是直接对接它的 Socket API：
一行一请求的 NDJSON 协议（{"id","method","params"} -> {"id","result"|"error"}），
以及订阅后的长连接事件推送。Python 侧实现请求/响应 id 关联与事件分发，
作为 Ikaros 任务事件总线的底层传输，向上对接 conversation-tree 的 exec_state。
"""

from .client import (
    HerdrClient,
    HerdrError,
    HerdrProtocolError,
    HerdrTimeout,
    StreamTransport,
    resolve_socket_path,
)
from .session import (
    SessionBinding,
    WorkspaceBinding,
    SessionRegistry,
    SessionBridge,
    map_agent_status,
)
from .supervisor import (
    CodingAgentSupervisor,
    SupervisorTask,
    SupervisorResult,
    SupervisorError,
    HerdrUnavailable,
    DisallowedKindError,
    NeedsApproval,
    SupervisorTimeout,
)

__all__ = [
    "HerdrClient",
    "HerdrError",
    "HerdrProtocolError",
    "HerdrTimeout",
    "StreamTransport",
    "resolve_socket_path",
    "SessionBinding",
    "WorkspaceBinding",
    "SessionRegistry",
    "SessionBridge",
    "map_agent_status",
    "CodingAgentSupervisor",
    "SupervisorTask",
    "SupervisorResult",
    "SupervisorError",
    "HerdrUnavailable",
    "DisallowedKindError",
    "NeedsApproval",
    "SupervisorTimeout",
]
