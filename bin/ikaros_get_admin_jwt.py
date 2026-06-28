"""Get admin JWT by logging in with vault credentials.

设计:
- vault 里有 ArtificialAngel / AngelIkaros (哥哥给的专属账号)
- 不重置密码! 用 vault credentials 直接 login
- login API 返回 token, 存到 data/webui/.admin-jwt.txt
- 文件 gitignored

用法:
    python bin/icarus_get_admin_jwt.py
"""
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

WEBUI_ROOT = Path("E:/Hermes Agent")
JWT_OUT = WEBUI_ROOT / "data" / "webui" / ".admin-jwt.txt"
VAULT = WEBUI_ROOT / "data" / "icarus-credentials.vault"
IDENTITY_KEY = Path.home() / ".icarus" / "identity.key"


def read_vault():
    """Decrypt Ikaros vault -> {username, password}."""
    if not IDENTITY_KEY.exists():
        print(f"ERROR: identity key not found at {IDENTITY_KEY}", file=sys.stderr)
        sys.exit(1)
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    import ast, base64

    identity = IDENTITY_KEY.read_bytes()
    text = VAULT.read_text(encoding="utf-8")
    data_line = [l for l in text.split("\n") if l.startswith("{")][0]
    vault = ast.literal_eval(data_line)
    salt = base64.b64decode(vault["salt_b64"])
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                     salt=salt, iterations=480_000)
    key = base64.urlsafe_b64encode(kdf.derive(identity))
    plaintext = Fernet(key).decrypt(vault["ciphertext"].encode()).decode()
    return ast.literal_eval(plaintext)


def login(username, password):
    """POST /api/auth/login, return token."""
    data = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8649/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read())
        return body.get("token")


def test_token(token, url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read()[:200]
            return f"HTTP {r.status}: {body[:150]}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read()[:200]}"


if __name__ == "__main__":
    print("[1] Decrypting vault...")
    creds = read_vault()
    print(f"    username: {creds['username']!r}")
    print(f"    password length: {len(creds['password'])}")

    print("[2] Logging in via /api/auth/login...")
    token = login(creds["username"], creds["password"])
    if not token:
        print("ERROR: no token returned", file=sys.stderr)
        sys.exit(1)
    print(f"    Got token: {token[:30]}... ({len(token)} chars)")

    print(f"[3] Writing to {JWT_OUT}...")
    JWT_OUT.write_text(token, encoding="utf-8")

    print("[4] Tests:")
    print(f"  proxy :8648 → {test_token(token, 'http://127.0.0.1:8648/api/hermes/kanban')}")
    print(f"  webui :8649 → {test_token(token, 'http://127.0.0.1:8649/api/hermes/kanban')}")

    print("\n=== Done ===")
    print(f"  JWT at: {JWT_OUT}")
    print(f"  loop-workflow.py can now use this token automatically")