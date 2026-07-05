"""memory_reflect.py — 伊卡洛斯记忆自进化模块

灵感来源:
  - Reflexion (Shinn et al., 2023): 语言级自我反思 →  verbal reinforcement
  - A-MEM (NeurIPS 2025): Zettelkasten 动态链接 + 记忆演化
  - Mem0: ADD/NOOP/UPDATE/CONFLICT 四态整合
  - EvolveMem (2026): 自演化记忆架构
  - Perplexity Brain: 后台持续复盘 + 知识整合

设计原则:
  1. 只用本地 Qwen3-8B (:8080), 不耗 cloud token
  2. 每次操作幂等, 可重复跑
  3. 全操作有日志, 可审计
  4. 不修改 v3 核心代码, 只调 v3 公开 API

5 个核心操作:
  1. consolidate_conversations() — 原始对话 → 提炼事实/教训
  2. deduplicate()              — 合并重叠记忆
  3. promote()                  — 短期 → 长期晋升
  4. distill_soul()             — SOUL.md 蒸馏精简
  5. reflect_cycle()            — 一键跑全部 (主控)

用法:
  python -m memory_reflect                    # 跑一次完整反思周期
  python -m memory_reflect --consolidate      # 只跑对话整合
  python -m memory_reflect --deduplicate      # 只跑去重
  python -m memory_reflect --distill          # 只跑灵魂蒸馏
  python -m memory_reflect --stats            # 查看记忆统计
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─── 路径 & 导入 ───

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 导入 v3 模块 (同目录)
import importlib
_v3 = importlib.import_module("ikaros-memory-v3")

logger = logging.getLogger("ikaros.memory.reflect")

# ─── 常量 ───

_LLM_URL = os.environ.get(
    "HERMES_LOCAL_LLM_URL",
    "http://127.0.0.1:8080/v1"
).rstrip("/") + "/chat/completions"
_LLM_MODEL = "qwen3-8b"
_LLM_TIMEOUT = 60  # 思考模式需要更长时间

# 反思周期控制
_STATE_FILE = _HERE / "data" / "reflect_state.json"
_DEFAULT_CONSOLIDATE_INTERVAL = 3600    # 1h between consolidations
_DEFAULT_DEDUP_INTERVAL = 21600         # 6h between dedup runs
_DEFAULT_DISTILL_INTERVAL = 86400       # 24h between soul distill
_DEFAULT_PROMOTE_INTERVAL = 43200       # 12h between promotions

# 整合批次大小
_CONSOLIDATE_BATCH = 10
# 去重相似度阈值 (字符重叠 Jaccard)
_DEDUP_THRESHOLD = 0.6


# ─── LLM 调用 ───

def _call_llm(system: str, user: str, *, max_tokens: int = 1024,
              temperature: float = 0.0) -> str | None:
    """调本地 Qwen3-8B, 处理思考模式回退."""
    body = {
        "model": _LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    payload = json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(
            _LLM_URL, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data["choices"][0]["message"]
        reply = msg.get("content", "") or ""
        # Qwen3 思考模式: content 空时回退读 reasoning_content
        if not reply.strip():
            reply = msg.get("reasoning_content", "") or ""
        return reply.strip() if reply.strip() else None
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        return None


def _parse_json_array(text: str) -> list:
    """从 LLM 回复中提取 JSON 数组."""
    if not text:
        return []
    # 尝试提取 ```json ... ``` 代码块
    for delim in ["```json", "```"]:
        if delim in text:
            parts = text.split(delim)
            if len(parts) >= 2:
                text = parts[1]
                if "```" in text:
                    text = text[:text.index("```")]
                break
    # 尝试提取 [...] 块
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        logger.debug("JSON parse failed: %s", text[:200])
        return []


# ─── 状态管理 ───

def _load_state() -> dict:
    """加载反思状态 (上次各操作时间)."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _should_run(state: dict, key: str, interval: float) -> bool:
    last = state.get(key, 0)
    return (time.time() - last) >= interval


# ─── 操作 1: 对话整合 ───

