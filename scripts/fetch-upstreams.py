#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch-upstreams.py — Ikaros 上游组件拉取脚本（幂等）

只负责把「有上游」的组件拉到本地落点，不修改任何仓库内文件。
对应清单见仓库根 UPSTREAM.md。

组件类型:
  - git:      git clone (轻量 --depth 1 --filter=blob:none)；已存在则 git pull
  - release:  走 bin/ikaros-fastdl.py (gopeed/aria2 + 镜像) 下载后解压
  - npm:      runtime/node 的 npm install（全局安装到 runtime/node_modules）

用法:
  python scripts/fetch-upstreams.py            # 拉全部
  python scripts/fetch-upstreams.py neko      # 只拉 neko
  python scripts/fetch-upstreams.py --dry-run  # 不实际下载，打印将要做什么
  python scripts/fetch-upstreams.py --list     # 列出所有组件
"""
import os, sys, json, shutil, subprocess, argparse, zipfile, tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")

# ─── 上游清单（单一事实来源；UPSTREAM.md 是其人类可读版）──────────────
# local: 相对 ROOT 的本地落点目录
# method: git | release | npm
MANIFEST = [
    {
        "name": "neko",
        "desc": "N.E.K.O 桌宠前端 (Apache-2.0)",
        "method": "git",
        "url": "https://github.com/Project-N-E-K-O/N.E.K.O",
        "local": "core/neko",
        "branch": "main",
        "patched": False,
    },
    {
        "name": "hermes-agent",
        "desc": "Hermes Agent 核心 (NousResearch)",
        "method": "git",
        "url": "https://github.com/NousResearch/hermes-agent",
        "local": "hermes-agent",
        "branch": "main",
        "patched": False,
    },
    {
        "name": "hermes-web-ui",
        "desc": "Hermes Web UI (EKKOLearnAI, 可选)",
        "method": "git",
        "url": "https://github.com/EKKOLearnAI/hermes-web-ui",
        "local": "data/webui-new/app",
        "branch": "main",
        "patched": False,
        "optional": True,
    },
    # ── 模型权重（多 GB）──
    {
        "name": "model-qwen3-1.7b",
        "desc": "Qwen3-1.7B GGUF (本地 LLM)",
        "method": "release",
        "url": "https://huggingface.co/Qwen/Qwen3-1.7B-GGUF/resolve/main/qwen3-1_7b-q4_k_m.gguf",
        "local": "core/memory_v5/models/Qwen_Qwen3-1.7B-Q4_K_M.gguf",
        "mirror": "hf",
        "optional": True,
    },
    {
        "name": "model-nomic-embed",
        "desc": "bge-m3 Q8_0 GGUF (embedding; v2-moe 在 llama.cpp 下输出全零)",
        "method": "release",
        "url": "https://huggingface.co/nomic-ai/bge-m3-GGUF/resolve/main/bge-m3-q8_0.gguf",
        "local": "core/memory_v5/models/bge-m3-q8_0.gguf",
        "mirror": "hf",
        "optional": True,
    },
    # ── codebase-memory MCP (release zip) ──
    {
        "name": "mcp-codebase-memory",
        "desc": "codebase-memory-mcp 0.8.1 (windows-amd64 zip)",
        "method": "release",
        "url": "https://github.com/DeusData/codebase-memory-mcp/releases/download/v0.8.1/codebase-memory-mcp-windows-amd64.zip",
        "local": "runtime/MCPServe/codebase-memory",
        "unzip": True,
        "optional": True,
    },
]


def log(msg):
    print(f"[fetch] {msg}")


def run(cmd, cwd=None, check=True):
    log("+ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        log(f"  FAILED ({r.returncode}): {r.stderr.strip()[:300]}")
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}")
    return r


def git_clone_or_pull(item, dry):
    local = os.path.join(ROOT, item["local"])
    if os.path.isdir(os.path.join(local, ".git")):
        log(f"{item['name']}: 已存在 git 仓库, git pull")
        if not dry:
            run(["git", "pull", "--ff-only"], cwd=local, check=False)
        return True
    if os.path.isdir(local) and os.listdir(local):
        log(f"{item['name']}: 目录已存在(非 git), 跳过")
        return True
    log(f"{item['name']}: git clone -> {item['local']}")
    if dry:
        return True
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", "--filter", "blob:none",
           "-b", item.get("branch", "main"), item["url"], local]
    r = run(cmd, check=False)
    return r.returncode == 0


def release_download(item, dry):
    local = os.path.join(ROOT, item["local"])
    if os.path.isfile(local) or (item.get("unzip") and os.path.isdir(local) and os.listdir(local)):
        log(f"{item['name']}: 已存在, 跳过")
        return True
    log(f"{item['name']}: 下载 -> {item['local']}")
    if dry:
        return True
    fastdl = os.path.join(BIN, "ikaros-fastdl.py")
    if not os.path.isfile(fastdl):
        log(f"  [skip] 缺少下载器 {fastdl}")
        return False
    os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
    cmd = [sys.executable, fastdl, item["url"], "-o", local]
    if item.get("mirror"):
        cmd += ["--mirror", item["mirror"]]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"  [FAIL] {r.stderr.strip()[:300]}")
        return False
    # 解压
    if item.get("unzip") and local.lower().endswith((".zip",)) and os.path.isfile(local):
        outdir = os.path.dirname(local)
        log(f"  unzip -> {outdir}")
        with zipfile.ZipFile(local) as z:
            z.extractall(outdir)
        os.remove(local)
    return True


def npm_install(pkg, version, dest, dry):
    node_modules = os.path.join(ROOT, "runtime", "node", "node_modules")
    target = os.path.join(node_modules, pkg)
    if os.path.isdir(target):
        log(f"npm {pkg}: 已安装, 跳过")
        return True
    log(f"npm install {pkg}@{version} -> {node_modules}")
    if dry:
        return True
    os.makedirs(node_modules, exist_ok=True)
    npm = os.path.join(ROOT, "runtime", "node", "npm.cmd")
    if not os.path.isfile(npm):
        log(f"  [skip] 缺少 npm: {npm}")
        return False
    run([npm, "install", f"{pkg}@{version}", "--no-save", "--prefix",
         os.path.join(ROOT, "runtime", "node")], check=False)
    return os.path.isdir(target)


def main():
    ap = argparse.ArgumentParser(description="Ikaros 上游组件拉取（幂等）")
    ap.add_argument("names", nargs="*", help="只拉指定组件 (默认全部)")
    ap.add_argument("--dry-run", action="store_true", help="只打印, 不实际下载")
    ap.add_argument("--list", action="store_true", help="列出所有组件后退出")
    args = ap.parse_args()

    items = MANIFEST
    if args.list:
        for it in items:
            flag = " (可选)" if it.get("optional") else ""
            print(f"  {it['name']:24s} [{it['method']:7s}] {it['desc']}{flag}")
        return

    if args.names:
        items = [it for it in items if it["name"] in args.names]
        if not items:
            log(f"未匹配到组件: {args.names}")
            sys.exit(2)

    ok, skip = 0, 0
    for it in items:
        try:
            if it["method"] == "git":
                res = git_clone_or_pull(it, args.dry_run)
            elif it["method"] == "release":
                res = release_download(it, args.dry_run)
            else:
                log(f"{it['name']}: 未知 method {it['method']}")
                res = False
            if res:
                ok += 1
            else:
                if it.get("optional"):
                    log(f"{it['name']}: 可选组件拉取失败, 跳过")
                    skip += 1
                else:
                    log(f"{it['name']}: 必需组件拉取失败!")
                    sys.exit(1)
        except Exception as e:
            if it.get("optional"):
                log(f"{it['name']}: 可选组件异常({e}), 跳过")
                skip += 1
            else:
                log(f"{it['name']}: 异常 {e}")
                sys.exit(1)

    log(f"完成: 成功 {ok}, 跳过(可选) {skip}")
    if args.dry_run:
        log("(dry-run, 未实际执行)")
    sys.exit(0)


if __name__ == "__main__":
    main()
