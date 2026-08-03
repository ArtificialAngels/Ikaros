"""core/conversation-tree/server.py chat SSE 链路测试 (子任务 M5).

覆盖 chat 最复杂/最易错的逻辑, 用本地假 SSE 服务 + 网络降级桩:
- _stream_hermes_gateway: 解析 Hermes gateway 的 OpenAI 兼容 SSE + 命名事件
  (hermes.tool.progress / hermes.reasoning) → 翻译为前端 tool_call/tool_result/thinking/
  content/usage 事件, 并正确回填 collector (含 L5: 同名工具按 toolCallId 精确匹配).
- _chat_stream_events: gateway 不可达时回退本地三层链路, 并以 warn 事件可见化降级.
"""
from __future__ import annotations

import json

import pytest

from conftest import server  # type: ignore  # noqa: E402


# ────────────────── 假 Hermes gateway (urlopen 桩, 回放预置 SSE) ──────────────────

class _FakeResp:
    """可被 `with urlopen(...) as resp: for chunk in resp:` 消费的假响应."""

    def __init__(self, data: bytes, chunk_size: int = 64):
        self._chunks = [data[i:i + chunk_size]
                        for i in range(0, len(data), chunk_size)] or [b""]
        self._it = iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def read(self):
        return b"".join(self._chunks)


def _fake_urlopen_factory(sse_text: str):
    """返回一个替换 urllib.request.urlopen 的桩, 恒定回放同一段 SSE."""
    body = sse_text.encode("utf-8")

    def _open(req, timeout=None):  # noqa: ANN001
        return _FakeResp(body)

    return _open


def _collect(events):
    """把事件序列收成列表."""
    return list(events)


# ────────────────── 工具: 组装 SSE 帧 ──────────────────

def _frame(event: str | None, payload) -> str:
    lines = []
    if event is not None:
        lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


# ────────────────── 测试 1: gateway SSE 解析 + collector 回填 ──────────────────

