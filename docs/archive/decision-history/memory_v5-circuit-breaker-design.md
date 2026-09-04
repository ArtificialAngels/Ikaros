# Circuit Breaker for `memory_v5` Embedding Path — Design (NOT YET IMPLEMENTED)

> Status: **design only**. No code changes are made in this commit. This
> document is the spec; implementation tasks below are scheduled for after
> the big brother review.

---

## 1. Problem Statement

`memory_v5.retrieve()` is the multi-route (semantic / FTS / time / graph /
vault) recall entry point used by `conversation-tree`, `memory_api`,
the MCP server (`v5_memory_search`, `v5_project_retrieve`), and the
night-watchdog reflect loop.

The semantic route ultimately calls
`memory_v5.search._fetch_embedding()`, which opens an `http.client.HTTPConnection`
to the bge-m3 llama-server at `http://127.0.0.1:8587/embedding`
(`IKAROS_EMBED_URL`, default `…:8587/embedding`; see
`core/memory_v5/search.py:102-104`). The connection has `timeout=EMBED_TIMEOUT`
(=10 seconds, `search.py:104`).

When the embedding daemon is **down** (llama-server crashed, port not
listening, OOM-killed, kernel upgrading, model reloading, etc.), every
single embedding call blocks the calling thread for the full10-second
HTTP timeout before the connection raises and `except` returns `None`.
Observed symptoms (real, from the team's history):

- **2–20s tail latency on every chat turn** during the embedding outage.
  The 2s case is connection-refused on a dead port (fast ECONNREFUSED);
  the 20s case is the full TCP-level timeout plus retries because
  `_embed_conn` is the process-singleton and stays in a "stuck" state
  until the connect handshake finally fails. With multiple retrieval
  calls per turn (semantic + refresh-on-empty + keyword fallback + vault),
  the wall-clock cost is multiplicative.
- **Thread starvation**: each `retrieve()` call holds a Python thread on
  the failed connect. The watchdog / reflect loop / MCP server can pile
  up dozens of stuck threads, eventually starving the asyncio loop in
  `conversation-tree/server.py`.
- **No early exit**: there is currently no "this daemon is known-broken,
  skip immediately" state. Each call pays the full timeout penalty.

The fix is the canonical **circuit breaker**: after observing N consecutive
failures we *open* the breaker and start failing fast (microseconds, not
seconds). After a cool-down we go to *half-open* and let one probe
through; success closes the breaker, failure re-opens it.

---

## 2. Design Goals

| Goal | Concrete target |
|------|-----------------|
| Fail-fast on bge-m3 outage | When breaker is **open**, `retrieve()` completes in **< 100 ms** (target: <10 ms p99) end-to-end, regardless of `EMBED_TIMEOUT`. |
| Self-healing | After **30 s** in *open* state, breaker moves to *half-open* and lets one probe through; success closes the breaker. |
| Configurable threshold | **3 consecutive** `_fetch_embedding` failures trips the breaker (matches industry defaults: Hystrix, resilience4j). |
| Pure fail-open | When open, `retrieve()` falls back to the **FTS-only** path; vector semantic result is `[]`, no exception bubbles. Caller sees an empty `vec_list`, exactly like today's exception path, so all downstream scoring / fusion logic is unaffected. |
| Observability | Each trip / reset emits one structured log line at `WARNING` (trip) and `INFO` (close). A counter is exposed via `_circuit_state()` for tests / future `/metrics` export. |
| Zero impact on happy path | When the daemon is healthy, breaker adds **<1 µs** per call (a single dict lookup + monotonic comparison). |
| Process-local, thread-safe | State lives in module globals with a `threading.Lock`; consistent with the existing `_RET_CACHE_LOCK`, `_EMBED_LOCK`, `_chroma_write_lock` patterns. |

Non-goals (out of scope for v1):

- No cross-process coordination. Each MCP worker / watchdog instance owns
  its own breaker. (Acceptable: outages are usually daemon-wide and
  worker restart will close the breaker cleanly.)
- No automatic daemon restart. The circuit breaker is purely a **client-side**
  guard; if we want to also restart llama-server that's a separate task
  (see §6).
- No SLM fallback. Future work could route to a smaller embedding model
  while bge-m3 is down; not in this design.

---

## 3. Implementation Plan

### 3.1 Where the timeout is observed

The single bottleneck is `memory_v5.search._fetch_embedding()`
(`core/memory_v5/search.py:112-200`). Every embed call — both
`task="query"` (retrieval path) and `task="document"` (write path) —
flows through it. We add a single hook here:

```
def _fetch_embedding(text: str, task: str = "query") -> Optional[list[float]]:
    if _circuit_is_open():
        return None          # fail-fast, breaker open
    try:
        vec = _do_fetch_embedding(text, task)   # the existing body, extracted
    except Exception as e:
        _circuit_record_failure(e)              # counts toward trip
        return None
    _circuit_record_success()                   # counts toward close
    return vec
```

The existing `except Exception` already swallows connection errors and
returns `None`. We just route the failure through `_circuit_record_failure`
on the way out so the breaker can observe every timeout/refused call
without changing the public contract (still returns `None`).

`_get_embedding()` (the LRU-cached entry point, `search.py:201-220`)
inherits the behavior — its cache miss path goes through
`_fetch_embedding`, so the breaker guard works for both retrieval and
write paths.

### 3.2 Data structure

Module-level state in `core/memory_v5/search.py`, guarded by a
`threading.Lock` (matches existing module style):

```
_CIRCUIT_LOCK = threading.Lock()
_CIRCUIT = {
    "state":          "closed",     # "closed" | "open" | "half_open"
    "failure_count":  0,            # resets to 0 on success
    "opened_at":      0.0,          # monotonic seconds when breaker tripped
    "last_failure_reason": "",      # for logs
}
```

State machine:

```
       failure_count >= THRESHOLD (3)              reset_period elapsed (30s)
closed ──────────────────────────────────> open ────────────────────────> half_open
   ^                          success                                       │
   │                                                                       probe
   └─────────────────────────── success ───────────────────────────────────┘
```

- **closed → open**: failure_count crosses THRESHOLD (3). Log WARNING.
  `opened_at = time.monotonic()`.
- **open → half_open**: any call after `now - opened_at > reset_period`.
  No real probe yet — the next call **is** the probe. If the probe returns
  a non-None vector → success path → closed. If it raises / returns None →
  back to open, `opened_at = now` (cool-down restarts).
- **half_open → closed**: probe succeeded.
- **closed state success**: reset `failure_count = 0`.

### 3.3 Tunable parameters

Two new config knobs go under `preprocess_config.yaml → cache:`
(the same place `retrieve_ttl_seconds`, `embedding_max` etc. live):

```
cache:
  ...
  circuit_breaker_enabled:        true
  circuit_breaker_threshold:      3      # consecutive failures to open
  circuit_breaker_reset_seconds:  30     # cool-down before half-open probe
```

Read via the same `_cache_cfg()` helper already in `search.py:79-85`. No
env-var overrides; yaml is the single source of truth (matches the
project convention; yaml is also where `IKAROS_TZ_OFFSET` overrides
`rhythm.tz_offset`, but embedding infra tunables have consistently used
yaml — e.g. `retrieve_ttl_seconds`). Environment override would create
two sources of truth for the same value; reject.

Defaults match §2: `enabled=true`, `threshold=3`, `reset=30`.

### 3.4 Hook into `retrieve()`

`retrieve()` itself (`memory_retrieval.py:224`) does **not** need to
change. Its current exception handler around the vector path already
returns `vec_list = []` and falls through to the FTS / time / keyword
fallback / vault layers. With the breaker in `_fetch_embedding`,
an open breaker simply means every `_get_embedding` call inside
`VectorIndex.search()` returns `None`, which `VectorIndex.search()` then
maps to `[]` — exactly today's behavior. **No call-site change needed.**

Verified by inspection of `VectorIndex.search()`
(`core/memory_v5/search.py:316-355`):

```
embedding = _get_embedding(query, task="query")
if embedding is None:
    logger.warning("search: embedding failed for '%s...'", query[:30])
    return []
```

Yes — that's the fail-open already there. We just make "embedding failed"
fast.

### 3.5 Why one global breaker, not per-host

The embedding service is currently a single endpoint (`EMBED_URL` →
single daemon). When we add a fallback small model later, we may shard
into per-host breakers; not now. One global `open/closed` flag is the
simplest correct design and the only thing the spec needs.

---

## 4. Test Plan

### 4.1 Unit: breaker state machine (no network)

New file: `core/memory_v5/tests/test_circuit_breaker.py` (next to
`test_embedding_chunk.py`). Pure-stdlib + `pytest`, no live daemon.

Cases:

| # | Scenario | Expected |
|---|----------|----------|
| T1 | Three consecutive `_do_fetch_embedding` exceptions in `closed` state | After #3, `_circuit_state()["state"] == "open"`; `opened_at > 0`. |
| T2 | Two failures, then a success | `_circuit_state()["state"] == "closed"`; `failure_count == 0`. |
| T3 | Open state, immediate call | `_fetch_embedding` returns `None` without touching HTTP; the inner stub is **not** invoked (verify with `Mock.call_count == 0`). |
| T4 | Open state, `time.monotonic` advanced past `reset_seconds` | First call after cool-down is a *probe*: state moves to `half_open` (transient), then `closed` on success. |
| T5 | Open state, probe fails | State returns to `open`; `opened_at` is updated to now (cool-down restarts). |
| T6 | `circuit_breaker_enabled = False` | Behavior matches today: every call hits `_do_fetch_embedding` regardless of failures. |
| T7 | Concurrent failures from N threads (stress) | Exactly one trip log; `failure_count` reaches threshold without races. |

The mock pattern mirrors `core/memory_v5/tests/test_embedding_chunk.py`'s
`_FakeConn` / `_FakeResponse`: we monkeypatch `search._do_fetch_embedding`
to raise / return on demand and assert on `search._CIRCUIT`.

### 4.2 Integration: simulated :8587 unreachable

New test (same file or `tests/test_circuit_breaker_integration.py`):

1. Monkeypatch `EMBED_URL` to point at `http://127.0.0.1:1` (port 1,
   unbindable on Windows / Linux).
2. Monkeypatch `EMBED_TIMEOUT = 10` (already the default).
3. Call `memory_retrieval.retrieve("test query", top_k=5)`.
4. **First 3 calls**: each measures ≥ 0 ms but **≤ EMBED_TIMEOUT × 1.05**
   (we don't assert a minimum — ECONNREFUSED returns fast).
   State should reach `open` after call 3.
5. **Call 4 (and beyond, within 30 s)**: measure with
   `time.perf_counter()`. Assert **`duration < 100 ms`** (target:
   `duration < 10 ms`). Assert `vec_list == []` for the semantic path.
   Assert `len(result) > 0` (FTS / fallback still produces results).
6. Advance `time.monotonic` by 31 s (via `monkeypatch.setattr(search,
   "time", fake_time)`). Call again. State transitions through
   `half_open` and back to `open` (probe fails because the URL is still
   dead). `opened_at` is updated.

### 4.3 Manual smoke

- Run with `IKAROS_EMBED_URL=http://127.0.0.1:1/embedding` so the daemon
  is unreachable; hit the MCP `v5_memory_search` tool 5 times; verify
  p99 latency is sub-100 ms after the 3rd call; verify `WARNING` log
  line "embedding circuit breaker OPEN: 3 consecutive failures".
- Kill llama-server mid-session; verify the breaker trips within 3
  embedding calls; restart the server; wait 30 s; verify the next call
  re-closes the breaker.

### 4.4 What we are NOT testing

- **Daemon restart** — out of scope; the breaker doesn't manage the
  daemon, only the client.
- **Per-process coordination** — explicitly non-goal; each worker has
  its own breaker state.
- **Long-term stability under flaky network** — covered by T7 (stress)
  and T4/T5 (cool-down semantics); not a load test.

---

## 5. Risks & Edge Cases

### 5.1 What should NOT trip the breaker

A blanket `except Exception` on `_fetch_embedding` would catch
*programmer errors* (e.g. `TypeError` from a malformed payload) and
incorrectly credit them as "daemon failures". Mitigations:

- Only count failures where the raised exception class is one of:
  - `http.client.HTTPException` (subclasses include `RemoteDisconnected`,
    `BadStatusLine`, `ResponseNotReady`, `CannotSendHeader`,
    `CannotSendRequest`, `ImproperConnectionState`, `IncompleteRead`).
  - `socket.timeout` (alias `TimeoutError` on 3.10+; also
    `http.client.RemoteDisconnected` covers the HTTP layer).
  - `ConnectionRefusedError`, `ConnectionResetError`,
    `ConnectionAbortedError`, `OSError` with `errno` in
    `{ECONNREFUSED, ECONNRESET, EHOSTUNREACH, ENETUNREACH}`.
- Anything else (TypeError, KeyError, ValueError, json decode errors) is
  a programming error and does **not** count toward the failure budget.

This is implemented with a small classifier:

```
_NETWORK_EXC = (
    OSError,                    # catches socket.* and Connection*
    http.client.HTTPException,
)
def _is_network_error(exc: BaseException) -> bool:
    return isinstance(exc, _NETWORK_EXC)
```

(OSError covers all `socket.*` and `Connection*` errors on 3.10+; on 3.12
the connection-error classes inherit from OSError. The classifier
correctly handles all observed timeout/refused cases from the team's
history.)

### 5.2 Half-open probe contention

If 10 worker threads all hit the breaker simultaneously while it's
half-open, we don't want 10 simultaneous probes (which can mask a
genuine recovery). Use the lock to ensure only **one** probe is in
flight at a time:

```
def _circuit_is_open() -> bool:
    with _CIRCUIT_LOCK:
        if _CIRCUIT["state"] == "open":
            if time.monotonic() - _CIRCUIT["opened_at"] >= _reset_seconds():
                _CIRCUIT["state"] = "half_open"
                # fall through; allow this call to probe
            else:
                return True
        return False        # closed or half-open (probe)
```

In `half_open` state the lock serializes probes; subsequent threads in
the same window see `half_open` and proceed normally — but only the
first probe through `half_open` updates state to `closed` on success.
On failure the lock+`opened_at = now` rule from §3.2 already protects
us from re-opening races.

### 5.3 Cache poisoning

`_EMBED_CACHE` (`search.py:108-118`) is populated by successful
`_fetch_embedding` calls. A failing-daemon run never inserts to the
cache, so there's no risk of stale vectors sneaking in. No change to
cache logic needed.

### 5.4 VectorIndex singleton + breaker

`_VI` (`search.py:111`) is the per-process Chroma client. It does not
participate in the network-call path that times out (Chroma is local
disk). The breaker only guards `_fetch_embedding`, so it has no impact
on Chroma's I/O. No change needed.

### 5.5 `retrieve_ttl` interaction

The 20-second result cache (`_RET_CACHE`, `memory_retrieval.py:18-32`)
already absorbs repeat queries. The breaker is independent: TTL cache
hits short-circuit before reaching the breaker. Behavior is unchanged —
TTL hits still serve cached results; TTL misses flow through the
breaker. This is desirable: a busy chat session with 30 queries in
20s will see only ~10 actual breaker checks (the rest are TTL hits).

### 5.6 Configuration migration

Adding new yaml keys (`circuit_breaker_enabled`, etc.) is backward
compatible: `_DEFAULTS` in `preprocess_config.py` keeps the project
running if yaml is missing the new keys. Default `_DEFAULTS` must add
the same three keys to prevent drift; `tests/test_config_alignment.py`
will catch any mismatch.

### 5.7 Rollback

All new code is **additive**: new module-level constants, new functions,
new yaml keys. The hook is a single early-return at the top of
`_fetch_embedding`. Disabling = `circuit_breaker_enabled: false` in yaml,
no code revert needed.

---

## 6. Follow-up Implementation Tasks (after review)

These are sequenced for after the big brother signs off on this
design. **Do NOT start until review.**

| # | Task | Files | Verification |
|---|------|-------|--------------|
| 2.1 | Add yaml keys `circuit_breaker_enabled`, `circuit_breaker_threshold`, `circuit_breaker_reset_seconds` under `cache:`. Mirror in `_DEFAULTS`. | `core/memory_v5/preprocess_config.yaml`, `core/memory_v5/preprocess_config.py` | `tests/test_config_alignment.py` passes. |
| 2.2 | Implement breaker state machine in `core/memory_v5/search.py`: `_CIRCUIT`, `_CIRCUIT_LOCK`, `_circuit_state()`, `_circuit_is_open()`, `_circuit_record_failure()`, `_circuit_record_success()`, `_reset_seconds()`, `_threshold()`, `_is_network_error()`. | `core/memory_v5/search.py` (additive) | New `tests/test_circuit_breaker.py` cases T1–T7 pass. |
| 2.3 | Refactor `_fetch_embedding` to wrap `_do_fetch_embedding` and call the breaker hooks. Keep public signature identical. | `core/memory_v5/search.py` | Existing `tests/test_embedding_chunk.py` still passes. |
| 2.4 | Add integration test T8 (simulated :8587 unreachable; measure <100 ms). | `core/memory_v5/tests/test_circuit_breaker_integration.py` | `pytest -v` passes. |
| 2.5 | Update `docs/memory_v5-analysis-*.md` (whichever is current) with the breaker section. | `docs/memory_v5-analysis-*.md` | Doc renders, internal link from §6. |
| 2.6 | Manual smoke on a live session: kill llama-server, hit `v5_memory_search` × 5, verify WARNING log + sub-100 ms p99 after trip. | n/a | Run book note in `docs/`. |

Estimated diff size: **~120 lines** in `search.py` (additive),
**~30 lines** in `preprocess_config.{yaml,py}`,
**~150 lines** in new test file(s). No call-site changes.

---

## 7. References

- `core/memory_v5/search.py:102-200` — `_fetch_embedding`, the timeout
  bottleneck.
- `core/memory_v5/search.py:269-355` — `VectorIndex.search`, the
  fail-open `if embedding is None: return []` we rely on.
- `core/memory_v5/memory_retrieval.py:224-411` — `retrieve()`, the
  multi-route caller that needs no change.
- `core/memory_v5/preprocess_config.yaml` (current) — yaml keys live
  here.
- Existing circuit-breaker analogues for inspiration: Hystrix
  (`HystrixCommandProperties.circuitBreakerRequestVolumeThreshold`),
  resilience4j (`CircuitBreakerConfig.failureRateThreshold`,
  `waitDurationInOpenState`). Our defaults (3 / 30s) match the lower
  end of those libraries, appropriate for a local daemon with fast
  recovery.