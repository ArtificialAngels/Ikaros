#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes-update-and-patch.py
==========================

将 ``core/hermes`` 更新到新版 upstream 后，稳定地重新打上 Ikaros 定制补丁。

实现 ``docs/hermes-ikaros-patches.md`` §2「两步法」协议：

  ① 确定性：``git cherry-pick <当前 Ikaros 提交>`` —— 把补丁 diff 直接重放到新 upstream。
     干净通过 → 验证 → 完成（新 HEAD = 新 upstream + 补丁）。
     冲突 / 失败 → 视为「相关区域有大改」，升级 ②。

  ② LLM 兜底：把补丁意图（spec §5）+ allowlist（§3）+ 提示词模板（§7）写成提示词，
     交给受约束子 agent 在新代码上重实现，再验证、提交、更新 §0 基线指针。

安全约束（来自 spec §8 / AGENTS.md）：
  - 绝不裸跑 ``llama-server.exe``。
  - 绝不自动 push（本地 ``main`` 是 Ikaros 修补分支，push 会污染上游 ``origin/main``）。
  - 绝不碰 allowlist（§3）外的文件。
  - 更新前打备份 tag，失败可整体回滚；safe-delete 拦删的残留用同盘 ``mv`` 处理。
  - 半完成态极难收拾，脚本须能从任意中间态安全恢复或整体回滚。

用法
----
  # 计划模式（默认，只读）：报告将做什么，不改动任何东西
  python bin/hermes-update-and-patch.py

  # 执行确定性打补丁（cherry-pick 路径）
  python bin/hermes-update-and-patch.py --apply

  # 冲突时尝试自动派 LLM（未配置派发命令时，仅写提示词并暂停）
  python bin/hermes-update-and-patch.py --apply --auto-llm

  # LLM（人工或 agent）改完后，验证 + 提交 + 更新 §0 指针
  python bin/hermes-update-and-patch.py --finalize

状态在两次调用之间用 ``tmp/hermes-patch-state.json`` 传递（target / 旧 Ikaros 提交 / 备份 tag）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# 路径解析（相对本脚本：bin/ 的父目录即 Ikaros 仓库根）
# --------------------------------------------------------------------------- #
SCRIPT = Path(__file__).resolve()
IKAROS_ROOT = SCRIPT.parent.parent                      # E:/Ikaros
REPO = IKAROS_ROOT / "core" / "hermes"                  # hermes 子仓库
SPEC = IKAROS_ROOT / "docs" / "hermes-ikaros-patches.md"
STATE_FILE = IKAROS_ROOT / "tmp" / "hermes-patch-state.json"
BACKUP_PREFIX = "ikaros-hermes-backup"
ALLOWED_UNTracked = {"config.yaml"}                     # 本地运行配置，允许存在、不碰

# allowlist（硬约束，镜像 spec §3）
ALLOWLIST_FILES = [
    "cron/scheduler.py",
    "hermes_cli/web_server.py",
    "plugins/context_engine/__init__.py",
    "scripts/run_tests.sh",
    "scripts/run_tests_parallel.py",
    "tests/cron/test_scheduler.py",
]
ALLOWLIST_DIRS = [
    "plugins/context_engine/ikaros_v5",
    "plugins/memory/ikaros_v5",
    "skills/creative/tldraw-skill",
]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[hermes-patch] {msg}", flush=True)


def run_git(args, check: bool = True, cwd: Path = REPO):
    return run_cmd(["git", *args], check=check, cwd=cwd)


def run_cmd(args, check: bool = True, cwd: Path = REPO):
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout: {proc.stdout}\n"
            f"stderr: {proc.stderr}"
        )
    return proc


def detect_python() -> Path:
    """优先用 hermes 自带 venv（Windows Scripts/python.exe），否则回退当前解释器。"""
    venv_py = REPO / "venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    venv_py_sh = REPO / "venv" / "bin" / "python"
    if venv_py_sh.exists():
        return venv_py_sh
    return Path(sys.executable)


# --------------------------------------------------------------------------- #
# spec §0 指针读写
# --------------------------------------------------------------------------- #
def parse_spec_pointers(spec_text: str) -> tuple[str, str]:
    """从 spec §0 解析 (upstream_tip, ikaros_commit)。"""
    up = re.search(r"\*\*Upstream tip\*\*[^\n]*?`([0-9a-f]{6,40})`", spec_text)
    ik = re.search(r"\*\*Ikaros 补丁提交\*\*[^\n]*?`([0-9a-f]{6,40})`", spec_text)
    if not up or not ik:
        raise RuntimeError("无法从 spec §0 解析基线指针，请检查 docs/hermes-ikaros-patches.md")
    return up.group(1), ik.group(1)


