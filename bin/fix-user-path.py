"""fix-user-path.py — repair Windows user PATH polluted with MSYS-style entries.

Converts /x/... entries to X:\\... form, dedupes, and guarantees bun/omp bin dirs are present.
Idempotent: safe to re-run.
"""
import re
import winreg


def msys_to_win(p: str) -> str:
    p = p.strip()
    m = re.match(r"^/([a-zA-Z])/(.*)$", p)
    if m:
        return m.group(1).upper() + ":\\" + m.group(2).replace("/", "\\")
    return p


key = winreg.OpenKey(
    winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE
)
try:
    cur = winreg.QueryValueEx(key, "Path")[0]
except FileNotFoundError:
    cur = ""
print("BEFORE entries:", len([x for x in cur.split(";") if x]))

parts = [msys_to_win(x) for x in cur.split(";")]
seen, out = set(), []
for p in parts:
    if not p:
        continue
    lk = p.lower()
    if lk in seen:
        continue
    seen.add(lk)
    out.append(p)

for extra in [
    r"E:\Ikaros\runtime\node\node_modules\bun\bin",
    r"C:\Users\PZS0X\.bun\bin",
]:
    if extra.lower() not in seen:
        out.append(extra)
        seen.add(extra.lower())

new_path = ";".join(out)
winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
winreg.CloseKey(key)
print("AFTER entries:", len(out))
print("has bun bin:", r"E:\Ikaros\runtime\node\node_modules\bun\bin" in new_path)
print("has omp bin:", r"C:\Users\PZS0X\.bun\bin" in new_path)
