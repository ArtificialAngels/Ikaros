"""B4 — CodingAgentSupervisor 单元测试（FakeClient，无需真实 herdr）。

用 FakeClient 模拟 herdr 的关键序列：
- blocked -> 批准 -> done（无/有 approval_cb 两条路径）
- 直接 done（agent 不阻塞）
- DisallowedKind / 未知节点 报错
"""

import os
import tempfile

from herdr.supervisor import (
    CodingAgentSupervisor,
    DisallowedKindError,
    NeedsApproval,
    SupervisorError,
    SupervisorResult,
    SupervisorTask,
)
from herdr.session import SessionRegistry


class FakeTree:
    """最小对话树桩：记录 exec_state 跳变。"""

    def __init__(self, valid=("n1",)):
        self.persist_key = "ui_test_tree"
        self.valid = set(valid)
        self.states = {}          # node_id -> 当前 exec_state
        self.transitions = []     # (node_id, state, detail)

    def get_node(self, node_id):
        return {"id": node_id} if node_id in self.valid else None

    def set_exec_state(self, node_id, state, progress=None, detail=None, meta=None, **kw):
        if node_id not in self.valid:
            raise KeyError(f"node not found: {node_id}")
        self.states[node_id] = state
        self.transitions.append((node_id, state, detail))
        return {"id": node_id}


class FakeClient:
    """模拟 herdr server 的关键方法。"""

    def __init__(self):
        self.prompts = 0
        self.waits = 0
        self.sent = []            # pane_send_text 收到的决策
        self.reads = 0
        self.cwd_used = None

    def ping(self):
        return {"protocol": 17}

    def workspace_create(self, cwd=None, label=None):
        self.cwd_used = cwd
        return {"workspace_id": "w1", "root_pane": {"pane_id": "w1:p1"}}

    def agent_start(self, name, kind, pane_id, timeout_s=30):
        return {"name": name, "kind": kind, "pane_id": pane_id, "started": True}

    def agent_prompt(self, target, text, wait=True, until="idle", timeout_ms=120000):
        self.prompts += 1
        # 首次 prompt 返回 blocked；后续（理论不会再到这）返回 done
        if self.prompts == 1:
            return {"type": "agent_prompt", "state": "blocked"}
        return {"type": "agent_prompt", "state": "done"}

    def agent_wait(self, target, until="done", timeout_ms=120000):
        self.waits += 1
        return {"type": "agent_wait", "state": "done"}

    def pane_send_text(self, pane_id, text):
        self.sent.append(text)
        return {"sent": True}

    def pane_read(self, pane_id, source="recent_unwrapped", lines=120):
        self.reads += 1
        # 第一次读（blocked 上下文）；第二次读（最终输出）
        if self.reads == 1:
            return {"text": "agent: 需要批准才能继续。是否继续？(y/n)"}
        return {"text": "DONE: 已修改 src/app.py 并跑通测试。"}

    def session_snapshot(self):
        return {"type": "session_snapshot", "snapshot": {"panes": []}}


def _sup(tree, client, allowed=("aider", "claude")):
    reg = SessionRegistry(os.path.join(tempfile.mkdtemp(), "bindings.json"))
    return CodingAgentSupervisor(
        tree, registry=reg, client=client,
        attach_bridge=False, allowed_kinds=allowed,
    )


def test_blocked_then_approve():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    sup = _sup(tree, client)
    # 无 approval_cb -> 抛 NeedsApproval
    try:
        sup.run_task(SupervisorTask(task="改 app.py", kind="aider", node_id="n1"))
        assert False, "应抛出 NeedsApproval"
    except NeedsApproval as na:
        assert na.node_id == "n1"
        assert "需要批准" in na.prompt
    # 节点应处于 blocked
    assert tree.states["n1"] == "blocked"
    # 提供决策继续
    res = sup.approve("n1", "yes, continue")
    assert isinstance(res, SupervisorResult)
    assert res.ok is True
    assert res.state == "done"
    assert "已修改 src/app.py" in res.output
    # 决策被发送（带回车）
    assert client.sent and client.sent[0].endswith("\r\n")
    # 终态 done
    assert tree.states["n1"] == "done"


def test_inline_approval_cb():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    sup = _sup(tree, client)
    captured = {}

    def cb(na):
        captured["prompt"] = na.prompt
        return "approved inline"

    res = sup.run_task(SupervisorTask(
        task="改 app.py", kind="aider", node_id="n1", approval_cb=cb,
    ))
    assert res.ok is True
    assert res.state == "done"
    assert captured.get("prompt")
    assert client.sent and "approved inline" in client.sent[0]
    assert tree.states["n1"] == "done"


def test_direct_done_no_block():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    # 让首次 prompt 直接返回 done
    client.agent_prompt = lambda *a, **k: {"type": "agent_prompt", "state": "done"}
    sup = _sup(tree, client)
    res = sup.run_task(SupervisorTask(task="t", kind="aider", node_id="n1"))
    assert res.ok is True
    assert res.state == "done"
    assert tree.states["n1"] == "done"


def test_disallowed_kind():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    sup = _sup(tree, client, allowed=("aider", "claude"))
    try:
        sup.run_task(SupervisorTask(task="t", kind="rm-rf", node_id="n1"))
        assert False, "应抛 DisallowedKindError"
    except DisallowedKindError:
        pass


def test_unknown_node():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    sup = _sup(tree, client)
    try:
        sup.run_task(SupervisorTask(task="t", kind="aider", node_id="nope"))
        assert False, "应抛 SupervisorError"
    except SupervisorError:
        pass


def test_state_transition_sequence_blocked():
    tree = FakeTree(valid=("n1",))
    client = FakeClient()
    sup = _sup(tree, client)
    try:
        sup.run_task(SupervisorTask(task="t", kind="aider", node_id="n1"))
    except NeedsApproval:
        pass
    states = [s for (nid, s, d) in tree.transitions if nid == "n1"]
    # pending -> working -> working -> blocked（至少含这些跳变）
    assert "pending" in states
    assert "blocked" in states
    assert states.index("pending") < states.index("blocked")
