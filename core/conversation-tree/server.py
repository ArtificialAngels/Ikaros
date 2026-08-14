"""对话树面板后端服务 (V5 store 集成版).

- 用标准库 ThreadingHTTPServer，零第三方依赖。
- 对话树引擎为唯一事实源: 拓扑 JSON 只存 v5_memory_id + summary,
  对话本体走 V5 store (SQLite)。
- 同一来源 serve UI (index.html) + 暴露 REST API。

启动: python core/conversation-tree/server.py --port 48920
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 让本服务能 import memory_v5.conversation_tree + memory_v5.store
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
# 2026-08-05: 面板以 safe_path (-P) 拉起本脚本, 脚本目录不进 sys.path →
# 同目录 rescue_tools 导入失败 (ModuleNotFoundError, _RESCUE 静默降级)。
# 显式加入脚本目录, 保证 import rescue_tools 在面板 env 下也命中。
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# 附件上传目录（相对项目根 data/，避免被 git 跟踪的源码目录污染）
_UPLOAD_DIR = _HERE.parent.parent / "data" / "conversation-tree-uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
_UPLOAD_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".webp": "image/webp", ".svg": "image/svg+xml", ".pdf": "application/pdf",
                ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
                ".json": "application/json", ".zip": "application/zip",
                ".py": "text/plain; charset=utf-8", ".js": "application/javascript",
                ".ts": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
                ".csv": "text/csv; charset=utf-8", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4"}
# 任务2: 上传上限 50MB (对齐 hermes-studio), 8 字节随机 hex 文件名防冲突
_UPLOAD_MAX_BYTES = 50 * 1024 * 1024
# 允许以文本方式读入上下文发给 LLM 的扩展名 (其他文件仅透出链接/元信息)
_TEXT_EXTENSIONS = {".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json",
                    ".csv", ".html", ".css", ".yaml", ".yml", ".toml", ".ini",
                    ".log", ".sh", ".bat", ".cmd", ".sql", ".xml", ".go", ".rs",
                    ".java", ".c", ".cpp", ".h", ".rb", ".php", ".r", ".lua",
                    ".toml", ".cfg", ".conf", ".diff", ".patch"}
# 文本附件读入上下文的内容上限 (防超大文件把上下文撑爆)
_UPLOAD_TEXT_CAP = 20000

import memory_v5.conversation_tree as ct  # noqa: E402
from memory_v5.conversation_tree import V5_DATA_DIR  # noqa: E402
from memory_v5 import store as v5s     # noqa: E402
# B2: 任务事件总线 (herdr events.subscribe 语义内化); core/ 已在 sys.path
from taskbus import EventBus, exec_state_event  # noqa: E402
# ── gateway 自救工具集 (2026-08-05): ikaros_* 工具由对话树侧直接执行,
# 不经过 gateway 透出 —— gateway 挂掉时诊断/日志/重启仍可用 (鸡生蛋解药)。
# 同目录模块; 缺失/异常时 _RESCUE=None 降级为旧逻辑 (fail-open)。
try:
    import rescue_tools as _RESCUE_MOD  # noqa: E402
    _RESCUE = _RESCUE_MOD
    sys.stderr.write(f"[ct] rescue_tools loaded ({len(_RESCUE.SCHEMAS)} ikaros_* tools)\n")
except Exception as _imp_err:
    _RESCUE = None
    sys.stderr.write(f"[ct] rescue_tools import FAILED: {type(_imp_err).__name__}: {_imp_err}\n")

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
# Hermes agent runtime 端点.
# 默认走 Ikaros 自有的 studio 式「0 侵入」包装层 core/hermes-bridge (:8650) —— 它透明代理
# 纯净 Hermes gateway(:8642) 的原生 session-chat 端点, 并把 reasoning/工具/正文翻译成
# 对话树方言. 这样 runtime/hermes-agent 工作树可保持 100% 纯净 (无 api_server.py 补丁).
# bridge 不可达时本服务自动降级到本地 DeepSeek (见 health probe + 流式降级逻辑), 不会硬崩.
# 设 HERMES_AGENT_URL="" 可禁用 agent runtime, 回退到 chat 补全 + Hermes 任务代理提示;
# 亦可覆盖为直连 :8642 (http://127.0.0.1:8642/v1/chat/completions) 绕过 bridge.
# gateway 需 Bearer API_SERVER_KEY (默认 ikaros-gateway-key, 由 :8642 gateway 进程设定; 见 core/dashboard/server.py:165).
HERMES_AGENT_URL = os.environ.get("HERMES_AGENT_URL", "http://127.0.0.1:8650/v1/chat/completions").strip() or None
# 任务2: 多模态直连端点 —— bridge :8650 会把 content 列表拍平成文本, 无法透传图片,
# 故含 image_url 的消息直接打 gateway :8642 原生的 OpenAI-wire 端点 (其
# _normalize_multimodal_content 接受 image_url base64; 该端点同样流 hermes.tool.progress)。
HERMES_GATEWAY_CHAT_URL = os.environ.get(
    "HERMES_GATEWAY_URL", "http://127.0.0.1:8642"
).rstrip("/") + "/v1/chat/completions"
# gateway 鉴权 token; 默认 ikaros-gateway-key (由 :8642 gateway 进程设定; 见 core/dashboard/server.py:165).
# 2026-08-03: 优先从 HERMES_HOME/.env (data/hermes-agent/.env) 读真实 API_SERVER_KEY,
# 与 dashboard server 同源, 避免 401.
HERMES_AGENT_KEY = os.environ.get("API_SERVER_KEY", "").strip()
if not HERMES_AGENT_KEY:
    try:
        _envp = _HERE.parent.parent / "data" / "hermes-agent" / ".env"
        if _envp.exists():
            for _l in _envp.read_text(encoding="utf-8", errors="replace").splitlines():
                _l = _l.strip()
                if _l.startswith("API_SERVER_KEY=") and not _l.startswith("#"):
                    HERMES_AGENT_KEY = _l.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    except Exception:
        pass
if not HERMES_AGENT_KEY:
    HERMES_AGENT_KEY = "ikaros-gateway-key"
HERMES_AGENT_MODEL = os.environ.get("HERMES_AGENT_MODEL", "hermes").strip()
LOCAL_CHAT_URL = os.environ.get("IKAROS_LOCAL_LLM_URL", "http://127.0.0.1:8080").rstrip("/") + "/v1/chat/completions"
LLM_TIMEOUT = int(os.environ.get("CT_LLM_TIMEOUT", "120"))
# R2: 分离连接与读取超时. 连接用短超时(快速失败), 流式读取用长超时(允许长生成
# 的静默间隙, 避免被 socket 超时掐断). 非流式补全同样用长读取超时(整段生成时间).
LLM_CONNECT_TIMEOUT = int(os.environ.get("CT_LLM_CONNECT_TIMEOUT", "20"))
LLM_READ_TIMEOUT = int(os.environ.get("CT_LLM_READ_TIMEOUT", "600"))
MAX_CONTEXT_MSGS = int(os.environ.get("CT_MAX_CONTEXT_MSGS", "50"))

# ── 运行时模型切换 (2026-08-03): POST /api/model_switch 覆盖默认 ──
# mode: ""=节点属性优先(默认) | "hermes" | "ikaros";  model: 模型名覆盖
_CT_RUNTIME = {"mode": "", "model": ""}

def _effective_mode(node_agent: str | None) -> str:
    """节点 agent 显式值优先; 无显式值时用运行时全局 mode; 再默认 ikaros。

    F6: 修复 model_switch 全局 mode 失效 —— 节点 agent 默认恒为 "ikaros"
    (from_dict 默认 + add_turn 继承), 旧逻辑 `if node_agent: return node_agent`
    永远命中, 导致 runtime mode=hermes 切换形同虚设。
    现在: set_agent 显式设置的值 (> 0 字节点) 优先; 全局 _CT_RUNTIME["mode"]
    作为 fallback; 两者皆空 → "ikaros"。
    """
    if node_agent and node_agent != "ikaros":
        # 显式 set_agent("hermes") 的节点不被全局模式覆盖
        return node_agent
    return _CT_RUNTIME["mode"] or (node_agent or "ikaros")

def _effective_model(mode: str) -> str:
    if _CT_RUNTIME["model"]:
        return _CT_RUNTIME["model"]
    return HERMES_AGENT_MODEL if mode == "hermes" else CT_DEEPSEEK_MODEL


def _load_hermes_config():
    """加载 hermes 运行配置 (data/hermes-agent/config.yaml)。

    返回 (parsed_yaml, raw_text)。yaml 缺失/解析失败 → parsed=None, 调用方退回正则。
    L6: 不再依赖 `^model:` 顶格正则, 改为 yaml 解析, 模型块无论嵌套多深都能取到。
    """
    try:
        cfg = _HERE.parent.parent / "data" / "hermes-agent" / "config.yaml"
        txt = cfg.read_text(encoding="utf-8")
    except Exception:
        return None, None
    data = None
    try:
        import yaml  # PyYAML; 缺失时退回正则
        data = yaml.safe_load(txt)
    except Exception:
        data = None
    return data, txt


def _hermes_model_context(model: str) -> int:
    """从 hermes 模型元数据表获取**实际** context window (2026-08-03)。

    复用 runtime/hermes-agent/agent/model_metadata.DEFAULT_CONTEXT_LENGTHS (hermes 权威
    模型参数表: deepseek-v4-flash/pro=1M, claude-opus-4.8=1M 等), 最长键优先模糊匹配;
    "hermes" 是 gateway 抽象名 → 用 hermes 配置的 model.default 查真实模型。
    失败回退 CT_CONTEXT_WINDOW。
    """
    probe = model
    if model == "hermes":
        data, txt = _load_hermes_config()
        dflt = None
        if isinstance(data, dict):
            mb = data.get("model") or {}
            if isinstance(mb, dict):
                dflt = mb.get("default")
        if not dflt and txt:
            m = re.search(r"default:\s*(\S+)", txt)  # 宽松回退 (yaml 未取到时)
            if m:
                dflt = m.group(1)
        if dflt:
            probe = dflt
    try:
        sys.path.insert(0, str(_HERE.parent / "hermes"))
        from agent import model_metadata as _mm
        ml = (probe or "").lower()
        for k, v in sorted(_mm.DEFAULT_CONTEXT_LENGTHS.items(),
                           key=lambda x: len(x[0]), reverse=True):
            if k in ml:
                return int(v)
        return int(_mm.DEFAULT_FALLBACK_CONTEXT)
    except Exception:
        return CT_CONTEXT_WINDOW


def _hermes_models() -> list[dict]:
    """从 hermes 运行配置 (data/hermes-agent/config.yaml) 提取模型候选 (2026-08-03)。

    L6: 用 yaml 解析 (而非 `^model:` 顶格正则), 模型块无论嵌套多深都能取到;
    默认模型以 "hermes" 抽象名透传 gateway, MoA 参考/聚合模型也一并列出。
    yaml 缺失时退回正则 (旧行为)。
    """
    cands: list[dict] = []
    data, txt = _load_hermes_config()

    def _ctx(mname: str) -> int:
        return _hermes_model_context(mname)

    def _add(mname: str, label: str, ctx: int) -> None:
        if mname and mname not in {c.get("model") for c in cands}:
            cands.append({"mode": "hermes", "model": mname,
                          "label": label, "context_window": ctx})

    if isinstance(data, dict):
        mb = data.get("model") or {}
        if isinstance(mb, dict):
            dflt = mb.get("default"); prov = mb.get("provider")
            if dflt:
                _add("hermes", f"Hermes Gateway（默认 {dflt} · {prov or '?'}）",
                     _ctx(dflt))
        # 通用递归扫描: 任何含 model+provider 的映射/列表项 (MoA references 等)
        def _scan(node):
            if isinstance(node, dict):
                mm = node.get("model"); pp = node.get("provider")
                if mm and pp:
                    _add(mm, f"{mm}（hermes · {pp}）", _ctx(mm))
                for v in node.values():
                    _scan(v)
            elif isinstance(node, list):
                for v in node:
                    _scan(v)
        _scan(data)
        agg = data.get("aggregator") or {}
        if isinstance(agg, dict):
            am = agg.get("model"); ap = agg.get("provider")
            if am:
                _add(am, f"{am}（MoA 聚合 · {ap or '?'}）", _ctx(am))
    elif txt:
        # 正则回退 (yaml 缺失/解析失败)
        m = re.search(r"^model:\s*\n\s+default:\s*(\S+)\s*\n\s+provider:\s*(\S+)", txt, re.M)
        if m:
            _add("hermes", f"Hermes Gateway（默认 {m.group(1)} · {m.group(2)}）",
                 _ctx(m.group(1)))
    if not cands:
        cands.append({"mode": "hermes", "model": "hermes", "label": "Hermes Gateway",
                      "context_window": CT_CONTEXT_WINDOW})
    return cands


def _hermes_providers_status() -> list[dict]:
    """provider 配置状态 (只读, 脱敏: 只报告 key 是否已配置, 绝不返回 key 本身)。

    信息源: data/hermes-agent/config.yaml 的 model.default/provider 与
    custom_providers 列表; 内置 provider 的 key 走环境变量 (``{NAME}_API_KEY``)。
    """
    data, _ = _load_hermes_config()
    out: list[dict] = []

    def _has_env_key(name: str) -> bool:
        env = (name or "").replace("-", "_").upper() + "_API_KEY"
        return bool(os.environ.get(env))

    if isinstance(data, dict):
        mb = data.get("model") or {}
        if isinstance(mb, dict):
            prov = mb.get("provider")
            if prov:
                out.append({
                    "name": str(prov), "role": "default",
                    "has_key": _has_env_key(str(prov)),
                    "model": mb.get("default"),
                    "source": "env {0}_API_KEY".format(str(prov).replace("-", "_").upper()),
                })
        cps = data.get("custom_providers") or []
        if isinstance(cps, list):
            for cp in cps:
                if isinstance(cp, dict) and cp.get("name"):
                    out.append({
                        "name": str(cp.get("name")), "role": "custom",
                        "has_key": bool(cp.get("api_key")),
                        "base_url": cp.get("base_url"),
                        "models": cp.get("models") or [],
                    })
    return out

# Hermes 任务代理 base system prompt (与 Ikaros 伴侣人格区分): 偏执行/工具/任务导向.
HERMES_AGENT_PROMPT = (
    "You are Hermes, an autonomous task agent operating inside Ikaros. "
    "You execute tasks decisively: break problems down, use available tools/skills when helpful, "
    "and prefer concrete results over lengthy exposition. Be precise and concise. "
    "When the user is exploring ideas rather than requesting action, you may still answer directly, "
    "but keep a task-oriented, capable tone. Markdown for code/structure when appropriate."
)

# ── Ikaros 人格来源 (V5 同步的身份/心绪) ──────────────────────
# server.py 位于 core/conversation-tree/ ; 根目录 = parent.parent
_IKAROS_ROOT = _HERE.parent.parent
_AXIOM_PATH = _IKAROS_ROOT / "config" / "identity" / "axiom.md"
_SOUL_PATH = _IKAROS_ROOT / "data" / "hermes-agent" / "SOUL.md"
_SELF_MODEL_PATH = _HERE.parent / "memory_v5" / "data" / "v5" / "self_model.json"

# ── LLM 调用 (本地降级三层 chat 补全, 主链路走 gateway) ──
def _urlopen_with_timeout(req, connect_timeout: int = LLM_CONNECT_TIMEOUT,
                          read_timeout: int = LLM_READ_TIMEOUT):
    """打开 HTTP 请求并分离连接/读取超时 (R2).

    - 连接阶段用 ``connect_timeout``(短, 不可达时快速失败, 触发降级);
    - 连接建立后把底层 socket 的读取超时改为 ``read_timeout``(长), 允许流式场景下
      模型长生成的静默间隙, 不被单一 timeout 掐断 (原 urllib 的 timeout 同时约束两者).

    返回已打开的 response, 调用方用 ``with`` 管理生命周期.
    """
    resp = urllib.request.urlopen(req, timeout=connect_timeout)
    try:
        fp = getattr(resp, "fp", None)
        raw = getattr(fp, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(read_timeout)
    except Exception:
        pass
    return resp


def _call_llm(messages: list[dict], agent: str = "ikaros",
              collector: "dict | None" = None) -> tuple[str, dict]:
    """两层 chat 补全 (DeepSeek → Local LLM) 降级链路.

    返回 (content, usage). 任一 provider 成功即返回; 全部失败抛 RuntimeError.
    collector 非 None 时把用量写入 collector["usage"] (供 SSE usage 事件).
    注意: 此函数是 gateway 不可达时的**降级**通道 (H2 恢复), 非主链路。
    """
    # 任务2: 文本端点不支持 ContentBlock 列表 —— 附件消息降级为纯文本 (图片→占位)
    messages = [dict(m, content=_flatten_openai_content(m.get("content")))
                for m in messages]
    errors: list[str] = []

    # 1) DeepSeek API
    if _DEEPSEEK_KEY:
        try:
            ds_body = json.dumps({
                # S2: 用 CT_DEEPSEEK_MODEL (默认 deepseek-v4-flash) 替代已废弃的
                # deepseek-chat 别名 (AGENTS.md: deepseek-chat 已废弃)
                "model": CT_DEEPSEEK_MODEL, "messages": messages,
                "max_tokens": 2048, "temperature": 0.7, "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                DEEPSEEK_CHAT_URL, data=ds_body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {_DEEPSEEK_KEY}"},
            )
            with _urlopen_with_timeout(req, LLM_CONNECT_TIMEOUT, LLM_READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"].get("content", "")
                usage = data.get("usage", {}) or {}
                if content.strip():
                    if collector is not None:
                        collector["usage"] = usage
                    return content.strip(), usage
                errors.append("DeepSeek returned empty content")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            errors.append(f"DeepSeek: {e}")
        except Exception as e:
            errors.append(f"DeepSeek unexpected: {e}")
    else:
        errors.append("DeepSeek: no API key")

    # 2) Local LLM
    try:
        l_body = json.dumps({
            "model": "local-llm", "messages": messages,
            "max_tokens": 2048, "temperature": 0.7, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(LOCAL_CHAT_URL, data=l_body,
                                     headers={"Content-Type": "application/json"})
        with _urlopen_with_timeout(req, LLM_CONNECT_TIMEOUT, LLM_READ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content", "")
            if not content.strip():
                content = data["choices"][0]["message"].get("reasoning_content", "")
            usage = data.get("usage", {}) or {}
            if content.strip():
                if collector is not None:
                    collector["usage"] = usage
                return content.strip(), usage
            errors.append("Local LLM returned empty content")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        errors.append(f"Local: {e}")
    except Exception as e:
        errors.append(f"Local unexpected: {e}")

    raise RuntimeError("LLM unavailable: " + "; ".join(errors))


# ── S2: 完整工具协议 (降级链自主工具调用) ──────────────────────────
# 只读工具 schema (OpenAI function-calling 格式), 与 _execute_chat_tool 对应。
# 由 _call_llm_tools 传给 DeepSeek, 模型可自主决定调用; 结果回填后多轮循环。
_READONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "在 V5 长期记忆中检索与 query 相关的内容 (按当前树域/会话加权)",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前系统时间",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "branch_overview",
            "description": "查看当前分支的路径脉络 (根→当前节点的摘要)",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gateway_ensure",
            "description": "检查 Hermes gateway (:8642) 健康状态; 不可达时自动拉起并等待就绪 (降级自救)。"
                           "当 gateway/Hermes 服务疑似挂掉、工具不可用、需要恢复完整工具链时调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ikaros_* 自救工具集 (gateway 独立通道, 2026-08-05): 进程/端口诊断、日志读取、
# 配置读取、gateway 重启、herdr 管道兜底。与 _execute_chat_tool 分派对应。
if _RESCUE is not None:
    _READONLY_TOOLS = _READONLY_TOOLS + list(_RESCUE.SCHEMAS)

# 工具循环上限: 防止模型无限调用工具.
# 2026-08-05: 4→6, 给诊断→日志→重启→复查 的完整自救链留足轮次 (ikaros_* 工具集).
MAX_TOOL_ROUNDS = 6


def _call_llm_tools(
    messages: list[dict],
    tools: list[dict],
    collector: "dict | None" = None,
) -> tuple[str, dict, list]:
    """带 tools 协议的两层补全 (S2): 返回 (content, usage, tool_calls).

    - DeepSeek 层支持 tools (OpenAI function-calling 兼容), 模型可返回 tool_calls;
    - Local LLM 不支持 tools 协议时返回空 content 由上层降级,
      因此本函数只对 DeepSeek 传 tools, 本地层回退普通补全 (不带 tools)。
    - 返回的 tool_calls 为原始 OpenAI 格式列表:
      [{"id":..., "type":"function", "function":{"name":..., "arguments":"{...}"}}, ...]
    """
    # 任务2: DeepSeek 文本端点不支持 ContentBlock 列表 —— 附件消息拍平成纯文本
    messages = [dict(m, content=_flatten_openai_content(m.get("content")))
                for m in messages]
    errors: list[str] = []

    # 1) DeepSeek API (唯一支持 tools 的降级层)
    if _DEEPSEEK_KEY:
        try:
            body = {
                "model": CT_DEEPSEEK_MODEL, "messages": messages,
                "max_tokens": 2048, "temperature": 0.7, "stream": False,
                "tools": tools,
            }
            req = urllib.request.Request(
                DEEPSEEK_CHAT_URL, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {_DEEPSEEK_KEY}"},
            )
            with _urlopen_with_timeout(req, LLM_CONNECT_TIMEOUT, LLM_READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                tool_calls = msg.get("tool_calls") or []
                usage = data.get("usage", {}) or {}
                if content.strip() or tool_calls:
                    if collector is not None:
                        collector["usage"] = usage
                    return content.strip(), usage, tool_calls
                errors.append("DeepSeek returned empty response")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            errors.append(f"DeepSeek: {e}")
        except Exception as e:
            errors.append(f"DeepSeek unexpected: {e}")
    else:
        errors.append("DeepSeek: no API key")

    # 2) Local LLM (不带 tools)
    try:
        l_body = json.dumps({
            "model": "local-llm", "messages": messages,
            "max_tokens": 2048, "temperature": 0.7, "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(LOCAL_CHAT_URL, data=l_body,
                                     headers={"Content-Type": "application/json"})
        with _urlopen_with_timeout(req, LLM_CONNECT_TIMEOUT, LLM_READ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"].get("content", "")
            if not content.strip():
                content = data["choices"][0]["message"].get("reasoning_content", "")
            usage = data.get("usage", {}) or {}
            if content.strip():
                if collector is not None:
                    collector["usage"] = usage
                return content.strip(), usage, []
            errors.append("Local LLM returned empty content")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        errors.append(f"Local: {e}")
    except Exception as e:
        errors.append(f"Local unexpected: {e}")

    raise RuntimeError("LLM unavailable: " + "; ".join(errors))


# ── Ikaros 人格 + V5 记忆注入 (chat 接入 Ikaros V5) ──────────────

def _warn(collector: "dict | None", message: str) -> None:
    """降级可见化: 把一条 warn 消息记进 collector, 由 /api/chat 以 SSE warn 事件透出前端.

    collector 为 None (纯函数被测试直接调用) 时静默丢弃, 不破坏 fail-open 语义.
    """
    if collector is not None:
        collector.setdefault("warns", []).append(message)


# 树感知压缩 (TreePathCompressor): 模块级守卫导入. 旧代码漏 import 导致 NameError 被
# except 静默吞掉, "树感知压缩"实际从未生效 (一直跑线性回退) —— 本次修复并加 warn 透出.
try:
    from memory_v5.extensions.tree_adapter import build_tree_aware_context  # noqa: F401
except Exception:  # 零硬依赖: tree_adapter 缺失/离线时降级为 None, 调用点走回退
    build_tree_aware_context = None


def build_branch_context_block(tree, node_id: "str | None") -> str:
    """当前分支脉络块: 根→当前节点路径 (每节点 #depth branch_label: summary) + agent 归属.

    供 hermes 模式注入 gateway 的树域上下文 (复用 branch_overview 工具的路径摘要逻辑).
    fail-open: 树缺失/异常返回空串, 不阻塞主线.
    """
    try:
        if tree is None:
            return ""
        nid = node_id or getattr(tree, "current_id", None)
        path = tree.get_path(nid) if nid else []
        if not path:
            return "(empty branch)"
        lines = []
        for n in path:
            s = (n.summary or "").strip() or "(no summary)"
            lines.append(f"#{n.depth} {n.branch_label or 'main'}: {s[:200]}")
        cur = path[-1]
        agent = getattr(cur, "agent", "ikaros") or "ikaros"
        return ("Current branch path (root → current):\n" + "\n".join(lines)
                + f"\nCurrent node agent: {agent}")
    except Exception:
        return ""


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


def build_v5_memory_block(node_id: str | None, query: str, collector: "dict | None" = None,
                           tree: "ct.ConversationTree | None" = None) -> str:
    """树域语义检索 (V5 记忆引擎): 按当前 query 检索相关记忆并做树域加权。

    依赖刚落地的存储打标 (node:/branch:/session:), 命中路径/分支/本会话的记忆优先,
    且按会话隔离过滤 (H1, 见 tree_adapter.tree_scoped_retrieve). 检索后端不可用
    时 fail-open 返回空串, 并向 collector 记一条 warn (降级可见化)。
    返回可直接拼进 system 的文本块。
    """
    t = tree or _tree
    if t is None:
        return ""
    try:
        from memory_v5.extensions.tree_adapter import tree_scoped_retrieve
        results = tree_scoped_retrieve(t, node_id, query, top_k=5)
    except Exception as e:
        _warn(collector, f"树域记忆检索不可用，已跳过记忆注入（{e}）")
        return ""
    lines: list[str] = []
    for r in results:
        txt = (r.get("content") or "").strip()
        if txt:
            scope = r.get("tree_scope", "global")
            lines.append(f"[{scope}] {txt}")
    return "\n".join(lines)


def build_system_prompt(mode: str) -> str:
    """ekko buildSystemPrompt 的等价: 按代理模式选 base 人格。

    - "hermes" → Hermes 任务代理提示 (执行/工具/任务导向)
    - 其他     → Ikaros 伴侣人格 (axiom + SOUL + 动态心绪)
    """
    if mode == "hermes":
        return HERMES_AGENT_PROMPT
    return build_ikaros_persona()


# ── 任务2: 附件 → OpenAI ContentBlock 协议 ─────────────────────────────
def _flatten_openai_content(content) -> str:
    """把 OpenAI 消息 content (str 或 [{"type":..., ...}] 列表) 拍平成纯文本.

    供不支持多模态的文本端点 (DeepSeek/Hermes dashboard/Local) 与树摘要使用:
    text 块取 text; image_url 块降级为占位说明 (无视觉能力的模型读不到图).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                ptype = str(item.get("type") or "").strip().lower()
                if ptype == "text":
                    parts.append(str(item.get("text") or ""))
                elif ptype == "image_url":
                    img = item.get("image_url") or {}
                    url = img.get("url") if isinstance(img, dict) else img
                    name = (img.get("name") if isinstance(img, dict) else None) or ""
                    parts.append(f"[图片附件 {name}]".strip())
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _resolve_upload_path(url_or_name: str) -> Path | None:
    """把前端传的 ``/uploads/<name>`` 或 ``<name>`` 解析为本地文件路径.

    只接受 _UPLOAD_DIR 下的文件名 (防目录穿越), 不存在返回 None.
    """
    raw = (url_or_name or "").strip()
    name = raw[len("/uploads/"):] if raw.startswith("/uploads/") else raw
    name = Path(name).name  # 只留 basename, 剥掉任何路径成分
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    f = _UPLOAD_DIR / name
    return f if f.is_file() else None


def _read_text_attachment(path: Path, cap: int = _UPLOAD_TEXT_CAP) -> str:
    """读文本附件内容 (UTF-8, 容错 replace; 超长截断). 非文本返回空串."""
    try:
        raw = path.read_bytes()[: cap * 2]
    except Exception:
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return text if len(text) <= cap else text[:cap] + "\n… (内容超长已截断)"


def _attachment_content_blocks(attachments: list[dict]) -> list[dict]:
    """附件引用列表 → OpenAI ContentBlock 协议列表.

    - 图片: 读文件 → ``{"type":"image_url","image_url":{"url":"data:<mime>;base64,..."}}``
    - 文本类: 读内容 → ``{"type":"text","text":"### 附件 <name>\n<content>"}``
    - 其他 (二进制/不可读): 仅透出文件名与大小占位
    单个附件读取出错时降级为占位块, 不阻塞整条消息.
    """
    blocks: list[dict] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        name = str(att.get("name") or "附件")
        path = _resolve_upload_path(str(att.get("url") or ""))
        if path is None:
            blocks.append({"type": "text",
                           "text": f"[附件 {name} 未找到，已跳过]"})
            continue
        ext = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
            mime = _UPLOAD_MIME.get(ext, "image/png")
            try:
                b64 = base64.b64encode(path.read_bytes()).decode("ascii")
                blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}", "name": name},
                })
                continue
            except Exception:
                blocks.append({"type": "text",
                               "text": f"[图片附件 {name} 读取失败]"})
                continue
        if ext in _TEXT_EXTENSIONS:
            content = _read_text_attachment(path)
            if content.strip():
                blocks.append({"type": "text",
                               "text": f"### 附件: {name}\n{content}"})
                continue
        size = path.stat().st_size if path.exists() else 0
        blocks.append({"type": "text",
                       "text": f"[附件 {name} ({size} 字节，非文本格式)]"})
    return blocks


