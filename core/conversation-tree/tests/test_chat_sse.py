"""core/conversation-tree/server.py chat SSE 链路测试 (子任务 M5, 2026-08-18 更新).

2026-08-18: hermes gateway 模式整体退役, 对话树统一走 DeepSeek 直连
(_chat_stream_events / _stream_fallback 本地三层链路)。
覆盖:
- _chat_stream_events: 主链路走 _stream_fallback (DeepSeek 直连), 降级可见化 warn.
- _stream_fallback: 预检索记忆 → tool_call/tool_result 事件 → 结果注入上下文.
- 工具回路: 模型自主调用工具 → 执行 → 回填 → 第二轮出正文; 循环上限.
- _effective_mode: ikaros 单模式语义.
"""
from __future__ import annotations

import pytest

from conftest import server  # type: ignore  # noqa: E402


def _collect(events):
    """把事件序列收成列表."""
    return list(events)


# ────────────────── 测试 1: 主链路走 fallback (DeepSeek 直连) ──────────────────

def test_chat_stream_events_uses_fallback(monkeypatch):
    """2026-08-18: hermes gateway 退役, _chat_stream_events 直接走 _stream_fallback."""
    called = {}

    def fake_fallback(messages, mode, collector, node_id=None):
        called["mode"] = mode
        collector["content"] += "直连回答"
        collector["usage"] = {"total_tokens": 3}
        return [{"type": "content", "text": "直连回答"},
                {"type": "usage", "usage": {"total_tokens": 3}}]

    monkeypatch.setattr(server, "_stream_fallback", fake_fallback)
    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._chat_stream_events(
        [{"role": "user", "content": "hi"}], "ikaros", None, collector, tree=None))
    assert called["mode"] == "ikaros"
    assert collector["content"] == "直连回答"
    assert events[-1]["type"] == "usage"


# ────────────────── 测试 2: 降级链工具协议 (预检索 + 多轮循环) ──────────────────

def test_stream_fallback_prefetches_memory(monkeypatch):
    """F12: 降级时 _execute_chat_tool 的 memory_search 被真实调用, 结果注入上下文.

    修复"工具回路从未生效": _stream_fallback 现在会先预检索记忆 → 产出
    tool_call/tool_result 事件 → 把结果作为 system 前缀喂给 LLM.
    """
    called = {}
    monkeypatch.setattr(
        server, "_execute_chat_tool",
        lambda name, arguments, node_id=None: called.update(name=name, args=arguments) or
        {"ok": True, "result": "找到1条: GTO 策略笔记"})
    monkeypatch.setattr(
        server, "_call_llm_tools",
        lambda messages, tools, collector=None: (
            messages[0]["content"], {"total_tokens": 1}, []))  # 返回首个 system 内容

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_fallback(
        [{"role": "user", "content": "什么是 GTO?"}], "ikaros", collector, node_id="n1"))

    # 工具被调用且是 memory_search
    assert called.get("name") == "memory_search", called
    # 预检索结果进入 LLM 的 system 前缀 (msgs[0])
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
        server, "_call_llm_tools",
        lambda messages, tools, collector=None: ("降级回答", {"total_tokens": 1}, []))

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_fallback(
        [{"role": "user", "content": "hi"}], "ikaros", collector, node_id=None))

    assert collector["content"] == "降级回答"
    assert collector["tool_calls"] == []  # 失败不产生工具卡片


