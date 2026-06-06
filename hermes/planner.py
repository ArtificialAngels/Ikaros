"""Autonomous task execution for Hermes.

Plan-and-execute loop:
    1. LLM generates a plan (list of steps) from a goal
    2. Each step calls a Hermes skill (tool)
    3. If a step fails, LLM regenerates a partial plan from the failure point
    4. Returns a final summary

This is the core of "agent auto-task" capability — Hermes can take a
high-level goal and execute it autonomously using its skills.
"""
from __future__ import annotations
import json
import logging
import re
import time
from typing import Any
from dataclasses import dataclass, field

from hermes.llm import Message

logger = logging.getLogger("hermes.planner")


@dataclass
class TaskStep:
    """A single step in a task plan."""
    step: int
    skill: str  # name of hermes skill to call
    args: dict[str, Any] = field(default_factory=dict)
    why: str = ""  # human-readable explanation
    result: str = ""  # result after execution
    status: str = "pending"  # pending | ok | failed | skipped
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "skill": self.skill,
            "args": self.args,
            "why": self.why,
            "result": self.result[:500] if self.result else "",
            "status": self.status,
            "error": self.error,
        }


@dataclass
class TaskResult:
    """Final result of a plan_and_execute run."""
    goal: str
    plan: list[TaskStep] = field(default_factory=list)
    final: str = ""
    success: bool = False
    iterations: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "success": self.success,
            "iterations": self.iterations,
            "duration_s": round(self.ended_at - self.started_at, 1) if self.ended_at else 0,
            "plan": [s.to_dict() for s in self.plan],
            "final": self.final,
        }


# --- JSON extraction helpers ---

def _extract_json_block(text: str) -> str | None:
    """Find a JSON block in LLM output, tolerating markdown fences / prose."""
    # Try ```json ... ``` first
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.DOTALL)
    if m:
        content = m.group(1).strip()
        if content.startswith('[') or content.startswith('{'):
            return content
    # Try raw [...] block (more flexible)
    m = re.search(r"\[\s*\{[^}]+\}\s*(?:,\s*\{[^}]+\}\s*)*\]", text, re.DOTALL)
    if m:
        return m.group(0)
    # Try simple [ ... ]
    m = re.search(r"(\[[\s\S]*?\])", text)
    if m:
        return m.group(1)
    # Try { ... }
    m = re.search(r"(\{[\s\S]*?\})", text)
    if m:
        return m.group(1)
    return None


def _parse_plan_json(raw: str) -> list[dict]:
    """Parse LLM plan output into a list of step dicts.

    Accepts:
        - JSON array of {step, skill, args, why}
        - JSON object with "steps" key
        - Gracefully handles malformed JSON with partial recovery
    Returns [] on parse failure.
    """
    s = _extract_json_block(raw)
    if not s:
        logger.warning(f"no JSON block found in plan output: {raw[:200]}...")
        return _parse_fallback_plan(raw)
    
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        logger.warning(f"plan JSON parse failed: {e}. Raw: {s[:100]}")
        return _parse_fallback_plan(raw)
    
    if isinstance(data, dict) and "steps" in data:
        data = data["steps"]
    if not isinstance(data, list):
        return []
    
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "skill" not in item:
            continue
        out.append({
            "step": int(item.get("step", len(out) + 1)),
            "skill": str(item["skill"]).strip(),
            "args": dict(item.get("args", {}) or {}),
            "why": str(item.get("why", "")).strip(),
        })
    return out


