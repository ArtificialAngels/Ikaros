"""B4 — CodingAgentSupervisor（编排核心，L2 调用点）。

把「在 herdr pane 里跑一个外部 coding agent、监督其生命周期、回收结果」这件事
封装成一个状态机。借鉴 herdr 的 agent 状态机（SKILL.md）：

    agent.start <kind>   -> 阻塞到检测到目标 agent 就绪（默认 30s）
    agent.prompt <task> --wait[blocked,done]  -> 等待首个稳定态
    [blocked] -> approval_cb(上下文) -> 用户批准 -> 发送决策继续
    [done]    -> pane.read 回收输出

对应设计文档 docs/herdr-integration-design.md §11 / §12（B4）。

关键约定（来自 B1/B3 实战坑）：
- workspace.create 响应里取 ``root_pane.pane_id``，不要依赖 list/snapshot 回查。
- pane.split 必须先在 create 后 workspace.focus(wid)（本版单 pane 不需要 split）。
- 节点 exec_state 由 supervisor 权威驱动；SessionBridge 事件流做 reattach 冗余。
- ``blocked`` 必须人工确认后才继续——绝不在无 approval_cb 时自动 send_keys 绕过批准。

依赖：``HerdrClient``（B1）、``SessionRegistry``/``SessionBridge``（B3）。
不依赖真实 herdr 二进制即可单测（用 FakeClient）。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .client import HerdrClient, HerdrError, HerdrProtocolError, HerdrTimeout
from .session import SessionBridge, SessionRegistry, map_agent_status

# 默认允许的外部 agent 种类（安全边界：仅启动已知 coding agent）。
# 传 allowed_kinds=None 可放开（需调用方自己负责）。
DEFAULT_ALLOWED_KINDS = (
    "aider", "claude", "cursor", "codex", "hermes", "opencode", "gemini",
    "qwen", "copilot", "cline", "roo",
)

# agent 状态（herdr 语义）
_AGENT_BLOCKED = "blocked"
_AGENT_DONE = "done"
_AGENT_WORKING = "working"


class SupervisorError(Exception):
    """supervisor 基类异常。"""


class HerdrUnavailable(SupervisorError):
    """herdr server 不可达。"""


class DisallowedKindError(SupervisorError):
    """kind 不在白名单（避免任意命令执行）。"""


class NeedsApproval(SupervisorError):
    """agent 进入 blocked，需调用方提供决策后调 ``approve()`` 继续。

    ``prompt`` 为 agent 当前阻塞处的上下文（通常是它提出的问题）。
    """

    def __init__(self, node_id: str, workspace_id: str, pane_id: str, prompt: str = ""):
        self.node_id = node_id
        self.workspace_id = workspace_id
        self.pane_id = pane_id
        self.prompt = prompt
        super().__init__(
            f"agent blocked on node {node_id} (pane {pane_id}): {prompt[:160]}"
        )


class SupervisorTimeout(SupervisorError):
    """等待 agent 稳定态超时。"""


@dataclass
class SupervisorTask:
    """一次 supervisor 调度请求。"""

    task: str                                   # 下发给外部 agent 的自然语言任务
    kind: str                                   # 外部 agent 种类 (aider/claude/...)
    node_id: str                                # 在对话树中锚定的节点 id
    cwd: Optional[str] = None                   # 仓库目录（agent 运行处）
    label: Optional[str] = None                 # workspace 标签
    timeout_s: int = 600                        # 单个等待阶段超时（秒）
    approval_cb: Optional[Callable[["NeedsApproval"], str]] = None
    # 结果回写钩子（可选）：orchestrator 用它把输出写进 V5 记忆
    on_result: Optional[Callable[["SupervisorResult"], None]] = None


@dataclass
class SupervisorResult:
    """一次 supervisor 调度的结果。"""

    ok: bool
    node_id: str
    workspace_id: str
    pane_id: str
    state: str                                  # 终态（done/unknown/...）
    output: str = ""
    error: Optional[str] = None
    agent_name: str = ""


def _res_state(res: Any) -> Optional[str]:
    """从 agent.prompt / agent.wait 响应里抽取 agent 状态（容错多字段）。"""
    if isinstance(res, dict):
        for k in ("state", "status", "agent_status", "agent_state"):
            v = res.get(k)
            if v:
                return str(v)
        # 嵌套（如 {"agent":{"status":...}}）
        ag = res.get("agent")
        if isinstance(ag, dict):
            s = ag.get("status") or ag.get("state")
            if s:
                return str(s)
    return None


class CodingAgentSupervisor:
    """在 herdr pane 里跑外部 coding agent 并监督其生命周期。

    典型用法::

        sup = CodingAgentSupervisor(tree, registry)
        res = sup.run_task(SupervisorTask(task="...", kind="aider",
                                          node_id="n_xxx", cwd="E:/repo"))
        # res.output 即回收的 agent 输出
    """

    def __init__(
        self,
        tree: Any,
        registry: Optional[SessionRegistry] = None,
        client: Optional[HerdrClient] = None,
        attach_bridge: bool = True,
        allowed_kinds: Optional[tuple] = DEFAULT_ALLOWED_KINDS,
        ikaros_session: Optional[str] = None,
    ):
        self.tree = tree
        self.registry = registry or SessionRegistry()
        self.client = client
        self.attach_bridge = attach_bridge
        self.allowed_kinds = allowed_kinds
        self.ikaros_session = ikaros_session or getattr(
            tree, "persist_key", "ui_conversation_tree"
        )
        # node_id -> (workspace_id, pane_id, agent_name)
        self._active: Dict[str, tuple] = {}
        self._lock = threading.RLock()

    # -- 连接 --------------------------------------------------------------- #
    def _ensure_client(self) -> HerdrClient:
        if self.client is None:
            self.client = HerdrClient()
        # 探活
        try:
            self.client.ping()
        except Exception as exc:  # noqa: BLE001
            raise HerdrUnavailable(f"herdr server 不可达: {exc}") from exc
        return self.client

    # -- 对外 API ----------------------------------------------------------- #
    def run_task(self, task: SupervisorTask) -> SupervisorResult:
        """调度一次 coding 任务，返回回收结果。

        若 agent 进入 blocked 且未提供 ``approval_cb``，抛出 ``NeedsApproval``
        （agent 仍停在 blocked；调用方 resolve 后调 ``approve(node_id, decision)``）。
        """
        client = self._ensure_client()
        node_id = task.node_id

        # 0. 校验
        if self.allowed_kinds is not None and task.kind not in self.allowed_kinds:
            raise DisallowedKindError(
                f"kind '{task.kind}' 不在白名单 {self.allowed_kinds}"
            )
        if self.tree.get_node(node_id) is None:
            raise SupervisorError(f"node not found: {node_id}")
        with self._lock:
            if node_id in self._active:
                raise SupervisorError(f"node {node_id} 已有进行中的任务")

        # 1. 绑定会话 + 建 workspace（任务批次）
        self.registry.bind_session(self.ikaros_session, herdr_socket=self._socket_of(client))
        self.tree.set_exec_state(node_id, "pending", detail=f"herdr {task.kind}: 建批次")
        try:
            ws = client.workspace_create(cwd=task.cwd, label=task.label)
            root_pane_id = self._root_pane(ws)
            ws_id = self._ws_id(ws) or f"ws-{uuid.uuid4().hex[:8]}"
        except Exception as exc:  # noqa: BLE001
            self.tree.set_exec_state(node_id, "unknown", detail=f"建批次失败: {exc}")
            raise HerdrError(f"workspace.create 失败: {exc}") from exc

        # 2. 绑定 workspace/pane -> 节点
        self.registry.bind_workspace(
            self.ikaros_session, ws_id, node_id,
            label=task.label, root_pane_id=root_pane_id,
        )
        # 3. 接桥（让外部观察者随 herdr 事件实时看到节点徽标）
        bridge = None
        if self.attach_bridge:
            try:
                bridge = SessionBridge(client, self.tree, self.registry, self.ikaros_session)
                bridge.attach()
            except Exception:  # noqa: BLE001
                bridge = None  # 桥失败不阻塞主流程

        agent_name = f"ikaros-{node_id[-6:]}" if len(node_id) >= 6 else f"ikaros-{uuid.uuid4().hex[:6]}"
        with self._lock:
            self._active[node_id] = (ws_id, root_pane_id, agent_name)

        try:
            # 4. agent.start（阻塞到 agent 就绪）
            self.tree.set_exec_state(node_id, "working", progress=0.1, detail=f"{task.kind} 启动")
            client.agent_start(agent_name, task.kind, root_pane_id)
            # 5. 下发任务，等待首个稳定态（blocked 或 done）
            self.tree.set_exec_state(node_id, "working", progress=0.2, detail="下发任务")
            res = client.agent_prompt(
                root_pane_id, task.task, wait=True,
                until=[_AGENT_BLOCKED, _AGENT_DONE],
                timeout_ms=task.timeout_s * 1000,
            )
            state = map_agent_status(_res_state(res))
            if state == _AGENT_BLOCKED or state == "unknown" and self._is_blocked(client, root_pane_id):
                return self._handle_blocked(task, ws_id, root_pane_id, agent_name, node_id, bridge)
            if state == _AGENT_DONE:
                return self._finish(task, ws_id, root_pane_id, agent_name, node_id, bridge)
            # 其他（working/idle）-> 继续等 done
            self.tree.set_exec_state(node_id, "working", progress=0.5, detail="agent 运行中")
            client.agent_wait(root_pane_id, until=_AGENT_DONE, timeout_ms=task.timeout_s * 1000)
            return self._finish(task, ws_id, root_pane_id, agent_name, node_id, bridge)
        except HerdrTimeout:
            self.tree.set_exec_state(node_id, "unknown", detail="等待 agent 超时")
            self._clear(node_id)
            raise SupervisorTimeout(f"node {node_id}: 等待 agent 稳定态超时")
        except NeedsApproval:
            # 保持 blocked 状态，直接上抛给调用方 resolve（不覆盖）
            raise
        except Exception as exc:  # noqa: BLE001
            self.tree.set_exec_state(node_id, "unknown", detail=f"调度异常: {exc}")
            self._clear(node_id)
            raise

    def approve(self, node_id: str, decision: str) -> SupervisorResult:
        """为一个停在 blocked 的任务提供决策并继续等待 done。

        通常在捕获 ``run_task`` 抛出的 ``NeedsApproval`` 后调用。
        """
        client = self._ensure_client()
        with self._lock:
            info = self._active.get(node_id)
        if info is None:
            raise SupervisorError(f"node {node_id} 没有进行中且可继续的任务")
        ws_id, pane_id, agent_name = info
        # 发送决策（文本 + 回车，等价于用户在 agent 提示符下作答）
        self.tree.set_exec_state(node_id, "working", progress=0.6, detail="已批准，继续")
        try:
            client.pane_send_text(pane_id, decision + "\r\n")
        except Exception as exc:  # noqa: BLE001
            self.tree.set_exec_state(node_id, "unknown", detail=f"发送决策失败: {exc}")
            raise HerdrError(f"发送批准决策失败: {exc}") from exc
        task_timeout = 600
        try:
            client.agent_wait(pane_id, until=_AGENT_DONE, timeout_ms=task_timeout * 1000)
        except HerdrTimeout:
            self.tree.set_exec_state(node_id, "unknown", detail="批准后等待 done 超时")
            self._clear(node_id)
            raise SupervisorTimeout(f"node {node_id}: 批准后等待 done 超时")
        return self._finish_from(node_id, ws_id, pane_id, agent_name)

    def cancel(self, node_id: str) -> bool:
        """取消进行中的任务（不强制杀 agent，仅清本地状态 + 置 unknown）。

        真正杀 herdr agent/pane 由调用方按需处理（保留 herdr 的 detach 语义）。
        """
        with self._lock:
            info = self._active.pop(node_id, None)
        if info is None:
            return False
        try:
            self.tree.set_exec_state(node_id, "unknown", detail="已取消")
        except Exception:  # noqa: BLE001
            pass
        return True

    # -- 内部 --------------------------------------------------------------- #
    def _handle_blocked(self, task, ws_id, pane_id, agent_name, node_id, bridge):
        self.tree.set_exec_state(node_id, "blocked", detail="等待用户批准")
        if task.approval_cb is not None:
            ctx = self._read_context(pane_id)
            na = NeedsApproval(node_id, ws_id, pane_id, prompt=ctx)
            decision = task.approval_cb(na)
            # 内联批准并继续
            self.tree.set_exec_state(node_id, "working", progress=0.6, detail="已批准，继续")
            try:
                self.client.pane_send_text(pane_id, str(decision) + "\r\n")
            except Exception as exc:  # noqa: BLE001
                self.tree.set_exec_state(node_id, "unknown", detail=f"发送决策失败: {exc}")
                raise HerdrError(f"发送批准决策失败: {exc}") from exc
            self.client.agent_wait(pane_id, until=_AGENT_DONE, timeout_ms=task.timeout_s * 1000)
            return self._finish(task, ws_id, pane_id, agent_name, node_id, bridge)
        # 无 approval_cb -> 抛出让调用方 resolve
        raise NeedsApproval(node_id, ws_id, pane_id, prompt=self._read_context(pane_id))

    def _finish(self, task, ws_id, pane_id, agent_name, node_id, bridge):
        output = self._read_context(pane_id)
        self.tree.set_exec_state(
            node_id, "done", progress=1.0,
            detail=(output[:200] if output else "done"),
            meta={"herdr_output": output, "workspace_id": ws_id, "agent": agent_name},
        )
        self._clear(node_id)
        res = SupervisorResult(
            ok=True, node_id=node_id, workspace_id=ws_id, pane_id=pane_id,
            state="done", output=output, agent_name=agent_name,
        )
        if task.on_result is not None:
            try:
                task.on_result(res)
            except Exception:  # noqa: BLE001
                pass
        return res

    def _finish_from(self, node_id, ws_id, pane_id, agent_name):
        output = self._read_context(pane_id)
        self.tree.set_exec_state(
            node_id, "done", progress=1.0,
            detail=(output[:200] if output else "done"),
            meta={"herdr_output": output, "workspace_id": ws_id, "agent": agent_name},
        )
        self._clear(node_id)
        return SupervisorResult(
            ok=True, node_id=node_id, workspace_id=ws_id, pane_id=pane_id,
            state="done", output=output, agent_name=agent_name,
        )

    def _read_context(self, pane_id: str) -> str:
        try:
            rd = self.client.pane_read(pane_id, lines=120)
            if isinstance(rd, dict):
                return rd.get("text", "") or ""
            return str(rd)
        except Exception:  # noqa: BLE001
            return ""

    def _is_blocked(self, client, pane_id: str) -> bool:
        """fallback：agent.prompt 返回 unknown 时，用 snapshot 确认是否真的 blocked。"""
        try:
            snap = client.session_snapshot()
            root = snap.get("snapshot", snap) if isinstance(snap, dict) else {}
            for pane in (root.get("panes") or []):
                if (pane.get("pane_id") or pane.get("id")) == pane_id:
                    return map_agent_status(pane.get("agent_status")) == _AGENT_BLOCKED
        except Exception:  # noqa: BLE001
            return False
        return False

    def _clear(self, node_id: str) -> None:
        with self._lock:
            self._active.pop(node_id, None)

    @staticmethod
    def _socket_of(client: HerdrClient) -> str:
        try:
            return getattr(client, "socket_path", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _root_pane(ws: Any) -> str:
        if not isinstance(ws, dict):
            return ""
        # 真实响应: 顶层 "root_pane": {"pane_id": "wX:pY"}
        rp = ws.get("root_pane")
        if isinstance(rp, dict):
            return rp.get("pane_id") or rp.get("id") or ""
        if isinstance(rp, str):
            return rp
        # 兼容: workspace.root_pane_id
        w = ws.get("workspace")
        if isinstance(w, dict):
            rpid = w.get("root_pane_id")
            if rpid:
                return rpid
        return ""

    @staticmethod
    def _ws_id(ws: Any) -> str:
        if not isinstance(ws, dict):
            return ""
        # 真实响应: workspace_id 嵌套在 ws["workspace"]["workspace_id"]
        w = ws.get("workspace")
        if isinstance(w, dict):
            wid = w.get("workspace_id") or w.get("id")
            if wid:
                return wid
        # 兼容扁平
        return ws.get("workspace_id") or ws.get("id") or ""
