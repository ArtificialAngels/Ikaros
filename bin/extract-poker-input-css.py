#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract-poker-input-css.py — 从 ai.explore.poker 原版提取输入栏完整 CSS 移植包
=============================================================================
输入: 原版 990 chunk（类名清单）+ 325c22a01b776fae.css（Tailwind 产物）+ color.css（变量）
输出: poker-input.css — 输入栏用到的全部类规则（原样复制，含变量依赖）
用法: python extract-poker-input-css.py [--out poker-input.css]
"""
import re
import sys
from pathlib import Path

BASE = Path(r"E:/Ikaros-something/reference project/ai.explore.poker")
TW = BASE / "_next/static/css/325c22a01b776fae.css"
COLOR = BASE / "color.css"

# 输入栏 JSX 里出现的全部类名（从 990 chunk 提取，人工整理）
CLASSES = [
    # 容器
    "bg-transparent", "p-4", "relative", "z-20",
    "bg-inputarea", "shadow-card", "border-2", "rounded-[28px]", "p-3",
    "flex", "flex-col", "gap-3", "transition-colors",
    "absolute", "inset-0", "bg-brandtw/10", "pointer-events-none",
    # 附件 chips
    "overflow-x-auto", "gap-2", "min-w-max", "pb-1", "items-center",
    "gap-1.5", "bg-btn-inputarea", "rounded-md", "px-2", "py-1",
    "text-primary", "flex-shrink-0", "text-xs", "max-w-[120px]", "truncate",
    "text-icon-secondary", "text-danger-hover-hover",
    # textarea
    "w-full", "h-full", "scrollbar-inputarea", "text-base", "leading-5",
    "resize-none", "outline-none", "placeholder:truncate",
    "placeholder:select-none", "placeholder-input", "px-0", "py-1",
    "overflow-y-auto", "block", "transition-[height]", "duration-300",
    "ease-[cubic-bezier(0.2,0,0,1)]",
    # 左侧按钮排
    "left-0", "right-0", "bottom-0", "z-10", "h-[34px]", "w-max", "max-w-[calc(100%_-_92px)]",
    "min-w-0",
    # 模型选择
    "max-w-[220px]", "justify-between", "text-sm", "bg-btn-selector",
    "shadow-selector", "px-2.5", "py-1.5", "rounded-[16px]", "hover:bg-opacity-80",
    "transition-all", "disabled:opacity-50", "disabled:cursor-not-allowed",
    "select-none", "transition-transform", "gap-1",
    # 模型下拉
    "bottom-full", "mb-2", "w-56", "bg-modal-floating", "border-modeldropdown",
    "rounded-2xl", "duration-200", "ease-in-out", "origin-bottom-left",
    "bg-btn-selector-item-hover", "cursor-pointer", "text-[10px]", "text-tertiary",
    "content-brand", "border-t", "border-divider",
    # 按钮
    "w-[34px]", "rounded-full", "bg-btn-inputarea-transparent-hover",
    "hover:text-primary", "justify-center", "bg-btn-inputarea-hover",
    "shadow-btn", "hidden", "transition-opacity", "duration-200", "ease-out",
    "text-icon-secondary", "hover:bg-opacity-80", "content-brand",
    # 激活态
    "text-brand", "bg-btn-inputarea-hover",
]

# Tailwind 工具类（标准，不在 color 变量里）也需要的：
TOOL_CLASSES = ["flex", "flex-col", "items-center", "justify-between", "gap-2", "gap-3",
                "w-full", "h-full", "relative", "absolute", "block", "truncate",
                "overflow-x-auto", "overflow-y-auto", "resize-none", "outline-none",
                "select-none", "cursor-pointer", "flex-shrink-0", "min-w-0", "min-w-max",
                "w-max", "w-56", "w-[34px]", "h-[34px]", "max-w-[120px]",
                "max-w-[220px]", "max-w-[calc(100%_-_92px)]", "px-0", "px-2", "px-2.5",
                "px-3", "px-4", "py-1", "py-1.5", "p-3", "p-4", "pb-1", "mb-2",
                "gap-1.5", "text-xs", "text-sm", "text-base", "text-[10px]",
                "leading-5", "rounded-md", "rounded-2xl", "rounded-[16px]",
                "rounded-[28px]", "rounded-full", "border-2", "border-t",
                "z-10", "z-20", "inset-0", "left-0", "bottom-0", "bottom-full",
                "justify-center", "items-center", "transition-colors", "transition-all",
                "transition-opacity", "transition-transform", "transition-[height]",
                "duration-200", "duration-300", "ease-in-out", "ease-out",
                "ease-[cubic-bezier(0.2,0,0,1)]", "origin-bottom-left",
                "pointer-events-none", "bg-transparent", "bg-brandtw/10",
                "hover:bg-opacity-80", "disabled:opacity-50", "disabled:cursor-not-allowed",
                "hover:text-primary", "placeholder:truncate", "placeholder:select-none",
                "text-danger-hover-hover"]


def extract_rules(css: str, classes: list[str]) -> str:
    """从压缩 CSS 里按类名提取规则块（保持原样）"""
    out = []
    seen = set()
    for cls in classes:
        # 直接字面查找 .类名{ 或 .类名:修饰{ —— 用正则宽松匹配
        # 类名含 [ ] ( ) / : 等字符，re.escape 全量转义
        literal = re.escape(cls)
        # CSS 文件里任意字符也可能被反斜杠转义（如 .rounded-\[28px\]、.px-2\.5）
        # Tailwind 产物里方括号转义是双反斜杠 (max-w-\\[220px\\])，统一按 0-2 个反斜杠匹配
        literal = literal.replace(r"\[", r"\\{0,2}\[").replace(r"\]", r"\\{0,2}\]")
        literal = literal.replace(r"\(", r"\\?\(").replace(r"\)", r"\\?\)")
        literal = literal.replace(r"\/", r"\\?/")
        literal = literal.replace(r"\.", r"\\?\.")
        pat = re.compile(r"\." + literal + r"(?::[\w-]+)?\{[^}]*\}")
        for m in pat.finditer(css):
            if m.group(0) not in seen:
                seen.add(m.group(0))
                out.append(m.group(0))
    return "\n".join(out)


def main() -> int:
    out_path = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path("poker-input.css")
    tw = TW.read_text(encoding="utf-8")
    color = COLOR.read_text(encoding="utf-8")

    all_classes = list(dict.fromkeys(CLASSES + TOOL_CLASSES))
    rules = extract_rules(tw, all_classes)

    # color 变量：提取默认主题（第一块 :root）的输入栏相关变量
    var_pat = re.compile(r"(--color-(?:bg-inputarea|bg-btn-inputarea|bg-btn-inputarea-hover|bg-btn-inputarea-transparent-hover|bg-btn-selector|bg-btn-selector-item-hover|border-std|border-divider|border-modeldropdown|text-primary|text-tertiary|text-quaternary|text-icon-secondary|brand|brandtw|bg-modal-floating|shadow-btn)[^;]*;)")
    first_root = color[color.find(":root"):color.find("}", color.find(":root"))]
    vars_found = var_pat.findall(first_root)
    var_block = "\n".join("  " + v for v in dict.fromkeys(vars_found))

    header = f"""/* ═══ poker 原版输入栏移植包（自动提取 {TW.name} + color.css）═══ */
/* 用法: 粘贴到 index.html <style> 末尾。类名与原版一致, 不映射不重写。 */
:root {{
{var_block}
}}
"""
    out_path.write_text(header + "\n" + rules, encoding="utf-8")
    print(f"提取 {len(all_classes)} 个类, {len(rules)} 字符规则")
    print(f"color 变量 {len(var_block)} 字符")
    print(f"输出: {out_path} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
