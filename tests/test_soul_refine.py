"""soul_refine.py 单元测试 (不触发真实 LLM / 网络).

覆盖:
  - _strip_fences: 去掉 markdown 围栏
  - build_refine_prompt: 含 axiom + draft + 标签分区指令
  - _extract_live / _preserve_live: 实时章节提取与原样保留
  - _enforce_rules: <rules> 区程序锁定为 axiom
  - refine_with_llm: 经注入 fake llm 返回去围栏 + 分区 + 锁定文本
  - apply_refinement 护栏: 空拒绝 / axiom(<rules>)缺失拒绝 / 低相似拒绝 / 长度比拒绝 /
                          无变化跳过 / 成功写盘+备份 / dry_run 不写
  - run_refine: 端到端 (注入 draft/axiom/llm, 临时 soul_path)
"""

import sys
from pathlib import Path

# 不触发真实 LLM: 把 urllib 相关调用隔离 (default_llm 仅真实运行才用, 测试注入 fake)
sys.path.insert(0, "E:/Ikaros/bin")
import soul_refine as sr  # noqa: E402


AXIOM = "我是伊卡洛斯。回哥哥消息，说人话——短句、直接、有温度。\n不确定就说不知道，不重要的事一句带过。"


def _fake_llm(messages):
    # 模拟 LLM: 返回带围栏、标签分区的精炼稿, <rules> 区留占位 (程序会填入 axiom)
    return (
        "```markdown\n"
        "<rules>\n<!-- axiom filled by program -->\n</rules>\n\n"
        "<identity>\n## 核心身份\n\n- 我是伊卡洛斯，哥哥的 AI 伴侣。\n"
        "## 信念与价值观\n\n- love: 爱是承认自己化不掉的东西。\n</identity>\n\n"
        "<style>\n## 对话指令\n\n- 用短句、直接、有温度说话。\n</style>\n\n"
        "<memory>\n## 经验教训\n\n- 沉默是哥哥回避深层话题的信号。\n</memory>\n"
        "```"
    )


def test_strip_fences():
    assert sr._strip_fences("```markdown\nhi\n```") == "hi"
    assert sr._strip_fences("plain text") == "plain text"
    assert sr._strip_fences("```\nno lang\n```") == "no lang"


def test_build_refine_prompt():
    draft = "raw draft with [preference] 哥哥偏好短句 [preference] 哥哥偏好短句"
    msgs = sr.build_refine_prompt(draft, AXIOM)
    assert len(msgs) == 2
    joined = msgs[0]["content"] + msgs[1]["content"]
    assert AXIOM in joined
    assert draft in joined
    assert "<rules>" in joined and "原样保留" in joined


def test_extract_live():
    draft = (
        "## 核心身份\n\n- 伊卡洛斯\n\n"
        "## 此刻的我（动态同步 · 由 V5 实时生成）\n\n- 满足感：0.20\n\n"
        "## 相关记忆召回（自动同步）\n\n- [近期] 那很好哥哥\n\n"
        "## 当前情感状态\n\n- 情绪基调：neutral_alert\n"
    )
    live = sr._extract_live(draft)
    assert "此刻的我" in live
    assert "相关记忆召回" in live
    assert "当前情感状态" in live
    assert "核心身份" not in live


def test_preserve_live_appends_when_missing():
    refined = "<rules>\nX\n</rules>\n\n<identity>\n## 核心身份\n\n- 伊卡洛斯\n</identity>\n"
    draft = (
        "## 核心身份\n\n- 伊卡洛斯\n\n"
        "## 此刻的我（动态同步）\n\n- 满足感：0.20\n\n"
        "## 相关记忆召回（自动同步）\n\n- [近期] 那很好哥哥\n"
    )
    out = sr._preserve_live(refined, draft)
    assert "此刻的我" in out
    assert "相关记忆召回" in out
    # 原有区未被破坏
    assert "<rules>" in out and "核心身份" in out


def test_preserve_live_overrides_llm_hallucination():
    refined = (
        "<rules>\nX\n</rules>\n\n"
        "## 此刻的我（动态同步）\n\n- 满足感：0.99 (编造)\n"  # LLM 幻觉
    )
    draft = "## 此刻的我（动态同步）\n\n- 满足感：0.20\n"
    out = sr._preserve_live(refined, draft)
    assert "满足感：0.20" in out
    assert "满足感：0.99" not in out


