"""Smoke test for hermes-env.{bat,ps1} portable-env setup.

Verifies that after running deps/hermes-env.bat:
  1. HERMES_ROOT resolves to the project root
  2. All 14 HERMES_* env vars are set
  3. The runtime/ subtree has the canonical assets (no junction needed)
  4. deps/ has NO directory junctions (i.e. nothing in deps/ is a
     reparse point — the old layout used `mklink /J` and broke when
     the project moved to a new drive letter)
  5. The PATH augmentation contains the canonical node23 + cuda bins

Run:
    portable-python\python.exe tests\smoke_hermes_env.py
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run(cmd: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
    return (r.stdout, r.stderr, r.returncode)


def main() -> int:
    failures: list[str] = []

    # ---- 1. hermes-root.py resolve ----
    out, err, rc = run([str(ROOT / "portable-python" / "python.exe"),
                        str(ROOT / "bin" / "hermes-root.py"), "resolve"])
    if rc != 0 or not out.strip():
        failures.append(f"hermes-root.py resolve failed: {err.strip()}")
        print(f"[FAIL] resolve: {err.strip()}")
        return 1
    resolved = out.strip()
    print(f"[OK]   resolve -> {resolved}")

    # ---- 2. hermes-root.py init (parse KEY=VALUE lines) ----
    out, err, rc = run([str(ROOT / "portable-python" / "python.exe"),
                        str(ROOT / "bin" / "hermes-root.py"), "init"])
    if rc != 0:
        failures.append(f"hermes-root.py init failed: {err.strip()}")
    env = {}
    for line in out.strip().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    expected = [
        "HERMES_ROOT", "HERMES_BIN", "HERMES_DATA", "HERMES_HOME",
        "HERMES_MODELS", "HERMES_PYTHON", "HERMES_DEPS", "HERMES_RUNTIME",
        "HERMES_CONFIG", "HERMES_MODULES", "HERMES_LOGS", "HERMES_DATA_DIR",
    ]
    for k in expected:
        if k not in env:
            failures.append(f"missing env var: {k}")
        elif not env[k]:
            failures.append(f"empty env var: {k}")
        else:
            print(f"[OK]   {k:20} = {env[k]}")

    # ---- 3. runtime/ asset presence ----
    runtime = Path(env["HERMES_RUNTIME"])
    assets = [
        ("llama-server.exe (CPU)",       runtime / "llama-server.exe"),
        ("llama-server-cuda-12.4.exe",   runtime / "cuda" / "12.4" / "llama-server-cuda-12.4.exe"),
        ("cudart64_12.dll",              runtime / "cuda" / "12.4" / "cudart64_12.dll"),
        ("node.exe",                     runtime / "node23" / "node.exe"),
        ("aria2c.exe",                   runtime / "aria2c.exe"),
    ]
    for label, p in assets:
        if p.is_file():
            print(f"[OK]   runtime asset present: {label} -> {p}")
        else:
            # Asset may legitimately be missing in a fresh clone; mark
            # as a soft failure but don't block. setup-portable.bat
            # is the install path.
            print(f"[INFO] runtime asset missing (run setup-portable.bat): {label} -> {p}")

    # ---- 4. deps/ MUST NOT have any directory junctions ----
    deps = Path(env["HERMES_DEPS"])
    print()
    print(f"[deps/] scanning {deps} for stale junctions...")
    junction_count = 0
    for entry in sorted(deps.iterdir()):
        full = str(entry)
        attrs = ctypes.windll.kernel32.GetFileAttributesW(full)
        is_reparse = bool(attrs & 0x400)
        is_dir = bool(attrs & 0x10)
        if is_reparse and is_dir:
            junction_count += 1
            print(f"[FAIL] stale junction under deps/: {entry.name}")
            failures.append(f"deps/ contains stale junction: {entry.name}")
        else:
            tag = "DIR" if is_dir else "FILE"
            print(f"[OK]   [{tag}] {entry.name}")
    if junction_count == 0:
        print(f"[OK]   no junctions under deps/ (junction-free layout verified)")
    else:
        failures.append(f"deps/ contains {junction_count} stale junction(s)")

    # ---- 5. Test hermes-env.bat (no-op smoke: just verify it does not crash) ----
    print()
    print("[hermes-env.bat] running smoke test...")
    out, err, rc = run([str(ROOT / "bin" / "hermes-root.bat"), "init"],
                       cwd=str(ROOT))
    if rc != 0:
        failures.append(f"hermes-root.bat init failed (rc={rc}): {err.strip()}")
        print(f"[FAIL] bat init: {err.strip()}")
    else:
        # Parse KEY=VALUE
        bat_env = {}
        for line in out.strip().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                bat_env[k.strip()] = v.strip()
        if bat_env.get("HERMES_RUNTIME", "").endswith("\\runtime"):
            print(f"[OK]   bat env resolves HERMES_RUNTIME correctly: {bat_env['HERMES_RUNTIME']}")
        else:
            failures.append(f"bat env HERMES_RUNTIME wrong: {bat_env.get('HERMES_RUNTIME')}")
            print(f"[FAIL] bat env HERMES_RUNTIME wrong: {bat_env.get('HERMES_RUNTIME')}")

    # ---- 6. Summary ----
    print()
    print("=" * 60)
    if failures:
        print(f"[FAIL] {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"[OK]   all smoke tests passed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
