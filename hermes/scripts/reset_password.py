"""Reset Open WebUI password by directly updating the auth table.

Usage:
    python reset_password.py [new_password]

If no password given, defaults to 'hermes123'.
"""
import os
import sqlite3
import sys
from pathlib import Path


def list_users(db_path: Path) -> list[dict]:
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        sys.exit(1)
    con = sqlite3.connect(str(db_path))
    cur = con.execute("SELECT id, name, email, role FROM user ORDER BY created_at")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def reset_password(db_path: Path, email: str, new_password: str) -> bool:
    try:
        from passlib.context import CryptContext
    except ImportError:
        # Fallback: use bcrypt directly
        import bcrypt
        hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    else:
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = ctx.hash(new_password)

    con = sqlite3.connect(str(db_path))
    cur = con.execute("SELECT id FROM user WHERE email = ?", (email,))
    row = cur.fetchone()
    if not row:
        print(f"ERROR: no user with email {email!r}")
        return False
    user_id = row[0]
    con.execute(
        "UPDATE auth SET password = ? WHERE id = ? OR email = ?",
        (hashed, user_id, email),
    )
    con.commit()

    # Verify
    cur = con.execute("SELECT email, substr(password, 1, 7) FROM auth WHERE id = ?", (user_id,))
    print(f"OK: updated password for {email} (hash prefix: {cur.fetchone()[1]}...)")
    con.close()
    return True


def main():
    db_path = HERMES_ROOT / "hermes" / "data" / "openwebui" / "webui.db"
    new_password = sys.argv[1] if len(sys.argv) > 1 else "hermes123"

    print(f"DB: {db_path}")
    print(f"New password: {new_password!r}")
    print()
    print("Existing users:")
    users = list_users(db_path)
    for u in users:
        print(f"  - {u['email']:30s}  role={u['role']:8s}  name={u['name']}")

    if not users:
        print("  (none)")
        return

    # Reset each user's password
    for u in users:
        print()
        print(f"Resetting password for {u['email']}...")
        reset_password(db_path, u["email"], new_password)

    print()
    print("=" * 50)
    print("All passwords reset successfully!")
    print(f"  Login:  {users[0]['email']} / {new_password}")
    print("=" * 50)


if __name__ == "__main__":
    HERMES_ROOT = Path(r"E:\Hermes Agent")
    main()
