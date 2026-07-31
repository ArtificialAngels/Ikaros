# path_resolve.py
# Ikaros path acquisition + self-heal, run on every 9100 panel launch.
#
# What it does (the "acquire everything once" step):
#   1. Self-locate Ikaros root via this script's own path (drive-letter independent).
#   2. Map the known volume GUID -> current drive letter (read-only, no admin).
#   3. If the launch location is invalid (folder moved), use Everything (es.exe,
#      the same engine hermes's everything MCP wraps) to search for the real Ikaros.
#   4. Pin the E: letter and create the C:\Ikaros volume-GUID mount (needs admin;
#      best-effort, warns if not elevated).
#   5. If the hermes venv's editable paths are stale (moved), rebuild it.
#   6. Write data/config/ikaros_root.json and (if relocated) a flag for the .bat
#      to re-exec the panel from the correct root.
#
# This script NEVER blocks the panel from starting: every step is wrapped and
# failures are logged, not raised.

import os
import re
import sys
import json
import subprocess
import datetime

IKAROS_VOLUME_GUID = "\\\\?\\Volume{3f18f903-0000-0000-0000-100000000000}\\"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IKAROS_ROOT_DEFAULT = os.path.dirname(SCRIPT_DIR)  # bin -> Ikaros root
LOG_PATH = os.path.join(IKAROS_ROOT_DEFAULT, "tmp", "path_resolve.log")
RELOC_FLAG = os.path.join(os.environ.get("TEMP", "C:\\tmp"), "ikaros_relocate_root.txt")


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (ts, msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def self_locate():
    return IKAROS_ROOT_DEFAULT


def validate_root(root):
    if not root:
        return False
    if not os.path.isfile(os.path.join(root, "core", "hermes", "venv", "pyvenv.cfg")):
        return False
    if not os.path.isfile(os.path.join(root, "bin", "ikaros-control.bat")):
        return False
    return True


def guid_to_letter():
    try:
        out = subprocess.run("mountvol", shell=True, capture_output=True,
                             encoding="utf-8", errors="ignore", timeout=30).stdout or ""
    except Exception as e:
        log("mountvol read failed: %s" % e)
        return None
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == IKAROS_VOLUME_GUID.lower():
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j].strip()
                if not nxt:
                    continue
                if "无装入点" in nxt or "NO MOUNT" in nxt.upper():
                    return None
                m = re.search(r"([A-Za-z]):\\", nxt)
                if m:
                    return m.group(1) + ":"
    return None


