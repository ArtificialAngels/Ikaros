"""
tests/test_phase1_cleanup.py — pytest canonical verify of Phase 1 cleanup batch.

Covers the 11-ops cleanup batch performed on 2026-07-02 by Ikaros
triggered by 哥哥's "A+B 删除, C 按推荐做, D 按推荐来" decision.

5-step protocol:
  1. ✅ git tag phase-1-cleanup-pre (before changes)
  2. ✅ blast radius listed (11 ops, 0 touched core 5, 0 process restart)
  3. ✅ report + wait for authorization
  4. ✅ 哥哥 A+B+C+D approved
  5. ✅ THIS TEST (canonical pytest runner, not ad-hoc)

Changed paths verified:
  modules/llm_engine/module.json       (start: "start.ps1" → null)
  modules/llm_engine/start.ps1         (D, mv to .disabled)
  modules/llm_engine/start.ps1.disabled (??, new untracked)
  modules/__pycache__ + 2 subdirs       (deleted)
  data/_backup_chat_history_C_2026-06-29 (deleted)
  data/_backup_pre_ikaros_rename       (deleted)
  .webui_secret_key                    (deleted, was template only)
  data/ikaros-coordination/*.json      (45 titles added, 4 renamed)

Process changes: NONE (5-step protocol 0 process changes).

Run: pytest tests/test_phase1_cleanup.py -v
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(r"E:\Ikaros")


def _run(cmd, **kw):
    """Run subprocess and return CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True,
                         encoding='gbk', errors='replace', **kw)


def _wmic_pid_for_orphan():
    """Return list of PIDs running 'hermes_cli.main serve --port 0'."""
    r = _run(['wmic', 'process', 'get', 'ProcessId,CommandLine', '/format:list'])
    pids = []
    cur = {}
    for line in r.stdout.split('\n'):
        line = line.strip()
        if '=' in line:
            k, v = line.split('=', 1)
            cur[k] = v
        if 'hermes_cli.main serve' in line and '--port 0' in line:
            pids.append(cur.get('ProcessId', '?'))
    return pids


# === [A+B P0-1] llm_engine disable =====================================
class TestLlamaEngineDisable:
    """Body 14.1: mv start.ps1 + module.json start:null."""

    def test_start_ps1_renamed(self):
        src = ROOT / "modules" / "llm_engine" / "start.ps1"
        dst = ROOT / "modules" / "llm_engine" / "start.ps1.disabled"
        assert not src.exists(), f"start.ps1 should be renamed, but exists at {src}"
        assert dst.exists(), f"start.ps1.disabled should exist at {dst}"
        assert dst.stat().st_size > 1000, "start.ps1.disabled should retain content (~17KB)"

    def test_module_json_start_is_null(self):
        mj = (ROOT / "modules" / "llm_engine" / "module.json").read_text(encoding='utf-8')
        assert '"start": null' in mj, "module.json should have 'start': null"

    def test_module_json_no_phantom_comment(self):
        mj = (ROOT / "modules" / "llm_engine" / "module.json").read_text(encoding='utf-8')
        assert '_start_disabled' not in mj, "module.json has phantom _start_disabled comment"


# === [A+B A1] modules/__pycache__ removed ===============================
class TestPycacheRemoved:
    """All __pycache__ in modules/ cleared (3 dirs: root + 2 subdirs)."""

    def test_no_pycache_in_modules(self):
        pycaches = list((ROOT / "modules").rglob("__pycache__"))
        assert pycaches == [], f"__pycache__ should not exist in modules/, found: {pycaches}"


# === [A+B A7] orphan hermes_cli serve ===================================
class TestOrphanServe:
    """Body 14.2: observe only, NOT kill. Hermes.exe反复拉起, kill 无用."""

    def test_orphan_count_at_most_one(self):
        """Hermes.exe parent拉起子进程; PID持续变; 5步协议0进程改动."""
        pids = _wmic_pid_for_orphan()
        assert len(pids) <= 1, (
            f"orphan hermes_cli serve should be <= 1, got {len(pids)}: {pids}"
        )


# === [C] data/_backup_* removed =========================================
class TestBackupDirsRemoved:
    """C1 + C2: 早期 chat history + pre-rename 备份删除."""

    def test_chat_history_backup_gone(self):
        path = ROOT / "data" / "_backup_chat_history_C_2026-06-29"
        assert not path.exists(), f"{path} should be removed"

    def test_pre_ikaros_rename_backup_gone(self):
        path = ROOT / "data" / "_backup_pre_ikaros_rename"
        assert not path.exists(), f"{path} should be removed"


