"""
Ikaros Loop Workflow — 闭环迭代式长周期任务管理器

模式 (哥哥 6-26):
  Check → Search → Execute → Verify → Test → [回到 Check] → ... → Done

实现:
  - 一个 task = 一个长周期目标
  - task body 包含 goal + 当前 cycle_n + phase + outcome + 备注
  - 每个 phase 完成后用 /v1/ikaros/kanban/comments API 记录
  - 失败 → 回到 check phase, 重新分析
  - 全 cycle 完 → /complete 总结

这是 webui Kanban 任务管理 + Neuro 闭环工作流的结合:
  - task 是 Kanban 上的可视化卡片 (哥哥能看到进度)
  - loop 跑在后台 (Neuro tray 监听 task 状态)
  - failure/blocked 自动通知 (Phase 4 PATIENCE)
  - 完了 → Phase 4 completion_event 触发告诉哥哥
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Any
from urllib import request as urlrequest
from urllib.error import URLError

HERE = Path(__file__).parent
HERE_ROOT = HERE.parent
KANBAN_API = "http://127.0.0.1:8648/api/hermes/kanban"
KANBAN_DIRECT_API = "http://127.0.0.1:8649/api/hermes/kanban"
_TOKEN_FILE = HERE_ROOT / "data" / "webui" / ".admin-jwt.txt"


def _load_token() -> Optional[str]:
    """Load admin JWT token from disk (used for webui auth)."""
    try:
        if _TOKEN_FILE.exists():
            t = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            return t or None
    except OSError:
        pass
    return None

# 5-phase 闭环 (哥哥定义)
PHASES = ["check", "search", "execute", "verify", "test"]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [loop-workflow] %(message)s",
    stream=sys.stderr,
    force=True,
)
log = logging.getLogger("loop-workflow")


# ─── Kanban API helpers ───

def api(method: str, path: str, body: Optional[dict] = None) -> Optional[dict]:
    """调 webui Kanban API (走 webui_proxy :8648 with JWT token).

    webui 0.6.21 quirks:
      - Requires Bearer JWT in Authorization header
      - For per-profile kanban operations, the active profile is read from
        the *request body* as the "profile" field, NOT from query string.
        Query-string ?profile=... is ignored by webui_proxy.
      - Some endpoints (e.g. /comments) don't require profile in body.
    """
    token = _load_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        url = f"{KANBAN_API}{path}"
        # 自动注入 profile 到 body (默认 default). 已有 profile 不覆盖.
        payload = dict(body) if body else {}
        if "profile" not in payload:
            payload["profile"] = "default"
        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urlrequest.Request(
            url, data=data, method=method,
            headers=headers,
        )
        with urlrequest.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log.warning("kanban API %s %s: %s", method, path, e)
        return None


# ─── Loop Workflow State ───

class LoopState:
    """每个 task 的循环状态 (in-memory)."""
    def __init__(self, task_id: str, goal: str, max_cycles: int = 10):
        self.task_id = task_id
        self.goal = goal
        self.cycle_n = 1
        self.phase_idx = 0  # PHASES index
        self.max_cycles = max_cycles
        self.history = []  # [(cycle, phase, outcome, note)]

    @property
    def phase(self) -> str:
        return PHASES[self.phase_idx]

    def advance(self, outcome: str, note: str = ""):
        """推进状态机:
        - 'passed' → 下一个 phase; phase 走完 → 下一 cycle
        - 'failed' → 回到 check (idx=0), 同一 cycle 重新做
        - 'blocked' → 不前进, 等哥哥决策
        """
        cycle, phase = self.cycle_n, self.phase
        self.history.append((cycle, phase, outcome, note))
        log.info("cycle %d, %s: %s — %s", cycle, phase, outcome, note)

        if outcome == "passed":
            self.phase_idx += 1
            if self.phase_idx >= len(PHASES):
                # 整个 cycle 完 → 下一 cycle 重新检查
                self.phase_idx = 0
                self.cycle_n += 1
                if self.cycle_n > self.max_cycles:
                    log.info("task done: max cycles reached")
                    return "done"
                log.info("→ cycle %d", self.cycle_n)
        elif outcome == "failed":
            self.phase_idx = 0  # 回到 check
            log.info("→ cycle %d, %s (重新分析)", self.cycle_n, self.phase)
        elif outcome == "blocked":
            log.info("blocked: waiting for 哥哥 decision")
            return "blocked"
        return "continue"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "cycle_n": self.cycle_n,
            "phase": self.phase,
            "max_cycles": self.max_cycles,
            "history_len": len(self.history),
            "last_outcome": self.history[-1][2] if self.history else None,
        }


# ─── Loop runner ───

class LoopRunner:
    """
    长周期闭环迭代运行器.

    用法:
        runner = LoopRunner(
            task_id="t_xxx",
            goal="把 PyQt6 桌宠 Live2D 化",
            check_fn=lambda ctx: ...,
            search_fn=lambda ctx: ...,
            execute_fn=lambda ctx: ...,
            verify_fn=lambda ctx: ...,
            test_fn=lambda ctx: ...,
            max_cycles=10,
        )
        runner.run()

    每个 phase_fn 接收 ctx dict (cycle_n, phase, history, goal) 返回:
        ("passed", "说明") | ("failed", "原因") | ("blocked", "等什么")
    """

    def __init__(
        self,
        task_id: str,
        goal: str,
        check_fn: Callable[[dict], tuple],
        search_fn: Callable[[dict], tuple],
        execute_fn: Callable[[dict], tuple],
        verify_fn: Callable[[dict], tuple],
        test_fn: Callable[[dict], tuple],
        max_cycles: int = 10,
    ):
        self.task_id = task_id
        self.state = LoopState(task_id, goal, max_cycles=max_cycles)
        self.phase_fns = {
            "check": check_fn,
            "search": search_fn,
            "execute": execute_fn,
            "verify": verify_fn,
            "test": test_fn,
        }

    def _ctx(self) -> dict:
        return {
            "cycle_n": self.state.cycle_n,
            "phase": self.state.phase,
            "goal": self.state.goal,
            "history": self.state.history,
        }

    def _post_comment(self, text: str):
        """把进展写到 task comment (哥哥在 webui 能看到)."""
        api("POST", f"/{self.task_id}/comments", {"body": text, "author": "ikaros"})
        log.info("comment posted: %s", text[:80])

    def _post_specify(self, body: str):
        """更新 task body (哥哥看 task 详情时看到)."""
        api("POST", f"/{self.task_id}/specify", {"author": "ikaros"})
        api("POST", f"/{self.task_id}/comments", {"body": body, "author": "ikaros"})

    def run(self):
        log.info("=" * 60)
        log.info("Loop workflow started: %s", self.state.goal)
        log.info("task_id: %s, max_cycles: %d", self.task_id, self.state.max_cycles)
        log.info("=" * 60)

        self._post_comment(f"🚀 闭环工作流启动: {self.state.goal}")

        while True:
            fn = self.phase_fns[self.state.phase]
            ctx = self._ctx()
            log.info("--- cycle %d, phase: %s ---", ctx["cycle_n"], ctx["phase"])

            try:
                outcome, note = fn(ctx)
            except Exception as exc:
                outcome, note = "failed", f"exception: {exc}"

            self._post_comment(
                f"**cycle {ctx['cycle_n']} / {ctx['phase']}**: {outcome}\n{note}"
            )

            result = self.state.advance(outcome, note)

            if result == "done":
                self._finalize("passed", "所有 cycle 完成")
                return "done"
            elif result == "blocked":
                # 把 task block, 等哥哥决策
                api("POST", f"/{self.task_id}/block", {"reason": note or "等哥哥决策"})
                self._post_comment(f"⏸️ blocked: {note}")
                return "blocked"

    def _finalize(self, outcome: str, summary: str):
        """task complete."""
        log.info("finalize: %s — %s", outcome, summary)
        self._post_comment(f"✅ {summary}")
        # 用 complete endpoint (task_ids 数组)
        api("POST", "/complete", {"task_ids": [self.task_id], "summary": summary})
        log.info("task complete: %s", self.task_id)


# ─── Sample: 5-phase function builders ───

def make_phase_fn(name: str, work: Callable[[dict], Any], pass_check: Callable[[Any], bool]) -> Callable:
    """Helper: 包一个 work 函数, 返回 (outcome, note).

    work(ctx) -> any_result
    pass_check(result) -> True=passed, False=failed
    """
    def fn(ctx: dict) -> tuple:
        log.info("[%s] running...", name)
        try:
            result = work(ctx)
            if pass_check(result):
                return "passed", f"{name} 完成"
            else:
                return "failed", f"{name} 不通过: {result}"
        except Exception as exc:
            return "failed", f"{name} 异常: {exc}"
    return fn


# ─── CLI test ───

def demo_test_run():
    """小 demo: 一个空循环, 全 passed, 3 cycle 完."""
    log.info("demo run")

    # 1. 建任务
    res = api("POST", "", {
        "title": "[Demo] 闭环工作流 demo",
        "body": "5-phase loop demo, 3 cycles",
    })
    if not res or not res.get("task"):
        log.error("create task failed")
        return
    task_id = res["task"]["id"]
    log.info("created task: %s", task_id)

    # 2. 跑 loop
    def check_fn(ctx):
        time.sleep(0.5)
        return "passed", f"检查 cycle {ctx['cycle_n']} 状态 OK"
    def search_fn(ctx):
        time.sleep(0.5)
        return "passed", "找到 0 个工具 (空环境)"
    def execute_fn(ctx):
        time.sleep(0.5)
        return "passed", "执行 0 步 (no-op)"
    def verify_fn(ctx):
        time.sleep(0.5)
        return "passed", "无产物, 无需验收"
    def test_fn(ctx):
        time.sleep(0.5)
        return "passed", "无代码, 测试通过"

    runner = LoopRunner(
        task_id=task_id,
        goal="[Demo] 闭环工作流测试",
        check_fn=check_fn,
        search_fn=search_fn,
        execute_fn=execute_fn,
        verify_fn=verify_fn,
        test_fn=test_fn,
        max_cycles=3,
    )
    runner.run()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo_test_run()
    else:
        print("Usage:")
        print("  python icarus_loop_workflow.py demo")
        print()
        print("This module is meant to be imported by other scripts.")
        print("Example: see demo_test_run() for 5-phase loop pattern.")