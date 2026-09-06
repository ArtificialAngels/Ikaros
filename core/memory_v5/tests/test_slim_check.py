"""切 slim 闸门 (core/memory_v5/tools/slim_check.py) 的守门测试。

守三件事:
  1. **闸门当前必须是绿的** —— 有人往运行期文件里塞 legacy 工具名, 这里立刻变红。
     (本闸门的价值全在于"切模式前有人真跑它", 绿着才有意义。)
  2. **替代建议必须可执行** —— 每个 legacy-only 工具都得给出明确去处,
     不能出现 "?" (那是"不知道该改什么"的意思, 等于闸门白报)。
  3. **action 反查必须全覆盖** —— 见 test_facade_action_map_covers_all,
     这条专守 2026-08-30 那个 re.MULTILINE 静默失效。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[2]  # tests/ -> memory_v5/ -> core/
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

from memory_v5.tools import registry as R
from memory_v5.tools import slim_check as SC


# ── 1) 闸门当前必须是绿的 ────────────────────────────────────────────
def test_gate_is_green():
    """切 slim 前跑 `python core/memory_v5/tools/slim_check.py` 应退出 0。"""
    res = SC.run()
    assert res["ok"], (
        "闸门红了 —— 有运行期文件引用了 legacy-only 工具名:\n"
        + "\n".join(
            f"  {b.get('file','?')}:{b.get('lines','?')} {b.get('tool', b.get('detail',''))}"
            for b in res["block"]
        )
    )
    assert res["slim_tools"] == 16  # 2026-09-05: v5_content 从 slim 移除
    assert res["legacy_only_tools"] == 41


# ── 2) 每个 legacy-only 工具都必须有明确去处 ──────────────────────────
def test_every_legacy_tool_has_a_replacement():
    """"?" = 不知道该改成什么, 闸门报了也白报。

    2026-09-05: SLIM_REMOVED_LEGACY 中的工具已从 slim facade 移除,
    它们的替代方案是"已从 slim 移除", 不含 "docstring" fallback。
    """
    for name in R.LEGACY_ONLY_NAMES:
        fix = SC._replacement_for(name)
        assert fix != "?", f"{name} 没有替代方案 —— 补进 facade action 表或 LOOP_ABSORBS"
        if name not in SC.SLIM_REMOVED_LEGACY:
            assert "docstring" not in fix, (
                f"{name} 的 action 解析失败 (fallback={fix!r}) —— "
                "多半是 facade.py 的分发写法变了, 检查 slim_check._facade_action_map"
            )


def test_loop_absorbed_tools_point_at_loop_not_facade():
    """被 Loop 内化的工具在 slim 下**根本不注册**。

    若建议写成 `v5_repeat(action=...)`, 模型照做会撞上不存在的工具 ——
    所以 Loop 内化的判定必须优先于门面 (v5_anti_repeat_record 两边都在)。
    """
    loop_absorbed = {t for v in R.LOOP_ABSORBS.values() for t in v}
    for name in sorted(loop_absorbed & set(R.LEGACY_ONLY_NAMES)):
        assert "v5_loop" in SC._replacement_for(name), (
            f"{name} 被 Loop 内化, 建议必须指向 v5_loop, 实际: {SC._replacement_for(name)}"
        )


# ── 3) action 反查覆盖率 (专守 re.MULTILINE 静默失效) ─────────────────
def test_facade_action_map_covers_all():
    """除 Loop 内化和已从 slim 移除的外, 每个 legacy-only 工具都要能反查到 (facade, action)。

    ⚠️ 2026-08-30 踩过: `_DOC_ACTION_RE` 用了 `^` 锚点却漏了 `re.MULTILINE`,
       finditer 静默返回 0 条 → 整条兜底路径空转 → 覆盖率从 41 掉到 32,
       而**没有任何报错**, 只是输出里多了几个 "action=? 见 docstring"。
       这条测试就是那个 bug 的墓碑。

    2026-09-05: 10 个零消费方工具从 slim facade 移除, 不计入覆盖率。
    """
    m = SC._facade_action_map()
    loop_absorbed = {t for v in R.LOOP_ABSORBS.values() for t in v}
    excluded = loop_absorbed | SC.SLIM_REMOVED_LEGACY
    unresolved = [t for t in R.LEGACY_ONLY_NAMES if t not in m and t not in excluded]
    assert not unresolved, (
        f"这些 legacy 工具反查不到 action: {unresolved}\n"
        "常见原因: facade.py 的 docstring action 块缩进变了, 或正则丢了 re.MULTILINE"
    )
    # 覆盖率不该悄悄退化 (原 37, 移除 10 个零消费方后应为 27)
    assert len(m) >= 27, f"action 映射从 27 掉到 {len(m)}, 检查解析逻辑"


# ── 4) group 未登记必须被判为阻塞 ────────────────────────────────────
def test_missing_group_is_blocked(monkeypatch):
    """slim 工具的 group 不在 V5_MCP_TOOL_GROUPS 里 → 工具**静默消失**。

    实测踩过: 缺 loop 组时 slim 只剩 16 个且无 Loop 入口, 不报任何错。
    """
    good = SC._check_groups()
    assert good == [], f"当前配置应无 group 缺口, 实际: {good}"

    monkeypatch.setattr(
        SC, "_read",
        lambda rel: "V5_MCP_TOOL_GROUPS: 'memory,self'" if rel.endswith("cordis.patch.yml") else None,
    )
    bad = SC._check_groups()
    assert bad and bad[0]["severity"] == "block"
    assert "loop" in bad[0]["missing"]


# ── 5) 注释行 / 历史哨兵不误报 ───────────────────────────────────────
def test_comment_and_history_are_not_reported(tmp_path, monkeypatch):
    """注释与 `<!-- v5-history -->` 区间里的工具名是叙事, 不是契约。"""
    f = tmp_path / "notes.py"
    f.write_text(
        "# 过去修了 v5_latest_thought (现在已被 v5_self 吸收)\n"
        "call_tool('v5_latest_thought')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SC, "_read", lambda rel: f.read_text(encoding="utf-8"))
    hits = SC._scan("notes.py")
    assert hits == {"v5_latest_thought": [2]}, f"只该报第 2 行(代码), 实际: {hits}"

    md = tmp_path / "doc.md"
    md.write_text(
        "用 `v5_self_model` 查身份。\n"
        "<!-- v5-history: 以下为历史 -->\n"
        "过去修了 `v5_relationship`。\n"
        "<!-- /v5-history -->\n"
        "用 `v5_relationship` 查关系。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(SC, "_read", lambda rel: md.read_text(encoding="utf-8"))
    hits = SC._scan("doc.md")
    assert hits == {"v5_self_model": [1], "v5_relationship": [5]}, (
        f"哨兵区间内(L3)不该报, 区间外(L5)要报, 实际: {hits}"
    )


# ── 6) 非工具名的白名单 ─────────────────────────────────────────────
def test_non_tool_identifiers_are_allowlisted():
    """v5_call / v5_kind 等长得像工具名但不是工具, 别制造假阳性。"""
    for name in ("v5_call", "v5_kind", "v5_key", "v5_memory_id", "v5_project", "v5__v5_recall"):
        assert not SC._is_tool_like(name), f"{name} 应被白名单排除"
    assert SC._is_tool_like("v5_memory_search")
    assert SC._is_tool_like("v5_typo_tool")


# ── 7) 身份文件缺失不许静默通过 ──────────────────────────────────────
def test_missing_identity_file_is_reported_not_silently_skipped(monkeypatch, capsys):
    """2026-08-30 修掉的**闸门自身** bug: 缺文件 → `_scan()` 返回 {} →
    零命中 → 照样打印「✅ 可以切 slim」。

    为什么这条必须守: `data/soul/SOUL.md` 被 `.gitignore:40` 的 `data/**`
    排除, 从不进 git (项目约定: 用户状态不入库)。换机器/新克隆时它必然不
    存在, 而它恰恰是 slim 下最容易翻车的文件 —— 里面写的旧工具名会指向
    没注册的工具, 要等模型真调用时才发现, 测试永远抓不到。
    静默放行 = 闸门在这类环境下形同虚设。
    """
    monkeypatch.setattr(SC, "IDENTITY_FILES", ("data/soul/__no_such_file__.md",))
    res = SC.run()

    # 缺文件不阻塞 (文件可能确实不需要存在), 但**必须**被报告
    assert res["ok"] is True
    absent = [w for w in res["warn"] if w["kind"] == "identity_absent"]
    assert len(absent) == 1, (
        f"缺失的身份文件没被报告 —— 静默放行又回来了: {res['warn']}"
    )
    assert absent[0]["file"] == "data/soul/__no_such_file__.md"

    # 结论句不许再说无条件 OK
    SC._render(res, show_docs=False)
    out = capsys.readouterr().out
    assert "没验证过" in out, f"结论句没提示有文件未验证过:\n{out}"
    assert "✅ 可以切 slim" not in out


def test_identity_files_present_yield_no_absent_warning():
    """当前环境两个身份文件都在, 不该报 identity_absent。"""
    res = SC.run()
    assert not [w for w in res["warn"] if w["kind"] == "identity_absent"], (
        f"身份文件明明在却报缺失: {res['warn']}"
    )