# === [C] data/_archive_2026-07-01/ kept (P2-1, 7-day retention) ========
class TestArchiveKept:
    """C3: 7-2删的mem0备份, 7天保留期, 7-9清."""

    def test_archive_present(self):
        path = ROOT / "data" / "ikaros-coordination" / "_archive_2026-07-01"
        assert path.exists(), f"{path} should exist (7-day retention until 7-9)"


# === [C] state-snapshots kept recent ====================================
class TestStateSnapshots:
    """C4: 只有最近1个 (7-1 19:44 pre-update), 老的已清."""

    def test_at_most_three_snapshots(self):
        path = ROOT / "data" / "hermes-agent" / "state-snapshots"
        if path.exists():
            items = list(path.iterdir())
            assert len(items) <= 3, f"snapshots should be <= 3, got {len(items)}"


# === [C] .webui_secret_key removed ======================================
class TestWebuiSecretKeyRemoved:
    """C10: 旧 webui 残留, 内容只是 template, 无 code refs."""

    def test_webui_secret_key_gone(self):
        path = ROOT / ".webui_secret_key"
        assert not path.exists(), f"{path} should be removed"


# === [D P1-1] handshake title field ====================================
class TestHandshakeTitles:
    """D: 45 handshakes补 title, 现在 81/81 = 100%."""

    def test_all_handshakes_have_title(self):
        coord = ROOT / "data" / "ikaros-coordination"
        jsons = list(coord.glob("handshake.*.json"))
        assert len(jsons) > 0, "should have handshake JSON files"
        n_with_title = 0
        for p in jsons:
            try:
                d = json.loads(p.read_text(encoding='utf-8', errors='replace'))
                if 'title' in d:
                    n_with_title += 1
            except Exception:
                pass
        assert n_with_title == len(jsons), (
            f"all handshakes should have title; got {n_with_title}/{len(jsons)}"
        )


# === [D P1-3] handoff rename ============================================
class TestHandoffNaming:
    """D: 4 非 handshake 命名 renamed, 只剩 schema 文件."""

    def test_only_schema_non_handshake(self):
        coord = ROOT / "data" / "ikaros-coordination"
        non_hs = [p for p in coord.glob("*.json")
                  if not p.name.startswith("handshake.")]
        assert len(non_hs) == 1, f"should be 1 non-handshake, got {len(non_hs)}: {non_hs}"
        assert non_hs[0].name == "ikaros-coordination-schema.json"


# === [Body 14.1] Ikaros-body.md docs correct ============================
class TestBodyDocCorrect:
    """Body 14.1 修对 (subagent 7-1 报告 'null + 注释' 是错的, 实际是字符串路径)."""

    def test_body_141_marked_fixed(self):
        body = (ROOT / "data" / "hermes-agent" / "ikaros-identity" / "Ikaros-body.md").read_text(encoding='utf-8')
        assert '14.1 ✅ 已修' in body, "Body 14.1 should be marked ✅ 已修"
        assert '14.1 🔴 风险' not in body, "Body 14.1 should NOT be 🔴 风险"

    def test_body_141_documents_fix(self):
        body = (ROOT / "data" / "hermes-agent" / "ikaros-identity" / "Ikaros-body.md").read_text(encoding='utf-8')
        assert 'start.ps1 → start.ps1.disabled' in body, "Body 14.1 should document the mv"
        assert 'start": "start.ps1"' in body, "Body 14.1 should document original string path"


# === [Git tag] backup exists ============================================
class TestGitTagBackup:
    """git tag phase-1-cleanup-pre 在 cleanup batch 前已落 (5步协议第1步)."""

    def test_phase1_cleanup_pre_tag_exists(self):
        r = _run(['git', '-C', str(ROOT), 'tag', '-l', 'phase-1-cleanup-pre'])
        assert 'phase-1-cleanup-pre' in r.stdout, "phase-1-cleanup-pre tag should exist"


# === Smoke: pytest can collect and run this file ========================
def test_pytest_smoke():
    """Sanity check that pytest framework itself is alive."""
    assert True, "pytest alive"