"""herdr 深度 socket 客户端（Windows 命名管道 / Unix 域套接字）。

协议（来自 herdr socket-api 文档，协议 17）：
  - 一行一个 JSON 对象，UTF-8，以 \\n 分隔。
  - 请求:  {"id": "<str>", "method": "<dot.notation>", "params": {<...>}}
  - 响应:  {"id": "<str>", "result": <...>}  |  {"id": "<str>", "error": {"code","message"}}
  - 普通 RPC：服务器在响应后关闭连接 —— 本客户端「每请求开一条独立连接」。
  - 事件订阅 events.subscribe：首响应确认（带同一 id），之后同一连接持续推送事件行。

Windows 上 herdr 用命名管道（interprocess GenericNamespaced -> 把 socket 文件
完整路径拼到 \\\\.\\pipe\\ 之后）。本客户端优先用 win32file，缺失时退回 ctypes。
传输层可注入（测试时用普通 TCP socket 即可），见 HerdrClient(transport_factory=...)。
"""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any, Callable, Optional

__all__ = [
    "HerdrClient",
    "HerdrError",
    "HerdrProtocolError",
    "HerdrTimeout",
    "StreamTransport",
    "resolve_socket_path",
]

_WIN32 = os.name == "nt"


class HerdrError(Exception):
    """herdr 客户端基类异常。"""


class HerdrProtocolError(HerdrError):
    """协议错误或 server 返回 error。"""


class HerdrTimeout(HerdrError):
    """等待响应超时。"""


# --------------------------------------------------------------------------- #
# 路径解析
# --------------------------------------------------------------------------- #
def _default_config_dir() -> str:
    if _WIN32:
        base = os.environ.get("APPDATA") or (os.path.expanduser("~") + "\\AppData\\Roaming")
        return os.path.join(base, "herdr")
    return os.path.expanduser("~/.config/herdr")


def resolve_socket_path(explicit: Optional[str] = None) -> str:
    """解析 herdr socket 路径，顺序：显式 > HERDR_SOCKET_PATH > HERDR_SESSION > 默认。"""
    if explicit:
        return explicit
    if os.environ.get("HERDR_SOCKET_PATH"):
        return os.environ["HERDR_SOCKET_PATH"]
    base = os.path.join(_default_config_dir(), "herdr.sock")
    sess = os.environ.get("HERDR_SESSION")
    if sess:
        return os.path.join(_default_config_dir(), "sessions", sess, "herdr.sock")
    return base


def _ns_pipe_name(path: str) -> str:
    """把 herdr socket 文件路径转换为 Windows 命名管道名。

    interprocess 的 GenericNamespaced 在 Windows 上把路径原样拼到 \\\\.\\pipe\\ 后，
    例如 C:\\Users\\x\\AppData\\Roaming\\herdr\\herdr.sock
      -> \\\\.\\pipe\\C:\\Users\\x\\AppData\\Roaming\\herdr\\herdr.sock
    """
    full = path.replace("/", "\\")
    return r"\\.\pipe\\" + full


# --------------------------------------------------------------------------- #
# 底层连接（write / read / close）
# --------------------------------------------------------------------------- #
class _WinPipeConn:
    """Windows 命名管道连接，基于 win32file。"""

    def __init__(self, name: str):
        import win32file  # type: ignore

        self._h = win32file.CreateFile(
            name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )

    def write(self, data: bytes) -> None:
        import win32file  # type: ignore

        win32file.WriteFile(self._h, data)

    def read(self, n: int) -> bytes:
        import win32file  # type: ignore

        try:
            _code, data = win32file.ReadFile(self._h, n)
        except Exception:
            return b""
        return data or b""

    def close(self) -> None:
        try:
            self._h.Close()
        except Exception:
            pass


class _WinPipeConnCtypes:
    """Windows 命名管道连接，基于 ctypes（win32file 缺失时的兜底）。"""

    def __init__(self, name: str):
        k = ctypes.windll.kernel32  # type: ignore
        k.CreateFileW.restype = ctypes.c_void_p  # type: ignore
        k.CreateFileW.argtypes = [  # type: ignore
            ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
            ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ]
        k.ReadFile.restype = ctypes.c_int  # type: ignore
        k.ReadFile.argtypes = [  # type: ignore
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
        ]
        k.WriteFile.restype = ctypes.c_int  # type: ignore
        k.WriteFile.argtypes = [  # type: ignore
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p,
        ]
        k.CloseHandle.argtypes = [ctypes.c_void_p]  # type: ignore
        h = k.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)
        if h == (1 << 64) - 1:
            raise HerdrError("无法打开命名管道: %s" % name)
        self._h = h
        self._k = k

    def write(self, data: bytes) -> None:
        got = ctypes.c_ulong(0)  # type: ignore
        self._k.WriteFile(self._h, data, len(data), ctypes.byref(got), None)

    def read(self, n: int) -> bytes:
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_ulong(0)  # type: ignore
        ok = self._k.ReadFile(self._h, buf, n, ctypes.byref(got), None)
        if not ok:
            return b""
        return buf.raw[: got.value]

    def close(self) -> None:
        try:
            self._k.CloseHandle(self._h)
        except Exception:
            pass


