# deps/ — Hermes unified dependency zone

**First thing every script in the project calls**: dot-source
`hermes-env.bat` (from `.bat` files) or `hermes-env.ps1` (from `.ps1`
modules). After that, the 14 `HERMES_*` env vars are set in the
current process, the active CUDA bin is on PATH, and portable-python's
Scripts/ is wired up.

## Layout

```
deps/
├── hermes-env.bat        # bat entry point — sets 14 HERMES_* env vars,
│                         #   reads cuda-active.json, auto-heals stale
│                         #   junctions, augments PATH
├── hermes-env.ps1        # PowerShell equivalent (same env-block, plus
│                         #   $PYTHON / $NODE / $LLAMACPP_BIN aliases)
├── manifest.json         # Version tracking for runtime assets that
│                         #   bin/setup-portable.bat downloads
└── README.md             # This file
```

**No junctions in this directory. Ever.**

Earlier versions of Hermes used `mklink /J` to expose `runtime/`,
`node23/`, and `portable-python/` under `deps\` as
`deps\node\`, `deps\tools\`, `deps\llamacpp\bin\`, and
`deps\python-test\`. That was abandoned on 2026-06-13 because NTFS
junctions store their target as an **absolute path** in the
reparse-point data — when the project is moved to a new drive letter
(E: → F:) the junctions still point at E:\... and any consumer that
touches `deps\node\...` silently gets nothing. The env files now
resolve the real paths directly via `%HERMES_RUNTIME%`, and there's
a one-time auto-heal step (Step 3 in `hermes-env.bat`, Step 2c in
`.ps1`) that rmdir's any leftover junction an old copy might still
be carrying.

**Do not recreate them.** If a script asks for `deps\node\node.exe`,
the script is wrong — go fix the script. The canonical paths are
`runtime\node23\node.exe` (Node) and `runtime\llama-server.exe`
(llama-server). See `modules\webui\module.json` and
`modules\llm_engine\module.json` for the modern, junction-free paths.

## Heavy assets are NOT in git

This directory's tracked files are tiny (env scripts + manifest).
The actual heavy assets (portable-python, llama-server, Node 23,
CUDA runtimes) live in `runtime/`, `portable-python/`, and
`data/models/`, and are downloaded on demand by
[`bin/setup-portable.bat`](../bin/setup-portable.bat) (see the
[Fresh install guide in README.md](../README.md) for the full
sequence).

`.gitignore` explicitly excludes:

```
portable-python/
runtime/
# (legacy junction stubs, kept ignored in case they ever return)
deps/python-test/
deps/tools/
deps/node/
deps/llamacpp/
```

so even if someone accidentally recreates a junction with `mklink /J`,
the resulting reparse point can never be committed.

## Usage pattern

From a `.bat`:
```bat
@echo off
call "%~dp0..\deps\hermes-env.bat"
if errorlevel 1 exit /b 1
REM now use %HERMES_PYTHON%, %HERMES_RUNTIME%, %PATH% (with node23), ...
```

From a `.ps1`:
```powershell
. "$PSScriptRoot\..\deps\hermes-env.ps1"
# now use $HERMES_PYTHON, $HERMES_RUNTIME, $NODE, $LLAMACPP_BIN, $env:PATH
```

The entry point first delegates to `bin/hermes-root.bat init` (which
in turn calls `bin/hermes-root.py`) to resolve `HERMES_ROOT` and the
other 13 derived vars. The resolver is portable across drive letters
(USB slot changes, drive remaps, etc.) — see
[`bin/hermes-root.py`](../bin/hermes-root.py) for the priority order.
