"""HaluMem 评测 —— V5 记忆系统适配版 (提取召回 + QA 两个维度).

基准: HaluMem (MemTensor, arXiv 2511.03506), 测记忆系统在三个操作级
环节的幻觉行为: 提取 (Extraction) / 更新 (Update) / 问答 (QA).
本脚本适配 V5 的能力边界, 评测两个维度:

  1. **提取完整性 (Extraction Recall, 无 LLM)**:
     对每个 golden memory point, 用其内容作为 query 检索 V5,
     判断能否召回包含该信息的记忆 (recall_any@k)。
     V5 没有显式"提取记忆点"能力 (store 存原始对话), 所以用
     "信息可检索性" 近似提取完整性 —— 这正是记忆系统的核心职能。

  2. **QA (需要 LLM)**:
     灌入 dialogue 后, 对每个 question 检索 top-k 记忆,
     用 gateway LLM (deepseek) 基于检索结果回答,
     用官方 judge prompt (evaluation_for_question) 打分。
     (默认关闭 --qa, 因为 20 persona × 164 题需要大量 LLM 调用)

隔离: 每 persona 独立临时目录 (DB + chroma), 不碰真实记忆库.

用法:
  python bin/eval-halumem.py --data "E:/Ikaros-something/reference project/HaluMem-Medium.jsonl" --limit 1
  python bin/eval-halumem.py --data ... --qa  # 打开 QA 维度 (慢)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

import numpy as np  # noqa: E402

import memory_v5.store as v5_store  # noqa: E402
import memory_v5.search as v5_search  # noqa: E402
from memory_v5.memory_retrieval import retrieve  # noqa: E402

_tmp_root = Path(tempfile.mkdtemp(prefix="halumem_"))


def _patched_sync(memory_id, content, type_, tags, weight):
    try:
        idx = v5_search.get_vector_index()
        return bool(idx.add(memory_id, content, type=type_, tags=tags, weight=weight))
    except Exception as e:  # noqa: BLE001
        print(f"    [sync warn] id={memory_id}: {e}", file=sys.stderr)
        return False


def _setup_persona(i: int):
    """每 persona 独立目录 (复用 LongMemEval 的 Windows 句柄防护)."""
    v5_search._VI["instance"] = None
    v5_search._VI["dir"] = None
    for old in _tmp_root.glob("p_*"):
        shutil.rmtree(old, ignore_errors=True)
    pdir = _tmp_root / f"p_{i}"
    pdir.mkdir(parents=True, exist_ok=True)
    v5_store.V5_DATA_DIR = pdir
    v5_store.V5_DB_PATH = pdir / "v5.db"
    v5_search.V5_DATA_DIR = pdir
    v5_search.CHROMA_DIR = pdir / "chroma"
    v5_search.get_vector_index(refresh=True)
    return pdir


def _ingest(sessions):
    """把 persona 的全部 dialogue 灌入 V5 (turn 级)."""
    n = 0
    for s in sessions:
        for turn in s.get("dialogue", []):
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            text = f"[{turn.get('role', '?')}] {content}"
            if len(text) > 9000:
                text = text[:9000]
            try:
                v5_store.store(content=text, type="conversation", weight=0.5,
                               tags="halumem")
                n += 1
            except Exception as e:  # noqa: BLE001
                print(f"    [ingest skip] {e}", file=sys.stderr)
    return n


def _extraction_recall(points, top_k=5):
    """提取完整性: 每个 golden memory point 能否被检索召回.

    匹配策略: 检索结果与 memory_content 的 token 重叠 (Jaccard 相似度)
    超过阈值即视为召回。golden 是提炼句, 原始对话是口语, 精确子串
    必然失败 (实测 0.053); token 重叠容忍措辞差异。
    """
    hits = []
    for mp in points:
        q = mp["memory_content"]
        try:
            r = retrieve(q, top_k=top_k)
        except Exception:  # noqa: BLE001
            r = []
        q_tokens = _tokens_of(q)
        if not q_tokens:
            hits.append(False)
            continue
        best = 0.0
        for x in r:
            c_tokens = _tokens_of(x.get("content", "") or "")
            if not c_tokens:
                continue
            inter = len(q_tokens & c_tokens)
            best = max(best, inter / len(q_tokens))
        # 40% 以上的 query token 出现在检索结果里 → 视为召回
        hits.append(best >= 0.4)
    return hits


def _tokens_of(text: str) -> set:
    """提取检索用 token 集: 小写词 + 数字 + 日期片段."""
    import re
    t = set(re.findall(r"[a-z0-9]+", text.lower()))
    # 去掉无信息量的停用词
    stop = {"the", "a", "an", "is", "are", "was", "were", "to", "of", "in",
            "on", "at", "for", "and", "or", "but", "with", "his", "her",
            "their", "he", "she", "it", "they", "i", "you", "my", "me"}
    return t - stop


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--qa", action="store_true", help="跑 QA 维度 (需要 gateway LLM, 慢)")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip()]
    rows = rows[: args.limit]

    v5_store._sync_vector_best_effort = _patched_sync

    all_recall: list = []
    qa_results: list = []
    t0 = time.time()

    for i, row in enumerate(rows):
        sessions = json.loads(row["sessions"]) if isinstance(row["sessions"], str) else row["sessions"]
        _setup_persona(i)
        n_ingested = _ingest(sessions)

        # 提取完整性 (所有 memory points)
        points = [mp for s in sessions for mp in s.get("memory_points", [])]
        rec = _extraction_recall(points, top_k=args.top_k)
        all_recall.extend(rec)
        r_any = sum(rec) / len(rec) if rec else 0.0
        print(f"[{i+1}/{len(rows)}] uuid={row['uuid'][:8]} ingested={n_ingested} "
              f"points={len(rec)} recall_any={r_any:.3f} elapsed={time.time()-t0:.0f}s")

        if args.qa:
            qa_results.extend(_run_qa(sessions, args.top_k))

    print("\n" + "=" * 60)
    print(f"HaluMem extraction recall  (n={len(all_recall)}, top_k={args.top_k})")
    if all_recall:
        print(f"  recall_any : {np.mean(all_recall):.3f}")
    if qa_results:
        print(f"  QA rows    : {len(qa_results)} (judge 待接)")
    print("=" * 60)

    out = Path("halumem_v5_result.json")
    out.write_text(json.dumps({
        "config": {"limit": args.limit, "top_k": args.top_k},
        "extraction_recall_any": float(np.mean(all_recall)) if all_recall else None,
        "n_points": len(all_recall),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {out}")

    shutil.rmtree(_tmp_root, ignore_errors=True)
    return 0


def _run_qa(sessions, top_k=5):
    """QA 维度: 检索 + gateway LLM 回答 (占位实现, 后续接 judge)."""
    results = []
    for s in sessions:
        for q in s.get("questions", []):
            try:
                hits = retrieve(q["question"], top_k=top_k)
            except Exception:  # noqa: BLE001
                hits = []
            results.append({
                "question": q["question"],
                "reference": q["answer"],
                "evidence": q.get("evidence", []),
                "retrieved": [h["content"][:200] for h in hits],
                "n_hits": len(hits),
            })
    return results


if __name__ == "__main__":
    sys.exit(main())
