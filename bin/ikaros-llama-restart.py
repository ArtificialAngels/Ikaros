#!/usr/bin/env python3
"""
icarus-llama-restart.py — end-to-end helper around `POST /v1/llama/restart`.

Why this exists
---------------
WebUI's "refresh model cache" button only refreshes cloud provider catalogs
(see webui index.js:1035). It does NOT touch local .gguf. After you
add/remove a model in data/models/, you have to restart llama-server to
make it re-scan --models-dir and router-preset.ini.

The simplest way for a human is:
    portable-python/python.exe bin/icarus-llama-restart.py

What it does
------------
1. Snapshots the model list before the restart.
2. POSTs /v1/llama/restart to bridge (which calls supervisor).
3. Waits for llama to come back healthy.
4. Snapshots the model list after the restart.
5. Prints a diff (added / removed / unchanged) so the user can see exactly
   which model ids moved in or out of the dropdown.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BRIDGE_URL = "http://127.0.0.1:7860"
LLAMA_URL = "http://127.0.0.1:8080"


def http_json(method: str, url: str, body: dict | None = None, timeout: float = 60.0) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as r:
            payload = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(payload)
            except json.JSONDecodeError:
                return r.status, payload
    except error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def fetch_model_ids() -> list[str]:
    """Read bridge's /v1/models (which dedups alias↔.gguf and filters mmproj)."""
    code, body = http_json("GET", f"{BRIDGE_URL}/v1/models", timeout=5.0)
    if code != 200:
        raise RuntimeError(f"bridge /v1/models failed: HTTP {code} {body}")
    data = body.get("data", []) if isinstance(body, dict) else []
    return [m["id"] for m in data]


def print_diff(before: list[str], after: list[str]) -> None:
    set_b, set_a = set(before), set(after)
    added = sorted(set_a - set_b)
    removed = sorted(set_b - set_a)
    kept = sorted(set_a & set_b)
    print(f"  before: {len(before):>2} model(s)")
    print(f"  after : {len(after):>2} model(s)")
    if added:
        print(f"  [+] added  : {', '.join(added)}")
    if removed:
        print(f"  [-] removed: {', '.join(removed)}")
    if kept:
        print(f"  [=] kept   : {', '.join(kept)}")
    if not added and not removed:
        print("  (no changes)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--dry-run", action="store_true", help="just show before/after, don't restart")
    ap.add_argument("--bridge", default=BRIDGE_URL, help="bridge base URL")
    args = ap.parse_args()

    print(f"== icarus-llama-restart  bridge={args.bridge} ==")
    print()
    print("Snapshot before:")
    before = fetch_model_ids()
    for m in before:
        print(f"  - {m}")
    print()

    if args.dry_run:
        print("--dry-run: not restarting")
        return 0

    print("POST /v1/llama/restart ...")
    t0 = time.monotonic()
    code, body = http_json("POST", f"{args.bridge}/v1/llama/restart", body={}, timeout=90.0)
    elapsed = time.monotonic() - t0
    print(f"  HTTP {code} ({elapsed:.1f}s)")
    if isinstance(body, dict):
        for k, v in body.items():
            if k == "supervisor_stdout_tail":
                print(f"  {k}:")
                for line in v:
                    print(f"      {line}")
            else:
                print(f"  {k}: {v}")
    else:
        print(f"  body: {body}")
    if code != 200:
        print(f"\n[FAIL] restart endpoint returned {code}; see body above")
        return 1

    print()
    print("Snapshot after:")
    after = fetch_model_ids()
    for m in after:
        print(f"  - {m}")
    print()
    print("Diff:")
    print_diff(before, after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
