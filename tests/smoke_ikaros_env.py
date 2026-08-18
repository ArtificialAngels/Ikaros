"""Smoke test for the unified portable env authority (bin/ikaros-env.{bat,ps1,sh}).

Verifies (2026-08-18, hermes/neko retired, dsh adopted as work engine):
  1. bin/ikaros-env.bat self-anchors IKAROS_ROOT to the project root (no
     hardcoded drive letters), and defines all core IKAROS_* vars.
  2. dsh (deepseek-harness) vars are present in the authority.
  3. No HERMES_* / NEKO_* legacy vars are defined.
  4. Root .env stays key-only (no active DEEPSEEK_BASE_URL — dsh reserves
     it as a launch-only variable; the .env may only mention it in comments).

Method: ikaros-env.bat supports `--print` mode (dumps all IKAROS_* vars
from inside its setlocal scope), so we can verify the resolved values
without relying on parent-shell visibility.

Run:
    portable-python\\python.exe tests\\smoke_ikaros_env.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BAT = ROOT / "bin" / "ikaros-env.bat"


def run(cmd: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd,
                       encoding="gbk", errors="replace")
    return (r.stdout, r.stderr, r.returncode)


def main() -> int:
    failures: list[str] = []

    # ---- 1. ikaros-env.bat --print ----
    out, err, rc = run(["cmd", "/c", str(BAT), "--print"], cwd=str(ROOT))
    if rc != 0:
        failures.append(f"ikaros-env.bat --print failed (rc={rc}): {err.strip()}")
        print(f"[FAIL] bat --print: {err.strip()}")
        return 1
    env = {}
    for line in out.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    ikaros_root = env.get("IKAROS_ROOT", "")
    norm = os.path.normpath(ikaros_root).lower()
    want = os.path.normpath(str(ROOT)).lower()
    if norm == want:
        print(f"[OK]   IKAROS_ROOT -> {ikaros_root}")
    else:
        failures.append(f"IKAROS_ROOT={ikaros_root} != {ROOT}")
        print(f"[FAIL] IKAROS_ROOT={ikaros_root}")

    core_vars = [
        "IKAROS_BIN", "IKAROS_CONFIG", "IKAROS_DATA", "IKAROS_RUNTIME",
        "IKAROS_PYTHON", "IKAROS_NODE", "IKAROS_LOGS", "IKAROS_MEMORY",
    ]
    for k in core_vars:
        if not env.get(k):
            failures.append(f"missing/empty env var: {k}")
        else:
            print(f"[OK]   {k:22} = {env[k]}")

    dsh_vars = ["IKAROS_DSH", "IKAROS_DSH_SOURCE", "IKAROS_DSH_PROFILE",
                "IKAROS_DSH_WEB_PORT", "IKAROS_DSH_OVERLAY"]
    for k in dsh_vars:
        if not env.get(k):
            failures.append(f"missing dsh env var: {k}")
        else:
            print(f"[OK]   {k:22} = {env[k]}")

    # ---- 2. No legacy HERMES_*/NEKO_* in the authority ----
    legacy = [k for k in env if k.startswith("HERMES") or k.startswith("NEKO") or "HERMES" in k]
    if legacy:
        failures.append(f"legacy env vars leaked: {legacy}")
        print(f"[FAIL] legacy env vars: {legacy}")
    else:
        print("[OK]   no HERMES_*/NEKO_* legacy vars")

    # ---- 3. Root .env key-only sanity (ignore comments) ----
    envf = ROOT / ".env"
    if envf.is_file():
        active = [l for l in envf.read_text(encoding="utf-8").splitlines()
                  if "=" in l and not l.strip().startswith("#")]
        if any(l.split("=", 1)[0].strip() == "DEEPSEEK_BASE_URL" for l in active):
            failures.append("root .env actively sets DEEPSEEK_BASE_URL (dsh reserved)")
            print("[FAIL] .env active DEEPSEEK_BASE_URL")
        else:
            print("[OK]   root .env has no active DEEPSEEK_BASE_URL")
    else:
        failures.append("root .env missing")
        print("[FAIL] .env missing")

    # ---- 4. dsh CLI reachable via local runtime (no npm-global dependency) ----
    dsh_bin = ROOT / "runtime" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    node = ROOT / "runtime" / "node" / "node.exe"
    if node.is_file() and dsh_bin.is_file():
        out2, err2, rc2 = run([str(node), str(dsh_bin), "--version"], cwd=str(ROOT))
        if rc2 == 0:
            print(f"[OK]   dsh CLI -> {out2.strip()}")
        else:
            failures.append(f"dsh --version failed: {err2.strip()}")
            print(f"[FAIL] dsh --version: {err2.strip()}")
    else:
        failures.append("runtime/dsh or runtime/node missing")
        print("[FAIL] runtime/dsh or runtime/node missing")

    # ---- Summary ----
    print()
    print("=" * 60)
    if failures:
        print(f"[FAIL] {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("[OK]   all env smoke tests passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
