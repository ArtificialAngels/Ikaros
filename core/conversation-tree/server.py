"""对话树面板后端服务 (V5 store 集成版).

- 用标准库 ThreadingHTTPServer，零第三方依赖。
- 对话树引擎为唯一事实源: 拓扑 JSON 只存 v5_memory_id + summary,
  对话本体走 V5 store (SQLite)。
- 同一来源 serve UI (index.html) + 暴露 REST API。

启动: python core/conversation-tree/server.py --port 48920
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 让本服务能 import memory_v5.conversation_tree + memory_v5.store
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

import memory_v5.conversation_tree as ct  # noqa: E402
from memory_v5 import store as v5s     # noqa: E402
# B2: 任务事件总线 (herdr events.subscribe 语义内化); core/ 已在 sys.path
from taskbus import EventBus, exec_state_event  # noqa: E402

# ── LLM 配置 ──────────────────────────────────────────────────
_DEEPSEEK_KEY = ""
_ENV_PATHS = [
    Path(os.environ.get("HERMES_HOME", str(Path(__file__).resolve().parent.parent.parent / "data" / "hermes-agent"))) / ".env",
    Path(os.environ.get("IKAROS_ROOT", str(Path(__file__).resolve().parent.parent.parent))) / ".env",
]
for _ep in _ENV_PATHS:
    try:
        if _ep.exists():
            for _line in _ep.read_text(encoding="utf-8").split("\n"):
                _line = _line.strip()
                if _line.startswith("DEEPSEEK_API_KEY="):
                    _DEEPSEEK_KEY = _line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
HERMES_CHAT_URL = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119").rstrip("/") + "/v1/chat/completions"
LOCAL_CHAT_URL = os.environ.get("IKAROS_LOCAL_LLM_URL", "http://127.0.0.1:8080").rstrip("/") + "/v1/chat/completions"
LLM_TIMEOUT = int(os.environ.get("CT_LLM_TIMEOUT", "120"))
MAX_CONTEXT_MSGS = int(os.environ.get("CT_MAX_CONTEXT_MSGS", "50"))

# ── Ikaros 人格来源 (V5 同步的身份/心绪) ──────────────────────
# server.py 位于 core/conversation-tree/ ; 根目录 = parent.parent
_IKAROS_ROOT = _HERE.parent.parent
_AXIOM_PATH = _IKAROS_ROOT / "config" / "identity" / "axiom.md"
_SOUL_PATH = _IKAROS_ROOT / "data" / "hermes-agent" / "SOUL.md"
_SELF_MODEL_PATH = _HERE.parent / "memory_v5" / "data" / "v5" / "self_model.json"

SYSTEM_PROMPT = (
    "You are Explore, a helpful AI assistant that engages in structured, deep conversations. "
    "The conversation is organized as a tree: each node is a decision point where the user can "
    "branch off and explore alternative directions. "
    "Answer naturally and helpfully. Keep responses concise unless asked for depth. "
    "Use markdown for code blocks and formatting when appropriate."
)

# ── LLM 调用 ──────────────────────────────────────────────
def _call_llm(messages: list[dict]) -> str:
    errors: list[str] = []

    # 1) DeepSeek API
    if _DEEPSEEK_KEY:
        try:
            ds_body = json.dumps({
                "model": "deepseek-chat", "messages": messages,
                "max_tokens": 2048, "temperature": 0.7, "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                DEEPSEEK_CHAT_URL, data=ds_body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {_DEEPSEEK_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"].get("content", "")
                if content.strip():
                    return content.strip()
                errors.append("DeepSeek returned empty content")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            errors.append(f"DeepSeek: {e}")
        except Exception as e:
            errors.append(f"DeepSeek unexpected: {e}")
    else:
        errors.append("DeepSeek: no API key")

    # 2) Hermes Dashboard
    try:
        h_body = json.dumps({
            "model": "hermes", "messages": messages,
            "max_tokens": 2048, "temperature": 0.7, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(HERMES_CHAT_URL, data=h_body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content", "")
            if content.strip():
                return content.strip()
            errors.append("Hermes returned empty content")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        errors.append(f"Hermes: {e}")
    except Exception as e:
        errors.append(f"Hermes unexpected: {e}")

    # 3) Local LLM
    try:
        l_body = json.dumps({
            "model": "local-llm", "messages": messages,
            "max_tokens": 2048, "temperature": 0.7, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(LOCAL_CHAT_URL, data=l_body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content", "")
            if not content.strip():
                content = data["choices"][0]["message"].get("reasoning_content", "")
            if content.strip():
                return content.strip()
            errors.append("Local LLM returned empty content")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        errors.append(f"Local: {e}")
    except Exception as e:
        errors.append(f"Local unexpected: {e}")

    raise RuntimeError("LLM unavailable: " + "; ".join(errors))


def _build_chat_messages(node_id: str | None, user_message: str) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if _tree is not None:
        try:
            ctx = _tree.get_context(node_id)
            if len(ctx) > MAX_CONTEXT_MSGS:
                msgs.append({
                    "role": "system",
                    "content": f"(Earlier conversation truncated; showing last {MAX_CONTEXT_MSGS} messages of {len(ctx)} total.)"
                })
                ctx = ctx[-MAX_CONTEXT_MSGS:]
            msgs.extend(ctx)
        except Exception:
            pass
    msgs.append({"role": "user", "content": user_message})
    return msgs


# ── Ikaros 人格 + V5 记忆注入 (chat 接入 Ikaros V5) ──────────────
def build_ikaros_persona() -> str:
    """组装 Ikaros 身份 system 文本: 公理(核心指令) + SOUL 身份/偏好 + 动态心绪。

    全部 fail-open: 任一文件缺失/读取失败都不影响主线, 退化为最小身份说明。
    """
    blocks: list[str] = []

    # 1) 公理: 核心身份指令, 必含 (约 288 字节)
    try:
        if _AXIOM_PATH.exists():
            blocks.append(_AXIOM_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        pass

    # 2) SOUL.md: V5 同步的身份 + 偏好 (按标题白名单抽取, 跳过运维/下载等操作章节,
    #    并截断避免撑爆上下文; 去掉自动同步注释头)
    try:
        if _SOUL_PATH.exists():
            soul = _SOUL_PATH.read_text(encoding="utf-8")
            soul = "\n".join(l for l in soul.split("\n")
                             if not l.strip().startswith("<!--"))
            keep = ("核心身份", "自我叙事", "此刻的我", "存在公理",
                    "身份", "偏好", "preferences", "identity", "preference")
            chunks, cur_head, buf = [], None, []
            for line in soul.split("\n"):
                if line.startswith("## "):
                    if cur_head and any(k in cur_head for k in keep):
                        chunks.append("\n".join(buf))
                    cur_head = line[3:].strip()
                    buf = [line]
                else:
                    buf.append(line)
            if cur_head and any(k in cur_head for k in keep):
                chunks.append("\n".join(buf))
            soul_kept = "\n\n".join(chunks)[:1800]
            if soul_kept.strip():
                blocks.append("[身份档案 SOUL]\n" + soul_kept)
    except Exception:
        pass

    # 3) 动态心绪: 来自 self_model.json 的此刻叙事 / 关系
    try:
        if _SELF_MODEL_PATH.exists():
            import json as _json
            sm = _json.loads(_SELF_MODEL_PATH.read_text(encoding="utf-8"))
            narr = sm.get("self_narrative", "") or ""
            snap = (narr.split("\n")[0] if narr else "")[:280]
            ident = sm.get("identity", {}) or {}
            name = ident.get("name", "") or "伊卡洛斯"
            nature = ident.get("nature", "") or ""
            rel = ""
            if snap:
                rel = f" | 此刻: {snap}"
            blocks.append(f"[此刻] 我是{name}（{nature}）{rel}")
    except Exception:
        pass

    blocks.append(
        "对话以树形组织: 每个节点是可分叉的探索点, 分支代表不同方向; "
        "保持温暖、直接、有温度的语气, 像和哥哥对话。"
    )
    return "\n\n".join(b for b in blocks if b.strip())


def build_v5_memory_block(node_id: str | None, query: str) -> str:
    """树域语义检索 (V5 记忆引擎): 按当前 query 检索相关记忆并做树域加权。

    依赖刚落地的存储打标 (node:/branch:), 命中路径/分支的记忆优先。检索后端
    不可用时 fail-open 返回空串。返回可直接拼进 system 的文本块。
    """
    if _tree is None:
        return ""
    try:
        from memory_v5.extensions.tree_adapter import tree_scoped_retrieve
        results = tree_scoped_retrieve(_tree, node_id, query, top_k=5)
    except Exception:
        return ""
    lines: list[str] = []
    for r in results:
        txt = (r.get("content") or "").strip()
        if txt:
            scope = r.get("tree_scope", "global")
            lines.append(f"[{scope}] {txt}")
    return "\n".join(lines)


def build_chat_messages_v5(node_id: str | None, user_message: str) -> list[dict]:
    """chat 接入 Ikaros V5 的主入口:

    - 人格: build_ikaros_persona (axiom + SOUL + 动态心绪)
    - 压缩: build_tree_aware_context (节点边界压缩, 替线性 [-50:] 截断)
    - 记忆: build_v5_memory_block (tree_scoped_retrieve 树域语义检索)
    任何环节异常都 fail-open 回退到旧的线性上下文 + 人格。
    """
    persona = build_ikaros_persona()
    mem_block = build_v5_memory_block(node_id, user_message)
    try:
        ctx = build_tree_aware_context(
            _tree, node_id,
            system_prompt=persona,
            extra_memory=mem_block or None,
        )
        return ctx + [{"role": "user", "content": user_message}]
    except Exception:
        # 回退: 旧线性上下文 + 人格 + 记忆
        msgs: list[dict] = [{"role": "system", "content": persona}]
        if mem_block:
            msgs.append({"role": "system", "content": "Relevant memories (V5):\n" + mem_block})
        try:
            ctx = _tree.get_context(node_id)
            if len(ctx) > MAX_CONTEXT_MSGS:
                ctx = ctx[-MAX_CONTEXT_MSGS:]
            msgs.extend(ctx)
        except Exception:
            pass
        msgs.append({"role": "user", "content": user_message})
        return msgs


PERSIST_KEY = "ui_conversation_tree"
HERE = _HERE
INDEX_HTML = HERE / "index.html"

# 全局单例
_tree: "ct.ConversationTree | None" = None
_retriever: "ct.MemoryRetriever | None" = None
_lock = threading.RLock()

# B2: 共享任务事件总线 —— 所有订阅方 (SSE / 9100 面板 / supervisor) 共用同一实例
_bus = EventBus()


# ───────────────────────── 树初始化 / 恢复 ─────────────────────────

def _load_str(memory_ids: list[int]) -> dict[int, str]:
    """get_batch 返回 Memory 对象, 引擎期望 {id: str}."""
    batch = v5s.get_batch(memory_ids)
    return {mid: m.content for mid, m in batch.items()}

def _search_dicts(query: str, top_k: int = 10) -> list[dict]:
    """search 返回 Memory 对象, 引擎期望 [{"id":..,"content":..}, ...]."""
    results = v5s.search(query, top_k=top_k)
    return [{"id": r.id, "content": r.content} for r in results]

def _make_tree() -> ct.ConversationTree:
    """创建注入 V5 store 后端的 ConversationTree."""
    return ct.ConversationTree(
        persist_key=PERSIST_KEY,
        _store=v5s.store,
        _load=_load_str,
        _search=_search_dicts,
    )


def build_demo() -> None:
    """初始化一棵 poker Demo 树 (对话内容走 V5 store)."""
    global _tree, _retriever
    with _lock:
        t = _make_tree()
        t.init([{"role": "system", "content": "Explore conversation started."}])
        a = t.add_turn([
            {"role": "user", "content": "What is GTO strategy in poker?"},
            {"role": "assistant", "content": "GTO = Game Theory Optimal: an unexploitable strategy balancing bluffs and value bets."},
        ], branch_label="main", title="GTO intro")
        b = t.add_turn([
            {"role": "user", "content": "Explain Nash equilibrium in this context."},
            {"role": "assistant", "content": "A Nash equilibrium is where no player gains by unilaterally changing strategy."},
        ], branch_label="main", title="Nash eq")
        c = t.add_turn([
            {"role": "user", "content": "Show me the river bluff frequency code."},
            {"role": "assistant", "content": "bluff_freq = bet/(pot+2*bet) ≈ 29% when bet=70, pot=100."},
        ], branch_label="main", title="Bluff code")
        d = t.branch_from(a.id, [
            {"role": "user", "content": "Can you explain GTO using neural networks?"},
            {"role": "assistant", "content": "Think of each decision as a policy network output; training against yourself converges to a Nash-like equilibrium (self-play)."},
        ], branch_label="ml", title="NN view")
        e = t.add_turn([
            {"role": "user", "content": "And how does attention relate to game theory?"},
            {"role": "assistant", "content": "Attention weights can be seen as a soft policy over opponents actions — a learned equilibrium surface."},
        ], branch_label="ml", title="Attention")
        t.jump_to(c.id)
        _tree = t
        t.event_bus = _bus  # B2: 注入共享事件总线
        # 种子记忆 (通过 retriever.add_memory 绑定到节点)
        _retriever = ct.MemoryRetriever(t)
        seed_memories = [
            {"text": "User is exploring GTO poker strategy examples",
             "tags": ["poker", "gto", "strategy"], "node_id": a.id, "branch": "main"},
            {"text": "Covered Nash equilibrium definition and unilaterality",
             "tags": ["nash", "game-theory", "equilibrium"], "node_id": b.id, "branch": "main"},
            {"text": "Derived river bluff frequency formula ~29 percent",
             "tags": ["poker", "code", "bluff", "frequency"], "node_id": c.id, "branch": "main"},
            {"text": "Bridged poker GTO to neural network self-play intuition",
             "tags": ["ml", "neural", "self-play", "game-theory"], "node_id": d.id, "branch": "ml"},
            {"text": "Attention weights as soft policy over opponents actions",
             "tags": ["ml", "attention", "policy", "game-theory"], "node_id": e.id, "branch": "ml"},
            {"text": "User generally prefers concrete code snippets over prose",
             "tags": ["preference", "code"], "node_id": c.id, "branch": "main"},
        ]
        for mem in seed_memories:
            _retriever.add_memory(mem)


def _migrate_if_needed() -> None:
    """一次性迁移: 旧树 JSON (节点含 messages 但 v5_memory_id=0) → 新格式.

    读原始 JSON 文件, 检测含 messages 的节点, 写入 V5 store, 更新 v5_memory_id.
    """
    assert _tree is not None
    old_path = _tree.data_dir / f"{PERSIST_KEY}.json"
    if not old_path.exists():
        return

    try:
        old_data = json.loads(old_path.read_text(encoding="utf-8"))
    except Exception:
        return

    migrated = 0
    for raw_node in old_data.get("nodes", []):
        nid = raw_node.get("id", "")
        msgs = raw_node.get("messages")
        mid = raw_node.get("v5_memory_id", 0)
        # 有消息但没有 v5_memory_id → 旧格式, 需要迁移
        if msgs and not mid:
            content = json.dumps(msgs, ensure_ascii=False)
            try:
                new_id = v5s.store(content, type="conversation", tags="migrated")
                # 更新内存中的节点
                node = _tree.get_node(nid)
                if node:
                    node.v5_memory_id = new_id
                    node.summary = ct._extract_summary(msgs)
                migrated += 1
            except Exception as e:
                sys.stderr.write(f"[ct] migrate node {nid}: {e}\n")

    if migrated:
        _tree.persist()
        sys.stderr.write(f"[ct] migrated {migrated} nodes to V5 store\n")


def ensure_tree() -> None:
    """进程启动 / 首次请求时恢复或初始化树 + 迁移旧格式."""
    global _tree, _retriever
    with _lock:
        if _tree is not None:
            return
        t = ct.ConversationTree.load(
            persist_key=PERSIST_KEY,
            _store=v5s.store, _load=_load_str, _search=_search_dicts,
        )
        if t is None:
            build_demo()
        else:
            _tree = t
            _retriever = ct.MemoryRetriever(t)
            t.event_bus = _bus  # B2: 注入共享事件总线
            # 迁移: 检测旧格式 (节点含 messages 字段但 v5_memory_id=0)
            _migrate_if_needed()


def state_dict() -> dict:
    """返回完整树状态, 含从 V5 store 解析的 messages 字段."""
    assert _tree is not None
    data = json.loads(_tree.serialize())
    # 从 store 批量回读消息, 注入 nodes (前端兼容)
    ids = [n.get("v5_memory_id", 0) for n in data["nodes"] if n.get("v5_memory_id")]
    if ids:
        try:
            batch = _tree._load_fn(ids)
        except Exception:
            batch = {}
        for n in data["nodes"]:
            mid = n.get("v5_memory_id", 0)
            raw = batch.get(mid, "")
            if raw:
                try:
                    n["messages"] = json.loads(raw)
                except json.JSONDecodeError:
                    n["messages"] = [{"role": "system", "content": raw}]
            else:
                n["messages"] = []
    return data


# ── B5: supervisor 端点 (9100 面板 herdr 卡片驱动) ───────────────────────
_supervisor = None
_SUPERVISOR_OVERRIDE = None  # 测试注入用 (FakeSupervisor)


def _get_supervisor() -> "CodingAgentSupervisor":
    """惰性创建 CodingAgentSupervisor；优先返回测试注入的 override。"""
    global _supervisor
    if _SUPERVISOR_OVERRIDE is not None:
        return _SUPERVISOR_OVERRIDE
    ensure_tree()
    if _supervisor is None:
        try:
            from herdr import CodingAgentSupervisor, SessionRegistry  # noqa: F401
        except Exception as exc:
            raise RuntimeError(f"herdr 桥不可用: {exc}")
        _supervisor = CodingAgentSupervisor(_tree, SessionRegistry())
    return _supervisor


# ───────────────────────── HTTP 处理 ─────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "ConversationTreeServer/2.0"
    # B2: SSE 事件流需要长连接 + 分块流; 升级到 HTTP/1.1 (其他端点都带 Content-Length,
    # keep-alive 行为安全)。
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # B5: 允许 9100 面板跨域订阅 supervisor 端点 + 事件流
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # B5: CORS 预检（9100 面板跨域 POST supervisor 端点）
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_text(self, text: str, code: int = 200, ctype: str = "text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path: Path):
        if not path.exists():
            self._send_text("index.html not found", 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _q(self, key: str):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        v = qs.get(key)
        return v[0] if v else None

    def do_GET(self):
        ensure_tree()
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                self._send_html(INDEX_HTML)
            elif path == "/api/state":
                self._send_json(state_dict())
            elif path == "/api/context":
                nid = self._q("node_id")
                self._send_json(_tree.get_context(nid))
            elif path == "/api/node_content":
                nid = self._q("node_id")
                if not nid:
                    self._send_json({"error": "node_id required"}, 400)
                    return
                node = _tree.get_node(nid)
                if not node:
                    self._send_json({"error": "node not found"}, 404)
                    return
                mid = node.v5_memory_id
                if mid:
                    raw = _tree._load_fn([mid]).get(mid, "")
                    try:
                        msgs = json.loads(raw) if raw else []
                    except json.JSONDecodeError:
                        msgs = [{"role": "system", "content": raw}] if raw else []
                else:
                    msgs = []
                self._send_json({
                    "node_id": nid,
                    "v5_memory_id": mid,
                    "summary": node.summary,
                    "messages": msgs,
                })
            elif path == "/api/path":
                nid = self._q("node_id")
                self._send_json([n.to_dict() for n in _tree.get_path(nid)])
            elif path == "/api/search":
                q = self._q("q") or ""
                res = _tree.search(q)
                # 解析每条结果的消息内容
                for r in res:
                    mid = r.get("v5_memory_id", 0)
                    if mid:
                        raw = _tree._load_fn([mid]).get(mid, "")
                        try:
                            r["messages"] = json.loads(raw) if raw else []
                        except json.JSONDecodeError:
                            r["messages"] = []
                self._send_json(res)
            elif path == "/api/memory":
                nid = self._q("node_id")
                self._send_json(_retriever.retrieve(nid) if nid else
                                {"path": [], "cross": [], "path_text": "", "branch_labels": []})
            elif path == "/api/full_context":
                nid = self._q("node_id")
                ctx = _tree.build_context_v2(
                    nid,
                    include_siblings=self._q("siblings") != "0",
                    include_merged=self._q("merged") != "0",
                )
                self._send_json({"context": ctx, "count": len(ctx)})
            elif path == "/api/mermaid":
                self._send_json({"mermaid": _tree.to_mermaid()})
            elif path == "/api/events":
                self._stream_events()
            else:
                self._send_json({"error": "not found", "path": path}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)

    # ── B2: SSE 事件流 (对应 herdr events.subscribe) ───────────────────
    def _stream_events(self) -> None:
        """GET /api/events —— 订阅共享事件总线, 实时推送类型化事件.

        客户端先收一条 ``hello`` 帧 (含事件协议版本), 随后任意
        ``node.exec_state_changed`` 等事件以 ``data: {json}\\n\\n`` 推送。
        每连接一个独立 queue, 断线时退订; 15s 心跳保活。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # 禁用代理缓冲, 保证实时
        # B5: 允许 9100 面板跨域订阅事件流
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: "queue.Queue" = queue.Queue()

        def _on(ev: object) -> None:
            try:
                d = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
                q.put_nowait(d)
            except Exception:
                pass

        unsub = _bus.subscribe(_on)
        # hello 帧: 告知客户端事件协议版本 + 当前树标识
        try:
            hello = {
                "v": 1,
                "type": "hello",
                "ts": time.time(),
                "tree": PERSIST_KEY,
                "data": {"event_protocol": 1},
            }
            self.wfile.write(
                ("event: hello\ndata: " + json.dumps(hello, ensure_ascii=False) + "\n\n").encode("utf-8")
            )
            self.wfile.flush()
        except Exception:
            try:
                unsub()
            except Exception:
                pass
            return

        while True:
            try:
                evt = q.get(timeout=15)
            except queue.Empty:
                # 心跳保活 (注释行, 不带 event: 前缀)
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except Exception:
                    break
                continue
            try:
                frame = "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                break
        try:
            unsub()
        except Exception:
            pass

    def do_POST(self):
        ensure_tree()
        path = self.path.split("?", 1)[0]
        try:
            data = self._body()
            if path == "/api/init":
                build_demo()
                self._send_json(state_dict())
            elif path == "/api/add_turn":
                _tree.add_turn(
                    messages=data.get("messages", []),
                    parent_id=data.get("parent_id"),
                    branch_label=data.get("branch_label"),
                    state=data.get("state"),
                    config=data.get("config"),
                    title=data.get("title"),
                )
                self._send_json(state_dict())
            elif path == "/api/branch_from":
                _tree.branch_from(
                    node_id=data["node_id"],
                    messages=data.get("messages", []),
                    branch_label=data.get("branch_label"),
                )
                self._send_json(state_dict())
            elif path == "/api/fork":
                node = _tree.fork_branch(
                    fork_point_id=data["node_id"],
                    branch_label=data.get("branch_label", "branch"),
                    messages=data.get("messages", []),
                    state=data.get("state"),
                    config=data.get("config"),
                    title=data.get("title"),
                )
                self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/conclude":
                node = _tree.conclude_branch(
                    node_id=data["node_id"],
                    conclusions=data.get("conclusions", []),
                )
                self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/merge":
                bid = data.get("branch_id") or data.get("source_id")
                tid = data.get("trunk_id") or data.get("target_id")
                if not bid or not tid:
                    self._send_json({"error": "branch_id and trunk_id required"}, 400)
                    return
                # 前端 "Merge to Trunk" 传 '__trunk__' → 沿祖先链找 trunk 节点
                if tid == "__trunk__":
                    bnode = _tree.get_node(bid)
                    cur = bnode.parent_id if bnode else None
                    tid = None
                    while cur:
                        cn = _tree.get_node(cur)
                        if cn and cn.node_type == "trunk":
                            tid = cn.id
                            break
                        cur = cn.parent_id if cn else None
                    if not tid:
                        self._send_json({"error": "no trunk ancestor found"}, 400)
                        return
                _tree.merge_branch(branch_node_id=bid, trunk_target_id=tid)
                self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/unmerge":
                _tree.unmerge_branch(data.get("node_id") or data.get("branch_id"))
                self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/abandon":
                _tree.abandon_branch(data["node_id"])
                self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/node/exec_state":
                # B2: 设置节点执行状态 (supervisor / 测试经此驱动事件流)
                nid = data.get("node_id")
                if not nid:
                    self._send_json({"error": "node_id required"}, 400)
                    return
                try:
                    node = _tree.set_exec_state(
                        nid,
                        data.get("state", "working"),
                        progress=data.get("progress"),
                        detail=data.get("detail"),
                    )
                except KeyError as e:
                    self._send_json({"error": str(e)}, 404)
                    return
                self._send_json({
                    "ok": True,
                    "node_id": nid,
                    "exec_state": node.exec_state,
                    "exec_progress": node.exec_progress,
                    "exec_detail": node.exec_detail,
                    "state": state_dict(),
                })
            elif path == "/api/jump_to":
                _tree.jump_to(data["node_id"])
                self._send_json(state_dict())
            elif path == "/api/prune":
                _tree.prune(data["node_id"])
                self._send_json(state_dict())
            elif path == "/api/rename":
                node = _tree.rename_node(
                    node_id=data["node_id"],
                    title=data.get("title", ""),
                )
                self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/delete_node":
                _tree.delete_node(data["node_id"])
                self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/memory":
                mem = _retriever.add_memory({
                    "text": data.get("text", ""),
                    "tags": data.get("tags", []),
                    "node_id": data.get("node_id"),
                    "branch": data.get("branch"),
                })
                self._send_json({"ok": True, "mem": mem})
            elif path == "/api/chat":
                user_message = data.get("message", "").strip()
                if not user_message:
                    self._send_json({"error": "message is required"}, 400)
                    return
                parent_id = data.get("parent_id")
                branch_label = data.get("branch_label")

                # chat 接入 Ikaros V5: 人格 + 树感知压缩 + 树域语义记忆 (fail-open)
                target_id = parent_id or _tree.current.id
                messages = build_chat_messages_v5(target_id, user_message)
                try:
                    reply = _call_llm(messages)
                except RuntimeError as e:
                    self._send_json({"error": str(e)}, 503)
                    return

                _tree.add_turn(
                    messages=[
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": reply},
                    ],
                    parent_id=parent_id,
                    branch_label=branch_label,
                )
                self._send_json({"reply": reply, "state": state_dict()})
            elif path == "/api/reset":
                build_demo()
                self._send_json(state_dict())
            # ── B5: supervisor 编排端点 (9100 面板 herdr 卡片驱动) ──
            elif path == "/api/supervisor/run":
                # 在 herdr pane 里跑一个外部 coding agent; 后台线程执行, 结果经 exec_state 回流
                try:
                    from herdr import SupervisorTask  # noqa: F401
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"herdr 桥不可用: {exc}"}, 500)
                    return
                try:
                    task = SupervisorTask(
                        task=str(data.get("task", "")),
                        kind=str(data.get("kind", "aider")),
                        node_id=str(data.get("node_id", "")),
                        cwd=data.get("cwd"),
                        label=data.get("label"),
                        timeout_s=int(data.get("timeout_s", 600) or 600),
                    )
                except Exception as exc:
                    self._send_json({"ok": False, "error": f"invalid task: {exc}"}, 400)
                    return
                if not task.node_id or _tree.get_node(task.node_id) is None:
                    self._send_json({"ok": False, "error": "node_id 不存在"}, 400)
                    return
                sup = _get_supervisor()
                def _bg_run(t):  # noqa: E306
                    try:
                        sup.run_task(t)
                    except Exception as e:
                        sys.stderr.write(f"[ct] supervisor run_task error: {e}\n")
                threading.Thread(target=_bg_run, args=(task,), daemon=True).start()
                self._send_json({"ok": True, "node_id": task.node_id,
                                 "msg": "任务已派发，进度见节点 exec_state"})
            elif path == "/api/supervisor/approve":
                # 为一个停在 blocked 的任务提供决策并继续
                node_id = data.get("node_id")
                decision = data.get("decision", "")
                if not node_id:
                    self._send_json({"ok": False, "error": "node_id required"}, 400)
                    return
                try:
                    sup = _get_supervisor()
                    res = sup.approve(node_id, decision)
                except Exception as exc:
                    code = 400 if "没有进行中" in str(exc) else 500
                    self._send_json({"ok": False, "error": str(exc)}, code)
                    return
                self._send_json({"ok": True, "result": res.__dict__})
            else:
                self._send_json({"error": "not found", "path": path}, 404)
        except Exception as exc:
            self._send_json({"error": str(exc)}, 500)


def main():
    ap = argparse.ArgumentParser(description="Conversation Tree panel server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=48920)
    args = ap.parse_args()

    ensure_tree()
    # Prevent SO_REUSEADDR from allowing multiple processes to bind the same port
    # (Windows quirk — without SO_EXCLUSIVEADDRUSE, taskkill/kill_port races can
    # leave zombie listeners that block the new process's traffic).
    class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def server_bind(self):
            import socket as _socket
            self.socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    httpd = ExclusiveThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"[ct] serving on http://{args.host}:{args.port}\n")
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
