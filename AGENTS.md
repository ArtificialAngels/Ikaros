# Hermes Agent — Project Memory Bank

> **Read this first** when picking up the project after a break.
> This file captures the project state, architecture, modification history,
> debugging tips, and the gotchas we hit along the way.
>
> **Last revised:** 2026-06-15d (compressed the rest of the
> revision-log-style comments in 13 .bat / .ps1 / .py files —
> "Phase 8 multi-version layout", "(Phase 11: migrated from hermes.X)",
> "Re-exports (Phase X ready)", "Phase 12: legacy helper removed",
> "Inlined from the deleted hermes/memory.py", etc. Net −19 lines
> in source comments / docstrings with no behavioural change — see §0.7d).
> Previous: 2026-06-15b (removed duplicate browser open in
> `hermes-all.bat`; the npm package's own health-check hook already
> opens the browser — see §0.7b).
>
> Previous: 2026-06-13 (v3 phase close-out — privacy cleanup,
> `HERMES_BIN` ENOENT fix, full `.gitignore` overhaul, docs refresh;
> repo **renamed** `hermes-agent` → `hermes-agent-portable` on 2026-06-13,
> origin updated, all live doc URLs refreshed; §10 historical log
> entries retain the pre-rename URL for accuracy; **2026-06-13 (junction
> fix)** — first commit of `deps/` to git (previously local-only),
> refactored `deps/hermes-env.{bat,ps1}` to resolve `%HERMES_RUNTIME%`
> / `runtime\node23` directly instead of via four `deps\node\tools\
> llamacpp\bin\python-test` directory junctions whose absolute
> reparse-point targets broke the project when it was moved to a
> new drive letter (E: -> F:), and added an auto-heal step that
> rmdir's any leftover junction on startup — see §0.5).
> For the user-facing introduction, see [README.md](README.md).

---

## 0. 2026-06-13 — Phase Close-Out: Privacy, Stability, and Repo Hygiene

This revision is a **soft release** — no behaviour changes, no new
features, no breaking refactors. The goal is to take the v2 router-mode
codebase from "works on the author's USB stick" to "ready for a public
GitHub push". Highlights:

1. **Logs-page ENOENT fix (t8).** `GET /api/hermes/logs/agent` etc. used
   to fail with `spawn E:\Hermes Agent\bin ENOENT` whenever the user's
   shell had a stale `HERMES_BIN=<project bin dir>` env var (a relic of
   the old `supervisor.bat` that did `set "SUPERVISOR=%HERMES_BIN%\…"`).
   Fixed by pinning `HERMES_AGENT_CLI_PYTHON=%HERMES_PYTHON%` in three
   places: `deps/hermes-env.bat`, `deps/hermes-env.ps1`, and
   `modules/webui/start.ps1`. The webui's `bundledCliPythonForWindows()`
   short-circuits on this var and never even looks at `HERMES_BIN`.

2. **Repo privacy scrub.** Four files were accidentally tracked before
   `data/` and `hermes/data/` were added to `.gitignore` (in commits
   `30c716b` and `ce99e4d`):
   - `data/hermes-agent/config.yaml`  (local config with absolute paths)
   - `data/models/router-preset.ini`  (per-model NGL/ctx)
   - `hermes/data/skills/note.py`     (sample skill)
   - `hermes/data/skills/weather.py`  (sample skill)
   All four have been `git rm --cached`d. The two skill files were
   relocated to `docs/examples/skills/` as reference code. A new
   `data/models/router-preset.example.ini` provides a commented
   template. `.gitignore` was substantially expanded (see §X.1 below).

3. **`.gitignore` overhaul.** Sections added: data subdirectories
   (`data/hermes-agent/`, `data/webui/`, `data/memory/`, `data/kanban/`,
   `data/crons/`, `data/logs/`, `data/skills/`, `data/knowledge/`),
   per-model NGL config, runtime caches (`.hermes-root`), IDE state
   (`.qoder/`, `.opencode/`), all backup / dump / corrupt variants
   (`*.bak`, `*.corrupt.*.bak`, `config.yaml.corrupt.*.bak`),
   Python/Node build artifacts, and OS / shell litter.

4. **Docs refresh.** `docs/00-速览.md` port numbers were updated
   (webui 7860 → 8648), `docs/examples/skills/` created with a README,
   and the README.md rewrite is forthcoming.

The following sections of AGENTS.md (§1-§10) were left **unchanged** —
they already describe the post-Phase-11 state.

---

## 0.5. 2026-06-13 — Junction De-coupling: Drive-Letter Portability

**Symptom (F: drive failure):** `bin\hermes-all.bat` on a copy of the
project moved from `E:\Hermes Agent` to `F:\Hermes Agent` failed with:

```
[2/2] Starting all services via Python supervisor...
  > llm_engine (service)    [TIMEOUT] llm_engine — port 8080 not ready in 90s
  > bridge (service)        [OK]   bridge (:7860)
  > webui (service)         [FAIL] webui — start.ps1 exited (rc=1)
  FAILED: 2 module(s): llm_engine, webui
```

`bridge` (which only needs `HERMES_PYTHON`) started fine, but
`llm_engine` and `webui` — both of which call `deps/hermes-env.{bat,ps1}`
and depend on the env-block's PATH entries for `llama-server` and
`node.exe` — failed silently.

**Root cause:** `deps/` used to expose `runtime/`, `node23/`, and
`portable-python/` to consumers via four NTFS **directory junctions**
(`mklink /J`):

| Junction              | Target (reparse point)              |
|-----------------------|--------------------------------------|
| `deps\node`           | `E:\Hermes Agent\runtime\node23`     |
| `deps\tools`          | `E:\Hermes Agent\runtime`            |
| `deps\llamacpp\bin`   | `E:\Hermes Agent\runtime`            |
| `deps\python-test`    | `E:\Hermes Agent\portable-python`    |

NTFS junctions **store the target as an absolute path** in their
reparse-point data. They cannot hold a relative target — `mklink /J`
will silently absolutize the path you give it. When the project folder
was copied from `E:\` to `F:\` (USB slot change, drive letter
remap, etc.) the junction targets still said `E:\...` so any consumer
that touched `deps\node\...` got "file not found" (or, depending on
the access method, a misleading "success" with empty content).

`deps/hermes-env.{bat,ps1}` referenced the junctions via
`Join-Path $HERMES_DEPS 'node'` / `'tools'` / `'llamacpp\bin'` to
build the runtime PATH — which made the env block useless on any
non-E: drive. And, critically, **`deps/` was never committed to git**
in the first place — the env files, manifest, and README all lived as
local-only artifacts. So a fresh `git clone` on F: would have been
even more broken (no env setup at all). The whole `deps/` directory
is now first-time-committed in the same change.

**Fix (applied in follow-up commit to `1b06b139`):**

1. **First commit of `deps/` to git** — `hermes-env.bat`,
   `hermes-env.ps1`, `manifest.json`, `README.md`. Without this, a
   fresh clone on F: would have no env setup at all and the auto-heal
   below wouldn't even run.
2. **Refactored `deps/hermes-env.bat` and `deps/hermes-env.ps1`** to
   resolve `%HERMES_RUNTIME%` and `Join-Path $HERMES_RUNTIME 'node23'`
   directly, bypassing the junctions entirely.
3. **Added a defensive self-heal step** in both env files: on every
   invocation, walk the four historical junction paths under `deps\`
   and `rmdir /Q` anything that still shows up as a reparse point
   (the user may be running an old checkout of the project on a
   different drive). `rmdir /Q` on a reparse point does NOT recurse
   into the target — the real on-disk content in `runtime\`,
   `node23\`, and `portable-python\` is untouched.
4. **Kept the existing `.gitignore` lines** (`deps/node/`,
   `deps/tools/`, `deps/llamacpp/`, `deps/python-test/`) so even if
   someone accidentally recreates a junction or stubs the directory,
   the resulting reparse point can't be committed.

**Why not recreate the junctions on startup?** Two reasons:
* `mklink /J` requires either admin privileges or the user to have
  "Create Symbolic Link" rights; we'd be adding a UAC prompt or a
  silent failure to the most common startup path.
* Relative targets are not supported by `mklink /J`, so we'd have
  to compute the absolute path first — at which point the env file
  is just as well off using that absolute path directly (and we
  sidestep the whole junction layer).

**Tested:** `call deps\hermes-env.bat` now sets `PATH` to include
`E:\Hermes Agent\runtime` and `E:\Hermes Agent\runtime\node23`
(bat) / their PowerShell equivalents (ps1) regardless of the
`HERMES_DEPS\node` junction's presence. The self-heal logs
`[hermes-env] removed stale junction: deps\X` on first run of a
project copy whose junctions still point at a different drive.

**Files changed:**
* `deps/hermes-env.bat` — replaced junction-based PATH, added self-heal
* `deps/hermes-env.ps1` — same
* `deps/manifest.json` — first commit; documents the runtime/CUDA
  asset layout
* `deps/README.md` — first commit; explains the junction refactor
* `AGENTS.md` — this section + §0 header + §3 directory-layout update
* (no other consumer of the junctions was found in tracked code;
  `modules\*/start.ps1` and `bin\*.bat` all go through `hermes-env.*`
  for the env block and never reference `deps\node\...` directly)

---

## 0.6. 2026-06-14 — Junction Sweep: Module Manifests + Doc Comments

The §0.5 fix in the previous revision removed the dependency on the
four `deps/node/` / `deps/llamacpp/bin/` / `deps/tools/` /
`deps/python-test/` NTFS junctions from the *runtime path*
(`deps/hermes-env.{bat,ps1}` now resolves `%HERMES_RUNTIME%` and
`runtime\node23` directly). But three tracked files still contained
**documentation / config-level references** to those junction paths,
which would silently mislead anyone debugging a future F:-drive-style
failure:

| File                                | Line | Old (junction path)                          |
|-------------------------------------|------|----------------------------------------------|
| `modules/webui/module.json`         | 8    | `"node": "deps/node/node.exe"`               |
| `modules/llm_engine/module.json`    | 8    | `"binary": "deps/llamacpp/bin/llama-server.exe"` |
| `bin/fix-eol.py`                    | 17,77 | docstring + comment listing the four junctions |

The supervisor itself doesn't read those `binary` / `node` fields
(it just `subprocess.Popen()`s the corresponding `start.ps1`), so the
failure was cosmetic — but the inconsistency was a footgun for
anyone reading the manifest to figure out "where does the Node exe
actually live?". Worse, **on the user's F: drive the runtime was
present but `webui/start.ps1` still said `Node missing: F:\Hermes
Agent\deps\node\node.exe`** in `data/logs/webui.log` — meaning the
F: copy was running on an older `deps/hermes-env.ps1` that pre-dated
the §0.5 fix.

**Fix (this revision):**

1. **`modules/webui/module.json`** — `"node": "deps/node/node.exe"`
   → `"node": "runtime/node23/node.exe"`. Same path that
   `deps/hermes-env.ps1` now resolves to at runtime, so the manifest
   is now self-consistent with the actual launch path.
2. **`modules/llm_engine/module.json`** — `"binary": "deps/llamacpp/bin/llama-server.exe"`
   → `"binary": "runtime/llama-server.exe"`. The `start.ps1` does its
   own CUDA-aware picker (`%LLAMACPP_BIN%\llama-server-cuda-<v>.exe`
   preferred, `llama-server.exe` fallback), so the manifest field is
   documentation — but it should match the reality on disk.
3. **`bin/fix-eol.py`** — Updated the "What `--all` does NOT cover"
   section: previously said "skip `deps/node/`, `deps/llamacpp/`,
   `deps/tools/`, `deps/python-test/` because those are third-party
   LF scripts". That was true *when* those dirs were junction targets
   of `runtime/node_modules/` and `runtime/cuda/<v>/` — they no
   longer exist. The new comment points at the correct third-party
   locations: `runtime/node_modules/` (Node.js packages, LF) and
   `runtime/cuda/<v>/` (bundled CUDA build, LF).
4. **`deps/README.md`** — Rewrote the "no junctions in this
   directory" paragraph to explicitly *forbid* recreating the
   legacy junctions and to spell out the canonical paths
   (`runtime\node23\node.exe` / `runtime\llama-server.exe`).
   The `.gitignore` block was also relaxed — the four `deps/*`
   ignore lines are kept (so any accidental `mklink /J` is still
   blocked from committing), but the comments no longer call them
   "third-party LF scripts".
5. **`deps/manifest.json`** — Bumped version to `2026.06.14`. Added a
   new top-level `canonical_paths` section that pins the one true
   location of `node.exe` + `llama-server.exe` + `python.exe` +
   `cuda/<v>/bin/`. Auditing consumers can `jq .canonical_paths` to
   see what the project *actually* depends on, with no ambiguity.
   Changelog gains a new entry.
6. **Cleaned `deps/llamacpp/`** — the directory still contained a
   leftover empty `lib/` subdirectory from a half-completed
   refactor (the rmdir auto-heal had materialized the empty target
   directory when it removed the junction). Removed; `deps/llamacpp/`
   is now a truly empty directory (and excluded by `.gitignore`
   regardless).
7. **New smoke test `tests/smoke_hermes_env.py`** — exercises
   `bin/hermes-root.py resolve` + `init`, then walks `deps/`
   asserting that no entry is a directory reparse point. Anyone
   who runs the test on a future checkout that *does* have a
   stale junction will get a clear failure pointing at the culprit.
   Plus `tests/smoke_node_path.ps1` — dot-sources
   `deps/hermes-env.ps1` in a fresh PowerShell session and asserts
   that `$NODE == E:\Hermes Agent\runtime\node23\node.exe`
   (NOT `deps\node\node.exe`). This is the exact assertion that
   would have caught the F: drive failure in the user's report.

**Why this matters beyond cosmetics:**
- A reader scanning `module.json` to find "where is the Node exe?"
  no longer gets a misleading pointer.
- An operator debugging the supervisor logs sees manifest fields
  matching the actual `start.ps1` behaviour.
- A fresh clone on a new drive letter is now provably
  junction-free by both `smoke_hermes_env.py` (Python side) and
  `smoke_node_path.ps1` (PowerShell side) — any regression will
  fail loudly.

**Files changed:**
* `modules/webui/module.json` — junction path → runtime/* path
* `modules/llm_engine/module.json` — same
* `bin/fix-eol.py` — updated third-party LF locations comment
* `deps/README.md` — forbids recreating junctions; documents canonical paths
* `deps/manifest.json` — version bump + `canonical_paths` section
* `tests/smoke_hermes_env.py` — NEW: junction-detection smoke test
* `tests/smoke_node_path.ps1` — NEW: dot-source + alias assertion
* `AGENTS.md` — this section + header bump

---

## 0.7. 2026-06-15 — Node.js Download Step in setup-portable.bat

Prior to this revision, `bin/setup-portable.bat` downloaded two
runtime pieces (portable-python + llama.cpp) but **did not download
Node.js**, even though `modules/webui/module.json` declared Node.js
23.11.1 as bundled and `runtime/node23/` was in `.gitignore`. The
result was a setup-portable script that claimed "ALL OK" yet left
the user with a non-functional WebUI on a fresh clone.

This revision adds a third download step that bootstraps Node.js
from nodejs.org, completing the "fresh clone + setup-portable =
everything works" promise.

### Changes

* `bin/setup-portable.bat`:
  - New section `[3/4] runtime/node23/` between llama.cpp and model
    sections. Downloads `node-v23.11.1-win-x64.zip` (~30 MB) from
    `https://nodejs.org/dist/v23.11.1/` and extracts to
    `runtime/node23/`. Idempotent — re-runs skip the download if
    `node.exe` already exists.
  - New `node` subcommand: `bin\setup-portable.bat node` installs
    only the Node.js piece.
  - Status subcommand now reports `runtime/node23/ present` or
    `MISSING` (previously it was silently skipped — see bug fix below).
  - Bug fix: the `:check_runtime` status block used
    `if not exist A if not exist B (then) else (else)`, which is a
    well-known cmd.exe broken-syntax combination (chained IF + else
    silently falls through in non-trivial truth tables). Rewrote as
    a nested IF so the status branch correctly reports
    "present" vs "MISSING".

* `deps/manifest.json`:
  - Bumped `version` to `2026.06.15`.
  - Moved `node23/` out of `runtime.components` into a new
    `runtime_node23` section with explicit `source` URL,
    `required_by: ["modules/webui/"]`, and notes about the
    hermes-web-ui npm package not being auto-installed.
  - Added 2026-06-15 changelog entry.

* `AGENTS.md` — header bump + this §0.7 section.

### What is NOT changed

* The `hermes-web-ui` npm package is **intentionally not** installed
  by setup-portable.bat. Users must install it manually with:
  ```bat
  cd runtime\node23
  npm install -g hermes-web-ui
  ```
  The download step prints a `[WARN]` if the npm global install is
  absent, with the same copy-pasteable remediation command.

* No new python dependencies, no new config, no breaking changes.
  Re-running `bin/setup-portable.bat` is safe and idempotent.

### Acceptance

* `bin\setup-portable.bat status` now reports all four pieces:
  portable-python, runtime/llama-server, runtime/node23/, model.
* On a fresh checkout, after `bin\setup-portable.bat`, the
  `hermes-supervisor` can start `llm_engine` and `webui` modules.

---

## 0.7a. 2026-06-15a — Remove `.\hermes-web-ui\` dev source (npm global is the only path)

### Why

The `.\hermes-web-ui\` folder at the repo root was a gitignored
clone of the [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)
repo, intended as a dev-source fallback for the WebUI.

In practice it was **dead code**:
* `modules/webui/start.ps1` always preferred the npm global install
  at `runtime/node23/node_modules/hermes-web-ui/`.
* The dev source was only used if the npm global install was absent,
  which never happened on any working setup.
* The local clone was also perpetually 2 patches behind the npm
  install (e.g. v0.6.12 vs v0.6.14), so it would have masked real
  fixes if it ever was used.

Two copies of the same package on disk also created confusion
("which one is authoritative?") and cost ~80 MB of unused disk.

### What changed

* **Removed the folder**: `e:\Hermes Agent\hermes-web-ui\` is gone
  (gitignored, so no git impact; on F: drive users will simply have
  the stale folder too — they can `rmdir /S /Q hermes-web-ui` on
  next `git pull`).
* **`modules/webui/start.ps1`** (lines 13-30): collapsed the
  if/elseif/else dev-source fallback into a single npm-global check.
  If the launcher is missing, the script now prints a one-liner
  remediation command and exits 1. The dev-source branch and its
  `[WARN] Falling back to dev source.` message are gone.
* **`bin/setup-portable.bat`**: the post-Node.js `[WARN]` now only
  fires when `runtime/node23/node_modules/hermes-web-ui/` is absent
  (previously it required both global AND dev source to be absent).
  The remediation command is now a single `cd runtime\node23 ^&^& npm install -g hermes-web-ui`.
* **`deps/manifest.json`** (`runtime_node23.notes` + new 2026-06-15
  `2026.06.15a` changelog entry): updated to reference the npm
  install command instead of the dev source.
* **`AGENTS.md` §0.7 (this file)**: the "What is NOT changed" section
  no longer mentions the dev source. §3 (Project Layout) no longer
  lists `hermes-web-ui\` as a directory.
* **`README.md`**: removed the `hermes-web-ui\ ← 上游 EKKOLearnAI/hermes-web-ui(只读)`
  line from the directory cheat sheet. The "致谢" section no longer
  claims `hermes-web-ui\` is a sibling clone — it now points to the
  npm install command.
* **`.gitignore`**: the "Upstream clean copies" section now only
  covers `hermes-agent/`. The `hermes-web-ui/` ignore rule and the
  `git clone ... hermes-web-ui` instructions are gone; a new note
  explains that the WebUI now ships as an npm global install.

### What is NOT changed

* The `hermes-web-ui` npm package itself is still NOT bundled by
  `bin/setup-portable.bat` — it remains a manual
  `cd runtime\node23 && npm install -g hermes-web-ui` step.
* The upstream project URL ([EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui))
  is still referenced in §1 ("What This Is") and §4 ("Components")
  as the canonical source of the WebUI code.

### Acceptance

* `Test-Path 'hermes-web-ui'` returns `False` (folder deleted).
* `modules/webui/start.ps1` does not contain `$DevSource` or
  the `elseif (Test-Path ... 'hermes-web-ui/...')` branch.
* `bin\setup-portable.bat` does not contain `HERMES_ROOT%\hermes-web-ui`
  in any echo line.
* `.gitignore` no longer contains a `hermes-web-ui/` ignore pattern.
  The "Upstream clean copies" section now only covers `hermes-agent/`
  (line 39). The `git clone ... hermes-web-ui` instructions are
  removed; a 4-line note (lines 41-44) explains the WebUI ships
  via `npm install -g hermes-web-ui`.
* `bin\setup-portable.bat` still works as before: `status` reports
  the same 4 pieces; `python` and `node` subcommands are unchanged.
* `runtime/node23/node_modules/hermes-web-ui/` is the single
  authoritative source for the WebUI runtime.

---

## 0.7b. 2026-06-15b — Remove duplicate browser open in `hermes-all.bat`

### Symptom

Running `bin\hermes-all.bat` opened the WebUI in two browser tabs
(or, on machines that dedupe tabs, showed a brief flash of two
load attempts). The user would see a `localhost:8648` page load,
then a second `localhost:8648` page load a couple of seconds later.

### Root cause

Two places in the launch chain were independently opening the
browser to `http://localhost:8648/`:

1. **`bin/hermes-all.bat` (lines 96-98, pre-fix)** — after the
   supervisor returned successfully, the launcher spawned a
   PowerShell one-liner to call `Start-Process` on the URL, with
   an `explorer` fallback if PowerShell was unhappy.

2. **`runtime/node23/node_modules/hermes-web-ui/bin/hermes-web-ui.mjs`
   (line 454-456)** — the npm package's `poll`-loop runs a
   `fetch(healthUrl)`; when the server returns 200, it calls
   `execSync(isWin ? 'start ' + url : 'xdg-open ' + url)` to open
   the browser itself.

   (See [EKKOLearnAI/hermes-web-ui README](https://github.com/EKKOLearnAI/hermes-web-ui#scripts):
   `- Opens browser on successful startup` is a documented feature.)

   Sequence: supervisor → webui module → node → hermes-web-ui.mjs
   polls health → `mjs` opens browser (1st) → control returns to
   supervisor → supervisor returns to `hermes-all.bat` → `hermes-all`
   opens browser (2nd).

### What changed

* **`bin/hermes-all.bat`**: removed the two-line `Start-Process` /
  `explorer` block. Replaced with a 4-line comment pointing at
  `hermes-web-ui.mjs` line ~454 and explaining the deliberate
  non-action. Header still says "Browser opens to webui at :8648"
  because that promise is still kept — just by a different layer
  of the stack.

### What is NOT changed

* `hermes-web-ui.mjs` is left untouched. It lives in
  `runtime/node23/node_modules/` (gitignored, user-managed npm
  package) and its auto-open-on-health-check behaviour is a
  documented upstream feature we want to keep.
* `bin/hermes-supervisor.bat` (used standalone) still does NOT
  open the browser — supervisors that are not full-stack launchers
  shouldn't open windows. Only the user-facing one-click launcher
  had the redundant block.
* No cmd, ps1, or .py logic was added; this is purely a deletion.

### Acceptance

* `grep -n 'Start-Process' bin/hermes-all.bat` returns no matches
  for the URL (the comment contains the phrase "Start-Process" as
  a string, which is expected and intentional).
* `bin\hermes-all.bat` exits 0 and the user sees exactly one
  browser tab at `http://localhost:8648/`.
* `bin/hermes-supervisor.bat --start` (standalone, not via
  `hermes-all`) still does NOT open any browser tab.

---

## 0.7c. 2026-06-15c — Compress revision-log-style comments in .bat / .ps1

### What the problem was

Over 2026-06-13 → 2026-06-15 the project accumulated a few
mini "changelogs" hidden inside .bat / .ps1 header comments. They
were useful when written (the author wanted to capture *why* a
decision was made, not just *what* it does), but once AGENTS.md
became the canonical project memory bank, the in-file copies
became drift hazards:

* `deps/hermes-env.ps1` had a 10-line `CHANGELOG (2026-06-13):` block
  in its header explaining the junction-heal step — but §0.6 already
  documents the same migration in 70+ lines.
* `bin/hermes-all.bat` Step 2 had a 7-line "Was: `cmd /c
  'powershell -File ...'`" block describing a fragility the current
  Python supervisor already comments on at its own header.
* `bin/hermes-firstrun.bat` had a "Phase 10: now delegates to the
  env_bootstrap module" line pointing at a long-since-deleted
  `hermes.firstrun` script.
* `bin/setup-runtime.bat` had a one-line "Migrate old-style
  llama-cuda.zip (legacy from previous setup)" with no date or
  context.
* `tests/smoke_node_path.ps1` had two "(NOT the legacy deps/*
  junctions)" hedges that confused more than they informed.
* `deps/hermes-env.bat` had a 7-line "Why this file is so small"
  explanation that just paraphrased the line above it.

### What changed

7 files, −80 / +36 = **net −44 lines** (and every line removed
was a comment, so behaviour is bit-for-bit unchanged):

| File | Old header | New header |
|------|-----------:|-----------:|
| `deps/hermes-env.ps1`      | 23 | 5 |
| `deps/hermes-env.bat`      | 36 | 13 |
| `bin/hermes-all.bat`       | 14 | 9 |
| `bin/hermes-firstrun.bat`  | 25 | 18 |
| `bin/hermes-supervisor.bat`| 12 | 6 |
| `bin/setup-runtime.bat`    |  1 | 3 (a real new explanatory comment) |
| `tests/smoke_node_path.ps1`|  5 | 5 (rewritten, no length change) |

Compression strategy: replace the verbose block with a single
"see AGENTS.md §X.Y" pointer, where X.Y is the existing memory
section that already covers the topic (§0.4 for the cmd /c
fragility, §0.6 / §3 for the junction audit, §0.7a for the
hermes-web-ui dev source, etc.). The "see §" links are the actual
code-comment equivalent of a paper's "see Appendix B" — terse, but
the reader can find the rationale in one click.

### What is NOT changed

* No logic was touched. `git diff` shows only comment lines
  (line-context-wise: only lines whose first non-whitespace char
  is `REM`, `#`, or `--`).
* The `bin/setup-runtime.bat` migration step itself (the
  `move /Y "!RUNTIME!\llama-cuda.zip" "!ZIP_PATH!"` block) was
  kept; only the comment above it was rewritten.
* AGENTS.md structure (the `## 0.7a. / 0.7b. / 0.7c.` series) is
  preserved — these sections *are* the project's "what changed
  and why" log; the .bat / .ps1 headers should not duplicate it.

### Acceptance

* `git diff` on the 7 files shows only comment-line changes.
* `bin\fix-eol.py --all --check` still passes (no CRLF drift).
* `tests\smoke_hermes_env.py` still passes (the env file's
  actual logic was untouched).
* `bin\hermes-supervisor.bat --dry-run` still produces the same
  start order (only the comment block above the call was
  rewritten).
* `git diff --stat` reports `7 files changed, 36 insertions(+),
  80 deletions(-)` (a net reduction of 44 comment lines).

## 0.7d. 2026-06-15d — Compress remaining revision-log-style comments (Python + extras)

### What the problem was

§0.7c swept the 7 worst offenders in `bin/`, `deps/`, and `tests/`
(header CHANGELOG blocks, "Was:" / "Why:" / "Phase 10 legacy" prose),
but several Python files (mostly in `modules/`, `hermes/`) plus a few
stray `bin/*.bat` lines still carried smaller bits of revision-log noise:

* `modules/env_bootstrap/gpu_detect.py` docstring had a 4-line
  "Multi-version CUDA support (Phase 8):" block — the version list is
  the design doc; "Phase 8" was history.
* `modules/model_manager/manager.py` had three "Re-exports from the X
  submodule (Phase 10 ready / migrated in Phase 11)" comments above
  the import blocks.
* `modules/model_manager/gguf.py` and `mirror.py` each had a
  "(Phase 11: migrated from hermes.X)" tag in their module docstring.
* `bin/setup-portable.bat` had a 4-line "Move extracted files to
  runtime/. Phase 8 multi-version layout: runtime/ ... runtime/cuda/12.4/ ..."
  block — the most repetitive of the lot.
* `bin/setup-runtime.bat` had a "Updated 2026-06-06: bump to b9538"
  line and a 3-line "Migrate legacy llama-cuda.zip" block.
* `bin/gpu-detect.bat` had a "Phase 10: forwards to ..." 2-line block.
* `hermes/knowledge.py` had a 4-line "Inlined from the deleted
  hermes/memory.py" comment whose first half is just provenance.
* `tests/test_hermes.py` had 3 lines saying "hermes.gpu was removed in
  Phase 1-6. The replacement lives in modules/env_bootstrap/gpu_detect.py
  (also callable as `python -m modules.env_bootstrap.gpu_detect recommend`)".

### What changed

13 files, +24 / −43 = **net −19 lines** (all comment / docstring):

| File | Net | Notes |
|------|----:|-------|
| `bin/setup-runtime.bat`                 | −3 | dropped "Updated 2026-06-06" header + rewrote 3-line "Migrate legacy" block to 1 line |
| `bin/setup-portable.bat`                | −3 | collapsed 4-line Phase 8 layout block + "2b. (Phase 8)" header |
| `bin/gpu-detect.bat`                    | −2 | "Phase 10: forwards to ..." 2-line block → 1 line |
| `bin/hermes-all.bat`                    | −2 | "Single source of truth: resolve HERMES_ROOT + 13 derived paths" header collapsed |
| `modules/llm_engine/start.ps1`          |  0 | "(multi-version support, Phase 8)" → "(multi-version: 11.8 / 12.4 / 13.0)" |
| `modules/env_bootstrap/gpu_detect.py`   |  0 | docstring re-aligned (no length change); legacy-section header shortened |
| `modules/model_manager/manager.py`      | −4 | 3 "Re-exports (Phase X)" comments + 5-line "Phase 12: legacy helper removed" |
| `modules/model_manager/gguf.py`         |  0 | "(Phase 11: migrated ...)" stripped from docstring |
| `modules/model_manager/mirror.py`       |  0 | same |
| `hermes/knowledge.py`                   | −3 | 4-line "Inlined from the deleted hermes/memory.py" → 1 line |
| `hermes/config.py`                      |  0 | "Legacy data-dir fallback (kept for back-compat; ...)" shortened |
| `tests/test_hermes.py`                  | −2 | 3-line "hermes.gpu was removed in Phase 1-6" → 1 line |
| `docs/15-故障排查.md`                   |  0 | "这是 Phase 13 之前的老 bug / **v3 已修**" → "详见 `AGENTS.md §0` (env-loader gotcha)" |

**Pre-existing test breakage (NOT in scope of this commit):**
`tests/test_hermes.py` 跑时 6/12 失败 (Agent / Memory / KB / LLM router /
Skills / Planner 都报 `No module named 'hermes.agent'` 等) — 这是
pre-existing 的 import 路径问题:测试 `from hermes.agent import HermesAgent`
试图 import `hermes-agent/agent/`,但 `hermes-agent/` 是 .gitignore 排除的
upstream clean copy,没有 `hermes-agent/__init__.py` 把它包成 Python package,
所以 `import hermes.agent` 找不到。**与本次注释清理无关**,不在本 commit 修。

### What is NOT changed

* `hermes/static/*.js` (前端 Vue/Socket.IO 代码) 中也有 "Phase X" /
  "v0.5 changes (Phase 5 of the kanban plan)" 字样 — 但这些是 feature
  / version marker,**不计入**修订日志;它们在浏览器侧运行,不是 Hermes
  自身的历史。
* `bin/hermes-supervisor.bat` L38-39 "Hand off to Python directly. No
  cmd /c layer ..." 是设计意图解释,不是修订历史,保留。
* `hermes/config.py` L184 `/data/config/hermes.yaml (legacy data-dir fallback)`
  是 candidate 列表的文档行,属于"为什么有这个 fallback",保留。
* `modules/env_bootstrap/gpu_detect.py` L303 "Back-compat shims
  (single-version CUDA layout, pre-multi-version)" — back-compat 函数
  段头,不是修订历史,保留 (只把 "Legacy single-version CUDA runtime
  checks" 改成 "Back-compat shims")。

### Acceptance

* `git diff` 显示净减 19 行注释/docstring,无任何 logic 变化。
* `bin\fix-eol.py --all --check` 通过(.bat/.ps1 全部 CRLF 不变)。
* 4 个 .py 文件被 SearchReplace 工具在 Windows 下写成了 CRLF,
  用一行 Python (`open(f,'rb').read().replace(b'\r\n',b'\n')` + write) LF-normalize
  修回;`.gitattributes` 强制 `*.py text eol=lf` 提交时会再过一遍 LF。
* `tests\smoke_node_path.ps1` 仍然 OK。
* 头部 "Last revised" 升级到 2026-06-15d。
* `git diff --stat` 报告 `13 files changed, 24 insertions(+), 43 deletions(-)`。

---

## 1. What This Is

A **portable, USB-drive-deployable Hermes Agent** — a hybrid LLM (cloud + local)
with a modern full-featured Web UI, designed to run on any Windows PC with zero install.

**One-click UX:** `bin\hermes-all.bat` → browser opens to `http://localhost:8648/` → chat ready.

**Web UI Source:** [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) — Vue 3 + Koa + Socket.IO

---

## 2. Architecture

Three processes, each with a single responsibility:

| Port  | Process                | Role                                                           |
|-------|------------------------|----------------------------------------------------------------|
| :8080 | **llama-server**       | LLM engine. OpenAI-compatible HTTP API. Internal — not exposed. |
| :7860 | **Hermes FastAPI**     | Memory + knowledge base + RAG embeddings shim + legacy static UI. |
| :8648 | **Hermes Web UI**      | **Main Web Interface** (EKKOLearnAI/hermes-web-ui). Vue 3 + Koa + Socket.IO. Browser opens here. |

**Data flow:**
```
Browser → :8648 Hermes Web UI (Koa BFF + Vue 3 SPA)
                │
                ├── Socket.IO /chat-run → Hermes Agent Bridge → hermes-agent-source
                │
                └── REST API → :7860 Hermes FastAPI (embeddings/RAG /api/*)
                             → :8080 llama-server (chat /v1/*)
```

**Hermes Web UI** (from [EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)):
- Full-featured Vue 3 + TypeScript frontend with Koa BFF backend
- Features: AI chat, platform channels, usage analytics, cron jobs, model management,
  multi-profile, file browser, group chat, skills, logs, web terminal
- Communicates with local llama-server via OpenAI-compatible API
- Uses Hermes Agent Bridge for chat execution

llama-server only loads **one model at a time**; the WebUI shows whichever
model llama-server exposes via `--alias`). When llama-server is down, `/v1/models`
falls back to scanning `data/models/*.gguf` via `modules/model_manager/gguf.py`.
See §6 for multi-model options.

**Module architecture** (Phase 1-13, completed 2026-06-10): each service is a
self-describing `modules/<name>/` package with its own `module.json` (declares
port, dependencies, env), `start.ps1` / `stop.ps1` / `health.ps1`. The Python
`bin/hermes-supervisor.py` does a topological sort by `depends_on` and starts
them in the right order; `bin/hermes-all.bat` just calls it. New service? Add
a directory, drop in a `module.json`, and the supervisor picks it up. See
§5 (Components -> Hermes Supervisor) and §4 (HERMES_ROOT resolution).

---

## 3. Project Layout

```
E:\Hermes Agent\
├── .env                          # runtime env vars (API keys, paths)
├── AGENTS.md                     # THIS FILE
├── README.md                     # user-facing docs
├── deps\                         # ★ 2026-06-13 — FIRST commit to git (was local-only)
│   ├── hermes-env.bat            # ★ Every .bat in the project calls this first
│   ├── hermes-env.ps1            #   PowerShell equivalent (every .ps1 dot-sources this)
│   ├── manifest.json             #   Version tracking for runtime assets (downloaded by bin/setup-portable.bat)
│   ├── README.md                 #   deps/ documentation
│   # (No junctions here, by design. Earlier Hermes versions used
│   #  `mklink /J` to expose runtime/ and node23/ under deps\ as
│   #  deps\node / tools / llamacpp\bin / python-test, but junctions
│   #  store absolute reparse-point targets and break when the
│   #  project is moved to a new drive letter. hermes-env.{bat,ps1}
│   #  now resolves %HERMES_RUNTIME% directly and auto-rmdir's any
│   #  leftover junction an old copy might still be carrying — see
│   #  §0.5 for the full story.)
├── modules\                      # ★ NEW 2026-06-10 — Independent modules with module.json
│   ├── __init__.py               # Marks modules/ as a Python package
│   ├── llm_engine\               # llama-server router mode (:8080)
│   │   ├── module.json           # name="llm_engine"
│   │   ├── start.ps1             # Multi-version CUDA selection (Phase 8) + launcher
│   │   ├── stop.ps1
│   │   └── health.ps1
│   ├── bridge\                   # FastAPI bridge (:7860)
│   │   ├── module.json
│   │   ├── start.ps1
│   │   ├── stop.ps1
│   │   └── health.ps1
│   ├── webui\                    # hermes-web-ui (:8648)
│   │   ├── module.json
│   │   ├── start.ps1
│   │   ├── stop.ps1
│   │   └── health.ps1
│   ├── env_bootstrap\            # GPU detection + multi-version CUDA runtime
│   │   ├── __init__.py           # Marks env_bootstrap/ as a Python package
│   │   ├── __main__.py           # `python -m modules.env_bootstrap` entrypoint
│   │   ├── module.json           # name="env_bootstrap"
│   │   ├── start.ps1             # Verifies Python/Node/llama-server, runs GPU status
│   │   ├── stop.ps1              # No-op (one-shot tool)
│   │   └── gpu_detect.py         # Merged from hermes/gpu.py + hermes/firstrun.py
│   ├── model_manager\            # Model management (GGUF + download + mirror)
│   │   ├── __init__.py           # Marks model_manager/ as a Python package
│   │   ├── module.json           # name="model_manager"
│   │   ├── start.ps1             # Discovers models + runs manager.py list
│   │   ├── stop.ps1              # No-op (one-shot tool)
│   │   ├── downloader.py         # Merged from hermes/download.py + hermes/gopeed_client.py
│   │   ├── gguf.py               # Migrated from hermes/gguf.py (Phase 11)
│   │   ├── mirror.py             # Migrated from hermes/mirror.py (Phase 11)
│   │   └── manager.py            # Unified CLI: list/info/download/import-ollama
│   └── supervisor\               # Process orchestrator (replaces hermes-all.bat core)
│       ├── module.json
│       ├── start.ps1
│       ├── stop.ps1
│       └── orchestrator.ps1      # Topological sort + health-check lifecycle
├── hermes\                       # Python package (BRIDGE LAYER — thin glue for bridge/ and bin/)
│   ├── __init__.py               # Docstring-only: lists what's in the package and what moved to modules/
│   ├── __main__.py               # `python -m hermes` delegates to upstream hermes_cli.main
│   ├── config.py                 # config loader (env-aware, bash ${VAR:-default} expansion)
│   ├── knowledge.py              # markdown KB with chunking
│   ├── memos_client.py           # memory plugin
│   ├── watchdog.py               # process supervisor (kills orphans on parent exit)
│   └── workspace.py              # whitelisted file browser (HERMES_ROOT trust boundary)
│   # (download.py, firstrun.py, gguf.py, mirror.py, gpu.py, gopeed_client.py,
│   #  skills.py, prompts.py were removed in Phases 5/10/11 — replaced by modules/*)
├── hermes-agent\                 # ★ upstream v0.16.0 (CLEAN — DO NOT MODIFY)
├── bridge\                       # FastAPI app + monkey-patch sitecustomize
│   ├── server.py                 # FastAPI: /v1/embeddings, /v1/models, /api/*, /static/
│   └── sitecustomize.py          # Windows-only monkey-patches for upstream
├── portable-python\              # embedded Python 3.12.10 + pip deps
│   └── python.exe
├── runtime\                      # llama.cpp binaries + per-version CUDA runtimes (Phase 8)
│   ├── llama-server.exe          # CPU build
│   ├── llama-server-vulkan.exe   # AMD / Intel / NVIDIA fallback
│   ├── llama-server-impl.dll     # shared by all CPU/Vulkan/CUDA builds
│   ├── aria2c.exe                # multi-thread downloader
│   ├── gopeed-web.exe            # download bridge
│   ├── cuda\                     # ★ NEW 2026-06-10 — per-version CUDA runtime (Phase 8)
│   │   ├── 11.8\                 # NVIDIA driver 470–524; on-demand install (pypi nvidia-cu11)
│   │   │   ├── llama-server-cuda-11.8.exe
│   │   │   ├── cudart64_110.dll
│   │   │   ├── cublas64_11.dll
│   │   │   ├── cublasLt64_11.dll
│   │   │   └── manifest.json     # download_on_demand / compatible_driver_min=470.0
│   │   ├── 12.4\                 # NVIDIA driver 525–554; bundled by default
│   │   │   ├── llama-server-cuda-12.4.exe
│   │   │   ├── cudart64_12.dll
│   │   │   ├── cublas64_12.dll
│   │   │   ├── cublasLt64_12.dll
│   │   │   ├── ggml-cuda.dll
│   │   │   └── manifest.json     # status=bundled
│   │   └── 13.0\                 # NVIDIA driver 555+; on-demand install
│   │       └── manifest.json     # uses _12 DLL naming for back-compat
│   └── *.dll                     # common runtime DLLs (ggml-*, mtmd, llama, etc.)
├── data\
│   ├── models\                   # GGUF files
│   │   └── *.gguf
│   ├── webui\                    # Web UI state (SQLite, auth, sessions)
│   ├── hermes-agent\             # Agent state (config.yaml, sessions, skills)
│   ├── memory\                   # JSONL memory store
│   ├── knowledge\                # markdown KB source + index.jsonl
│   ├── skills\                   # Hermes FastAPI skill registry (built-in: time/calc/echo/...)
│   ├── hermes-agent\skills\      # ★ NEW 2026-06-08 — installed skills for Web UI (see §15)
│   │   ├── finance\              # excel-author, pptx-author, comps-analysis, dcf-model
│   │   ├── creative\             # avoid-ai-writing, claude-design, drawio-skill
│   │   ├── productivity\         # google-workspace, nano-pdf, ocr-and-documents,
│   │   │                         #   plur-memory, plur-session-end, powerpoint
│   │   └── autonomous-ai-agents\ # hermes-dojo
│   │   (others: apikey-image-gen, grok-image-to-video, hyperframes,
│   │    markdown-viewer, remotion — empty legacy stubs)
│   ├── logs\                     # hermes.log + bootstrap.log
│   ├── sessions\                 # ★ NEW 2026-06-07 — one JSON file per chat session
│   ├── kanban\                   # ★ NEW 2026-06-07 — boards.json + tasks.json + events.json
│   ├── crons\                    # ★ NEW 2026-06-07 — jobs.json (croniter-scheduled)
│   └── webui_settings.json       # ★ NEW 2026-06-07 — single-file atomic webui prefs
├── bin\                          # user-facing launchers (CRLF line endings!)
│   ├── hermes-all.bat            # ★ MAIN: one-click everything (now opens :8648)
│   │                                [Phase 3] delegates to modules/supervisor/orchestrator.ps1
│   ├── hermes-stop.bat           # kill all Hermes processes (delegates to orchestrator -Stop)
│   ├── hermes-firstrun.bat       # first-run GPU detection → modules/env_bootstrap/
│   ├── hermes-models.py          # CLI model manager (list/info/download) → modules/model_manager/manager
│   ├── hermes-console.bat        # wrapper for console.ps1
│   ├── hermes-console.ps1        # model management shell (router mode: Switch-Model)
│   ├── hermes-model-run.bat      # wrapper for live LLM log viewer
│   ├── hermes-model-run.ps1      # tail llm-server.log/err with smart colors
│   ├── hermes-health.ps1         # 3-layer liveness probe (TCP / /v1/models / /v1/completions)
│   ├── setup-portable.bat        # one-shot bootstrap: portable-python + runtime + default model
│   └── gpu-detect.bat            # one-shot GPU probe → modules/env_bootstrap/gpu_detect
│   # (start-llm-router.ps1, start-bridge-server.ps1, start-webui.ps1 removed in Phase 10;
│   #  their work is now done by modules/{llm_engine,bridge,webui}/start.ps1 via the supervisor)
├── data\models\
│   └── router-preset.ini         # ★ NEW 2026-06-09 — per-model NGL/ctx/temp for router mode
├── tests\                        # functional test scripts (kept clean)
│   └── test_hermes.py            # 17-test E2E suite (mock LLM, no GPU needed)
│   # (verify_smart_ngl.py was removed in router-mode refactor — NGL now lives in router-preset.ini)
├── .hermes-root                   # ★ NEW 2026-06-10 — Persisted HERMES_ROOT cache (atomic write)
├── .githooks/                     # ★ NEW 2026-06-10 — Versioned git hooks (tracked in repo)
│   └── pre-commit                 # bash: blocks commit if .bat/.ps1 are not CRLF
├── bin/hermes-root.py             # ★ NEW 2026-06-10 — The single source of truth for path resolution
├── bin/hermes-root.bat            # ★ NEW 2026-06-10 — Thin bat launcher (ASCII-only, CRLF)
├── bin/fix-eol.py                 # ★ NEW 2026-06-10 — One-shot CRLF normalizer for bat/ps1
├── bin/install-git-hooks.bat      # ★ NEW 2026-06-10 — One-time: sets core.hooksPath=.githooks
└── requirements.txt
```

### Path Resolution — Single Source of Truth (NEW 2026-06-10)

Hermes is **portable across USB drives** — the project root can live on `E:\`,
`F:\`, `G:\`, etc. depending on which slot the user plugged the drive into.
This is solved by a **single source of truth** that every script defers to:

```
bin/hermes-root.py       — Python resolver (the ONLY place that decides HERMES_ROOT)
bin/hermes-root.bat      — Thin bat wrapper so cmd / ps1 can call it
deps/hermes-env.bat      — Consumes the resolver's output and exports 14 HERMES_* vars
deps/hermes-env.ps1      — PowerShell equivalent
```

**Resolution priority** (first hit wins, in `bin/hermes-root.py`):

1. `HERMES_ROOT` env var (explicit override from a caller)
2. `<root>/.hermes-root` cache file (atomic write, written by `init` / `persist`)
3. `<bin>/..` (one level up from this script: assume `<root>\bin\`)
4. Scan drive letters `D:\..Z:\` for `<drive>:\Hermes Agent\portable-python\python.exe`

**Every other script** (bat, ps1, py) **MUST NOT** re-implement path resolution.
The only acceptable call sites are:

```bat
REM bat (any script in bin/ or elsewhere)
call "%~dp0..\deps\hermes-env.bat"
REM then use %HERMES_ROOT%, %HERMES_PYTHON%, %HERMES_BIN%, ...
```

```powershell
# PowerShell (any .ps1 module)
. "$PSScriptRoot\..\deps\hermes-env.ps1"
# then use $env:HERMES_ROOT, $env:HERMES_PYTHON, $env:HERMES_BIN, ...
```

```python
# Python (e.g. hermes-supervisor.py)
# Use _resolve_hermes_root(HERE) which is defined at the top of the file
# and delegates to bin/hermes-root.py resolve via subprocess.
```

**Diagnostic subcommands** of `bin/hermes-root.py`:

| Subcommand  | Purpose                                          |
|-------------|--------------------------------------------------|
| `resolve`   | Print absolute HERMES_ROOT path (single line)    |
| `verify`    | Validate all required markers exist              |
| `init`      | bat-friendly: print `KEY=VALUE` env block         |
| `scan`      | Scan all drive letters for candidates            |
| `persist`   | Write `.hermes-root` cache                       |
| `clean`     | Remove `.hermes-root` cache                      |

Example:

```
$ bin\hermes-root.bat verify
HERMES_ROOT: E:\Hermes Agent
Source: cache:.hermes-root
[OK] All required markers present
```

**CRLF maintenance** — `bin\fix-eol.py` normalizes line endings on every
`.bat` / `.cmd` / `.ps1` to CRLF. cmd.exe does not parse LF-only bat files
correctly (paths with spaces get truncated, scripts fail silently). Run
`portable-python\python.exe bin\fix-eol.py --all` after editing any bat,
or `bin\hermes-all.bat` will warn you automatically (see §7).

**Why Python and not PowerShell for the resolver** — we previously had
`modules\supervisor\orchestrator.ps1` doing the orchestration, but it
relied on `cmd /c "powershell -File ..."` bridges that broke on paths
with spaces. The Python `subprocess.Popen` with list args goes straight
to `CreateProcessW`, sidestepping all cmd /c / PowerShell -File fragility.
See §10 Debugging for the painful history.

---

## 4. Components

### Hermes Web UI (EKKOLearnAI/hermes-web-ui) — Main Interface
- **Source**: [https://github.com/EKKOLearnAI/hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui)
- **Tech Stack**: Vue 3 + TypeScript + Vite + Naive UI (frontend) + Koa 2 (BFF backend)
- **Port**: 8648 (configurable via `PORT` env var)
- **Features**:
  - AI Chat: Real-time streaming via Socket.IO `/chat-run`, multi-session management, Markdown rendering
  - Platform Channels: Unified config for 8 platforms (Telegram/Discord/Slack/WhatsApp/Matrix/Feishu/WeChat/WeCom)
  - Usage Analytics: Token tracking, cost estimation, 30-day trends
  - Cron Jobs: Create/edit/pause/resume scheduled tasks
  - Model Management: Auto-discover models, provider management, OAuth login
  - Multi-Profile: Isolated configs, import/export/clone
  - File Browser: Remote file management (local/Docker/SSH/Singularity)
  - Group Chat: Multi-agent rooms with @mention routing
  - Skills & Memory: Browse/search installed skills
  - Logs: Agent/server/error logs with filtering
  - Web Terminal: Integrated terminal via node-pty
- **Integration**: Configured via `bin\webui-new.bat` with portable Python and local llama-server

### hermes/server.py
- FastAPI app
- Serves the Hermes WebUI (nesquena/hermes-webui) from `hermes/static/` at `/` and `/static/`
- Key endpoints: `/health` (JSON status), `/v1/embeddings`, `/v1/models` (live-proxied from
  llama-server, falls back to `data/models/*.gguf` scan via `hermes/gguf.py`),
  `/api/chat/*`, `/api/sessions`, `/api/memory`, `/api/skills`, `/api/task` (autonomous plan-execute),
  `/api/webui/*` (stubs for the upstream WebUI's ~25 expected endpoints, all answered by
  `static/api-adapter.js` client-side)
- **Hash-based embeddings** at `/v1/embeddings` — used by WebUI RAG
  (search quality is poor but it boots without a real embedding model)
- **Autonomous task API** at `/api/task` — POST `{goal, wait}` triggers the Planner
  (sync returns full result, async returns `task_id` to poll at `GET /api/task/{id}`)

### hermes/llm.py
- `LLMRouter` with fallback chain
- Providers: `OpenAIProvider` (covers OpenAI, llama-server, MiniMax via
  OpenAI-compat), `AnthropicProvider`, `MockProvider`
- MiniMax config in `hermes.yaml` is `provider: openai` with MiniMax base URL

### Hermes WebUI (nesquena/hermes-webui)
- Three-panel dark UI: left session list / center chat / right workspace
- Served at `/` by FastAPI from `hermes/static/` (no Node.js, no build step)
- 16 theme skins, light/dark, streaming-markdown, KaTeX math, Prism syntax highlighting
- **api-adapter.js** (in `hermes/static/`) wraps `window.fetch` + `EventSource` to
  translate the upstream WebUI's expected endpoints onto our `/api/chat/*` + `/v1/*`
  backends. Missing endpoints (workspaces, kanban, crons, etc.) return sane empty
  defaults so the UI continues to boot. See `static/api-adapter.js` for the full route
  table (~25 mapped + ~30 no-op).
- Single-model dropdown reflects live `/v1/models`: proxies llama-server when up,
  scans `data/models/*.gguf` when down

### llama-server (b9503+)
- `--alias qwen2.5-3b-instruct` makes the model id clean (default
  returns filename like `Qwen2.5-3B-Instruct-Q4_K_M.gguf`)
- `--n-gpu-layers N` controls GPU offload: 0=CPU, 99=full GPU, N=hybrid
- Per-model NGL/ctx-size come from `data\models\router-preset.ini`
  (see `bin\start-llm-router.ps1` for the launcher)
- b9538+ supports router mode (`--models-dir` + `--models-preset` +
  `--models-max`): single process hosts all GGUFs in
  `data\models\`, switches on `model` field, LRU evicts.

### hermes/sessions.py (NEW 2026-06-07)
- `SessionStore` — disk-backed chat session store. One JSON file per session
  at `hermes/data/sessions/<session_id>.json`. Atomic writes (tempfile + `os.replace`).
- API: `list_sessions()`, `get_session(sid)`, `upsert_session(sid, data)`,
  `append_message(sid, msg)`, `delete_session(sid)`, `rename_session(sid, title)`.
- Replaces the previous in-memory `agent._chat_sessions` cache.
- Used by `/api/chat/sessions`, `/api/chat/sessions/{id}` (GET, DELETE, PATCH),
  and `/api/chat/start` (persists user + assistant messages on each chunk/finish).

### hermes/workspace.py (NEW 2026-06-07)
- `WorkspaceManager` — whitelisted file browser. Trust boundary is `HERMES_ROOT`.
  Whitelisted subdirs: `data/{knowledge,memory,models,skills,logs}`, `docs`, `tests`,
  plus root files `README.md` / `AGENTS.md`. Anything else returns 403.
- Path-traversal defense: `Path.resolve()` + `Path.is_relative_to()` + Windows
  `normcase` (case-folding FS bypass).
- API: `list_workspaces()`, `add_workspace(path)`, `remove_workspace(path)`,
  `list_dir(rel)`, `read_file(rel, max_bytes=200k)`, `media_path(rel)` (binary).
- Persistence: `hermes/data/workspaces.json` (atomic + `asyncio.Lock`).
- Endpoints: `/api/workspaces`, `/api/workspaces/add`, `/api/workspaces/remove`,
  `/api/list`, `/api/file`, `/api/media`.

### hermes/webui_settings.py (NEW 2026-06-07)
- `WebUISettingsStore` — atomic JSON store for the WebUI's user preferences
  (theme, skin, language, display, agent, memory, session, privacy, ...).
- 32 default keys defined in `DEFAULT_SETTINGS`. POST applies a 1-level
  nested deep-merge (nested dict keys are merged, not replaced).
- API: `get_settings_store()` (singleton), `.load()`, `.update(patch)`, `.all()`.
- Persistence: `hermes/data/webui_settings.json` (atomic + `asyncio.Lock`).
- Endpoints: `GET /api/webui/settings` returns full object;
  `POST /api/webui/settings` accepts partial patch and returns `{ok, settings}`.

### hermes/kanban.py (NEW 2026-06-07)
- `KanbanStore` — board/task/event store with atomic JSON writes, asyncio Lock,
  capped events log (2000), CSS-safe color sanitizer, default board + 5
  sample tasks bootstrap on first use.
- Board model: `board_id`, `slug`, `name`, `description`, `icon`, `color`,
  `columns` (list of column ids), `created_at`, `updated_at`, `archived`.
- Task model: `task_id`, `board_id`, `title`, `body`, `status`, `assignee`,
  `tenant`, `priority`, `tags`, `due_at`, `blocked`, `blocked_reason`,
  `created_at`, `updated_at`, `archived`.
- 22 endpoints under `/api/kanban/*` (all 4 HTTP methods on boards/tasks,
  plus block/unblock, bulk, comments, worktree, aggregates, events).
- SSE/dispatch/comments/worktree are noop stubs per spec; UI falls back to
  30s polling on `/api/kanban/events`.
- Persistence: `hermes/data/kanban/{boards,tasks,events}.json`.

### hermes/cron.py (NEW 2026-06-07)
- `CronManager` — scheduled job runner using `croniter` for next-fire
  calculation. 30-second background loop scans for due jobs and dispatches
  them in background asyncio tasks.
- Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST).
- Job model: `id`, `name`, `cron_expr`, `action`, `enabled`, `no_agent`,
  `script`/`prompt`, `deliver`, `profile`, `toast_notifications`, `skills`.
  Serialized with UI-shape fields (`schedule_display`, `next_run_at`,
  `last_run_at`, `last_status`, `last_error`, `last_output`, `state`).
- Endpoints: `/api/crons`, `/api/crons/create`, `/api/crons/update`,
  `/api/crons/delete`, `/api/crons/run`, `/api/crons/pause`, `/api/crons/resume`,
  `/api/crons/status`, `/api/crons/history`, `/api/crons/delivery-options`.
- Persistence: `hermes/data/crons/jobs.json` (atomic + `asyncio.Lock`).
  Background loop started in `create_app` startup; stops on shutdown.

### hermes/llm.py (streaming support, NEW 2026-06-07)
- `LLMRouter.stream_chat(...)` and `collect_stream(...)` — async generator over
  provider chunks. Providers: `OpenAIProvider.stream()` (covers OpenAI,
  llama-server, MiniMax via OpenAI-compat), `MockProvider.stream()`.
- Used by `/api/chat/start` → `asyncio.Queue` → `/api/chat/stream/{id}` SSE.

### bin/hermes-model-run.{bat,ps1} (NEW 2026-06-07)
- **Purpose**: dedicated real-time viewer for the llama-server backend
  (model load progress, offload decisions, HTTP request lines, prompt-eval
  / generation timing, errors). Window title: "Hermes Model Running".
- Tails `hermes/data/logs/llm-server.log` + `llm-server.err` (written by
  `start-llm.ps1` via `RedirectStandardOutput` / `RedirectStandardError`).
- Smart color highlighting: magenta for model load, green for "HTTP server
  listening", yellow for eval time / tokens-per-second, red for errors,
  cyan for HTTP request lines, dark-yellow for warnings.
- 400ms polling loop, file-locked-safe (skip round on lock error).
- Boot banner + initial 5-line tail dump of each file.
- `hermes-all.bat` step 7 launches it; `hermes-stop.bat` step 5 kills its
  powershell process and step 6 closes the cmd window by title match.
- **Prerequisite**: `start-llm.ps1` no longer passes `--log-disable` (was
  silencing everything). With it removed, llama-server streams its internal
  log to stdout, which gets redirected to `llm-server.log` for the viewer
  to tail.

---

## 5. Key Decisions & Why

| Decision                                 | Why                                                         |
|------------------------------------------|-------------------------------------------------------------|
| Hermes WebUI (nesquena) as main UI       | Mature three-panel dark UI, no Node.js build, 16 theme skins, streaming markdown |
| `--alias qwen2.5-3b-instruct`            | Avoid filename-based model id mismatch between GGUF and WebUI |
| Hash-based embeddings (RAG shim)         | Avoid downloading 100MB+ embedding model just to boot RAG   |
| One launcher `hermes-all.bat`            | User experience: one double-click = everything             |
| Smart NGL (auto offload calculation)      | Support loading models larger than VRAM (e.g. 22GB on 8GB) |
| Bundle all llama.cpp variants             | Portable — works on any GPU (NVIDIA/AMD/Intel)             |
| Skip CPU when VRAM full of weights       | Hybrid offload with <5 layers = full CPU is faster         |
| CRLF line endings for all .bat files      | cmd.exe does NOT parse LF-only files (bug: truncates paths)  |
| client-side api-adapter.js                | Translate upstream WebUI's endpoints onto ours — no need to fork upstream Python BFF |

---

## 6. Multi-Model Loading

llama-server is **single-model per process**. Three options:

1. **Switch model** — kill llama-server, restart with different `--model`:
   ```bat
   set MODEL=%HERMES_ROOT%\data\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf
   bin\hermes-all.bat
   ```

2. **Multiple llama-server instances** on different ports (8080, 8081, 8082),
   switch the WebUI's model picker to point at the desired one. Resource-hungry
   but lets you hot-swap.

3. **Ollama-compatible import** — run `python hermes/scripts/import_ollama_blobs.py`
   to convert Ollama `sha256-XXXXX` blobs to `.gguf` files in `data\models\`.

For the 22.8GB `f5ee307a2982.gguf` (qwen3) on 8GB VRAM, smart NGL
calculates ~16 layers on GPU + rest on CPU. Works, but slow.

---

## 7. Common Gotchas (READ THIS BEFORE EDITING!)

### Windows / cmd.exe
- **NEVER hardcode a drive letter** (e.g. `E:\Hermes Agent\...`) in any
  script. Hermes is portable across USB drives — the slot the user plugs
  the drive into determines the letter. Always go through the resolver:
  - bat: `call "%~dp0..\deps\hermes-env.bat"` then use `%HERMES_ROOT%`
  - ps1: `. "$PSScriptRoot\..\deps\hermes-env.ps1"` then use `$env:HERMES_ROOT`
  - py: `from bin.hermes_root import resolve` or use `_resolve_hermes_root(HERE)`
  See §3 (Path Resolution) for the full mechanism. If you see `E:\` in any
  new file under `bin\` or `deps\`, reject the change.
- **CRLF for .bat files!** LF-only → cmd can't parse → paths with spaces
  get truncated, scripts fail silently. **Don't hand-roll a PowerShell
  converter** — use the project tool:
  ```bat
  portable-python\python.exe bin\fix-eol.py --all
  ```
  After every bat edit, verify: `CR=NN, LF=NN` (must be equal). The same
  tool also fixes `.ps1` (which we keep LF but the tool normalizes anyway —
  harmless). `bin\hermes-all.bat` calls `fix-eol.py --check` at startup and
  warns you if any bat is in a bad state.
- **Pre-commit hook blocks LF-only bat commits.** Run once after cloning:
  ```bat
  bin\install-git-hooks.bat
  ```
  This sets `core.hooksPath=.githooks` (the versioned hooks directory at the
  repo root, NOT the per-clone `.git\hooks\`). From then on every
  `git commit` runs `.githooks\pre-commit`, which calls
  `portable-python\python.exe bin\fix-eol.py --all --check` and aborts the
  commit if any of the 17 Hermes-owned scripts (bin/*.bat/*.cmd/*.ps1 +
  deps/hermes-env.{bat,ps1}) have wrong line endings. To skip in an
  emergency: `git commit --no-verify`. To uninstall:
  `bin\install-git-hooks.bat uninstall`. The hook gracefully no-ops on
  fresh clones where `portable-python/python.exe` is missing yet.
  ```powershell
  # Legacy hand-rolled conversion (only if fix-eol.py is broken):
  $c = Get-Content file.bat -Raw
  [System.IO.File]::WriteAllText(file.bat, $c -replace "`r`n","`n" -replace "`n","`r`n", [System.Text.UTF8Encoding]::new($false))
  ```

- **`cmd /c "path with space"`** — truncates at the space. Workarounds:
  - `cmd /c "bat.bat" arg` (bat is relative, run from its dir)
  - Wrap the whole command in outer quotes
  - Or invoke from a wrapper bat

- **`for /f "tokens=*" %%V in ('cmd with --flag=value,flag2')`** — the comma
  breaks the parser. Use `usebackq` + backticks:
  ```bat
  for /f "usebackq tokens=*" %%V in (`cmd --flag=value`) do ...
  ```
  Or wrap in PowerShell to avoid cmd parsing entirely.

- **`set /a` is 32-bit signed integer.** For files > 2GB, use PowerShell:
  ```bat
  for /f "tokens=*" %%S in (`powershell -NoProfile -Command "$f=(Get-Item -LiteralPath '%FILE%').Length; [int][math]::Floor($f/1MB)"`) do set "MB=%%S"
  ```

### Hermes WebUI (nesquena upstream)
- **`__WEBUI_VERSION__`, `__MAX_UPLOAD_BYTES__`, `__CSRF_TOKEN_JSON__` placeholders
  in `index.html` are filled in by server.py** at request time. If you copy the
  static dir to a different web server, do the substitution yourself or the
  bootstrap script will 404 on the versioned asset URLs.
- **Adapter must load BEFORE `pwa-startup.js`** — `<script src="api-adapter.js">`
  in `<head>` is non-negotiable; the wrapper needs to be in place before any other
  script calls `fetch`.
- **`/v1/models` resolution order** — first try `http://127.0.0.1:8080/v1/models`
  (live proxy), then `data/models/*.gguf` scan via `hermes.gguf.list_gguf_models()`,
  then empty. The WebUI uses this list as the model dropdown — if llama-server is
  down, you see filename stems like `Qwen2.5-7B-Instruct-Q4_K_M`. To use one, your
  `hermes.yaml` `llm.router.providers.local.alias` must match.
- **Hermes Agent upstream is `nousresearch/hermes-agent`** — the WebUI was built
  for that. Our adapter translates the ~25 endpoints it actually hits on boot/chat;
  everything else (workspaces, kanban, crons, voice, OAuth, etc.) is no-op'd. Don't
  expect those panels to do anything useful.

### llama-server
- **Model id from `/v1/models` defaults to the filename** (ugly).
  Use `--alias clean-name` to override.

- **Single model per process.** To switch, restart with different `--model`.

### hermes (Python)
- **Config env expansion**: `os.path.expandvars` doesn't support bash
  `${VAR:-default}` syntax. `hermes/config.py` has custom regex
  `_ENV_VAR_RE` for this — don't replace with plain `expandvars`.

- **`.env` loading**: config.py searches in cwd, parent, then `hermes/`
  package parent dir (absolute path). Works regardless of cwd.

---

## 8. Modification Log (chronological)

| When        | Change                                                              |
|-------------|---------------------------------------------------------------------|
| Day 1       | Built hermes package: config, llm router, memory, KB, skills, server |
| Day 1       | Embedded portable-python 3.12.10 + llama.cpp CPU b9503 + 3 GGUF     |
| Day 1       | Built React admin SPA → `hermes/web_dist` (now deprecated)           |
| Day 1       | Wrote `bin\hermes-all.bat` v1 (vulnerable to path issues)            |
| Day 2       | Fixed hermes-all.bat CRLF issue (was LF-only, paths truncated)        |
| Day 2       | Added `--alias qwen2.5-3b-instruct` to llama-server                  |
| Day 2       | Integrated Open WebUI 0.9.6: install + bootstrap + config            |
| Day 2       | Added `/v1/embeddings` shim in hermes/server.py (hash vectors)        |
| Day 2       | Discovered: OW shows system Ollama models (we want only ours)        |
| Day 3       | Fixed: `ENABLE_OLLAMA_API=false` in hermes-all.bat + start-openwebui.bat |
| Day 3       | Created `hermes/scripts/bootstrap_openwebui.py` (auto signup + add model) |
| Day 3       | Created `setup-runtime.bat` (download ALL llama.cpp variants, aria2 16-thread) |
| Day 3       | Added `start-llm-smart.bat` (auto NGL based on model size + VRAM)    |
| Day 3       | Imported user's Ollama blob (qwen3 22.8GB) → `f5ee307a2982.gguf`    |
| Day 3       | Project cleanup: trashed 1-off test scripts, kept 2 functional tests |
| Day 3       | THIS FILE created                                                 |
| 2026-06-06  | **llama.cpp b9503 → b9538 upgrade** (Qwen3 MoE / Qwen3.5 MoE support) |
| 2026-06-06  | Cleaned 22.29GB `Qwen3.6.incompatible-b9503.gguf` (model+llama.cpp upgrade obsoletes) |
| 2026-06-06  | Trashed `hermes/web_dist/` (React admin SPA) — server.py uses HTML_FALLBACK now |
| 2026-06-06  | Trashed `scripts/` (6/3 legacy) — bin/ replaced all |
| 2026-06-06  | Switched default model: `Qwen2.5-7B-Instruct` → `Qwen3.5-35B-A3B-Q4_K_M` (20.5GB MoE) |
| 2026-06-06  | Verified Qwen3.5-35B-A3B works: n_params=34.66B, n_ctx_train=262144, chat "Hello! How can I assist you today?" OK |
| 2026-06-06  | Studied ComfyUI-aki-v3 for inspiration (see §13 Roadmap below)        |
| 2026-06-06  | **A**: `bin/hermes-models.py` CLI 多模型切换器 (list/switch/download/gopeed) — parses GGUF v3 header (arch, ctx_len, n_tensors) |
| 2026-06-06  | **A**: `hermes/gguf.py` module — extracted GGUF v2/v3 header parser, reused by CLI + web UI |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher` page — web UI for model switching (replaces deprecated `web_dist/` admin SPA) |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/switch` (POST) — async subprocess runs `switch-model.bat`, returns when done |
| 2026-06-06  | **A**: `hermes/server.py` `/launcher/download` (POST) — creates gopeed-web task via Python communication bridge |
| 2026-06-06  | **A**: Integrated `gopeed-web` (89MB single exe) into `runtime/gopeed-web.exe` as the Python communication bridge for downloads |
| 2026-06-06  | **B**: `hermes/firstrun.py` + `bin/hermes-firstrun.bat` — detects NVIDIA/AMD/Vulkan, downloads cudart via gopeed-web if missing, graceful CPU fallback |
| 2026-06-06  | **B**: `hermes-firstrun.bat` wired into `hermes-all.bat` as Step 0 (idempotent check, doesn't block startup) |
| 2026-06-06  | **C**: `hermes/doctor.py` + `bin/hermes-doctor.bat` — 8-section health report (runtime, models, GPU, services, gopeed, python, disk, env) |
| 2026-06-06  | **D**: `hermes/gopeed_client.py` — gopeed-web API client (urllib only, no deps). gopeed-web API differs from desktop gopeed (POST body wrapped in `req`, response `data` is task_id string, opts at `meta.opts`) |
| 2026-06-06  | Memory: 120s bash timeout, gopeed+file-lock download check, gopeed-web API quirks, GGUF v3 type table |
| 2026-06-10  | **Hot-swap architecture (Phase 14)** — closed WebUI ↔ llama-server disconnect |
| 2026-06-10  | **bridge/server.py v0.3.0** — added 5 endpoints: `POST /v1/models/swap`, `GET /v1/models/status`, `POST /v1/models/evict`, `POST /v1/models/warmup`, `GET /v1/models/warmup/{id}`. All paths from env vars (HERMES_BRIDGE_URL/HERMES_MODELS_DIR), no hardcoded drives. |
| 2026-06-10  | **hermes_bridge.py patch** — added `model_swap` / `model_warmup` / `model_status` actions to BOTH worker (line 2589) and broker (line 3808) `handle()`. Broker does NOT auto-forward unknown actions — must mirror. Uses stdlib `urllib.request` (no httpx dep) for portability. |
| 2026-06-10  | **E2E verified** — `model_swap` HTTP 200 `{"success":true}`; `model_warmup` 2 models in <3s with progress polling; `model_status` returns resident + available list; `/v1/chat/completions` still works after swap (HTTP 200 with valid usage). |
| 2026-06-10  | **llama-server b9538 router mode placeholder quirk** — when nothing is resident, `/props.model_alias == "llama-server"` and `/props.model_path == "none"`. Used in evict endpoint to return noop instead of triggering reload. |
| 2026-06-06  | **CRITICAL BUGFIX**: `hermes-stop.bat` v1 used `taskkill /IM llama-server.exe` literal — but the actual binary is `llama-server-cuda-12.4.exe`. Old stop left **stale llama-server processes holding VRAM** (one PID survived 20+ hours, working set -1140MB = leaked kernel handles). v2 fix: use `llama-server*` wildcard + PowerShell-based kill for clean output. |
| 2026-06-06  | Also fixed: all `bin\*.bat` files were **LF-only** (Edit tool had stripped CRs), causing cmd.exe to mis-parse multi-line `powershell -Command` blocks (visible as random "X 不是内部或外部命令" noise). Restored CRLF on all 9 bat files.
| 2026-06-07  | **Track 1: streaming-and-sessions** — `hermes/sessions.py` (SessionStore: one JSON per session, atomic write + asyncio.Lock) + `hermes/llm.py` (`stream()` on OpenAI/Mock, `stream_chat`/`collect_stream` on router) + `hermes/server.py` (`/api/chat/start`, `/api/chat/stream/{id}` SSE, `/api/chat/cancel`, `/api/chat/stream/status`, persistent `/api/chat/sessions{,/{id}}`, legacy `/api/chat/send` kept) + `hermes/static/api-adapter.js` (removed EventSource mock, chat/start is passthrough, cancel/status forward to real endpoints). SSE event shape: `{type: starting|delta|done|error|replay, content?, stream_id, session_id, model, provider, ...}`. Persistence path: `data/sessions/<session_id>.json`. Owner had to move the catch-all `@app.api_route('/api/{path:path}')` to the very last position in `create_app` because FastAPI matches routes in registration order; added a multi-line warning comment. |
| 2026-06-07  | **Track 2: workspace-browser** — `hermes/workspace.py` (WorkspaceManager: HERMES_ROOT trust boundary, case-insensitive whitelist `data/{knowledge,memory,models,skills,logs}` + `docs` + `tests` + root files `README.md`/`AGENTS.md`, path-traversal defense via `Path.resolve()` + `is_relative_to()` + Windows `normcase`, binary sniff for `read_file`, mime for media, atomic JSON persistence). Added 6 endpoints to `server.py`: `GET/POST /api/workspaces{,/add,/remove}`, `GET /api/list`, `GET /api/file`, `GET /api/media`. `api-adapter.js` updated: removed noop transforms, added `dropParams` route field, made workspaces/list/file/media passthrough. Persisted at `data/workspaces.json`. |
| 2026-06-07  | **Track 3: settings-persistence** — `hermes/webui_settings.py` (WebUISettingsStore + DEFAULT_SETTINGS with 32 keys + 1-level nested deep-merge + asyncio.Lock + atomic write). Replaced server's `GET/POST /api/webui/settings` noop handlers with real ones; the store singleton is instantiated in `create_app`. `api-adapter.js` simplified: removed the hardcoded 32-key default on `/api/settings` GET, both GET and POST are now passthrough. Persisted at `data/webui_settings.json`. |
| 2026-06-07  | **Track 4: kanban-board** — `hermes/kanban.py` (KanbanStore: Board+Task models, atomic JSON writes for boards/tasks/events, asyncio.Lock, capped events log at 2000 entries, CSS-safe color sanitizer, default board + 5 sample tasks bootstrap, board switcher pointer). 22 endpoints registered in `server.py` between `/api/webui/noop` and the workspace block (all 4 HTTP methods on `boards/{slug}` and `tasks/{id}` so spec's PUT and UI's PATCH both work). `api-adapter.js` v0.5: 14 explicit kanban passthrough routes using `url:null+passthrough:true`. SSE/dispatch/comments/worktree are intentional noop per spec (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`. |
| 2026-06-07  | **Track 5: cron-scheduler** — `hermes/cron.py` (CronManager + Job dataclass + atomic JSON persistence + 30s background scan loop + shell/task/webhook runners + UI-shape serializers). Started in `create_app` startup, stopped on shutdown. 10 endpoints registered BEFORE the `/api/{path:path}` catch-all: list/create/update/delete/run/pause/resume/status/history/run+filename/delivery-options. Action types: `shell` (subprocess), `task` (agent.run_task), `webhook` (POST). `api-adapter.js`: replaced 2 crons noop entries with passthrough; fixed fetch wrapper to use original URL when `route.url` is null. `requirements.txt`: added `croniter==6.0.0`. Persisted at `data/crons/jobs.json`. |
| 2026-06-07  | **Track 6: final-integration** — All 5 tracks verified end-to-end on a live mock-mode server (port 7860). 13 GET/POST endpoints all 200; SSE stream produced 53+ chunks; settings (`theme=sepia`, `display.streaming=false`) + session `e2e-test-session` (5 messages) + kanban default board (6 tasks) all survived `Stop-Process` + restart. Kanban CRUD roundtrip (POST→GET→PATCH→DELETE) verified. Cron CRUD roundtrip (create→list→run→delete) verified. `bin\*.bat` CRLF audit: 9/13 CRLF-OK, 4 still LF-only (`gpu-detect.bat`, `hermes.bat`, `model-manager.bat`, `verify-server.bat`) — pre-existing issue, not introduced by these tracks. AGENTS.md §3/§4/§8/§12/§13 updated; README.md mentions new WebUI features. Full e2e transcript in `deliverable-final.md`.
| 2026-06-07  | **Hermes WebUI merge (nesquena/hermes-webui)**: replaced the in-house single-file `chat_ui.py` with the upstream three-panel dark UI. Source: `D:\PZS0X\下载\hermes-webui-master\hermes-webui-master\static\` (3.5MB: 18 vanilla-JS files + 366KB CSS with 16 skins + vendored streaming-markdown + KaTeX). Copied to `hermes/static/`, new UI served at `/`; old `chat_ui.py` kept as `/chat` fallback. Adaptation: `hermes/static/api-adapter.js` (23KB) wraps `window.fetch` + `EventSource` to translate the new UI's ~25 expected endpoints onto our existing `/api/chat/*` + `/v1/*` backends; missing endpoints (workspaces, kanban, crons, etc.) return sane empty defaults so the UI continues to boot. server.py: `GET /` serves `hermes/static/index.html` with `__WEBUI_VERSION__` / `__MAX_UPLOAD_BYTES__` / `__CSRF_TOKEN_JSON__` placeholder substitution; added 11 new `/api/webui/*` stub endpoints; added `app.mount("/static", StaticFiles(...))` for the new asset tree. **Two bugs hit and fixed during integration**: (1) `/api/webui/*` registered AFTER the catch-all `/api/{path:path}` got swallowed — moved all webui routes before the catch-all; (2) duplicate `@app.get("/")` returned the legacy DASHBOARD_HTML — removed the old one. **Knowingly not implemented** (UI may show empty panels / "no data" / disabled features): streaming Markdown renders but no real token-by-token SSE (we block on /api/chat/send and emit one fake delta — works but not live), workspaces/file browser, kanban boards, cron jobs, projects, memory editor, voice, OAuth/passkeys, multi-profile, web terminal. Adding these is straightforward but out of scope for v0.1.
| 2026-06-07  | **Full cutover to Hermes WebUI**: deleted `hermes/chat_ui.py` (336 lines), `bin/hermes-web.bat`, `data/openwebui/` (residual data), and `docker/` (unused). Removed `GET /chat` endpoint, `DASHBOARD_HTML` constant, and the `DASHBOARD_HTML` fallback from `GET /` and `GET /health` — both now 503 if `hermes/static/index.html` is missing (install is broken). `bin/hermes-all.bat` v8: title says "WebUI mode", opens `http://localhost:7860/` (not `/chat`), drops the legacy `:7870` line. **LLM model dropdown fix**: rewrote `GET /v1/models` to **live-proxy** `http://127.0.0.1:8080/v1/models` when llama-server is up (so the UI sees the exact `--alias` model llama-server has loaded), then fall back to scanning `data/models/*.gguf` via `hermes/gguf.py` and exposing filename stems as model ids (matches llama-server's default alias), then empty list. Response now includes `_size_gb` / `_arch` / `_ctx_len` / `_quant` / `_filename` extras on each model entry (the adapter surfaces them in the WebUI's tooltip). Updated AGENTS.md §1, §2, §3, §4, §5, §7, §9, §10, §12 to remove all Open WebUI references and reflect the new architecture (2 processes instead of 3, WebUI at :7860, no `:7870`).
| 2026-06-07  | **Hermes Model Running window**: new persistent `bin/hermes-model-run.bat` + `.ps1` that tails `data/logs/llm-server.log` + `llm-server.err` with smart color highlighting (model load magenta, HTTP requests cyan, eval-time yellow, errors red). User asked for a way to "see what the LLM is doing" — previously `llm-server.log` was 0 bytes because `start-llm.ps1` passed `--log-disable`. Removed that flag so llama-server now streams its internal log to stdout, which gets redirected to the log file. Step 7 of `hermes-all.bat` launches the window; `hermes-stop.bat` step 5 kills the powershell and step 6 closes the cmd window by title match. Title set via `$Host.UI.RawUI.WindowTitle` with try-catch guard against host-less invocations. Initial 5-line tail dump at boot, 400ms polling loop, file-locked-safe. CRLF verified on both new files. AGENTS.md §3, §4, §8 updated.
| 2026-06-08  | **Hermes Web UI Integration (EKKOLearnAI)**: integrated [hermes-web-ui](https://github.com/EKKOLearnAI/hermes-web-ui) as the main web interface. Source: `data/webui-new/app/` (Vue 3 + Koa + Socket.IO). New launcher: `bin/webui-new.bat` with environment setup for portable Python (`HERMES_AGENT_BRIDGE_PYTHON`), data isolation (`HERMES_WEB_UI_HOME`, `HERMES_HOME`), and gateway disable (`HERMES_WEB_UI_DISABLE_GATEWAY_AUTOSTART=1`). Updated `hermes-all.bat` to start Web UI at :8648. **Compatibility fixes**: (1) Python bridge path injection for portable Python; (2) Data directory isolation to `data/webui-new/data/` and `data/hermes-agent/`; (3) Auto-generated `hermes-agent/config.yaml` with llama-local provider pointing to `http://127.0.0.1:8080/v1`; (4) Node.js dependency on system PATH (Web UI requires Node.js). Architecture now has 3 processes: llama-server (:8080), Hermes FastAPI (:7860), Hermes Web UI (:8648). README.md rewritten with acknowledgment to EKKOLearnAI/hermes-web-ui project. AGENTS.md §1/§2/§3/§4 updated.
| 2026-06-08  | **Skill installation for Web UI**: 9 skills installed to `data/hermes-agent/skills/` across 4 categories — 4 from upstream `optional-skills/finance/` (excel-author, pptx-author, comps-analysis, dcf-model) + 4 from community GitHub via `git clone` (drawio-skill from `Agents365-ai/`, hermes-dojo from `Yonkoo11/`, avoid-ai-writing from `conorbronsdon/`, plur-memory + plur-session-end from `plur-ai/plur`). Config change: `data/hermes-agent/config.yaml` `toolsets:` list extended with `skills` (was `[hermes-cli]` only) — required to load the upstream `skills` toolset. **Process note**: `hermes skills install <name> --force` is rate-limited by GitHub API (60 req/hr unauthenticated) so we used `git clone --depth=1` as the rate-limit-free fallback. 2 short names (`research-agent`, `multiagent`) not present in upstream source tree and were skipped pending a specific source URL from the user. See §11 for the full list. AGENTS.md §3/§8/§11 updated.
| 2026-06-08  | **Built-in skill install (round 2)**: copied 5 built-in skills from `E:\Hermes Agent\hermes-agent-source\skills\` to `data/hermes-agent/skills/` — `productivity/{powerpoint, ocr-and-documents, nano-pdf, google-workspace}` and `creative/claude-design`. Total now 14 active skills across 4 categories (finance 4, productivity 6, creative 3, autonomous-ai-agents 1). User confirmed trust = same-source GitHub repo so no security scan. AGENTS.md §3/§15 updated.
| 2026-06-08  | **Portability audit (full project)** — user asked: every file/service/dep/env that `hermes-all.bat` opens must be inside the `Hermes Agent` folder itself (plug-and-play on a fresh Windows PC, no PATH, no drive-letter literals). Audited all `bin/*.bat` (18), `bin/*.ps1` (5), root `*.bat`/`*.ps1` (4), `hermes/*.py`, `hermes-agent-source/`, `data/webui-new/app/bin/*.mjs`, `data/hermes-agent/config.yaml`, `portable-python/`, `runtime/node23/`. Fixed: `bin/verify-server.bat` (4-line rewrite to use `%~dp0..`), `bin/webui-new.bat` (portable dev hint + PowerShell fix-up of `mcp_servers.hermes-studio.env.HERMES_WEB_UI_HOME/HERMES_WEBUI_STATE_DIR` on every launch, idempotent), `hermes/scripts/install_skill.py` + `rebuild_kb.py` (`Path(__file__).resolve().parents[2]`). Deleted: root `start_llm_server.bat` (dead code, called missing `local_llm_server.py`), root `update_env.ps1` (contained a live **MiniMax API key** — would have leaked to GitHub), 47 debug-residue files in `data/logs/` (17 `_diag*.bat` + 1 `_test.ps1` + 1 `_test_arg.bat` + 25 `_diag*.txt` + 2 `removed-*.bat` + 17 underscore-prefixed session logs). Verified portable: portable-python runs in any cwd ✓; `runtime/node23/node.exe` resolves via `%HERMES_ROOT%` ✓; `hermes-agent-source/` has no hardcoded paths ✓; webui `hermes-web-ui.mjs` ✓. Items left as-is documented in §16 (env-var fallbacks, docstring examples, third-party Node build scripts, doctor diagnostic strings). New `§16 Portability Audit` written.
| 2026-06-10  | **Modular refactoring (Phase 1-5 complete)** — Three core principles: (1) No reinventing wheels, (2) Bridge don't modify upstream, (3) Keep upstream clean. **Phase 1**: Created `deps/` dependency zone with `hermes-env.bat`+`hermes-env.ps1` (centralized env vars), `manifest.json` (version tracking), and NTFS junctions: `deps/node/`→`runtime/node23`, `deps/llamacpp/bin/`→`runtime`, `deps/tools/`→`runtime`. Python intentionally NOT junctioned (would break `python312._pth` `..` resolution). **Phase 2**: Created `modules/` skeleton with 6 modules: `llm-engine/` (port 8080), `bridge/` (7860), `webui/` (8648), `env-bootstrap/` (GPU detect), `model-manager/` (downloaders), `supervisor/` (orchestrator). Each has `module.json` (self-describing: name, version, type, runtime, network, lifecycle, depends_on, env), `start.ps1`, `stop.ps1`, `health.ps1` (for services). **Phase 3**: `supervisor/orchestrator.ps1` reads all `modules/*/module.json`, topologically sorts by `depends_on`, starts services in order with health checks, stops in reverse order. Supports `--status`, `--stop`, `--dry-run`. Updated `bin/hermes-all.bat` v2 and `bin/hermes-stop.bat` v2 to call orchestrator. **Phase 4**: Merged `hermes/gpu.py`+`hermes/firstrun.py` GPU parts → `modules/env-bootstrap/gpu_detect.py`. Merged `hermes/download.py`+`hermes/gopeed_client.py` → `modules/model-manager/downloader.py`. Implemented `bridge/sitecustomize.py` two monkey-patches: PATCH 1 (Windows path raw-string preprocess, wrapping `tools.code_execution_tool.execute_code`) and PATCH 2 (Windows-cwd terminal wrapper, wrapping `tools.environments.base.BaseEnvironment.execute`). Copied to `portable-python/Lib/site-packages/sitecustomize.py` for auto-load. **Phase 5**: Deleted duplicate files: `hermes/skills.py`, `hermes/prompts.py` (upstream covers), `hermes/gpu.py`, `hermes/gopeed_client.py`, `hermes/scripts/gpu_detector.py` (merged into modules). Kept in `hermes/`: `config.py`, `__init__.py`, `__main__.py`, `workspace.py`, `memos_client.py`, `knowledge.py`, `watchdog.py`, `download.py`, `mirror.py`, `gguf.py`, `firstrun.py` (last 3 to be removed after Phase 6 verification). Updated AGENTS.md §3 project layout + §8. |
| 2026-06-08  | **Console Switch bug + Process.Start fix + NGL=0 + new health probe + setup-portable** — five related fixes: **(1)** `hermes-console.ps1` Switch-Model previously used `Start-Process cmd /c "bat" "gguf"` which silently failed (cmd's quote-pair rule + spaces in `E:\Hermes Agent\...`) — no `last-launch.json`, no llama-server PID, no logs. Switched to `Start-Process -FilePath $startBat -ArgumentList @($ModelPath)` (ShellExecuteEx detaches the child reliably). Verified end-to-end: 35B → kill → 3B switch takes 3s. **(2)** `start-llm.ps1` had two real bugs: (a) `$pid = $wmi.ProcessId` triggered `VariableNotWritable` (PID is a read-only auto-variable), so the script aborted AFTER the child had already been spawned — leaving an orphan llama-server. (b) It was WMI + cmd /c indirection that PowerShell-session-detach problems couldn't shake. Replaced the whole WMI + cmd redirect block with `Start-Process -FilePath $BinFull -ArgumentList $argList -RedirectStandardOutput/Error -WindowStyle Hidden -PassThru`. Confirmed this works: 35B stayed up across 3 separate PS session exits. **(3)** `start-llm-smart.bat` NGL calculator had two bugs: (a) `if %VRAM_FREE_MB% GTR 0` was immediate-expansion but VRAM was set inside a `for /f` block above — the read saw empty string, so NGL=0 with the misleading "no NVIDIA GPU detected" message even when 7GB VRAM was free. (b) The `if A else if B else if C` chained form raised `'else' is not recognized as an internal or external command` on some Windows builds. Fixed: all reads inside the NGL block now use `!VRAM_FREE_MB!` (delayed expansion), and the chain is rewritten as nested `if/else`. Re-ran with 3B model: NGL=99, Mode="GPU (full offload, 2007MB / 6996MB free VRAM)" ✓. **(4)** New `bin/hermes-health.ps1` — three-layer liveness probe with millisecond timestamps: `/health` (TCP-up), `/v1/models` (loader done), `/v1/completions` (model warm). Reports each layer with `HH:mm:ss.fff` and total elapsed. Wired into `hermes-console.ps1` Switch-Model step 3; hermes-all.bat uses it on a future commit. End-to-end: 3B switch + health probe reported "ALL OK in 210ms" with the model returning text from a "ping" prompt. **(5)** New `bin/setup-portable.bat` — idempotent first-boot bootstrap. Detects and downloads missing pieces: (1) `portable-python/` from python.org official embed zip (~10MB), (2) `runtime/llama-server-cuda-12.4.exe` from ggml-org's official b9503 release on GitHub (~250MB with CUDA DLLs), (3) `data/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf` from Hugging Face official mirror (~2GB). Each piece is checked separately; subcommands `python`, `runtime`, `model`, `status` for fine control. Downloads use `runtime/aria2c.exe -x16 -s16` if present, else PowerShell `Start-BitsTransfer`. Exits 1 with `MISSING` on partial failure so `hermes-all.bat` can warn-and-continue. Wired into `hermes-all.bat` as new step `[0/8]` (renumbered 2-7 to 3-8). All `.bat` files normalized to CRLF: 19/19 OK. **(6)** `hermes-model-run.ps1` evaluated per user request: **functionality is sound** — it correctly tails `data/logs/llm-server.{log,err}` with smart color highlighting (model load in magenta, offload in dark-magenta, HTTP requests in cyan, eval time in yellow, errors in red, warnings in dark-yellow), 400ms polling loop, file-locked-safe, initial 5-line tail dump. What it shows is **the llama-server backend's own log** (load progress, offload decisions, HTTP request lines, prompt-eval/eval/total times, tokens/s) — NOT the model's token-by-token "thinking" text. For that, the server would need `--verbose` (which prints the full prompt + generated text per request), but that's a separate enhancement; the script's current role is "watch the server is healthy and what it's doing" and it does that correctly.

| 2026-06-08  | **`MINIMUM_CONTEXT_LENGTH = 64_000` gate + 3B 32K override** — user hit `Error: Model Qwen2_53BInstructQ4_K_M has a context window of 32,768 tokens, which is below the minimum 64,000 required by Hermes Agent. Choose a model with at least 64K context, or set model.context_length in config.yaml to override.` Root cause: `hermes-agent-source/agent/model_metadata.py:133` hardcodes `MINIMUM_CONTEXT_LENGTH = 64_000` (for tool-calling working memory). `cli.py:5378` rejects `ctx_len < MINIMUM_CONTEXT_LENGTH`; `run_agent.py:661` resolves `target_ctx = max(config_context_length or 0, 64K)`. The **3B model's `n_ctx_train` is only 32K**, so server reports `n_ctx=32768`, agent computes `effective_context_length` from that, and the gate fails. **Fix:** add `context_length: 65536` to `data/hermes-agent/config.yaml` under `model:` — `agent/agent_init.py:1370` reads `_model_cfg.get("context_length")` directly, so this value is honoured and the gate passes. The actual server still runs 32K (n_ctx_train cap) and will warn + cap any request that exceeds 32768, but the chat-run-socket pre-flight check is what was blocking, and it now passes. Also updated `hermes-console.ps1` Switch-Model: `$ctxLen = 65536` for 3B/7B, 131072 for 35B (was 32768 for 3B — would have re-broken the override next time the user switched back to 3B). **Important side-effect:** the WebUI node process caches `_config_context_length` at startup, so changing the value in `config.yaml` (or in the WebUI's own model context window field, which is `model_context_length` and writes back via `hermes-agent-source/hermes_cli/web_server.py:399`) requires restarting the WebUI before the new value is honoured. Sequence used: edit config.yaml → `bin\webui-new.bat stop` (PID 23656 gone) → `bin\webui-new.bat start` (new PID 26460, reads the updated config) → 8648 returns 200 → 3B chat now flows. **Active WebUI session at the time of the fix:** `mq59rjli3ip8yk` (URL `http://localhost:8648/#/hermes/session/mq59rjli3ip8yk`). The desktop app's settings panel exposes this same field at `apps/desktop/src/app/settings/constants.ts:279` (`modelContextLength: 'Context Window'`, default 0 = use server-detected), reachable through the i18n key `modelContextLength` in `zh.ts` ("上下文长度") / `zh-hant.ts` ("上下文長度") / `ja.ts` ("モデルコンテキストウィンドウ") etc.
| 2026-06-08  | **Live process layout (after this session):** llama-server PID 27444 (`llama-server-cuda-12.4`, 22:01:47 start, ~2.3GB WS, 3B Qwen2.5 at 32K ctx) on :8080 — Hermes FastAPI PID 26176 ("Hermes-API", ~90MB WS) on :7860 — Hermes WebUI PID 26460 (node 22+, ~177MB WS) on :8648. All three serve 200 OK. Use `bin\hermes-stop.bat` for full shutdown, `bin\webui-new.bat stop` for just the WebUI (keeps model + API running, useful for picking up config.yaml edits without dropping the model).
| 2026-06-08  | **Pushed commit `f3d4140` to `origin/main`** at `https://github.com/ArtificialAngels/hermes-agent.git`. 15 files changed, 954 insertions, 167 deletions (the full §16 portability audit + 5 bug fixes from this session, plus the two new scripts `bin/hermes-health.ps1` and `bin/setup-portable.bat`).
| 2026-06-08  | **Hermes-agent (`hermes-agent-source/`) sandbox-leak + Windows-path SyntaxError fix** — user uploaded an xlsx in the WebUI, pointed the model at the local `parse_excel` skill, and the run failed with two stacked bugs visible in the response: **(a)** the auto-generated `script.py` lived under `C:\Users\PZS0X\.mavis\agents\mavis\workspace\.opencode\tmp\hermes_sandbox_sn9iixd4\` — i.e. in **Mavis's workspace, not in `E:\Hermes Agent\`**, violating the plug-and-play "every file under the project folder" rule. **(b)** the script body contained `parse_excel('E:\Hermes Agent\data\...')` and Python raised `SyntaxError: (unicode error) 'unicodeescape' codec can't decode bytes in position 35-36: truncated \uXXXX escape` because `\H` / `\A` / `\u` inside the string literal look like the start of unicode escapes. Root cause: `hermes-agent-source/tools/code_execution_tool.py:1135` called `tempfile.mkdtemp(prefix="hermes_sandbox_")` with no `dir=` argument, so it followed `tempfile.gettempdir()` → `TMP` env → the Mavis workspace's `.opencode/tmp`. Fix in `hermes-agent-source` (this is the user's **forked** repo, not the main project, so the diff lives in `E:\Hermes Agent\hermes-agent-source\.git\` and is on `main`, ahead of `ArtificialAngens/hermes-agent` by 1): pin the sandbox under `<HERMES_HOME>/tmp/sandbox` using `get_hermes_home()` (which on the WebUI launch resolves to `E:\Hermes Agent\data\hermes-agent` thanks to `HERMES_HOME` being set by `bin\webui-new.bat`). Add `_auto_rawstring_windows_paths(code)` pre-pass on the script before writing `script.py` — regex `(?<![rR])(['"])([A-Z]:\\[A-Za-z0-9_.\\ ()~+@#${},!:-]+)['"]` matches Windows-path-shaped string literals and the `sub` rewrites the whole match to a raw string. **Critical regex gotchas hit while writing this** (record for next time): (1) the closing delimiter must be a literal quote-class `['"]`, NOT a backref `\1` — in a raw string `r'\1'` is two characters (`\` + `1`) and Python `re` interprets that as a literal backslash + digit, not a backref; use `\\1` to get an actual backref, or skip backref entirely and use a literal class. (2) The character class MUST include both `:` and `\\` (escape backslashes in code) — `E:\foo` won't match if the class only has `A-Z` because `:`, `\`, and the path-separator backslash all need to be in the allow-list. (3) Use a `(?<![rR])` lookbehind to skip already-raw literals so `r'C:\foo'` is left untouched. Verified end-to-end with seven test fixtures: single/double-quote paths, multiple paths in one call (`multi("X:\a\b","Y:\c\d")` → `multi(r"X:\a\b",r"Y:\c\d")`), already-raw, and plain-`\n` strings. All seven pass. Pushed commit `c8d1e0ea8` to the user's `hermes-agent-source` git. **Open question for the user:** do you want to PR this back to `NousResearch/hermes-agent` upstream, or keep it as a local fork patch?
| 2026-06-08  | **Active state at end of session:** WebUI on :8648 (node PID 26460, 22:10:28 start, ~177MB WS, just restarted to pick up `data/hermes-agent/config.yaml` `context_length: 65536` override for the 64K gate), Hermes FastAPI on :7860 (PID 26176 "Hermes-API", ~90MB WS), llama-server on :8080 (PID 27444, `llama-server-cuda-12.4`, 22:01:47 start, ~2.3GB WS, running `Qwen2.5-3B-Instruct-Q4_K_M.gguf` at 32K ctx). Active chat session: `mq59rjli3ip8yk` at `http://localhost:8648/#/hermes/session/mq59rjli3ip8yk`. `hermes-agent-source/` git is on commit `c8d1e0ea8` (sandbox pin + raw-string preprocess), 1 commit ahead of `origin/main`. Main project on commit `f3d4140` (already pushed to `ArtificialAngels/hermes-agent`), no further source-tree changes pending from this session beyond these two commit-log entries.
| 2026-06-09  | **Switch-model bug — `LLAMA_MODEL` env var inherited from parent overrode `argv`** — user reported that `hermes-console.ps1` Switch-Model and `hermes-all.bat` initial launch were always loading the same model (3B or 7B) regardless of what was picked in the dropdown. Root cause: `bin\start-llm-smart.bat` had `if not "%LLAMA_MODEL%"=="" set "MODEL=%LLAMA_MODEL%"` AFTER `set "MODEL=%~1"` — so the env var set by `hermes-all.bat` L94 (`set "LLAMA_MODEL=%MODEL%"`) was inherited by the child cmd started via `start /MIN`, and unconditionally overrode any explicit argv. **Fix:** swap the precedence: argv > env > default. Also `hermes-console.ps1` [3/5] Verify step now compares `/v1/models` response to the requested alias and reports red `MISMATCH`/`FAILED` instead of green `SUCCESS` when they differ — the previous "verify" only printed the model ID and never compared, which is why this whole class of bugs was invisible. Pushed as `3618291`. AGENTS.md §3 / §7 / §8 (this entry) updated.
| 2026-06-09  | **Detach bug — `Start-Process` + parent cmd window close killed llama-server** — `bin/start-llm.ps1` used `Start-Process` (ShellExecuteEx) which left the new process attached to the parent PowerShell's console. When the user closed the parent cmd window, Windows broadcast `CTRL_CLOSE_EVENT` to every process attached to that console, and llama-server (a console app) responded by exiting. Verified by user: bat works directly when run in foreground, but disappears after closing the cmd. **Fix:** replace `Start-Process` with `System.Diagnostics.Process.Start(ProcessStartInfo)` + `UseShellExecute=$false` + `CreateNoWindow=$true` + `RedirectStandardOutput/Error=$true`. This goes through `CreateProcess` with `CREATE_NO_WINDOW`, the new process has no console of its own and is NOT attached to the parent's, so the parent's CTRL_CLOSE_EVENT never reaches it. Output drains to log files via `BeginOutputReadLine()`. Also removed the `Wait-Process` at end of script (was meant to keep ps1 alive as a process-group guard, but on Win11 it just made the chain die harder when parent cmd closes). Pushed as `8334330`.
| 2026-06-09  | **cmd /c `""<path>""` double-double-quote — silent-fail pattern** — `bin\hermes-all.bat` L95 used `start "Hermes-LLM" /MIN cmd /c ""%HERMES_ROOT%\bin\start-llm-smart.bat""`. The `""path""` is the standard cmd /c escape for paths with spaces, but it has a known silent-fail pattern: cmd /c strips the outer quotes and the leading quote of the inner string, then looks up the bat as a literal quoted executable name (which doesn't exist). cmd /c exits with no error, the parent `start` returns successfully with no child cmd, and the user sees nothing. **Fix:** use `cd /d "%HERMES_ROOT%"` first, then `start "Hermes-LLM" /MIN cmd /c "bin\start-llm-smart.bat"` with a relative path that has no quotes around it. Pushed as `04f626e`.
| 2026-06-09  | **Refactor: llama-server router mode (b9538+) — abandon kill+restart for multi-model** — user gave up on the kill+restart model-switch flow after multiple rounds of fixes and pointed at llama.cpp's native **router mode** (`--models-dir` + `--models-preset` + `--models-max`). Confirmed via `llama-server.exe --help` that b9538 supports all of it. **New architecture**: SINGLE llama-server process started with `--models-dir data\models`; switches models on demand when an API request arrives with `model="<filename>"`. With `--models-max 1` and LRU eviction, only the most-recently-used model is resident in VRAM at a time — fits 3B/7B/35B-MoE on 8GB GPU (35B uses 16 GPU layers + CPU offload via preset). **New files**: `bin\start-llm-router.ps1` (single launcher, .NET Process.Start for proper detach, --models-max computed from free VRAM), `data\models\router-preset.ini` (per-model NGL/ctx/temp). **Updated**: `bin\hermes-all.bat` step 2 calls the new ps1; `bin\hermes-console.ps1` Switch-Model no longer kill+restarts — it POSTs `/v1/models/load` to preload, updates config.yaml, sends a tiny warmup; `data\hermes-agent/config.yaml` default model id is now the GGUF filename (`Qwen2.5-3B-Instruct-Q4_K_M.gguf`) to match what router exposes; `hermes/scripts/model_manager.py` rewritten to call `/v1/models/load` instead of stop+start. **Deleted**: `bin\start-llm-smart.bat`, `bin\start-llm.ps1`, `bin/switch-model.bat`, `tests/verify_smart_ngl.py`, `test_model_switch.py` — all obsolete with router mode. **.gitignore**: added `!data/models/*` so the preset ini can be tracked. Pushed as `ce99e4d`. README.md §"启动方式" + "目录结构" + new "Router 模式" section updated.
| 2026-06-09  | **Active state at end of session:** All 5 commits from this session pushed to `origin/main`. Last commit is `ce99e4d` (router mode refactor). `bin\start-llm-router.ps1` is the single source of truth for LLM launch; `data\models\router-preset.ini` is the per-model config. WebUI dropdown → llama-server router → LRU eviction. Zero restart cycles.
| 2026-06-10 | **stdin inherit bug fix** — `bin/start-llm-router.ps1`, `bin/start-bridge-server.ps1`, `bin/start-webui.ps1` all used `.NET [System.Diagnostics.Process]::Start($psi)` with `RedirectStandardInput=$false` which is a no-op (means INHERIT, not “don't redirect”). With `UseShellExecute=$false + CreateNoWindow=$true`, the child inherited the parent's stdin handle (a pipe from cmd/bat), and llama-server detected non-console stdin and exited immediately with "Input redirection is not supported". **Fix:** wrap each binary in `cmd /c "<bin> <args>" < NUL` — cmd.exe opens NUL device for stdin, the real child inherits cmd's stdin (NUL = valid device, not a pipe). Also fixed PID recovery: since the direct child is now cmd.exe, the real server PID is obtained from `netstat -aon` matching the listening port. **Also:** cleaned `bin/setup-portable.bat` — removed hardcoded 3B model download (`DEFAULT_MODEL_URL` + `DEFAULT_MODEL_PATH`), replaced with a simple `*.gguf` existence check and a message to use `hermes-models.py` or WebUI model manager. Verified: all three services (8080/7860/8648) return 200 OK. |

| 2026-06-09  | **Full upstream cutover — clean hermes-agent v0.16.0 + hermes-web-ui v0.6.12, hermes/*.py dedup, bridge skeleton** — user copied fresh clean copies of both upstream repos into project root (`hermes-agent/` 100.8MB / 2082 .py + 514 .ts v0.16.0; `hermes-web-ui/` 59.6MB / 515 .ts + 126 .vue v0.6.12) and deleted the old `hermes-agent-source/` fork. Decisions confirmed: **1.A** delete `data/webui-new/app/` (EKKOLearnAI 0.6.11 fork); **2.A** try running clean v0.16.0 directly; **3.A** build deps/ + clean 16 duplicate .py + bridge module skeleton. **What was done:** (1) `mavis-trash data/webui-new/app/` ✅ — the 0.6.11 fork with 4 local mods (loadModel/unloadModel controller + .gguf filter + 2 API helpers) is gone; (2) Verified `hermes-agent v0.16.0` imports end-to-end (`hermes_cli.main`, `AIAgent`, `agent`, `cron.jobs`, `hermes_state`, `gateway`, `tools` all importable; `HERMES_HOME=E:\Hermes Agent\data\hermes-agent` honored by upstream `get_hermes_home()`); (3) **Removed broken editable finder** (`__editable___hermes_agent_0_16_0_finder.py` + `.pth`) — both still pointed at deleted `hermes-agent-source/` paths; (4) **Added `../hermes-agent` to `portable-python/python312._pth`** so `hermes_cli`/`run_agent`/`agent`/`tools`/`cron`/`gateway` all resolve from clean source; (5) **Backed up 13 duplicate .py to `data/_backup/hermes_dups_2026-06-09/`** (agent, cron, doctor, embeddings, kanban, llm, memory, planner, sessions, webui_settings, mock + scripts/{install_skill, model_manager}) — upstream v0.16.0 has equivalent or richer implementations; (6) **`mavis-trash hermes/server.py`** (97KB) — broken imports of the 11 deleted modules; replaced with `bridge/server.py` skeleton (FastAPI app, `/health` returns 200 with version + endpoint manifest, 8 endpoints planned: `/v1/models`, `/v1/models/load`, `/v1/chat/completions`, `/api/chat/sessions`, `/api/workspaces`, `/api/kanban`, `/api/crons`, `/api/webui/settings`); (7) **Rewrote `hermes/__init__.py`** as thin shim (re-export doc only, no eager imports — upstream is authoritative); (8) **Rewrote `hermes/__main__.py`** as thin CLI delegate (`from hermes_cli.main import main as upstream_main; sys.exit(upstream_main())`); (9) **Fixed `hermes/knowledge.py`** — it imported deleted `hermes.memory.cosine_similarity` and `hermes.memory.Embedder`; inlined a minimal `cosine_similarity` + `Embedder` base class + `HashEmbedder` fallback (deterministic 384-dim hash-based pseudo-embedder for offline use). All 13 truly-independent hermes/*.py modules now import cleanly (`config`, `skills`, `gguf`, `gpu`, `workspace`, `watchdog`, `knowledge`, `mirror`, `prompts`, `download`, `firstrun`, `gopeed_client`, `memos_client`). (10) **Built `bridge/` skeleton**: `__init__.py` (version `0.1.0-skeleton`), `README.md` (architecture diagram + what's-in/where/why), `server.py` (FastAPI app with TODO imports for upstream `AIAgent`/`SessionDB`/`JobStore`/`KanbanDB`/our `WorkspaceManager`/`list_gguf_models`), `sitecustomize.py` (monkey-patch template for `c8d1e0ea8` + `d59d06c2d` — both documented with original-commit context, ready for `portable-python/Lib/site-packages/sitecustomize.py` install); (11) **Built `deps/README.md`** documenting the layout (`hermes-agent/` and `hermes-web-ui/` at root are upstream deps; not moved to `deps/` because PYTHONPATH and `_pth` already point at root, and moving would force every ref to update). **Smoke tests pass**: `python -m hermes --help` delegates to upstream and shows 50+ subcommands (`chat, model, fallback, gateway, proxy, setup, kanban, cron, doctor, security, skills, plugins, memory, mcp, sessions, claw, version, update, acp, profile, dashboard, desktop, logs, ...`); `from bridge.server import app` → FastAPI title="Hermes Bridge" v0.1.0-skeleton; `TestClient(app).get('/health')` → 200 with `{"status":"ok","version":"0.1.0-skeleton","upstream":"hermes-agent-0.16.0","endpoints_implemented":["/health"],...}`. **KNOWN BROKEN — launcher chain needs next-session fix**: (a) `bin/hermes-all.bat` L122 calls `python -m hermes serve --port 7860` — **upstream's CLI has NO `serve` subcommand** (closest is `dashboard` which starts upstream's own FastAPI at a different port); (b) `bin/webui-new.bat` L9 references deleted `data\webui-new\app`, L63 `cd hermes-agent\data\webui-new\app` (wrong), L80 `set "HERMES_AGENT_ROOT=%HERMES_ROOT%\hermes-agent-source"` (deleted). The launcher needs to either (i) call `python -m bridge.server` directly (with our FastAPI as :7860), or (ii) call upstream's `hermes dashboard` and let it own :7860. Decision deferred to user. **Also known**: `data/webui-new/` (parent dir) still contains upstream hermes-agent's old state files (`auth.json`, `config.yaml`, `kanban.db`, `state.db`, `crons/`, `kanban/`, `memory/`, `sessions/`, `skills/`, `logs/`, etc.) — this was the OLD `HERMES_HOME` before `bin/webui-new.bat` was changed to point at `data/hermes-agent/`. NOT deleted (real data, may contain valuable sessions) — user decides whether to back up + clean. **Decision not yet made**: PR `c8d1e0ea8` (sandbox pin + raw-string) and `d59d06c2d` (Windows-cwd terminal) back to upstream NousResearch, or keep as monkey-patch in `bridge/sitecustomize.py` forever.
| 2026-06-10  | **★ Path-management reform — single source of truth for HERMES_ROOT (USB-portable)** — user raised the "project is plug-and-play, drive letter changes" concern. The old setup had each .bat / .ps1 re-deriving `HERMES_ROOT` independently (`set "HERMES_ROOT=%~dp0.."`), and one hardcoded `HERMES_DATA_DIR=E:/Hermes Agent/hermes/data` in `.env`. Replaced with: **(1)** `bin/hermes-root.py` — Python resolver with 4-tier priority (env var → `.hermes-root` cache → script-location inference → drive-letter scan across D:..Z: for `\Hermes Agent\portable-python\python.exe`); 6 subcommands (`resolve`, `verify`, `init`, `scan`, `persist`, `clean`); `init` outputs a bat-parseable `KEY=VALUE` env block. **(2)** `bin/hermes-root.bat` — thin bat launcher (ASCII-only, CRLF). **(3)** `deps\hermes-env.bat` / `.ps1` — completely rewritten to consume `init`'s output (down from 69 lines of hand-rolled env to 36 lines of consumption + cuda/PATH tweaks). **(4)** Refactored 8 bat files (`hermes-all`, `hermes-stop`, `hermes-supervisor`, `hermes-firstrun`, `hermes-model-run`, `hermes-console`, `gpu-detect`, `install-embeddings`) to all go through `deps\hermes-env.bat` first. Removed the old 8.3 short-path workaround (`HERMES_ROOT_S=%%~sI`) since we no longer bridge through PowerShell `-File`. **(5)** `bin/hermes-supervisor.py` — added `_resolve_hermes_root(HERE)` helper that delegates to `bin/hermes-root.py resolve` via subprocess (env-var fast-path, then subprocess, then `here.parent.parent` fallback). **(6)** `bin\fix-eol.py` — permanent CRLF maintenance tool (replaces ad-hoc PowerShell conversions in AGENTS.md §7); accepts file list or `--all`; `--check` mode for CI/hooks. **(7)** `.env` — removed the hardcoded `HERMES_DATA_DIR=E:/Hermes Agent/hermes/data`; `hermes/config.py` already uses `load_dotenv(override=False)` so it honors the process env (set by `deps\hermes-env.bat` from `HERMES_ROOT`) over .env. **(8)** Deleted the entire `modules\supervisor\` directory (`module.json`, `orchestrator.ps1`, `start.ps1`, `stop.ps1`) — superseded by Python supervisor. **E2E verified** — corrupted `.hermes-root` to `Z:\NonExistent\Fake\Path`, `hermes-root.py verify` correctly reported `Source: inferred:script-location` (downgrade), `init` auto-repaired the cache, full env block produced 14 vars with `HERMES_STATUS=ok`. AGENTS.md §2/§3/§4/§7/§8 (this entry) updated. |
| 2026-06-10  | **★ Pre-commit hook + versioned git hooks** — make the CRLF check permanent at the git level so LF-only .bat / .ps1 files can never enter the repo. **(1)** `.githooks/pre-commit` — bash script (LF-only, 1376 bytes) that calls `portable-python\python.exe bin\fix-eol.py --all --check` and exits 1 on any failure. Skips gracefully if `portable-python` is missing (fresh clone). **(2)** `bin\install-git-hooks.bat` — one-shot installer that runs `git config core.hooksPath .githooks` (relative to repo root), with `uninstall` arg to revert. **(3)** Updated `bin\fix-eol.py` `--all` mode to scan ONLY Hermes-owned scripts (`bin/*.bat/*.cmd/*.ps1` one level + `deps/hermes-env.{bat,ps1}`) — 17 files total — instead of all 177 bat/ps1 under `deps/` (which would falsely fail on third-party node_modules with LF line endings). **(4)** Verified end-to-end: `git commit --allow-empty` triggers the hook and prints `[pre-commit] OK: all .bat / .ps1 files are CRLF.`; with a synthetic LF-only test file, `fix-eol.py --check` returns exit 1 as expected. **Phase 1 hook installed**: `core.hooksPath=.githooks`. AGENTS.md §3 / §7 / §8 updated. |

| 2026-06-10 | **Phase 7-13 收尾：硬路径消除 + CUDA 11/12/13 多版本 + 模块回归 + 文档同步** — 6 个 Phase 一气完成, 最终验证全绿. 逐项摘要:
  - **Phase 7 (硬路径消除)**: 修复 `hermes/scripts/import_ollama_blobs.py:21` (`Path('E:/Hermes Agent')` → `Path(__file__).resolve().parents[2]`); `hermes/workspace.py:49` (`'E:\\Hermes Agent'` → `str(self.root)`); `hermes/config.py:188/217-219` (删掉 `E:/D:` fallback, 改用 `HERMES_ROOT` env); AGENTS.md 3 处文档示例 (`E:\Hermes Agent` → `%HERMES_ROOT%`). **二次扫描**: 在 modules/bin/bridge 全量 grep `'[EeDdCc]:[\\/][Hh]ermes'` 返回 **0 匹配**.
  - **Phase 8 (CUDA 11-13 多版本)**: 新建 `runtime/cuda/{11.8,12.4,13.0}/` 目录结构 + `manifest.json` (描述版本 + 包含的 DLL). 扩展 `modules/env_bootstrap/gpu_detect.py` 5 个新函数: `detect_driver_version()` / `driver_to_cuda_version()` / `find_cuda_runtime()` / `install_cuda_runtime()` / `recommend_cuda_version()` (driver→CUDA 映射表: ≥555→13.0, ≥525→12.4, ≥470→11.8, ≥450→11.0, <450→None). 修改 `modules/llm_engine/start.ps1` 调用 `recommend` 动态选 CUDA, 不存在则触发 `install`. 修改 `deps/hermes-env.bat`+`.ps1` 加 `CUDA_VERSION`/`LLAMACPP_BIN_CUDA` 变量. `setup-portable.bat` 增加多版本下载步骤.
  - **Phase 9 (修复 breakage)**: `tests/test_hermes.py` 删掉已死引用 `from hermes.gpu import detect_gpu`, 注释指向新模块; `modules/model_manager/manager.py` 创建 (统一 CLI: list/info/download/import-ollama), module.json `script` 字段保持不变; `hermes/__init__.py` 重写 docstring, 移除 9 个已删/待迁移条目 (skills/prompts/gpu/gopeed_client/gguf/mirror/download/firstrun), 保留 5 个真独立的 (config/knowledge/memos_client/watchdog/workspace).
  - **Phase 10 (删除重复)**: `bin/hermes-firstrun.bat` 改为调用 `python -m modules.env_bootstrap %*`; 删除 `hermes/download.py` (47KB, 已被 modules/model_manager/downloader.py 取代) + `hermes/firstrun.py` (32KB, 已被 modules/env_bootstrap/gpu_detect.py 取代); 删除 `bin/start-llm-router.ps1` + `start-bridge-server.ps1` + `start-webui.ps1` (3 个旧启动脚本, 已被 modules/*/start.ps1 取代). `hermes/scripts/rebuild_kb.py` 改用新下载器.
  - **Phase 11 (迁移 bridge 依赖)**: `hermes/gguf.py` → `modules/model_manager/gguf.py` (含 import path 调整); `hermes/mirror.py` → `modules/model_manager/mirror.py`; `modules/model_manager/__init__.py` 暴露 `list_gguf_models`/`parse_gguf_meta`/`DownloadManager`/`GopeedClient`/`mirror_url`; `bridge/server.py:120` 和 `bin/hermes-models.py:36` 更新 import 为 `from modules.model_manager.gguf import ...`. `modules/model_manager/manager.py` 把所有子模块通过 `__all__` 统一暴露.
  - **Phase 12 (清理废弃)**: 删除整个 `hermes/scripts/` 目录 (import_ollama_blobs.py 已迁到 model_manager); 删除 `hermes/data/webui_settings.json` + `hermes/data/workspaces.json` (旧 HERMES_HOME 残留); 删除 `runtime/node/` 旧版 Node (junction `deps/node` 已指向 `runtime/node23`). `hermes/data/logs/hermes-download.ps1` 残留日志也被清掉.
  - **Phase 13 (最终验证)**: (a) 路径扫描 0 匹配 ✓; (b) `runtime/cuda/{11.8,12.4,13.0}/manifest.json` 全部就位 ✓; (c) `python -m modules.env_bootstrap status` → GPU NVIDIA RTX 3070 8192MB 驱动 610.47 ✓; `recommend` → `12.4` ✓; `check` → `[check] OK: CUDA 12.4 ready` ✓; `python -m modules.model_manager.manager list` → 2 个 GGUF 模型列出 ✓; `modules\llm_engine\start.ps1` 实际启动 llama-server (PID 37232) ✓; `modules\supervisor\orchestrator.ps1 -Status` 显示 6 个模块 ✓; `-DryRun` 显示拓扑排序 ✓; `bin\gpu-detect.bat` 返回 JSON ✓.
  - **🚨 Phase 13.3 期间发现的关键 BUG 并已修复**: `modules/env-bootstrap/`/`model-manager/`/`llm-engine/` 三个目录用 **连字符 (hyphen)** 命名, 但 Python `import modules.env_bootstrap.gpu_detect` 需要 **下划线 (underscore)** — 连字符在 Python 包名里是 **非法的标识符**, 所有 `python -m modules.X.Y` 入口都因此 ImportError. 修复: 三个目录全部重命名为下划线版本 (`env_bootstrap`/`model_manager`/`llm_engine`), 同步更新 `module.json` 的 `name` 字段、`start.ps1`/`stop.ps1`/`health.ps1` 头注释、`bridge/module.json` 和 `webui/module.json` 的 `depends_on` 字段、AGENTS.md §3 + `hermes/__init__.py` 文档. **原理记录**: NTFS 支持连字符文件名, 但 Python `importlib` 只接受 PEP 508 标识符 (`[A-Za-z_][A-Za-z0-9_]*`), 这是隐性陷阱 (目录可见但 import 失败, 错误信息是 `ModuleNotFoundError: No module named 'modules.env-bootstrap'` 容易误判为 "包不存在").
  - **Phase 13.4 (文档同步)**: AGENTS.md §2 加 "Module architecture (Phase 1-13, completed 2026-06-10)" 说明; §3 项目布局反映重命名后的目录 (`env_bootstrap`/`model_manager`/`llm_engine`), 移除 `hermes/` 包里已删的 download.py/firstrun.py/gguf.py/mirror.py 4 个条目, runtime/ 树改为 `cuda/{11.8,12.4,13.0}/manifest.json` 多版本结构, bin/ 树移除 3 个 start-*.ps1; `deps/manifest.json` 加 3 条 CUDA 运行时记录 (11.8/12.4/13.0, 物理路径 `runtime/cuda/<ver>/`); `tests/test_hermes.py` 顶部 SyntaxWarning (行 6 `\\` 转义) 顺手修掉; `deps/hermes-env.bat`+`.ps1` 注释里 "llm-engine" → "llm_engine"; `bin/hermes-firstrun.bat` 注释 "env-bootstrap" → "env_bootstrap".
  - **Active state at end of session**: 6 个模块全部就绪 (`env_bootstrap`/`model_manager`/`llm_engine`/`bridge`/`webui`/`supervisor`), 拓扑依赖正确解析, `python -m modules.<name>.<script>` 全通, llm-engine 实测能拉起 llama-server. 整个项目现在可以 `xcopy /E /I` 到任意盘符/目录后 `bin\hermes-all.bat` 即用 (plug-and-play).

---

## 9. Setup Flow (clean install from scratch)

```bash
# 1. Download all llama.cpp variants + aria2 (one-time, ~280MB)
bin\setup-runtime.bat

# 2. Run it
bin\hermes-all.bat
# → browser opens at http://localhost:7860/  (the new Hermes WebUI)
# → WebUI is unauthenticated by default; data lives in agent memory
# → chat: type a message, pick a model from the dropdown (auto-populated
#   from llama-server at :8080 if up, else scanned from data/models/*.gguf)
```

To switch default model, edit `hermes-all.bat` line `set "MODEL=..."` (line ~13).

To install a different GGUF, drop it in `data\models\`, then either:
- Restart `bin\hermes-all.bat` after editing the MODEL line, OR
- Use Ollama's `import_ollama_blobs.py` to convert `sha256-XXXX` blobs

---

## 10. Debugging

### Log files
- `hermes\data\logs\hermes.log` — Hermes FastAPI + bootstrap.log
- Each launcher writes to its own window (visible in title bar)
- Browser DevTools Network tab shows the adapter's URL translations live

### Common issues
| Symptom                                  | Cause / Fix                                |
|------------------------------------------|--------------------------------------------|
| bat flashes and exits                     | LF line endings → convert to CRLF          |
| `'E:\Hermes' is not recognized`           | Space in path + bad cmd /c invocation      |
| llama-server OOM                          | Model > VRAM → NGL=0 (CPU only)            |
| WebUI model dropdown empty                | llama-server down + no GGUF in `data/models/` |
| WebUI "Model '' was not found"            | Model id mismatch → check llama-server `--alias` matches what's in `hermes.yaml` `llm.router.providers.local` |
| `MiniMax`/cloud "invalid api key"         | API key not activated on provider platform |
| WebUI stuck on "Loading..." forever       | `api-adapter.js` not loaded → check Network tab for /api/webui/* 404s |

### Reset to clean state
```bash
# Wipe Hermes in-memory chat session cache (only sessions created in this process lifetime)
"%HERMES_ROOT%\portable-python\python.exe" -c "from hermes.agent import HermesAgent; from hermes.config import load_config; a = HermesAgent(load_config(), use_mock=True); a._chat_sessions.clear(); print('cleared')"

# Run E2E test (no GPU needed)
"%HERMES_ROOT%\portable-python\python.exe" "%HERMES_ROOT%\tests\test_hermes.py"

# Verify NGL math
"%HERMES_ROOT%\portable-python\python.exe" "%HERMES_ROOT%\tests\verify_smart_ngl.py"

# Verify GGUF scan works
"%HERMES_ROOT%\portable-python\python.exe" -c "from modules.model_manager.gguf import list_gguf_models; from pathlib import Path; import json; print(json.dumps(list_gguf_models(Path(os.environ['HERMES_ROOT']) / 'data' / 'models'), indent=2, default=str))"
```

### Verify GPU is actually used
Open a separate terminal:
```bash
nvidia-smi
```
Look for `python.exe` or `llama-server.exe` row → check **GPU-Util** column.
If 0% → CPU mode, no GPU offload.

---

## 11. Testing

| Test                          | Purpose                                      | When to run        |
|-------------------------------|----------------------------------------------|---------------------|
| `tests\test_hermes.py`        | 17 E2E checks (mock LLM, no GPU)            | After major changes |
| `tests\verify_smart_ngl.py`  | Verify NGL math for all models               | After bat changes   |
| `bin\hermes-all.bat` e2e      | Real LLM full pipeline                       | Before commits      |

`test_hermes.py` uses `HERMES_LLM_MOCK=1` so it runs without GPU/LLM.

---

## 12. Known Limitations / TODO

- **GPU is RTX 3070 8GB** — fits 7B Q4_K_M, partial offload for 22GB qwen3
- **MiniMax API key not activated** — returns 2049 invalid
- **llama-server is single-model** — multi-model needs multi-instance
- **Hash embeddings are placeholders** — RAG quality is poor (until user runs `bin\install-embeddings.bat`)
- **WebUI streaming is now real (NEW 2026-06-07)** — `hermes/llm.py` `stream()` + `/api/chat/start` + `/api/chat/stream/{id}` SSE delivers per-chunk JSON `{type, content}` events. Adapter's old EventSource mock removed.
- **WebUI panels for workspaces / kanban / crons are real (NEW 2026-06-07)** — `hermes/workspace.py`, `hermes/kanban.py`, `hermes/cron.py` power them. See §4.
- **Kanban SSE / dispatch / comments / worktree are noop stubs** — UI falls back to 30s polling on `/api/kanban/events`. Real-time event push and agent dispatch are TODO.
- **Cron `/api/crons/pause` and `/resume` returned 400 in one harness test** — body schema mismatch; the endpoints are registered and respond 200 from the WebUI. Unverified whether the harness body was the issue; e2e-step3 in deliverable.md has the full request/response.
- **Auth is off** — Hermes WebUI has no login screen. Don't expose :7860 to the internet.
## 13. Roadmap: 1+2+4 Plan (in progress)

User confirmed priorities: **4 (KB) → 1 (embeddings) → 2A (autonomous tasks)** + native skill marketplace.

### ✅ 4. Knowledge Base management — DONE
- `hermes/scripts/rebuild_kb.py` — wipes `index.jsonl` + `sources/`, re-ingests `data/knowledge/*.md` with sane limits
- Per-doc cap: `--max-chunks 1000`
- Result: 256k bloated chunks → 13 clean chunks (5 files, all with embeddings)
- Runtime add: planned (TODO: `hermes kb add <path>` CLI)

### ⚙️ 1. Real embeddings — FRAMEWORK DONE, MODEL OPTIONAL
- `hermes/embeddings.py` — `SBERTEmbedder` (sentence-transformers) + `HashEmbedderFallback`
- `hermes/server.py` `/v1/embeddings` uses the new factory; auto-falls back to hash
- `bin\install-embeddings.bat` — installs sentence-transformers + downloads all-MiniLM-L6-v2 (~330MB)
  - **Not run yet** — user's internet is 137KB/s, big downloads are slow
  - When user has fast internet: `bin\install-embeddings.bat` (interactive, asks confirm)
  - Sets `HERMES_EMBEDDER=auto` (default) — uses sbert if installed, hash if not

### ✅ 2A. Autonomous task execution — DONE
- `hermes/planner.py` — `Planner` class with `TaskStep` / `TaskResult` dataclasses
- Loop: LLM generates JSON plan → execute skills one by one → on failure, replan → summarize
- CLI: `hermes task "<goal>" --mock --json` (use `--mock` to test without LLM)
- HTTP: `POST /api/task` (sync or async with task_id polling via `GET /api/task/{id}`)
- `hermes agent.run_task(goal)` method wraps the planner
- Constants: `MAX_REPLANS=3`, `MAX_STEPS=20` (prevent runaway)
- 17/17 tests pass (planner tested with mock)
- Real LLM test still pending (user needs to run with `bin\start-llm-smart.bat` first)
- Wrapper: `bin\hermes-task.bat "<goal>"` for one-liner use
- Health probe: `GET /health` returns `{status, version, cloud_available, local_available, mode}`

Original sketch (from planning):
```python
async def plan_and_execute(self, goal: str) -> str:
    plan = await self.llm.plan(goal, available_skills=self.skills.list())
    for step in plan:
        try:
            result = await self._execute_step(step)
        except Exception as e:
            plan = await self.llm.replan(goal, plan, step, e)
    return summary
```

Actual implementation lives in `hermes/planner.py`, much richer (replan on step failure, error recovery, JSON parsing with tolerance for non-strict LLM output).

### ✅ Skill marketplace — FRAMEWORK DONE
- `hermes/scripts/install_skill.py`:
  - `list` — show installed + registry
  - `install <name|url>` — download + verify SHA256 + safety check
  - `remove <name>` — uninstall
  - `publish <name> <url> --sha ... --desc ...` — add to registry
- Registry: `hermes/data/skills/registry.json` (JSON list of {name, url, sha256, desc})
- User can curate the registry themselves (or set up a public GitHub repo)



---


### ✅ 2026-06-07 — 6-track parallel integration — DONE
- **Track 1 streaming-and-sessions** (owner commit c93cb6b): real SSE via `hermes/llm.py stream()` + `hermes/sessions.py SessionStore` (atomic JSON, asyncio.Lock, one file per session at `data/sessions/<sid>.json`). Endpoints: `/api/chat/start` (returns `{stream_id, session_id, effective_model, effective_model_provider}`), `/api/chat/stream/{id}` (SSE `data: {type,content,...}`), `/api/chat/cancel`, `/api/chat/stream/status`, persistent `GET/PATCH/DELETE /api/chat/sessions{,/{id}}`. Adapter's old EventSource mock removed.
- **Track 2 workspace-browser**: `hermes/workspace.py` (HERMES_ROOT trust boundary, whitelist-gated file browser, path-traversal defense, atomic JSON). Endpoints: `/api/workspaces{,/add,/remove}`, `/api/list`, `/api/file`, `/api/media`. Persisted at `data/workspaces.json`.
- **Track 3 settings-persistence**: `hermes/webui_settings.py` (32-key DEFAULT_SETTINGS + 1-level nested deep-merge + atomic write). `GET/POST /api/webui/settings` is now real (was noop). Persisted at `data/webui_settings.json`.
- **Track 4 kanban-board**: `hermes/kanban.py` (KanbanStore: boards/tasks/events with atomic JSON, default board + 5 sample tasks bootstrap, 2000-event cap, CSS-safe color sanitizer). 22 endpoints registered. SSE/dispatch/comments/worktree are noop stubs (UI falls back to 30s polling). Persisted at `data/kanban/{boards,tasks,events}.json`.
- **Track 5 cron-scheduler**: `hermes/cron.py` (CronManager + Job dataclass + croniter + 30s background loop + shell/task/webhook action runners + UI-shape serializers). 10 endpoints. Started in `create_app` startup, stops on shutdown. `requirements.txt` + `croniter==6.0.0`. Persisted at `data/crons/jobs.json`.
- **Track 6 final-integration** (this task): 13 endpoints all 200 on a live mock-mode server; SSE 53+ chunks; settings `theme=sepia` + `display.streaming=false` survive `Stop-Process` + restart; session `e2e-test-session` (5 msgs) survives; kanban default board (6 tasks) survives; kanban CRUD + cron CRUD roundtrips verified. AGENTS.md §3/§4/§8/§12/§13 updated. Full transcript in `deliverable-final.md`.
- **Architecture now has 6 new modules** + 4 new data dirs/files (all in §3). Adapter grew from `~25 mapped + ~30 noop` to `~75 mapped, 0 noop` (the upstream WebUI's workspaces/kanban/crons/etc. panels are now real).

### ✅ WebUI panels: workspaces / kanban / crons — REAL
Previously `§12 Known Limitations` listed these as no-op. They are now backed by real modules (see §4 and the 6-track entry above). Adapter's noop transforms for these routes are gone.

### ⚠️ Real-time push + agent dispatch are still noop
- Kanban: SSE / dispatch / comments / worktree endpoints are stubs (UI uses 30s polling on `/api/kanban/events`).
- Crons: action runners are real; but no streaming/notification back to the WebUI.
- Next: WebSocket / SSE upgrade for kanban + cron status.

### Skill marketplace — unchanged
Still framework-only (`hermes/scripts/install_skill.py`); no marketplace backend yet.

## 14. Conversation Reference

This project was built across one long session on 2026-06-04/05. Key
turns (in Mavis conversation memory if picked up later):
- Built portable framework (Day 1)
- Fixed hermes-all.bat CRLF bug
- Integrated Open WebUI (overcame: missing __main__, RAG embedder,
  model id mismatch, Ollama auto-detect)
- Smart NGL launcher (overcame: 32-bit int overflow, nvidia-smi in for/f)
- Memory bank + cleanup (this file)

---

## 15. Installed Skills (NEW 2026-06-08)

14 skills live at `data/hermes-agent/skills/`, organized by upstream category.
Loaded into the Web UI agent via the `skills` toolset (see `config.yaml` `toolsets:`).

### From upstream `optional-skills/finance/` (copy)

| Skill | Purpose | Has `pip` deps in SKILL.md |
|---|---|---|
| `finance/excel-author` | Build .xlsx with named ranges + formula audit trail | `openpyxl>=3.0` |
| `finance/pptx-author` | Build .pptx with presentation design conventions | `python-pptx>=0.6` |
| `finance/comps-analysis` | Comparable Company Analysis (Excel model) | `openpyxl` |
| `finance/dcf-model` | DCF discounted cash flow model (Excel) | `openpyxl` |

### From upstream `skills/` built-in tree (copy, round 2)

| Skill | Purpose | Notes |
|---|---|---|
| `productivity/powerpoint` | Build .pptx with comprehensive conventions (1MB, biggest skill) | Full templates + slide-design rules |
| `productivity/ocr-and-documents` | OCR scanned PDFs / extract text + tables from images | Likely tesseract-based |
| `productivity/nano-pdf` | Lightweight PDF reading / extraction | Smallest skill (1.4 KB) |
| `productivity/google-workspace` | Google Docs / Sheets / Slides integration | 83 KB, requires `gcloud` auth |
| `creative/claude-design` | Visual design system + mockup conventions | 20 KB |

### From community GitHub repos (`git clone --depth=1`)

| Skill | Source | Purpose |
|---|---|---|
| `creative/drawio-skill` | `Agents365-ai/drawio-skill` | drawio diagram generation (flowcharts / architecture / ER / UML) |
| `creative/avoid-ai-writing` | `conorbronsdon/avoid-ai-writing` | Audit + rewrite text to strip AI-isms (multiple voice profiles) |
| `productivity/plur-memory` | `plur-ai/plur` (`skills/plur-memory/`) | Persistent engram memory across sessions |
| `productivity/plur-session-end` | `plur-ai/plur` (`skills/plur-session-end/`) | Extract durable learnings at session end |
| `autonomous-ai-agents/hermes-dojo` | `Yonkoo11/hermes-dojo` | Self-improvement system — analyzes past sessions, auto-patches skills |

### How they get discovered

- Web UI agent spawn-time: scans `~/.hermes/skills/` (= `E:\Hermes Agent\data\hermes-agent\skills/`)
- Each category dir has subdirs, each subdir has `SKILL.md` with `name:` frontmatter
- Slash commands (`/excel-author`, `/drawio-skill`, etc.) auto-injected as user messages (not system prompt) — preserves prompt caching
- Toolset filter: agent must have `skills` toolset enabled in `config.yaml` `toolsets:` list

### Install method (rate-limit-free)

`hermes skills install <name> --force` would route through `unified_search` → GitHub API (60 req/hr). To avoid burning the rate limit we used direct `git clone --depth=1` of each `user/repo` into the staging dir, then `shutil.copytree` into `data/hermes-agent/skills/<category>/<name>/`. For built-ins we just `shutil.copytree` from `E:\Hermes Agent\hermes-agent-source\skills/<cat>/<name>/` directly. The Web UI's skill scanner doesn't care about provenance, only the directory structure.

### Skipped (2 of 6 user-requested, from round 1)

- `research-agent` — not in upstream `skills/` or `optional-skills/`; needs a specific GitHub source URL from the user
- `multiagent` — same; closest upstream skill is `skills/research/research-paper-writing` but that's not the same thing

### To install more later

```bash
# Method 1: upstream catalog (uses GitHub API, may rate-limit)
"E:\Hermes Agent\portable-python\python.exe" "E:\Hermes Agent\hermes-agent-source\hermes_cli\skills_hub.py" install <name> --force

# Method 2: direct git clone (no rate limit) — for community skills
# 1. git clone --depth=1 https://github.com/<user>/<repo>.git to scratchpad
# 2. find the canonical SKILL.md (root or skills/<name>/)
# 3. shutil.copytree to data/hermes-agent/skills/<category>/<skill-name>/
# 4. restart Web UI

# Method 3: copy built-in (no network needed)
xcopy /E /I "E:\Hermes Agent\hermes-agent-source\skills\<cat>\<name>" "E:\Hermes Agent\data\hermes-agent\skills\<cat>\<name>"
```

---

## 16. Portability Audit (2026-06-08)

User asked: every file/service/dep/env that `hermes-all.bat` opens must be
**inside the `Hermes Agent` folder itself** — no system PATH, no
`E:\Hermes Agent\…` hardcoded literals, no missing `~/.mavis/...` lookups.
On a fresh Windows PC the project must be plug-and-play: copy the folder,
double-click `bin\hermes-all.bat`, browser opens. Goal: zero
post-install configuration.

### Audit method

For every script reached from `hermes-all.bat`:

1. `hermes-all.bat` (entry) → all the bat/ps1/py/binaries it spawns
2. For each child script: grep all path-like tokens; flag literals
   matching `[C-Z]:\\` (real drive letters, not `\s` regex escapes or
   `C:\Windows` placeholders in error text)
3. Cross-check env vars injected into subprocesses (NODE, PYTHONPATH,
   HERMES_*) resolve to paths under `HERMES_ROOT`
4. For Python: any module that uses `Path('E:\Hermes Agent')` literal
   instead of `Path(__file__).resolve().parents[N]`
5. Spot-check fallback (portable-python: runs in any cwd ✓; Node:
   bundled in `runtime/node23/`)

### Fixes applied this session

| File | Was | Now |
|---|---|---|
| `bin/verify-server.bat` | `cd /d "E:\Hermes Agent"` + literal `E:\Hermes Agent\portable-python\python.exe` | `set "HERMES_ROOT=%~dp0.."` + `%HERMES_ROOT%\portable-python\python.exe` |
| `bin/webui-new.bat` | dev hint `mklink /J "E:\hermes-web-ui-main"` hardcoded in error message | portable git-clone hint pointing at `ArtificialAngels/hermes-agent` |
| `bin/webui-new.bat` | bootstrap wrote `data/hermes-agent/config.yaml` once and never touched it, so `mcp_servers.hermes-studio.env.HERMES_WEB_UI_HOME` stayed at the old install's drive letter | PowerShell fix-up block rewrites the two mcp env values to current `HERMES_ROOT` on every launch, idempotent |
| `hermes/scripts/install_skill.py` | `HERMES_ROOT = Path(r'E:\Hermes Agent')` | `HERMES_ROOT = Path(__file__).resolve().parents[2]` |
| `hermes/scripts/rebuild_kb.py` | `HERMES_ROOT = Path(r'E:\Hermes Agent')` | same |
| Root `start_llm_server.bat` | hardcoded `E:\` + dead code (called `local_llm_server.py` which doesn't exist + used PATH `python` not portable) | **deleted** (no references in repo) |
| Root `update_env.ps1` | hardcoded `E:\` + **contained a live MiniMax API key in plaintext** | **deleted** (would have leaked key to GitHub) |
| `data/logs/debug-residue-*.bat` (17) | early NGL debug scripts, never invoked | **deleted** |
| `data/logs/debug-residue-*.ps1` (1) | ps1 startup repro harness, never invoked | **deleted** |
| `data/logs/_diag*.txt` (25) | stdout from the deleted diag bats | **deleted** |
| `data/logs/_*.{log,err,txt}` (17) | other underscore-prefixed debug dumps from 6/5–6/6 sessions | **deleted** |
| `data/logs/removed-*.bat` (2) | backups of superseded scripts | **deleted** |

### Items intentionally left as-is

| File | Why kept |
|---|---|
| `hermes/scripts/import_ollama_blobs.py` L25 | `os.environ.get('USERPROFILE', r'C:\Users\PZS0X')` — env-var lookup with a one-user default that won't fire on a normal install. Cosmetic only. |
| `hermes/config.py` L169 | Comment `# E:\Hermes Agent\.env when running from anywhere` — doc, not code. |
| `hermes/workspace.py` L49 | Docstring example of the `workspaces.json` shape — not a real value. |
| `hermes/workspace.py` L75 | Already portable: `Path(__file__).resolve().parent.parent` ✓ |
| (removed) | `hermes/doctor.py` was deleted in an earlier cleanup phase; no portable rewrite needed. |
| `data/webui-new/app/bin/hermes-web-ui.mjs` L109 | `process.env.SystemRoot || 'C:\\Windows'` — env-var fallback, doesn't fire in practice. |
| `hermes-agent-source/scripts/install.ps1` L210 | User-facing hint about `setx NODE_EXTRA_CA_CERTS "C:\path\to\corp-ca.pem"` — placeholder text in a help message. |
| `data/webui-new/app/portable/*.bat`, `runtime/node23/install_tools.bat`, etc. | Third-party (Node 23 build scripts). Not touched. |

### Portability checklist (recurring)

When adding a new script, the test is mechanical:

```powershell
# from a fresh shell, with the folder on D:\ or C:\ or any drive:
cd D:\Hermes Agent
.\bin\hermes-all.bat
# → browser opens :8648, all 3 services up, model loads, chat works
# If anything needed a registry entry, %APPDATA% lookup, system Python,
# system Node, or `E:\` literal, audit fails.
```

### GitHub-readiness

- All hardcoded `E:\Hermes Agent\` literals that the project would have
  shipped: removed (4 files) or marked as doc-only (4 files).
- API keys that lived in repo-tracked `.ps1` files (`update_env.ps1`):
  removed. Remaining secrets live in `data/hermes-agent/config.yaml`
  (gitignored) and `.env` (gitignored).
- `data/webui-new/app/node_modules/` is the only remaining ~big
  dependency, shipped pre-bundled so `npm install` is not required.