def update_spec_pointers(spec_path: Path, new_upstream: str, new_ikaros: str) -> None:
    text = spec_path.read_text(encoding="utf-8")
    text, n1 = re.subn(
        r"(\*\*Upstream tip\*\*[^\n]*?`)[0-9a-f]{6,40}(`)",
        lambda m: f"{m.group(1)}{new_upstream}{m.group(2)}",
        text, count=1,
    )
    text, n2 = re.subn(
        r"(\*\*Ikaros 补丁提交\*\*[^\n]*?`)[0-9a-f]{6,40}(`)",
        lambda m: f"{m.group(1)}{new_ikaros}{m.group(2)}",
        text, count=1,
    )
    if n1 != 1 or n2 != 1:
        raise RuntimeError("更新 spec §0 指针失败（替换次数异常）")
    spec_path.write_text(text, encoding="utf-8")
    log(f"已更新 spec §0：upstream={new_upstream[:8]}  ikaros={new_ikaros[:8]}")


def extract_llm_template(spec_text: str) -> str:
    """提取 spec §7 的提示词模板（``` 代码块）。"""
    m = re.search(r"##\s*7\..*?```\n(.*?)```", spec_text, re.S)
    if not m:
        raise RuntimeError("无法从 spec §7 提取 LLM 提示词模板")
    return m.group(1)


# --------------------------------------------------------------------------- #
# 状态持久化（跨调用）
# --------------------------------------------------------------------------- #
def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_state() -> dict:
    if not STATE_FILE.exists():
        raise RuntimeError(f"找不到状态文件 {STATE_FILE}，请先用 --apply 启动一次流程")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# --------------------------------------------------------------------------- #
# 前置检查
# --------------------------------------------------------------------------- #
def ensure_clean_working_tree() -> None:
    """工作树应仅有允许的未跟踪文件（config.yaml）。"""
    proc = run_git(["status", "--porcelain"], check=True)
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    bad = []
    for l in lines:
        code = l[:2]
        path = l[3:].strip()
        if code == "??" and path in ALLOWED_UNTracked:
            continue
        bad.append(l)
    if bad:
        raise RuntimeError(
            "工作树不干净，存在非预期改动，已中止：\n  " + "\n  ".join(bad)
        )


def backup_tag() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = f"{BACKUP_PREFIX}-{ts}"
    run_git(["tag", tag, "HEAD"], check=True)
    log(f"已打备份 tag：{tag}（指向当前 HEAD，便于回滚）")
    return tag


def rollback_to_backup(tag: str) -> None:
    log(f"回滚到备份 tag {tag} ...")
    run_git(["cherry-pick", "--abort"], check=False)
    run_git(["checkout", "-B", "main", tag], check=True)
    run_git(["reset", "--hard", tag], check=True)
    log("已回滚。")


