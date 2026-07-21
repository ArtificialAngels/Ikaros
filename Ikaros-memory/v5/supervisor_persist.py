# -*- coding: utf-8 -*-
"""Ikaros 持续运行监督 — 持久化治理层.

借鉴 (详见 E:/Ikaros/exProject/ 下参考项目):
  - OpenHarness: mission.md 机器可验证契约 + heartbeat.md 跨会话断点
  - strict-agent-loop: 磁盘状态机 + 连续失败熔断 + latest-status 心跳广播
  - sleepless-agent: 状态机 daemon + 单 JSON 状态文件
  - Reverie: 潜意识意图循环 + 启发式兜底 (意图分计算在 think.py)

本模块只依赖标准库, 不导入 v5 业务模块 (避免循环依赖); 业务侧 (think.py)
按需 import 本模块来读写监督状态. 落点: data/supervisor/
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ikaros.v5.supervisor_persist")

# ── 路径 ───────────────────────────────────────────────────────
V5_ROOT = Path(__file__).resolve().parent.parent          # Ikaros-memory/
IKAROS_ROOT = V5_ROOT.parent                               # Ikaros/
SUPERVISOR_DIR = IKAROS_ROOT / "data" / "supervisor"
MISSION_PATH = SUPERVISOR_DIR / "mission.md"
HEARTBEAT_PATH = SUPERVISOR_DIR / "heartbeat.md"
STATE_PATH = SUPERVISOR_DIR / "state.json"
STATUS_PATH = SUPERVISOR_DIR / "latest-status.txt"        # 心跳广播 (strict-agent-loop 式)

# ── 默认 mission.md (OpenHarness 风格: 机器可验证完成契约) ──────
_DEFAULT_MISSION = """\
# Ikaros Supervisor Mission

## Goal
让伊卡洛斯的自主思考持续、健康地运行: 无人对话时也能自我推进, 且不失控。

## Done Definition (机器可验证)
- `data/v5/latest_thought.json` 持续产出且非空
- 连续失败 < 3 次 (否则断路器熔断)
- 心跳 `latest-status.txt` 间隔 < 5 分钟

## Boundaries and Constraints
- LLM 调用优先走本地 :8080, 云端 DeepSeek 仅作兜底 (绝不裸跑外部 API 作主链路)
- 用户离开(away)时进入休眠, 不烧 GPU 深度思考
- 单轮深度思考硬超时 120s; 连续失败 3 次熔断并告警

## Execution Cycle
- 意图驱动: 新记忆 / 情感显著变化 / 好奇心高 / 待办到期 / 用户回来 -> 立刻深度思考
- 软上限: 最长 30min 至少一次深度思考, 防饿死
- 潜意识流每 2-3min 轻量絮语 (独立, 不受熔断影响)
"""

# ── 状态机 (sleepless-agent 式) ───────────────────────────────
PHASE_RUNNING = "RUNNING"
PHASE_IDLE = "IDLE"
PHASE_PAUSED = "PAUSED"      # 用户 away
PHASE_TRIPPED = "TRIPPED"    # 断路器熔断
PHASE_STOPPED = "STOPPED"

CIRCUIT_TRIP_THRESHOLD = 3
SOFT_CAP_SEC = 30 * 60       # 软上限, 防饿死


@dataclass
class SupervisorState:
    phase: str = PHASE_IDLE
    last_deep_think_ts: float = 0.0
    last_heartbeat_ts: float = 0.0
    consecutive_failures: int = 0
    circuit_tripped: bool = False
    total_cycles: int = 0
    last_intent_score: float = 0.0
    last_error: str = ""

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "last_deep_think_ts": self.last_deep_think_ts,
            "last_heartbeat_ts": self.last_heartbeat_ts,
            "consecutive_failures": self.consecutive_failures,
            "circuit_tripped": self.circuit_tripped,
            "total_cycles": self.total_cycles,
            "last_intent_score": self.last_intent_score,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SupervisorState":
        return cls(
            phase=str(d.get("phase", PHASE_IDLE)),
            last_deep_think_ts=float(d.get("last_deep_think_ts", 0.0)),
            last_heartbeat_ts=float(d.get("last_heartbeat_ts", 0.0)),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            circuit_tripped=bool(d.get("circuit_tripped", False)),
            total_cycles=int(d.get("total_cycles", 0)),
            last_intent_score=float(d.get("last_intent_score", 0.0)),
            last_error=str(d.get("last_error", "")),
        )


def _human(ts: float) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


# ── 状态持久化 (原子写, 借鉴 ReflectScheduler) ────────────────
def load_state(path: Path | None = None) -> SupervisorState:
    p = path or STATE_PATH
    if p.is_file():
        try:
            return SupervisorState.from_dict(json.loads(p.read_text("utf-8")))
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning("supervisor state 损坏, 重置: %s", e)
    return SupervisorState()


def save_state(state: SupervisorState, path: Path | None = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def ensure_mission() -> None:
    SUPERVISOR_DIR.mkdir(parents=True, exist_ok=True)
    if not MISSION_PATH.is_file():
        MISSION_PATH.write_text(_DEFAULT_MISSION, encoding="utf-8")


# ── 心跳广播 (OpenHarness heartbeat + strict-agent-loop latest-status) ──
def write_heartbeat(state: SupervisorState, *, intent_score: float = 0.0, note: str = "") -> None:
    now = time.time()
    state.last_heartbeat_ts = now
    SUPERVISOR_DIR.mkdir(parents=True, exist_ok=True)
    hb = (
        "# Ikaros Heartbeat (L1 pointer)\n\n"
        f"- System Status: {state.phase}\n"
        f"- Execution Pointer: last_deep_think={_human(state.last_deep_think_ts)} | "
        f"total_cycles={state.total_cycles}\n"
        f"- Intent Score: {intent_score:.2f}\n"
        f"- Consecutive Failures: {state.consecutive_failures}\n"
        f"- Circuit Breaker: {'TRIPPED' if state.circuit_tripped else 'ok'}\n"
        f"- Active Alerts: {note or 'none'}\n"
        f"- Updated: {_human(now)}\n"
    )
    try:
        HEARTBEAT_PATH.write_text(hb, encoding="utf-8")
        STATUS_PATH.write_text(
            f"{state.phase} | score={intent_score:.2f} | fails={state.consecutive_failures} | "
            f"{'TRIPPED' if state.circuit_tripped else 'ok'} | {_human(now)}\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.debug("heartbeat write failed: %s", e)
    save_state(state)


# ── 成功 / 失败 + 熔断器 ──────────────────────────────────────
def record_success(state: SupervisorState) -> SupervisorState:
    state.consecutive_failures = 0
    state.circuit_tripped = False
    if state.phase == PHASE_TRIPPED:
        state.phase = PHASE_IDLE
    return state


def record_failure(state: SupervisorState, err: str) -> SupervisorState:
    state.consecutive_failures += 1
    state.last_error = str(err)[:200]
    if state.consecutive_failures >= CIRCUIT_TRIP_THRESHOLD:
        state.circuit_tripped = True
        state.phase = PHASE_TRIPPED
        logger.error("supervisor 断路器熔断 (连续失败 %d 次)", state.consecutive_failures)
    return state


def reset_circuit(state: SupervisorState) -> SupervisorState:
    """外部 (如人工 / 健康检查) 重置熔断器."""
    state.consecutive_failures = 0
    state.circuit_tripped = False
    if state.phase == PHASE_TRIPPED:
        state.phase = PHASE_IDLE
    save_state(state)
    return state