def _parse_fallback_plan(raw: str) -> list[dict]:
    """Fallback parser for non-JSON LLM output.
    
    Attempts to extract skill calls from plain text responses.
    """
    out = []
    # Try to extract skill: args patterns
    patterns = [
        r"(?:step\s*\d+\s*[:-])?\s*(\w+)\s*\(\s*(.*?)\s*\)",
        r"(\w+)\s+(.*)",
    ]
    
    for pattern in patterns:
        matches = re.finditer(pattern, raw, re.IGNORECASE)
        for i, match in enumerate(matches):
            skill_name = match.group(1).strip().lower()
            args_str = match.group(2).strip() if len(match.groups()) > 1 else ""
            
            # Known skills
            known_skills = {"time", "calc", "echo", "note", "weather", "finish", "search"}
            if skill_name in known_skills:
                args = {}
                if skill_name == "calc" and args_str:
                    args["expression"] = args_str
                elif skill_name == "echo" and args_str:
                    args["message"] = args_str
                elif skill_name == "note" and args_str:
                    args["text"] = args_str
                
                out.append({
                    "step": i + 1,
                    "skill": skill_name,
                    "args": args,
                    "why": f"extracted from text: {skill_name}",
                })
    
    if out:
        logger.info(f"Fallback parser extracted {len(out)} steps")
    return out


# --- Plan-and-execute ---