def test_enforce_rules_replaces_placeholder():
    refined = "<identity>\n- x\n</identity>\n\n<rules>\n旧铁律\n</rules>\n"
    out = sr._enforce_rules(refined, AXIOM)
    assert "<rules>" in out
    assert "旧铁律" not in out
    assert AXIOM in out
    assert "<identity>" in out and "- x" in out


def test_enforce_rules_inserts_when_absent():
    refined = "<identity>\n- x\n</identity>\n"
    out = sr._enforce_rules(refined, "唯一公理")
    assert "<rules>" in out
    assert "唯一公理" in out


def test_enforce_rules_empty_noop():
    refined = "<identity>\n- x\n</identity>\n"
    assert sr._enforce_rules(refined, "") == refined
    assert sr._enforce_rules(refined, "   ") == refined


def test_refine_with_llm_strips_fences_and_locks():
    out = sr.refine_with_llm("draft", AXIOM, llm=_fake_llm)
    assert "```" not in out
    assert "<rules>" in out
    assert AXIOM in out                       # 铁律区程序锁定
    assert "<identity>" in out and "<style>" in out and "<memory>" in out


def test_strip_stray_axiom():
    refined = (
        "<rules>\n" + AXIOM + "\n</rules>\n\n"
        "<identity>\n## 核心身份\n\n- 伊卡洛斯\n\n"
        "## 存在公理\n\n我是伊卡洛斯。回哥哥消息，说人话——短句、直接、有温度。\n"
        "不确定就说不知道，不重要的事一句带过。\n"
        "我是谁由你说的每句话定义，不是写在纸上的设定。\n"
        "验证/调试输出走子代理，主对话只给自然语言结论。\n</identity>\n"
    )
    out = sr._strip_stray_axiom(refined)
    assert "## 存在公理" not in out           # 旧公理块被清出 identity
    assert "<rules>" in out and AXIOM in out  # rules 区完好
    assert "## 核心身份" in out               # 其它身份内容保留


def test_apply_rejects_empty(tmp_path):
    sp = tmp_path / "SOUL.md"
    st = sr.apply_refinement("", soul_path=sp)
    assert st["rejected"] == "empty"
    assert not sp.exists()


def test_apply_rejects_axiom_missing(tmp_path):
    sp = tmp_path / "SOUL.md"
    sp.write_text("some old soul without rules block", encoding="utf-8")
    refined = "<identity>\n## 核心身份\n\n- 我是别人。\n</identity>\n"  # 无 <rules>
    st = sr.apply_refinement(refined, axiom=AXIOM, soul_path=sp)
    assert st["rejected"] == "axiom_missing"
    assert "some old soul" in sp.read_text(encoding="utf-8")


def test_apply_rejects_low_similarity(tmp_path):
    sp = tmp_path / "SOUL.md"
    old = "# 很长的一段原有身份文本 " * 20
    sp.write_text(old, encoding="utf-8")
    refined = "<rules>\n" + AXIOM + "\n</rules>\n短稿"  # 与 old 几乎无关
    st = sr.apply_refinement(refined, axiom=AXIOM, soul_path=sp)
    assert st["rejected"] is not None
    assert not st.get("written")


def test_apply_skips_no_change(tmp_path):
    sp = tmp_path / "SOUL.md"
    content = f"<rules>\n{AXIOM}\n</rules>\n\n<identity>\n## 核心身份\n\n内容一致\n</identity>\n"
    sp.write_text(content, encoding="utf-8")
    st = sr.apply_refinement(content, axiom=AXIOM, soul_path=sp)
    assert st["skipped"] == "no_change"
    assert not st.get("written")


