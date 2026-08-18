#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup-native.py — Ikaros 原生配置脚本（幂等）

在 fetch-upstreams.py 拉取上游之后运行，做「我们自己的」落地配置:
  1. 校验关键 runtime exe 是否就位 (portable-python / node / llama-server / dsh)
  2. 生成/刷新 core/env/ikaros-paths.json (path 以 IKAROS_ROOT 相对化, 不写死盘符)
  3. 生成 dsh profile 环境参考 (llama-local 指向 http://127.0.0.1:8080/v1)
  4. 校验 dsh (deepseek-harness) 本地安装是否就位; 未装则提示但不阻断
  5. 写 .env 风格的 IKAROS_ROOT 提示 (不覆盖已有)

2026-08-18: hermes / neko 配置段已随底座退役移除，改为 dsh (deepseek-harness) 语义。

不修改任何上游代码。失败给出明确提示，便于定位缺了哪个上游。

用法:
  python scripts/setup-native.py
  python scripts/setup-native.py --check   # 只校验, 不写文件
"""
import os, sys, json, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print(f"[setup] {msg}")


def resolve(p):
    return os.path.normpath(os.path.join(ROOT, p))


def check_exe(path, label):
    ok = os.path.isfile(resolve(path))
    log(f"  {'OK ' if ok else 'MISS'} {label}: {path}")
    return ok


def build_paths():
    """按 IKAROS_ROOT 相对化生成路径表（避免写死盘符）。"""
    rt = "runtime"
    return {
        "_comment": "Ikaros 路径配置 - 由 scripts/setup-native.py 生成 (相对 IKAROS_ROOT)。",
        "_version": "2.0",
        "ikaros_root": ROOT,
        "core": {
            "python": resolve(f"{rt}/portable-python/python.exe"),
            "portable_python": resolve(f"{rt}/portable-python/python.exe"),
            "runtime": resolve(rt),
            "node": resolve(f"{rt}/node/node.exe"),
            "npm": resolve(f"{rt}/node/npm.cmd"),
            "node_modules": resolve(f"{rt}/node/node_modules"),
            "aria2": resolve(f"{rt}/aria2"),
            "gopeed": resolve(f"{rt}/gopeed"),
            "rpc_server": resolve(f"{rt}/rpc-server"),
            "data": resolve("data"),
            "data_models": resolve("data/models"),
            "bin": resolve("bin"),
            "config": resolve("config"),
            "logs": resolve("data/logs"),
        },
        "dsh": {
            "root": resolve("runtime/dsh"),
            "source": resolve("runtime/deepseek-harness-master"),
            "bin": resolve("runtime/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"),
            "profile": resolve("data/dsh/profiles"),
            "web_port": 3080,
            "overlay": resolve("core/ikaros-dsh/cordis.patch.yml"),
        },
        "memory": {
            "root": resolve("core/memory_v5"),
            "data": resolve("core/memory_v5/data"),
            "models": resolve("core/memory_v5/models"),
            "db": resolve("core/memory_v5/data/v5/v5.db"),
            "chromadb": resolve("core/memory_v5/data/v5/chroma"),
        },
        "llama": {
            "version": "b10000-cuda",
            "dir": resolve(f"{rt}/llama/b10000-cuda"),
            "server": resolve(f"{rt}/llama/b10000-cuda/llama-server.exe"),
            "cli": resolve(f"{rt}/llama/b10000-cuda/llama-cli.exe"),
        },
        "models": {
            "embedding": resolve("core/memory_v5/models/bge-m3-q8_0.gguf"),
            "llm": resolve("core/memory_v5/models/Phi-4-mini-instruct-Q4_K_M.gguf"),
        },
        "mcp": {
            "root": resolve(f"{rt}/MCPServe"),
            "codebase_memory_exe": resolve(f"{rt}/MCPServe/codebase-memory/package/bin/codebase-memory-mcp.exe"),
        },
        "ports": {
            "embedding": 8587,
            "llama": 8080,
            "dsh_web": 3080,
            "conversation_tree": 48920,
            "dashboard": 9100,
        },
    }


def write_dsh_profile_env():
    """生成 data/dsh/profiles/env.json 参考（llama-local 指向本地 :8080）。"""
    target = resolve("data/dsh/profiles/env.json")
    if os.path.isfile(target):
        log(f"  dsh profile env 已存在, 跳过: {target}")
        return True
    os.makedirs(os.path.dirname(target), exist_ok=True)
    content = {
        "local_llm": {"base_url": "http://127.0.0.1:8080/v1", "model": "local-llm"},
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "model": "deepseek-v4-flash",
        },
    }
    with open(target, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
    log(f"  dsh profile env 已生成: {target}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Ikaros 原生配置（幂等）")
    ap.add_argument("--check", action="store_true", help="只校验, 不写文件")
    args = ap.parse_args()

    log(f"IKAROS_ROOT = {ROOT}")

    # 1) 校验关键 runtime exe
    log("校验关键 runtime 组件:")
    checks = [
        ("runtime/portable-python/python.exe", "portable-python"),
        ("runtime/node/node.exe", "node"),
        ("runtime/llama/b10000-cuda/llama-server.exe", "llama-server"),
        ("runtime/gopeed/gopeed-web.exe", "gopeed (下载器)"),
        ("runtime/aria2/aria2c.exe", "aria2 (兜底下载)"),
    ]
    missing = [label for p, label in checks if not check_exe(p, label)]

    # 2) dsh 落点校验
    dsh_bin = resolve("runtime/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js")
    dsh_ok = os.path.isfile(dsh_bin)
    log(f"  {'OK ' if dsh_ok else 'MISS'} dsh 本地安装: {dsh_bin}")
    if not dsh_ok:
        log("    提示: 未检测到 dsh (deepseek-harness)，运行 `cd runtime/dsh && npm install` 安装")

    # 3) 写配置
    if args.check:
        log("(check 模式, 不写文件)")
    else:
        log("写入原生配置:")
        paths = build_paths()
        pj = resolve("core/env/ikaros-paths.json")
        with open(pj, "w", encoding="utf-8") as f:
            json.dump(paths, f, ensure_ascii=False, indent=2)
        log(f"  paths -> {pj}")
        write_dsh_profile_env()

    # 4) 结论
    if missing:
        log(f"缺少必需 runtime 组件: {missing}")
        log("请先运行 `python scripts/fetch-upstreams.py` 拉取 runtime 工具链。")
        sys.exit(1)
    if not dsh_ok:
        log("dsh 未安装（工作引擎缺失，建议尽快安装）。")
    log("原生配置完成。")


if __name__ == "__main__":
    main()