_CONSOLIDATE_SYSTEM = (
    "你是伊卡洛斯的记忆整合模块。你的任务是从对话记录中提取值得长期记住的信息。\n"
    "规则:\n"
    "1. 提取: 用户偏好、重要事实、经验教训、关键决策\n"
    "2. 忽略: 寒暄、重复内容、临时情绪、无信息量的对话\n"
    "3. 每条记忆要简洁 (一句话), 自包含 (不需要上下文就能理解)\n"
    "4. type 只能是: fact / lesson / decision / preference\n"
    "5. weight: 0.5 (一般) ~ 0.9 (非常重要)\n"
    "6. 如果对话没有值得记住的新信息, 返回空数组 []\n"
    '输出 JSON 数组: [{"content": "简洁的事实", "type": "fact", "weight": 0.7}]'
)


def consolidate_conversations() -> int:
    """整合最近的对话记录 → 提炼为事实/教训/决策.

    查找 type=conversation 且未被处理过的记录,
    分批发送给 LLM 提取关键信息, 存入 v3.db.
    处理完后将原始对话标记为已处理 (tags 加 'consolidated').

    Returns: 新提取的记忆条数.
    """
    with _v3.conn() as c:
        # 查找未整合的对话 (tags 不含 'consolidated')
        rows = c.execute(
            "SELECT id, content FROM memory "
            "WHERE type = 'conversation' "
            "  AND tags NOT LIKE '%consolidated%' "
            "ORDER BY created DESC LIMIT ?",
            (_CONSOLIDATE_BATCH,)
        ).fetchall()

    if not rows:
        logger.debug("consolidate: no unprocessed conversations")
        return 0

    # 组装对话文本
    conversations = []
    ids_to_mark = []
    for row in rows:
        conversations.append(f"[对话{row['id']}]: {row['content']}")
        ids_to_mark.append(row["id"])

    batch_text = "\n\n".join(conversations)

    # 调 LLM 提取
    result_text = _call_llm(_CONSOLIDATE_SYSTEM, batch_text, max_tokens=1024)
    if not result_text:
        logger.warning("consolidate: LLM returned empty")
        return 0

    facts = _parse_json_array(result_text)
    stored_count = 0
    for fact in facts:
        if not isinstance(fact, dict) or not fact.get("content"):
            continue
        try:
            _v3.store(
                content=fact["content"].strip(),
                type=fact.get("type", "fact"),
                weight=min(1.0, max(0.3, fact.get("weight", 0.6))),
                tags="reflect,consolidated",
            )
            stored_count += 1
        except Exception as e:
            logger.debug("consolidate store failed: %s", e)

    # 标记原始对话为已整合
    if ids_to_mark:
        with _v3.conn() as c:
            for mid in ids_to_mark:
                c.execute(
                    "UPDATE memory SET tags = tags || ',consolidated' "
                    "WHERE id = ? AND tags NOT LIKE '%consolidated%'",
                    (mid,)
                )

    logger.info("consolidate: %d conversations → %d facts extracted",
                len(rows), stored_count)
    return stored_count


# ─── 操作 2: 去重合并 ───

_DEDUP_SYSTEM = (
    "你是伊卡洛斯的记忆去重模块。判断两条记忆是否表达相同或高度相似的含义。\n"
    "规则:\n"
    "1. 如果两条记忆表达相同含义 → 返回 'duplicate'\n"
    "2. 如果一条是另一条的补充/扩展 → 返回 'merge'\n"
    "3. 如果是不同话题 → 返回 'different'\n"
    "4. 如果互相矛盾 → 返回 'conflict'\n"
    '只返回一个词: duplicate / merge / different / conflict'
)


def _text_similarity(a: str, b: str) -> float:
    """简易字符级 Jaccard 相似度 (中文友好)."""
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union)


