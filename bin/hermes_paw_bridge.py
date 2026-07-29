#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hermes-Paw Bridge
=================

伪装成 QwenPaw 的 RESTful 服务，内部用 **Hermes Agent** (run_agent.AIAgent)
替代 QwenPaw 执行 Neko 的"猫爪 / 工具臂"指令。

Neko 的 `brain/openclaw_adapter.py` 只认 QwenPaw 的 API：
    GET  /api/agent/health
    POST /api/agent/compatible-mode/v1/responses
    POST /api/agent/process
本服务在 :8088 实现这三个端点，协议与 QwenPaw 完全一致，因此
`openclaw_adapter.py` 零改动、Neko 无感知——它以为自己连的还是 QwenPaw。

Hermes Agent 能力覆盖 QwenPaw 的"系统操作 / 多模态解析 / 工具调用"角色
（agent.tool_executor 真能跑 shell / 工具，且自带 skills / MCP / 云端 LLM），
且和 Ikaros 共用同一套 Hermes 基础设施。

运行（需在 core/hermes 的 Python 环境下，以便 import run_agent）：
    HERMES_PAW_BASE_URL=http://127.0.0.1:8080/v1 HERMES_PAW_MODEL=Qwen3-1.7B \
        python hermes_paw_bridge.py
若不设 HERMES_PAW_BASE_URL / HERMES_PAW_MODEL，则交给 Hermes Agent 使用其
自身默认 provider 配置（通常即你已在用的云端 LLM）。

环境变量：
    HERMES_AGENT_ROOT    core/hermes 包根目录 (默认 E:\\Ikaros\\core/hermes)
    HERMES_PAW_BASE_URL  OpenAI 兼容 base_url (默认 None -> Hermes 默认)
    HERMES_PAW_MODEL     模型名 (默认 None -> Hermes 默认)
    HERMES_PAW_API_KEY   API Key (默认 None)
    HERMES_PAW_PORT      监听端口 (默认 8088)
    HERMES_PAW_TOOLSETS  逗号分隔的启用工具集 (默认 None -> Hermes 默认)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---- 让 core/hermes 包可导入 ------------------------------------------------
HERMES_AGENT_ROOT = os.environ.get("HERMES_AGENT_ROOT", r"E:\Ikaros\core\hermes")
if HERMES_AGENT_ROOT not in sys.path:
    sys.path.insert(0, HERMES_AGENT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hermes_paw_bridge] %(levelname)s %(message)s",
)
logger = logging.getLogger("hermes_paw_bridge")

try:
    from run_agent import AIAgent
except Exception as exc:  # pragma: no cover
    logger.error("无法导入 core/hermes 的 AIAgent: %s", exc)
    logger.error("请确认在 core/hermes 的 Python 环境下运行本桥。")
    raise

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI(title="Hermes-Paw Bridge (QwenPaw-compatible)")

# ---- 配置 -------------------------------------------------------------------
BASE_URL = os.environ.get("HERMES_PAW_BASE_URL") or None
MODEL = os.environ.get("HERMES_PAW_MODEL") or None
API_KEY = os.environ.get("HERMES_PAW_API_KEY") or None
PORT = int(os.environ.get("HERMES_PAW_PORT", "8088"))
RAW_TOOLSETS = os.environ.get("HERMES_PAW_TOOLSETS") or None
TOOLSETS = [t.strip() for t in RAW_TOOLSETS.split(",") if t.strip()] if RAW_TOOLSETS else None

# ---- 会话历史（按 QwenPaw 的 session_id 维护）------------------------------
_session_histories: Dict[str, List[Dict[str, str]]] = {}
_history_lock = asyncio.Lock()


def _build_agent() -> AIAgent:
    """构造一个 Hermes Agent 实例（每次请求新建，避免跨线程共享状态）。"""
    agent = AIAgent(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL or "",
        enabled_toolsets=TOOLSETS,
    )
    logger.info(
        "Hermes Agent 实例已建 | base_url=%s model=%s toolsets=%s",
        getattr(agent, "base_url", BASE_URL),
        getattr(agent, "model", MODEL),
        TOOLSETS,
    )
    return agent


def _parse_payload(payload: Dict[str, Any]):
    """从 QwenPaw 的 responses/process payload 提取文本与图片 URL。"""
    texts: List[str] = []
    images: List[str] = []

    inputs = payload.get("input")
    if isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = str(part.get("type") or "").lower()
                if ptype in ("input_text", "text"):
                    txt = part.get("text")
                    if isinstance(txt, str) and txt.strip():
                        texts.append(txt.strip())
                elif ptype in ("input_image", "image"):
                    url = (
                        part.get("image_url")
                        or part.get("url")
                        or part.get("data_url")
                    )
                    if isinstance(url, str) and url.strip():
                        images.append(url.strip())

    text = "\n".join(texts).strip()
    return text, images


