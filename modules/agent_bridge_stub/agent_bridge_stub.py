"""
agent_bridge_stub - Reverse-proxy router for port 18765
======================================================
Quest 修了 webui 聊天挂的问题: stub 占着 :18765 让真 broker 起不来.
现在我们改成真正的 router: 路径分拣, 部分请求转 :7860 bridge, 其他透传到 :18765 broker.

设计思路 (给 Quest 参考):
  - 用 fastapi-reverse-proxy 的 proxy_pass 做透传
  - 路径白名单 (前缀匹配):
      /v1/reach/*        -> :7860 bridge  (Agent-Reach)
      /v1/notebooklm/*   -> :7860 bridge  (notebooklm-py)
      /v1/ikaros/*       -> :7860 bridge  (Neuro memory/sessions)
      /v1/llama/*        -> :7860 bridge  (本地 LLM 管理)
      /v1/models/*       -> :7860 bridge  (模型 warmup/list)
      其他路径              -> :18765 broker (webui chat)
  - 长连接 httpx.AsyncClient (启动时建, 关闭时清)
  - 日志每条请求 (方法+路径+上游+状态码), 出错带详细错误

历史:
  - 2026-06-26 Quest 把 stub 禁用 (module.json -> module.json.disabled)
    原因: stub 是 TCP shim, 占着 :18765 让 webui 真 broker 起不来, 聊天挂
  - 2026-06-26 Ikaros 把 stub 改造成真 router (本文件)
    新增: 路径分拣, 真透传 (streaming/WebSocket 都支持)
    依赖: fastapi-reverse-proxy (PyPI) + httpx
"""
import logging
import os
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

# 配置
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://127.0.0.1:7860")
BROKER_URL = os.environ.get("BROKER_URL", "http://127.0.0.1:18765")
HOST = "127.0.0.1"
PORT = 18765

# 路径前缀 -> 上游映射
BRIDGE_PREFIXES = (
    "/v1/reach",
    "/v1/notebooklm",
    "/v1/ikaros",
    "/v1/llama",
    "/v1/models",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [stub] %(message)s"
)
log = logging.getLogger("agent_bridge_stub")

app = FastAPI(title="agent_bridge_stub (router)", version="2.0.0")


@app.on_event("startup")
async def startup():
    """建长连接 httpx client"""
    from fastapi_reverse_proxy import create_httpx_client
    create_httpx_client(app)
    log.info(f"router up: bridge={BRIDGE_URL}, broker={BROKER_URL}, "
             f"bridge_prefixes={BRIDGE_PREFIXES}")


@app.on_event("shutdown")
async def shutdown():
    """关长连接"""
    from fastapi_reverse_proxy import close_httpx_client
    await close_httpx_client(app)
    log.info("router down")


def _route_target(path: str) -> str:
    """路径分拣: 返回 upstream URL (without trailing slash)"""
    for prefix in BRIDGE_PREFIXES:
        if path.startswith(prefix):
            return BRIDGE_URL
    return BROKER_URL


@app.get("/health")
async def health():
    """健康检查 (Quest 修的 webui 探活用)"""
    return {"status": "ok", "router": "agent_bridge_stub v2.0.0",
            "bridge": BRIDGE_URL, "broker": BROKER_URL}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def route_http(full_path: str, request: Request):
    """所有 HTTP 请求都过这里, 按路径分拣"""
    target = _route_target("/" + full_path)
    try:
        from fastapi_reverse_proxy import proxy_pass
        response = await proxy_pass(request, host=target, timeout=60.0)
        log.info(f"{request.method} /{full_path} -> {target} ({response.status_code})")
        return response
    except Exception as e:
        log.exception(f"proxy failed: {e}")
        return JSONResponse({"error": str(e), "upstream": target}, status_code=502)


@app.websocket("/{full_path:path}")
async def route_ws(websocket: WebSocket, full_path: str):
    """WebSocket 透传 - 跟 HTTP 同样的分拣规则"""
    target = _route_target("/" + full_path)
    # WebSocket URL 转换: http:// -> ws://
    ws_target = target.replace("http://", "ws://").replace("https://", "wss://")
    try:
        from fastapi_reverse_proxy import proxy_pass_websocket
        log.info(f"WS /{full_path} -> {ws_target}")
        await proxy_pass_websocket(websocket, host=ws_target, timeout=10.0)
    except Exception as e:
        log.exception(f"WS proxy failed: {e}")
        await websocket.close(code=1011, reason=str(e)[:100])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")