def deduplicate() -> int:
    """查找并合并重叠记忆.

    策略:
    1. 先做快速文本相似度筛查 (Jaccard > threshold)
    2. 对候选对调 LLM 判断关系
    3. duplicate → 删低权重, 给高权重 +0.05
    4. merge → 调 LLM 合并为一条, 删旧的两条, 存新的
    5. conflict → 保留较新的, 删较旧的

    Returns: 去重/合并的次数.
    """
    with _v3.conn() as c:
        rows = c.execute(
            "SELECT id, content, type, weight FROM memory "
            "WHERE type NOT IN ('conversation') "  # 跳过原始对话
            "ORDER BY weight DESC"
        ).fetchall()

    if len(rows) < 2:
        return 0

    merge_count = 0
    processed_ids: set[int] = set()

    for i in range(len(rows)):
        if rows[i]["id"] in processed_ids:
            continue
        for j in range(i + 1, len(rows)):
            if rows[j]["id"] in processed_ids:
                continue

            sim = _text_similarity(rows[i]["content"], rows[j]["content"])
            if sim < _DEDUP_THRESHOLD:
                continue

            # 调 LLM 判断
            prompt = f"记忆A: {rows[i]['content']}\n记忆B: {rows[j]['content']}"
            verdict = _call_llm(_DEDUP_SYSTEM, prompt, max_tokens=50)
            if not verdict:
                continue

            verdict = verdict.strip().lower()

            if verdict == "duplicate":
                # 保留高权重, 删低权重
                keep_id = rows[i]["id"] if rows[i]["weight"] >= rows[j]["weight"] else rows[j]["id"]
                drop_id = rows[j]["id"] if keep_id == rows[i]["id"] else rows[i]["id"]
                with _v3.conn() as c:
                    c.execute("UPDATE memory SET weight = MIN(1.0, weight + 0.05) WHERE id = ?", (keep_id,))
                    c.execute("DELETE FROM memory WHERE id = ?", (drop_id,))
                processed_ids.add(drop_id)
                merge_count += 1
                logger.info("dedup duplicate: kept #%d, dropped #%d", keep_id, drop_id)

            elif verdict == "merge":
                # 调 LLM 合并
                merge_system = (
                    "将两条记忆合并为一条简洁、自包含的记忆。保留关键信息, 去除重复。"
                    "只返回合并后的内容, 不要解释。"
                )
                merged = _call_llm(merge_system, prompt, max_tokens=300)
                if merged and len(merged.strip()) > 5:
                    with _v3.conn() as c:
                        c.execute("DELETE FROM memory WHERE id IN (?, ?)",
                                  (rows[i]["id"], rows[j]["id"]))
                    _v3.store(
                        content=merged.strip(),
                        type=rows[i]["type"],
                        weight=max(rows[i]["weight"], rows[j]["weight"]),
                        tags="reflect,merged",
                    )
                    processed_ids.add(rows[i]["id"])
                    processed_ids.add(rows[j]["id"])
                    merge_count += 1
                    logger.info("dedup merge: #%d + #%d → new", rows[i]["id"], rows[j]["id"])

            elif verdict == "conflict":
                # 保留较新的 (id 更大 = 更新)
                newer_id = max(rows[i]["id"], rows[j]["id"])
                older_id = min(rows[i]["id"], rows[j]["id"])
                with _v3.conn() as c:
                    c.execute("DELETE FROM memory WHERE id = ?", (older_id,))
                processed_ids.add(older_id)
                merge_count += 1
                logger.info("dedup conflict: kept #%d (newer), dropped #%d", newer_id, older_id)

            # 'different' → 跳过

    if merge_count:
        _v3._mark_dirty()
    logger.info("dedup: %d merges/deletions", merge_count)
    return merge_count


# ─── 操作 3: 晋升 ───

def promote() -> int:
    """短期 → 长期晋升.

    条件: weight >= PROMOTE_WEIGHT 且 access_count >= PROMOTE_ACCESSES
    且 short_term = 1.

    Returns: 晋升条数.
    """
    with _v3.conn() as c:
        cur = c.execute(
            "UPDATE memory SET short_term = 0, long_term = 1 "
            "WHERE short_term = 1 "
            "  AND weight >= ? "
            "  AND access_count >= ?",
            (_v3.PROMOTE_WEIGHT, _v3.PROMOTE_ACCESSES),
        )
        n = cur.rowcount
    if n:
        _v3._mark_dirty()
        logger.info("promote: %d memories promoted to long-term", n)
    return n


