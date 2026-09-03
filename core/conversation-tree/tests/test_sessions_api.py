"""会话卡 C6: 新增端点 pytest 测试 (duplicate / batch_archive / restore / reorder /
get / groups / pin / set_meta). 覆盖正向 + 反向 + 锁一致性.

测试架构复用 conftest.server / http_get / http_post (测试 ThreadingHTTPServer 真实链路).
"""
from __future__ import annotations

import json

import pytest

# 从 conftest 拿到: server 模块 / 工具函数
from conftest import server, http_get, http_post  # type: ignore  # noqa: E402


def _post(http_server, path: str, payload: dict | None = None):
    return http_post(http_server, path, payload or {})


def _get(http_server, path: str):
    return http_get(http_server, path)


# ────────────────── duplicate ──────────────────

def test_duplicate_creates_new_session(http_server, tmp_data_dir, patched_store, reset_state):
    _, r = _post(http_server, "/api/sessions/create", {})
    sid_src = r["active_id"]
    code, r2 = _post(http_server, "/api/sessions/duplicate", {"source_id": sid_src})
    assert code == 200
    assert r2["ok"] is True
    assert r2["id"] != sid_src
    new_id = r2["id"]
    sids = [s["id"] for s in r2["sessions"]]
    assert new_id in sids
    new_sess = next(s for s in r2["sessions"] if s["id"] == new_id)
    assert "副本" in new_sess["title"]
    new_topo = server.V5_DATA_DIR / f"ui_conversation_tree_{new_id}.json"
    assert new_topo.exists()


def test_duplicate_missing_source_returns_404(http_server, tmp_data_dir, patched_store, reset_state):
    code, r = _post(http_server, "/api/sessions/duplicate", {"source_id": "no_such_id"})
    assert code == 404
    assert "not found" in r["error"]


# ────────────────── batch_archive ──────────────────

def test_batch_archive_marks_multiple(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, rb = _post(http_server, "/api/sessions/create", {})
    b = rb["active_id"]
    code, r = _post(http_server, "/api/sessions/batch_archive", {"ids": [a, b], "archived": True})
    assert code == 200
    assert r["changed"] == 2
    for s in r["sessions"]:
        if s["id"] in (a, b):
            assert s["archived"] is True
        else:
            assert s["archived"] is False


def test_batch_archive_idempotent(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, r1 = _post(http_server, "/api/sessions/batch_archive", {"ids": [a], "archived": True})
    _, r2 = _post(http_server, "/api/sessions/batch_archive", {"ids": [a], "archived": True})
    assert r1["changed"] == 1
    assert r2["changed"] == 0


def test_batch_archive_empty_ids_returns_400(http_server, tmp_data_dir, patched_store, reset_state):
    code, _ = _post(http_server, "/api/sessions/batch_archive", {"ids": []})
    assert code == 400


# ────────────────── restore ──────────────────

def test_restore_unarchives(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _post(http_server, "/api/sessions/archive", {"id": a})
    code, r = _post(http_server, "/api/sessions/restore", {"ids": [a]})
    assert code == 200
    assert r["changed"] == 1
    sess = next(s for s in r["sessions"] if s["id"] == a)
    assert sess["archived"] is False


def test_restore_active_session_no_change(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, r = _post(http_server, "/api/sessions/restore", {"ids": [a]})
    assert r["changed"] == 0


# ────────────────── reorder ──────────────────

def test_reorder_assigns_order_field(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, rb = _post(http_server, "/api/sessions/create", {})
    b = rb["active_id"]
    _, rc = _post(http_server, "/api/sessions/create", {})
    c = rc["active_id"]
    code, r = _post(http_server, "/api/sessions/reorder", {"order": [c, b, a]})
    assert code == 200
    by_id = {s["id"]: s for s in r["sessions"]}
    assert by_id[c]["order"] == 0
    assert by_id[b]["order"] == 1
    assert by_id[a]["order"] == 2


def test_reorder_partial_appends_missing(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, rb = _post(http_server, "/api/sessions/create", {})
    b = rb["active_id"]
    _, rc = _post(http_server, "/api/sessions/create", {})
    c = rc["active_id"]
    code, r = _post(http_server, "/api/sessions/reorder", {"order": [c, a]})
    by_id = {s["id"]: s for s in r["sessions"]}
    assert by_id[c]["order"] == 0
    assert by_id[a]["order"] == 1
    assert by_id[b]["order"] >= 2


# ────────────────── pin ──────────────────

def test_pin_toggles_pinned_field(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/pin", {"id": a, "pinned": True})
    assert r["session"]["pinned"] is True
    code2, r2 = _post(http_server, "/api/sessions/pin", {"id": a, "pinned": False})
    assert r2["session"]["pinned"] is False


def test_pin_missing_session_404(http_server, tmp_data_dir, patched_store, reset_state):
    code, _ = _post(http_server, "/api/sessions/pin", {"id": "no"})
    assert code == 404


# ────────────────── set_meta ──────────────────

def test_set_meta_color(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/set_meta", {"id": a, "color": "#FF0080"})
    assert code == 200
    assert r["session"]["color"] == "#FF0080"


def test_set_meta_short_color(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/set_meta", {"id": a, "color": "#f0a"})
    assert code == 200
    assert r["session"]["color"] == "#f0a"


def test_set_meta_bad_color_rejected(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, _ = _post(http_server, "/api/sessions/set_meta", {"id": a, "color": "red"})
    assert code == 400
    code2, _ = _post(http_server, "/api/sessions/set_meta", {"id": a, "color": "#GGGGGG"})
    assert code2 == 400


def test_set_meta_tags(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/set_meta", {"id": a, "tags": ["代码", "review"]})
    assert r["session"]["tags"] == ["代码", "review"]


def test_set_meta_tags_must_be_list(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, _ = _post(http_server, "/api/sessions/set_meta", {"id": a, "tags": "not list"})
    assert code == 400


def test_set_meta_system_prompt_and_model(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/set_meta", {
        "id": a,
        "system_prompt": "You are a coding agent.",
        "model": "deepseek-coder",
    })
    sess = r["session"]
    assert sess["system_prompt"] == "You are a coding agent."
    assert sess["model"] == "deepseek-coder"


def test_set_meta_whitelist_rejects_unknown_keys(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _post(http_server, "/api/sessions/set_meta", {"id": a, "evil": "x", "color": "#fff"})
    assert code == 200
    assert "evil" not in r["session"]
    assert r["session"]["color"] == "#fff"


# ────────────────── get / groups ──────────────────

def test_get_session_detail(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    code, r = _get(http_server, f"/api/sessions/get/{a}")
    assert code == 200
    assert r["session"]["id"] == a


def test_get_missing_session_404(http_server, tmp_data_dir, patched_store, reset_state):
    code, _ = _get(http_server, "/api/sessions/get/no_such")
    assert code == 404


def test_groups_partitions(http_server, tmp_data_dir, patched_store, reset_state):
    _, ra = _post(http_server, "/api/sessions/create", {})
    a = ra["active_id"]
    _, rb = _post(http_server, "/api/sessions/create", {})
    b = rb["active_id"]
    _post(http_server, "/api/sessions/archive", {"id": b})
    _post(http_server, "/api/sessions/pin", {"id": a, "pinned": True})
    code, body = _get(http_server, "/api/sessions/groups")
    assert code == 200
    archived_ids = [s["id"] for s in body["archived"]]
    pinned_ids = [s["id"] for s in body["pinned"]]
    active_ids = [s["id"] for s in body["active"]]
    assert b in archived_ids
    assert b not in active_ids
    assert a in pinned_ids
    assert a in active_ids