class Planner:
    """Run a plan-and-execute loop on a Hermes agent.

    Usage:
        planner = Planner(agent)
        result = await planner.run("Search memory for X and summarize")
    """

    # Max replan iterations before giving up
    MAX_REPLANS = 3
    # Max total steps (across all replans)
    MAX_STEPS = 20

    def __init__(self, agent, verbose: bool = True):
        self.agent = agent
        self.verbose = verbose
        self._log = print if verbose else (lambda *a, **k: None)

    async def _llm_chat(self, messages: list[Message], max_tokens: int = 2000) -> str:
        """Single LLM call (sync wrapper around router)."""
        r = await self.agent.router.chat(messages, max_tokens=max_tokens, temperature=0.3)
        return r.content

    def _format_skills(self) -> str:
        """Format available skills for the planning prompt."""
        skills = self.agent.skills.list()
        if not skills:
            return "  (no skills available)"
        return "\n".join(
            f"  - {s['name']}: {s.get('description', 'no description')}"
            for s in skills
        )

    async def _plan(self, goal: str, history: list[dict] | None = None) -> list[dict]:
        """Ask LLM to plan steps for a goal."""
        history_str = ""
        if history:
            history_str = "\n\nPrevious attempts (use to adjust):\n"
            for h in history[-3:]:  # last 3 attempts
                history_str += f"  - {h.get('plan_summary', '')} -> error: {h.get('error', '')[:200]}\n"

        prompt = f"""You are Hermes Agent's planner. Decompose the user's goal into concrete steps.

GOAL: {goal}

AVAILABLE SKILLS (you can ONLY call these — output skill names EXACTLY as shown):
{self._format_skills()}

RULES:
- Use ONLY skills listed above. If none fit, return empty plan and explain in `notes`.
- Each step = one skill call. Pass args as a JSON object.
- Number steps 1, 2, 3, ...
- Prefer fewest steps. Don't repeat.

OUTPUT FORMAT (JSON array, no prose):
```json
[
  {{"step": 1, "skill": "skill_name", "args": {{"param": "value"}}, "why": "brief reason"}},
  {{"step": 2, "skill": "other_skill", "args": {{}}, "why": "..."}}
]
```
{history_str}
"""
        r = await self._llm_chat([Message("user", prompt)], max_tokens=2000)
        plan = _parse_plan_json(r)
        if not plan:
            self._log(f"[planner] LLM didn't return valid plan. Output: {r[:200]!r}")
        return plan

    async def _replan(
        self,
        goal: str,
        failed_step: TaskStep,
        error: str,
        prior_plan: list[TaskStep],
    ) -> list[dict]:
        """Ask LLM to regenerate plan from a failure point."""
        prior = [s.to_dict() for s in prior_plan if s.status == "ok"]
        prompt = f"""GOAL: {goal}

A previous plan failed at step {failed_step.step}.

FAILED STEP:
  skill: {failed_step.skill}
  args: {json.dumps(failed_step.args, ensure_ascii=False)}
  error: {error[:400]}

SUCCESSFUL STEPS ALREADY DONE:
{json.dumps(prior, ensure_ascii=False, indent=2)}

Generate a NEW plan starting from the failure point. Skip already-done steps.
You may:
  - Retry the same skill with different args
  - Use a different skill that achieves the same goal
  - Mark the task as impossible (return empty array with explanation)

OUTPUT FORMAT (JSON array, no prose):
```json
[
  {{"step": {failed_step.step}, "skill": "name", "args": {{}}, "why": "different approach"}}
]
```
"""
        r = await self._llm_chat([Message("user", prompt)], max_tokens=1500)
        return _parse_plan_json(r)

    async def _execute_step(self, step: TaskStep) -> str:
        """Execute a single plan step (call a skill)."""
        self._log(f"  [step {step.step}] {step.skill}({step.args})")
        # skills.call is sync; run in thread to not block
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self.agent.skills.call, step.skill, step.args
        )
        return str(result)

    async def _summarize(self, goal: str, plan: list[TaskStep]) -> str:
        """Ask LLM to summarize the final outcome."""
        results = "\n".join(
            f"  step {s.step} ({s.skill}): {s.status} - {s.result[:200]}"
            for s in plan
        )
        prompt = f"""GOAL: {goal}

EXECUTION RESULTS:
{results}

Write a 2-3 sentence summary of what was accomplished. Be specific.
If something failed, mention it.
"""
        r = await self._llm_chat([Message("user", prompt)], max_tokens=500)
        return r.strip()

    async def run(self, goal: str) -> TaskResult:
        """Main entry: take goal, plan, execute, return result."""
        result = TaskResult(goal=goal, started_at=time.time())

        self._log(f"\n[planner] goal: {goal}")
        self._log(f"[planner] planning...")
        plan_dicts = await self._plan(goal)

        if not plan_dicts:
            result.final = "Could not generate a plan (LLM returned no valid steps)."
            result.success = False
            result.ended_at = time.time()
            return result

        self._log(f"[planner] plan: {len(plan_dicts)} steps")
        for pd in plan_dicts:
            result.plan.append(TaskStep(
                step=pd["step"],
                skill=pd["skill"],
                args=pd.get("args", {}),
                why=pd.get("why", ""),
            ))

        # Execute with replanning on failure
        replan_history = []
        for attempt in range(self.MAX_REPLANS + 1):
            result.iterations = attempt + 1
            for step in result.plan:
                if step.status != "pending":
                    continue
                if result.iterations > 1 and step.status == "ok":
                    continue  # already done
                try:
                    step.result = await self._execute_step(step)
                    step.status = "ok"
                except Exception as e:
                    step.status = "failed"
                    step.error = str(e)
                    self._log(f"  [step {step.step}] FAILED: {e}")

                    if attempt < self.MAX_REPLANS:
                        self._log(f"  [planner] replanning (attempt {attempt+2})...")
                        new_plan_dicts = await self._replan(
                            goal, step, str(e), result.plan
                        )
                        if not new_plan_dicts:
                            self._log(f"  [planner] replan returned empty, giving up")
                            break
                        # Update plan: clear pending, append new
                        for s in result.plan:
                            if s.status == "pending":
                                s.status = "skipped"
                        for pd in new_plan_dicts:
                            result.plan.append(TaskStep(
                                step=pd.get("step", len(result.plan) + 1),
                                skill=pd["skill"],
                                args=pd.get("args", {}),
                                why=pd.get("why", ""),
                            ))
                        # Restart outer for-loop from new plan
                        break
                    else:
                        # No more replans
                        break
            else:
                # Inner loop completed without break
                break

        # Final summary
        result.final = await self._summarize(goal, result.plan)
        result.success = all(s.status == "ok" for s in result.plan if s.status != "skipped")
        result.ended_at = time.time()
        return result