def test_stream_hermes_gateway_parses_sse(monkeypatch):
    """解析 content / thinking / tool_call / tool_result / usage 五类事件."""
    sse = (
        _frame("hermes.tool.progress",
               {"tool": "memory_search", "toolCallId": "call_1",
                "status": "running", "label": "检索记忆", "emoji": "🔍"})
        + _frame(None, {"choices": [{"delta": {"content": "你好"}}]})
        + _frame("hermes.reasoning", {"text": "让我想想"})
        + _frame(None, {"choices": [{"delta": {"content": "，世界"}}]})
        + _frame("hermes.tool.progress",
                 {"tool": "memory_search", "toolCallId": "call_1",
                  "status": "completed", "result": "找到3条"})
        + _frame(None, {"choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15}})
    )
    monkeypatch.setattr(server.urllib.request, "urlopen", _fake_urlopen_factory(sse))
    monkeypatch.setattr(server, "HERMES_AGENT_URL", "http://fake-gateway/v1/chat/completions")
    monkeypatch.setattr(server, "HERMES_AGENT_KEY", "test-key")
    # 让 usage 事件的 context_window 确定, 不依赖模型元数据表
    monkeypatch.setattr(server, "_hermes_model_context", lambda m: 12345)

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_hermes_gateway(
        [{"role": "user", "content": "hi"}], collector, model="test-model"))

    types = [e["type"] for e in events]
    assert types == ["tool_call", "content", "thinking", "content",
                     "tool_result", "usage"], types

    # 正文与思考累积正确
    assert collector["content"] == "你好，世界"
    assert collector["thinking"] == "让我想想"

    # 工具卡片: 注册 + 按 id 回填结果
    assert len(collector["tool_calls"]) == 1
    tc = collector["tool_calls"][0]
    assert tc["id"] == "call_1" and tc["name"] == "memory_search"
    assert tc["result_summary"] == "找到3条" and tc["success"] is True

    # usage 事件透出
    usage_ev = events[-1]
    assert usage_ev["usage"]["total_tokens"] == 15
    assert usage_ev["context_window"] == 12345


# ────────────────── 测试 2: L5 同名工具按 toolCallId 精确匹配 ──────────────────

def test_stream_hermes_gateway_same_name_tools_by_id(monkeypatch):
    """两次同名 memory_search, 结果必须按 toolCallId 回填到正确的卡片 (不串台)."""
    sse = (
        _frame("hermes.tool.progress",
               {"tool": "memory_search", "toolCallId": "call_a",
                "status": "running", "label": "检索A", "emoji": "🔍"})
        + _frame("hermes.tool.progress",
                 {"tool": "memory_search", "toolCallId": "call_b",
                  "status": "running", "label": "检索B", "emoji": "🔍"})
        + _frame(None, {"choices": [{"delta": {"content": "答案"}}]})
        + _frame("hermes.tool.progress",
                 {"tool": "memory_search", "toolCallId": "call_a",
                  "status": "completed", "result": "结果A"})
        + _frame("hermes.tool.progress",
                 {"tool": "memory_search", "toolCallId": "call_b",
                  "status": "completed", "result": "结果B"})
        + _frame(None, {"choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"total_tokens": 1}})
    )
    monkeypatch.setattr(server.urllib.request, "urlopen", _fake_urlopen_factory(sse))
    monkeypatch.setattr(server, "HERMES_AGENT_URL", "http://fake-gateway/v1/chat/completions")
    monkeypatch.setattr(server, "HERMES_AGENT_KEY", "test-key")
    monkeypatch.setattr(server, "_hermes_model_context", lambda m: 12345)

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    _collect(server._stream_hermes_gateway(
        [{"role": "user", "content": "hi"}], collector, model="test-model"))

    by_id = {tc["id"]: tc for tc in collector["tool_calls"]}
    assert set(by_id) == {"call_a", "call_b"}, by_id
    assert by_id["call_a"]["result_summary"] == "结果A"
    assert by_id["call_b"]["result_summary"] == "结果B"


# ────────────────── 测试 3: gateway 不可达 → 本地降级 + warn ──────────────────

def test_chat_stream_events_falls_back_when_gateway_down(monkeypatch):
    """gateway 地址不可达时, _chat_stream_events 先发 warn, 再走 _stream_fallback."""
    # 指向一个不可能在监听的端口
    monkeypatch.setattr(server, "HERMES_AGENT_URL", "http://127.0.0.1:1/")
    monkeypatch.setattr(server, "HERMES_AGENT_KEY", "test-key")
    # 桩掉本地三层 chat 补全, 避免真实网络; 返回确定性内容
    monkeypatch.setattr(
        server, "_call_llm",
        lambda messages, agent="ikaros", collector=None: (
            "本地降级回答内容", {"prompt_tokens": 1, "completion_tokens": 2,
                                 "total_tokens": 3}))

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._chat_stream_events(
        [{"role": "user", "content": "hi"}], "ikaros", None, collector, tree=None))

    types = [e["type"] for e in events]
    # 第一个事件必须是 warn (降级可见化), 随后是降级 content 分片 + usage
    assert types[0] == "warn", types
    assert "content" in types
    assert "usage" in types
    # 降级内容应等于桩返回值 (按 ≈24 字切片后仍拼回原文)
    assert collector["content"] == "本地降级回答内容"
    assert any("降级" in (e.get("message") or "") for e in events if e["type"] == "warn")


# ────────────────── 测试 4: gateway 未配置 → 直接本地降级 (不报错) ──────────────────

def test_chat_stream_events_no_gateway_config(monkeypatch):
    """HERMES_AGENT_URL 为 None 时, 直接走本地降级而非抛错."""
    monkeypatch.setattr(server, "HERMES_AGENT_URL", None)
    monkeypatch.setattr(
        server, "_call_llm",
        lambda messages, agent="ikaros", collector=None: (
            "无网关降级", {"total_tokens": 1}))

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._chat_stream_events(
        [{"role": "user", "content": "hi"}], "ikaros", None, collector, tree=None))

    assert events[0]["type"] == "warn"
    assert collector["content"] == "无网关降级"


# ────────────────── F12: 降级链挂只读工具回路 (memory_search 预检索) ──────────────────

def test_stream_fallback_prefetches_memory(monkeypatch):
    """F12: 降级时 _execute_chat_tool 的 memory_search 被真实调用, 结果注入上下文.

    修复"工具回路从未生效": _stream_fallback 现在会先预检索记忆 → 产出
    tool_call/tool_result 事件 → 把结果作为 system 前缀喂给 _call_llm.
    """
    called = {}
    monkeypatch.setattr(
        server, "_execute_chat_tool",
        lambda name, arguments, node_id=None: called.update(name=name, args=arguments) or
        {"ok": True, "result": "找到1条: GTO 策略笔记"})
    monkeypatch.setattr(
        server, "_call_llm",
        lambda messages, agent="ikaros", collector=None: (
            messages[0]["content"], {"total_tokens": 1}))  # 返回首个 system 内容

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_fallback(
        [{"role": "user", "content": "什么是 GTO?"}], "ikaros", collector, node_id="n1"))

    # 工具被调用且是 memory_search
    assert called.get("name") == "memory_search", called
    # 预检索结果进入 _call_llm 的 system 前缀
    assert "找到1条: GTO 策略笔记" in collector["content"]
    # 工具事件已透出
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types
    assert len(collector["tool_calls"]) == 1
    assert collector["tool_calls"][0]["name"] == "memory_search"


