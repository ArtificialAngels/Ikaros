#!/usr/bin/env python3
"""Launcher for Ikaros hermes-bridge — studio 式「0 侵入」包装层.

让对话树(:48920)继续调它熟悉的 OpenAI-wire /v1/chat/completions (零前端改动),
内部驱动纯净 Hermes gateway(:8642) 的原生 session-chat 端点, 把 reasoning/工具/
正文翻译为对话树方言. core/hermes 工作树因此可保持 100% 纯净.

运行:  python bin/hermes-bridge.py
环境变量 (均可选, 有上游默认值):
  HERMES_BRIDGE_HOST      默认 127.0.0.1
  HERMES_BRIDGE_PORT      默认 8650
  HERMES_GATEWAY_URL      默认 http://127.0.0.1:8642  (纯净 Hermes gateway)
  HERMES_BRIDGE_API_KEY   用于创建 Hermes session; 默认回退 API_SERVER_KEY / HERMES_AGENT_KEY
"""
import os
import sys

os.environ.setdefault("HERMES_BRIDGE_HOST", "127.0.0.1")
os.environ.setdefault("HERMES_BRIDGE_PORT", "8650")
os.environ.setdefault("HERMES_GATEWAY_URL", "http://127.0.0.1:8642")

_BRIDGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core", "hermes-bridge",
)
if _BRIDGE_DIR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR)

# studio 式 0 侵入：原 hermes mcp_tool 补丁的 IKAROS_* 自推导逻辑已搬到
# 此 Ikaros 自有模块。启动器拉起前确保环境完整（setdefault，不覆盖 build_env）。
from inject_ikaros_paths import inject_ikaros_root_paths  # noqa: E402
inject_ikaros_root_paths()

from server import main  # noqa: E402

if __name__ == "__main__":
    main()
