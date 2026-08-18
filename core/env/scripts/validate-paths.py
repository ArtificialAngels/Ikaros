#!/usr/bin/env python3
# 详细说明见 docs/scripts/core/env/scripts/validate-paths.md
# 2026-08-18: 移除 hermes/neko 检查, 新增 dsh; env 权威源改指 bin/ikaros-env.*
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


# ANSI colors
class C:
    GRN = "\033[32m"
    YEL = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"
    RST = "\033[0m"
    BLD = "\033[1m"


@dataclass
class PathCheck:
    """单个路径检查项。"""
    name: str
    path: str
    critical: bool = False      # 关键路径 (缺失则退出码 1)
    must_be_file: bool = False  # 必须是文件
    must_be_dir: bool = False   # 必须是目录
    description: str = ""


@dataclass
class CheckResult:
    """检查结果。"""
    check: PathCheck
    exists: bool
    is_file: bool = False
    is_dir: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        if not self.exists:
            return not self.check.critical
        if self.check.must_be_file and not self.is_file:
            return False
        if self.check.must_be_dir and not self.is_dir:
            return False
        return True

    @property
    def status_icon(self) -> str:
        if self.ok:
            return f"{C.GRN}OK{C.RST}"
        if self.check.critical:
            return f"{C.RED}FAIL{C.RST}"
        return f"{C.YEL}WARN{C.RST}"


def resolve_root() -> Optional[Path]:
    """解析 IKAROS_ROOT (只认 IKAROS_ROOT, HERMES_ROOT 兼容已废弃)。"""
    # 1. 环境变量
    env_root = os.environ.get("IKAROS_ROOT", "").strip()
    if env_root and Path(env_root).exists():
        return Path(env_root).resolve()

    # 2. 从脚本位置推导
    script_dir = Path(__file__).resolve().parent
    env_dir = script_dir.parent  # Ikaros-environment
    candidate = env_dir.parent   # Ikaros
    if (candidate / "runtime" / "portable-python" / "python.exe").exists():
        return candidate

    # 3. 从当前工作目录向上查找
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "runtime" / "portable-python" / "python.exe").exists() and \
           (parent / "core/env").exists():
            return parent

    return None


def load_paths_json(root: Path) -> dict:
    """加载 ikaros-paths.json。"""
    json_path = root / "core/env" / "ikaros-paths.json"
    if not json_path.exists():
        return {}
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{C.YEL}[WARN] 无法读取 ikaros-paths.json: {e}{C.RST}")
        return {}


def build_checks(root: Path, paths_cfg: dict) -> list[PathCheck]:
    """构建路径检查列表。"""
    checks = []

    # ---- 核心组件 ----
    checks.append(PathCheck(
        "Python 解释器",
        str(root / "runtime" / "portable-python" / "python.exe"),
        critical=True, must_be_file=True,
        description="嵌入式 Python"
    ))
    checks.append(PathCheck(
        "Node.js",
        str(root / "runtime" / "node" / "node.exe"),
        critical=True, must_be_file=True,
        description="嵌入式 Node.js"
    ))
    checks.append(PathCheck(
        "llama-server",
        str(root / "runtime" / "llama" / "b10000-cuda" / "llama-server.exe"),
        critical=True, must_be_file=True,
        description="本地 LLM 服务"
    ))

    # ---- 目录结构 ----
    for name, subdir in [
        ("数据目录", "data"),
        ("脚本目录", "bin"),
        ("配置目录", "config"),
        ("运行时目录", "runtime"),
        ("工作引擎目录", "core/ikaros-dsh"),
    ]:
        checks.append(PathCheck(
            name,
            str(root / subdir),
            critical=True, must_be_dir=True
        ))

    # ---- dsh (DeepSeek Harness) ----
    checks.append(PathCheck(
        "dsh 运行时",
        str(root / "runtime" / "dsh"),
        critical=True, must_be_dir=True,
        description="DeepSeek Harness 工作引擎"
    ))
    checks.append(PathCheck(
        "dsh overlay",
        str(root / "core" / "ikaros-dsh" / "cordis.patch.yml"),
        critical=True, must_be_file=True,
        description="Ikaros 组合 overlay"
    ))

    # ---- core/memory_v5 ----
    checks.append(PathCheck(
        "core/memory_v5 模块",
        str(root / "core/memory_v5"),
        critical=True, must_be_dir=True
    ))
    checks.append(PathCheck(
        "记忆数据库",
        str(root / "core/memory_v5" / "data" / "v5" / "v5.db"),
        critical=False, must_be_file=True
    ))
    checks.append(PathCheck(
        "Embedding 模型",
        str(root / "core/memory_v5" / "models" / "bge-m3-q8_0.gguf"),
        critical=True, must_be_file=True
    ))
    checks.append(PathCheck(
        "LLM 模型",
        str(root / "core/memory_v5" / "models" / "Phi-4-mini-instruct-Q4_K_M.gguf"),
        critical=True, must_be_file=True
    ))

    # ---- env 权威源 (bin/) ----
    checks.append(PathCheck(
        "环境配置 (bat)",
        str(root / "bin" / "ikaros-env.bat"),
        critical=True, must_be_file=True
    ))
    checks.append(PathCheck(
        "环境配置 (ps1)",
        str(root / "bin" / "ikaros-env.ps1"),
        critical=True, must_be_file=True
    ))
    checks.append(PathCheck(
        "环境配置 (sh)",
        str(root / "bin" / "ikaros-env.sh"),
        critical=True, must_be_file=True
    ))
    checks.append(PathCheck(
        "路径配置 (JSON)",
        str(root / "core/env" / "ikaros-paths.json"),
        critical=True, must_be_file=True
    ))

    return checks


