# Observability Scaffold — Design Plan

This is a **design document** describing the observability scaffolding for
Ikaros. It does not modify any running service code; it proposes conventions
and points at the tooling that already exists under `bin/`.

## 1. Liveness & readiness endpoints

Every HTTP service should expose two distinct probes:

- **`/healthz`** — *alive*. Returns 200 as soon as the process is up and able
  to serve a trivial response. Cheap; no dependency checks.
- **`/readyz`** — *ready*. Returns 200 only when the service's real
  dependencies are satisfied — e.g. the model is loaded, the vector store is
  connected, config validated. Returns 503 otherwise.

### Current gap (watchdog)

The existing watchdog (`bin/ikaros-memory-watchdog.py`) only checks **port
presence** (can it connect to `:8080`?). That is insufficient: the model server
on 8080 is **lazy-loaded** — the port accepts connections before the model is
actually ready to serve inference. A readiness check that only sees the port
will report "healthy" while inference requests fail or queue.

### Proposed change (service code, future)

- Service owners add `/healthz` and `/readyz`.
- The watchdog (and any external orchestrator) should poll `/readyz`, not just
  the raw port, before marking a service healthy / routing traffic to it.
- `readyz` must reflect model-load state, not socket bind state.

## 2. Centralized process kill

On Windows, `SIGTERM` is unreliable for child processes and Python/node trees.
Kills should be centralized through a single wrapper so behavior is consistent
and auditable.

`bin/proc.py` provides this wrapper:

```bash
python bin/proc.py ps                 # list python/node processes (PID, image, cmd)
python bin/proc.py kill 8080          # kill by listening port
python bin/proc.py kill <keyword>     # kill by command-line / image keyword
```

- `kill` resolves a target by **port** (`netstat -ano`) or by **command-line
  keyword** (`wmic` / PowerShell `Get-CimInstance`).
- It then runs `taskkill /F /T /PID <pid>` (force, terminate the whole process
  tree). `/IM` by image is available as a fallback.
- **Safe by design:** if no target PID is resolved, nothing is killed; the
  tool prints what it is about to do and what it did.

This replaces ad-hoc `taskkill` invocations scattered across `.bat`/`.ps1`
scripts and gives one place to reason about process lifecycle.

## 3. Unified structured logs

All services should emit logs to a single, **gitignored** `logs/` directory as
**JSON lines** (one JSON object per line), e.g.:

```json
{"ts":"2026-07-27T10:00:00Z","level":"info","svc":"neko","msg":"started","pid":1234}
```

Conventions:

- Directory: `logs/` (already gitignored — verify it stays so).
- Format: newline-delimited JSON with at least `ts`, `level`, `svc`, `msg`.
- No secrets in logs. Redact `Authorization`, `api_key`, `token`, `password`.
- Rotation handled per-service (size/time based) to avoid unbounded growth.

### Current state

Services currently log in mixed formats (plain text, mixed streams). Migrating
to JSON lines under `logs/` is a normalization effort tracked here; the
scaffolding does not force it yet.

## 4. Summary of proposed conventions

| Area        | Convention                                    | Status            |
|-------------|-----------------------------------------------|-------------------|
| Liveness    | `/healthz` (alive)                            | proposed          |
| Readiness   | `/readyz` (deps ready, model loaded)          | proposed; watchdog gap noted |
| Kill        | `bin/proc.py kill <port\|keyword>` → `taskkill /F /T` | implemented (tool) |
| Logs        | `logs/*.jsonl` (gitignored)                   | proposed          |
| Secrets     | env / `.env` only; `secret-scan.py` in hook   | implemented (tool) + policy doc |

See `docs/SECURITY.md` for secret handling and `bin/secret-scan.py` for the
scanner referenced above.