def venv_matches_root(root):
    finder = os.path.join(root, "core", "hermes", "venv", "Lib", "site-packages",
                          "__editable___hermes_agent_0_19_0_finder.py")
    if not os.path.isfile(finder):
        return False
    try:
        with open(finder, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        return False
    m = re.search(r"'hermes_cli':\s*r?'([^']+)'", content)
    if not m:
        return False
    val = m.group(1).replace("\\\\", "\\")
    norm = root.replace("/", "\\")
    return val.lower().startswith(norm.lower())


def rebuild_venv(root):
    bat = os.path.join(root, "bin", "rebuild-hermes-venv.bat")
    if not os.path.isfile(bat):
        log("rebuild script missing; cannot rebuild venv")
        return False
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_SANDBOX"] = "0"
    env["UV_CACHE_DIR"] = os.path.join(os.environ.get("TEMP", "C:\\tmp"), "uvcache")
    log("rebuilding hermes venv (root=%s) ..." % root)
    try:
        r = subprocess.run(bat, shell=True, cwd=root, env=env,
                           capture_output=True, encoding="utf-8", errors="ignore", timeout=600)
        if r.returncode == 0:
            log("venv rebuild OK")
            return True
        log("venv rebuild failed rc=%s: %s" % (r.returncode, (r.stderr or "")[-500:]))
        return False
    except Exception as e:
        log("venv rebuild exception: %s" % e)
        return False


def locate_via_everything(root):
    candidates = []
    if root:
        candidates.append(os.path.join(root, "runtime", "everything", "es.exe"))
    candidates.append(r"C:\Ikaros\Ikaros\runtime\everything\es.exe")
    candidates.append(r"C:\Ikaros\runtime\everything\es.exe")
    candidates.append(r"E:\Ikaros\runtime\everything\es.exe")
    es = None
    for c in candidates:
        if os.path.isfile(c):
            es = c
            break
    if not es:
        log("es.exe not found in candidates; skipping Everything search")
        return None
    try:
        r = subprocess.run([es, "ikaros-control.bat"], capture_output=True,
                           encoding="utf-8", errors="ignore", shell=False, timeout=30)
        out = (r.stdout or "") + (r.stderr or "")
        if "IPC not found" in out or out.strip().startswith("Error"):
            log("Everything IPC not available (%s); skipping" % out.strip()[:80])
            return None
        for line in out.splitlines():
            line = line.strip()
            if "ikaros-control.bat" in line.lower() and os.path.isfile(line):
                cand = os.path.dirname(os.path.dirname(line))  # parent of bin/
                if validate_root(cand):
                    log("Everything found Ikaros at %s" % cand)
                    return cand
    except Exception as e:
        log("Everything search exception: %s" % e)
    return None


def _is_admin():
    """当前进程是否以管理员权限运行（Windows）。非 Windows 视为 False。"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def pin_and_mount(letter):
    results = {}
    # 钉盘符 / 挂载卷别名都需要管理员权限。非管理员直接跳过：
    # 既不产生 "needs admin" 噪音，也不会在 C:\ 留下空的 C:\Ikaros 目录。
    # （E: 当前已是正确的盘符，不钉也不影响运行；C:\Ikaros 只是给写死老路径的
    #   组件用的可选别名，缺失时无影响。）
    if not _is_admin():
        log("not running as admin; skipping pin/mount (E: already correct, no C:\\Ikaros alias)")
        return results
    drive = (letter or "E") + ":"
    try:
        r = subprocess.run("mountvol %s %s" % (drive, IKAROS_VOLUME_GUID), shell=True,
                           capture_output=True, encoding="utf-8", errors="ignore", timeout=30)
        results["pin_E"] = (r.returncode == 0)
        if r.returncode != 0:
            log("pin %s failed (%s)" % (drive, (r.stderr or "").strip()[:80]))
    except Exception as e:
        results["pin_E"] = False
        log("pin %s exception %s" % (drive, e))
    try:
        os.makedirs(r"C:\Ikaros", exist_ok=True)
        r = subprocess.run("mountvol C:\\Ikaros %s" % IKAROS_VOLUME_GUID, shell=True,
                           capture_output=True, encoding="utf-8", errors="ignore", timeout=30)
        results["mount_C"] = (r.returncode == 0)
        if r.returncode != 0:
            log("mount C:\\Ikaros failed (%s)" % (r.stderr or "").strip()[:80])
    except Exception as e:
        results["mount_C"] = False
        log("mount C exception %s" % e)
    return results


def write_state(root, letter, resolved_via, venv_rebuilt, pin):
    try:
        d = os.path.join(root, "data", "config")
        os.makedirs(d, exist_ok=True)
        state = {
            "ikaros_root": root,
            "volume_guid": IKAROS_VOLUME_GUID,
            "current_letter": letter,
            "resolved_via": resolved_via,
            "venv_rebuilt": venv_rebuilt,
            "pin": pin,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        with open(os.path.join(d, "ikaros_root.json"), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log("write state failed: %s" % e)


def main():
    log("=== ikaros path resolve start ===")
    launch_root = self_locate()
    log("launched from: %s" % launch_root)
    letter = guid_to_letter()
    log("volume GUID -> current letter: %s" % letter)

    root = launch_root
    resolved_via = "self"

    if not validate_root(root):
        log("launch root INVALID; trying Everything (es.exe) search")
        found = locate_via_everything(root)
        if found:
            root = found
            resolved_via = "everything"
        elif letter:
            cand = letter + ":\\Ikaros"
            if validate_root(cand):
                root = cand
                resolved_via = "volume_guid"
            else:
                log("candidate %s also invalid; giving up relocation" % cand)
        else:
            log("no letter / no everything result; cannot relocate")

    log("effective Ikaros root: %s (via %s)" % (root, resolved_via))

    pin = pin_and_mount(letter)

    venv_rebuilt = False
    if validate_root(root):
        if venv_matches_root(root):
            log("venv paths match current root; no rebuild needed")
        else:
            log("venv paths STALE (moved); rebuilding...")
            venv_rebuilt = rebuild_venv(root)
    else:
        log("root invalid; skipping venv check")

    write_state(root, letter, resolved_via, venv_rebuilt, pin)

    if resolved_via in ("everything", "volume_guid") and \
       os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(launch_root)):
        try:
            os.makedirs(os.path.dirname(RELOC_FLAG), exist_ok=True)
            with open(RELOC_FLAG, "w", encoding="utf-8") as f:
                f.write(root)
            log("relocation flag set -> %s" % root)
        except Exception as e:
            log("relocation flag write failed: %s" % e)

    log("=== done ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("FATAL: %s" % e)
    sys.exit(0)
