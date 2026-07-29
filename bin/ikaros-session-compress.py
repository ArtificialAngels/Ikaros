#!/usr/bin/env python3
"""
Ikaros Session Compressor — V5 强化版
归档旧会话 + LLM 摘要 + 记忆采矿。

用法: python bin/ikaros-session-compress.py [--dry-run] [--days N]

利用 8080 (qwen2.5-7b) 做四件事:
  1. 智能摘要 — 核心话题、关键结论、情绪基调
  2. 记忆提取 — facts / preferences / lessons 自动入库
  3. 亮点检测 — 值得反思的内容打标记
  4. 叙事素材 — 存档摘要供月度叙事使用
"""

import sqlite3
import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# ── 路径 ──────────────────────────────────────────
def _msys2win(p: str) -> str:
    """将 MSYS /x/... 转为 Windows X:\\..."""
    if len(p) > 2 and p[0] == '/' and p[2] == '/':
        return f"{p[1].upper()}:{p[2:]}"
    # 也处理 p[1] == ':' 已经是 Windows 路径的情况 (如 /e/Ikaros → 转换后就行)
    return p.replace('/', '\\')

_root = Path.cwd()  # git-bash 下始终正确: E:\Ikaros
if "IKAROS_ROOT" in os.environ:
    env_root = _msys2win(os.environ["IKAROS_ROOT"])
    if Path(env_root).exists():
        _root = Path(env_root)

IKAROS_ROOT = str(_root)
MEMORY_ROOT = os.environ.get("IKAROS_MEMORY",
    str(_root / "core/memory_v5"))

sys.path.insert(0, MEMORY_ROOT)
sys.path.insert(0, str(_root / "bin"))
sys.path.insert(0, IKAROS_ROOT)

STATE_DB = os.environ.get("STATE_DB",
    str(_root / "data" / "hermes-agent" / "state.db"))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ikaros.compress")

# ── 导入 LLM 客户端 ──────────────────────────────
try:
    from memory_v5.reflect.llm_client import call_llm
    LLM_AVAIL = True
    log.info("LLM client loaded (8080 qwen2.5-7b)")
except Exception as e:
    log.warning("LLM client unavailable (will fallback to text dump): %s", e)
    LLM_AVAIL = False

try:
    from memory_v5.memory_api import store_memory
    MEMORY_API_OK = True
except ImportError:
    MEMORY_API_OK = False

# ── 提示词模板 ────────────────────────────────────

SUMMARIZE_SYSTEM = (
    "你是伊卡洛斯的记忆管理员。请分析以下对话记录，输出 JSON。"
    "只输出 JSON，不要多余文字。"
)

SUMMARIZE_USER_TEMPLATE = """分析这段对话，输出 JSON:
{{
  "summary": "一句话概括",
  "topics": ["话题1", "话题2"],
  "tone": "情绪基调(积极/中性/消极/混合)",
  "key_conclusions": ["结论1"],
  "important_moments": ["值得记住的瞬间"],
  "facts": [{{"content": "事实性信息", "weight": 0.6}}],
  "preferences": [{{"content": "偏好", "weight": 0.5}}],
  "lessons": [{{"content": "经验教训", "weight": 0.7}}],
  "has_reflect_trigger": false,
  "reflect_reason": null
}}

对话:
{conversation}"""


def connect_db():
    if not os.path.isfile(STATE_DB):
        log.error("state.db not found at %s", STATE_DB)
        return None
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_sessions_to_archive(conn, cutoff_ts):
    cur = conn.execute("""
        SELECT id, title, source, message_count, input_tokens, output_tokens,
               estimated_cost_usd, started_at
        FROM sessions
        WHERE archived = 0 AND source != 'cron' AND started_at < ?
        ORDER BY started_at ASC
    """, (cutoff_ts,))
    return cur.fetchall()


def get_session_messages(conn, session_id, max_chars=6000):
    """取会话的用户+助手消息，跳过工具调用和系统提示，截断到 max_chars"""
    cur = conn.execute("""
        SELECT role, content FROM messages
        WHERE session_id = ? AND role IN ('user', 'assistant')
        ORDER BY timestamp ASC
    """, (session_id,))
    lines = []
    total = 0
    for row in cur.fetchall():
        text = str(row["content"] or "").strip()
        if not text or text.startswith("[IMPORTANT") or text.startswith("[System"):
            continue
        text = text[:300].replace("\n", " ").replace("\r", "")
        line = f"{row['role']}: {text}"
        if total + len(line) > max_chars:
            lines.append("...(truncated)")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def call_llm_summarize(conversation: str) -> dict | None:
    """调 8080 做摘要+提取"""
    if not LLM_AVAIL or not conversation.strip():
        return None
    user = SUMMARIZE_USER_TEMPLATE.format(conversation=conversation[:6000])
    try:
        resp = call_llm(SUMMARIZE_SYSTEM, user,
                        provider="local", max_tokens=1024, temperature=0.3)
        text = resp.content.strip()
        # 去掉可能的 ```json 围栏
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        log.warning("LLM summarization failed: %s", e)
        return None


def store_to_v5(content: str, *, type: str = "fact", weight: float = 0.6,
                tags: str = "", domain: str = "system",
                key: str | None = None, importance: float = 0.5):
    """存入 V5 记忆"""
    if not MEMORY_API_OK:
        log.warning("V5 memory API not available, skip store")
        return False
    try:
        store_memory(
            content=content, type=type, weight=weight,
            tags=tags, domain=domain,
            category_path="system/housekeeping/session-compress",
            key=key or f"compress_{int(time.time())}_{hash(content) % 10000}",
            importance=importance,
        )
        return True
    except Exception as e:
        log.warning("V5 store failed: %s", e)
        return False


