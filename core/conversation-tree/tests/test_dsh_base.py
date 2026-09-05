"""对话树底座语义 (2026-08-23, 重写 2026-09-05): deepseek-harness-shared.

LLM 配置完全复用 dsh (~/.dsh/settings.yaml -> llm-pi-ai.providers.* +
~/.dsh/.credentials.yaml refs.<apiKeyEnv>), 不再读 .env.
测试用 monkeypatch 指向临时 settings.yaml, 验证 API 形态正确.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── 加载 server.py + _dsh_shared.py ──
_HERE = Path(__file__).resolve().parent
_CT_DIR = _HERE.parent
_CORE = _CT_DIR.parent

_SERVER_PATH = _CT_DIR / "server.py"
_spec = importlib.util.spec_from_file_location("ct_server", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
sys.modules["ct_server"] = server  # 在 exec 前注册, 避免 server.py 内部 import 找不到
_spec.loader.exec_module(server)

_DSH_PATH = _CT_DIR / "_dsh_shared.py"
# 关键: 必须先注册到 sys.modules, 再加载 server.py; 否则 server.py 内部
# `import _dsh_shared` 会创建第二个模块实例, 导致 server._dsh 和这里的
# _dsh 是不同对象, 缓存刷新不生效.
_dsh_spec = importlib.util.spec_from_file_location("_dsh_shared", _DSH_PATH)
_dsh = importlib.util.module_from_spec(_dsh_spec)
sys.modules["_dsh_shared"] = _dsh
_dsh_spec.loader.exec_module(_dsh)


# ── fixtures ──


@pytest.fixture(autouse=True)
def _isolate_dsh_home(request, monkeypatch: pytest.MonkeyPatch):
    """自动给每个 test_dsh_base 测试设置隔离的 DSH_HOME, 默认空目录.

    每个测试用 monkeypatch.setenv 覆盖 DSH_HOME 写到自己的 tmpdir.
    autouse=True 保证 conftest 的 http_server 启动前 DSH_HOME 已设.
    同时清 _dsh._active_llm_cache 避免上一测试的缓存命中.
    """
    import tempfile as _tempfile
    default = Path(_tempfile.mkdtemp(prefix="ct_dsh_iso_"))
    monkeypatch.setenv("DSH_HOME", str(default))
    server._dsh.refresh_active_llm_cache()
    yield
    server._dsh.refresh_active_llm_cache()


def _write_yaml(path: Path, data) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


@pytest.fixture
def configured_dsh_home(monkeypatch: pytest.MonkeyPatch):
    """写最小 settings.yaml + .credentials.yaml 到 server 进程的 DSH_HOME.

    必须用 http_server 的 tmpdir (不是 fixture 自己的 tmp_path), 否则 server
    进程的 _dsh 看不到. 通过 conftest 的 http_server 把 DSH_HOME 设到共享路径,
    这里 monkeypatch.setattr 直接复用.
    """
    import tempfile as _tempfile
    shared = Path(_tempfile.mkdtemp(prefix="ct_dsh_cfg_"))
    monkeypatch.setenv("DSH_HOME", str(shared))
    _write_yaml(
        shared / "settings.yaml",
        {
            "llm-pi-ai": {
                "providers": {
                    "opencode-go": {
                        "apiKeyEnv": "OPENCODE_GO_API_KEY",
                        "baseURL": "https://api.example.com/v1",
                        "models": [
                            {"id": "test-model", "name": "Test Model",
                             "contextWindow": 128000, "maxTokens": 8192},
                        ],
                    }
                }
            },
            "agent-default-model": {"provider": "opencode-go", "model": "test-model"},
        },
    )
    _write_yaml(
        shared / ".credentials.yaml",
        {"version": 1, "refs": {"OPENCODE_GO_API_KEY": "sk-test-redacted"}},
    )
    _dsh.refresh_active_llm_cache()
    yield shared
    _dsh.refresh_active_llm_cache()


@pytest.fixture
def empty_dsh_home(monkeypatch: pytest.MonkeyPatch):
    """空 DSH_HOME (settings.yaml 缺失), 验证 fallback / 503 路径.

    依赖 autouse _isolate_dsh_home 已设了空 tmpdir, 这里只需要
    确保 DEEPSEEK_API_KEY 不在 env (兜底路径才不会误触发).
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    _dsh.refresh_active_llm_cache()
    yield
    _dsh.refresh_active_llm_cache()


