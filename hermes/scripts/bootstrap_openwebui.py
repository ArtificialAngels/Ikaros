"""Auto-bootstrap Open WebUI on first run.

Runs after Open WebUI is up. Wipes (optional) + creates first admin + adds model.
Idempotent: if admin exists, just signs in.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


def req(url, method="GET", data=None, token=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def wait_for_webui(url, timeout=120):
    print(f"[bootstrap] waiting for Open WebUI at {url} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _get_model_info():
    """Extract model ID and name from environment or LLM server."""
    # Try to get from HERMES_MODEL_ALIAS (set by hermes-all.bat)
    alias = os.environ.get("HERMES_MODEL_ALIAS", "")
    if alias:
        return alias, f"{alias.replace('_', '.')} (Local)"
    
    # Try to get model path from environment
    model_path = os.environ.get("LLAMA_MODEL", os.environ.get("MODEL", ""))
    if model_path:
        # Extract filename without extension
        model_name = os.path.splitext(os.path.basename(model_path))[0]
        # Clean up for model ID (replace dots with underscores)
        model_id = model_name.replace(".", "_").replace("-", "_")
        return model_id, f"{model_name} (Local)"
    
    # Default fallback
    return "qwen2.5-7b-instruct", "Qwen2.5-7B (Local)"


def main():
    webui_url = os.environ.get("WEBUI_URL", "http://127.0.0.1:7870")
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@hermes.local")
    admin_password = os.environ.get("ADMIN_PASSWORD", "hermes123")
    admin_name = os.environ.get("ADMIN_NAME", "hermes")
    model_id, model_name = _get_model_info()
    wipe = os.environ.get("WIPE", "0") == "1"

    # Also write to a log file in case bat output is lost
    log_path = Path(os.environ.get("BOOTSTRAP_LOG", "")) or (Path("E:/Hermes Agent/hermes/data/logs") / "bootstrap.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        log_fh.write(msg + "\n")
        log_fh.flush()

    log("=" * 50)
    log(f"[bootstrap] start at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"[bootstrap] webui_url={webui_url} admin={admin_email}")

    # Optional: wipe OW data
    if wipe:
        data_dir = Path(os.environ.get("OW_DATA_DIR", ""))
        if data_dir.exists():
            for fname in ["webui.db", "webui.db-shm", "webui.db-wal"]:
                f = data_dir / fname
                if f.exists():
                    f.unlink()
                    log(f"[bootstrap] wiped {f}")

    # Wait for OW
    if not wait_for_webui(f"{webui_url}/health"):
        log("[bootstrap] FAIL: Open WebUI never came up")
        log_fh.close()
        sys.exit(1)
    log("[bootstrap] Open WebUI is up")

    # Signup (creates first user as admin)
    log(f"[bootstrap] signing up first admin: {admin_email}")
    code, resp = req(
        f"{webui_url}/api/v1/auths/signup",
        method="POST",
        data={"email": admin_email, "password": admin_password, "name": admin_name},
    )
    token = None
    if code == 200:
        token = resp.get("token")
        log(f"[bootstrap] signup OK, role={resp.get('role')!r}")
    elif code == 403:
        log(f"[bootstrap] admin already exists, signing in...")
        code2, resp2 = req(
            f"{webui_url}/api/v1/auths/signin",
            method="POST",
            data={"email": admin_email, "password": admin_password},
        )
        if code2 == 200:
            token = resp2.get("token")
            log(f"[bootstrap] signin OK, role={resp2.get('role')!r}")
        else:
            log(f"[bootstrap] signin FAILED: {code2} {resp2}")
            log_fh.close()
            sys.exit(1)
    else:
        log(f"[bootstrap] signup FAILED: {code} {resp}")
        log_fh.close()
        sys.exit(1)

    if not token:
        log("[bootstrap] no token, aborting")
        log_fh.close()
        sys.exit(1)

    # Clean up old models first (only keep current running model)
    log("[bootstrap] cleaning up old models...")
    code, resp = req(f"{webui_url}/api/v1/models", method="GET", token=token)
    if code == 200:
        existing_models = resp.get("data", [])
        for m in existing_models:
            # Only delete if it's not the current model
            if m.get("id") != model_id:
                log(f"[bootstrap] removing old model: {m.get('id')}")
                req(
                    f"{webui_url}/api/v1/models/delete",
                    method="DELETE",
                    token=token,
                    data={"id": m.get("id")},
                )

    # Add current model
    log(f"[bootstrap] adding model: {model_id}")
    code, resp = req(
        f"{webui_url}/api/v1/models/create",
        method="POST",
        token=token,
        data={
            "id": model_id,
            "name": model_name,
            "base_model_id": model_id,
            "base_model_name": model_id,
            "meta": {
                "capabilities": {"vision": False, "usage": True},
            },
            "params": {},
        },
    )
    if code == 200:
        log(f"[bootstrap] model added: {resp.get('id')!r}")
    elif code == 401:
        log(f"[bootstrap] model add 401 (auth issue), continuing")
    else:
        log(f"[bootstrap] model add returned {code}: {resp}")

    # Final status
    log("")
    log("=" * 50)
    log("  Hermes bootstrap complete!")
    log("")
    log(f"  Chat UI:   {webui_url}")
    log(f"  Login:     {admin_email} / {admin_password}")
    log(f"  Model:     {model_id}")
    log("=" * 50)
    log_fh.close()


if __name__ == "__main__":
    main()
