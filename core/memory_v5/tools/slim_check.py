"""切 slim 前的可执行闸门 (2026-08-30)。

为什么需要它:
    `V5_MCP_TOOL_MODE=slim` 只暴露 17 个工具, 另外 41 个 legacy-only 工具不再注册。
    「哪些地方引用了会被摘掉的工具名」这件事**没有任何静态检查** —— 切过去之后
    persona / AGENTS.md / 插件里的旧工具名会变成指向不存在的工具:
      - persona 里的 → 模型照着指令调用, 直接 tool-not-found, 每轮都浪费一次重试
      - 插件 TS 里的 → 静默失败, 连报错都没有
    这类问题**不会在测试里暴露**(测试直接 import Python 函数, 不走 MCP 注册),
    只在真实会话里发作。所以做成可执行闸门, 切模式前跑一次。

用法 (⚠️ 必须以**脚本方式**运行, 不能用 -m):
    python core/memory_v5/tools/slim_check.py          # 检查
    python core/memory_v5/tools/slim_check.py --json   # 机器可读
    python core/memory_v5/tools/slim_check.py --all    # 连文档/历史也报 (默认只报运行期文件)

为什么不能 `python -m memory_v5.tools.slim_check`:
    `-m` 要求 `memory_v5` 在**模块执行之前**就可导入, 而本文件的 `sys.path.insert`
    在 import registry 之前 —— 顺序上救不了自己。裸 PYTHONPATH=core 也能跑,
    但那样这条命令就依赖调用方环境, 不如脚本方式自包含。

退出码:
    0 = 可以切 slim
    1 = 有阻塞项 (运行期文件引用了 legacy-only 工具名, 或 group 未登记)

设计取舍:
    只扫「运行期会被加载」的文件 (LIVE_FILES), 不扫 docs/ 与 .workbuddy/memory/ ——
    历史文档里引用旧工具名是**事实陈述**, 改它等于篡改历史, 而且量太大
    (26 个工具名 / 上百处) 会把真正的阻塞项淹掉。加 --all 可看全量。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# memory_v5/ -> core/
_CORE = Path(__file__).resolve().parents[2]
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from memory_v5.tools import registry as R  # noqa: E402

# IKAROS 根 (core/ 的上一级)
_ROOT = _CORE.parent

#: 运行期会被 dsh / agent 真正加载的文件 —— 这里的工具名引用是**硬契约**。
#: 新增/删除 persona 或改插件入口时同步这里。
LIVE_FILES: tuple[str, ...] = (
    "core/ikaros-dsh/cordis.patch.yml",                          # persona + V5_MCP_TOOL_GROUPS
    "core/ikaros-dsh/plugins/ikaros-memory/src/index.ts",        # 插件 hook
    "core/ikaros-dsh/plugins/ikaros-memory/bin/v5_call.py",      # 插件 Python 侧
    "AGENTS.md",                                                 # agent 指令
    "CLAUDE.md",
)

#: 身份文件 —— 不直接被 dsh 加载(由 soul-sync 工具链生成/消费), 但一旦注入
#: 就是「我是谁」的权威表述, 引用不存在的工具名等于让 Ikaros 学一套做不到的动作。
#: 单独一档, 不算阻塞, 但切 slim 时应同步。
IDENTITY_FILES: tuple[str, ...] = (
    "data/soul/SOUL.md",
    "config/identity/capabilities.md",
)

#: 扫描时跳过的目录 (缓存/依赖/产物, 里面的工具名是旧文件快照, 全是噪音)
_SKIP_DIRS = {
    ".git", "node_modules", "runtime", "tmp", "target", "__pycache__",
    ".cache", ".hermes", ".gitnexus", "venv", "output", "assets",
}

#: 允许的文本后缀
_TEXT_SUFFIXES = {".md", ".py", ".ts", ".tsx", ".json", ".yaml", ".yml", ".sh", ".bat", ".ps1"}

_TOOL_NAME_RE = re.compile(r"\bv5_[a-z0-9_]+")

#: 长得像工具名、其实是别的东西的标识符 —— 不加白名单会制造一堆假阳性,
#: 而假阳性一多, 真阻塞项就没人看了 (狼来了)。
#:   v5_call      插件的 CLI 脚本名 (bin/v5_call.py), 不是 MCP 工具
#:   v5_kind      插件 TS 里的字段名
#:   v5_key       记忆表的 key 字段
#:   v5_memory_id 记忆 id 字段名
#:   v5_project   项目名参数 / 目录名
#:   v5__*        mcp__ikaros-v5__v5_xxx 这种带前缀的写法被正则截断的残片
_NON_TOOL_ALLOWLIST = {"v5_call", "v5_kind", "v5_key", "v5_memory_id", "v5_project"}


def _is_tool_like(name: str) -> bool:
    """排除已知的「长得像工具名但不是工具」的标识符。"""
    if name in _NON_TOOL_ALLOWLIST:
        return False
    if name.startswith("v5__"):  # mcp__ikaros-v5__v5_xxx 的截断残片
        return False
    return True


def _read(rel: str) -> str | None:
    p = _ROOT / rel
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return None


#: 代码文件里表示「这一行是注释」的行首标记。
#: 为什么注释行不算引用: 本闸门回答的问题是「切 slim 后会不会指向不存在的工具」。
#: 注释里的工具名是**叙事**(解释历史/背景), 不产生调用, 切过去也不会坏。
#: 反过来, 把它们算成阻塞项只会制造假阳性 —— AGENTS.md 的变更日志里有上百处
#: 「过去修了 v5_xxx」的陈述, 全算进去真阻塞项就没人看了。
_COMMENT_PREFIXES = ("//", "#", "*", "/*")

#: markdown 文件里标记「以下是历史记录」的显式哨兵。
#: 用法: 在历史章节开头写 `<!-- v5-history: 说明 -->`, 结尾写 `<!-- /v5-history -->`。
#: 为什么用哨兵而不是按标题匹配: 标题文案会改, 哨兵不会; 而且哨兵是自解释的 ——
#: 后来者看到它就知道「这里面的工具名是事实陈述, 不是契约」。
_HISTORY_BEGIN = "<!-- v5-history"
_HISTORY_END = "<!-- /v5-history -->"


def _is_comment_line(line: str, suffix: str) -> bool:
    if suffix in {".md"}:
        return False
    s = line.strip()
    return s.startswith(_COMMENT_PREFIXES)


def _scan(rel: str) -> dict[str, list[int]]:
    """返回 {工具名: [行号...]}。跳过注释行与 `<!-- v5-history -->` 区间。"""
    text = _read(rel)
    if text is None:
        return {}
    suffix = Path(rel).suffix.lower()
    out: dict[str, list[int]] = {}
    in_history = False
    for i, line in enumerate(text.splitlines(), 1):
        if _HISTORY_BEGIN in line:
            in_history = True
            continue
        if _HISTORY_END in line:
            in_history = False
            continue
        if in_history or _is_comment_line(line, suffix):
            continue
        for name in _TOOL_NAME_RE.findall(line):
            out.setdefault(name, []).append(i)
    return out


#: 门面 action 反查表: {legacy 工具名: (门面名, action)}
#: 从 facade.py 源码静态解析 `if action == "x": ... import v5_legacy` 得到 ——
#: **读的是真源**, 不复制一份映射表(复制就会漂移, 这正是 registry 要根治的病)。
#: 解析失败时退化成 `v5_x(action=...)`, 不阻塞闸门。
_FACADE_ACTION_RE = re.compile(
    r'if\s+action\s*==\s*["\']([a-z_]+)["\']\s*:(?:(?!\n\s*if\s+action)[\s\S])*?'
    r'import\s+(v5_[a-z0-9_]+)',
)
_ACTION_CACHE: dict[str, tuple[str, str]] | None = None


#: 门面 docstring 里 `action:` 块列出的合法 action。
#: ⚠️ 必须带 re.MULTILINE —— 少了它 `^` 只匹配整个 body 的开头,
#: finditer 静默返回 0 条, 表现成"解析不到"而不是报错, 极难察觉。
_DOC_ACTION_RE = re.compile(r"^\s{8,}([a-z_][a-z0-9_]*)\s{2,}\S", re.MULTILINE)


def _facade_action_map() -> dict[str, tuple[str, str]]:
    global _ACTION_CACHE
    if _ACTION_CACHE is not None:
        return _ACTION_CACHE
    out: dict[str, tuple[str, str]] = {}
    src = _read("core/memory_v5/tools/facade.py") or ""
    lines = src.splitlines()

    # 每个门面函数的起止行
    spans: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        m = re.match(r"def\s+(v5_[a-z0-9_]+)\s*\(", line)
        if m:
            spans.append((m.group(1), i))

    for k, (fname, start) in enumerate(spans):
        end = spans[k + 1][1] if k + 1 < len(spans) else len(lines)
        body = "\n".join(lines[start:end])

        # 1) 精确路径: 解析 `if action == "x": ... import v5_legacy`
        for action, legacy in _FACADE_ACTION_RE.findall(body):
            out.setdefault(legacy, (fname, action))

        # 2) 兜底路径: 有些门面不走 import v5_x (比如 v5_skill 直接调 skill_store),
        #    正则抓不到。改为**解析 docstring 的 action 表**, 再用 legacy 名去掉
        #    门面前缀后的后缀去匹配 (v5_skill_write -> write)。
        #    匹配不上就留空, `_replacement_for` 会退化成 `action=?`。
        doc_actions = [m.group(1) for m in _DOC_ACTION_RE.finditer(body)]
        for legacy in R.FACADE_ABSORBS.get(fname, []):
            if legacy in out:
                continue
            suffix = legacy[len(fname) + 1:]  # v5_skill_write -> write
            if suffix in doc_actions:
                out[legacy] = (fname, suffix)
    _ACTION_CACHE = out
    return out


def _replacement_for(name: str) -> str:
    """legacy-only 工具名 → 替代方案。

    判定顺序**必须是 Loop 内化优先于门面**: 被 Loop 内化的工具在 slim 下
    **根本不注册**, 告诉模型「改用 v5_repeat(action=...)」是错的 —— 那扇门也关着,
    正确说法是「由 v5_loop 的 post 阶段自动推进, 不用手动调」。
    (v5_anti_repeat_record 同时出现在 FACADE_ABSORBS 和 LOOP_ABSORBS 里,
     顺序反了就会给出指向不存在工具的坏建议。)
    """
    for phase, absorbed in R.LOOP_ABSORBS.items():
        if name in absorbed:
            return f"v5_loop(phase='{phase}') 内化 (无需手动调)"
    hit = _facade_action_map().get(name)
    if hit:
        facade, action = hit
        return f'{facade}(action="{action}")'
    for facade in R.FACADE_ABSORBS:
        if name in R.FACADE_ABSORBS[facade]:
            return f"{facade}(action=? 见 docstring)"  # 解析兜底
    return "?"


def _check_groups() -> list[dict]:
    """slim 工具的 group 必须都在 V5_MCP_TOOL_GROUPS 里, 否则被静默过滤。

    这是实测踩过的坑: group 不在列表里时工具**静默消失**, 不报错。
    缺 loop 组 → slim 只剩 16 个且无 Loop 入口。
    """
    text = _read("core/ikaros-dsh/cordis.patch.yml") or ""
    m = re.search(r"V5_MCP_TOOL_GROUPS:\s*'([^']*)'", text)
    if not m:
        return [{"kind": "group", "severity": "block",
                 "detail": "cordis.patch.yml 里找不到 V5_MCP_TOOL_GROUPS"}]
    declared = {g.strip() for g in m.group(1).split(",") if g.strip()}
    needed = {R.TOOL_GROUPS[n] for n in R.SLIM_TOOL_NAMES}
    missing = sorted(needed - declared)
    if not missing:
        return []
    return [{
        "kind": "group",
        "severity": "block",
        "detail": f"slim 工具需要 group {sorted(needed)}, "
                  f"但 V5_MCP_TOOL_GROUPS 缺 {missing} —— 缺的组会被静默过滤",
        "missing": missing,
    }]


def _check_identity_absent() -> list[dict]:
    """身份文件读不到 → 必须说出来, 不能静默跳过。

    这是本闸门自己踩的坑: `_scan()` 对读不到的文件返回 `{}`, 于是
    **缺文件 == 零命中 == 干净**, 闸门照样打印「✅ 可以切 slim」。

    为什么必须警告: `data/soul/SOUL.md` 被 `.gitignore:40` 的 `data/**`
    排除, **从不进 git**(项目约定: 用户状态不入库)。新克隆/换机器时它
    根本不存在, 而它恰恰是 slim 下最容易翻车的地方 —— 里面写的旧工具名
    会指向没注册的工具, 模型调用时才发现, 且**这类问题测试永远抓不到**
    (测试直接 import Python 函数, 不走 MCP 注册)。

    severity 用 warn 不是 block: 文件可能确实不需要存在, 不该硬卡住。
    但「可以切 slim」这句必须带上「有 N 项没验证过」的注脚。
    """
    out: list[dict] = []
    for rel in IDENTITY_FILES:
        if _read(rel) is None:
            out.append({
                "kind": "identity_absent",
                "severity": "warn",
                "file": rel,
                "detail": "文件不存在或读不到 —— 无法验证里面有没有 legacy 工具名",
            })
    return out


def run(*, include_all: bool = False) -> dict:
    slim = set(R.SLIM_TOOL_NAMES)
    legacy_only = set(R.LEGACY_ONLY_NAMES)

    blocks: list[dict] = []
    warns: list[dict] = []
    identity: list[dict] = []

    for rel in LIVE_FILES:
        for name, lines in sorted(_scan(rel).items()):
            if name in legacy_only:
                blocks.append({
                    "kind": "legacy_ref",
                    "severity": "block",
                    "file": rel,
                    "tool": name,
                    "lines": lines,
                    "fix": _replacement_for(name),
                })
            elif name not in slim and _is_tool_like(name):
                # 不在注册表里 —— 可能是拼错, 也可能是 mcp__ikaros-v5__v5_x 这种前缀串
                warns.append({
                    "kind": "unknown_ref",
                    "severity": "warn",
                    "file": rel,
                    "tool": name,
                    "lines": lines,
                })

    for rel in IDENTITY_FILES:
        for name, lines in sorted(_scan(rel).items()):
            if name in legacy_only:
                identity.append({
                    "kind": "identity_ref",
                    "severity": "info",
                    "file": rel,
                    "tool": name,
                    "lines": lines,
                    "fix": _replacement_for(name),
                })

    blocks.extend(_check_groups())
    warns.extend(_check_identity_absent())

    docs: list[dict] = []
    if include_all:
        slim_refs: dict[str, int] = {}
        for p in _ROOT.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except (PermissionError, OSError):
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            rel = p.as_posix().replace(f"{_ROOT.as_posix()}/", "")
            if rel.startswith("core/memory_v5/"):
                continue  # 实现自身必然引用全部工具名
            if rel in LIVE_FILES or rel in IDENTITY_FILES:
                continue
            text = _read(rel)
            if text is None:
                continue
            for name in _TOOL_NAME_RE.findall(text):
                if name in legacy_only:
                    docs.append({"file": rel, "tool": name})

    return {
        "slim_tools": len(slim),
        "legacy_only_tools": len(legacy_only),
        "block": blocks,
        "warn": warns,
        "identity": identity,
        "docs": docs,
        "ok": not blocks,
    }


def _render(res: dict, *, show_docs: bool) -> None:
    print(f"slim 工具 {res['slim_tools']} 个 / legacy-only {res['legacy_only_tools']} 个")
    print()

    if res["block"]:
        print(f"❌ 阻塞项 {len(res['block'])} 个 (切 slim 前必须清掉):")
        for b in res["block"]:
            if b["kind"] == "group":
                print(f"   [group] {b['detail']}")
                continue
            loc = ",".join(f"L{n}" for n in b["lines"][:6])
            print(f"   {b['file']}:{loc}  {b['tool']}")
            print(f"      → 改用 {b['fix']}")
        print()

    if res["warn"]:
        unknown = [w for w in res["warn"] if w["kind"] == "unknown_ref"]
        absent = [w for w in res["warn"] if w["kind"] == "identity_absent"]
        if unknown:
            print(f"⚠️  可疑引用 {len(unknown)} 个 (工具名不在注册表, 可能拼错):")
            for w in unknown:
                loc = ",".join(f"L{n}" for n in w["lines"][:4])
                print(f"   {w['file']}:{loc}  {w['tool']}")
            print()
        if absent:
            print(f"⚠️  身份文件缺失 {len(absent)} 个 —— 这些文件没被验证过:")
            for w in absent:
                print(f"   {w['file']}  ({w['detail']})")
            print("   提示: data/soul/SOUL.md 被 .gitignore 排除, 不进 git ——")
            print("         换机器/新克隆时它不存在, 需先恢复再跑本闸门。")
            print()

    if res["identity"]:
        print(f"ℹ️  身份文件 {len(res['identity'])} 处 (不影响运行, 但切 slim 时应同步):")
        for it in res["identity"]:
            loc = ",".join(f"L{n}" for n in it["lines"][:4])
            print(f"   {it['file']}:{loc}  {it['tool']} → {it['fix']}")
        print()

    if show_docs and res["docs"]:
        seen: dict[str, int] = {}
        for d in res["docs"]:
            seen[d["tool"]] = seen.get(d["tool"], 0) + 1
        print(f"📄 文档/历史引用 legacy-only 名 {len(res['docs'])} 处 "
              f"({len(seen)} 个工具) —— 事实陈述, 不必改:")
        for t, n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"   {t} × {n}")
        print()

    if not res["ok"]:
        print(f"❌ 有 {len(res['block'])} 个阻塞项, 还不能切 slim")
        return

    # ⚠️ 别无条件说 OK: 缺文件 = 没验证过 = 「没发现阻塞」不等于「确认安全」。
    #    静默放行一个未验证的身份文件, 比多打两行警告危险得多。
    absent = [w for w in res["warn"] if w["kind"] == "identity_absent"]
    if absent:
        print(f"✅ 未发现阻塞项, 但有 {len(absent)} 个身份文件没验证过 —— "
              f"补上后再跑一次才算真的绿")
    else:
        print("✅ 可以切 slim")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="切 V5_MCP_TOOL_MODE=slim 前的静态闸门")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--all", action="store_true", help="连文档/历史引用也报")
    args = ap.parse_args(argv)

    res = run(include_all=args.all)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _render(res, show_docs=args.all)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
