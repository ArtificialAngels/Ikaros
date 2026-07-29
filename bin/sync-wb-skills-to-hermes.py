#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync-wb-skills-to-hermes.py

把 WorkBuddy 用户级技能单向同步到 Hermes 的技能目录，让 Hermes "学习"
WorkBuddy 这边沉淀的自建技能。

来源 (source) : %USERPROFILE%\\.workbuddy\\skills\\   (WorkBuddy 用户级技能)
目标 (dest)   : <IKAROS_ROOT>\\data\\hermes-agent\\skills\\  (Hermes 技能目录)

规则:
  - 只同步包含 SKILL.md 的目录 (那才是技能)。
  - 跳过名称以 "." 开头的条目 (隐藏目录/.git 等)。
  - 整目录 sha256 比对: 目标已存在且内容一致 -> 跳过; 否则覆盖更新。
  - 目标不存在 -> 新增拷贝。
  - 用 --dry-run 只报告不落盘。

WorkBuddy 的 SKILL.md frontmatter 与 Hermes 兼容, 直接拷贝即可被 Hermes 自发现。
"""

import hashlib
import os
import shutil
import sys

# ---- 路径解析 ---------------------------------------------------------------
WB_SKILLS = os.path.expandvars(r"%USERPROFILE%\.workbuddy\skills")
# IKAROS_ROOT 可由环境变量覆盖, 否则向上找带 AGENTS.md 的仓库根
IKAROS_ROOT = os.environ.get("IKAROS_ROOT") or r"E:\Ikaros"
HERMES_SKILLS = os.path.join(IKAROS_ROOT, "data", "hermes-agent", "skills")


def hash_skill(skill_dir: str) -> str:
    """对技能目录下所有文件(相对路径+内容)求 sha256, 作为整体指纹。"""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(skill_dir):
        # 不跟进隐藏子目录
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in sorted(files):
            if f.startswith("."):
                continue
            fp = os.path.join(root, f)
            rel = os.path.relpath(fp, skill_dir)
            h.update(rel.replace("\\", "/").encode("utf-8"))
            try:
                with open(fp, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        h.update(chunk)
            except OSError:
                pass
    return h.hexdigest()


def has_disable_true(skill_dir: str) -> bool:
    """粗略检测 SKILL.md frontmatter 是否含 'disable: true'。"""
    fp = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(fp):
        return False
    try:
        with open(fp, "r", encoding="utf-8") as fh:
            head = fh.read(800)
    except OSError:
        return False
    # 只在 frontmatter (首个 --- 块) 内判定
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    block = head[3:end] if end != -1 else head
    return "disable: true" in block


def main(argv) -> int:
    dry = "--dry-run" in argv
    verbose = "-v" in argv or "--verbose" in argv

    print(f"[src] {WB_SKILLS}")
    print(f"[dst] {HERMES_SKILLS}")
    if dry:
        print("[mode] DRY-RUN (不会落盘)\n")

    if not os.path.isdir(WB_SKILLS):
        print(f"[skip] WorkBuddy 用户级技能目录不存在: {WB_SKILLS}")
        return 0
    if not os.path.isdir(HERMES_SKILLS):
        print(f"[skip] Hermes 技能目录不存在: {HERMES_SKILLS}")
        return 0

    added, updated, unchanged, skipped = [], [], [], []
    disabled_warn = []

    for name in sorted(os.listdir(WB_SKILLS)):
        if name.startswith("."):
            continue
        src = os.path.join(WB_SKILLS, name)
        if not os.path.isdir(src):
            continue
        if not os.path.isfile(os.path.join(src, "SKILL.md")):
            skipped.append(name)
            if verbose:
                print(f"  [skip] {name}: 无 SKILL.md, 非技能目录")
            continue

        dst = os.path.join(HERMES_SKILLS, name)
        src_hash = hash_skill(src)
        if os.path.isdir(dst):
            if hash_skill(dst) == src_hash:
                unchanged.append(name)
                if verbose:
                    print(f"  [ok]   {name}: 已是最新, 跳过")
                continue
            action = "update"
        else:
            action = "add"

        if has_disable_true(src):
            disabled_warn.append(name)

        if dry:
            (updated if action == "update" else added).append(name)
            print(f"  [{'UPDATE' if action == 'update' else 'ADD'}] {name} (dry-run)")
            continue

        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        (updated if action == "update" else added).append(name)
        print(f"  [{'UPDATE' if action == 'update' else 'ADD'}] {name}")

    # ---- 汇总 ----------------------------------------------------------------
    print("\n==== 同步汇总 ====")
    print(f"  新增 (added)   : {len(added)}  -> {', '.join(added) or '无'}")
    print(f"  更新 (updated) : {len(updated)}  -> {', '.join(updated) or '无'}")
    print(f"  未变 (same)    : {len(unchanged)}  -> {', '.join(unchanged) or '无'}")
    print(f"  跳过 (skipped) : {len(skipped)}  -> {', '.join(skipped) or '无'}")

    if disabled_warn:
        print("\n⚠️ 注意: 以下技能在 WorkBuddy 侧带 'disable: true', "
              "会一并拷贝到 Hermes 并可能同样被禁用:")
        for n in disabled_warn:
            print(f"   - {n}")
        print("   若想让 Hermes 启用, 请在 Hermes 副本的 SKILL.md 中删除/改该字段。")

    if dry:
        print("\n(DRY-RUN 完成, 未发生任何写入)")
    else:
        print("\n同步完成。Hermes 下次加载技能时会自动发现更新后的 SKILL.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