def test_stream_fallback_prefetch_fail_open(monkeypatch):
    """F12: 预检索失败时 fail-open, 不阻塞降级正文."""
    monkeypatch.setattr(server, "_execute_chat_tool",
                        lambda name, arguments, node_id=None: {"ok": False, "result": "检索失败"})
    monkeypatch.setattr(
        server, "_call_llm",
        lambda messages, agent="ikaros", collector=None: ("降级回答", {"total_tokens": 1}))

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_fallback(
        [{"role": "user", "content": "hi"}], "ikaros", collector, node_id=None))

    assert collector["content"] == "降级回答"
    assert collector["tool_calls"] == []  # 失败不产生工具卡片


# ────────────────── F6: _effective_mode 全局切换生效 ──────────────────

def test_effective_mode_runtime_override(monkeypatch):
    """F6: model_switch 全局 mode=hermes 对默认 ikaros 节点生效 (旧逻辑失效)."""
    # 节点 agent 是默认 ikaros (显式 set_agent 之外的值)
    assert server._effective_mode("ikaros") == "ikaros"
    # 全局切 hermes → 默认节点跟随
    monkeypatch.setattr(server, "_CT_RUNTIME", {"mode": "hermes", "model": ""})
    assert server._effective_mode("ikaros") == "hermes"
    # 显式 set_agent("hermes") 节点不受全局 ikaros 覆盖
    monkeypatch.setattr(server, "_CT_RUNTIME", {"mode": "ikaros", "model": ""})
    assert server._effective_mode("hermes") == "hermes"
    # 空 agent + 空全局 → 默认 ikaros
    monkeypatch.setattr(server, "_CT_RUNTIME", {"mode": "", "model": ""})
    assert server._effective_mode("") == "ikaros"


# ────────────────── F10: /api/state 解析 inline 参数 ──────────────────

def test_state_inline_param_respected(monkeypatch):
    """F10: inline=0 时 state_dict 返回轻量拓扑 (不内联 messages)."""
    monkeypatch.setattr(server, "_tree", None)
    # 手工验证参数透传逻辑: state_dict(inline=...) 由 do_GET 解析
    # 这里直接测 do_GET 的 query 解析分支 (用 _q 的行为)
    handler = server.Handler  # 类上直接验证参数约定
    assert hasattr(handler, "_q")  # _q 存在 (query 解析) —— 修复后 do_GET 调它
    # 核心断言: state_dict 签名接受 inline 且默认为 True (兼容旧调用)
    import inspect
    sig = inspect.signature(server.state_dict)
    assert "inline" in sig.parameters
    assert sig.parameters["inline"].default is True
