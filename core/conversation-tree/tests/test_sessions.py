"""core/conversation-tree/server.py 会话 API pytest 测试 (子任务 C2).

覆盖目标 (按 cli-tasks.md C2 要求):
- _load_sessions: 文件不存在 / 空列表 / 正常列表 / 损坏 JSON
- _save_sessions: 往返读写一致 / 创建父目录
- HTTP 端点 (经 ThreadingHTTPServer + urllib 真实链路):
  - GET  /api/sessions           列出会话
  - POST /api/sessions/create    新建会话 (自动建树 + 切为 active)
  - POST /api/sessions/switch    切换会话 (含: 不存在 404 / 拓扑缺失自动重建)
  - POST /api/sessions/delete    删除会话 (含: 不存在 404 / 拒删最后一个 /
                                 删 active 自动切下一个 / 拓扑文件随之清除)
  - POST /api/sessions/rename    重命名
  - POST /api/sessions/archive   归档切换 / 显式归档 (archived=true|false)
  - POST /api/sessions/unarchive 显式取消归档 (C5)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# 从 conftest 拿到: server 模块 / 工具函数
from conftest import server, http_get, http_post  # type: ignore  # noqa: E402


# ────────────────── _load_sessions 单元测试 ──────────────────

def test_load_sessions_no_file(tmp_data_dir, patched_store, reset_state):
    """文件不存在时返回空列表 (不抛异常)."""
    assert not server.SESSIONS_FILE.exists()
    assert server._load_sessions() == []


def test_load_sessions_empty_list(tmp_data_dir, patched_store, reset_state):
    """文件内容是空 JSON 数组时返回空列表."""
    server.SESSIONS_FILE.write_text("[]", encoding="utf-8")
    assert server._load_sessions() == []


def test_load_sessions_valid_list(tmp_data_dir, patched_store, reset_state):
    """正常 JSON 数组返回等价列表 (字段不丢)."""
    sessions = [
        {"id": "sess_a", "title": "A", "persist_key": "per_a",
         "created_at": 1.0, "updated_at": 2.0, "archived": False},
        {"id": "sess_b", "title": "B", "persist_key": "per_b",
         "created_at": 3.0, "updated_at": 4.0, "archived": True},
    ]
    server.SESSIONS_FILE.write_text(json.dumps(sessions), encoding="utf-8")
    got = server._load_sessions()
    assert got == sessions
    assert got[1]["archived"] is True


def test_load_sessions_corrupt_json(tmp_data_dir, patched_store, reset_state):
    """损坏 JSON: 静默返回空列表 (不抛异常)."""
    server.SESSIONS_FILE.write_text("{not json", encoding="utf-8")
    assert server._load_sessions() == []


def test_load_sessions_non_list_json(tmp_data_dir, patched_store, reset_state):
    """顶层不是 list (如 dict / int): 返回空列表 (兼容 isinstance 守卫)."""
    server.SESSIONS_FILE.write_text('{"oops": "not a list"}', encoding="utf-8")
    assert server._load_sessions() == []


# ────────────────── _save_sessions 单元测试 ──────────────────

def test_save_sessions_roundtrip(tmp_data_dir, patched_store, reset_state):
    """写 → 读: 字段一致, 中文不转义."""
    sessions = [
        {"id": "sess_中文", "title": "新会话", "persist_key": "per_1",
         "created_at": 1.5, "updated_at": 2.5, "archived": False},
    ]
    server._save_sessions(sessions)
    assert server.SESSIONS_FILE.exists()
    # 中文应原样落盘 (ensure_ascii=False)
    assert "新会话" in server.SESSIONS_FILE.read_text(encoding="utf-8")
    assert server._load_sessions() == sessions


def test_save_sessions_creates_parent_dir(tmp_data_dir, patched_store, reset_state):
    """父目录缺失时自动创建 (V5_DATA_DIR 已重定向到 tmp_path 子目录)."""
    deep_dir = tmp_data_dir / "deep" / "nested"
    # 手动把 SESSIONS_FILE 指向更深的子目录 (conftest 默认指向 tmp_path 根)
    server.SESSIONS_FILE = deep_dir / "sessions.json"
    try:
        server._save_sessions([{"id": "x", "title": "X", "persist_key": "p",
                                "created_at": 0, "updated_at": 0, "archived": False}])
        assert deep_dir.is_dir()
        assert server._load_sessions() == [{"id": "x", "title": "X",
                                            "persist_key": "p",
                                            "created_at": 0, "updated_at": 0,
                                            "archived": False}]
    finally:
        # 还原为 conftest 设的路径, 避免影响后续测试
        server.SESSIONS_FILE = tmp_data_dir / "sessions.json"


def test_save_sessions_empty_list(tmp_data_dir, patched_store, reset_state):
    """空列表也要能写盘 (避免 delete 把文件留成 stale 内容)."""
    server._save_sessions([])
    assert server.SESSIONS_FILE.exists()
    assert server._load_sessions() == []


# ────────────────── HTTP: GET /api/sessions ──────────────────

def test_get_sessions_empty_via_http(http_server):
    """首次启动 (无 sessions.json): ensure_tree 建 default 会话, GET 返回它."""
    status, data = http_get(http_server, "/api/sessions")
    assert status == 200
    assert "sessions" in data and "active_id" in data
    assert len(data["sessions"]) >= 1
    assert data["sessions"][0]["id"] == "default"
    assert data["active_id"] == "default"


# ────────────────── HTTP: POST /api/sessions/create ──────────────────

def test_create_session_via_http(http_server):
    """新建会话: 返回新会话 id, persist_key 唯一, 树已切为 active, sessions.json 已落盘."""
    status, data = http_post(http_server, "/api/sessions/create")
    assert status == 200
    assert "sessions" in data and "active_id" in data and "state" in data
    new_id = data["active_id"]
    assert new_id.startswith("sess_")
    # 新会话出现在列表里
    ids = [s["id"] for s in data["sessions"]]
    assert new_id in ids
    # persist_key 按代码约定: ui_conversation_tree_<id>
    new_sess = next(s for s in data["sessions"] if s["id"] == new_id)
    assert new_sess["persist_key"] == f"ui_conversation_tree_{new_id}"
    # sessions.json 已落盘, 内容一致
    on_disk = server._load_sessions()
    assert any(s["id"] == new_id for s in on_disk)
    # 拓扑文件已生成 (新建会话顺手 init 过一棵树)
    assert (tmp_path_from_server() / f"ui_conversation_tree_{new_id}.json").exists()


def test_create_multiple_sessions_via_http(http_server):
    """连续建 3 个会话: 每次 active 切换, 列表递增, ids 互不相同."""
    ids = []
    for _ in range(3):
        _, data = http_post(http_server, "/api/sessions/create")
        ids.append(data["active_id"])
    assert len(set(ids)) == 3
    _, data = http_get(http_server, "/api/sessions")
    assert len(data["sessions"]) >= 3


# ────────────────── HTTP: POST /api/sessions/switch ──────────────────

def test_switch_session_via_http(http_server):
    """新建第二个会话后切回第一个: active_id 跟随, state 反映新树."""
    # 先 ensure_tree 建好 default, 再新建一个 sess_X
    _, data0 = http_get(http_server, "/api/sessions")
    default_id = data0["sessions"][0]["id"]
    _, data1 = http_post(http_server, "/api/sessions/create")
    new_id = data1["active_id"]
    assert new_id != default_id

    # 切回 default
    status, data = http_post(http_server, "/api/sessions/switch",
                             {"id": default_id})
    assert status == 200
    assert data["active_id"] == default_id
    assert "state" in data


def test_switch_nonexistent_session_404(http_server):
    """切换不存在的会话 id: 返回 404, active 不变."""
    _, data0 = http_get(http_server, "/api/sessions")
    before_active = data0["active_id"]
    status, data = http_post(http_server, "/api/sessions/switch",
                             {"id": "sess_does_not_exist"})
    assert status == 404
    assert "error" in data
    # active 未被改动
    _, data1 = http_get(http_server, "/api/sessions")
    assert data1["active_id"] == before_active


def test_switch_restores_missing_topology(http_server, tmp_data_dir):
    """切到的会话拓扑 JSON 已被手动删: 自动建空树, 不崩溃."""
    # 建一个会话, 拿到它的 persist_key
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    per = next(s["persist_key"] for s in data["sessions"] if s["id"] == sid)
    # 删除它的拓扑文件
    topo = tmp_data_dir / f"{per}.json"
    assert topo.exists()
    topo.unlink()
    # 再切过去: 不应抛 500, 反而重建空树
    status, data = http_post(http_server, "/api/sessions/switch", {"id": sid})
    assert status == 200
    assert data["active_id"] == sid


# ────────────────── HTTP: POST /api/sessions/delete ──────────────────

def test_delete_session_via_http(http_server):
    """新建 2 个会话, 删其中一个 (非 active): 列表减 1, 拓扑文件清除."""
    _, data0 = http_post(http_server, "/api/sessions/create")
    _, data1 = http_post(http_server, "/api/sessions/create")
    # data1.active_id 是当前 active; 我们去删 data0.active_id (非 active)
    to_delete = data0["active_id"]
    per = next(s["persist_key"] for s in data1["sessions"]
             if s["id"] == to_delete)
    # 拓扑文件存在
    topo = tmp_path_from_server() / f"{per}.json"
    assert topo.exists()

    status, data = http_post(http_server, "/api/sessions/delete",
                             {"id": to_delete})
    assert status == 200
    assert to_delete not in [s["id"] for s in data["sessions"]]
    # 拓扑文件已删
    assert not topo.exists()
    # sessions.json 已落盘
    on_disk = server._load_sessions()
    assert to_delete not in [s["id"] for s in on_disk]


def test_delete_nonexistent_session_404(http_server):
    """删除不存在的会话 id: 返回 404."""
    status, data = http_post(http_server, "/api/sessions/delete",
                             {"id": "sess_ghost"})
    assert status == 404
    assert "error" in data


def test_delete_last_session_refused(http_server):
    """只剩一个会话时拒绝删除 (400), 避免面板无可用会话."""
    _, data0 = http_get(http_server, "/api/sessions")
    only_id = data0["sessions"][0]["id"]
    status, data = http_post(http_server, "/api/sessions/delete",
                             {"id": only_id})
    assert status == 400
    assert "error" in data
    # 该会话仍在
    _, data1 = http_get(http_server, "/api/sessions")
    assert any(s["id"] == only_id for s in data1["sessions"])


def test_delete_active_session_auto_switches(http_server):
    """删当前 active 会话: 自动切到下一个未归档会话, 新 active 反映到 GET."""
    _, data_a = http_post(http_server, "/api/sessions/create")
    _, data_b = http_post(http_server, "/api/sessions/create")
    active_id = data_b["active_id"]
    other_id = data_a["active_id"]

    status, data = http_post(http_server, "/api/sessions/delete",
                             {"id": active_id})
    assert status == 200
    # 删除的 id 不在 active
    assert data["active_id"] != active_id
    # 切到的是剩余会话之一 (other_id 或 default)
    assert data["active_id"] in (other_id, "default")
    # GET 一致
    _, data_get = http_get(http_server, "/api/sessions")
    assert data_get["active_id"] == data["active_id"]


# ────────────────── HTTP: rename + archive (相关行为加固) ──────────────────

def test_rename_session_via_http(http_server):
    """rename: 写入新标题, 截断 60 字符, 落盘."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    new_title = "我的学习会话"
    status, data = http_post(http_server, "/api/sessions/rename",
                             {"id": sid, "title": new_title})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["title"] == new_title
    # 落盘
    on_disk = server._load_sessions()
    sess_disk = next(s for s in on_disk if s["id"] == sid)
    assert sess_disk["title"] == new_title


