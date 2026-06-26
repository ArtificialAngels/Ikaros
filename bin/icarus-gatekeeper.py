"""Icarus Gatekeeper — verify 哥哥身份 via password prompt.

设计 (per axiom Rule 7):
- 默认信任 PZS0X 物理登录本机 = 哥哥
- 显式调用 verify_gege() 才弹窗 (Linux sudo 风格)
- 密码不入任何文件, 不入 commit, 不入 vault
- 密码用 == 比较, 用完即丢

用法:
    from icarus_gatekeeper import verify_gege, is_trusted_session

    if not is_trusted_session():
        if not verify_gege(reason="远程 SSH 来源"):
            raise PermissionError("哥哥认证失败")
"""
from __future__ import annotations
import getpass
import os
import sys
from pathlib import Path

# ─── Trust heuristic (per axiom Rule 7) ───
def is_trusted_session() -> bool:
    """当前 session 是否被信任 (PZS0X 物理登录本机).

    Heuristic:
      - Windows + interactive session + username=PZS0X → 信任
      - 其他情况 (remote, other user, headless, webui MCP) → 不信任
    """
    try:
        if sys.platform != "win32":
            return False  # 当前假设 Windows 本机
        user = os.environ.get("USERNAME") or os.environ.get("USER")
        if user != "PZS0X":
            return False
        # 看 SESSIONNAME — interactive = Console / RDP-Tcp...
        session = os.environ.get("SESSIONNAME", "")
        # console = 物理登录; RDP-Tcp = 远程登录 (需 gate)
        if session and session.upper() == "CONSOLE":
            return True
        # 默认本地 session 也信任 (Python 在 IDE / 终端启动)
        if session == "":
            return True
        return False
    except Exception:
        return False


# ─── Password prompt (Linux sudo 风格) ───
def _prompt_password(prompt: str = "请输入哥哥密码以继续: ") -> str:
    """Echo-off password input.

    用 winpty / msvcrt fallback:
      - getpass() 优先 (Unix 风格)
      - Windows 上 getpass fallback 到 print (echo off in newer Python)
      - 加 msvcrt fallback if needed
    """
    try:
        # getpass works on Windows console + IDLE fallback
        return getpass.getpass(prompt)
    except Exception:
        # IDLE / no TTY — fallback to msvcrt for true echo-off
        try:
            import msvcrt
            sys.stdout.write(prompt)
            sys.stdout.flush()
            chars = []
            while True:
                ch = msvcrt.getch()
                if ch in (b"\r", b"\n"):
                    sys.stdout.write("\n")
                    return "".join(chars)
                elif ch == b"\x08":  # backspace
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif ch == b"\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    try:
                        chars.append(ch.decode("utf-8"))
                        sys.stdout.write("*")
                        sys.stdout.flush()
                    except UnicodeDecodeError:
                        pass
        except Exception:
            # last resort: normal input (echo on — NOT secure but at least works)
            return input(prompt)


# ─── PyQt6 GUI password window (Linux sudo 视觉风格) ───
def _gui_password_window(prompt: str) -> str | None:
    """弹 PyQt6 密码窗, 跟 Linux sudo 一致 (黑底 + 输入框不可视).
    Returns password or None if cancelled.
    Returns "FALLBACK_TERMINAL" string (special) if no display available,
    so caller falls back to terminal.
    """
    try:
        # Check if we have a display (DISPLAY env on linux, win32 console auto)
        if sys.platform == "linux" and not os.environ.get("DISPLAY"):
            return "FALLBACK_TERMINAL"
        # On Windows, check session type — services / headless have no display
        if sys.platform == "win32":
            session = os.environ.get("SESSIONNAME", "")
            if session == "Services":
                return "FALLBACK_TERMINAL"
        from PyQt6.QtWidgets import (QApplication, QInputDialog, QLineEdit)
        # 确保 QApplication 存在 (桌宠启动时已有, 独立调用要新建)
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
            standalone = True
        else:
            standalone = False

        text, ok = QInputDialog.getText(
            None,
            "伊卡洛斯 · 身份确认",
            prompt,
            QLineEdit.EchoMode.Password,
        )
        if standalone:
            app.quit()
        return text if ok else None
    except ImportError:
        return "FALLBACK_TERMINAL"  # PyQt6 not available, fallback to terminal
    except Exception:
        return "FALLBACK_TERMINAL"


# ─── Main entry: verify 哥哥 ───
def verify_gege(reason: str = "") -> bool:
    """弹窗验证是不是哥哥. 返回 True=确认, False=失败/取消.

    密码不入任何文件, 不入 axiom, 不入 vault, 不入 commit.
    密码比较用 ==, 用完即丢 (局部变量, 函数返回后失效).
    """
    prompt = f"【伊卡洛斯身份确认】{reason}\n请输入哥哥密码以继续 (输入不可视): "

    # 优先 GUI (Linux sudo 视觉风格)
    pw = _gui_password_window(reason or "请输入哥哥密码以继续")
    if pw == "FALLBACK_TERMINAL" or pw is None and os.environ.get("ICARUS_GATE_FORCE_TERMINAL"):
        # fallback to terminal echo-off
        pw = _prompt_password(prompt)

    # None = cancelled, "" = empty
    if pw is None or pw == "":
        return False

    # 比较 — 密码不入 vault, 也不入任何常量.
    # 优先从 environment variable 拿 (哥哥可以一次性 export, 不入文件)
    # 备选: 让哥哥在 prompt 后面再敲一次确认密码 (双盲确认, 防自己骗自己)
    expected = os.environ.get("ICARUS_GATE_EXPECTED")
    if not expected:
        # 没设 env, 拒绝 (无法验证)
        result = False
    else:
        # 用 hmac.compare_digest 防 timing attack
        import hmac
        result = hmac.compare_digest(pw.encode("utf-8"), expected.encode("utf-8"))
    # 显式擦除局部变量
    pw = None

    return result


# ─── CLI 入口 (哥哥手动测) ───
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "trust":
        t = is_trusted_session()
        print(f"trusted: {t}")
        print(f"  user: {os.environ.get('USERNAME')}")
        print(f"  session: {os.environ.get('SESSIONNAME')!r}")
        sys.exit(0 if t else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "verify":
        reason = sys.argv[2] if len(sys.argv) > 2 else "manual test"
        ok = verify_gege(reason)
        print(f"verified: {ok}")
        sys.exit(0 if ok else 1)
    else:
        print(__doc__)
        sys.exit(0)