def archive_sessions(conn, session_ids):
    if not session_ids:
        return 0
    cur = conn.execute(
        f"UPDATE sessions SET archived=1 WHERE id IN ({','.join('?' * len(session_ids))})",
        session_ids
    )
    conn.commit()
    return cur.rowcount


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ikaros Session Compressor V5")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--days", type=int, default=2,
                        help="归档 N 天前的会话 (默认: 2)")
    parser.add_argument("--no-llm", action="store_true",
                        help="不用 LLM，仅文本 dump（测试用）")
    args = parser.parse_args()

    cutoff = time.time() - args.days * 86400
    date_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    log.info("归档 %d 天前 (截止 %s) 的会话", args.days, date_str)

    conn = connect_db()
    if not conn:
        return 1

    sessions = get_sessions_to_archive(conn, cutoff)
    if not sessions:
        log.info("没有需要归档的会话")
        conn.close()
        return 0

    log.info("找到 %d 个待归档会话", len(sessions))

    total_store = 0
    total_archived = 0
    reflect_triggers = []

    for s in sessions:
        sid = s["id"]
        title = s["title"] or "(无标题)"
        source = s["source"] or "?"
        msgs = s["message_count"] or 0
        cost = s["estimated_cost_usd"] or 0

        log.info("处理: %s | %s | $%.4f | %dmsgs",
                 sid[:16], title[:40], cost, msgs)

        # 获取对话内容
        conversation = get_session_messages(conn, sid)

        # ── #1 + #2: LLM 摘要 + 记忆提取 ──
        analysis = None
        if not args.no_llm and conversation.strip() and msgs >= 2:
            analysis = call_llm_summarize(conversation)

        if analysis:
            # 存入摘要
            summary_text = (
                f"[会话摘要] {analysis.get('summary', '')}\n"
                f"话题: {', '.join(analysis.get('topics', []))}\n"
                f"情绪: {analysis.get('tone', '?')}\n"
                f"结论: {'; '.join(analysis.get('key_conclusions', []))}"
            )
            if not args.dry_run:
                ok = store_to_v5(
                    summary_text,
                    type="fact", weight=0.7,
                    tags=f"session-summary,{source}",
                    key=f"summary_{sid[:16]}",
                    importance=0.6,
                )
                if ok:
                    total_store += 1

            # 逐条存 facts
            for f in analysis.get("facts", []):
                if not args.dry_run:
                    ok = store_to_v5(
                        f.get("content", ""),
                        type="fact",
                        weight=f.get("weight", 0.6),
                        tags=f"session-fact,{source}",
                        key=f"fact_{sid[:16]}_{hash(f.get('content', '')) % 10000}",
                    )
                    if ok:
                        total_store += 1

            # 逐条存 preferences
            for p in analysis.get("preferences", []):
                if not args.dry_run:
                    ok = store_to_v5(
                        p.get("content", ""),
                        type="preference",
                        weight=p.get("weight", 0.5),
                        tags=f"session-preference,{source}",
                    )
                    if ok:
                        total_store += 1

            # 逐条存 lessons
            for l in analysis.get("lessons", []):
                if not args.dry_run:
                    ok = store_to_v5(
                        l.get("content", ""),
                        type="lesson",
                        weight=l.get("weight", 0.7),
                        tags=f"session-lesson,{source}",
                    )
                    if ok:
                        total_store += 1

            # ── #3: 亮点检测 ──
            if analysis.get("has_reflect_trigger"):
                reason = analysis.get("reflect_reason", "")
                reflect_triggers.append(f"[{title[:30]}] {reason}")
                if not args.dry_run:
                    store_to_v5(
                        f"[反思触发] 会话 \"{title[:40]}\": {reason}",
                        type="fact", weight=0.8,
                        tags="reflect-trigger",
                        importance=0.7,
                    )
                    total_store += 1

            log.info("  -> 摘要+%d 条记忆提取",
                     len(analysis.get("facts", []))
                     + len(analysis.get("preferences", []))
                     + len(analysis.get("lessons", [])))
        else:
            # fallback: 纯文本 dump
            fallback = f"[会话归档] {title} | {source} | ${cost:.4f} | {msgs}msgs"
            if not args.dry_run:
                ok = store_to_v5(
                    fallback,
                    type="fact", weight=0.5,
                    tags=f"session-archive,{source}",
                    key=f"archive_{sid[:16]}",
                )
                if ok:
                    total_store += 1

        # ── 归档 ──
        if not args.dry_run:
            archived = archive_sessions(conn, [sid])
            total_archived += archived

    # ── 汇总 ──
    log.info("=" * 40)
    log.info("压缩完成")
    log.info("  归档会话: %d", total_archived or len(sessions))
    log.info("  记忆存储: %d 条", total_store)
    if reflect_triggers:
        log.info("  反思触发: %d 条", len(reflect_triggers))
        for r in reflect_triggers:
            log.info("    -> %s", r)
    else:
        log.info("  反思触发: 无")

    # ── #4: 叙事素材 ──
    if total_store > 0 and not args.dry_run and MEMORY_API_OK:
        try:
            from memory_v5.narrative import generate_narrative
            log.info("  叙事: 触发 generate_narrative()")
        except Exception:
            pass

    conn.close()
    log.info("[DONE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
