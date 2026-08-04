"""
test_dashboard_server.py — 9100 面板 server.py 缓存与接口验证 (8-04)

背景: UI 重构时发现 /api/components 轮询 4-5s, 根因是 hermes_patch_status /
_running_command_lines / comp_already_up / local_repo_version 每次请求都跑
git / PowerShell / 端口超时探测。修复: 引入 _ttl_cache 装饰器。
本测试验证:
  - _ttl_cache 按参数区分 (曾 bug: 单槽缓存导致 hermes/neko 互相覆盖)
  - 缓存命中: warm 调用显著快于 cold
  - get_component_statuses 返回结构完整 (13 组件 + hermes_patch 注入)

运行:
  E:/Ikaros/runtime/portable-python/python.exe -m pytest tests/test_dashboard_server.py -v
"""
from __future__ import annotations

import os
import sys
import time
import unittest

_DASH = os.path.join(os.path.dirname(__file__), "..", "core", "dashboard")

# 用唯一模块名加载, 避免与 conversation-tree 的 server.py 在 sys.modules 冲突
# (两个测试文件都 import server 时, 后加载者会拿到错模块 → AttributeError)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("dashboard_server", os.path.join(_DASH, "server.py"))
server = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(server)


class TestTtlCache(unittest.TestCase):
    """_ttl_cache 核心行为: TTL 失效 + 按参数区分。"""

    def test_cache_hits_within_ttl(self):
        calls = {"n": 0}

        @server._ttl_cache(60)
        def slow(x):
            calls["n"] += 1
            time.sleep(0.05)
            return x * 2

        self.assertEqual(slow(3), 6)
        self.assertEqual(slow(3), 6)  # 命中缓存, 不再执行
        self.assertEqual(calls["n"], 1)

    def test_cache_distinguishes_args(self):
        """不同参数必须独立缓存 (修复: 单槽缓存 hermes/neko 互相覆盖)。"""
        seen = []

        @server._ttl_cache(60)
        def f(name):
            seen.append(name)
            return name.upper()

        self.assertEqual(f("hermes"), "HERMES")
        self.assertEqual(f("neko"), "NEKO")
        self.assertEqual(f("hermes"), "HERMES")
        self.assertEqual(seen, ["hermes", "neko"])  # 每参数只执行一次

    def test_cache_expires(self):
        calls = {"n": 0}

        @server._ttl_cache(0.1)
        def g():
            calls["n"] += 1
            return calls["n"]

        g()
        time.sleep(0.15)
        self.assertEqual(g(), 2)  # TTL 过期后重新执行

    def test_cache_clear(self):
        calls = {"n": 0}

        @server._ttl_cache(60)
        def h():
            calls["n"] += 1
            return calls["n"]

        h()
        h.__wrapped__  # 装饰器保留 __wrapped__
        h.cache_clear()
        self.assertEqual(h(), 2)


class TestDashboardFunctions(unittest.TestCase):
    """9100 关键函数: 缓存生效 + 返回结构完整。"""

    def test_hermes_patch_status_cached(self):
        server.hermes_patch_status.cache_clear()
        t0 = time.time()
        server.hermes_patch_status()
        t1 = time.time()
        server.hermes_patch_status()
        t2 = time.time()
        # warm 必须命中缓存: 远快于 cold (git 调用 ~0.5s)
        self.assertLess(t2 - t1, 0.1, f"warm 调用应命中缓存: {t2-t1:.3f}s")
        self.assertLess(t2 - t1, t1 - t0)

    def test_comp_already_up_cached(self):
        # 缓存命中验证: 不比较时间(端口在线时两次都快, 时间断言 flaky),
        # 改为直接断言 tcp_probe 只被调用一次。
        calls = {"n": 0}
        orig = server.tcp_probe

        def spy(port):
            calls["n"] += 1
            return orig(port)

        server.tcp_probe = spy
        try:
            server.comp_already_up.cache_clear()
            server.comp_already_up("memory")
            server.comp_already_up("memory")  # 命中缓存, 不再探测
            server.comp_already_up("memory")
            self.assertEqual(calls["n"], 1, "缓存未命中: tcp_probe 被多次调用")
        finally:
            server.tcp_probe = orig

    def test_local_repo_version_cached_and_distinct(self):
        """按 name 区分: hermes 与 neko 各自缓存, 不串数据。"""
        server.local_repo_version.cache_clear()
        t0 = time.time()
        v_h1 = server.local_repo_version("hermes")
        t1 = time.time()
        v_h2 = server.local_repo_version("hermes")
        t2 = time.time()
        self.assertLess(t2 - t1, 0.05, f"warm 未命中: {t2-t1:.3f}s")
        self.assertEqual(v_h1, v_h2)  # 同参数命中同一缓存
        v_n = server.local_repo_version("neko")
        self.assertNotEqual(v_h2.get("commit"), v_n.get("commit"),
                            "hermes/neko 缓存串数据")

    def test_get_component_statuses_complete(self):
        st = server.get_component_statuses()
        self.assertIsInstance(st, list)
        self.assertGreater(len(st), 0)
        ids = {c["id"] for c in st}
        for cid in ("local_model", "memory", "hermes_dashboard", "neko_group"):
            self.assertIn(cid, ids, f"缺少组件 {cid}")
        hermes = next(c for c in st if c["id"] == "hermes_dashboard")
        self.assertIn("hermes_patch", hermes)
        self.assertIn("repo", hermes)


class TestUIAssets(unittest.TestCase):
    """UI 重构后的静态资源完整性。"""

    def _read(self, rel):
        with open(os.path.join(os.path.dirname(__file__), "..", "core", "dashboard", rel),
                  encoding="utf-8") as f:
            return f.read()

    def test_index_html_new_elements(self):
        html = self._read("index.html")
        for marker in ("hero-banner", 'id="fab"', "tour-mask"):
            self.assertIn(marker, html, f"index.html 缺少 {marker}")

    def test_dashboard_css_new_styles(self):
        css = self._read(os.path.join("assets", "dashboard.css"))
        for marker in (".hero-banner", ".fab", ".canvas"):
            self.assertIn(marker, css, f"dashboard.css 缺少 {marker}")


if __name__ == "__main__":
    unittest.main()
