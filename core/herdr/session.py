"""B3 — herdr session ↔ Ikaros conversation session / 任务批次 对齐。

借鉴 herdr 的「server = 唯一真相 + 客户端 detach/reattach」模式（见 docs/herdr-
integration-design.md §11）：

- ``SessionRegistry``：持久化 **herdr 会话**（命名 session / socket 路径）与
  **Ikaros 对话会话**（`ConversationTree.persist_key`，如 ``ui_conversation_tree``）
  以及 **任务批次**（herdr ``workspace_id``）三者之间的映射，重启后可重建连接。
- ``SessionBridge``：把一条 ``HerdrClient`` 接到一个 ``ConversationTree``——先
  ``session.snapshot`` 引导本地节点状态，再 ``events.subscribe`` 增量同步，将 herdr
  的 pane/agent 语义状态（idle/working/blocked/done/unknown）映射到 Ikaros 节点的
  ``exec_state``。对应 §11.2 概念映射（workspace/tab → 任务批次；agent 状态 → 节点徽标）。

模块不依赖真实 herdr 二进制：``SessionBridge`` 只依赖 ``HerdrClient`` 的接口
（``session_snapshot`` / ``subscribe`` / ``close``），测试用 mock 即可。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .client import HerdrClient, resolve_socket_path

# Ikaros 节点执行状态（与 conversation_tree.EXEC_STATES 对齐）
IKAROS_EXEC_STATES = ("idle", "pending", "working", "blocked", "done", "unknown")

# herdr agent/pane 语义状态 -> Ikaros exec_state（容错，未知归 unknown）
_AGENT_STATUS_MAP = {
    "idle": "idle",
    "pending": "pending",
    "queued": "pending",
    "working": "working",
    "running": "working",
    "busy": "working",
    "blocked": "blocked",
    "waiting": "blocked",
    "needs_input": "blocked",
    "needs-input": "blocked",
    "done": "done",
    "finished": "done",
    "exited": "done",
    "complete": "done",
    "completed": "done",
    "unknown": "unknown",
    "error": "unknown",
    "failed": "unknown",
}


def map_agent_status(status: Any) -> str:
    """把 herdr agent/pane 状态归一为 Ikaros exec_state（容错）。"""
    if not status:
        return "unknown"
    return _AGENT_STATUS_MAP.get(str(status).strip().lower(), "unknown")


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass
class WorkspaceBinding:
    """一个 herdr workspace（= 一次「任务批次」）↔ Ikaros 树节点。"""

    workspace_id: str
    node_id: str                     # 该任务批次在对话树中锚定的节点
    label: Optional[str] = None
    root_pane_id: Optional[str] = None
    panes: Dict[str, str] = field(default_factory=dict)  # pane_id -> node_id（子分支）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "node_id": self.node_id,
            "label": self.label,
            "root_pane_id": self.root_pane_id,
            "panes": dict(self.panes),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkspaceBinding":
        return cls(
            workspace_id=d["workspace_id"],
            node_id=d["node_id"],
            label=d.get("label"),
            root_pane_id=d.get("root_pane_id"),
            panes=dict(d.get("panes", {}) or {}),
        )


@dataclass
class SessionBinding:
    """一组 herdr 会话 ↔ Ikaros 对话会话的绑定。"""

    ikaros_session: str              # ConversationTree.persist_key，如 "ui_conversation_tree"
    herdr_session: str = ""          # herdr --session 名（"" = 默认会话）
    herdr_socket: str = ""           # 显式记录的 socket 路径（"" => 用优先级解析）
    workspaces: Dict[str, WorkspaceBinding] = field(default_factory=dict)
    created_at: float = 0.0
    last_seen: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ikaros_session": self.ikaros_session,
            "herdr_session": self.herdr_session,
            "herdr_socket": self.herdr_socket,
            "workspaces": {wid: wb.to_dict() for wid, wb in self.workspaces.items()},
            "created_at": self.created_at,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionBinding":
        return cls(
            ikaros_session=d["ikaros_session"],
            herdr_session=d.get("herdr_session", ""),
            herdr_socket=d.get("herdr_socket", ""),
            workspaces={
                wid: WorkspaceBinding.from_dict(wb)
                for wid, wb in (d.get("workspaces") or {}).items()
            },
            created_at=d.get("created_at", 0.0),
            last_seen=d.get("last_seen", 0.0),
        )


# --------------------------------------------------------------------------- #
# 注册表（JSON 持久化，线程安全）
# --------------------------------------------------------------------------- #
class SessionRegistry:
    """持久化 herdr 会话 ↔ Ikaros 对话会话 ↔ 任务批次 的映射。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "session_bindings.json"
        )
        self._lock = threading.RLock()
        self._sessions: Dict[str, SessionBinding] = {}
        self._load()

    # -- 持久化 ------------------------------------------------------------- #
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._sessions = {
                k: SessionBinding.from_dict(v)
                for k, v in (data.get("sessions") or {}).items()
            }
        except (FileNotFoundError, json.JSONDecodeError):
            self._sessions = {}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"version": 1, "sessions": {k: s.to_dict() for k, s in self._sessions.items()}},
                f,
                ensure_ascii=False,
                indent=2,
            )
        os.replace(tmp, self.path)  # 原子替换

    # -- 会话级 ------------------------------------------------------------- #
    def bind_session(
        self, ikaros_session: str, herdr_socket: str = "", herdr_session: str = ""
    ) -> SessionBinding:
        with self._lock:
            b = self._sessions.get(ikaros_session)
            if b is None:
                b = SessionBinding(ikaros_session=ikaros_session, created_at=time.time())
            if herdr_socket:
                b.herdr_socket = herdr_socket
            if herdr_session:
                b.herdr_session = herdr_session
            b.last_seen = time.time()
            self._sessions[ikaros_session] = b
            self._save()
            return b

    def lookup_session(self, ikaros_session: str) -> Optional[SessionBinding]:
        with self._lock:
            return self._sessions.get(ikaros_session)

    def list_sessions(self) -> List[SessionBinding]:
        with self._lock:
            return list(self._sessions.values())

    def unbind_session(self, ikaros_session: str) -> bool:
        with self._lock:
            if ikaros_session in self._sessions:
                del self._sessions[ikaros_session]
                self._save()
                return True
            return False

    def touch(self, ikaros_session: str) -> None:
        with self._lock:
            b = self._sessions.get(ikaros_session)
            if b:
                b.last_seen = time.time()
                self._save()

    def resolve_socket(self, ikaros_session: str) -> str:
        """解析该 Ikaros 会话对应的 herdr socket（优先用绑定记录，否则走优先级解析）。"""
        with self._lock:
            b = self._sessions.get(ikaros_session)
        if b and b.herdr_socket:
            return b.herdr_socket
        return resolve_socket_path()

    # -- workspace / 任务批次 级 ------------------------------------------- #
    def bind_workspace(
        self,
        ikaros_session: str,
        workspace_id: str,
        node_id: str,
        label: Optional[str] = None,
        root_pane_id: Optional[str] = None,
    ) -> WorkspaceBinding:
        with self._lock:
            b = self._sessions.get(ikaros_session) or self.bind_session(ikaros_session)
            wb = b.workspaces.get(workspace_id)
            if wb is None:
                wb = WorkspaceBinding(workspace_id=workspace_id, node_id=node_id)
                b.workspaces[workspace_id] = wb
            wb.node_id = node_id
            if label is not None:
                wb.label = label
            if root_pane_id is not None:
                wb.root_pane_id = root_pane_id
            self._save()
            return wb

    def bind_pane(
        self, ikaros_session: str, workspace_id: str, pane_id: str, node_id: str
    ) -> Optional[WorkspaceBinding]:
        with self._lock:
            b = self._sessions.get(ikaros_session)
            if b is None:
                return None
            wb = b.workspaces.get(workspace_id)
            if wb is None:
                return None
            wb.panes[pane_id] = node_id
            self._save()
            return wb

    def lookup_by_workspace(self, workspace_id: str) -> Optional[Tuple[str, WorkspaceBinding]]:
        with self._lock:
            for sid, b in self._sessions.items():
                if workspace_id in b.workspaces:
                    return sid, b.workspaces[workspace_id]
        return None

    def lookup_by_pane(self, pane_id: str) -> Optional[Tuple[str, str, str]]:
        """返回 (ikaros_session, workspace_id, node_id)。"""
        with self._lock:
            for sid, b in self._sessions.items():
                for wid, wb in b.workspaces.items():
                    if pane_id in wb.panes:
                        return sid, wid, wb.panes[pane_id]
        return None