def test_rename_session_empty_title_defaults(http_server):
    """空标题 → 默认 '未命名会话'."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    status, data = http_post(http_server, "/api/sessions/rename",
                             {"id": sid, "title": "   "})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["title"] == "未命名会话"


def test_rename_nonexistent_session_404(http_server):
    status, data = http_post(http_server, "/api/sessions/rename",
                             {"id": "sess_no", "title": "x"})
    assert status == 404
    assert "error" in data


def test_archive_toggle_via_http(http_server):
    """archive: 切换 archived 标志, 落盘."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    # 第一次: archived False -> True
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": sid})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is True
    # 第二次: True -> False
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": sid})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is False


def test_archive_nonexistent_session_404(http_server):
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": "sess_no"})
    assert status == 404
    assert "error" in data


# ────────────────── C5: 显式 archive/unarchive 端点 ──────────────────

def test_archive_explicit_true_via_http(http_server):
    """传 archived=true: 显式归档 (非切换), 已归档再传 true 保持 True."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    # 显式归档
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": sid, "archived": True})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is True
    # 再次显式归档: 仍为 True (幂等, 不是切换)
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": sid, "archived": True})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is True


def test_archive_explicit_false_via_http(http_server):
    """传 archived=false: 显式取消归档 (非切换)."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    # 先归档
    http_post(http_server, "/api/sessions/archive", {"id": sid, "archived": True})
    # 显式取消
    status, data = http_post(http_server, "/api/sessions/archive",
                             {"id": sid, "archived": False})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is False