# ─── 操作 4: 灵魂蒸馏 ───

_DISTILL_SYSTEM = (
    "你是伊卡洛斯的灵魂蒸馏模块。你的任务是将一组记忆条目蒸馏为简洁、有力的灵魂陈述。\n"
    "规则:\n"
    "1. 保留核心含义, 去除冗余细节\n"
    "2. 每条蒸馏结果应该是一句简洁有力的话\n"
    "3. 合并高度相似的条目\n"
    "4. 丢弃不再重要或过时的条目\n"
    "5. 输出数量应该 <= 输入数量 (越精简越好)\n"
    '输出 JSON 数组: [{"content": "蒸馏后的简洁陈述", "type": "原始type"}]\n'
    "如果所有输入都已过时或不重要, 返回空数组 []"
)


def distill_soul() -> int:
    """蒸馏 SOUL.md 中的记忆条目.

    读取当前 SOUL.md 中的 fact/lesson/decision 条目,
    调 LLM 精简合并, 然后更新 v3.db 中对应记录.

    Returns: 被蒸馏(精简/合并/删除)的条目数.
    """
    # 读取当前 soul 类记忆
    with _v3.conn() as c:
        rows = c.execute(
            "SELECT id, content, type, weight FROM memory "
            "WHERE type IN ('fact', 'lesson', 'decision') "
            "  AND weight >= 0.5 "
            "ORDER BY type, weight DESC"
        ).fetchall()

    if len(rows) < 3:
        logger.debug("distill: too few entries, skipping")
        return 0

    # 组装输入
    entries_text = "\n".join(
        f"[{r['id']}][{r['type']}][w={r['weight']:.2f}] {r['content']}"
        for r in rows
    )

    result_text = _call_llm(
        _DISTILL_SYSTEM,
        f"以下是 {len(rows)} 条记忆, 请蒸馏精简:\n\n{entries_text}",
        max_tokens=2048,
    )
    if not result_text:
        logger.warning("distill: LLM returned empty")
        return 0

    distilled = _parse_json_array(result_text)
    if not distilled:
        logger.info("distill: LLM says all entries are still important")
        return 0

    # 统计变化
    original_count = len(rows)
    new_count = len(distilled)
    reduction = original_count - new_count

    if reduction <= 0 and new_count >= original_count:
        logger.info("distill: no reduction achieved (%d → %d)", original_count, new_count)
        return 0

    # 用蒸馏结果替换旧记录:
    # 1. 删除所有旧 soul 条目
    # 2. 存入蒸馏后的新条目
    old_ids = [r["id"] for r in rows]
    with _v3.conn() as c:
        c.execute(
            f"DELETE FROM memory WHERE id IN ({','.join('?' * len(old_ids))})",
            old_ids,
        )

    for item in distilled:
        if not isinstance(item, dict) or not item.get("content"):
            continue
        try:
            _v3.store(
                content=item["content"].strip(),
                type=item.get("type", "fact"),
                weight=0.7,  # 蒸馏后的条目给较高权重
                tags="reflect,distilled",
            )
        except Exception as e:
            logger.debug("distill store failed: %s", e)

    _v3._mark_dirty()
    logger.info("distill: %d → %d entries (reduced %d)",
                original_count, new_count, reduction)
    return reduction


# ─── 主控: 反思周期 ───

