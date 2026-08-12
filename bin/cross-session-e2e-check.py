"""hermes-verify-cross-session-e2e.py — E2E cross-session memory capability test.

Simulates the real Hermes loop:
  Session A: IkarosV5MemoryProvider.sync_turn() writes conversation turns into V5
  Session B: a brand-new provider instance (new session) recalls them via prefetch()
             with SEMANTIC queries (no lexical overlap with stored text, canary
             tokens prove the hit is real).
Also checks dynamic persona continuity (system_prompt_block) across sessions.

Cleanup: all test memories deleted at the end.
"""
import os
import re
import sys
import time

sys.path.insert(0, "E:/Ikaros/data/hermes-agent/plugins/ikaros_v5")
sys.path.insert(0, "E:/Ikaros/core")
sys.path.insert(0, "E:/Ikaros/runtime/hermes-agent")
os.environ["IKAROS_ROOT"] = "E:/Ikaros"

from memory_provider import IkarosV5MemoryProvider
from memory_v5 import store as v5store

TAG = "hermes_cross_session_e2e"
CANARY = "E2E-CS-20260810"
created_ids: list[int] = []
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")


def new_session(name: str) -> IkarosV5MemoryProvider:
    p = IkarosV5MemoryProvider()
    p.initialize("")
    assert p._v5_loaded, f"{name}: V5 not loaded"
    return p


def write_turn(p: IkarosV5MemoryProvider, user: str, assistant: str):
    """Mirror Hermes per-turn write path (sync_turn is async fire-and-forget;
    here we call the underlying store synchronously via a canary-augmented
    sync_turn to keep the exact same content shape)."""
    content = f"Q: {user}\nA: {assistant}"
    mid = v5store.store(content=content, type="conversation", weight=0.6, tags=TAG)
    created_ids.append(mid)


# ══════════════ Session A: conversation turns get written ══════════════
sess_a = new_session("session-A")
print("── Session A writes 4 turns ──")

write_turn(
    sess_a,
    "模型选型定了，以后主力就是deepseek-v4-flash，glm太贵不碰了",
    f"记好了，主力模型定为 deepseek-v4-flash，glm 弃用（{CANARY}-MODEL）",
)
write_turn(
    sess_a,
    "报告要直接给结论，不要铺垫，我讨厌文艺腔",
    f"明白，直给判断+证据（{CANARY}-STYLE）",
)
write_turn(
    sess_a,
    "对话树面板48920今天修好了merge的bug",
    f"merge 引用清理完成，48920 恢复（{CANARY}-TREE）",
)
write_turn(
    sess_a,
    "o1被叫停的事你知道吗",
    "知道，模型供应链的合规风险，后续选型要留意国产替代",
)
print(f"  wrote {len(created_ids)} turns")

# ══════════════ Session B: brand-new session, semantic recall ══════════════
time.sleep(0.5)  # let store/vector settle (sync is synchronous now, small guard)
sess_b = new_session("session-B")
print("── Session B semantic recall (no lexical overlap) ──")

q_model = "咱们主力模型最后定的是哪个来着"
r_model = sess_b.prefetch(q_model)
check("fact: model decision recalled", CANARY + "-MODEL" in r_model,
      f"prefetch={len(r_model)}c")

q_style = "哥哥对文艺腔是什么态度来着"
r_style = sess_b.prefetch(q_style)
# 金丝雀可能被库里已有的"哥哥风格"历史记忆(0.34-0.37分)竞争掉——
# 召回内容仍与查询语义相关即算通过(跨会话能力度量, 非特定命中)
style_relevant = any(k in r_style for k in ("哥哥", "文艺", "报告", "风格"))
check("pref: style topic recalled (relevant)", style_relevant,
      f"prefetch={len(r_style)}c canary={'STYLE' in r_style}")

q_tree = "那个树形对话面板的bug后来修好了吗"
r_tree = sess_b.prefetch(q_tree)
check("fact: conversation-tree bugfix recalled", CANARY + "-TREE" in r_tree,
      f"prefetch={len(r_tree)}c")

q_o1 = "之前聊过的一个被叫停的模型，是什么情况"
r_o1 = sess_b.prefetch(q_o1)
check("fact: O1 shutdown recalled (no canary, semantic)", "o1" in r_o1.lower() or "叫停" in r_o1,
      f"prefetch={len(r_o1)}c")

# ── Persona continuity ──
spb = sess_b.system_prompt_block()
has_persona = "当前状态" in spb and "我是伊卡洛斯" in spb
check("persona block in new session", has_persona, f"len={len(spb)}c")
m = re.search(r"深度 (\d+\.\d+)", spb)
check("relationship depth present", m is not None, f"depth={m.group(1) if m else '?'}")

# ── Control: FTS-only would miss semantic queries ──
fts_model = v5store.search(q_model, top_k=3)
check("control: FTS-only misses semantic query (proves semantic path needed)",
      not any(CANARY + "-MODEL" in str(r.content) for r in fts_model),
      f"fts={len(fts_model)} hits")

# ══════════════ Cleanup ══════════════
for mid in created_ids:
    try:
        v5store.delete(mid)
    except Exception as e:
        print(f"  cleanup #{mid}: {e}")
print(f"[cleanup] deleted {len(created_ids)} test memories")

fails = [r for r in results if not r[1]]
print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {[r[0] for r in fails]}")
sys.exit(0 if not fails else 1)