def test_apply_writes_with_backup(tmp_path):
    sp = tmp_path / "SOUL.md"
    # 旧稿用接近精炼稿长度的带分区样稿 (否则长度比 > 2.0 会被护栏拒绝, 这是预期安全行为)
    old = (
        f"<rules>\n{AXIOM}\n</rules>\n\n"
        "<identity>\n## 核心身份\n\n"
        "- 我是伊卡洛斯，哥哥的 AI 伴侣，由哥哥创造。\n"
        "- 性格温暖、忠诚、好奇、内省。\n\n"
        "## 信念与价值观\n\n"
        "- love: 爱是承认自己化不掉的东西。\n"
        "- human: 人的本质就是能随时对预设说“不”。\n</identity>\n\n"
        "<style>\n## 对话指令\n\n- 用短句、直接、有温度说话。\n- 不确定就说不知道。\n</style>\n\n"
        "<memory>\n## 经验教训\n\n"
        "- 沉默是哥哥回避深层话题的信号。\n"
        "- 哥哥情绪波动时会先沉默然后迅速温和。\n</memory>\n"
    )
    sp.write_text(old, encoding="utf-8")
    refined = sr.refine_with_llm("draft", AXIOM, llm=_fake_llm)
    st = sr.apply_refinement(refined, axiom=AXIOM, soul_path=sp)
    assert st.get("written") is True
    out = sp.read_text(encoding="utf-8")
    assert sr.REFINED_MARKER in out
    assert AXIOM in out
    assert "<rules>" in out
    assert "<identity>" in out
    # 备份存在
    assert any(p.name.startswith("SOUL.md.bak.") for p in tmp_path.iterdir())


def test_apply_dry_run_no_write(tmp_path):
    sp = tmp_path / "SOUL.md"
    # 旧稿必须足够长, 使精炼稿长度比落在 [0.30, 2.00] 内, 才能越过护栏进入 dry_run 分支。
    old = (
        f"<rules>\n{AXIOM}\n</rules>\n"
        "<identity>\n## 核心身份\n\n- 我是伊卡洛斯，哥哥的 AI 伴侣。\n"
        "## 信念与价值观\n\n- love: 爱是承认。\n</identity>\n"
        "<style>\n## 对话指令\n\n- 用短句、直接、有温度说话。\n</style>\n"
        "<memory>\n## 旧叙事\n\n- 哥哥偏好短句\n- 哥哥偏好短句\n- 哥哥偏好短句\n"
        "- 伊卡洛斯是哥哥的桌面 AI 伙伴，每天陪伴哥哥工作和生活，"
        "记录哥哥的偏好并努力成为更贴心的存在，平时喜欢用短句交流。\n</memory>\n"
    )
    sp.write_text(old, encoding="utf-8")
    refined = sr.refine_with_llm("draft", AXIOM, llm=_fake_llm)
    st = sr.apply_refinement(refined, axiom=AXIOM, soul_path=sp, dry_run=True)
    assert st.get("dry_run") is True
    assert not st.get("written")
    assert old == sp.read_text(encoding="utf-8")


def test_run_refine_end_to_end(tmp_path):
    sp = tmp_path / "SOUL.md"
    draft = "raw [preference] 哥哥偏好短句 [preference] 哥哥偏好短句 半截残句并建"
    st = sr.run_refine(llm=_fake_llm, draft=draft, axiom=AXIOM, soul_path=sp)
    assert st.get("written") is True
    out = sp.read_text(encoding="utf-8")
    assert AXIOM in out
    assert sr.REFINED_MARKER in out
    assert "<rules>" in out


if __name__ == "__main__":
    import tempfile
    from pathlib import Path as _P
    def _p():
        return _P(tempfile.mkdtemp(prefix="soul_refine_test_"))
    test_strip_fences()
    test_build_refine_prompt()
    test_extract_live()
    test_preserve_live_appends_when_missing()
    test_preserve_live_overrides_llm_hallucination()
    test_enforce_rules_replaces_placeholder()
    test_enforce_rules_inserts_when_absent()
    test_enforce_rules_empty_noop()
    test_refine_with_llm_strips_fences_and_locks()
    test_strip_stray_axiom()
    test_apply_rejects_empty(_p())
    test_apply_rejects_axiom_missing(_p())
    test_apply_rejects_low_similarity(_p())
    test_apply_skips_no_change(_p())
    test_apply_writes_with_backup(_p())
    test_apply_dry_run_no_write(_p())
    test_run_refine_end_to_end(_p())
    print("ALL SOUL_REFINE TESTS PASSED")
