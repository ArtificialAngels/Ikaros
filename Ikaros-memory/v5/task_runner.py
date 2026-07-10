"""
v5.task_runner — 后台任务执行 + 结果持久化 + 主动提醒

流程:
  1. task_runner.call_async(optimized_text, original_text)
     → spawn 后台线程调 cloud LLM
     → 立即返 {"status": "running", "task_id": "..."}
  2. LLM 完成 → 写结果到 data/v5/task_result.json
  3. cloud_chat.build_system_prompt() 每次检查
     → 有结果则注入 "哥哥，任务完成了，有空听吗？"
  4. 用户反应:
     - "有空" → deliver result, 清文件
     - "没空" → 写 pending 标记, cron 后重提
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ikaros.v5.task_runner")

V5_ROOT = Path(__file__).resolve().parent.parent  # Ikaros-memory/
_TASK_DIR = V5_ROOT / "data" / "v5"
_RESULT_PATH = _TASK_DIR / "task_result.json"
_PENDING_PATH = _TASK_DIR / "task_pending.json"


def call_async(text: str, optimized: Optional[str] = None) -> dict:
    """后台执行任务 (子线程调 cloud LLM), 立即返回.

    Args:
        text: 原始用户输入
        optimized: 已优化的指令 (如有)

    Returns:
        {"status": "running", "task_id": "xxx"}
    """
    task_id = uuid.uuid4().hex[:12]
    _TASK_DIR.mkdir(parents=True, exist_ok=True)

    # 写运行中标记 (防止重复触发)
    _write_json(_RESULT_PATH, {
        "task_id": task_id,
        "status": "running",
        "text": text,
        "optimized": optimized,
        "started_at": time.time(),
    })

    # 后台线程执行
    t = threading.Thread(
        target=_execute_async,
        args=(task_id, text, optimized),
        daemon=True,
        name=f"task-{task_id}",
    )
    t.start()
    return {"status": "running", "task_id": task_id}


def _execute_async(task_id: str, text: str, optimized: Optional[str]) -> None:
    """后台: 委托 Hermes Agent 执行任务 (hermes chat -q), 写结果文件.

    Hermes Agent 有全套工具/技能/记忆/子代理, 比裸调 API 强得多.
    """
    try:
        import subprocess as _sp
        import sys as _sys

        # 定位 hermes 可执行文件
        _hermes = str(Path(__file__).resolve().parent.parent.parent
                      / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
        if not Path(_hermes).is_file():
            raise FileNotFoundError(f"hermes not found: {_hermes}")

        user_content = optimized if optimized else text
        # 构建指令: 简短任务标签让 Hermes Agent 的 router 自己决定用什么工具
        goal = f"执行这个任务: {user_content}"

        _result = _sp.run(
            [_hermes, "chat", "-q", goal, "--max-turns", "3"],
            capture_output=True, text=True, timeout=300,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )

        reply = _result.stdout.strip()
        if not reply or _result.returncode != 0:
            reply = _result.stderr.strip() or "（任务执行失败）"

        _write_json(_RESULT_PATH, {
            "task_id": task_id, "status": "done",
            "text": text, "optimized": optimized,
            "result": reply, "completed_at": time.time(),
        })
        logger.info("task %s: done (%d chars)", task_id, len(reply))

    except Exception as e:
        logger.error("task %s: failed (%s)", task_id, e)
        _write_json(_RESULT_PATH, {
            "task_id": task_id,
            "status": "failed",
            "text": text,
            "error": str(e),
            "completed_at": time.time(),
        })


def check_result() -> Optional[dict]:
    """检查是否有已完成的任务结果. 有则返回结果 dict, 不删文件."""
    if not _RESULT_PATH.is_file():
        return None
    try:
        data = json.loads(_RESULT_PATH.read_text(encoding="utf-8"))
        if data.get("status") in ("done", "failed"):
            return data
        return None
    except Exception:
        return None


def check_pending_reminder() -> Optional[dict]:
    """检查是否有挂起的提醒 (用户说没空时设的)."""
    if not _PENDING_PATH.is_file():
        return None
    try:
        data = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        return data
    except Exception:
        return None


def set_reminder(data: dict) -> None:
    """设一个提醒 (用户没空时调用)."""
    _TASK_DIR.mkdir(parents=True, exist_ok=True)
    data["remind_at"] = time.time()  # 记下设置时间
    _PENDING_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def consume_result() -> Optional[dict]:
    """消费结果 (用户说有空时调用). 读取后删除文件."""
    data = check_result()
    if data:
        try:
            _RESULT_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    return data


def consume_reminder() -> Optional[dict]:
    """消费提醒. 读取后删除."""
    data = check_pending_reminder()
    if data:
        try:
            _PENDING_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