def _messages_have_images(messages: list[dict]) -> bool:
    """消息列表里是否含 image_url 内容块 (用于决定是否必须直连 gateway 多模态)."""
    for m in messages or []:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "").lower() == "image_url":
                return True
    return False


def _build_user_content(user_message: str, attachments: list | None):
    """组装 user 消息 content: 无附件 → 纯字符串; 有附件 → OpenAI ContentBlock 列表.

    内容块顺序: 文本(用户输入)在前, 附件块在后 (gateway 按顺序消费).
    """
    if not attachments:
        return user_message
    blocks: list[dict] = []
    if user_message and user_message.strip():
        blocks.append({"type": "text", "text": user_message})
    blocks.extend(_attachment_content_blocks(attachments))
    return blocks if blocks else user_message


def build_chat_messages_v5(node_id: str | None, user_message: str,
                           collector: "dict | None" = None,
                           tree: "ct.ConversationTree | None" = None,
                           attachments: "list | None" = None) -> list[dict]:
    """chat 接入 Ikaros V5 的主入口 (ekko 模式: 分支=session, Ikaros=人格层, Hermes=runtime):

    - ikaros 模式: 人格(build_ikaros_persona: axiom+SOUL+心绪) + 树域记忆 + 树感知压缩
    - hermes 模式: 树域上下文(分支脉络) + 树域记忆, **不注入完整 SOUL** —— gateway
      的 core system 已含 SOUL.md 身份 + AGENTS.md + ikaros_v5 记忆, 外部 system 消息
      会**叠加**在 core 之后 (chat_completion_helpers 追加, 不替换), 故树端只补它
      没有的: 当前分支在树里的位置 + 树域语义记忆, 人格不重复注入。
    任何环节异常都 fail-open 回退到旧的线性上下文, 并向 collector 记 warn (降级可见化)。
    tree: H3 捕获的活动树引用, 默认全局 _tree。
    attachments: 任务2 附件列表 [{name,url,isImage}] → 转 OpenAI ContentBlock
      (图片 image_url base64 / 文本读内容), 见 _build_user_content。
    """
    t = tree or _tree
    mode = "ikaros"
    if t is not None:
        n = t.get_node(node_id) if node_id else t.current
        if n is not None:
            mode = _effective_mode(getattr(n, "agent", None) or None)

    # ── hermes 模式: 树域上下文 + 树域记忆, 人格/工具/技能全由 gateway 提供 ──
    if mode == "hermes":
        # 树域上下文: 分支说明 (路径摘要 + 当前分支标签 + 分支归属)
        branch_ctx = build_branch_context_block(t, node_id)
        # 树域记忆: 复用现有 tree_scoped_retrieve (fail-open, 已含会话隔离 H1)
        mem_block = build_v5_memory_block(node_id, user_message, collector=collector, tree=t)
        system_text = "\n\n".join(filter(None, [
            "You are speaking inside Ikaros' conversation tree. "
            "The branch context below is authoritative for this exchange.",
            branch_ctx,
            ("Relevant tree-scoped memories (V5):\n" + mem_block) if mem_block else "",
        ]))
        try:
            if build_tree_aware_context is None:
                raise RuntimeError("tree_adapter unavailable (build_tree_aware_context)")
            ctx = build_tree_aware_context(
                t, node_id, system_prompt=system_text, extra_memory=None,
            )
            return ctx + [{"role": "user", "content": _build_user_content(user_message, attachments)}]
        except Exception as e:
            _warn(collector, f"树感知压缩失败，已回退线性上下文（{e}）")
            msgs: list[dict] = [{"role": "system", "content": system_text}]
            try:
                ctx = t.get_context(node_id)
                if len(ctx) > MAX_CONTEXT_MSGS:
                    ctx = ctx[-MAX_CONTEXT_MSGS:]
                msgs.extend(ctx)
            except Exception:
                pass
            msgs.append({"role": "user", "content": user_message})
            return msgs

    # ── ikaros 模式: 人格(伴侣) + 树域记忆 + 树感知压缩 ──
    persona = build_system_prompt(mode)
    mem_block = build_v5_memory_block(node_id, user_message, collector=collector, tree=t)
    try:
        if build_tree_aware_context is None:
            raise RuntimeError("tree_adapter unavailable (build_tree_aware_context)")
        ctx = build_tree_aware_context(
            t, node_id,
            system_prompt=persona,
            extra_memory=mem_block or None,
        )
        return ctx + [{"role": "user", "content": _build_user_content(user_message, attachments)}]
    except Exception as e:
        # 回退: 旧线性上下文 + 人格 + 记忆
        _warn(collector, f"树感知压缩失败，已回退线性上下文（{e}）")
        msgs: list[dict] = [{"role": "system", "content": persona}]
        if mem_block:
            msgs.append({"role": "system", "content": "Relevant memories (V5):\n" + mem_block})
        try:
            ctx = t.get_context(node_id)
            if len(ctx) > MAX_CONTEXT_MSGS:
                ctx = ctx[-MAX_CONTEXT_MSGS:]
            msgs.extend(ctx)
        except Exception:
            pass
        msgs.append({"role": "user", "content": _build_user_content(user_message, attachments)})
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

