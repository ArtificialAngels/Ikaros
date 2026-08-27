"""v5_recall — 预算感知 + 跨輪去重的召回工具 (OpenViking context_assembler 借鉴) — F1+F2+F4.

unified_retrieve 返回 top-k 原始结果, 本工具在它之上加三层:
  1. 跨輪去重 (recall_ledger): 近 dedup_turns 轮展示过正文的记忆跳过;
  2. token 预算装配 (recall_budget): 广度后深度 + per-entry cap + 降级不截断;
  3. 轨迹 stats (F4): candidates/placed/dropped/deduped/cooled/tier_counts/fill.

每次调用 = 一轮 (advance_turn), 记录本轮展示 (bare-URI 不计), 供下一轮去重.
"""

from __future__ import annotations

from memory_v5.tools.utils import safe_tool, dumps, answer


def _render_block(entries) -> str:
    """紧凑可读渲染 (每条: [id] type score detail)."""
    if not entries:
        return "(无相关记忆)"
    lines = []
    for e in entries:
        lines.append(f"[#{e.id}] ({e.type}, score={e.score:.2f}, {e.detail}) {e.text}")
    return "\n".join(lines)


@safe_tool
def v5_recall(
    query: str,
    *,
    max_tokens: int = 1600,
    top_k: int = 20,
    session_id: str = "default",
    dedup_turns: int = 5,
    include_dsh_only: bool = True,
) -> str:
    """Token-bounded, dedup-aware recall. Returns assembled context + trajectory stats.

    Paths:
      1. unified_retrieve (语义三路融合 + 图补路 + Vault) over-fetch top_k=20
      2. recall_ledger 排除近 dedup_turns 轮展示过正文的记忆 (bare-URI 不冷却)
      3. recall_budget 广度后深度装配到 max_tokens (降级不截断)
      4. 记录本轮展示 (bare-URI 不计) 供下一轮去重
    Always returns JSON; never raises.
    """
    if not query or not query.strip():
        return answer("空查询", {"context": "", "stats": {}})

    from memory_v5.memory_retrieval import unified_retrieve
    from memory_v5.recall_ledger import RecallLedger
    from memory_v5.recall_budget import Candidate, plan_entries

    # 0. 推进一轮 (本次召回 = 新一轮)
    ledger = RecallLedger(session_id=session_id, dedup_turns=dedup_turns)
    cur_turn = ledger.advance_turn()

    # 1. 检索 (over-fetch 给预算留头)
    try:
        results = unified_retrieve(query, top_k=top_k, include_dsh_only=include_dsh_only)
    except Exception as exc:  # noqa: BLE001
        return answer(f"召回失败: {exc}", {"context": "", "stats": {"error": str(exc)}})

    retrieved = len(results)

    # 2. 跨轮去重: 排除近 N 轮展示过正文的记忆
    cooled_set = ledger.cooled_ids()
    fresh = [r for r in results if str(r.get("id")) not in cooled_set]
    cooled_n = retrieved - len(fresh)

    # 3. 预算装配 (广度后深度 + 降级不截断)
    cands = [
        Candidate(
            id=str(r.get("id") or ""),
            content=r.get("content") or "",
            type=r.get("type") or "fact",
            score=float(r.get("score") or 0.0),
            tags=r.get("tags") or "",
        )
        for r in fresh
    ]
    plan = plan_entries(cands, max_tokens)

    # 4. 记录本轮展示 (bare-URI 不计 = 输给预算, 下轮可正常展示)
    served = [(e.id, e.detail != "uri") for e in plan.entries]
    ledger.record_served(served)

    # 5. 渲染 + stats
    block = _render_block(plan.entries)
    stats = {
        "retrieved": retrieved,
        "cooled": cooled_n,
        "turn": cur_turn,
        **plan.stats,
    }
    return answer(
        f"召回 {len(plan.entries)} 条 (检索 {retrieved}, 冷却 {cooled_n}, 预算 {max_tokens}t 用 {plan.stats['used_tokens']}t)",
        {"context": block, "stats": stats},
    )