def run_checks(checks: list[PathCheck]) -> list[CheckResult]:
    """执行所有检查。"""
    results = []
    for check in checks:
        p = Path(check.path)
        result = CheckResult(check=check, exists=p.exists())
        if result.exists:
            result.is_file = p.is_file()
            result.is_dir = p.is_dir()
            if check.must_be_file and not result.is_file:
                result.error = "应为文件但实际是目录"
            elif check.must_be_dir and not result.is_dir:
                result.error = "应为目录但实际是文件"
        results.append(result)
    return results


def print_report(results: list[CheckResult]) -> tuple[int, int]:
    """打印检查报告。返回 (pass_count, fail_count)。"""
    pass_count = sum(1 for r in results if r.ok)
    fail_count = sum(1 for r in results if not r.ok)

    print(f"\n{C.BLD}{'='*60}{C.RST}")
    print(f"{C.BLD}  Ikaros 路径验证报告{C.RST}")
    print(f"{C.BLD}{'='*60}{C.RST}\n")

    # 按类别分组
    categories = [
        ("核心组件", ["Python", "Node.js", "llama-server"]),
        ("目录结构", ["目录"]),
        ("dsh 工作引擎", ["dsh"]),
        ("core/memory_v5", ["memory", "Embedding", "LLM 模型", "记忆"]),
        ("env 权威源", ["环境配置", "路径配置"]),
    ]

    for cat_name, keywords in categories:
        cat_results = [r for r in results if any(k in r.check.name for k in keywords)]
        if not cat_results:
            continue
        print(f"{C.DIM}--- {cat_name} ---{C.RST}")
        for r in cat_results:
            desc = f" ({r.check.description})" if r.check.description else ""
            status = r.status_icon
            path_short = r.check.path.replace("E:\\Ikaros\\", "~\\")
            print(f"  {status} {r.check.name}{desc}")
            print(f"       {C.DIM}{path_short}{C.RST}")
            if r.error:
                print(f"       {C.RED}{r.error}{C.RST}")
        print()

    # 总结
    print(f"{C.BLD}{'='*60}{C.RST}")
    if fail_count == 0:
        print(f"{C.GRN}{C.BLD}  全部通过 ({pass_count}/{len(results)}){C.RST}")
    else:
        print(f"{C.RED}{C.BLD}  失败: {fail_count}{C.RST} / 通过: {pass_count} / 总计: {len(results)}")
    print(f"{C.BLD}{'='*60}{C.RST}\n")

    return pass_count, fail_count


def main() -> int:
    """主函数。"""
    json_output = "--json" in sys.argv

    # 解析根目录
    root = resolve_root()
    if root is None:
        if json_output:
            print(json.dumps({"error": "无法解析 IKAROS_ROOT"}, ensure_ascii=False))
        else:
            print(f"{C.RED}[FATAL] 无法解析 IKAROS_ROOT{C.RST}")
            print("请设置环境变量: set IKAROS_ROOT=E:\\Ikaros")
        return 2

    if not json_output:
        print(f"{C.DIM}IKAROS_ROOT: {root}{C.RST}")

    # 加载配置
    paths_cfg = load_paths_json(root)

    # 构建检查列表
    checks = build_checks(root, paths_cfg)

    # 执行检查
    results = run_checks(checks)

    # 输出
    if json_output:
        output = {
            "ikaros_root": str(root),
            "checks": [
                {
                    "name": r.check.name,
                    "path": r.check.path,
                    "exists": r.exists,
                    "ok": r.ok,
                    "critical": r.check.critical,
                    "error": r.error,
                }
                for r in results
            ],
            "summary": {
                "total": len(results),
                "pass": sum(1 for r in results if r.ok),
                "fail": sum(1 for r in results if not r.ok),
            }
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        pass_count, fail_count = print_report(results)

    # 退出码
    critical_fails = sum(1 for r in results if not r.ok and r.check.critical)
    return 1 if critical_fails > 0 else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
