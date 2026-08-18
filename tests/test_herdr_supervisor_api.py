"""B5.5 — herdr supervisor 端点验收测试 (in-process :48920).

设计目标
--------
- 完全 hermetic：不依赖真实 herdr 二进制、不污染真实 V5 store。
- 通过 ``server._SUPERVISOR_OVERRIDE`` 注入 FakeSupervisor，驱动**真实**的
  ``ConversationTree.set_exec_state`` → 真实 ``EventBus`` → 真实 SSE 事件流。
- 核对设计文档 §11/§12：run→blocked(SSE)→approve→done(SSE) 闭环、
  CORS(跨域订阅)、OPTIONS 预检。

运行
----
    E:/Ikaros/runtime/portable-python/python.exe -m pytest tests/test_herdr_supervisor_api.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

# ── 让测试能 import memory_v5 / herdr / taskbus ───────────────────────────
CORE = str(Path(__file__).resolve().parent.parent / "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

# 以文件方式加载 conversation-tree/server.py（目录名带连字符，不能作为包名）
_SERVER_PATH = Path(CORE) / "conversation-tree" / "server.py"
_spec = importlib.util.spec_from_file_location("ct_server_under_test", str(_SERVER_PATH))
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

import memory_v5.conversation_tree as ct  # noqa: E402
from herdr import CodingAgentSupervisor, SupervisorTask, SupervisorResult  # noqa: E402


# ── 内存 tree 后端（避免触达真实 V5 store） ───────────────────────────────
class _MemStore:
    def __init__(self) -> None:
        self._data: dict[int, str] = {}
        self._seq = 0

    def store(self, messages, summary=None, **kw):
        self._seq += 1
        self._data[self._seq] = json.dumps(messages, ensure_ascii=False)
        return self._seq

    def load(self, ids):
        return {mid: self._data.get(mid, "") for mid in ids}

    def search(self, q, top_k=10):
        return [{"id": mid, "content": c} for mid, c in self._data.items()][:top_k]


# ── FakeSupervisor：只驱动真实 tree 的 exec_state，不走真实 herdr ─────────
class FakeSupervisor:
    def __init__(self, tree) -> None:
        self.tree = tree
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.last_result: SupervisorResult | None = None

    def run_task(self, task: SupervisorTask) -> SupervisorResult:
        nid = task.node_id
        ev = threading.Event()
        with self._lock:
            self._events[nid] = ev
        # 模拟 agent 进入 blocked
        self.tree.set_exec_state(nid, "blocked", detail="等待用户批准")
        # 阻塞等待 approve（或超时兜底，避免测试挂死）
        ev.wait(timeout=30)
        # 批准后继续 -> done
        self.tree.set_exec_state(
            nid, "done", progress=1.0, detail="fake done",
            meta={"herdr_output": "fake output", "workspace_id": "ws-fake",
                  "agent": "fake"},
        )
        res = SupervisorResult(
            ok=True, node_id=nid, workspace_id="ws-fake",
            pane_id="pane-fake", state="done",
            output="fake output", agent_name="fake",
        )
        self.last_result = res
        return res

    def approve(self, node_id: str, decision: str) -> SupervisorResult:
        with self._lock:
            ev = self._events.get(node_id)
        if ev is None:
            raise Exception(f"node {node_id} 没有进行中且可继续的任务")
        ev.set()  # 解除 run_task 的阻塞
        return SupervisorResult(
            ok=True, node_id=node_id, workspace_id="ws-fake",
            pane_id="pane-fake", state="done",
            output="fake output", agent_name="fake",
        )


# ── 测试上下文 ─────────────────────────────────────────────────────────────
class Ctx:
    def __init__(self) -> None:
        self.mem = _MemStore()
        self.tree = ct.ConversationTree(
            persist_key="test_ct_herdr",
            _store=self.mem.store, _load=self.mem.load, _search=self.mem.search,
        )
        self.tree.init([{"role": "system", "content": "herdr test tree"}])
        node = self.tree.add_turn([
            {"role": "user", "content": "run a coding task"},
            {"role": "assistant", "content": "ok"},
        ], branch_label="main", title="task node")
        self.node_id = node.id
        self.supervisor = FakeSupervisor(self.tree)

        # 注入到 server 模块全局（endpoint 通过 _get_supervisor() 取用）
        server._tree = self.tree
        server._retriever = ct.MemoryRetriever(self.tree)
        self.tree.event_bus = server._bus
        server._SUPERVISOR_OVERRIDE = self.supervisor

        # 启动 in-process HTTP server（临时端口）
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()

        # SSE 事件捕获
        self.captured: list[dict] = []
        self.cv = threading.Condition()
        def _on(ev):
            d = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
            with self.cv:
                self.captured.append(d)
                self.cv.notify_all()
        self._unsub = server._bus.subscribe(_on)

    def shutdown(self) -> None:
        try:
            self._unsub()
        except Exception:
            pass
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        server._SUPERVISOR_OVERRIDE = None

    def wait_for_state(self, nid: str, state: str, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            with self.cv:
                self.cv.wait(timeout=max(0.05, end - time.time()))
                for d in self.captured:
                    if (d.get("type") == "node.exec_state_changed"
                            and d.get("data", {}).get("node_id") == nid
                            and d.get("data", {}).get("exec_state") == state):
                        return True
        return False


def _http(ctx: Ctx, method: str, path: str, body: dict | None = None, timeout: float = 10.0):
    url = f"http://127.0.0.1:{ctx.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode("utf-8", "replace")
        return resp.status, dict(resp.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


@pytest.fixture
def ctx():
    c = Ctx()
    yield c
    c.shutdown()


# ── 验收用例 ──────────────────────────────────────────────────────────────
def test_cors_on_state(ctx: Ctx):
    """GET /api/state 必须带 CORS 头（跨域订阅）。"""
    st, hdrs, _ = _http(ctx, "GET", "/api/state")
    assert st == 200, f"state 返回 {st}"
    assert hdrs.get("Access-Control-Allow-Origin") == "*"


def test_options_preflight(ctx: Ctx):
    """OPTIONS 预检必须 204 + CORS 头。"""
    st, hdrs, _ = _http(ctx, "OPTIONS", "/api/supervisor/run")
    assert st == 204, f"OPTIONS 返回 {st}"
    assert hdrs.get("Access-Control-Allow-Origin") == "*"
    assert "POST" in (hdrs.get("Access-Control-Allow-Methods") or "")


def test_run_invalid_node_400(ctx: Ctx):
    """不存在的 node_id 必须 400。"""
    st, _, raw = _http(ctx, "POST", "/api/supervisor/run",
                       {"task": "x", "kind": "aider", "node_id": "nope"})
    assert st == 400, f"应 400，实际 {st}: {raw}"
    assert "node_id" in raw


def test_approve_without_active_400(ctx: Ctx):
    """对一个没有进行中任务的节点 approve 必须 400。"""
    st, _, raw = _http(ctx, "POST", "/api/supervisor/approve",
                       {"node_id": ctx.node_id, "decision": "yes"})
    assert st == 400, f"应 400，实际 {st}: {raw}"


def test_run_approve_closed_loop(ctx: Ctx):
    """run→blocked(SSE)→approve→done(SSE) 全闭环 + CORS。"""
    # 1) 派发任务
    st, hdrs, raw = _http(ctx, "POST", "/api/supervisor/run",
                          {"task": "refactor module X", "kind": "aider",
                           "node_id": ctx.node_id, "cwd": "E:/tmp"})
    assert st == 200, f"run 返回 {st}: {raw}"
    assert json.loads(raw).get("ok") is True
    assert hdrs.get("Access-Control-Allow-Origin") == "*"

    # 2) 等待 blocked 事件（经真实 EventBus）
    assert ctx.wait_for_state(ctx.node_id, "blocked"), "未收到 blocked 事件"

    # 3) 批准
    st2, _, raw2 = _http(ctx, "POST", "/api/supervisor/approve",
                         {"node_id": ctx.node_id, "decision": "yes"})
    assert st2 == 200, f"approve 返回 {st2}: {raw2}"
    rd = json.loads(raw2)
    assert rd.get("ok") is True
    assert rd.get("result", {}).get("state") == "done"

    # 4) 等待 done 事件
    assert ctx.wait_for_state(ctx.node_id, "done"), "未收到 done 事件"

    # 5) 终态校验
    assert ctx.tree.get_node(ctx.node_id).exec_state == "done"


def test_sse_endpoint(ctx: Ctx):
    """GET /api/events 必须是 text/event-stream + CORS + hello 帧。"""
    url = f"http://127.0.0.1:{ctx.port}/api/events"
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=3)
    try:
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        # read1：只读当前可用字节，不阻塞等满 2048 / EOF（SSE 是无限流）
        chunk = resp.read1(4096).decode("utf-8", "replace")
        assert "event: hello" in chunk, "缺少 hello 帧"
    finally:
        resp.close()


# ── 无 pytest 时的手动 runner ─────────────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")
             and callable(v) and not k.endswith("_closed_loop")]  # closed_loop 需 fixture
    # 兼容：手动跑也构造 ctx
    failed = 0
    for fn in tests:
        c = Ctx()
        try:
            fn(c)
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        finally:
            c.shutdown()
    # closed_loop 单独跑（需要完整 ctx 生命周期）
    c = Ctx()
    try:
        test_run_approve_closed_loop(c)
        print("PASS  test_run_approve_closed_loop")
    except Exception as e:  # noqa: BLE001
        failed += 1
        print(f"FAIL  test_run_approve_closed_loop: {e}")
    finally:
        c.shutdown()
    print(f"\n{'OK' if failed == 0 else 'FAILED'} ({failed} failed)")
    sys.exit(1 if failed else 0)