def _run_agent(text: str, images: List[str], session_id: str) -> str:
    """在独立线程里跑 Hermes Agent，返回最终回复文本。"""
    user_msg = text
    if images:
        user_msg += "\n[用户附带图片: " + ", ".join(images) + "]"

    history = _session_histories.get(session_id, [])

    agent = _build_agent()
    # run_conversation 返回 dict，最终文本在 "final_response"
    result = agent.run_conversation(
        user_msg,
        conversation_history=history,
    )
    reply = ""
    if isinstance(result, dict):
        reply = result.get("final_response") or ""
    if not isinstance(reply, str):
        reply = str(reply)

    # 维护会话历史
    history = history + [
        {"role": "user", "content": user_msg},
        {"role": "assistant", "content": reply},
    ]
    # 控制长度，避免无限增长
    if len(history) > 40:
        history = history[-40:]
    _session_histories[session_id] = history
    return reply


def _format_reply(reply: str) -> Dict[str, Any]:
    """包成 OpenAI Responses 风格，对上 openclaw_adapter._extract_reply_text。"""
    return {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": reply}],
            }
        ]
    }


async def _read_json(request: Request) -> Dict[str, Any]:
    """鲁棒读取请求体 JSON: UTF-8 优先, 回退 GBK (Windows 终端常见非 UTF-8 编码)。"""
    raw = await request.body()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    try:
        return json.loads(raw.decode("latin-1"))
    except Exception:
        return {}


@app.get("/api/agent/health")
async def health():
    return {
        "status": "ok",
        "provider": "hermes-agent",
        "mode": "qwenpaw-compatible",
        "port": PORT,
    }


@app.get("/api/agent/config")
async def config():
    """把桥的配置暴露给管理面板 (供前端展示)。"""
    return {
        "provider": "hermes-agent",
        "mode": "qwenpaw-compatible",
        "port": PORT,
        "base_url": BASE_URL or "(Hermes 默认)",
        "model": MODEL or "(Hermes 默认)",
        "toolsets": TOOLSETS or "(Hermes 默认)",
        "endpoints": {
            "health": "/api/agent/health",
            "responses": "/api/agent/compatible-mode/v1/responses",
            "process": "/api/agent/process",
        },
        "neko_openclaw_url": "http://127.0.0.1:8088",
        "neko_guide_url": "http://127.0.0.1:48911/api/agent/openclaw/guide",
    }


_HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hermes-Paw 桥 · 猫爪服务管理面板</title>
<style>
  :root { --bg:#0f1726; --card:#16203200; --fg:#e6f2ff; --muted:#9fb6cf;
          --accent:#79cbff; --ok:#3ddc84; --line:rgba(121,203,255,.18); }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Segoe UI","PingFang SC",sans-serif;
         background:radial-gradient(circle at top,#173c57,#0f1726 60%);
         color:var(--fg); padding:28px; min-height:100vh; }
  .wrap { max-width:860px; margin:0 auto; }
  h1 { font-size:22px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:20px; }
  .card { background:rgba(20,28,40,.82); border:1px solid var(--line);
          border-radius:14px; padding:18px 20px; margin-bottom:16px; }
  .status { display:flex; align-items:center; gap:10px; font-size:15px; }
  .dot { width:11px; height:11px; border-radius:50%; background:var(--ok);
         box-shadow:0 0 10px var(--ok); }
  .kv { display:grid; grid-template-columns:120px 1fr; gap:6px 14px;
        font-size:13px; margin-top:12px; }
  .kv b { color:var(--muted); font-weight:600; }
  code { background:rgba(121,203,255,.12); padding:2px 7px; border-radius:6px;
         font-size:12px; }
  h2 { font-size:15px; margin:0 0 12px; color:var(--accent); }
  ol { margin:0; padding-left:20px; line-height:1.9; font-size:13px; }
  .row { display:flex; gap:10px; flex-wrap:wrap; margin-top:6px; }
  button, a.btn { background:linear-gradient(180deg,#0a578f,#08345c);
        color:#fff; border:none; padding:9px 16px; border-radius:9px;
        font-size:13px; cursor:pointer; text-decoration:none; display:inline-block; }
  button:hover, a.btn:hover { filter:brightness(1.15); }
  textarea { width:100%; height:70px; background:#0c1422; color:var(--fg);
        border:1px solid var(--line); border-radius:9px; padding:9px;
        font-size:13px; resize:vertical; }
  #testout { margin-top:10px; font-size:13px; color:var(--muted);
        white-space:pre-wrap; min-height:20px; }
  .ep { font-size:12px; color:var(--muted); margin:3px 0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Hermes-Paw 桥 · 猫爪服务管理面板</h1>
  <div class="sub">伪装成 QwenPaw 的本地服务，内部用 Hermes Agent 执行 Neko 的猫爪/工具臂指令。
    这是替代原生 QwenPaw 控制台的面板。</div>

  <div class="card">
    <div class="status"><span class="dot"></span><b>运行中</b>
        <span style="color:var(--muted)">· 提供商 core/hermes · 兼容 QwenPaw API</span></div>
    <div class="kv" id="kv">加载中…</div>
  </div>

  <div class="card">
    <h2>API 端点 (Neko openclaw_adapter 调用)</h2>
    <div class="ep">GET  <code>/api/agent/health</code></div>
    <div class="ep">POST <code>/api/agent/compatible-mode/v1/responses</code></div>
    <div class="ep">POST <code>/api/agent/process</code></div>
  </div>

  <div class="card">
    <h2>在 N.E.K.O 中启用猫爪（官方流程）</h2>
    <ol>
      <li>确认本面板"运行中"且 <code>health</code> 返回 200（即猫爪服务已接上）。</li>
      <li>打开 Neko 的<b>猫爪面板</b>（侧边面板 / 设置里的 Agent OpenClaw）。</li>
      <li>先打开<b>猫爪总开关</b>，确认 <code>openclawUrl</code> 指向
          <code>http://127.0.0.1:8088</code>。</li>
      <li>再打开 <b>OpenClaw 子开关</b>，等待可用性检查通过即可使用。</li>
    </ol>
    <div class="row">
      <a class="btn" id="guideBtn" href="#" target="_blank">打开 Neko 猫爪接入教程</a>
    </div>
  </div>

  <div class="card">
    <h2>连通性自测</h2>
    <textarea id="testin" placeholder="输入一句测试指令，例如：帮我列一下当前目录">用一句话告诉我今天是星期几</textarea>
    <div class="row"><button id="testBtn">发送测试</button></div>
    <div id="testout"></div>
  </div>
</div>
<script>
const cfg = __CONFIG__;
document.getElementById('kv').innerHTML =
  '<b>端口</b><span>'+cfg.port+'</span>'+
  '<b>Base URL</b><span>'+cfg.base_url+'</span>'+
  '<b>模型</b><span>'+cfg.model+'</span>'+
  '<b>工具集</b><span>'+(Array.isArray(cfg.toolsets)?cfg.toolsets.join(', '):cfg.toolsets)+'</span>'+
  '<b>Neko openclawUrl</b><span>'+cfg.neko_openclaw_url+'</span>';
document.getElementById('guideBtn').href = cfg.neko_guide_url;

document.getElementById('testBtn').onclick = async () => {
  const out = document.getElementById('testout');
  const text = document.getElementById('testin').value.trim();
  if (!text) { out.textContent = '请输入测试指令'; return; }
  out.textContent = '请求中…';
  try {
    const r = await fetch('/api/agent/compatible-mode/v1/responses', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ session_id:'panel_test',
        input:[{type:'message',role:'user',
          content:[{type:'input_text',text:text}]}] })
    });
    const j = await r.json();
    const txt = (j.output||[]).flatMap(o=>o.content||[])
                  .map(c=>c.text||'').join('');
    out.textContent = 'HTTP '+r.status+'：\n'+(txt || JSON.stringify(j,null,2));
  } catch(e) { out.textContent = '请求失败：'+e; }
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/panel", response_class=HTMLResponse)
async def management_panel():
    """猫爪服务管理面板（替代原生 QwenPaw 控制台 http://127.0.0.1:8088）。"""
    cfg = await config()
    html = _HTML_PAGE.replace("__CONFIG__", json.dumps(cfg, ensure_ascii=False))
    return HTMLResponse(content=html)


@app.post("/api/agent/compatible-mode/v1/responses")
async def responses_endpoint(request: Request):
    payload = await _read_json(request)
    session_id = (
        payload.get("session_id")
        or (payload.get("conversation") or {}).get("id")
        or "default"
    )
    text, images = _parse_payload(payload)
    if not text:
        return JSONResponse(
            status_code=400,
            content={"error": "empty instruction", "status": "failed"},
        )
    try:
        reply = await asyncio.to_thread(_run_agent, text, images, session_id)
    except Exception as exc:
        logger.exception("Hermes Agent 执行失败: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": f"hermes agent failed: {exc}", "status": "failed"},
        )
    if not reply:
        return JSONResponse(
            status_code=502,
            content={"error": "hermes agent returned empty reply", "status": "failed"},
        )
    return _format_reply(reply)


@app.post("/api/agent/process")
async def process_endpoint(request: Request):
    # 与 responses 端点共用解析逻辑（process payload 的 content 用 type=text/image）
    payload = await _read_json(request)
    session_id = payload.get("session_id") or "default"
    text, images = _parse_payload(payload)
    if not text:
        return JSONResponse(
            status_code=400,
            content={"error": "empty instruction", "status": "failed"},
        )
    try:
        reply = await asyncio.to_thread(_run_agent, text, images, session_id)
    except Exception as exc:
        logger.exception("Hermes Agent 执行失败: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": f"hermes agent failed: {exc}", "status": "failed"},
        )
    if not reply:
        return JSONResponse(
            status_code=502,
            content={"error": "hermes agent returned empty reply", "status": "failed"},
        )
    return _format_reply(reply)


def main():
    logger.info(
        "启动 Hermes-Paw Bridge @ :%d | base_url=%s model=%s toolsets=%s",
        PORT, BASE_URL, MODEL, TOOLSETS,
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