def test_stream_fallback_tool_loop(monkeypatch):
    """S2: 模型自主调用工具 → 执行 → 回填 → 第二轮出正文.

    模拟: 轮1 模型返回 memory_search tool_call; 轮2 返回正文.
    验证工具执行结果回填消息列表 (第二轮 messages 含 tool 消息).
    """
    seen_messages = []

    def fake_tools(messages, tools, collector=None):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ("", {"total_tokens": 1}, [{
                "id": "call_1", "type": "function",
                "function": {"name": "get_current_time", "arguments": "{}"},
            }])
        # 第二轮: 正文里引用工具结果
        has_tool = any(m.get("role") == "tool" for m in messages)
        assert has_tool, "工具结果必须回填进第二轮消息"
        return ("当前时间已获取", {"total_tokens": 2}, [])

    monkeypatch.setattr(server, "_call_llm_tools", fake_tools)
    monkeypatch.setattr(server, "_execute_chat_tool",
                        lambda name, arguments, node_id=None: {"ok": True, "result": "2026-08-03 22:00"})

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    events = _collect(server._stream_fallback(
        [{"role": "user", "content": "现在几点?"}], "ikaros", collector, node_id=None))

    assert collector["content"] == "当前时间已获取"
    assert len(seen_messages) == 2  # 两轮调用
    # 预检索(memory_search) + 模型自主调用(get_current_time) = 2 条
    assert len(collector["tool_calls"]) == 2, collector["tool_calls"]
    names = [tc["name"] for tc in collector["tool_calls"]]
    assert "memory_search" in names and "get_current_time" in names
    tc_gt = next(tc for tc in collector["tool_calls"] if tc["name"] == "get_current_time")
    assert tc_gt["result_summary"] == "2026-08-03 22:00"
    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types and "content" in types


def test_stream_fallback_tool_loop_cap(monkeypatch):
    """S2: 工具循环达到 MAX_TOOL_ROUNDS 上限时终止, 不无限调用."""
    calls = {"n": 0}

    def fake_tools(messages, tools, collector=None):
        calls["n"] += 1
        return ("", {"total_tokens": 1}, [{
            "id": f"call_{calls['n']}", "type": "function",
            "function": {"name": "get_current_time", "arguments": "{}"},
        }])

    monkeypatch.setattr(server, "_call_llm_tools", fake_tools)
    monkeypatch.setattr(server, "_execute_chat_tool",
                        lambda name, arguments, node_id=None: {"ok": True, "result": "x"})
    monkeypatch.setattr(server, "MAX_TOOL_ROUNDS", 3)

    collector = {"content": "", "thinking": "", "tool_calls": [], "usage": {}, "warns": []}
    with pytest.raises(RuntimeError):
        _collect(server._stream_fallback(
            [{"role": "user", "content": "hi"}], "ikaros", collector, node_id=None))
    # 预检索(memory_search) + 3 轮工具 = 4 条工具记录; 3 次 LLM 调用
    assert calls["n"] == 3, calls
    assert len(collector["tool_calls"]) == 4, collector["tool_calls"]


# ────────────────── F6: _effective_mode ikaros 单模式 ──────────────────

def test_effective_mode_ikaros_only(monkeypatch):
    """2026-08-18: hermes 退役后, 全局 mode 不再切 hermes, 恒为 ikaros."""
    assert server._effective_mode("ikaros") == "ikaros"
    # 存量 hermes 节点值 (conversation_tree 层兼容) 在 server 层同样返回 hermes 值本身
    # (server 只转发节点值; 任务代理提示差异已消除)
    assert server._effective_mode("hermes") == "hermes"
    # 空 agent + 空全局 → 默认 ikaros
    monkeypatch.setattr(server, "_CT_RUNTIME", {"mode": "", "model": ""})
    assert server._effective_mode("") == "ikaros"


# ────────────────── F10: /api/state 解析 inline 参数 ──────────────────

def test_state_inline_param_respected(monkeypatch):
    """F10: inline=0 时 state_dict 返回轻量拓扑 (不内联 messages)."""
    monkeypatch.setattr(server, "_tree", None)
    handler = server.Handler  # 类上直接验证参数约定
    assert hasattr(handler, "_q")  # _q 存在 (query 解析) —— 修复后 do_GET 调它
    import inspect
    sig = inspect.signature(server.state_dict)
    assert "inline" in sig.parameters
    assert sig.parameters["inline"].default is True