# ── 多会话 (session) 支持: 每个 session = 一棵独立对话树 ──────────────
# 会话注册表 (sessions.json) 记录所有会话元数据; 每棵树的拓扑 JSON 仍以各自
# persist_key 存放在同一 V5_DATA_DIR, 互不影响. 活动会话的树始终挂在全局 _tree.
SESSIONS_FILE = V5_DATA_DIR / "sessions.json"
SESSION_DEFAULT_PER = "ui_conversation_tree"

_sessions: list[dict] = []
_active_session_id: str | None = None


def _new_session_id() -> str:
    return "sess_" + time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]


def _tree_kwargs() -> dict:
    return dict(_store=v5s.store, _load=_load_str, _search=_search_dicts)


def _load_sessions() -> list[dict]:
    if SESSIONS_FILE.exists():
        try:
            data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_sessions(sessions: list[dict]) -> None:
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_FILE.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")


def _active_session() -> "dict | None":
    if _active_session_id is None:
        return _sessions[0] if _sessions else None
    for s in _sessions:
        if s["id"] == _active_session_id:
            return s
    return _sessions[0] if _sessions else None


def _active_persist_key() -> str:
    s = _active_session()
    return s["persist_key"] if s else SESSION_DEFAULT_PER


def _touch_active_session(tree: "ct.ConversationTree | None" = None) -> None:
    """活动会话元数据打时间戳 + 记录当前节点摘要, 便于侧栏排序/展示.

    tree: 显式传入树引用 (chat 在飞时用捕获的局部 tree, 避免切会话后写错会话)。
    """
    s = _active_session()
    if s is None:
        return
    try:
        s["updated_at"] = time.time()
        t = tree or _tree  # F7: chat 流式期间传局部 tree, 防止全局 _tree 已切到新会话
        if t is not None and t.current_id:
            n = t.get_node(t.current_id)
            if n and n.summary:
                s["current_title"] = n.summary[:80]
        _save_sessions(_sessions)
    except Exception:
        pass


