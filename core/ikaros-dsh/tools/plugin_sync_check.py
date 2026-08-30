"""检查 dsh profile 里装载的 Ikaros 插件是否与源码同步 (2026-08-30)。

为什么需要它:
    `pnpm add file:<dir>` 是**复制**, 不是符号链接 —— 改了 `src/` 或 `bin/`
    之后必须 `npm run build` + `pnpm remove` + `pnpm add` 才会进到 dsh 实际
    加载的那份包里。漏了这一步:
      - 代码看着改了, 测试从源码目录跑也通
      - 但 :3080 上跑着的 dsh 加载的是 `~/.dsh/profiles/web/node_modules/` 里的旧副本
      - **全程无任何报错** —— 旧代码安静地跑着, 新功能像不存在一样

    这是实测踩到的: 2026-08-30 改完 `src/index.ts` + `bin/v5_call.py` (新增
    `loop` op), 本地 dist 也构建到了当天, 但 profile 里装的还是 08-24 的副本
    —— Loop 三阶段在整个 dsh 里都是死的, 而冒烟测试从源码目录跑**全通**。
    靠人肉 `ls -la` 比时间戳不会有人记得做, 所以做成可执行检查。

判定方式:
    **内容 sha256**, 不比时间戳。时间戳会撒谎:
      - pnpm 复制会刷新 mtime, 但也可能保留
      - 构建产物内容变了但 tsc 输出 determinism 让大小一致
    哈希是唯一不会误判的依据。时间戳只作为人类可读的上下文一并打印。

用法:
    python core/ikaros-dsh/tools/plugin_sync_check.py            # 检查
    python core/ikaros-dsh/tools/plugin_sync_check.py --json     # 机器可读
    python core/ikaros-dsh/tools/plugin_sync_check.py --fix-cmd  # 打印修复命令

退出码:
    0 = 已同步 (或本次改动无需重装)
    1 = 有 STALE / MISSING —— dsh 跑的不是最新代码
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# ⚠️ 层级: tools/ -> ikaros-dsh/ -> core/ -> Ikaros 根, 所以是 parents[3]。
#    少一层会静默算成 E:/Ikaros/core/core/... 然后报「找不到」—— 2026-08-24
#    reflect/scheduler.py 用 `Path(__file__).parent` 锚定, 在改包名 v5 -> memory_v5
#    后嵌套层级变化, 把状态文件写进 core/memory_v5/reflect/data/v5/ 孤儿目录。
#    **锚定路径一律断言一次, 别靠数。**
_ROOT = Path(__file__).resolve().parents[3]          # E:/Ikaros
assert (_ROOT / "AGENTS.md").exists(), f"_ROOT 锚错了: {_ROOT}"
_PLUGINS = _ROOT / "core" / "ikaros-dsh" / "plugins"
_PROFILE_NM = Path.home() / ".dsh" / "profiles" / "web" / "node_modules"

#: 不做比较的噪声文件 (Python 字节码缓存, 每次运行都会生成)
_NOISE_SUFFIXES = {".pyc", ".pyo"}
_NOISE_DIRS = {"__pycache__", ".cache", "node_modules"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def _rel_files(base: Path, *, only_dirs: list[str] | None = None) -> dict[str, Path]:
    """收集 base 下 (可选限定子目录) 的相对路径 -> Path, 排除噪声。"""
    out: dict[str, Path] = {}
    roots = [base / d for d in only_dirs] if only_dirs else [base]
    for r in roots:
        if not r.exists():
            continue
        for p in r.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _NOISE_DIRS for part in p.relative_to(base).parts):
                continue
            if p.suffix in _NOISE_SUFFIXES:
                continue
            out[p.relative_to(base).as_posix()] = p
    return out


def _pkg_meta(src: Path) -> tuple[str | None, list[str]]:
    """读源码插件的 package.json -> (包名, files 白名单)。读真源, 不猜目录名 ——
    `@ikaros/dsh-ikaros-memory` 与目录 `ikaros-memory` 无法互相推导。"""
    pj = src / "package.json"
    if not pj.exists():
        return None, []
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, []
    return data.get("name"), list(data.get("files") or [])


def _installed_dir(pkg_name: str) -> Path:
    """`@ikaros/dsh-ikaros-memory` -> ~/.dsh/.../node_modules/@ikaros/dsh-ikaros-memory

    就按 npm 的目录约定展开: scope 包是 `@scope/name` 两级, 无 scope 是一级。
    """
    return _PROFILE_NM / pkg_name


def check() -> dict:
    results: list[dict] = []
    if not _PLUGINS.exists():
        return {"ok": False, "error": f"找不到 {_PLUGINS}", "plugins": []}

    for src in sorted(p for p in _PLUGINS.iterdir() if p.is_dir()):
        pkg_name, files_whitelist = _pkg_meta(src)
        if not pkg_name:
            results.append({"dir": src.name, "status": "skip",
                            "detail": "package.json 缺失或没有 name"})
            continue

        inst = _installed_dir(pkg_name)
        if not inst.exists():
            results.append({
                "dir": src.name, "pkg": pkg_name, "status": "missing_pkg",
                "installed": str(inst),
                "detail": "profile 里没装这个包 —— dsh 根本加载不到它",
            })
            continue

        # 只比 package.json `files` 声明的目录 (dist / bin) ——
        # 源码树里的 src/、node_modules/、tsconfig 等本来就不该进安装包。
        only = files_whitelist or None
        src_files = _rel_files(src, only_dirs=only)
        inst_files = _rel_files(inst, only_dirs=only)

        stale, missing, extra = [], [], []
        for rel, sp in sorted(src_files.items()):
            ip = inst_files.get(rel)
            if ip is None:
                missing.append({"file": rel, "detail": "安装包里没有"})
            elif _sha256(sp) != _sha256(ip):
                stale.append({
                    "file": rel,
                    "src_mtime": sp.stat().st_mtime,
                    "inst_mtime": ip.stat().st_mtime,
                })
        for rel in sorted(set(inst_files) - set(src_files)):
            extra.append({"file": rel, "detail": "源码里已删, 安装包里还在"})

        status = "ok" if not (stale or missing) else "stale"
        results.append({
            "dir": src.name,
            "pkg": pkg_name,
            "status": status,
            "installed": str(inst),
            "compared": len(src_files),
            "stale": stale,
            "missing": missing,
            "extra": extra,
        })

    ok = all(r.get("status") in {"ok", "skip"} for r in results)
    return {"ok": ok, "plugins": results}


def _ts(t: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(t).strftime("%m-%d %H:%M")


def _render(res: dict, *, show_fix: bool) -> None:
    if res.get("error"):
        print(f"❌ {res['error']}")
        return

    print(f"profile: {_PROFILE_NM}")
    print()
    for r in res["plugins"]:
        st = r["status"]
        icon = {"ok": "✅", "skip": "⏭ ", "missing_pkg": "❌", "stale": "❌"}[st]
        head = f"{icon} {r['dir']}  ({r.get('pkg', '?')})"
        if st == "ok":
            print(f"{head}  同步, 比对 {r['compared']} 个文件")
            continue
        if st == "skip":
            print(f"{head}  跳过: {r['detail']}")
            continue
        if st == "missing_pkg":
            print(f"{head}  未安装 -> {r['installed']}")
            print(f"     {r['detail']}")
            continue

        print(f"{head}  **不同步** (比对 {r['compared']} 个文件)")
        for s in r["stale"]:
            print(f"     STALE   {s['file']}   源码 {_ts(s['src_mtime'])} "
                  f"-> 已装 {_ts(s['inst_mtime'])}")
        for m in r["missing"]:
            print(f"     MISSING {m['file']}   {m['detail']}")
        for e in r["extra"]:
            print(f"     EXTRA   {e['file']}   {e['detail']}")
    print()

    if show_fix:
        # ⚠️ 包名从 package.json 读真源, 不要从目录名推导 ——
        #    `ikaros-memory` -> `@ikaros/dsh-ikaros-memory`, 目录名砍掉 `ikaros-`
        #    前缀会推成不存在的 `dsh-memory`。
        bad = [r for r in res["plugins"] if r["status"] in {"stale", "missing_pkg"}]
        if bad:
            print("修复 (每个插件都要走完整三步, pnpm file: 是复制, remove+add 才生效):")
            for r in bad:
                d = r["dir"]
                print(f"  cd {(_PLUGINS / d).as_posix()} && npm run build")
                print(f"  cd {_PROFILE_NM.parent.as_posix()} && "
                      f"pnpm remove {r['pkg']} && pnpm add file:{(_PLUGINS / d).as_posix()}")
            print()
            print("⚠️ 装完还要**重启 dsh** 才会加载新代码 (重启会中断 :3080 当前会话):")
            print("  powershell -File bin/restart-dsh-ikaros.ps1")
            print()

    print("✅ 插件已同步" if res["ok"] else "❌ 有插件不同步 —— dsh 跑的不是最新代码")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检查 dsh 装载的 Ikaros 插件是否与源码同步")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--fix-cmd", action="store_true", help="打印修复命令")
    args = ap.parse_args(argv)

    res = check()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        _render(res, show_fix=args.fix_cmd)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
