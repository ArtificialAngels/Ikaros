#!/usr/bin/env python3
r"""
Ikaros — 本地 GGUF 模型定时扫描 & 注册
=======================================

在 llama-server router 模式下, 新放入 data/models/ 的 GGUF 文件不会自动出现在
/v1/models 列表中。此脚本定时扫描磁盘, 通过桥 API 将新模型注册到 llama-server。

用法:
    portable-python\python.exe bin\scan-local-models.py          # 单次扫描
    portable-python\python.exe bin\scan-local-models.py --watch  # 启动后每小时扫描

首次启动时自动扫描所有模型并注册, 之后每小时增量扫描。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.model_manager.gguf import list_gguf_models

# ── 配置 ──
MODELS_DIR = _ROOT / "data" / "models"
BRIDGE_BASE = "http://127.0.0.1:7860"
LLAMA_BASE = "http://127.0.0.1:8080"
SCAN_INTERVAL_HOURS = 1  # 每小时扫描
REGISTRY_CACHE = _ROOT / "data" / "logs" / "model-registry.json"


def log(msg: str):
    print(f"[scan-models] {msg}", flush=True)


def api_get(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log(f"GET {url} → {e}")
        return None


def api_post(url: str, body: dict, timeout: float = 30.0) -> dict | None:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        log(f"POST {url} → HTTP {e.code}: {body_text[:120]}")
        return None
    except Exception as e:
        log(f"POST {url} → {e}")
        return None


def get_known_models() -> set[str]:
    """从桥 API 获取 llama-server 已注册的模型列表."""
    data = api_get(f"{BRIDGE_BASE}/v1/models/status", timeout=5.0)
    if not data:
        return set()
    known = set()
    for m in data.get("available", []):
        mid = m.get("id", "")
        if mid:
            known.add(mid)
    return known


def scan_and_register() -> int:
    """扫描磁盘上的 GGUF 并注册新模型. 返回新注册数量."""
    if not MODELS_DIR.is_dir():
        log(f"模型目录不存在: {MODELS_DIR}")
        return 0

    # 1. 扫描磁盘
    disk_models = list_gguf_models(MODELS_DIR)
    disk_names = {m["name"] for m in disk_models}
    log(f"磁盘上有 {len(disk_names)} 个 GGUF 模型")

    if not disk_names:
        return 0

    # 2. 获取已注册
    known = get_known_models()
    log(f"llama-server 已注册 {len(known)} 个模型")

    # 3. 找出新模型
    new_models = disk_names - known
    if not new_models:
        log("没有新模型需要注册")
        return 0

    log(f"发现 {len(new_models)} 个新模型, 正在注册到 llama-server...")
    registered = 0
    for name in sorted(new_models):
        # 尝试通过桥 /v1/models/load 注册
        result = api_post(f"{BRIDGE_BASE}/v1/models/load", {"model": name}, timeout=60.0)
        if result is not None:
            log(f"  ✅ {name} 注册成功")
            registered += 1
        else:
            log(f"  ❌ {name} 注册失败")

    # 4. 持久化注册清单
    try:
        registry = {
            "ts": time.time(),
            "models": sorted(disk_names),
            "newly_registered": sorted(new_models),
            "count": len(disk_names),
        }
        REGISTRY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_CACHE.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"写入注册缓存失败: {e}")

    return registered


def watch_loop():
    """每 SCAN_INTERVAL_HOURS 小时扫描一次."""
    log(f"启动定时扫描模式 (每 {SCAN_INTERVAL_HOURS} 小时)")
    while True:
        try:
            scan_and_register()
        except Exception as e:
            log(f"扫描异常: {e}")
        time.sleep(SCAN_INTERVAL_HOURS * 3600)


def main():
    # 确保 httpx 不读取代理
    for k in list(os.environ.keys()):
        if "proxy" in k.lower():
            os.environ.pop(k, None)

    scan_and_register()

    if "--watch" in sys.argv:
        watch_loop()


if __name__ == "__main__":
    main()