def _make_tree_for(persist_key: str) -> "ct.ConversationTree":
    """创建注入 V5 store 后端的 ConversationTree (指定 persist_key)."""
    return ct.ConversationTree(persist_key=persist_key, **_tree_kwargs())


def _load_tree_for(persist_key: str) -> "ct.ConversationTree | None":
    return ct.ConversationTree.load(persist_key=persist_key, **_tree_kwargs())


def _bind_active_tree(t: "ct.ConversationTree") -> None:
    """把给定树挂为活动树, 并重建 retriever + 注入共享事件总线."""
    global _tree, _retriever
    _tree = t
    _retriever = ct.MemoryRetriever(t)
    t.event_bus = _bus



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
    """向后兼容: 用默认 persist_key 创建树."""
    return _make_tree_for(PERSIST_KEY)


def build_demo() -> None:
    """初始化一棵 poker Demo 树 (对话内容走 V5 store). 使用当前活动会话的 persist_key."""
    global _tree, _retriever
    with _lock:
        t = _make_tree_for(_active_persist_key())
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
    # F8: 用当前树的 persist_key 而非硬编码 PERSIST_KEY —— 多会话下旧格式迁移
    # 必须读写"当前会话"的拓扑文件, 否则切到其他会话时迁移错树/根本不迁移.
    old_path = _tree.data_dir / f"{_tree.persist_key}.json"
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


def _migrate_tree(t: "ct.ConversationTree | None") -> int:
    """树级 V5 迁移 (整包导入用): 把 'messages 已内联但 v5_memory_id=0' 的节点
    写入 V5 store 并更新 id + summary。返回迁移节点数; 有迁移则 persist。
    与 _migrate_if_needed 的区别: 不依赖全局 _tree, 任意树可迁移。
    """
    if t is None:
        return 0
    migrated = 0
    for node in t.nodes.values():
        msgs = getattr(node, "messages", None)
        mid = getattr(node, "v5_memory_id", 0) or 0
        if msgs and not mid:
            try:
                new_id = v5s.store(json.dumps(msgs, ensure_ascii=False),
                                   type="conversation", tags="imported")
                node.v5_memory_id = new_id
                try:
                    node.summary = ct._extract_summary(msgs)
                except Exception:
                    pass
                migrated += 1
            except Exception as exc:
                sys.stderr.write(f"[ct] import migrate node {node.id}: {exc}\n")
    if migrated:
        try:
            t.persist()
        except Exception:
            pass
    return migrated


def ensure_tree() -> None:
    """进程启动 / 首次请求时恢复或初始化会话 + 迁移旧格式 + 加载活动会话的树."""
    global _tree, _retriever, _sessions, _active_session_id
    with _lock:
        if _tree is not None:
            return
        _sessions = _load_sessions()
        if not _sessions:
            # 首次运行: 若已有旧版单树 (ui_conversation_tree.json) 则就地包装为第一个会话;
            # 否则用 poker Demo 初始化默认会话.
            default_per = SESSION_DEFAULT_PER
            _sessions = [{
                "id": "default", "title": "默认会话", "persist_key": default_per,
                "created_at": time.time(), "updated_at": time.time(), "archived": False,
            }]
            _save_sessions(_sessions)
            _active_session_id = "default"
            if (V5_DATA_DIR / f"{default_per}.json").exists():
                t = _load_tree_for(default_per)
                if t is None:
                    build_demo()
                else:
                    _bind_active_tree(t)
            else:
                build_demo()
            return
        # 会话已存在: 校正活动会话, 加载其树
        if _active_session_id is None or not any(s["id"] == _active_session_id for s in _sessions):
            _active_session_id = _sessions[0]["id"]
        s = _active_session()
        t = _load_tree_for(s["persist_key"]) if s else None
        if t is None:
            # 拓扑文件缺失 (被手动删), 重建空树避免整体崩溃
            t = _make_tree_for((s or {}).get("persist_key", SESSION_DEFAULT_PER))
            t.init([{"role": "system", "content": "会话已恢复。"}])
        _bind_active_tree(t)
        _migrate_if_needed()


def state_dict(tree: "ct.ConversationTree | None" = None, inline: bool = True) -> dict:
    """返回完整树状态, 含从 V5 store 解析的 messages 字段.

    - tree: 指定活动树 (H3: 在飞 chat 捕获局部引用, 避免会话切换写错树); 默认全局 _tree.
    - inline: True 时把每个节点的对话内容从 V5 store 回读内联进 nodes (前端 renderThread
      直接消费); False 时只返拓扑 (M1 性能: 大对话减少 payload, 前端按需 /api/node_content
      惰性拉取). 默认 True 保持兼容.
    """
    t = tree or _tree
    assert t is not None
    data = json.loads(t.serialize())
    if not inline:
        return data
    # 从 store 批量回读消息, 注入 nodes (前端兼容)
    ids = [n.get("v5_memory_id", 0) for n in data["nodes"] if n.get("v5_memory_id")]
    if ids:
        try:
            batch = t._load_fn(ids)
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


# ── 会话导出 (任务1: GET /api/sessions/:id/export?format=json|txt) ────────
def _export_tree_data(sess: dict) -> dict:
    """构造**自包含**的导出树 JSON (节点内联 messages, v5_memory_id 清零).

    与 /api/sessions/import 的输入格式对齐: {v, schema, root_id, current_id,
    trunk_id, nodes[]}。对话本体从 V5 store 回读内联进节点, v5_memory_id 置 0 →
    导入时由 _migrate_tree 按 messages 重新入库, 导出文件不依赖原 store 中的旧 id
    (换机/换库也能完整恢复消息历史, 含 thinking/tool_calls/usage —— 这些本就随
    to_dict 落在节点上)。
    """
    t = _load_tree_for(sess["persist_key"])
    if t is None:
        return {
            "v": 0, "schema": "super-conv-2.0", "root_id": "root",
            "current_id": "root", "trunk_id": None,
            "nodes": [{"id": "root", "parent_id": None, "children": [], "depth": 0,
                       "branch_label": None, "v5_memory_id": 0, "summary": "",
                       "node_type": "trunk", "is_valid": True, "messages": []}],
        }
    data = json.loads(t.serialize())
    ids = [n.get("v5_memory_id", 0) for n in data["nodes"] if n.get("v5_memory_id")]
    try:
        batch = t._load_fn(ids) if ids else {}
    except Exception:
        batch = {}
    for n in data["nodes"]:
        mid = n.get("v5_memory_id", 0)
        raw = batch.get(mid, "")
        if raw:
            try:
                msgs = json.loads(raw)
                n["messages"] = msgs if isinstance(msgs, list) else [{"role": "system", "content": raw}]
            except json.JSONDecodeError:
                n["messages"] = [{"role": "system", "content": raw}]
        else:
            n["messages"] = []
        n["v5_memory_id"] = 0  # 自包含: 不依赖原 V5 store id
    return data


def _export_payload(sess: dict) -> dict:
    """JSON 导出完整载荷 —— 结构即 /api/sessions/import 的 POST body."""
    return {
        "tree": _export_tree_data(sess),
        "session_id": sess["id"],
        "name": sess.get("title") or sess["id"],
        "exported_at": time.time(),
    }