# --------------------------------------------------------------------------- #
# 验证（spec §4，两步都必须过）
# --------------------------------------------------------------------------- #
def verify(python_exe: Path) -> tuple[bool, list[str]]:
    report: list[str] = []
    ok = True

    def check(name: str, passed: bool, detail: str = ""):
        nonlocal ok
        mark = "PASS" if passed else "FAIL"
        report.append(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
        if not passed:
            ok = False

    # 1) 工作树干净（允许 config.yaml）
    try:
        ensure_clean_working_tree()
        check("工作树干净（允许 config.yaml）", True)
    except RuntimeError as e:
        check("工作树干净", False, str(e))

    # 2) compileall（依赖轻，优先跑）
    proc = run_cmd(
        [str(python_exe), "-m", "compileall", "-q",
         "cron", "hermes_cli", "plugins", "scripts", "tests"],
        check=False,
    )
    check("compileall 全过", proc.returncode == 0,
          "" if proc.returncode == 0 else proc.stderr[-300:])

    # 3) list_context_engine_names() 能发现 ikaros_v5（目录扫描，不 import 引擎）
    probe = ("from plugins.context_engine import list_context_engine_names; "
             "assert 'ikaros_v5' in list_context_engine_names()")
    proc = run_cmd([str(python_exe), "-c", probe], check=False)
    check("发现 ikaros_v5 引擎", proc.returncode == 0,
          "" if proc.returncode == 0 else proc.stderr[-300:])

    # 4) scheduler 的 _cron_session_id 为固定 f"cron_{job_id}"
    sched = (REPO / "cron" / "scheduler.py")
    if sched.exists():
        src = sched.read_text(encoding="utf-8", errors="ignore")
        has_fixed = 'f"cron_{job_id}"' in src or "f'cron_{job_id}'" in src
        check("scheduler 固定 session_id", has_fixed)
    else:
        check("scheduler 文件存在", False, "cron/scheduler.py 缺失")

    # 5) run_tests.sh 同时探测 bin/activate 与 Scripts/activate
    rts = (REPO / "scripts" / "run_tests.sh")
    if rts.exists():
        src = rts.read_text(encoding="utf-8", errors="ignore")
        check("run_tests.sh 双 venv 探测",
              ("bin/activate" in src) and ("Scripts/activate" in src))
    else:
        check("run_tests.sh 存在", False, "scripts/run_tests.sh 缺失")

    # 6) （加分项）import hermes_cli.web_server —— 依赖缺失时仅 WARN，不算 FAIL
    proc = run_cmd([str(python_exe), "-c",
                    "import hermes_cli.web_server, plugins.context_engine"],
                   check=False)
    if proc.returncode == 0:
        report.append("  [PASS] import hermes_cli.web_server, plugins.context_engine")
    else:
        report.append("  [WARN] import 失败（多为 venv 依赖未装，非补丁问题）："
                      + proc.stderr[-200:])

    # 7) 行尾：提交后无意外 EOL 翻转（informational，不阻断）
    report.append("  [INFO] EOL 检查：hermes .gitattributes 对 *.sh 强制 LF；"
                  "新增文件请保持与周围一致。")

    return ok, report


# --------------------------------------------------------------------------- #
# LLM 兜底：生成提示词 + 可选派发
# --------------------------------------------------------------------------- #
def build_llm_prompt(spec_text: str, target_sha: str) -> str:
    tpl = extract_llm_template(spec_text)
    # 填 §0 的 upstream 占位符
    prompt = tpl.replace("<填 §0 的 upstream 提交>", target_sha)
    prompt += f"\n\n（本次目标 upstream 提交：{target_sha}；"
    prompt += f"参考 spec：{SPEC}；允许改动范围见上）\n"
    return prompt


def write_llm_prompt(spec_text: str, target_sha: str) -> Path:
    prompt = build_llm_prompt(spec_text, target_sha)
    out = IKAROS_ROOT / "tmp" / f"hermes-llm-patch-{datetime.now():%Y%m%d-%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    log(f"已写出 LLM 提示词：{out}")
    return out


def dispatch_llm(prompt_file: Path) -> bool:
    """若配置了派发命令（HERMES_PATCH_LLM_CMD），则调用；否则返回 False（需人工）。"""
    cmd_tpl = os.environ.get("HERMES_PATCH_LLM_CMD")
    if not cmd_tpl:
        return False
    # 简单把 {prompt} 替换为文件路径
    args = cmd_tpl.replace("{prompt}", str(prompt_file)).split()
    log(f"派发 LLM：{' '.join(args)}")
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode == 0


# --------------------------------------------------------------------------- #
# 收尾：验证 + 提交（兜底时）+ 更新 §0
# --------------------------------------------------------------------------- #
def finalize(target_sha: str, already_committed: bool, python_exe: Path) -> bool:
    log("运行验证（spec §4）...")
    ok, report = verify(python_exe)
    for line in report:
        print(line)
    if not ok:
        log("验证未通过，终止收尾（未提交）。请修复后重试或回滚。")
        return False

    if not already_committed:
        # LLM 兜底分支：把 allowlist 改动提交为新 Ikaros 单提交
        paths = [p for p in ALLOWLIST_FILES if (REPO / p).exists()]
        paths += ALLOWLIST_DIRS
        run_git(["add", "--", *paths], check=True)
        run_git(["commit", "-m",
                 f"feat(hermes): apply Ikaros integration patches on upstream {target_sha[:8]}"],
                check=True)
        log("已提交新的 Ikaros 单提交。")

    new_commit = run_git(["rev-parse", "HEAD"], check=True).stdout.strip()
    update_spec_pointers(SPEC, target_sha, new_commit)
    clear_state()
    log(f"完成。新 Ikaros 提交 = {new_commit[:8]}（基于 upstream {target_sha[:8]}）")
    log("注意：未自动 push（本地 main 是 Ikaros 修补分支，push 会污染 origin/main）。")
    return True


# --------------------------------------------------------------------------- #
# 确定性路径
# --------------------------------------------------------------------------- #
def step_deterministic(target: str, ikaros_old: str, backup_tag_name: str,
                       apply: bool, python_exe: Path, auto_llm: bool,
                       spec_text: str) -> int:
    if not apply:
        log(f"[计划] 将 fetch 并 checkout {target}，然后 cherry-pick {ikaros_old[:8]}。")
        log(f"[计划] 冲突时{'自动派 LLM' if auto_llm else '写出提示词并暂停'}。")
        return 0

    # 已包含补丁？
    anc = run_git(["merge-base", "--is-ancestor", ikaros_old, "HEAD"],
                  check=False)
    if anc.returncode == 0:
        log(f"HEAD 已包含 Ikaros 提交 {ikaros_old[:8]}，无需重打。")
        return 0

    target_sha = run_git(["rev-parse", "--verify", target], check=True).stdout.strip()
    # 已确认需要重打，此时才打备份 tag（避免 no-op 时产生多余 tag）
    backup_tag_name = backup_tag()
    save_state({"target": target_sha, "ikaros_old": ikaros_old,
                "backup_tag": backup_tag_name, "mode": "deterministic"})

    run_git(["checkout", "-B", "main"], check=True)
    run_git(["reset", "--hard", target_sha], check=True)
    log(f"已 reset main → {target_sha[:8]}（新 upstream，旧补丁由备份 tag 保留）")

    proc = run_git(["cherry-pick", ikaros_old], check=False)
    if proc.returncode == 0:
        log("cherry-pick 干净通过 → 确定性打补丁成功。")
        finalize(target_sha, already_committed=True, python_exe=python_exe)
        return 0

    # 冲突 → 升级 LLM 兜底
    log("cherry-pick 冲突 → 升级 LLM 兜底（§2 第②步）。")
    run_git(["cherry-pick", "--abort"], check=False)
    # 恢复 B 类静态目录（abort 后它们不在树里）
    run_git(["checkout", backup_tag_name, "--", *ALLOWLIST_DIRS], check=True)
    log("已恢复 B 类静态插件目录。")

    prompt_file = write_llm_prompt(spec_text, target_sha)
    save_state({"target": target_sha, "ikaros_old": ikaros_old,
                "backup_tag": backup_tag_name, "mode": "need-llm",
                "prompt_file": str(prompt_file)})

    if auto_llm and dispatch_llm(prompt_file):
        log("LLM 派发完成，进入收尾验证。")
        if finalize(target_sha, already_committed=False, python_exe=python_exe):
            return 0
        return 1

    log("=== 需要人工 / agent 步骤 ===")
    log(f"提示词已写入：{prompt_file}")
    log("请让受约束子 agent 按提示词在 core/hermes 上重实现补丁（仅改 allowlist），")
    log("改完后运行：python bin/hermes-update-and-patch.py --finalize")
    return 2


def step_finalize(python_exe: Path) -> int:
    state = load_state()
    if state.get("mode") != "need-llm":
        log("状态显示并非处于 LLM 兜底待收尾阶段；若确需收尾请确认流程。")
        # 仍允许：直接用当前 HEAD 父作为 target 猜测
    target_sha = state["target"]
    backup_tag_name = state["backup_tag"]

    # 确保 B 类目录存在（兜底保险）
    run_git(["checkout", backup_tag_name, "--", *ALLOWLIST_DIRS], check=False)

    if finalize(target_sha, already_committed=False, python_exe=python_exe):
        return 0
    return 1


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes × Ikaros 补丁稳定重打工具")
    ap.add_argument("--apply", action="store_true",
                    help="执行实际 git 操作（默认仅计划模式，只读）")
    ap.add_argument("--target", default="origin/main",
                    help="要更新到的 upstream 提交/分支/标签（默认 origin/main）")
    ap.add_argument("--auto-llm", action="store_true",
                    help="冲突时尝试自动派 LLM（需 HERMES_PATCH_LLM_CMD 环境变量）")
    ap.add_argument("--finalize", action="store_true",
                    help="LLM 改完后：验证 + 提交 + 更新 §0 指针")
    ap.add_argument("--python", default=None,
                    help="验证用的 python 解释器（默认自动探测 hermes venv）")
    args = ap.parse_args()

    if not REPO.exists():
        log(f"找不到 hermes 仓库：{REPO}")
        return 1
    if not SPEC.exists():
        log(f"找不到 spec：{SPEC}")
        return 1

    spec_text = SPEC.read_text(encoding="utf-8")
    upstream_tip, ikaros_commit = parse_spec_pointers(spec_text)
    python_exe = Path(args.python) if args.python else detect_python()

    log(f"spec §0：upstream={upstream_tip[:8]}  ikaros={ikaros_commit[:8]}")
    log(f"验证用 python：{python_exe}")

    if args.finalize:
        return step_finalize(python_exe)

    if not args.apply:
        ensure_clean_working_tree()
        log("=== 计划模式（未加 --apply，不改动）===")
        return step_deterministic(args.target, ikaros_commit, "",
                                  apply=False, python_exe=python_exe,
                                  auto_llm=args.auto_llm, spec_text=spec_text)

    ensure_clean_working_tree()
    return step_deterministic(args.target, ikaros_commit, "",
                              apply=True, python_exe=python_exe,
                              auto_llm=args.auto_llm, spec_text=spec_text)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        log(f"错误：{e}")
        sys.exit(1)
