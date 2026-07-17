#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 详细说明见 docs/scripts/bin/download_sensevoice.md
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIR = os.path.join(_ROOT, "data", "models",
                          "sherpa-onnx-sense-voice-zh-en-ja-ko-yue")
REPO_ID = "k2-fsa/sherpa-onnx-sense-voice-zh-en-ja-ko-yue"
FILES = ["model.int8.onnx", "tokens.txt"]


def _ensure_dir() -> None:
    os.makedirs(TARGET_DIR, exist_ok=True)


def _via_huggingface_hub() -> bool:
    try:
        from huggingface_hub import hf_hub_download
    except Exception:
        print("[skip] huggingface_hub 未安装 (pip install huggingface_hub)")
        return False
    print(f"[try] huggingface_hub 下载 {REPO_ID} ...")
    try:
        for f in FILES:
            p = hf_hub_download(
                repo_id=REPO_ID, filename=f, repo_type="model",
                local_dir=TARGET_DIR,
            )
            print("  ->", p)
        return True
    except Exception as e:
        print(f"[fail] huggingface_hub: {e}")
        return False


def _via_modelscope() -> bool:
    try:
        from modelscope import snapshot_download
    except Exception:
        print("[skip] modelscope 未安装 (pip install modelscope)")
        return False
    print(f"[try] modelscope 下载 {REPO_ID} ...")
    try:
        snapshot_download(REPO_ID, local_dir=TARGET_DIR)
        return True
    except Exception as e:
        print(f"[fail] modelscope: {e}")
        return False


def _check() -> None:
    missing = [f for f in FILES if not os.path.isfile(os.path.join(TARGET_DIR, f))]
    if not missing:
        print(f"[ok] 模型已就绪: {TARGET_DIR}")
        sys.exit(0)
    print(f"[warn] 缺失: {missing}")


def main() -> None:
    print(f"目标目录: {TARGET_DIR}")
    _ensure_dir()
    _check()
    if _via_huggingface_hub() and all(
        os.path.isfile(os.path.join(TARGET_DIR, f)) for f in FILES
    ):
        print("[done] 通过 HuggingFace 下载完成")
        return
    if _via_modelscope() and all(
        os.path.isfile(os.path.join(TARGET_DIR, f)) for f in FILES
    ):
        print("[done] 通过 ModelScope 下载完成")
        return
    print("\n[manual] 自动下载均失败。请手动下载以下文件到:")
    print(f"  {TARGET_DIR}/")
    print(f"  仓库: https://huggingface.co/{REPO_ID}")
    print(f"  需要: {FILES}")
    print("下载后重启 bin\\ikaros-voice-ws.py 即可启用高精度 STT。")


if __name__ == "__main__":
    main()