# --------------------------------------------------------------------------- #
# 重连桥（snapshot + subscribe）
# --------------------------------------------------------------------------- #
class SessionBridge:
    """把一条 HerdrClient 接到一个 ConversationTree，做 snapshot 引导 + subscribe 增量同步。"""

    def __init__(
        self,
        client: HerdrClient,
        tree: Any,
        registry: Optional[SessionRegistry] = None,
        ikaros_session: Optional[str] = None,
    ):
        self.client = client
        self.tree = tree
        self.ikaros_session = ikaros_session or getattr(tree, "persist_key", "ui_conversation_tree")
        self.registry = registry
        self._subscribed = False

    # -- 引导: session.snapshot -> 节点 exec_state ------------------------- #
    def resync(self) -> Dict[str, str]:
        """拉取 session.snapshot 并把 agent 状态写入对应节点。返回 {node_id: exec_state}。"""
        snap = self.client.session_snapshot()
        return self._apply_snapshot(snap)

    def _apply_snapshot(self, snap: Any) -> Dict[str, str]:
        """把 session.snapshot 的状态写入对应节点。

        真实 herdr snapshot 结构（协议 17）：
          {"type":"session_snapshot","snapshot":{
             "workspaces":[{"workspace_id","label","agent_status",...}],
             "tabs":[{"tab_id","workspace_id","agent_status",...}],
             "panes":[FLAT {"pane_id","workspace_id","agent_status","cwd",...}],
             "agents":[FLAT {"pane_id","status",...}]
          }}
        panes/agents 是扁平列表，pane 自带 workspace_id；workspaces 顶层带 agent_status。
        """
        root = _dig(snap, "snapshot") or snap
        applied: Dict[str, str] = {}
        # workspace 级
        for ws in (root.get("workspaces") or []):
            ws_id = ws.get("workspace_id") or ws.get("id")
            if not ws_id:
                continue
            wb = self._workspace_binding(ws_id)
            st = _agent_status_of(ws)
            # unknown = herdr 无有效 agent 信号，快照引导时跳过（避免无谓覆盖节点）
            if wb is not None and st and map_agent_status(st) != "unknown":
                self._set(wb.node_id, st, detail=f"herdr ws {ws_id}")
                applied[wb.node_id] = st
        # pane 级（扁平列表，每个 pane 自带 workspace_id）
        for pane in (root.get("panes") or []):
            pane_id = pane.get("pane_id") or pane.get("id")
            ws_id = pane.get("workspace_id")
            st = _agent_status_of(pane)
            if not st or map_agent_status(st) == "unknown":
                continue
            node_id = self._resolve_node(ws_id, pane_id)
            if node_id:
                self._set(node_id, st, detail=f"herdr pane {pane_id}")
                applied[node_id] = st
        # agent 级（扁平列表，引用 pane_id）
        for ag in (root.get("agents") or []):
            pane_id = ag.get("pane_id")
            st = ag.get("status") or ag.get("agent_status")
            if not st or map_agent_status(st) == "unknown":
                continue
            node_id = self._resolve_node(ag.get("workspace_id"), pane_id)
            if node_id:
                self._set(node_id, st, detail=f"herdr agent {pane_id}")
                applied[node_id] = st
        return applied

    # -- 增量: events.subscribe -------------------------------------------- #
    def attach(self, subscriptions: Optional[list] = None) -> "SessionBridge":
        """snapshot 引导 + 订阅增量事件。subscription 形状透传给 client.subscribe。"""
        self.resync()
        # 真实 herdr 订阅格式：[] = 订阅全部事件（已对运行 server 验证）；
        # 精确按资源订阅需 {"type":"pane"} 等 internally-tagged 变体（B4 再细化）。
        subs = subscriptions if subscriptions is not None else []
        self.client.subscribe(subs, handler=self._on_event)
        self._subscribed = True
        if self.registry is not None:
            self.registry.touch(self.ikaros_session)
        return self

    def detach(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self._subscribed = False

    # -- 内部 --------------------------------------------------------------- #
    def _workspace_binding(self, ws_id: str) -> Optional[WorkspaceBinding]:
        if self.registry is not None:
            r = self.registry.lookup_by_workspace(ws_id)
            if r is not None:
                return r[1]
        return None

    def _resolve_node(self, ws_id: str, pane_id: Optional[str]) -> Optional[str]:
        if self.registry is None:
            return None
        if pane_id:
            p = self.registry.lookup_by_pane(pane_id)
            if p is not None:
                return p[2]
        w = self.registry.lookup_by_workspace(ws_id)
        if w is not None:
            return w[1].node_id
        return None

    def _set(self, node_id: str, status: Any, detail: Optional[str] = None) -> None:
        try:
            self.tree.set_exec_state(node_id, map_agent_status(status), detail=detail)
        except Exception:
            # 节点不存在 / 状态非法 -> 跳过，不阻塞整体同步
            pass

    def _on_event(self, msg: Any) -> None:
        ev = _parse_event(msg)
        if ev is None:
            return
        etype, data = ev
        ws_id = _dig(data, "workspace_id") or _dig(data, "workspace", "id")
        pane_id = _dig(data, "pane_id") or _dig(data, "pane", "id")
        status = (
            _dig(data, "status")
            or _dig(data, "state")
            or _dig(data, "agent", "status")
        )
        if not status or (not ws_id and not pane_id):
            return
        node_id = self._resolve_node(ws_id, pane_id) if (ws_id or pane_id) else None
        if node_id:
            self._set(node_id, status, detail=f"event {etype}")


# --------------------------------------------------------------------------- #
# 容错解析（herdr snapshot / event 结构未与真实 server 强耦合，按常见形状 tolerant 处理）
# --------------------------------------------------------------------------- #
def _dig(d: Any, *keys: Any) -> Any:
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list):
            try:
                cur = cur[int(k)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def _agent_status_of(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None
    # herdr 真实字段是 agent_status（workspace/tab/pane 级）
    s = obj.get("agent_status") or obj.get("status") or obj.get("state")
    if s:
        return s
    agent = obj.get("agent")
    if isinstance(agent, dict):
        s = agent.get("agent_status") or agent.get("status") or agent.get("state")
        if s:
            return s
    if obj.get("running") is True:
        return "working"
    return None


def _parse_event(msg: Any) -> Optional[Tuple[str, dict]]:
    """解析 herdr 事件推送。支持两种常见形状：
    - {"id":"sub_..","method":"events","params":{"event":<type>,"data":{...}}}
    - {"method":"event","params":{"type":<type>,"payload":{...}}}
    """
    if not isinstance(msg, dict):
        return None
    params = msg.get("params")
    if not isinstance(params, dict):
        return None
    etype = params.get("event") or params.get("type")
    data = params.get("data") or params.get("payload") or {}
    if not etype:
        return None
    if not isinstance(data, dict):
        data = {}
    return etype, data


__all__ = [
    "SessionBinding",
    "WorkspaceBinding",
    "SessionRegistry",
    "SessionBridge",
    "map_agent_status",
    "IKAROS_EXEC_STATES",
]