class _UnixConn:
    """Unix 域套接字连接。"""

    def __init__(self, sock: "socket.socket"):
        self._s = sock

    def write(self, data: bytes) -> None:
        self._s.sendall(data)

    def read(self, n: int) -> bytes:
        try:
            return self._s.recv(n)
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass


def _open_pipe(path: str):
    """打开本地 socket / Windows 命名管道，返回支持 write/read/close 的连接对象。"""
    if _WIN32:
        name = _ns_pipe_name(path)
        try:
            return _WinPipeConn(name)
        except ImportError:
            return _WinPipeConnCtypes(name)
    s = socket.socket(socket.AF_UNIX)
    s.connect(path)
    return _UnixConn(s)


# --------------------------------------------------------------------------- #
# 传输层：在连接之上提供 send_line / read_line（带缓冲）
# --------------------------------------------------------------------------- #
class StreamTransport:
    """把底层连接包装成 send_line / read_line 接口（newline 分隔）。"""

    def __init__(self, raw):
        self._r = raw
        self._buf = b""

    def send_line(self, text: str) -> None:
        self._r.write((text + "\n").encode("utf-8"))

    def read_line(self) -> Optional[str]:
        while b"\n" not in self._buf:
            chunk = self._r.read(4096)
            if not chunk:
                if self._buf:
                    line = self._buf
                    self._buf = b""
                    return line.decode("utf-8", "replace")
                return None
            self._buf += chunk
        i = self._buf.index(b"\n") + 1
        line = self._buf[:i]
        self._buf = self._buf[i:]
        return line.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._r.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 客户端
# --------------------------------------------------------------------------- #
EventHandler = Callable[[dict], None]