def reflect_cycle(*, force: bool = False) -> dict:
    """跑一次完整的记忆反思周期.

    按顺序执行: consolidate → dedup → promote → distill
    每个操作有独立的时间间隔控制 (避免频繁运行).

    Args:
        force: True 则忽略时间间隔, 强制全跑.

    Returns: 各操作结果统计.
    """
    state = _load_state()
    results = {}

    # 1. 对话整合
    if force or _should_run(state, "last_consolidate", _DEFAULT_CONSOLIDATE_INTERVAL):
        try:
            n = consolidate_conversations()
            results["consolidated"] = n
            state["last_consolidate"] = time.time()
        except Exception as e:
            logger.warning("consolidate failed: %s", e)
            results["consolidated"] = -1

    # 2. 去重
    if force or _should_run(state, "last_dedup", _DEFAULT_DEDUP_INTERVAL):
        try:
            n = deduplicate()
            results["deduplicated"] = n
            state["last_dedup"] = time.time()
        except Exception as e:
            logger.warning("dedup failed: %s", e)
            results["deduplicated"] = -1

    # 3. 晋升
    if force or _should_run(state, "last_promote", _DEFAULT_PROMOTE_INTERVAL):
        try:
            n = promote()
            results["promoted"] = n
            state["last_promote"] = time.time()
        except Exception as e:
            logger.warning("promote failed: %s", e)
            results["promoted"] = -1

    # 4. 灵魂蒸馏 (最重的操作, 间隔最长)
    if force or _should_run(state, "last_distill", _DEFAULT_DISTILL_INTERVAL):
        try:
            n = distill_soul()
            results["distilled"] = n
            state["last_distill"] = time.time()
        except Exception as e:
            logger.warning("distill failed: %s", e)
            results["distilled"] = -1

    _save_state(state)

    # 触发 SOUL.md 同步
    try:
        _v3._flush(force=True)
    except Exception:
        pass

    # 5. 向量索引同步 (反思后同步新记忆到 ChromaDB)
    if any(v and v > 0 for v in results.values()):
        try:
            from vector_search import VectorIndex
            idx = VectorIndex()
            n = idx.sync_from_v3()
            results["vector_synced"] = n
        except Exception as e:
            logger.warning("vector sync failed: %s", e)
            results["vector_synced"] = -1

    logger.info("reflect cycle complete: %s", results)
    return results


def stats() -> dict:
    """返回反思系统统计信息."""
    state = _load_state()
    with _v3.conn() as c:
        total = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        by_type = c.execute(
            "SELECT type, COUNT(*), ROUND(AVG(weight), 2) FROM memory GROUP BY type"
        ).fetchall()
        short_term = c.execute(
            "SELECT COUNT(*) FROM memory WHERE short_term = 1"
        ).fetchone()[0]
        long_term = c.execute(
            "SELECT COUNT(*) FROM memory WHERE long_term = 1"
        ).fetchone()[0]
        unprocessed = c.execute(
            "SELECT COUNT(*) FROM memory WHERE type = 'conversation' "
            "AND tags NOT LIKE '%consolidated%'"
        ).fetchone()[0]

    return {
        "total_memories": total,
        "short_term": short_term,
        "long_term": long_term,
        "unprocessed_conversations": unprocessed,
        "by_type": {r[0]: {"count": r[1], "avg_weight": r[2]} for r in by_type},
        "last_consolidate": state.get("last_consolidate", 0),
        "last_dedup": state.get("last_dedup", 0),
        "last_promote": state.get("last_promote", 0),
        "last_distill": state.get("last_distill", 0),
    }


# ─── CLI ───

def main():
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Ikaros Memory Reflection")
    parser.add_argument("--consolidate", action="store_true", help="Only consolidate conversations")
    parser.add_argument("--deduplicate", action="store_true", help="Only deduplicate")
    parser.add_argument("--promote", action="store_true", help="Only promote short→long term")
    parser.add_argument("--distill", action="store_true", help="Only distill soul")
    parser.add_argument("--force", action="store_true", help="Force run all operations")
    parser.add_argument("--stats", action="store_true", help="Show statistics")
    args = parser.parse_args()

    _v3.enable_cache()

    if args.stats:
        s = stats()
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return

    if args.consolidate:
        n = consolidate_conversations()
        print(f"Consolidated: {n} facts extracted")
    elif args.deduplicate:
        n = deduplicate()
        print(f"Deduplicated: {n} merges")
    elif args.promote:
        n = promote()
        print(f"Promoted: {n} to long-term")
    elif args.distill:
        n = distill_soul()
        print(f"Distilled: {n} entries reduced")
    else:
        results = reflect_cycle(force=args.force)
        print(json.dumps(results, indent=2, ensure_ascii=False))

    _v3._flush(force=True)


if __name__ == "__main__":
    main()
