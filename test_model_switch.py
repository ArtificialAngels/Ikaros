"""
Model switch integration test.

Tests:
  1. Start llama-server with model A
  2. Verify /v1/models returns model A
  3. Switch to model B via model_manager.py
  4. Verify /v1/models returns model B
  5. Report all errors
"""
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

HERMES_ROOT = Path(__file__).resolve().parent
MODELS_DIR = HERMES_ROOT / "data" / "models"
PYTHON = HERMES_ROOT / "portable-python" / "python.exe"
MANAGER = HERMES_ROOT / "hermes" / "scripts" / "model_manager.py"
LLAMA_URL = "http://127.0.0.1:8080"


def run(cmd, timeout=30):
    """Run a command, return (rc, stdout, stderr)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def get_model():
    """Get current model from llama-server."""
    try:
        r = urllib.request.urlopen(f"{LLAMA_URL}/v1/models", timeout=5)
        import json
        data = json.loads(r.read())
        if data.get("data"):
            return data["data"][0]["id"]
    except Exception as e:
        return f"(error: {e})"
    return "(no models)"


def kill_all():
    """Kill all llama-server processes."""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process -Name 'llama-server*' -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True, timeout=10
    )
    time.sleep(3)


def start_model(model_path):
    """Start llama-server with given model."""
    env = dict(subprocess.os.environ)
    env["LLAMA_MODEL"] = str(model_path)
    subprocess.Popen(
        ["cmd", "/c", str(HERMES_ROOT / "bin" / "start-llm-smart.bat")],
        env=env, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def wait_ready(timeout=120):
    """Wait for llama-server to be ready."""
    for i in range(timeout // 2):
        try:
            r = urllib.request.urlopen(f"{LLAMA_URL}/health", timeout=2)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


# ================================================================
print("=" * 60)
print("  Model Switch Integration Test")
print("=" * 60)
print()

# ---- Phase 1: Find models ----
models = sorted(MODELS_DIR.glob("*.gguf"), key=lambda p: p.stat().st_size)
if len(models) < 2:
    print("ERROR: Need at least 2 GGUF models to test switching")
    sys.exit(1)

model_a = models[0]  # smallest
model_b = models[1]  # next smallest

print(f"Models found: {len(models)}")
for m in models:
    print(f"  {m.name}  ({m.stat().st_size / 1e9:.2f} GB)")
print()

# ---- Phase 2: Kill any existing server ----
print("[1] Killing any existing llama-server...")
kill_all()
print("    Done.")
print()

# ---- Phase 3: Start with model A ----
print(f"[2] Starting with model A: {model_a.name}")
start_model(model_a)
print(f"    Waiting for model to load...")
if not wait_ready():
    print("    ERROR: Model A failed to start")
    sys.exit(1)
model_a_id = get_model()
print(f"    Now serving: {model_a_id}")
print()

# ---- Phase 4: Switch to model B via model_manager.py ----
print(f"[3] Switching to model B: {model_b.name}")
rc, stdout, stderr = run(
    [str(PYTHON), str(MANAGER), "switch", model_b.name],
    timeout=180
)
print(f"    Exit code: {rc}")
if stdout:
    for line in stdout.splitlines():
        print(f"    [out] {line}")
if stderr:
    for line in stderr.splitlines():
        print(f"    [err] {line}")
print()

# ---- Phase 5: Verify model B ----
print(f"[4] Verifying model B...")
time.sleep(3)
model_b_id = get_model()
print(f"    Now serving: {model_b_id}")

if model_a_id == model_b_id and model_a.name != model_b.name:
    print(f"    FAIL: Model did not change! Still serving {model_a_id}")
    sys.exit(1)
elif "error" in str(model_b_id).lower():
    print(f"    FAIL: Cannot reach llama-server: {model_b_id}")
    sys.exit(1)
else:
    print(f"    OK: Model switched from {model_a.name} -> {model_b.name}")
    print(f"    Model ID: {model_b_id}")
    print()

# ---- Phase 6: Cleanup ----
print("[5] Cleanup...")
kill_all()
time.sleep(2)
print("    Done.")
print()

print("=" * 60)
print("  Test PASSED")
print("=" * 60)