def test_unarchive_endpoint_via_http(http_server):
    """POST /api/sessions/unarchive: 强制 archived=False (幂等)."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    # 先归档
    http_post(http_server, "/api/sessions/archive", {"id": sid, "archived": True})
    # 调 unarchive
    status, data = http_post(http_server, "/api/sessions/unarchive",
                             {"id": sid})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is False
    # 对未归档会话再调 unarchive: 仍为 False (幂等)
    status, data = http_post(http_server, "/api/sessions/unarchive",
                             {"id": sid})
    assert status == 200
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is False


def test_unarchive_nonexistent_session_404(http_server):
    """unarchive 不存在的会话: 404."""
    status, data = http_post(http_server, "/api/sessions/unarchive",
                             {"id": "sess_no"})
    assert status == 404
    assert "error" in data


def test_archive_then_unarchive_flow(http_server):
    """端到端: 显式归档 → 取消归档 → 状态落盘一致."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    # 1. 归档
    _, data = http_post(http_server, "/api/sessions/archive",
                        {"id": sid, "archived": True})
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is True
    # 2. 取消归档 (用 unarchive 端点)
    _, data = http_post(http_server, "/api/sessions/unarchive",
                        {"id": sid})
    sess = next(s for s in data["sessions"] if s["id"] == sid)
    assert sess["archived"] is False
    # 3. 落盘一致
    on_disk = server._load_sessions()
    sess_disk = next(s for s in on_disk if s["id"] == sid)
    assert sess_disk["archived"] is False


