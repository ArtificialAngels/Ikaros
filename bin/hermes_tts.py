#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hermes_tts.py — 复用 Hermes Agent 内置的 TTS 服务。

设计: 本脚本在 hermes-agent 的 venv 下运行, 直接复用
``hermes-agent/tools/tts_tool._generate_edge_tts`` —— 即 Hermes Agent
内置的 TTS 生成逻辑 (edge-tts 后端)。voice-ws 通过 subprocess 调用
本脚本, 把文本合成 mp3 后再取回二进制帧下发给桌宠。

为什么绕一层而不是直接在 voice-ws 里 import hermes-agent:
hermes-agent 依赖其自有 venv 的包; 在 voice-ws 的 portable-python
进程里硬 import 会缺依赖。subprocess 到 hermes venv 既隔离又复用
"Hermes Agent 内置的 TTS 服务"。

用法 (均由 voice-ws 调用):
    hermes_tts.py <textfile> <outfile>
    <textfile> : UTF-8 文本文件路径 (避免命令行引号/编码问题)
    <outfile>  : 输出 mp3 路径
成功打印 outfile 绝对路径; 失败打印 {"error": "..."} 到 stdout 并退出码非 0。

voice 解析: 优先用 Hermes 配置 tts.edge.voice; 若该值是无效占位
(如 "cn-"), 回退到 zh-CN-XiaoxiaoNeural, 保证一定能合成。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

# 让 import 找到 hermes-agent 的 tools 包 (含 tts_tool)
HERMES_ROOT = os.environ.get("HERMES_ROOT", r"E:/Ikaros/hermes-agent")
if HERMES_ROOT not in sys.path:
    sys.path.insert(0, HERMES_ROOT)

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


def _resolve_voice() -> str:
    """优先取 Hermes 配置里的 tts.edge.voice, 无效则回退默认。"""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        edge = (cfg.get("tts") or {}).get("edge") or {}
        v = edge.get("voice")
        if isinstance(v, str) and v.strip() and _is_valid_voice(v.strip()):
            return v.strip()
    except Exception:
        pass
    return DEFAULT_VOICE


def _is_valid_voice(v: str) -> bool:
    """edge-tts 完整 voice 形如 zh-CN-XiaoxiaoNeural (至少两个 '-',
    且不以 '-' 结尾)。'cn-' 这种占位会判 False。"""
    if not v or v.endswith("-"):
        return False
    if v.count("-") < 2:
        return False
    return True


async def _synth(text: str, out_path: str, voice: str) -> str:
    from tools.tts_tool import _generate_edge_tts
    tts_config = {"edge": {"voice": voice}}
    return await _generate_edge_tts(text, out_path, tts_config)


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: hermes_tts.py <textfile> <outfile>"},
                         ensure_ascii=False))
        return 2
    text_file = sys.argv[1]
    out_path = sys.argv[2]
    try:
        with open(text_file, "r", encoding="utf-8") as fh:
            text = fh.read()
    except Exception as e:
        print(json.dumps({"error": f"read text file failed: {e}"},
                         ensure_ascii=False))
        return 3
    if not text.strip():
        print(json.dumps({"error": "empty text"}, ensure_ascii=False))
        return 4

    voice = _resolve_voice()
    try:
        # 确保父目录存在
        parent = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(parent, exist_ok=True)
        path = asyncio.run(_synth(text, out_path, voice))
    except Exception as e:
        print(json.dumps({"error": f"TTS generation failed: {e}"},
                         ensure_ascii=False))
        return 1

    if not os.path.exists(path) or os.path.getsize(path) == 0:
        print(json.dumps({"error": "TTS produced no output"}, ensure_ascii=False))
        return 1

    # 成功: 打印输出文件绝对路径
    print(os.path.abspath(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
