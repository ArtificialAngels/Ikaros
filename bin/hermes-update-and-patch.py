#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes-update-and-patch.py
==========================

将 ``core/hermes`` 更新到新版 upstream 后，稳定地重新打上 Ikaros 定制补丁。

实现 ``docs/hermes-ikaros-patches.md`` §2「两步法」协议：

  ① 确定性（3-way 重放）：以 §0 的 ``Upstream tip`` 为基准、``Ikaros 补丁提交`` 为目标，
     生成 ``base -> ikaros`` 的 A 类 diff，用 ``git apply --3way`` 重放到新 upstream。
     ``--3way`` 用 base 的 blob 作共同祖先，自动并入 upstream 自身改动，只把冲突
     聚焦到 Ikaros 与 upstream 改了同一行的地方 → 上游推进时冲突面最小、最稳。
     干净通过 → marker 校验 → 验证 → 提交 → 更新 §0（新 base = 新 upstream）。
     冲突 / marker 缺失 → 视为「相关区域有大改」，升级 ②。

  ② LLM 兜底：把补丁意图（spec §5）+ allowlist（§3）+ 提示词模板（§7）+ 冲突文件
     写成提示词，交给受约束子 agent 在新代码上重实现，再验证、提交、更新 §0 基线指针。

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
# 补丁源文件目录（被 Ikaros 主仓库 git 跟踪，不依赖 core/hermes 的 git 状态）。
# hermes reset --hard / git clean 不会影响这里；仓库损坏重建后也能直接恢复。
PATCH_SOURCE_DIR = IKAROS_ROOT / "patches" / "hermes"
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
# A / B 类补丁定义 + 3-way 重放核心
# --------------------------------------------------------------------------- #
# A 类：需随 upstream 重打的 tracked 文件（以 upstream 为基准做 3-way 重放）。
#   注意：web_server.py / test_scheduler.py 等改动量较大（并非 3 行小补丁），
#   因此必须用 3-way（git apply --3way）而非整文件覆盖——这样 upstream 推进时
#   能自动并入 upstream 自身改动，只把冲突聚焦到真正重叠的行。
A_CLASS_FILES = [
    "plugins/context_engine/__init__.py",
    "hermes_cli/web_server.py",
    "cron/scheduler.py",
    "scripts/run_tests.sh",
    "scripts/run_tests_parallel.py",
    "tests/cron/test_scheduler.py",
    "agent/conversation_loop.py",
    "gateway/platforms/api_server.py",
]
# B 类：Ikaros 自有静态目录（原样复制；upstream 不碰，缺失即补、存在即跳过）。
B_CLASS_DIRS = [
    "plugins/context_engine/ikaros_v5",
    "plugins/memory/ikaros_v5",
    "skills/creative/tldraw-skill",
]
B_CLASS_REPR = {
    "plugins/context_engine/ikaros_v5": "plugins/context_engine/ikaros_v5/__init__.py",
    "plugins/memory/ikaros_v5": "plugins/memory/ikaros_v5/__init__.py",
    "skills/creative/tldraw-skill": "skills/creative/tldraw-skill/SKILL.md",
}
B_CLASS_MARKERS = {
    "plugins/context_engine/ikaros_v5": "class IkarosV5ContextEngine",
    "plugins/memory/ikaros_v5": "class IkarosV5MemoryProvider",
    "skills/creative/tldraw-skill": "tldraw",
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print(f"[hermes-patch] {msg}", flush=True)


def run_git(args, check: bool = True, cwd: Path = REPO, input_data: str | None = None):
    return run_cmd(["git", *args], check=check, cwd=cwd, input_data=input_data)


def run_cmd(args, check: bool = True, cwd: Path = REPO, env: dict | None = None,
            input_data: str | None = None):
    # 显式 utf-8 解码：本机 Windows locale 为 gbk，git 输出（含中文 / marker 签名行）
    # 是 UTF-8，text=True 不指定 encoding 会用 gbk 解码 → 后台读线程抛 UnicodeDecodeError，
    # 进而让 proc.stdout 变为 None、解析崩溃。errors="replace" 保证永不因个别字节中断。
    proc = subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, input=input_data,
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


def fetch_upstream(remote: str = "origin") -> None:
    """拉取 upstream 最新提交，使后续 reset --hard origin/main 指向真正的上游 HEAD。

    支持 ``IKAROS_GIT_MIRROR`` 镜像前缀（与 dashboard 的 ``_mirror_url`` 一致），
    避免直连 GitHub 在弱网环境下长时间挂起（这正是「更新」按钮卡死的根因）。

    - 设置了镜像：读 ``origin`` 真实 URL 拼镜像前缀，直接 fetch 并把结果写入
      ``refs/remotes/origin/main`` 远程跟踪引用（不修改本地 remote 配置）。
    - 未设置镜像：退回 ``git fetch origin``（弱网下可能很慢，由调用方超时控制）。
    """
    mirror = (os.environ.get("IKAROS_GIT_MIRROR") or "").rstrip("/")
    fetch_url = None
    if mirror:
        try:
            base = run_git(["remote", "get-url", remote], check=True).stdout.strip()
            fetch_url = f"{mirror}/{base}"
        except Exception:
            fetch_url = None
    log(f"拉取 upstream（{'镜像 ' + fetch_url if fetch_url else remote}）...")
    try:
        if fetch_url:
            # 直接拉镜像 URL，并把远程 main 写入本地 origin/main 远程跟踪引用
            run_git(["fetch", fetch_url, "main:refs/remotes/origin/main"], check=True)
        else:
            run_git(["fetch", remote], check=True)
    except RuntimeError as e:
        # 网络不可达 / 镜像不可用时**不致命**：后续 reset 用的是本地已缓存的
        # origin/main（上次成功 fetch 的快照）。只要 upstream 未推进，离线重放
        # 完全正确；若 upstream 真有新提交而 fetch 失败，则会在 3-way 重放时因
        # base 漂移而冲突，届时自然升级 LLM 兜底。故此处仅告警继续。
        log(f"[warn] fetch upstream 失败（网络/镜像不可达），将使用本地缓存的 "
            f"origin/main 继续：{e}")


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


# --------------------------------------------------------------------------- #
# 3-way 重放核心
# --------------------------------------------------------------------------- #
def normalize_lf(text: str) -> str:
    """归一换行符为 LF（hermes 仓库 .py 以 LF 存储，避免 CRLF 造成脏树 / 假 diff）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def patch_present_via_grep() -> bool:
    """Ikaros 集成补丁是否已就位：grep HEAD 历史里的补丁提交（覆盖 原提交 /
    分层 cherry-pick / 重打新提交 三种来源，比 is-ancestor 更稳）。"""
    out = run_git(["log", "--oneline", "--grep",
                   "apply Ikaros integration patches", "HEAD"],
                  check=False).stdout
    return bool(out.strip())


def derive_markers(base: str, ikaros: str) -> dict:
    """从 base->ikaros 的 diff 自动抽取每个 A 类文件的「签名行」作为 marker。

    这些签名行是 Ikaros 修改引入、upstream 不会删除的内容；校验时只要它们
    存在于工作树文件，即可证明该文件补丁已落地（与 commit 哈希解耦，稳定）。
    """
    markers: dict = {}
    for f in A_CLASS_FILES:
        proc = run_git(["diff", base, ikaros, "--", f], check=True)
        sig: list[str] = []
        for line in proc.stdout.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            s = line[1:].rstrip()
            if not s.strip():
                continue
            if any(k in s for k in ("def ", "class ", "import ", "f\"", "f'",
                                    "= \"", "= '", "merge(")):
                sig.append(s)
            if len(sig) >= 2:
                break
        if not sig:
            for line in proc.stdout.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    s = line[1:].rstrip()
                    if s.strip():
                        sig = [s]
                        break
        markers[f] = sig
    for d in B_CLASS_DIRS:
        markers[d] = [B_CLASS_MARKERS[d]]
    return markers


def markers_missing(markers: dict) -> list:
    """返回缺失的 marker 描述（空列表 = 全部就位）。"""
    missing: list = []
    for f, ms in markers.items():
        if f in B_CLASS_REPR:
            repr_path = REPO / B_CLASS_REPR[f]
            if not repr_path.exists():
                missing.append(f"{f} (目录/代表文件缺失)")
                continue
            txt = normalize_lf(repr_path.read_text(encoding="utf-8", errors="ignore"))
            if not any(m in txt for m in ms):
                missing.append(f"{f} (marker 缺失: {ms})")
        else:
            p = REPO / f
            if not p.exists():
                missing.append(f"{f} (文件缺失)")
                continue
            txt = normalize_lf(p.read_text(encoding="utf-8", errors="ignore"))
            if not any(m in txt for m in ms):
                missing.append(f"{f} (marker 缺失: {ms})")
    return missing


def _copytree_lf(src: Path, dst: Path) -> None:
    """复制目录并归一为 LF（防止 patches/hermes/ 的 CRLF 文件污染 hermes 工作树）。"""
    import shutil
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            data = normalize_lf(item.read_text(encoding="utf-8", errors="ignore"))
            target.write_text(data, encoding="utf-8", newline="\n")


def ensure_b_class(d: str) -> None:
    """B 类目录：代表文件存在且含 marker 即跳过；否则从 patches/hermes/ 复制（LF）。"""
    repr_path = REPO / B_CLASS_REPR[d]
    need = True
    if repr_path.exists():
        txt = normalize_lf(repr_path.read_text(encoding="utf-8", errors="ignore"))
        if B_CLASS_MARKERS[d] in txt:
            need = False
    if not need:
        return
    src = PATCH_SOURCE_DIR / d
    if not src.exists():
        log(f"  [warn] B 类源缺失，跳过 {d}")
        return
    _copytree_lf(src, REPO / d)
    log(f"  补 B 类目录：{d}/")


def scan_conflicts() -> list:
    """扫描 A 类文件与 B 类代表文件中的 git 冲突标记（<<<<<<<）。"""
    conflicts: list = []
    candidates = list(A_CLASS_FILES) + [B_CLASS_REPR[d] for d in B_CLASS_DIRS]
    for f in candidates:
        p = REPO / f
        if not p.exists():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^<<<<<<< ", txt, re.M):
            conflicts.append(f)
    return conflicts


def apply_patch_delta(base: str, ikaros: str) -> list:
    """3-way 重放 Ikaros delta (base->ikaros) 到当前工作树（= 当前 upstream）。

    返回冲突文件列表（空 = 干净应用）。A 类**逐文件**走 ``git apply --3way``
    （不合并成单 patch——单 patch 是原子的，任一文件失败会连累其它文件全部不落地）；
    B 类按需复制。``--3way`` 用 base 的 blob 作为共同祖先，自动并入 upstream
    自身改动，只在 Ikaros 与 upstream 改了同一行时才留下冲突标记 → 冲突面最小、最稳。

    注意：patch 必须以**字节**喂给 git apply（二进制 stdin），否则 Windows 上
    text 模式会把 \\n 翻成 \\r\\n，破坏 unified diff 的 context 行导致匹配失败。
    """
    failed: list = []
    for f in A_CLASS_FILES:
        proc = run_git(["diff", base, ikaros, "--", f], check=True)
        patch = proc.stdout
        if not patch.strip():
            continue
        # 字节 stdin：避免 Windows text 模式换行翻译破坏 diff
        p = subprocess.run(
            ["git", "apply", "--3way", "--whitespace=nowarn", "-"],
            cwd=str(REPO), input=patch.encode("utf-8"),
            capture_output=True,
        )
        if p.returncode != 0:
            log(f"  [warn] git apply --3way 失败于 {f}（rc={p.returncode}）；"
                f"stderr={p.stderr.decode('utf-8','replace').strip()[:200]}")
            failed.append(f)
    for d in B_CLASS_DIRS:
        ensure_b_class(d)
    # 真正留下冲突标记的（极少），优先上报；其余靠 markers_missing 兜底
    conflicts = scan_conflicts()
    if failed and not conflicts:
        log(f"  [warn] 以下文件 3-way 未落地（将触发 LLM 兜底）：{failed}")
    return conflicts


def _sync_tree_from_commit(commit: str, rel_dir: str) -> None:
    # 注意：git ls-tree <commit>:<dir> 返回的是**相对于该目录**的路径
    # （如 `__init__.py`），必须拼回 rel_dir 才是仓库根相对全路径，否则
    # `git show <commit>:__init__.py` 会找不到文件。
    out = run_git(["ls-tree", "-r", "--name-only", f"{commit}:{rel_dir}"],
                  check=True).stdout
    for sub in out.splitlines():
        sub = sub.strip()
        if not sub:
            continue
        full = f"{rel_dir}/{sub}"
        blob = run_git(["show", f"{commit}:{full}"], check=True).stdout
        dst = PATCH_SOURCE_DIR / full
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(normalize_lf(blob), encoding="utf-8", newline="\n")


def refresh_patch_source(new_commit: str) -> None:
    """把新 Ikaros 提交里的 A 类文件与 B 类目录同步回 patches/hermes/（LF 归一），
    保持源文件与提交一致 —— 这是后续兜底与人工审阅的事实源，也确保下次 delta
    以最新 upstream 为基准（最小化漂移 → 最大化 3-way 成功率）。"""
    log("刷新 patches/hermes/ 源文件（与新提交保持一致）...")
    for f in A_CLASS_FILES:
        txt = run_git(["show", f"{new_commit}:{f}"], check=True).stdout
        dst = PATCH_SOURCE_DIR / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(normalize_lf(txt), encoding="utf-8", newline="\n")
    for d in B_CLASS_DIRS:
        _sync_tree_from_commit(new_commit, d)
    log("patches/hermes/ 已刷新。")


def rollback_to_backup(tag: str) -> None:
    log(f"回滚到备份 tag {tag} ...")
    run_git(["cherry-pick", "--abort"], check=False)
    # 丢弃 apply 阶段已应用的 A 类改动（相对当前 HEAD）
    run_git(["checkout", "--", "."], check=False)
    # 删除 apply 阶段复制进来、尚未提交的未跟踪 B 类目录，
    # 否则后续 checkout -B main <tag> 会因“覆盖未跟踪文件”而中止。
    run_git(["clean", "-fd", "--", *B_CLASS_DIRS], check=False)
    run_git(["checkout", "-B", "main", tag], check=True)
    run_git(["reset", "--hard", tag], check=True)
    log("已回滚。")


# --------------------------------------------------------------------------- #
# 验证加强（spec §4 增补）：ikaros_v5 记忆提供方是否真的能在 hermes 内运行
# --------------------------------------------------------------------------- #
# 子进程内执行的检查：复刻 Hermes Dashboard 的就绪判定（discover + is_available），
# 并进一步实际 initialize() 载入 V5——这正是「打补丁后是否真能跑」的硬证据，
# 能直接拦住本次出现的 'Memory provider ikaros_v5 is not ready (unavailable)' 类 400。
_IKAROS_V5_RUNTIME_CHECK = r'''
import os, sys

root = os.environ.get("IKAROS_ROOT")
if not root:
    print("FAIL: IKAROS_ROOT 未设置")
    sys.exit(1)
# 确保可被 import：core/hermes(plugins.*) 与 core(memory_v5 包)
sys.path.insert(0, os.path.join(root, "core"))
for p in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    if p and p not in sys.path:
        sys.path.insert(0, p)

errors = []
def check(cond, msg):
    if not cond:
        errors.append(msg)

# 1) 记忆提供方被发现且 available
from plugins.memory import discover_memory_providers
disc = {n: (d, avail) for n, d, avail in discover_memory_providers()}
check("ikaros_v5" in disc, "ikaros_v5 未被 discover_memory_providers 发现: %r" % sorted(disc))
if "ikaros_v5" in disc:
    check(disc["ikaros_v5"][1] is True,
          "ikaros_v5 被发现但 available=False（Hermes 会报 unavailable）")

# 2) 直接实例化并 is_available()
from plugins.memory.ikaros_v5 import IkarosV5MemoryProvider
prov = IkarosV5MemoryProvider()
check(prov.is_available(),
      "IkarosV5MemoryProvider.is_available()=False, import_error=%r" % (prov._import_error,))

# 3) 真正载入 V5（这才是「能在 hermes 里运行」的硬证据）
try:
    prov.initialize("patch-verify")
    check(prov._v5_loaded,
          "initialize() 未真正载入 V5: import_error=%r" % (prov._import_error,))
except Exception as e:
    errors.append("initialize() 抛异常: %r" % (e,))

# 4) context engine 同样可用
from plugins.context_engine import list_context_engine_names
check("ikaros_v5" in list_context_engine_names(),
      "ikaros_v5 未出现在 context engine 列表")
from plugins.context_engine.ikaros_v5 import IkarosV5ContextEngine
check(IkarosV5ContextEngine().is_available(),
      "context engine ikaros_v5 is_available()=False")

if errors:
    print("FAIL")
    for e in errors:
        print(" -", e)
    sys.exit(1)
print("OK: ikaros_v5 在 hermes 内可用且已成功载入 V5")
'''


def verify_ikaros_v5_runtime(python_exe: Path) -> tuple[bool, str]:
    """ikaros_v5 记忆提供方是否真的能在 hermes 子进程内运行（硬验证）。"""
    env = dict(os.environ)
    env["IKAROS_ROOT"] = str(IKAROS_ROOT)
    env["PYTHONPATH"] = os.pathsep.join([
        str(REPO),                   # core/hermes -> plugins.*
        str(IKAROS_ROOT / "core"),   # memory_v5 包
    ])
    proc = run_cmd([str(python_exe), "-c", _IKAROS_V5_RUNTIME_CHECK],
                   check=False, env=env)
    if proc.returncode == 0:
        last = [l for l in proc.stdout.splitlines() if l.strip()][-1:]
        return True, (last[0] if last else "OK")
    detail = (proc.stderr.strip()[-600:] or proc.stdout.strip()[-600:] or "(无输出)")
    return False, detail


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

    # 1) 落地判据 marker（A 类签名行 + B 类目录 marker 全部命中 = 补丁真的打上了）。
    #    注意：应用补丁后工作树必然有改动（A 类 M + B 类未跟踪），不能用“工作树干净”
    #    判定；正确的“完成定义”是 marker 全部命中（与 derive_markers / markers_missing 一致）。
    base, ikaros = parse_spec_pointers(SPEC.read_text(encoding="utf-8"))
    markers = derive_markers(base, ikaros)
    miss = markers_missing(markers)
    if not miss:
        check("落地判据 marker 全部命中", True)
    else:
        check("落地判据 marker 全部命中", False,
              "缺失: " + "; ".join(miss))

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

    # 3b) ikaros_v5 记忆提供方真的能在 hermes 内运行（发现 + 可用 + 实际载入 V5）。
    #     这是本次补丁脚本最关键的健壮性缺口：只检查 context_engine 目录存在，
    #     却没验证 memory provider 在 hermes 里 available，导致 upstream 改动把
    #     _resolve_root()/包布局打坏后仍能“验证通过”，上线即 400 unavailable。
    rt_ok, rt_detail = verify_ikaros_v5_runtime(python_exe)
    check("ikaros_v5 记忆提供方在 hermes 内可用且可载入 V5", rt_ok, rt_detail)

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


def write_llm_prompt(spec_text: str, target_sha: str, problems: list | None = None,
                     problem_kind: str = "需修复项") -> Path:
    """写出 LLM / 人工 兜底提示词，并**动态注入落地判据（marker）**，
    让任何接手修复的智能体都有自包含的"定义完成"清单，无需读源码即可核对。"""
    prompt = build_llm_prompt(spec_text, target_sha)
    base, ikaros = parse_spec_pointers(spec_text)
    markers = derive_markers(base, ikaros)
    miss = markers_missing(markers)
    # 本次需优先处理的清单（3-way 冲突文件 或 marker 缺失项）
    if problems:
        prompt += f"\n\n【本次{problem_kind}】（请优先处理这些）：\n"
        prompt += "\n".join(f"  - {p}" for p in problems)
        prompt += "\n"
    # 落地判据：由脚本动态注入，权威（与 derive_markers / markers_missing 完全一致）
    prompt += "\n\n【落地判据 marker（改完必须全部命中，否则 --finalize 不会提交）】:\n"
    for f, ms in markers.items():
        status = "OK" if f not in miss else "MISSING"
        sig = " / ".join(ms) if ms else "(无签名行)"
        prompt += f"  - {f}: {sig}  [{status}]\n"
    prompt += (f"\n（3-way 上下文：base={base[:8]}  ours=当前 upstream 工作树  "
               f"theirs={ikaros[:8]}。A 类文件须含其签名行；"
               f"B 类目录须存在且含代表 marker。全部 OK 后才能 --finalize。）\n")
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
        # 把 A 类文件 + B 类目录作为新 Ikaros 单提交（不碰 config.yaml 等本地文件）
        paths = list(A_CLASS_FILES) + list(B_CLASS_DIRS)
        run_git(["add", "--", *paths], check=True)
        run_git(["commit", "-m",
                 f"feat(hermes): apply Ikaros integration patches on upstream {target_sha[:8]}"],
                check=True)
        log("已提交新的 Ikaros 单提交。")

    new_commit = run_git(["rev-parse", "HEAD"], check=True).stdout.strip()
    # §0 指针：新 base = 当前 upstream（target_sha），新 Ikaros 提交 = new_commit。
    # 下次更新时 delta 将以 target_sha 为基准重放 → 自动 rebase、漂移最小。
    update_spec_pointers(SPEC, target_sha, new_commit)
    # 同步 patches/hermes/ 源文件，保持事实源与提交一致
    refresh_patch_source(new_commit)
    clear_state()
    log(f"完成。新 Ikaros 提交 = {new_commit[:8]}（基于 upstream {target_sha[:8]}）")
    log("注意：未自动 push（本地 main 是 Ikaros 修补分支，push 会污染 origin/main）。")
    return True


# --------------------------------------------------------------------------- #
# 确定性路径（完整更新：fetch + reset + 3-way 重放 + 提交 + 更新 §0）
# --------------------------------------------------------------------------- #
def step_deterministic(target: str, ikaros_old: str, backup_tag_name: str,
                       apply: bool, python_exe: Path, auto_llm: bool,
                       spec_text: str) -> int:
    base, ikaros = parse_spec_pointers(spec_text)
    if not apply:
        log(f"[计划] 将 fetch upstream，reset 到 {target}，再以 3-way 重放 "
            f"Ikaros delta (base={base[:8]} -> ikaros={ikaros[:8]})。")
        log(f"[计划] 冲突时{'自动派 LLM' if auto_llm else '写出提示词并暂停'}。")
        return 0

    # 先拉取 upstream 最新（支持镜像加速，避免直连 GitHub 卡死）
    fetch_upstream()

    target_sha = run_git(["rev-parse", "--verify", target], check=True).stdout.strip()
    # 已是最新？（HEAD 已基于该 upstream 且补丁已就位）→ 无需重打
    at_target = run_git(["merge-base", "--is-ancestor", target_sha, "HEAD"],
                         check=False).returncode == 0
    present = patch_present_via_grep()
    if at_target and present:
        log(f"HEAD 已基于 {target_sha[:8]} 且 Ikaros 补丁就位，无需重打。")
        return 0

    # 已确认需要重打，此时才打备份 tag（避免 no-op 时产生多余 tag）
    backup_tag_name = backup_tag()
    save_state({"target": target_sha, "base": base, "ikaros_old": ikaros,
                "backup_tag": backup_tag_name, "mode": "deterministic"})

    run_git(["checkout", "-B", "main"], check=True)
    # 破坏性 reset 前提示：若工作树有未提交改动，将被丢弃（备份 tag 已在上一步创建）。
    wt = run_git(["status", "--porcelain"], check=True).stdout.strip()
    if wt:
        log(f"[warn] 工作树存在未提交改动，reset --hard 将丢弃它们（备份 tag 已创建）：\n  "
            + wt[:500])
    run_git(["reset", "--hard", target_sha], check=True)
    log(f"已 reset main → {target_sha[:8]}（新 upstream，旧补丁由备份 tag 保留）")

    markers = derive_markers(base, ikaros)
    conflicts = apply_patch_delta(base, ikaros)
    if conflicts:
        log(f"3-way 重放冲突于：{', '.join(conflicts)} → 升级 LLM 兜底。")
        prompt_file = write_llm_prompt(spec_text, target_sha, problems=conflicts,
                                       problem_kind="3-way 冲突文件")
        save_state({"target": target_sha, "base": base, "ikaros_old": ikaros,
                    "backup_tag": backup_tag_name, "mode": "need-llm",
                    "prompt_file": str(prompt_file)})
        if auto_llm and dispatch_llm(prompt_file):
            if finalize(target_sha, already_committed=False, python_exe=python_exe):
                return 0
            return 1
        log("=== 需要人工 / agent 步骤 ===")
        log(f"提示词已写入：{prompt_file}；改完后运行 --finalize")
        return 2

    # 干净应用 → 校验 markers（证明每个文件补丁真的落地）
    miss = markers_missing(markers)
    if miss:
        log(f"3-way 重放完成，但 markers 缺失：{miss} → 升级 LLM 兜底。")
        run_git(["reset", "--hard", target_sha], check=True)
        prompt_file = write_llm_prompt(spec_text, target_sha, problems=miss,
                                       problem_kind="marker 缺失项")
        save_state({"target": target_sha, "base": base, "ikaros_old": ikaros,
                    "backup_tag": backup_tag_name, "mode": "need-llm",
                    "prompt_file": str(prompt_file)})
        return 2

    log("3-way 重放干净通过，验证中...")
    if finalize(target_sha, already_committed=False, python_exe=python_exe):
        return 0
    log("验证未通过，回滚到备份。")
    rollback_to_backup(backup_tag_name)
    return 1


def step_light_patch(base: str, ikaros: str, python_exe: Path, spec_text: str) -> int:
    """轻量补丁（启动预检 / 检查补丁用）：不 fetch / 不 reset，
    仅当 markers 缺失时才把 Ikaros delta 3-way 重放到「当前 HEAD」。
    冲突则回滚，且不阻塞启动。"""
    markers = derive_markers(base, ikaros)
    miss = markers_missing(markers)
    if not miss:
        log("Ikaros 补丁已就位（markers 全在），无需操作。")
        return 0
    log(f"检测到 markers 缺失：{miss} → 轻量补丁（3-way 重放到当前 HEAD）...")
    conflicts = apply_patch_delta(base, ikaros)
    if conflicts:
        log(f"轻量补丁冲突：{conflicts} → 回滚，不阻塞启动（建议手动 更新并打补丁）。")
        run_git(["reset", "--hard", "HEAD"], check=False)
        return 2
    if markers_missing(markers):
        log("轻量补丁后仍缺失，回滚。")
        run_git(["reset", "--hard", "HEAD"], check=False)
        return 2
    # 在当前 HEAD 上提交（本地 Ikaros 修补分支，不 push）
    paths = list(A_CLASS_FILES) + list(B_CLASS_DIRS)
    run_git(["add", "--", *paths], check=True)
    run_git(["commit", "-m",
             "feat(hermes): apply Ikaros integration patches (auto, light)"],
            check=True)
    log("轻量补丁已提交到当前 HEAD。")
    return 0


def step_finalize(python_exe: Path) -> int:
    state = load_state()
    if state.get("mode") != "need-llm":
        log("状态显示并非处于 LLM 兜底待收尾阶段；若确需收尾请确认流程。")
    target_sha = state["target"]
    backup_tag_name = state.get("backup_tag")

    # 仅确保 B 类目录存在（A 类已由 LLM/人工改好，勿用旧源覆盖）
    for d in B_CLASS_DIRS:
        ensure_b_class(d)

    if finalize(target_sha, already_committed=False, python_exe=python_exe):
        return 0
    return 1


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Hermes × Ikaros 补丁稳定重打工具")
    ap.add_argument("--apply", action="store_true",
                    help="完整更新：fetch + reset + 3-way 重放 + 提交 + 更新 §0")
    ap.add_argument("--light-patch", action="store_true",
                    help="轻量补丁：不 fetch/不 reset，仅当 markers 缺失时 3-way "
                         "重放到当前 HEAD（启动预检用，冲突则回滚且不阻塞）")
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

    if args.light_patch:
        return step_light_patch(upstream_tip, ikaros_commit, python_exe, spec_text)

    if not args.apply:
        ensure_clean_working_tree()
        log("=== 计划模式（未加 --apply，不改动）===")
        return step_deterministic(args.target, ikaros_commit, "",
                                  apply=False, python_exe=python_exe,
                                  auto_llm=args.auto_llm, spec_text=spec_text)

    # --apply：脚本会自行 reset --hard 到 upstream 基线，无需预先要求工作树干净。
    # 破坏性 reset 前已在 step_deterministic 内打备份 tag，可随时回滚；若工作树
    # 有未提交改动，reset 会丢弃——这里仅交由下方告警处理，不阻断（避免
    # “上一轮失败残留脏树导致下一轮 --apply 被预检卡死”的陷阱）。
    return step_deterministic(args.target, ikaros_commit, "",
                              apply=True, python_exe=python_exe,
                              auto_llm=args.auto_llm, spec_text=spec_text)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        log(f"错误：{e}")
        sys.exit(1)