def test_archive_explicit_persists_across_reload(http_server, tmp_data_dir):
    """显式归档后重新加载 sessions.json: archived 状态持久化."""
    _, data = http_post(http_server, "/api/sessions/create")
    sid = data["active_id"]
    http_post(http_server, "/api/sessions/archive", {"id": sid, "archived": True})
    # 重新从盘读
    reloaded = server._load_sessions()
    sess = next(s for s in reloaded if s["id"] == sid)
    assert sess["archived"] is True


# ────────────────── 集成: create+switch+delete 一条流 ──────────────────

def test_full_session_lifecycle(http_server):
    """端到端: 新建 → 列表可见 → 切换 → 删除 → 不在列表."""
    # 1. 新建
    _, created = http_post(http_server, "/api/sessions/create")
    sid = created["active_id"]

    # 2. 列表可见
    _, data = http_get(http_server, "/api/sessions")
    assert any(s["id"] == sid for s in data["sessions"])

    # 3. 再建一个 (保证可删)
    _, _ = http_post(http_server, "/api/sessions/create")

    # 4. 切回 sid
    status, data = http_post(http_server, "/api/sessions/switch", {"id": sid})
    assert status == 200 and data["active_id"] == sid

    # 5. 删 sid (此时不是 active, 因为第 3 步又建了个新的并设为 active)
    status, data = http_post(http_server, "/api/sessions/delete", {"id": sid})
    assert status == 200
    assert not any(s["id"] == sid for s in data["sessions"])


# ────────────────── 辅助 ──────────────────

def tmp_path_from_server() -> Path:
    """从当前 server.V5_DATA_DIR 取测试期 tmp 目录 (conftest 已 patch)."""
    return Path(server.V5_DATA_DIR)
