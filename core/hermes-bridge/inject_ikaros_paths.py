"""Standalone IKAROS_* path injection — studio 式替代 mcp_tool.py 补丁.

原 hermes ``tools/mcp_tool.py`` 里的 ``_inject_ikaros_root_paths()`` 在每次
加载 MCP 配置时从 HERMES_HOME 布局推导 ``IKAROS_*`` 并 **覆盖** 写入
``os.environ``，目的是「换盘符后 ``${IKAROS_*}`` 占位符仍能解析，不被 .env 旧盘符遮蔽」。

studio 式 0 侵入做法：把这段逻辑搬出 hermes，放进 Ikaros 自有层
（启动器 / bridge 拉起子进程前调用），hermes 工作树保持纯净。

与原始补丁的差异：
- 纯标准库实现，**不 import hermes_cli**（Ikaros 层可独立运行）。
- 用 ``os.environ.setdefault`` 而非覆盖：启动器 ``build_env()`` 注入的
  权威值不被这里覆盖；仅当启动器未注入时才按标准布局兜底推导。
- 推导出的路径与原始补丁完全一致（IKAROS_ROOT / RUNTIME / NODE /
  PYTHON / MEMORY / HERMES_HOME）。

安全无操作条件：HERMES_HOME 不指向 ``<root>/data/hermes-agent``，或
``<root>/runtime/node/node.exe`` 不存在 —— 这种情况下直接返回，不改任何环境。
"""

import os
from pathlib import Path


def inject_ikaros_root_paths() -> None:
    """Derive IKAROS_* from the project layout when the dashboard
    build_env() path did not inject them. Mirrors the removed hermes
    mcp_tool patch. Safe no-op unless HERMES_HOME points at the standard
    ``<root>/data/hermes-agent`` layout with a populated ``<root>/runtime``.
    """
    home = os.environ.get("HERMES_HOME")
    if not home:
        root = os.environ.get("IKAROS_ROOT")
        if root:
            home = str(Path(root) / "data" / "hermes-agent")
    if not home:
        return
    home = Path(home)
    if home.name != "hermes-agent" or home.parent.name != "data":
        return  # 非 Ikaros 标准结构，跳过
    root = home.parent.parent
    runtime = root / "runtime"
    if not (runtime / "node" / "node.exe").exists():
        return  # 结构不完整（runtime 缺失），跳过
    vals = {
        "IKAROS_ROOT": str(root),
        "IKAROS_RUNTIME": str(runtime),
        "IKAROS_NODE": str(runtime / "node" / "node.exe"),
        "IKAROS_PYTHON": str(runtime / "portable-python" / "python.exe"),
        "IKAROS_MEMORY": str(root / "core" / "memory_v5"),
        "IKAROS_HERMES_HOME": str(home),
    }
    for k, v in vals.items():
        os.environ.setdefault(k, v)


if __name__ == "__main__":
    inject_ikaros_root_paths()
    for k in (
        "IKAROS_ROOT",
        "IKAROS_RUNTIME",
        "IKAROS_NODE",
        "IKAROS_PYTHON",
        "IKAROS_MEMORY",
        "IKAROS_HERMES_HOME",
    ):
        print(f"{k}={os.environ.get(k, '<unset>')}")
