"""LongMemEval 检索能力评测 —— 用 V5 记忆系统跑官方基准.

基准: LongMemEval (ICLR 2025, xiaowu0162/LongMemEval), 500 个高质量问答,
测 5 项长期记忆能力: 信息抽取 / 跨会话推理 / 知识更新 / 时间推理 / 弃权.

本脚本评测 V5 的**记忆检索层** (官方 retrieval 指标):
  - 灌入: 每个实例的 haystack_sessions 逐 session 存为一条 V5 记忆
  - 检索: unified_retrieve(question, top_k)
  - 指标: recall_any / recall_all / nDCG@10 (官方 eval_utils 定义)

隔离: 每实例独立临时目录 (DB + chroma), 不碰真实记忆库.
向量写入 patch 为共享 client (等价生产 singleton 用法), 检索路径完全真实.

用法:
  python bin/eval-longmemeval.py --data "E:/Ikaros-something/reference project/longmemeval_s_cleaned.json" --limit 5
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
from memory_v5.memory_retrieval import unified_retrieve  # noqa: E402

_tmp_root = Path(tempfile.mkdtemp(prefix="lmeval_"))


# ── 官方指标 (LongMemEval src/retrieval/eval_utils.py, numpy2 兼容版) ──
def dcg(relevances, k):
    relevances = np.asarray(relevances, dtype=float)[:k]
    if relevances.size:
        return relevances[0] + np.sum(relevances[1:] / np.log2(np.arange(2, relevances.size + 1)))
    return 0.0


def evaluate_retrieval(rankings, correct_docs, corpus_ids, k=10):
    recalled_docs = set(corpus_ids[idx] for idx in rankings[:k])
    recall_any = float(any(doc in recalled_docs for doc in correct_docs))
    recall_all = float(all(doc in recalled_docs for doc in correct_docs))
    relevances = [1 if doc_id in correct_docs else 0 for doc_id in corpus_ids]
    sorted_rel = [relevances[idx] for idx in rankings[:k]]
    ideal_rel = sorted(relevances, reverse=True)
    ideal_dcg = dcg(ideal_rel, k)
    actual_dcg = dcg(sorted_rel, k)
    ndcg = actual_dcg / ideal_dcg if ideal_dcg > 0 else 0.0
    return recall_any, recall_all, ndcg


def evaluate_retrieval_turn2session(rankings, correct_docs, corpus_ids, k=10):
    """官方 turn2session: turn 级 ranking 聚合到 session 级再评估.

    turn docid 形如 ``<session_id>_<turn_idx>``, session id = 去掉最后一段.
    """
    def strip_turn_id(docid):
        return "_".join(str(docid).split("_")[:-1])

    correct_sessions = list(set(strip_turn_id(x) for x in correct_docs))
    sess_corpus = [strip_turn_id(x) for x in corpus_ids]
    sess_rankings = [strip_turn_id(corpus_ids[idx]) for idx in rankings]
    # session 级去重保序 (同 session 多 turn 命中只算一次)
    seen = set()
    uniq = []
    for s in sess_rankings:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    uniq_ids = [s for s in uniq if s in set(sess_corpus)]
    # session 在去重后 corpus 中的位置 (官方: 取该 session 的 turn 排名)
    final_corpus = list(dict.fromkeys(sess_corpus))
    final_idx = {s: i for i, s in enumerate(final_corpus)}
    final_rankings = [final_idx[s] for s in uniq_ids]
    return evaluate_retrieval(final_rankings, correct_sessions, final_corpus, k=k)


# ── 共享向量 client (patch 掉每次 new 的开销, 等价生产 singleton) ──
def _patched_sync(memory_id, content, type_, tags, weight):
    try:
        idx = v5_search.get_vector_index()  # 共享 client
        return bool(idx.add(memory_id, content, type=type_, tags=tags, weight=weight))
    except Exception as e:  # noqa: BLE001
        print(f"    [sync warn] id={memory_id}: {e}", file=sys.stderr)
        return False


def _setup_instance(i: int):
    """每实例独立目录, 强制 vector index 重建.

    Windows 坑 (2026-08-10 实测): chroma PersistentClient 持有 sqlite 文件句柄,
    不关闭直接换目录会句柄累积 → 几十实例后 disk I/O error + MemoryError.
    所以: 先关闭旧 client, 删掉旧实例目录, 再建新目录.
    """
    # 关闭并清掉上一个实例的 vector index (释放 chroma 文件句柄)
    v5_search._VI["instance"] = None
    v5_search._VI["dir"] = None
    for old in _tmp_root.glob("inst_*"):
        shutil.rmtree(old, ignore_errors=True)

    inst_dir = _tmp_root / f"inst_{i}"
    inst_dir.mkdir(parents=True, exist_ok=True)
    v5_store.V5_DATA_DIR = inst_dir
    v5_store.V5_DB_PATH = inst_dir / "v5.db"
    v5_search.V5_DATA_DIR = inst_dir
    v5_search.CHROMA_DIR = inst_dir / "chroma"
    v5_search.get_vector_index(refresh=True)  # 目录变了, 强制重建
    return inst_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-vector", action="store_true", help="跳过向量写入 (只测 FTS+融合)")
    ap.add_argument("--flat-boost", action="store_true",
                    help="conversation type_boost 1.0 (默认 0.8)。LongMemEval 语料全是会话记忆, "
                         "生产里 0.8 是防会话噪声的, 但评测里等于给所有证据统一打 8 折")
    args = ap.parse_args()

    if args.flat_boost:
        from memory_v5 import preprocess_config as pc
        try:
            mr_cfg = pc.cfg()["memory_retrieval"]
            mr_cfg["type_boost"]["conversation"] = 1.0
            print("[eval] conversation boost -> 1.0 (flat)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[eval] flat-boost patch failed: {e}", file=sys.stderr)

    # 流式加载: 264MB JSON 全量 json.load 需要 ~2GB 内存, 系统内存紧张时
    # MemoryError (2026-08-10 实测)。ijson 流式只保留需要的实例。
    data = []
    with open(args.data, encoding="utf-8") as f:
        import ijson
        for item in ijson.items(f, "item"):
            data.append(item)
    if args.limit:
        import random
        rng = random.Random(args.seed)
        data = rng.sample(data, min(args.limit, len(data)))

    if args.skip_vector:
        v5_store._sync_vector_best_effort = lambda *a, **k: False
    else:
        v5_store._sync_vector_best_effort = _patched_sync

    agg = {"recall_any": [], "recall_all": [], "ndcg": []}
    by_type: dict = {}
    n_fail = 0
    t0 = time.time()
    embed_n = 0

    for i, inst in enumerate(data):
        qid = inst["question_id"]
        qtype = inst["question_type"]
        by_type.setdefault(qtype, {"recall_any": [], "recall_all": [], "ndcg": []})
        _setup_instance(i)

        # 灌入: 每 turn 一条记忆 (官方 corpus 是 turn 级, docid = <session>_<turn>)
        turn_ids: list[str] = []  # corpus_ids: "<session_id>_<turn_idx>"
        mid2turn: dict[int, str] = {}
        for sess_idx, session in enumerate(inst["haystack_sessions"]):
            sid = inst["haystack_session_ids"][sess_idx]
            for turn_idx, turn in enumerate(session):
                role = turn.get("role", "?")
                content = (turn.get("content") or "").strip()
                if not content:
                    continue
                text = f"[{role}] {content}"
                if len(text) > 9000:  # V5 validation 上限 10000
                    text = text[:9000]
                docid = f"{sid}_{turn_idx}"
                try:
                    mid = v5_store.store(
                        content=text,
                        type="conversation",
                        weight=0.5,
                        tags=f"lmeval_session:{sid}",
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"[{i}] {qid} store {docid} FAILED: {e}", file=sys.stderr)
                    continue
                embed_n += 1
                mid2turn[mid] = docid
                turn_ids.append(docid)

        # 检索
        try:
            hits = unified_retrieve(inst["question"], top_k=args.top_k)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}] {qid} retrieve FAILED: {e}", file=sys.stderr)
            n_fail += 1
            continue

        corpus_ids = [mid2turn[mid] for mid in mid2turn]  # 按灌入顺序
        rankings = []
        for h in hits:
            docid = mid2turn.get(int(h["id"]))
            if docid is None:
                continue
            try:
                rankings.append(corpus_ids.index(docid))
            except ValueError:
                continue

        # 证据 turn: 证据 session (answer_xxx) 内的全部 turns
        correct_sessions = set(inst["answer_session_ids"])
        correct_docs = [d for d in turn_ids if "_".join(d.split("_")[:-1]) in correct_sessions]
        if not correct_docs:
            n_fail += 1
            print(f"[{i}] {qid} no evidence turn in corpus", file=sys.stderr)
            continue

        r_any, r_all, ndcg = evaluate_retrieval_turn2session(
            rankings, correct_docs, corpus_ids, k=args.top_k
        )
        agg["recall_any"].append(r_any)
        agg["recall_all"].append(r_all)
        agg["ndcg"].append(ndcg)
        by_type[qtype]["recall_any"].append(r_any)
        by_type[qtype]["recall_all"].append(r_all)
        by_type[qtype]["ndcg"].append(ndcg)

        if (i + 1) % 5 == 0 or i == len(data) - 1:
            print(f"[{i+1}/{len(data)}] elapsed={time.time()-t0:.0f}s")

    # ── 汇总 ──
    print("\n" + "=" * 64)
    print(f"LongMemEval retrieval eval  (n={len(agg['recall_any'])}, top_k={args.top_k})")
    print(f"  recall_any : {np.mean(agg['recall_any']):.3f}   (证据 session 至少一个被召回)")
    print(f"  recall_all : {np.mean(agg['recall_all']):.3f}   (全部证据 session 被召回)")
    print(f"  nDCG@k     : {np.mean(agg['ndcg']):.3f}")
    print(f"  failures   : {n_fail}   embedded: {embed_n}")
    print("-" * 64)
    for qt, v in sorted(by_type.items()):
        if v["recall_any"]:
            print(f"  {qt:28s} recall_any={np.mean(v['recall_any']):.3f} "
                  f"recall_all={np.mean(v['recall_all']):.3f} nDCG={np.mean(v['ndcg']):.3f} (n={len(v['recall_any'])})")
    print("=" * 64)

    out = Path("longmemeval_v5_result.json")
    out.write_text(json.dumps({
        "config": {"limit": args.limit, "top_k": args.top_k, "seed": args.seed,
                   "skip_vector": args.skip_vector},
        "agg": {k: float(np.mean(v)) for k, v in agg.items()},
        "by_type": {k: {m: float(np.mean(vv)) for m, vv in v.items() if vv}
                    for k, v in by_type.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved -> {out}")

    shutil.rmtree(_tmp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
