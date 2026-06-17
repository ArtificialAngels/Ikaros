"""
Smoke test for X-Hermes-Routing header override (bug fixed 2026-06-17).

Before fix:
  - X-Hermes-Routing: local  → routes to llama_server ✅
  - X-Hermes-Routing: cloud  → silently ignored, falls through to smart routing ❌

After fix:
  - X-Hermes-Routing: local  → routes to llama_server ✅
  - X-Hermes-Routing: cloud  → routes to cloud_api ✅ (or clean 4xx if model unknown)

Verifies by sending POSTs to /v1/chat/completions with both header values and
inspecting the response header `x-hermes-routing-target` (set by bridge).

Run:
    python tests/smoke_routing_override.py
"""

from __future__ import annotations

import sys
import urllib.request
import urllib.error
import json
from pathlib import Path

BRIDGE = "http://127.0.0.1:7860"


def post(path: str, headers: dict, body: dict, timeout: int = 30) -> tuple[int, dict, str]:
    req = urllib.request.Request(
        f"{BRIDGE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "curl/8.19.0",  # bridge middleware 500s on bare "Python-urllib/3.12"
            "Accept": "*/*",
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (
                resp.status,
                dict(resp.headers),
                resp.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as e:
        return (e.code, dict(e.headers or {}), e.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return (-1, {}, f"connection error: {e}")


def main() -> int:
    failures: list[str] = []

    # ---- Test 1: cloud header routes to cloud_api (or returns clean 4xx for unknown model) ----
    status, headers, body = post(
        "/v1/chat/completions",
        headers={"X-Hermes-Routing": "cloud"},
        body={"model": "MiniMax-M3", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 20, "stream": False},
    )
    route_target = headers.get("x-hermes-routing-target", "").lower()
    # Acceptable outcomes after the fix:
    #   - cloud_api reached: route_target starts with "cloud"
    #   - cloud client fall back to local: route_target == "llama_server(fallback)"
    #   - explicit 4xx: route_target == "cloud_api" + status in (400, 422)
    # The bug we fixed was: header was silently ignored, route_target stayed "llama_server"
    # (the model-not-found 400 from llama). After the fix, the routing decision
    # is taken in /v1/chat/completions regardless of model existence downstream.
    ok_routing = (
        route_target.startswith("cloud")
        or route_target == "llama_server(fallback)"  # cloud client fell back, expected
    )
    if not ok_routing:
        failures.append(
            f"[cloud header] routing override was ignored! "
            f"x-hermes-routing-target='{route_target}', expected 'cloud:*' or 'llama_server(fallback)' "
            f"(status={status}, body={body[:200]})"
        )
    else:
        print(f"  ✓ X-Hermes-Routing: cloud  -> x-hermes-routing-target={route_target} (status={status})")

    # ---- Test 2: local header routes to llama_server ----
    status, headers, body = post(
        "/v1/chat/completions",
        headers={"X-Hermes-Routing": "local"},
        body={"model": "Qwen3-4B-Q4_K_M", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10, "stream": False},
    )
    route_target = headers.get("x-hermes-routing-target", "").lower()
    if route_target != "llama_server":
        failures.append(
            f"[local header] expected x-hermes-routing-target=llama_server, got '{route_target}' "
            f"(status={status}, body={body[:200]})"
        )
    else:
        print(f"  ✓ X-Hermes-Routing: local  -> x-hermes-routing-target=llama_server (status={status})")

    # ---- Test 3: no header falls through to smart routing (engine) ----
    status, headers, body = post(
        "/v1/chat/completions",
        headers={},
        body={"model": "Qwen3-4B-Q4_K_M", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10, "stream": False},
    )
    # Just confirm we got SOMETHING (200 or 4xx for valid model), not crash
    if status < 0:
        failures.append(f"[no header] bridge unreachable: {body}")
    else:
        print(f"  ✓ no header (smart routing) -> status={status}, target={headers.get('x-hermes-routing-target', '?')}")

    # ---- Summary ----
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL PASSED ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