class HerdrClient:
    def __init__(
        self,
        socket_path: Optional[str] = None,
        timeout: float = 30.0,
        transport_factory: Optional[Callable[[str], StreamTransport]] = None,
    ):
        self.socket_path = socket_path or resolve_socket_path()
        self.timeout = timeout
        self._transport_factory = transport_factory or (lambda p: StreamTransport(_open_pipe(p)))
        self._counter = 0
        self._event_handlers: list[EventHandler] = []
        self._sub: Optional[StreamTransport] = None
        self._listener: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- 连接管理（普通 RPC 走每请求独立连接，这里 connect 仅作可选声明）-- #
    def connect(self) -> None:
        """普通 RPC 不需要持久连接；connect 为可选调用，保持 API 习惯一致。"""
        return

    def close(self) -> None:
        """关闭事件订阅长连接（若存在）。"""
        self._stop.set()
        if self._sub is not None:
            try:
                self._sub.close()
            except Exception:
                pass
            self._sub = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- 请求/响应（每请求一条独立连接）------------------------------------ #
    def request(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
        t = self._transport_factory(self.socket_path)
        try:
            self._counter += 1
            rid = "ik_%d" % self._counter
            t.send_line(json.dumps({"id": rid, "method": method, "params": params or {}}))
            line = t.read_line()
        finally:
            try:
                t.close()
            except Exception:
                pass
        if line is None:
            raise HerdrProtocolError("未收到响应: %s" % method)
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            raise HerdrProtocolError("响应不是合法 JSON: %s" % e)
        if "error" in msg:
            err = msg["error"]
            raise HerdrProtocolError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        return msg.get("result")

    def ping(self) -> dict:
        return self.request("ping")

    def protocol(self) -> Any:
        return self.ping().get("protocol")

    # -- 事件订阅（独立长连接）--------------------------------------------- #
    def subscribe(self, subscriptions: list[dict], handler: Optional[EventHandler] = None) -> Any:
        """订阅类型化事件。handler(event_dict) 在后台线程被调用。返回订阅确认结果。"""
        if handler is not None:
            self._event_handlers.append(handler)
        t = self._transport_factory(self.socket_path)
        self._sub = t
        self._counter += 1
        rid = "sub_%d" % self._counter
        t.send_line(json.dumps({"id": rid, "method": "events.subscribe", "params": {"subscriptions": subscriptions}}))
        line = t.read_line()
        if line is None:
            raise HerdrProtocolError("events.subscribe 未收到确认")
        ack = json.loads(line)
        if "error" in ack:
            err = ack["error"]
            raise HerdrProtocolError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        self._stop.clear()
        self._listener = threading.Thread(target=self._read_loop, args=(t,), daemon=True)
        self._listener.start()
        return ack.get("result")

    def on_event(self, handler: EventHandler) -> None:
        self._event_handlers.append(handler)

    # -- 高层便捷方法（对应 herdr socket-api 方法名）----------------------- #
    def session_snapshot(self) -> Any:
        return self.request("session.snapshot")

    def workspace_create(self, cwd: Optional[str] = None, label: Optional[str] = None) -> Any:
        params: dict = {}
        if cwd:
            params["cwd"] = cwd
        if label:
            params["label"] = label
        return self.request("workspace.create", params)

    def workspace_list(self) -> Any:
        return self.request("workspace.list")

    def workspace_close(self, workspace_id: str) -> Any:
        return self.request("workspace.close", {"workspace_id": workspace_id})

    def tab_create(self, label: Optional[str] = None, workspace_id: Optional[str] = None) -> Any:
        params: dict = {}
        if label:
            params["label"] = label
        if workspace_id:
            params["workspace_id"] = workspace_id
        return self.request("tab.create", params)

    def pane_split(self, pane_id: str, direction: str = "right", ratio: Optional[float] = None, cwd: Optional[str] = None, env: Optional[dict] = None) -> Any:
        params: dict = {"pane_id": pane_id, "direction": direction}
        if ratio is not None:
            params["ratio"] = ratio
        if cwd:
            params["cwd"] = cwd
        if env:
            params["env"] = env
        return self.request("pane.split", params)

    def pane_run(self, pane_id: str, command: str) -> Any:
        """在 pane 中执行命令：经 pane.send_text 发送 <command>+回车（Windows 用 \\r\\n）。"""
        return self.request("pane.send_text", {"pane_id": pane_id, "text": command + "\r\n"})

    def pane_send_text(self, pane_id: str, text: str) -> Any:
        return self.request("pane.send_text", {"pane_id": pane_id, "text": text})

    def pane_send_keys(self, pane_id: str, keys) -> Any:
        """发送按键。keys 可为字符串（如 "enter"）或字符串列表（herdr 要求数组）。"""
        if isinstance(keys, str):
            keys = [keys]
        return self.request("pane.send_keys", {"pane_id": pane_id, "keys": keys})

    def pane_read(self, pane_id: str, source: str = "recent_unwrapped", lines: int = 120) -> Any:
        result = self.request("pane.read", {"pane_id": pane_id, "source": source, "lines": lines})
        # 真实响应结构: {"type":"pane_read","read":{"text":..., ...}}；返回内层 read 便于取 .text
        if isinstance(result, dict) and "read" in result:
            return result["read"]
        return result

    def pane_list(self) -> Any:
        return self.request("pane.list")

    def pane_get(self, pane_id: str) -> Any:
        return self.request("pane.get", {"pane_id": pane_id})

    def agent_list(self) -> Any:
        return self.request("agent.list")

    def agent_get(self, agent_id: str) -> Any:
        return self.request("agent.get", {"agent": agent_id})

    def agent_start(self, name: str, kind: str, pane_id: str, timeout_s: int = 30) -> Any:
        return self.request("agent.start", {"name": name, "kind": kind, "pane_id": pane_id}, timeout=timeout_s + 5)

    def agent_prompt(self, target: str, text: str, wait: bool = True, until: Any = "idle", timeout_ms: int = 120000) -> Any:
        # herdr agent.prompt 用 target（pane_id）寻址，而非 name
        params: dict = {"target": target, "text": text}
        if wait:
            # herdr 要求 wait.until 为状态数组（如 ["idle"]），非单个字符串。
            # until 可为 str（自动包成单元素数组）或 list/tuple（多稳定态：["blocked","done"]）。
            if isinstance(until, (list, tuple)):
                until_list: list = list(until)
            else:
                until_list = [until]
            params["wait"] = {"until": until_list, "timeout_ms": timeout_ms}
        return self.request("agent.prompt", params, timeout=timeout_ms / 1000.0 + 10)

    def agent_wait(self, target: str, until: str = "done", timeout_ms: int = 120000) -> Any:
        # herdr agent.wait 用 target（pane_id）而非 name，且 until 为数组
        return self.request("agent.wait", {"target": target, "until": [until], "timeout_ms": timeout_ms}, timeout=timeout_ms / 1000.0 + 10)

    # -- 内部读取循环（仅事件订阅长连接使用）------------------------------- #
    def _read_loop(self, t: StreamTransport) -> None:
        while not self._stop.is_set():
            try:
                line = t.read_line()
            except (OSError, ValueError):
                break
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            if rid and not str(rid).startswith("sub_"):
                # 订阅连接上不应有普通 RPC 响应；忽略
                continue
            for h in list(self._event_handlers):
                try:
                    h(msg)
                except Exception:
                    pass


if __name__ == "__main__":
    print("herdr client module loaded; 直接对运行中的 herdr 测试可运行 core/herdr/_selftest.py。")
