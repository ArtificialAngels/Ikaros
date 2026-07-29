#!/usr/bin/env python3
# 详细说明见 docs/scripts/bin/llama-help.md
"""llama-help — 查看本地 LLM (:8080) 配置逻辑 + 状态 + 热载入/停止。

配置逻辑统一来自 core/memory_v5/models/model_config.py (经看门狗 _load_model_cfg 读取),
本工具只做「只读展示」与「控制」, 不重复定义任何启动参数。

运行模式 (2026-07-26 后重构):
  - 看门狗只检测 :8080 端口在不, 不主动拉起模型、不在启动/巡检时加载模型。
  - 模型在 agent 第一次调用本地 LLM 时由 ensure_local_llm() 热载入,
    或手动 `llama-help --hotload` 触发。

子命令:
  (默认)       打印配置逻辑摘要 + 当前状态
  --config     仅打印配置逻辑 (模型 / 二进制 / 端口 / 参数 / 环境变量覆盖)
  --status     仅打印端口与 /health 状态
  --hotload    触发热载入 (未起则 detached spawn llama-server 并等 /health 200)
  --stop       停止本地 llama-server (:8080)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("IKAROS_ROOT", r"E:\Ikaros"))
BIN = ROOT / "bin"

# 复用看门狗模块的配置逻辑与热载入/端口工具 (单一事实来源)
# 文件名含连字符, 用 wd_import 按路径加载
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))
from wd_import import load_watchdog  # noqa: E402

wd = load_watchdog()


def _cfg() -> dict:
    return wd._load_model_cfg()


def show_config() -> None:
    cfg = _cfg()
    print("=== 本地 LLM (:8080) 配置逻辑 ===")
    print(f"  来源        : core/memory_v5/models/model_config.py (resolve_model_config)")
    print(f"  模型文件    : {wd.LLM_MODEL}")
    print(f"               : {'存在 ✓' if wd.LLM_MODEL.exists() else '缺失 ✗'}")
    print(f"  llama 二进制 : {wd.LLAMA_BIN}")
    print(f"               : {'存在 ✓' if wd.LLAMA_BIN.exists() else '缺失 ✗'}")
    print(f"  监听地址    : {cfg.get('host', '127.0.0.1')}:{wd.LLM_PORT}")
    print(f"  ctx-size    : {cfg.get('ctx_size', 8192)}")
    print(f"  gpu-layers  : {cfg.get('gpu_layers', 'auto')}")
    print(f"  flash-attn  : {cfg.get('flash_attn', 'auto')}")
    print(f"  alias       : {cfg.get('alias', 'local-llm')}")
    print(f"  cont-batch  : {cfg.get('cont_batching', True)}")
    print(f"  jinja       : {cfg.get('jinja', True)}")
    print("  --- 环境变量覆盖 ---")
    print(f"  IKAROS_LLAMA_SERVER  : {os.environ.get('IKAROS_LLAMA_SERVER', '(未设 → 用默认)')}")
    print(f"  IKAROS_MODEL_LLM     : {os.environ.get('IKAROS_MODEL_LLM', '(未设 → 用配置)')}")
    print(f"  IKAROS_LOCAL_LLM_URL : {os.environ.get('IKAROS_LOCAL_LLM_URL', '(未设 → http://127.0.0.1:8080)')}")
    print(f"  IKAROS_LOCAL_LLM_ALIAS: {os.environ.get('IKAROS_LOCAL_LLM_ALIAS', '(未设 → local-llm)')}")
    print("  --- 运行模式 ---")
    print("  懒加载 / 按需: 看门狗只检测端口, 不自动拉起模型。")
    print("  模型在 agent 调用本地 LLM 时热载入, 或 `llama-help --hotload` 手动触发。")
    print("  llama-server 完整启动参数:")
    print("    " + " ".join(wd._build_llm_argv()))


def show_status() -> None:
    alive = wd.MemoryWatchdog._port_alive(wd.LLM_PORT)
    healthy = wd.MemoryWatchdog._health_ok(wd.LLM_PORT) if alive else False
    print("=== :8080 状态 ===")
    print(f"  端口监听 : {'是 ✓' if alive else '否 ✗'}")
    if alive:
        print(f"  /health  : {'200 OK ✓' if healthy else '无/失败 (可能模型未就绪)'}")
    else:
        print(f"  /health  : N/A (端口未监听)")
    print(f"  模式     : 懒加载 (看门狗不自动拉起)")


def do_hotload() -> int:
    print("[llama-help] 触发热载入 :8080 ...")
    ok = wd.ensure_local_llm(timeout=180)
    print(f"  结果: {'已就绪 ✓' if ok else '失败 ✗'}")
    return 0 if ok else 1


def do_stop() -> int:
    print("[llama-help] 停止 llama-server (:8080) ...")
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe", "/T"],
                   capture_output=True)
    print("  已发送停止信号 (taskkill /F /IM llama-server.exe /T)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="llama-help: 本地 LLM (:8080) 配置/状态/热载入")
    ap.add_argument("--config", action="store_true", help="仅打印配置逻辑")
    ap.add_argument("--status", action="store_true", help="仅打印端口/health 状态")
    ap.add_argument("--hotload", action="store_true", help="触发热载入 (拉起模型)")
    ap.add_argument("--stop", action="store_true", help="停止 llama-server (:8080)")
    args = ap.parse_args()

    if args.hotload:
        sys.exit(do_hotload())
    if args.stop:
        sys.exit(do_stop())
    if args.status:
        show_status()
        return
    if args.config:
        show_config()
        return

    # 默认: 配置 + 状态
    show_config()
    print()
    show_status()


if __name__ == "__main__":
    main()
