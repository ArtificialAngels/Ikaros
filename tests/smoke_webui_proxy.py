"""Smoke test: verify webui_proxy intercepts /api/hermes/usage/stats and
forwards all other paths to upstream :8649."""
import urllib.request
import urllib.error
import json
import sys

BASE = "http://127.0.0.1:8648"
failures = 0

def check(name, expect, got):
    global failures
    status = "OK" if expect == got else "FAIL"
    if expect != got:
        failures += 1
    print(f"  [{status}] {name}: expect={expect} got={got}")

# --- Test 1: usage/stats (intercepted, fixed SQL) ---
print("=== TEST 1: usage/stats (intercepted) ===")
r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats?days=30", timeout=5)
d = json.loads(r.read().decode())
check("status", 200, r.status)
check("total_sessions type", int, type(d.get("total_sessions")))
check("model_usage rows >= 1", True, len(d.get("model_usage", [])) >= 1)
check("daily_usage rows", 31, len(d.get("daily_usage", [])))
# Check structure: each model_usage row has model/provider/base_url
if d.get("model_usage"):
    row = d["model_usage"][0]
    check("model_usage has model", True, "model" in row)
    check("model_usage has provider", True, "provider" in row)
    check("model_usage has base_url", True, "base_url" in row)
print()

# --- Test 2: usage/stats with days=90 ---
print("=== TEST 2: usage/stats?days=90 ===")
r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats?days=90", timeout=5)
d = json.loads(r.read().decode())
check("status", 200, r.status)
check("daily_usage rows", 91, len(d.get("daily_usage", [])))
print()

# --- Test 3: usage/stats with days=365 ---
print("=== TEST 3: usage/stats?days=365 ===")
r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats?days=365", timeout=5)
d = json.loads(r.read().decode())
check("status", 200, r.status)
check("daily_usage rows", 366, len(d.get("daily_usage", [])))
print()

# --- Test 4: usage/stats with days=invalid (should fall back to 30) ---
print("=== TEST 4: usage/stats?days=invalid (falls back to 30) ===")
r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats?days=abc", timeout=5)
d = json.loads(r.read().decode())
check("status", 200, r.status)
check("daily_usage rows", 31, len(d.get("daily_usage", [])))
print()

# --- Test 5: usage/stats with days=1000 (out of range, falls back to 30) ---
print("=== TEST 5: usage/stats?days=1000 (out of range, falls back to 30) ===")
r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats?days=1000", timeout=5)
d = json.loads(r.read().decode())
check("status", 200, r.status)
check("daily_usage rows", 31, len(d.get("daily_usage", [])))
print()

# --- Test 6: passthrough /api/hermes/health (proxy to :8649) ---
print("=== TEST 6: passthrough /api/hermes/health ===")
try:
    r = urllib.request.urlopen(f"{BASE}/api/hermes/health", timeout=5)
    check("status", 200, r.status)
except urllib.error.HTTPError as e:
    # 401 unauthorized is acceptable (no cookie)
    check("status (401 ok)", 401, e.code)
print()

# --- Test 7: passthrough / (root, should reach webui spa) ---
print("=== TEST 7: passthrough / (root, webui spa) ===")
try:
    r = urllib.request.urlopen(f"{BASE}/", timeout=5)
    check("status", 200, r.status)
    body = r.read(80).decode("utf-8", errors="replace")
    print(f"  body (first 80): {body[:60]}...")
except urllib.error.HTTPError as e:
    check("status", 200, e.code)
print()

# --- Test 8: repeat usage/stats 3x to check stability ---
print("=== TEST 8: stability (3x repeat) ===")
for i in range(3):
    r = urllib.request.urlopen(f"{BASE}/api/hermes/usage/stats", timeout=5)
    d = json.loads(r.read().decode())
    check(f"call {i+1} status", 200, r.status)
print()

print("=" * 50)
if failures == 0:
    print("=== ALL TESTS PASSED ===")
    sys.exit(0)
else:
    print(f"=== {failures} TEST(S) FAILED ===")
    sys.exit(1)