def _truncate_txt(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "… (截断)"


def _export_txt(sess: dict) -> str:
    """TXT 压缩可读版导出 (参考 hermes-studio ExportCompressor, 纯 Python 实现).

    每条消息单行, 长内容截断; 节点带分支标签 / 摘要 / 思考 / 工具 / 用量。
    """
    data = _export_tree_data(sess)
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    trunk_id = data.get("trunk_id")
    lines = ["Ikaros 对话树导出".center(48), ""]
    lines.append("会话: %s" % (sess.get("title") or sess["id"]))
    lines.append("会话 ID: %s" % sess["id"])
    lines.append("导出时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("节点数: %d" % len(nodes))
    if trunk_id:
        lines.append("主线终点: %s" % trunk_id)
    lines.append("=" * 48)

    # 按分支分组, 组内沿节点顺序 (序列化顺序 = 创建顺序)
    by_branch: dict[str, list] = {}
    for n in data.get("nodes", []):
        by_branch.setdefault(n.get("branch_label") or "main", []).append(n)

    for branch, group in by_branch.items():
        lines.append("")
        lines.append("── 分支 [%s] · %d 节点 ──" % (branch, len(group)))
        for n in group:
            depth = n.get("depth", 0)
            summary = _truncate_txt(n.get("summary") or "", 80)
            head = "#%d %s" % (depth, summary) if summary else "#%d" % depth
            if n.get("id") == trunk_id:
                head += " ★"
            lines.append("[%s] %s" % (n.get("node_type", "trunk"), head))
            for m in n.get("messages", []):
                role = (m.get("role") or "?").strip()
                content = m.get("content", "")
                if isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False)
                content = _truncate_txt(str(content))
                if role == "system":
                    continue
                lines.append("    %s> %s" % (role, content))
            if n.get("thinking"):
                lines.append("    thinking> %s" % _truncate_txt(n["thinking"], 160))
            for tc in n.get("tool_calls", []):
                tname = tc.get("name", "?")
                targs = _truncate_txt(json.dumps(tc.get("args", {}), ensure_ascii=False), 100)
                tstatus = "ok" if tc.get("ok") else ("fail" if tc.get("ok") is False else "…")
                lines.append("    tool> %s(%s) → %s" % (tname, targs, tstatus))
            if n.get("usage"):
                u = n["usage"]
                tok = u.get("total_tokens") or u.get("completion_tokens") or 0
                lines.append("    usage> %s tokens" % tok)
    lines.append("")
    lines.append("=" * 48)
    return "\n".join(lines)


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

# ── B6: 流式 chat (SSE) + 只读工具回路 (chat 面板思考/工具可视化) ──
# 默认 deepseek-v4-flash (与 V5 认知管线一致; thinking 默认 disabled); 仅影响本地降级路径,
# gateway 正常时无关. 设 CT_DEEPSEEK_MODEL=deepseek-reasoner 可开启思考可视化.
CT_DEEPSEEK_MODEL = os.environ.get("CT_DEEPSEEK_MODEL", "deepseek-v4-flash")
# 上下文窗口 (用于"上下文用量"进度条). DeepSeek API 默认 64K; 可用 env 覆盖 (如 128000).
CT_CONTEXT_WINDOW = int(os.environ.get("CT_CONTEXT_WINDOW", "64000"))

# chat 专用只读/安全工具集 (hermes 模式启用). 全部不写磁盘/不执行命令.
CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "在 Ikaros 长期记忆(V5)中做语义检索, 找回与查询相关的过去对话/事实/洞察. "
                           "当用户提到过去的事、需要回忆上下文、或问题依赖已知信息时调用.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "语义检索查询词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前本地日期与时间. 当用户问'现在几点''今天几号''距离某事件还有多久'等时间相关问题时调用.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "branch_overview",
            "description": "总结当前对话分支脉络: 返回从根到当前节点的路径摘要, 帮助把握对话走向. "
                           "当用户问'我们聊到哪了''之前说了什么'时调用.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gateway_ensure",
            "description": "检查 Hermes gateway (:8642) 健康状态; 不可达时自动拉起它并等待就绪. "
                           "当 gateway/Hermes 服务疑似挂掉、工具不可用、或需要恢复完整工具链时调用.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# ikaros_* 自救工具集同样并入 hermes 模式工具表 (正常态也可见/可用)
if _RESCUE is not None:
    CHAT_TOOLS = CHAT_TOOLS + list(_RESCUE.SCHEMAS)

MAX_TOOL_ITER = 5


# ── gateway 降级自救 (2026-08-04): gateway 不可达时由对话树侧拉起 ──

def _gateway_health_check(timeout: float = 2.0) -> bool:
    """探测 :8642 gateway 是否可达 (GET /health, 200/404 均视为进程活着).

    2026-08-05 修复: 原实现探测 HERMES_AGENT_URL (默认 :8650 bridge) 的 /health,
    bridge 活着但背后 gateway 死了时误报"健康", 导致 gateway_ensure 假阳性、
    自救链不触发。现直连 :8642 本体。
    """
    try:
        req = urllib.request.Request("http://127.0.0.1:8642/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status < 500
    except Exception:
        return False


def _spawn_gateway() -> dict:
    """拉起/重启 gateway: 优先委托 rescue_tools (venv python + kill + 完整 env);
    模块缺失时回退旧逻辑 (portable-python, 30s 轮询)。"""
    if _RESCUE is not None:
        r = _RESCUE.call("ikaros_restart_gateway", {})
        return {"ok": bool(r.get("ok")), "result": r.get("result", "")}
    root = Path(os.environ.get("IKAROS_ROOT",
                               str(Path(__file__).resolve().parent.parent.parent)))
    py = root / "runtime" / "portable-python" / "python.exe"
    cwd = root / "runtime" / "hermes"
    log = root / "tmp" / "gateway8642.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("HERMES_HOME", str(root / "data" / "hermes-agent"))
    try:
        fh = open(log, "a", encoding="utf-8", errors="replace")
    except OSError:
        fh = open(os.devnull, "w")
    try:
        proc = subprocess.Popen(
            [str(py), "-m", "hermes_cli.main", "gateway", "run", "--replace"],
            cwd=str(cwd), env=env, stdout=fh, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        return {"ok": False, "result": f"gateway 拉起失败: {e}"}
    deadline = time.time() + 30
    while time.time() < deadline:
        if _gateway_health_check():
            return {"ok": True, "pid": proc.pid,
                    "result": f"gateway 已拉起 (PID {proc.pid}), :8642 健康检查通过"}
        if proc.poll() is not None:
            return {"ok": False,
                    "result": f"gateway 进程提前退出 (code={proc.returncode}), 见 tmp/gateway8642.log"}
        time.sleep(1)
    return {"ok": False,
            "result": f"gateway 进程存活 (PID {proc.pid}) 但 :8642 30s 内未就绪, 见 tmp/gateway8642.log"}


def _tool_duration_ms(collector: dict, tcid: str) -> int | None:
    """按 tcid 逆序找注册时戳, 返回工具执行耗时 ms (任务5). 找不到返回 None."""
    for _tc in reversed(collector.get("tool_calls", [])):
        if _tc.get("id") == tcid and _tc.get("timestamp"):
            return int((time.time() - _tc["timestamp"]) * 1000)
    return None


def _execute_chat_tool(name: str, arguments: str, node_id: str | None) -> dict:
    """执行 chat 降级工具, 返回 {ok, result}. 除 gateway_ensure / ikaros_restart_gateway /
    ikaros_herdr (白名单) 外均为只读/安全."""
    try:
        args = json.loads(arguments) if arguments else {}
    except Exception:
        args = {}
    # ikaros_* 自救工具集: 本地执行, 不依赖 gateway (鸡生蛋解药)
    if _RESCUE is not None and name.startswith("ikaros_"):
        try:
            return _RESCUE.call(name, args)
        except Exception as e:
            return {"ok": False, "result": f"{name} 执行失败: {type(e).__name__}: {e}"}
    if name == "memory_search":
        q = (args.get("query") or "").strip()
        if not q:
            return {"ok": False, "result": "缺少 query 参数"}
        try:
            from memory_v5 import memory_retrieval
            # 统一检索路由: tree scope 让结果按当前对话分支/路径加权 (依赖 node:/branch: 打标)
            hits = memory_retrieval.unified_retrieve(
                q, top_k=5, scope="tree", node_id=node_id, tree=_tree,
            )
            if not hits:
                return {"ok": True, "result": "(无相关记忆)"}
            lines = []
            for h in hits[:5]:
                txt = (h.get("content") or "").strip().replace("\n", " ")
                lines.append(f"- [{float(h.get('score', h.get('raw', 0))):.2f}] {txt[:300]}")
            return {"ok": True, "result": "\n".join(lines)}
        except Exception as e:
            return {"ok": False, "result": f"检索失败: {e}"}
    if name == "get_current_time":
        return {"ok": True, "result": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())}
    if name == "branch_overview":
        try:
            if _tree is None:
                return {"ok": False, "result": "树未初始化"}
            nid = node_id or _tree.current_id
            path = _tree.get_path(nid) if nid else []
            if not path:
                return {"ok": True, "result": "(空分支)"}
            lines = []
            for n in path:
                s = (n.summary or "").strip() or "(无摘要)"
                lines.append(f"#{n.depth} {n.branch_label or 'main'}: {s[:200]}")
            return {"ok": True, "result": "\n".join(lines)}
        except Exception as e:
            return {"ok": False, "result": f"概览失败: {e}"}
    if name == "gateway_ensure":
        # 降级自救 (2026-08-04): gateway 不可达时拉起, 可达时报告状态
        if _gateway_health_check():
            return {"ok": True, "result": "gateway 正常运行 (:8642 健康检查通过)"}
        return _spawn_gateway()
    return {"ok": False, "result": f"未知工具: {name}"}
def _stream_hermes_gateway(messages: list[dict], collector: dict, model: str | None = None):
    """委托 Hermes gateway (:8642) 跑完整 tools/skills 循环, 代理其 SSE 到前端 chat 面板.

    gateway 用 OpenAI 兼容 SSE 流式:
    - 正文走普通 ``data:`` 行 (delta.content / delta.reasoning_content / usage)
    - 工具生命周期走命名事件 ``event: hermes.tool.progress``
      (payload: tool / emoji / label / toolCallId / status="running"|"completed")
    - 模型推理思考走命名事件 ``event: hermes.reasoning`` (payload: text)
      —— 由 gateway 从模型 reasoning 字段透出, 让 hermes 模式也能显示思考块

    这里解析这些帧: 把 ``hermes.tool.progress`` 翻译为 chat 面板的
    ``tool_call``(running) / ``tool_result``(completed) 事件, 把 ``hermes.reasoning``
    翻译为 ``thinking`` 事件. 网关不在线透出工具结果文本 (被 agent 内部消费并并入
    最终正文), 故卡片"结果"用占位说明. 若 gateway 不可达/报错会在 urlopen 阶段立即
    抛出, 由 _chat_stream_events 回退到本地循环 (此时尚未 yield 任何内容, 不会污染前端).
    """
    if not HERMES_AGENT_URL:
        raise RuntimeError("HERMES_AGENT_URL not configured")
    # 任务2: 含图片内容块 → 直连 gateway :8642 (bridge 会拍平 content, 图片无法透传)
    target_url = HERMES_GATEWAY_CHAT_URL if _messages_have_images(messages) else HERMES_AGENT_URL
    body = json.dumps({
        "model": model or HERMES_AGENT_MODEL,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7,
        "stream": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        target_url, data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {HERMES_AGENT_KEY}",
            # studio 式包装层 (core/hermes-bridge) 用此头稳定绑定 Hermes session;
            # 直连 8642 网关时该头被忽略, 故向前兼容、零风险.
            "X-Ikaros-Conv-Id": (_active_session_id or ""),
        },
    )
    # 连接用短超时(urlopen 的 timeout 在连接阶段生效); 建立后把底层 socket 读取超时
    # 调长(R2), 允许模型长生成期间的静默间隙, 不被 socket 超时掐断.
    # urlopen 不可达时立即抛 URLError/HTTPError → 调用方回退本地循环 (尚未 yield 内容)
    with _urlopen_with_timeout(req, LLM_CONNECT_TIMEOUT, LLM_READ_TIMEOUT) as resp:
        buf = b""
        evt = None
        data_lines: list[str] = []

        def _flush():
            nonlocal evt, data_lines
            raw = "\n".join(data_lines)
            data_lines = []
            this_evt = evt
            evt = None
            if not raw:
                return
            # 命名事件: 工具生命周期 (hermes.tool.progress)
            if this_evt == "hermes.tool.progress":
                try:
                    p = json.loads(raw)
                except Exception:
                    return
                status = (p.get("status") or "").lower()
                tcid = p.get("toolCallId") or p.get("tool_call_id") or ""
                tool = p.get("tool") or "tool"
                label = p.get("label") or ""
                emoji = p.get("emoji") or "🔧"
                # 过滤 Hermes 内部元步骤 (调用前的 "describe tool" 等), 避免噪音卡片
                if tool.startswith("tool_describe") or tool == "tool_describe":
                    return
                if status == "running" or (not status and label):
                    # 工具开始: 注册卡片 (前端 ok:null → 待执行态). 记录 tcid 以便
                    # 结果回填按 id 精确匹配 (L5: 避免同名工具多次调用互相串台).
                    collector["tool_calls"].append({
                        "id": tcid,
                        "name": tool,
                        "params": {"input": label, "emoji": emoji},
                        "result_summary": "",
                        "success": True,
                        "timestamp": time.time(),
                    })
                    yield {
                        "type": "tool_call", "id": tcid, "name": tool,
                        "args": {"input": label, "emoji": emoji},
                    }
                else:
                    # completed / failed / 其它: 终结卡片. gateway 现随事件透出结果文本
                    # (阶段 2: api_server._on_tool_complete 携带 result, 截断 2000),
                    # 实时卡片显示真实结果; 同时回填 collector 供持久化 (阶段 3).
                    ok = (status != "failed")
                    result_txt = p.get("result", "") if ok else "（执行失败）"
                    if ok:
                        # L5: 优先按 toolCallId 精确回填; 无 id 时回退按 name 逆序匹配
                        matched = False
                        if tcid:
                            for _tc in reversed(collector["tool_calls"]):
                                if _tc.get("id") == tcid:
                                    _tc["result_summary"] = str(result_txt)[:500]
                                    _tc["success"] = True
                                    _tc["duration"] = _tool_duration_ms(collector, tcid)
                                    matched = True
                                    break
                        if not matched:
                            for _tc in reversed(collector["tool_calls"]):
                                if _tc.get("name") == tool:
                                    _tc["result_summary"] = str(result_txt)[:500]
                                    _tc["success"] = True
                                    _tc["duration"] = _tool_duration_ms(collector, tcid)
                                    break
                    yield {
                        "type": "tool_result", "id": tcid, "ok": ok,
                        "result": result_txt,
                        "duration": _tool_duration_ms(collector, tcid),
                    }
                return
            # 命名事件: 模型推理思考流 (hermes.reasoning) — gateway 从模型 reasoning 字段透出
            if this_evt == "hermes.reasoning":
                try:
                    p = json.loads(raw)
                except Exception:
                    return
                text = p.get("text") or ""
                if text:
                    collector["thinking"] += text
                    yield {"type": "thinking", "delta": text}
                return
            # 上游错误透传 (bridge 透出的 gateway upstream 失败: 401/404/连接失败)
            # —— studio 式包装层把上游失败显式转成 event:error, 这里消费它,
            # 让降级提示从笼统的「空响应」变成准确的「gateway 上游错误 (HTTP 401 ...)」.
            if this_evt == "error":
                try:
                    p = json.loads(raw)
                except Exception:
                    return
                collector["upstream_error"] = p.get("error") or "unknown upstream error"
                yield {"type": "warn",
                       "message": f"Hermes 上游返回错误：{collector['upstream_error']}"}
                return
            # 其它命名事件 (hermes.token 等) 不处理
            if this_evt is not None:
                return
            # 普通 OpenAI data: 行 (正文 / usage / [DONE])
            if not raw.startswith("{"):
                return
            try:
                obj = json.loads(raw)
            except Exception:
                return
            # 末块 usage (gateway 把 usage 放在 finish_reason 同块)
            if obj.get("usage"):
                collector["usage"] = obj["usage"]
                yield {"type": "usage", "usage": obj["usage"],
                       "model": model or HERMES_AGENT_MODEL,
                       "context_window": _hermes_model_context(model or HERMES_AGENT_MODEL)}
            try:
                delta = obj["choices"][0]["delta"]
            except Exception:
                return
            if delta.get("reasoning_content"):
                collector["thinking"] += delta["reasoning_content"]
                yield {"type": "thinking", "delta": delta["reasoning_content"]}
            if delta.get("content"):
                collector["content"] += delta["content"]
                yield {"type": "content", "delta": delta["content"]}

        for chunk in resp:
            buf += chunk
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                line = buf[:idx].decode("utf-8", "replace").rstrip("\r")
                buf = buf[idx + 1:]
                if line == "":
                    yield from _flush()
                    continue
                if line.startswith("event:"):
                    evt = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
                # retry: / id: 等其它 SSE 字段忽略
        yield from _flush()  # 收尾: 处理缓冲区中最后一个未以空行结束的帧


def _stream_fallback(messages: list[dict], agent: str, collector: dict,
                     node_id: str | None = None):
    """本地降级通道 (H2 恢复): gateway 不可达/空响应时, 走 DeepSeek→Hermes→Local 三层
    chat 补全, 把正文以 SSE content 增量流式透出 (≈24 字/片, 前端 rAF 限频重渲 markdown),
    并回传 usage 事件. 全部失败时抛错, 由上层转 error 事件.

    S2: 完整工具协议 —— 降级链跑多轮工具循环 (上限 MAX_TOOL_ROUNDS):
      1. 先做一轮 memory_search 预检索 (query=最后一条 user 消息), 结果并入上下文;
      2. 带 tools 调 _call_llm_tools, 模型可自主决定调 memory_search /
         get_current_time / branch_overview;
      3. 每轮把 assistant tool_calls + tool 结果回填消息列表, 直到模型不再调工具
         或达到轮次上限。
    所有工具经 _execute_chat_tool 执行 (只读安全), 失败 fail-open 不阻塞主线。
    """
    # ── 0. 预检索 (只读工具回路, 保证降级时至少有记忆上下文) ──
    msgs: list[dict] = list(messages)
    try:
        q = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                # 任务2: content 可能是 ContentBlock 列表, 拍平成纯文本取检索词
                q = _flatten_openai_content(m.get("content") or "").strip()
                break
        if q:
            t0 = time.time()
            res = _execute_chat_tool("memory_search",
                                     json.dumps({"query": q[:200]}, ensure_ascii=False),
                                     node_id)
            if res.get("ok") and res.get("result") and "(无相关记忆)" not in res["result"]:
                tcid = f"fb_mem_{int(time.time() * 1000)}"
                collector["tool_calls"].append({
                    "id": tcid, "name": "memory_search",
                    "params": {"query": q[:80]},
                    "result_summary": str(res["result"])[:500],
                    "success": True, "timestamp": t0,
                })
                yield {"type": "tool_call", "id": tcid, "name": "memory_search",
                       "args": {"query": q[:80]}}
                yield {"type": "tool_result", "id": tcid, "ok": True,
                       "result": res["result"],
                       "duration": _tool_duration_ms(collector, tcid)}
                msgs.insert(0, {
                    "role": "system",
                    "content": "[树域记忆预检索 (降级链)]\n" + str(res["result"])[:1500],
                })
    except Exception as e:
        sys.stderr.write(f"[ct] fallback prefetch failed: {e}\n")

    # ── 1. 多轮工具循环 (S2) ──
    final_content = ""
    usage: dict = {}
    tool_rounds = 0
    while tool_rounds < MAX_TOOL_ROUNDS:
        content, usage, tool_calls = _call_llm_tools(
            msgs, _READONLY_TOOLS, collector)
        if not tool_calls:
            final_content = content
            break
        tool_rounds += 1
        # 执行本轮工具调用, 回填消息 + 前端卡片
        tool_msgs: list[dict] = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name") or "tool"
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            tcid = tc.get("id") or f"fb_{tool_rounds}_{name}"
            collector["tool_calls"].append({
                "id": tcid, "name": name, "params": args,
                "result_summary": "", "success": True, "timestamp": time.time(),
            })
            yield {"type": "tool_call", "id": tcid, "name": name, "args": args}
            res = _execute_chat_tool(name, fn.get("arguments") or "", node_id)
            for _tc in reversed(collector["tool_calls"]):
                if _tc.get("id") == tcid:
                    _tc["result_summary"] = str(res.get("result", ""))[:500]
                    _tc["success"] = bool(res.get("ok", False))
                    break
            yield {"type": "tool_result", "id": tcid, "ok": bool(res.get("ok", False)),
                   "result": res.get("result", ""),
                   "duration": _tool_duration_ms(collector, tcid)}
            tool_msgs.append({
                "role": "tool", "tool_call_id": tcid,
                "content": json.dumps(res, ensure_ascii=False),
            })
        # assistant 的 tool_calls 声明 + 工具结果 → 下一轮上下文
        msgs = msgs + [{"role": "assistant", "content": content or "",
                        "tool_calls": tool_calls}] + tool_msgs

    if not final_content.strip():
        raise RuntimeError("本地模型返回空响应")
    for i in range(0, len(final_content), 24):
        chunk = final_content[i:i + 24]
        collector["content"] += chunk
        yield {"type": "content", "delta": chunk}
    if usage:
        yield {"type": "usage", "usage": usage,
               "model": _effective_model(agent), "context_window": CT_CONTEXT_WINDOW}


def _chat_stream_events(messages: list[dict], agent: str, node_id: str | None, collector: dict,
                        tree: "ct.ConversationTree | None" = None):
    """生成 chat SSE 事件字典. collector 累积 {content, thinking, tool_calls, usage} 供持久化.

    主链路 = Hermes gateway (:8642) 跑完整 tools/skills 循环; gateway 不可达 / 空响应 /
    报错 → 降级到本地三层 chat 补全 (DeepSeek→Hermes→Local), 并以 SSE ``warn`` 黄色提示条
    告知用户 (符合 AGENTS.md 既定设计, 修复"只走 gateway 禁止降级"与文档/health 的矛盾, H2).
    """
    if not HERMES_AGENT_URL:
        # 未配置 gateway → 直接走本地降级 (不报错)
        yield {"type": "warn", "message": "Hermes gateway 未配置，已使用本地模型"}
        try:
            yield from _stream_fallback(messages, agent, collector, node_id=node_id)
        except Exception as e:
            yield {"type": "error", "message": f"本地模型不可用（{e}）"}
        return

    cur_model = _effective_model(agent)
    gw_yielded = False
    try:
        for _ev in _stream_hermes_gateway(messages, collector, model=cur_model):
            gw_yielded = True
            yield _ev
        # gateway 正常结束但无正文 → 降级本地
        if not collector["content"].strip():
            if collector.get("upstream_error"):
                yield {"type": "warn",
                       "message": f"Hermes 上游错误（{collector['upstream_error']}），已降级本地模型"}
            else:
                yield {"type": "warn", "message": "Hermes gateway 返回空响应，已降级本地模型"}
        else:
            return
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        yield {"type": "warn", "message": f"Hermes gateway 不可达（{e}），已降级本地模型"}
        sys.stderr.write(f"[ct] Hermes gateway unreachable ({e}); fallback to local\n")
    except Exception as e:
        if gw_yielded:
            # 中段错误: gateway 已流式产出部分内容后中断.
            # - 若尚未产出正文(仅工具事件): 安全降级本地补全, 不会产生重复正文;
            # - 若已有部分正文: 保留残缺 + 明确 warn(黄色提示条), 不再重复生成,
            #   避免把 gateway 半截答案 + 本地全新答案拼接成乱文落库.
            if not collector["content"].strip():
                sys.stderr.write(f"[ct] Hermes gateway mid-stream error before content ({e}); fallback to local\n")
                yield {"type": "warn", "message": f"Hermes gateway 中断（{e}），已降级本地模型补全"}
                try:
                    yield from _stream_fallback(messages, agent, collector, node_id=node_id)
                except Exception as e2:
                    yield {"type": "error", "message": f"本地模型也不可用（{e2}）"}
                return
            sys.stderr.write(f"[ct] Hermes gateway mid-stream error ({e}); keep partial\n")
            yield {"type": "warn", "message": f"Hermes gateway 流中途中断（{e}），上方为已生成的部分内容"}
            return
        yield {"type": "warn", "message": f"Hermes gateway 错误（{e}），已降级本地模型"}
        sys.stderr.write(f"[ct] Hermes gateway error ({e}); fallback to local\n")

    # 降级本地三层链路
    try:
        yield from _stream_fallback(messages, agent, collector, node_id=node_id)
    except Exception as e:
        yield {"type": "error", "message": f"本地模型也不可用（{e}）"}
        sys.stderr.write(f"[ct] fallback failed ({e})\n")


# ── 任务2: multipart/form-data 上传解析 (纯标准库, 无 cgi 依赖) ─────────
def _parse_multipart(body: bytes, boundary: str) -> list[tuple[dict, bytes]]:
    """解析 multipart/form-data 报文为 [(headers, content), ...].

    边界拆分按 RFC 2046 的 ``\\r\\n--<boundary>`` 分隔, 二进制内容中的 CRLF 不会被误剥;
    文件内容里的 ``\\r\\n--<boundary>`` 命中概率可忽略 (boundary 为随机串).
    """
    bnd = boundary.encode("utf-8")
    parts: list[tuple[dict, bytes]] = []
    for seg in body.split(b"\r\n--" + bnd):
        if not seg:
            continue
        if seg.startswith(b"--"):  # 末段终止符 "--boundary--"
            continue
        if b"\r\n\r\n" in seg:
            header_block, content = seg.split(b"\r\n\r\n", 1)
        else:
            header_block, content = seg, b""
        headers: dict = {}
        for line in header_block.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.decode("utf-8", "replace").strip().lower()] = v.decode("utf-8", "replace").strip()
        parts.append((headers, content))
    return parts


def _random_hex_filename(original: str) -> str:
    """8 字节随机 hex 文件名 (16 hex 字符) + 原扩展名, 防冲突/防注入."""
    ext = Path(original).suffix.lower()
    if len(ext) > 12 or not re.fullmatch(r"\.[A-Za-z0-9]+", ext):
        ext = ""
    return uuid.uuid4().hex[:16] + ext


def _save_upload(raw: bytes, original_name: str) -> dict | None:
    """保存上传字节 → {url,name,size}; 超限/非法返回 None (调用方给错误提示)."""
    if len(raw) > _UPLOAD_MAX_BYTES:
        return None
    safe = Path(original_name).name
    if not safe or safe.startswith("."):
        return None
    fname = _random_hex_filename(safe)
    dest = _UPLOAD_DIR / fname
    dest.write_bytes(raw)
    return {"ok": True, "url": f"/uploads/{fname}", "name": safe,
            "size": len(raw)}


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

    def _send_sse(self, generator):
        """以 SSE (text/event-stream) 分块推送生成器产出的字符串.

        S4: 手动 Transfer-Encoding: chunked —— HTTP/1.1 下若响应体无 Content-Length
        也无 chunked, 标准客户端 (curl/urllib/http.client) 会一直等连接关闭,
        而 keep-alive 永不关闭 → 挂起. 浏览器 fetch ReadableStream 不依赖 EOF
        所以前端无恙, 但协议上必须显式 chunked 才能让所有客户端正确判定流结束.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for chunk in generator:
                if not chunk:
                    continue
                data = chunk.encode("utf-8")
                # S4: chunked frame: <hex-size>\r\n<data>\r\n\r\n
                self.wfile.write(f"{len(data):x}\r\n".encode("ascii"))
                self.wfile.write(data)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            # chunked terminator
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()


        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # M3 + F9: 客户端断开(ESC/切节点)时主动关闭生成器链, 让 _stream_hermes_gateway
            # 的 urlopen with 块立即退出并关掉 gateway HTTP 连接, 不再挂到 LLM_TIMEOUT.
            # F9: 补 ConnectionAbortedError —— Windows 上客户端 abort 抛的是它
            # (WinError 10053), 旧代码只捕前两个, 导致完整 traceback 刷屏日志.
            try:
                generator.close()
            except Exception:
                pass


    def _send_html(self, path: Path):
        if not path.exists():
            self._send_text("index.html not found", 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        # 必须带 Content-Length, 否则 HTTP/1.1 keep-alive 下客户端只能等连接
        # 关闭才能判定 HTML 结束 —— 浏览器标签页会因此一直转圈。
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # 静态资源: /assets/xxx.woff2 等 (2026-08-02, 字体自托管零外部依赖)
    _MIME = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
             ".css": "text/css; charset=utf-8", ".js": "application/javascript",
             ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

    def _serve_asset(self, path: str):
        name = path[len("/assets/"):].split("?", 1)[0]
        # 防目录穿越: 允许 assets/ 下单层子目录 (如 cursors/), 但禁止 .. / 绝对路径 / 隐藏文件
        if not name or name.startswith(".") or ".." in name or name.startswith("/") or name.startswith("\\"):
            self._send_text("forbidden", 403)
            return
        if "\\" in name or "/" in name.split("/")[-1] or any(p.startswith(".") for p in name.split("/")[:-1]):
            self._send_text("forbidden", 403)
            return
        f = HERE / "assets" / name
        if not f.exists():
            self._send_text("not found", 404)
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self._MIME.get(f.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_upload(self, path: str):
        """GET /uploads/<name> —— 附件静态服务（目录穿越防护 + MIME 白名单）。"""
        name = path[len("/uploads/"):].split("?", 1)[0]
        if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
            self._send_text("forbidden", 403)
            return
        f = _UPLOAD_DIR / name
        if not f.exists():
            self._send_text("not found", 404)
            return
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _UPLOAD_MIME.get(f.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            elif path.startswith("/assets/"):
                self._serve_asset(path)
            elif path.startswith("/uploads/"):
                self._serve_upload(path)
            elif path == "/api/state":
                # F10: 解析 inline 参数 —— 前端 state() 传 ?inline=0 要轻量拓扑,
                # 旧代码忽略 query 默认全量内联, M1 惰性加载实际从未生效
                self._send_json(state_dict(inline=self._q("inline") != "0"))
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
            elif path == "/api/sessions":
                self._send_json({"sessions": _sessions, "active_id": _active_session_id})
            elif path == "/api/sessions/tree":
                # 融合桥 (8099 poker UI): 读指定会话完整树 (拓扑 + messages, V5 回读)
                qs = {}
                if "?" in self.path:
                    for kv in self.path.split("?", 1)[1].split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            try:
                                qs[k] = urllib.parse.unquote(v)
                            except Exception:
                                qs[k] = v
                sid = qs.get("session_id", "")
                if not sid:
                    self._send_json({"error": "session_id required"}, 400)
                    return
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if sess is None:
                    self._send_json({"error": "session not found"}, 404)
                    return
                t = _load_tree_for(sess["persist_key"])
                if t is None:
                    t = _make_tree_for(sess["persist_key"])
                    t.init([{"role": "system", "content": "会话已恢复。"}])
                self._send_json(state_dict(t, inline=True))
            elif path.startswith("/api/sessions/") and path.endswith("/export"):
                # 任务1: 会话导出 (JSON 完整历史 / TXT 压缩可读版)
                sid = urllib.parse.unquote(path[len("/api/sessions/"):-len("/export")])
                if not sid:
                    self._send_json({"error": "session_id required"}, 400)
                    return
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if sess is None:
                    self._send_json({"error": "session not found"}, 404)
                    return
                fmt = (self._q("format") or "json").lower()
                if fmt == "json":
                    self._send_json(_export_payload(sess))
                elif fmt == "txt":
                    body = _export_txt(sess).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="session-{sid}.txt"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send_json({"error": "format must be json or txt"}, 400)
            elif path == "/api/health":
                # 连接状态探测: gateway 主通道 / 本地 LLM 降级链 / DeepSeek key
                health = {"gateway": False, "local_llm": False, "deepseek_key": bool(_DEEPSEEK_KEY),
                          "model": HERMES_AGENT_MODEL, "ts": time.time()}
                try:
                    req = urllib.request.Request(
                        HERMES_AGENT_URL, method="GET",
                        headers={"Authorization": f"Bearer {HERMES_AGENT_KEY}"},
                    )
                    urllib.request.urlopen(req, timeout=2)
                    health["gateway"] = True
                except urllib.error.HTTPError as e:
                    health["gateway"] = e.code in (200, 405)  # POST-only 端点 GET 405 = 可达
                except Exception:
                    health["gateway"] = False
                try:
                    urllib.request.urlopen(LOCAL_CHAT_URL + "/health", timeout=2)
                    health["local_llm"] = True
                except Exception:
                    health["local_llm"] = False
                self._send_json(health)
            elif path == "/api/model_switch":
                self._send_json({
                    "ok": True,
                    "current": dict(_CT_RUNTIME),
                    "defaults": {"hermes": HERMES_AGENT_MODEL, "ikaros": CT_DEEPSEEK_MODEL},
                    "available": _hermes_models(),
                })
            elif path == "/api/providers":
                self._send_json({"ok": True, "providers": _hermes_providers_status()})
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
                # F11: 用当前活动树的 persist_key (多会话下事件订阅者需要真实树标识)
                "tree": getattr(_tree, "persist_key", None) or PERSIST_KEY,
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
        global _sessions, _active_session_id
        ensure_tree()
        path = self.path.split("?", 1)[0]
        try:
            # 任务2: multipart 上传先于 JSON body 解析 (multipart 不是 JSON,
            # _body() 会 json.loads 失败; 且 body 只能读一次)
            ctype = self.headers.get("Content-Type", "")
            if path == "/api/upload" and ctype.lower().startswith("multipart/form-data"):
                m = re.search(r"boundary=([^;]+)", ctype)
                if not m:
                    self._send_json({"error": "multipart boundary missing"}, 400)
                    return
                boundary = m.group(1).strip().strip('"')
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    self._send_json({"error": "invalid Content-Length"}, 400)
                    return
                # 提前拦截: 整体报文超限 (文件本身 + 表单开销余量)
                if length > _UPLOAD_MAX_BYTES + (2 * 1024 * 1024):
                    self._send_json(
                        {"error": f"文件过大(>{_UPLOAD_MAX_BYTES // (1024 * 1024)}MB)"}, 413)
                    return
                body = self.rfile.read(length)
                name, raw = "", b""
                for headers, content in _parse_multipart(body, boundary):
                    disp = headers.get("content-disposition", "")
                    if 'name="file"' in disp or "filename=" in disp:
                        fm = re.search(r'filename="([^"]*)"', disp)
                        if fm:
                            name = fm.group(1)
                        raw = content
                        break
                if not name or not raw:
                    self._send_json({"error": "file part (name=file) required"}, 400)
                    return
                res = _save_upload(raw, name)
                if res is None:
                    self._send_json(
                        {"error": f"文件过大(>{_UPLOAD_MAX_BYTES // (1024 * 1024)}MB)"}, 413)
                    return
                self._send_json(res)
                return
            data = self._body()
            if path == "/api/init":
                build_demo()
                self._send_json(state_dict())
            elif path == "/api/add_turn":
                with _lock:
                    _tree.add_turn(
                        messages=data.get("messages", []),
                        parent_id=data.get("parent_id"),
                        branch_label=data.get("branch_label"),
                        state=data.get("state"),
                        config=data.get("config"),
                        title=data.get("title"),
                    )
                    _touch_active_session()
                    self._send_json(state_dict())
            elif path == "/api/branch_from":
                _tree.branch_from(
                    node_id=data["node_id"],
                    messages=data.get("messages", []),
                    branch_label=data.get("branch_label"),
                )
                self._send_json(state_dict())
            elif path == "/api/fork":
                with _lock:
                    node = _tree.fork_branch(
                        fork_point_id=data["node_id"],
                        branch_label=data.get("branch_label", "branch"),
                        messages=data.get("messages", []),
                        state=data.get("state"),
                        config=data.get("config"),
                        title=data.get("title"),
                        thinking=data.get("thinking", ""),
                        tool_calls=[ct.ToolCall(**tc) for tc in data.get("tool_calls", [])],
                        usage=data.get("usage") or {},
                        skills_used=data.get("skills_used") or [],
                    )
                    self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/conclude":
                with _lock:
                    node = _tree.conclude_branch(
                        node_id=data["node_id"],
                        conclusions=data.get("conclusions", []),
                    )
                    self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/merge":
                with _lock:
                    bid = data.get("branch_id") or data.get("source_id")
                    tid = data.get("trunk_id") or data.get("target_id")
                    if not bid or not tid:
                        self._send_json({"error": "branch_id and trunk_id required"}, 400)
                        return
                    # 前端 "Merge to Trunk" 传 '__trunk__' → 主线终点 (trunk_id), 无则沿祖先链
                    # S1: 优先 trunk_id (唯一真源), 不再沿 node_type 猜 (旧逻辑会命中 root 或误标 trunk)
                    if tid == "__trunk__":
                        bnode = _tree.get_node(bid)
                        if _tree.trunk_id and _tree.get_node(_tree.trunk_id):
                            tid = _tree.trunk_id
                        else:
                            cur = bnode.parent_id if bnode else None
                            tid = None
                            while cur:
                                cn = _tree.get_node(cur)
                                if cn and (cn.id == _tree.trunk_id or
                                           (cn.node_type == "trunk" and cn.id != _tree.root_id)):
                                    tid = cn.id
                                    break
                                cur = cn.parent_id if cn else None
                            if not tid:
                                self._send_json({"error": "no trunk ancestor found"}, 400)
                                return
                    _tree.merge_branch(branch_node_id=bid, trunk_target_id=tid)
                    self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/unmerge":
                with _lock:
                    _tree.unmerge_branch(data.get("node_id") or data.get("branch_id"))
                    self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/abandon":
                with _lock:
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
                with _lock:
                    _tree.jump_to(data["node_id"])
                    self._send_json(state_dict())
            elif path == "/api/prune":
                try:
                    _tree.prune(data["node_id"])
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                    return
                self._send_json(state_dict())
            elif path == "/api/rename":
                node = _tree.rename_node(
                    node_id=data["node_id"],
                    title=data.get("title", ""),
                )
                self._send_json({"ok": True, "node_id": node.id, "state": state_dict()})
            elif path == "/api/set_trunk":
                # S1 + F14: 显式主线提升 (把分支节点设为主线终点); 废弃分支禁止提升
                try:
                    node = _tree.set_trunk(
                        node_id=data["node_id"],
                        cascade=bool(data.get("cascade", False)),
                    )
                except ValueError as e:
                    self._send_json({"error": str(e)}, 400)
                    return
                self._send_json({"ok": True, "node_id": node.id,
                                 "trunk_id": _tree.trunk_id, "state": state_dict()})
            elif path == "/api/delete_node":
                _tree.delete_node(data["node_id"])
                self._send_json({"ok": True, "state": state_dict()})
            elif path == "/api/set_agent":
                # 设置分支归属代理 (ekko 模式): ikaros 伴侣 / hermes 任务代理
                node = _tree.set_agent(
                    node_id=data["node_id"],
                    agent=data.get("agent", "ikaros"),
                    cascade=bool(data.get("cascade", False)),
                )
                self._send_json({"ok": True, "node_id": node.id,
                                 "agent": node.agent, "state": state_dict()})
            elif path == "/api/memory":
                mem = _retriever.add_memory({
                    "text": data.get("text", ""),
                    "tags": data.get("tags", []),
                    "node_id": data.get("node_id"),
                    "branch": data.get("branch"),
                })
                self._send_json({"ok": True, "mem": mem})
            elif path == "/api/upload":
                # 旧 base64 JSON 通道 (兼容; multipart 由 do_POST 入口处理)
                name = (data.get("name") or "").strip()
                b64 = data.get("data_b64") or ""
                if not name or not b64:
                    self._send_json({"error": "name and data_b64 required"}, 400)
                    return
                # 防穿越 + 防路径注入: 只保留文件名
                safe = Path(name).name
                if not safe or safe.startswith("."):
                    self._send_json({"error": "invalid filename"}, 400)
                    return
                try:
                    raw = base64.b64decode(b64)
                except Exception:
                    self._send_json({"error": "invalid base64"}, 400)
                    return
                res = _save_upload(raw, safe)
                if res is None:
                    self._send_json({"error": f"文件过大(>{_UPLOAD_MAX_BYTES // (1024*1024)}MB)"}, 413)
                    return
                self._send_json(res)
            elif path == "/api/model_switch":
                # POST: {"mode":"hermes"|"ikaros"|"","model":"<名>"}
                mode = (data.get("mode") or "").strip()
                model = (data.get("model") or "").strip()
                if mode not in ("", "hermes", "ikaros"):
                    self._send_json({"error": "mode must be hermes|ikaros|''"}, 400)
                    return
                _CT_RUNTIME["mode"] = mode
                _CT_RUNTIME["model"] = model
                self._send_json({"ok": True, "current": dict(_CT_RUNTIME)})
            elif path == "/api/chat":
                user_message = data.get("message", "").strip()
                if not user_message:
                    self._send_json({"error": "message is required"}, 400)
                    return
                parent_id = data.get("parent_id")
                branch_label = data.get("branch_label")
                # 任务2: 附件引用列表 [{name,url,isImage}] → ContentBlock 协议
                attachments = data.get("attachments")
                if not isinstance(attachments, list) or not attachments:
                    attachments = None

                # chat 接入 Ikaros V5: 人格 + 树感知压缩 + 树域语义记忆 (fail-open)
                # H3: 捕获局部 tree 引用, 贯穿整条 chat 生命周期, 使并发会话切换
                # 不会把在飞对话写错树或读到半切换状态.
                tree = _tree
                target_id = parent_id or tree.current.id
                node = tree.get_node(target_id)
                mode = _effective_mode(node.agent if node else None)
                collector = {"content": "", "thinking": "", "tool_calls": [],
                             "usage": {}, "warns": []}

                def _gen():
                    errored = False
                    try:
                        messages = build_chat_messages_v5(target_id, user_message,
                                                          collector=collector, tree=tree,
                                                          attachments=attachments)
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'build messages failed: {e}'}, ensure_ascii=False)}\n\n"
                        return
                    # 降级可见化: 组装阶段记录的 warn 先透出 (黄色提示条, 不中断流)
                    for w in collector.get("warns", []):
                        yield f"data: {json.dumps({'type': 'warn', 'message': w}, ensure_ascii=False)}\n\n"
                    try:
                        for ev in _chat_stream_events(messages, mode, target_id, collector, tree=tree):
                            if ev.get("type") == "error":
                                errored = True
                            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        # 持久化本轮对话 (含思考/工具), 再推送 done (出错则不落库, 避免空节点)
                        if not errored and collector["content"].strip():
                            try:
                                # skills_used 近似: gateway 无 skill 专属事件, 用本轮工具名列表
                                # (阶段 3.2 降级方案; 精确 skill 元数据待 gateway 侧补事件源)
                                skills_used = list(dict.fromkeys(
                                    tc.get("name", "") for tc in collector["tool_calls"] if tc.get("name")
                                ))
                                tree.add_turn(
                                    messages=[
                                        {"role": "user", "content": user_message},
                                        {"role": "assistant", "content": collector["content"]},
                                    ],
                                    parent_id=parent_id,
                                    branch_label=branch_label,
                                    thinking=collector["thinking"],
                                    tool_calls=[ct.ToolCall(**tc) for tc in collector["tool_calls"]],
                                    usage=collector["usage"],
                                    skills_used=skills_used,
                                    # S1: 前端显式 fork (从此分叉) 时强制 branch
                                    force_branch=bool(data.get("force_branch", False)),
                                )
                                # F7: 传局部 tree, 防止 chat 在飞时切会话把摘要写进新会话
                                _touch_active_session(tree)
                            except Exception as e:
                                sys.stderr.write(f"[ct] chat persist error: {e}\n")
                        yield f"data: {json.dumps({'type': 'done', 'state': state_dict(tree)}, ensure_ascii=False)}\n\n"
                    except Exception as e:
                        sys.stderr.write(f"[ct] chat stream error: {e}\n")
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

                self._send_sse(_gen())
                return
            elif path == "/api/reset":
                build_demo()
                self._send_json(state_dict())
            # ── 多会话管理 (左侧栏 新建 / 切换 / 删除 / 归档 / 重命名) ──
            elif path == "/api/sessions/import":
                # 融合桥 (8099 poker UI): 整包导入 super-conv-2.0 树 JSON.
                # body: {tree:{v,schema,root_id,current_id,trunk_id,nodes[]},
                #        session_id?: 覆盖已有会话, name?: 新建会话标题}
                tree_data = data.get("tree")
                if not isinstance(tree_data, dict) or not isinstance(tree_data.get("nodes"), list):
                    self._send_json({"error": "tree.nodes (list) required"}, 400)
                    return
                nodes = tree_data["nodes"]
                sid = data.get("session_id") or data.get("id")
                if sid and any(s["id"] == sid for s in _sessions):
                    sess = next(s for s in _sessions if s["id"] == sid)
                    persist_key = sess["persist_key"]
                    created = False
                else:
                    sid = _new_session_id()
                    persist_key = f"ui_conversation_tree_{sid}"
                    sess = {"id": sid, "title": str(data.get("name") or "导入会话")[:80],
                            "persist_key": persist_key, "created_at": time.time(),
                            "updated_at": time.time(), "archived": False}
                    _sessions.append(sess)
                    created = True
                # 归一化: root / current / trunk 兜底 (节点 id 由调用方生成, 此处只保证字段齐全)
                ids = [n.get("id") for n in nodes if isinstance(n, dict) and n.get("id")]
                root_id = tree_data.get("root_id") or (ids[0] if ids else "root")
                if root_id not in set(ids):
                    nodes.insert(0, {"id": root_id, "parent_id": None, "children": [],
                                     "depth": 0, "branch_label": None, "messages": [],
                                     "summary": "", "node_type": "trunk", "is_valid": True})
                    ids.insert(0, root_id)
                current_id = tree_data.get("current_id") or root_id
                if current_id not in set(ids):
                    current_id = root_id
                payload = {
                    "v": int(tree_data.get("v", 0) or 0),
                    "schema": "super-conv-2.0",
                    "root_id": root_id,
                    "current_id": current_id,
                    "trunk_id": tree_data.get("trunk_id") or current_id,
                    "nodes": nodes,
                }
                with _lock:
                    topo = V5_DATA_DIR / f"{persist_key}.json"
                    topo.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    t = _load_tree_for(persist_key)
                    if t is None:
                        t = _make_tree_for(persist_key)
                        t.init([{"role": "system", "content": "会话已恢复。"}])
                    migrated = _migrate_tree(t)
                    if sid == _active_session_id:
                        _bind_active_tree(t)
                        _touch_active_session(t)
                _save_sessions(_sessions)
                self._send_json({"ok": True, "id": sid, "created": created,
                                 "version": payload["v"], "node_count": len(nodes),
                                 "migrated": migrated})
            elif path == "/api/sessions/create":
                sid = _new_session_id()
                per = f"ui_conversation_tree_{sid}"
                with _lock:
                    t = _make_tree_for(per)
                    t.init([{"role": "system", "content": "新会话已开始。"}])
                    _bind_active_tree(t)
                sess = {"id": sid, "title": "新会话", "persist_key": per,
                        "created_at": time.time(), "updated_at": time.time(), "archived": False}
                _sessions.append(sess)
                _active_session_id = sid
                _save_sessions(_sessions)
                self._send_json({"sessions": _sessions, "active_id": sid, "state": state_dict()})
            elif path == "/api/sessions/switch":
                sid = data.get("id")
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if not sess:
                    self._send_json({"error": "session not found"}, 404)
                    return
                with _lock:
                    t = _load_tree_for(sess["persist_key"])
                    if t is None:
                        t = _make_tree_for(sess["persist_key"])
                        t.init([{"role": "system", "content": "会话已恢复。"}])
                    _bind_active_tree(t)
                    _migrate_if_needed()
                _active_session_id = sid
                _touch_active_session()
                self._send_json({"active_id": sid, "state": state_dict()})
            elif path == "/api/sessions/delete":
                sid = data.get("id")
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if not sess:
                    self._send_json({"error": "session not found"}, 404)
                    return
                if len(_sessions) <= 1:
                    self._send_json({"error": "至少保留一个会话，无法删除"}, 400)
                    return
                per = sess["persist_key"]
                # 1) 删除拓扑 JSON
                topo = V5_DATA_DIR / f"{per}.json"
                try:
                    if topo.exists():
                        topo.unlink()
                except Exception:
                    pass
                # 2) 尽力清理该会话占用的 V5 记忆行 (避免孤儿行堆积)
                try:
                    old = _load_tree_for(per)
                    if old is not None:
                        for n in old.nodes.values():
                            if n.v5_memory_id:
                                try:
                                    v5s.delete(n.v5_memory_id)
                                except Exception:
                                    pass
                            # 一并清理 MemoryRetriever 写入的 fact 记忆 (共享 store, 否则成孤儿行)
                            for mid in getattr(n, "memory_ids", []) or []:
                                try:
                                    v5s.delete(mid)
                                except Exception:
                                    pass
                except Exception:
                    pass
                _sessions = [s for s in _sessions if s["id"] != sid]
                _save_sessions(_sessions)
                # 若删除的是活动会话, 自动切到下一个未归档会话
                if _active_session_id == sid:
                    nxt = next((s for s in _sessions if not s["archived"]), _sessions[0])
                    _active_session_id = nxt["id"]
                    with _lock:
                        t = _load_tree_for(nxt["persist_key"])
                        if t is None:
                            t = _make_tree_for(nxt["persist_key"])
                            t.init([{"role": "system", "content": "会话已恢复。"}])
                        _bind_active_tree(t)
                        _migrate_if_needed()
                self._send_json({"sessions": _sessions, "active_id": _active_session_id, "state": state_dict()})
            elif path == "/api/sessions/archive":
                # 显式归档: 传 archived=true/false 直接设置; 省略则切换 (前端兼容).
                sid = data.get("id")
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if not sess:
                    self._send_json({"error": "session not found"}, 404)
                    return
                if "archived" in data:
                    sess["archived"] = bool(data["archived"])
                else:
                    sess["archived"] = not sess.get("archived", False)
                _save_sessions(_sessions)
                self._send_json({"sessions": _sessions, "active_id": _active_session_id})
            elif path == "/api/sessions/unarchive":
                # 显式取消归档 (C5): 无论当前状态, 强制 archived=False.
                sid = data.get("id")
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if not sess:
                    self._send_json({"error": "session not found"}, 404)
                    return
                sess["archived"] = False
                _save_sessions(_sessions)
                self._send_json({"sessions": _sessions, "active_id": _active_session_id})
            elif path == "/api/sessions/rename":
                sid = data.get("id")
                title = (data.get("title") or "").strip() or "未命名会话"
                sess = next((s for s in _sessions if s["id"] == sid), None)
                if not sess:
                    self._send_json({"error": "session not found"}, 404)
                    return
                sess["title"] = title[:60]
                _save_sessions(_sessions)
                self._send_json({"sessions": _sessions, "active_id": _active_session_id})
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
    # 阶段 4.3: 启动健康检查 —— 三行状态打印, 帮助运维快速判断链路
    try:
        import memory_v5.extensions.tree_adapter  # noqa: F401
        sys.stderr.write("[ct] health: tree_adapter OK (树域记忆可用)\n")
    except Exception as e:
        sys.stderr.write(f"[ct] health: tree_adapter UNAVAILABLE ({e}); 树域记忆将降级为空\n")
    if HERMES_AGENT_URL:
        try:
            probe = urllib.request.Request(HERMES_AGENT_URL, method="GET")
            with urllib.request.urlopen(probe, timeout=3) as _pr:
                sys.stderr.write(f"[ct] health: gateway {HERMES_AGENT_URL} reachable (主通道 OK)\n")
        except urllib.error.HTTPError as _he:
            # 405 Method Not Allowed = 端点存在且只接受 POST → gateway 在线
            if _he.code == 405:
                sys.stderr.write(f"[ct] health: gateway {HERMES_AGENT_URL} reachable (主通道 OK, HTTP 405=POST-only)\n")
            else:
                sys.stderr.write(f"[ct] health: gateway {HERMES_AGENT_URL} HTTP {_he.code}; chat 可能降级\n")
        except Exception as e:
            sys.stderr.write(f"[ct] health: gateway {HERMES_AGENT_URL} unreachable ({e}); chat 将降级本地模型\n")
    else:
        sys.stderr.write("[ct] health: HERMES_AGENT_URL 未配置; chat 走本地 DeepSeek 直连\n")
    if _DEEPSEEK_KEY:
        sys.stderr.write("[ct] health: DeepSeek key present (降级链可用)\n")
    else:
        sys.stderr.write("[ct] health: DeepSeek key MISSING; 本地直连将不可用\n")
    # 阶段 5.3: ThreadingHTTPServer 连接上限 + 单请求超时, 防 SSE 长连接线程堆积
    class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = False
        request_queue_size = 128      # 监听队列上限 (防大量短连接打满 backlog)
        timeout = 60                  # 单连接空闲/读超时 (SSE 心跳由应用层负责, 不会误断)
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
