"""
Hermes cron job scheduler (Phase 1 — MVP).

Provides persistent cron-style scheduled jobs whose action payloads are run
asynchronously when their schedule is due.  Three action types are
supported out of the box:

  * ``shell``    — run an OS command via ``asyncio.create_subprocess_exec``
  * ``task``     — call the agent's autonomous ``run_task()`` planner
  * ``webhook``  — POST a JSON body to a remote URL via ``httpx``

Jobs are persisted to ``<data_dir>/crons/jobs.json`` so they survive
process restarts.  A background asyncio loop (started in the FastAPI
``create_app`` startup hook and cancelled on shutdown) wakes up every
30 seconds, scans the enabled jobs, and triggers any whose
``next_run_at`` has fallen due.

A small, in-memory ``status`` registry tracks currently-running jobs
so the SPA can poll ``/api/crons/status`` and show a live "running"
indicator (the Hermes WebUI consumes this).

Public surface (all async except for static helpers):

  CronManager
      .jobs() -> dict[str, Job]
      .list_jobs() -> list[Job]                  (UI-friendly)
      .get_job(job_id) -> Job | None
      .create_job(name, cron_expr, action, **opts) -> Job
      .update_job(job_id, **fields) -> Job
      .delete_job(job_id) -> bool
      .enable_job(job_id) -> Job
      .disable_job(job_id) -> Job
      .trigger_job(job_id) -> dict               (returns {ok, run_id})
      .run_history(job_id, limit=50) -> list     (in-memory history)
      .status(job_id) -> dict                    (running? elapsed? state?)
      .to_api_job(job) -> dict                   (UI-shaped JSON payload)
      .to_api_runs(job, limit=50) -> list[dict]  (UI-shaped run list)
      .start() / .stop()                         (background loop)

The UI-shaped payloads include the camelCase + nested keys the Hermes
WebUI's ``panels.js`` actually reads, so the SPA can use the responses
verbatim.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import croniter  # type: ignore
from croniter import croniter as _Croniter  # type: ignore
import httpx

logger = logging.getLogger("hermes.cron")

# History entries per job kept on disk + returned by /api/crons/history.
HISTORY_LIMIT = 50
# Background loop scan period in seconds.
SCAN_INTERVAL_SEC = 30
# Max output bytes captured per run (truncate to keep the jobs.json small).
MAX_OUTPUT_BYTES = 8_000
# Max single run wall-clock (so a stuck shell doesn't block forever).
SHELL_TIMEOUT_SEC = 600


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

VALID_ACTION_TYPES = {"shell", "task", "webhook"}


@dataclass
class Job:
    """A single scheduled job, persisted in jobs.json."""
    id: str
    name: str
    cron_expr: str
    action: dict                          # {type, payload}
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_run_at: Optional[float] = None
    last_status: Optional[str] = None     # success | failed | running
    last_error: Optional[str] = None
    last_output: str = ""                 # truncated
    # History is a list of small run records.  We trim to HISTORY_LIMIT
    # on every new entry so the on-disk file never grows unbounded.
    history: list[dict] = field(default_factory=list)
    # UI-only fields (preserved on update):
    deliver: str = "local"
    profile: str = ""
    toast_notifications: bool = True
    skills: list[str] = field(default_factory=list)
    no_agent: bool = False
    script: str = ""                      # for shell actions / no_agent
    prompt: str = ""                      # for task actions
    provider: str = ""
    model: str = ""
    # Cached "next" timestamp so the scheduler doesn't re-evaluate every
    # 30s when nothing's due.  Recomputed on create/update/resume.
    _next_run_at: Optional[float] = None

    # ---- helpers ----

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict() recurses into action (which is a dict, so noop) and
        # history (list of dicts, noop).  All good.
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        """Build a Job from a persisted dict, ignoring unknown keys."""
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in d.items() if k in known}
        return cls(**clean)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class CronManager:
    """Owns the job store, the in-process status registry, and the loop.

    ``agent`` is a reference to the Hermes ``Agent`` instance.  It is only
    used for ``task`` actions (``agent.run_task``).  The manager tolerates
    ``None`` so unit tests can exercise it without a full agent.
    """

    def __init__(self, data_dir: Path, agent: Any = None):
        self._data_dir = Path(data_dir) / "crons"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_file = self._data_dir / "jobs.json"
        self._agent = agent

        # In-memory: job_id -> Job
        self._jobs: dict[str, Job] = {}
        # In-memory: job_id -> {started_at, run_id, future}
        self._running: dict[str, dict] = {}
        # In-memory: job_id -> [recent in-memory runs, mostly redundant with history]
        # (we read from job.history so no separate store)
        self._lock = asyncio.Lock()
        # Background loop bookkeeping
        self._scan_task: Optional[asyncio.Task] = None
        self._stop_evt: Optional[asyncio.Event] = None

        # Load on construction (sync — just a small JSON file)
        self._load()

    # ---- Persistence ----

    def _load(self) -> None:
        if not self._jobs_file.exists():
            return
        try:
            raw = json.loads(self._jobs_file.read_text(encoding="utf-8") or "{}")
        except Exception as e:
            logger.error(f"[cron] failed to read {self._jobs_file}: {e}")
            return
        for jid, jd in (raw.get("jobs") or {}).items():
            try:
                self._jobs[jid] = Job.from_dict(jd)
                # Recompute next run lazily on first scan
            except Exception as e:
                logger.error(f"[cron] skipping malformed job {jid}: {e}")
        logger.info(f"[cron] loaded {len(self._jobs)} job(s) from {self._jobs_file}")

    def _save(self) -> None:
        """Write the current jobs dict to disk.  Called under _lock."""
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "jobs": {jid: j.to_dict() for jid, j in self._jobs.items()},
        }
        # Atomic write: tmp then rename.  Avoids half-written files if
        # the process is killed mid-flush.
        tmp = self._jobs_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._jobs_file)
        except Exception as e:
            logger.error(f"[cron] failed to persist jobs: {e}")

    # ---- Public CRUD ----

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def create_job(
        self,
        name: str,
        cron_expr: str,
        action: dict,
        *,
        enabled: bool = True,
        deliver: str = "local",
        profile: str = "",
        toast_notifications: bool = True,
        skills: Optional[list[str]] = None,
        no_agent: bool = False,
        script: str = "",
        prompt: str = "",
        provider: str = "",
        model: str = "",
    ) -> Job:
        # Validate action shape
        if not isinstance(action, dict):
            raise ValueError("action must be a dict with 'type' and 'payload'")
        atype = action.get("type")
        if atype not in VALID_ACTION_TYPES:
            raise ValueError(f"action.type must be one of {sorted(VALID_ACTION_TYPES)}, got {atype!r}")
        if "payload" not in action or not isinstance(action["payload"], dict):
            raise ValueError("action.payload must be a dict")

        # Validate cron expression eagerly
        try:
            _Croniter(cron_expr, datetime.now())
        except Exception as e:
            raise ValueError(f"invalid cron_expr {cron_expr!r}: {e}")

        job_id = "cron_" + uuid.uuid4().hex[:10]
        now = time.time()
        job = Job(
            id=job_id,
            name=name.strip() or job_id,
            cron_expr=cron_expr.strip(),
            action={"type": atype, "payload": action["payload"]},
            enabled=enabled,
            created_at=now,
            updated_at=now,
            deliver=deliver,
            profile=profile,
            toast_notifications=toast_notifications,
            skills=list(skills or []),
            no_agent=no_agent,
            script=script,
            prompt=prompt,
            provider=provider,
            model=model,
        )
        self._compute_next(job)
        async with self._lock:
            self._jobs[job_id] = job
            self._save()
        logger.info(f"[cron] created job {job_id} ({name!r}) expr={cron_expr!r}")
        return job

    async def update_job(self, job_id: str, **fields) -> Job:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            # Whitelist of updatable fields
            allowed = {
                "name", "cron_expr", "action", "enabled",
                "deliver", "profile", "toast_notifications", "skills",
                "no_agent", "script", "prompt", "provider", "model",
            }
            for k, v in fields.items():
                if k not in allowed:
                    continue
                if k == "action" and v is not None:
                    if not isinstance(v, dict) or v.get("type") not in VALID_ACTION_TYPES:
                        raise ValueError(f"invalid action: {v!r}")
                if k == "cron_expr" and v is not None:
                    try:
                        _Croniter(v, datetime.now())
                    except Exception as e:
                        raise ValueError(f"invalid cron_expr {v!r}: {e}")
                setattr(job, k, v)
            job.updated_at = time.time()
            self._compute_next(job)
            self._save()
        logger.info(f"[cron] updated job {job_id} -> {list(fields.keys())}")
        return job

    async def delete_job(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            # If a run is in flight, let it finish (the future has the
            # reference) but remove the registry entry.
            self._running.pop(job_id, None)
            self._save()
        logger.info(f"[cron] deleted job {job_id}")
        return True

    async def enable_job(self, job_id: str) -> Job:
        return await self.update_job(job_id, enabled=True)

    async def disable_job(self, job_id: str) -> Job:
        return await self.update_job(job_id, enabled=False)

    # ---- Execution ----

    async def trigger_job(self, job_id: str) -> dict:
        """Kick off an immediate run of the job.  Returns {ok, run_id}."""
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "job not found", "job_id": job_id}

        # Refuse if already running
        async with self._lock:
            if job_id in self._running:
                return {
                    "ok": False,
                    "error": "job already running",
                    "job_id": job_id,
                    "run_id": self._running[job_id]["run_id"],
                }
            run_id = "run_" + uuid.uuid4().hex[:10]
            self._running[job_id] = {
                "run_id": run_id,
                "started_at": time.time(),
                "task": None,
            }
        # Persist the running status immediately so a status-poll right
        # after the trigger sees it.
        job.last_status = "running"
        job.last_run_at = time.time()
        await self._append_history(job, {
            "run_id": run_id,
            "run_at": job.last_run_at,
            "status": "running",
            "output": "",
            "duration_ms": 0,
            "trigger": "manual",
        })

        # Fire-and-track the coroutine
        task = asyncio.create_task(self._execute_job(job, run_id, trigger="manual"))
        self._running[job_id]["task"] = task
        return {"ok": True, "run_id": run_id, "job_id": job_id}

    async def _execute_job(self, job: Job, run_id: str, trigger: str) -> None:
        """Run the action, capture the result, append a history row."""
        start = time.time()
        status = "success"
        output = ""
        error: Optional[str] = None
        try:
            atype = job.action.get("type")
            payload = job.action.get("payload") or {}
            if atype == "shell":
                output, status, error = await self._run_shell(payload)
            elif atype == "task":
                output, status, error = await self._run_task(payload, job)
            elif atype == "webhook":
                output, status, error = await self._run_webhook(payload)
            else:
                status = "failed"
                error = f"unknown action.type: {atype!r}"
        except Exception as e:
            logger.exception(f"[cron] job {job.id} run {run_id} crashed")
            status = "failed"
            error = f"{type(e).__name__}: {e}"
        duration_ms = int((time.time() - start) * 1000)

        # Truncate output to avoid blowing up jobs.json
        if len(output) > MAX_OUTPUT_BYTES:
            output = output[:MAX_OUTPUT_BYTES] + f"\n…[truncated, {MAX_OUTPUT_BYTES}+ bytes]…"

        async with self._lock:
            # Update last_* + replace the running history row
            job.last_status = status
            job.last_error = error
            job.last_output = output
            job.last_run_at = start
            job.updated_at = time.time()
            for h in job.history:
                if h.get("run_id") == run_id:
                    h["status"] = status
                    h["output"] = output
                    h["error"] = error
                    h["duration_ms"] = duration_ms
                    h["finished_at"] = time.time()
                    break
            self._trim_history(job)
            self._running.pop(job.id, None)
            self._save()

        logger.info(
            f"[cron] job {job.id} ({job.name!r}) run {run_id} -> {status} "
            f"({duration_ms}ms) trigger={trigger}"
        )

    async def _append_history(self, job: Job, entry: dict) -> None:
        async with self._lock:
            job.history.append(entry)
            self._trim_history(job)
            self._save()

    def _trim_history(self, job: Job) -> None:
        if len(job.history) > HISTORY_LIMIT:
            # Keep the most recent HISTORY_LIMIT entries
            job.history = job.history[-HISTORY_LIMIT:]

    async def _run_shell(self, payload: dict) -> tuple[str, str, Optional[str]]:
        cmd = payload.get("cmd")
        if not cmd or not isinstance(cmd, str):
            return ("", "failed", "shell action requires payload.cmd (str)")
        cwd = payload.get("cwd") or None
        env = payload.get("env") or None
        shell_env = None
        if env:
            shell_env = dict(os.environ)
            shell_env.update({str(k): str(v) for k, v in env.items()})
        try:
            proc = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=shell_env,
            )
        except FileNotFoundError:
            return ("", "failed", "cmd.exe not found (Windows only?)")
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return ("", "failed", f"shell timed out after {SHELL_TIMEOUT_SEC}s")
        rc = proc.returncode
        out_text = (stdout or b"").decode("utf-8", errors="replace")
        err_text = (stderr or b"").decode("utf-8", errors="replace")
        combined = out_text + (("\n[stderr]\n" + err_text) if err_text else "")
        status = "success" if rc == 0 else "failed"
        err = None if rc == 0 else f"shell exited with rc={rc}"
        return (combined, status, err)

    async def _run_task(self, payload: dict, job: Job) -> tuple[str, str, Optional[str]]:
        if self._agent is None or not hasattr(self._agent, "run_task"):
            return ("", "failed", "agent.run_task not available (no agent bound)")
        # The payload may carry the actual prompt; fall back to job.prompt
        # so a "task" action created without a payload still works.
        goal = (
            (payload.get("goal") if isinstance(payload, dict) else None)
            or job.prompt
            or job.script
        )
        if not goal:
            return ("", "failed", "task action requires a goal/prompt")
        try:
            result = await self._agent.run_task(goal)
        except Exception as e:
            return ("", "failed", f"{type(e).__name__}: {e}")
        # TaskResult is a dataclass with to_dict()
        try:
            out = json.dumps(result.to_dict(), ensure_ascii=False)
        except Exception:
            out = str(result)
        status = "success" if getattr(result, "success", True) else "failed"
        err = getattr(result, "error", None) if status == "failed" else None
        return (out, status, err)

    async def _run_webhook(self, payload: dict) -> tuple[str, str, Optional[str]]:
        url = payload.get("url")
        if not url or not isinstance(url, str):
            return ("", "failed", "webhook action requires payload.url")
        method = (payload.get("method") or "POST").upper()
        body = payload.get("body")
        headers = payload.get("headers") or {}
        timeout = float(payload.get("timeout") or 30)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    r = await client.get(url, headers=headers)
                elif method == "PUT":
                    r = await client.put(url, json=body, headers=headers)
                elif method == "DELETE":
                    r = await client.delete(url, headers=headers)
                else:
                    r = await client.post(url, json=body, headers=headers)
            text = (r.text or "")[:MAX_OUTPUT_BYTES]
            if 200 <= r.status_code < 300:
                return (text, "success", None)
            return (text, "failed", f"webhook returned HTTP {r.status_code}")
        except Exception as e:
            return ("", "failed", f"{type(e).__name__}: {e}")

    # ---- Status / history ----

    def status(self, job_id: str) -> dict:
        """Live status of a single job (mirrors /api/crons/status shape)."""
        job = self._jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "job not found", "job_id": job_id}
        run = self._running.get(job_id)
        running = run is not None
        elapsed = (time.time() - run["started_at"]) if running else 0
        return {
            "ok": True,
            "job_id": job_id,
            "running": running,
            "elapsed": elapsed,
            "started_at": run["started_at"] if running else None,
            "run_id": run["run_id"] if running else None,
            "state": self._state_of(job),
            "last_status": job.last_status,
            "last_error": job.last_error,
            "last_run_at": job.last_run_at,
        }

    def run_history(self, job_id: str, limit: int = 50) -> list[dict]:
        job = self._jobs.get(job_id)
        if job is None:
            return []
        return list(job.history)[-limit:]

    # ---- Scheduling ----

    def _compute_next(self, job: Job) -> None:
        try:
            it = _Croniter(job.cron_expr, datetime.now())
            nxt = it.get_next(datetime)
            job._next_run_at = nxt.timestamp()
        except Exception as e:
            logger.warning(f"[cron] failed to compute next for {job.id}: {e}")
            job._next_run_at = None

    def _state_of(self, job: Job) -> str:
        if not job.enabled:
            return "paused"
        if job._next_run_at is None:
            return "schedule_error"
        if job.id in self._running:
            return "running"
        # If the last run failed, flag for attention
        if job.last_status == "failed" and job.last_error:
            return "needs_attention"
        return "active"

    # ---- Background loop ----

    async def start(self) -> None:
        if self._scan_task is not None and not self._scan_task.done():
            return  # already running
        self._stop_evt = asyncio.Event()
        self._scan_task = asyncio.create_task(self._scan_loop(), name="hermes-cron-scan")
        logger.info(f"[cron] background loop started (scan every {SCAN_INTERVAL_SEC}s)")

    async def stop(self) -> None:
        if self._stop_evt is not None:
            self._stop_evt.set()
        if self._scan_task is not None:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except (asyncio.CancelledError, Exception):
                pass
            self._scan_task = None
        # Wait briefly for in-flight runs to settle (best-effort)
        for run in list(self._running.values()):
            t = run.get("task")
            if t and not t.done():
                try:
                    await asyncio.wait_for(t, timeout=2.0)
                except Exception:
                    pass
        logger.info("[cron] background loop stopped")

    async def _scan_loop(self) -> None:
        """Wake every SCAN_INTERVAL_SEC and trigger any due jobs."""
        assert self._stop_evt is not None
        while not self._stop_evt.is_set():
            try:
                await self._scan_once()
            except Exception as e:
                logger.exception(f"[cron] scan error: {e}")
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=SCAN_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass  # tick over

    async def _scan_once(self) -> None:
        now = time.time()
        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            if job.id in self._running:
                continue
            if job._next_run_at is None:
                # Try to recover
                self._compute_next(job)
                continue
            if job._next_run_at <= now:
                # Compute the *next* next before triggering so we don't
                # immediately re-fire if the run is slow.
                self._compute_next(job)
                logger.info(
                    f"[cron] firing job {job.id} ({job.name!r}); "
                    f"next scheduled at {job._next_run_at}"
                )
                await self.trigger_job(job.id)

    # ---- API-shaped serialization (Hermes WebUI compatibility) ----

    def to_api_job(self, job: Job) -> dict:
        """Map a Job to the JSON the WebUI's panels.js expects.

        The UI reads:
          id, name, schedule_display, schedule.expression, next_run_at,
          last_run_at, last_error, last_status, state, prompt, script,
          no_agent, deliver, profile, toast_notifications, skills,
          provider, model, enabled, created_at, updated_at
        """
        next_at_ms = int(job._next_run_at * 1000) if job._next_run_at else None
        return {
            "id": job.id,
            "name": job.name,
            "schedule": {
                "expression": job.cron_expr,
            },
            "schedule_display": job.cron_expr,
            "next_run_at": next_at_ms,
            "last_run_at": int(job.last_run_at * 1000) if job.last_run_at else None,
            "last_status": job.last_status,
            "last_error": job.last_error,
            "last_output": job.last_output,
            "state": self._state_of(job),
            "enabled": job.enabled,
            "no_agent": job.no_agent,
            "script": job.script,
            "prompt": job.prompt,
            "deliver": job.deliver,
            "profile": job.profile,
            "toast_notifications": job.toast_notifications,
            "skills": list(job.skills),
            "provider": job.provider,
            "model": job.model,
            "action": {"type": job.action.get("type"), "payload": job.action.get("payload")},
            "created_at": int(job.created_at * 1000),
            "updated_at": int(job.updated_at * 1000),
        }

    def to_api_runs(self, job: Job, limit: int = 50) -> list[dict]:
        """Map history rows to the shape the WebUI's run list reads.

        The UI reads filename, size, modified, usage per run.
        """
        rows: list[dict] = []
        now = time.time()
        for i, h in enumerate(job.history[-limit:]):
            run_id = h.get("run_id", f"run_{i}")
            ts = datetime.fromtimestamp(h.get("run_at", now))
            # The UI does: ts = run.filename.replace('.md','').replace(/_/g,' ')
            filename = ts.strftime("%Y_%m_%d_%H%M%S") + f"_{run_id}.md"
            content = h.get("output") or ""
            size = len(content.encode("utf-8")) if content else 0
            rows.append({
                "id": run_id,
                "filename": filename,
                "size": size,
                "modified": h.get("run_at", now),
                "status": h.get("status"),
                "duration_ms": h.get("duration_ms", 0),
                "error": h.get("error"),
                # The UI uses run.usage (with input/output/total tokens,
                # estimated_cost_usd, model).  We don't have token counts
                # for shell/webhook runs, so leave empty.
                "usage": h.get("usage") or {},
            })
        return rows

    def to_api_run_content(self, job: Job, filename: str) -> dict:
        """Return the full output for a single run file (used by /api/crons/run)."""
        # The UI passes back the synthetic filename we generated.  Match
        # by run_id (suffix before .md).
        target_id = None
        for h in job.history:
            rid = h.get("run_id", "")
            if filename.endswith(rid + ".md") or filename == rid + ".md":
                target_id = rid
                break
        if target_id is None:
            return {"ok": False, "error": "run not found", "filename": filename}
        for h in job.history:
            if h.get("run_id") == target_id:
                content = h.get("output") or ""
                snippet = content[:600]
                return {
                    "ok": True,
                    "filename": filename,
                    "content": content,
                    "snippet": snippet,
                    "status": h.get("status"),
                    "error": h.get("error"),
                    "usage": h.get("usage") or {},
                    "duration_ms": h.get("duration_ms", 0),
                }
        return {"ok": False, "error": "run not found", "filename": filename}


# ---------------------------------------------------------------------------
# Singleton accessor (used by server.py)
# ---------------------------------------------------------------------------

_INSTANCE: Optional[CronManager] = None


def get_manager(data_dir: Path, agent: Any = None) -> CronManager:
    """Return a process-wide CronManager, creating it on first call."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = CronManager(data_dir, agent=agent)
    return _INSTANCE


def reset_manager_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None