# ── /api/health ──


def test_health_declares_dsh_shared_base(configured_dsh_home, http_server):
    """2026-09-05: 健康检查返回 dsh-shared 配置, base = 'deepseek-harness-shared'."""
    from conftest import http_get
    st, h = http_get(http_server, "/api/health")
    assert st == 200
    assert h["base"] == "deepseek-harness-shared"
    assert h["dsh_key"] is True
    assert h["provider"] == "opencode-go"
    assert h["model"] == "test-model"
    assert h["base_url"] == "https://api.example.com/v1"


def test_health_handles_missing_dsh_config(empty_dsh_home, http_server):
    """settings.yaml 缺失时 health 返回 200 + dsh_key=false, 不挂."""
    from conftest import http_get
    st, h = http_get(http_server, "/api/health")
    assert st == 200
    assert h["base"] == "deepseek-harness-shared"
    assert h["dsh_key"] is False
    assert "error" in h


# ── /api/providers ──


def test_providers_list_dsh_shared(configured_dsh_home, http_server):
    from conftest import http_get
    st, p = http_get(http_server, "/api/providers")
    assert st == 200
    assert p["base"] == "deepseek-harness-shared"
    provs = p.get("providers") or []
    assert any(pr["id"] == "opencode-go" for pr in provs)
    opencode = next(pr for pr in provs if pr["id"] == "opencode-go")
    assert opencode["configured"] is True
    assert opencode["api_key_env"] == "OPENCODE_GO_API_KEY"
    assert "test-model" in opencode["models"]
    # 密钥不得出现在响应
    assert "sk-test-redacted" not in str(p), "密钥脱敏失败"


def test_providers_empty_when_no_settings(empty_dsh_home, http_server):
    from conftest import http_get
    st, p = http_get(http_server, "/api/providers")
    assert st == 200
    assert p["base"] == "deepseek-harness-shared"
    assert p.get("providers") == []


# ── /api/model_switch (GET) ──


def test_model_switch_lists_dsh_catalog(configured_dsh_home, http_server):
    from conftest import http_get
    st, m = http_get(http_server, "/api/model_switch")
    assert st == 200
    assert m["ok"] is True
    assert m["defaults"]["ikaros"] == "test-model"
    assert m["active"] == {"provider": "opencode-go", "model": "test-model"}
    avail = m.get("available") or []
    assert any(it["model"] == "test-model" for it in avail)


def test_model_switch_503_when_config_missing(empty_dsh_home, http_server):
    from conftest import http_get
    st, m = http_get(http_server, "/api/model_switch")
    assert st == 503
    assert m["ok"] is False
    assert "error" in m


# ── /api/llm_config ──


def test_llm_config_returns_dsh_state(configured_dsh_home, http_server):
    """新增端点: 返回 dsh 共享 LLM 完整配置 (脱敏)."""
    from conftest import http_get
    st, c = http_get(http_server, "/api/llm_config")
    assert st == 200
    assert c["ok"] is True
    assert c["provider"] == "opencode-go"
    assert c["model"] == "test-model"
    assert c["baseURL"] == "https://api.example.com/v1"
    assert c["apiKeyEnv"] == "OPENCODE_GO_API_KEY"
    assert c["apiKey_set"] is True
    assert c["contextWindow"] == 128000
    assert c["maxTokens"] == 8192
    assert c["source"] == "settings"
    assert any(m["id"] == "test-model" for m in c["models"])
    # 密钥不外泄
    assert "sk-test-redacted" not in str(c), "密钥脱敏失败"
    assert "sk-test" not in str(c)


def test_llm_config_503_when_config_missing(empty_dsh_home, http_server):
    from conftest import http_get
    st, c = http_get(http_server, "/api/llm_config")
    assert st == 503
    assert c["ok"] is False
    assert "code" in c
    # dsh_home 应该指隔离 tmpdir (ct_dsh_iso_*), 不应该是真实 ~/.dsh
    assert "ct_dsh_iso_" in c["dsh_home"]
