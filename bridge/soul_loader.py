"""
soul_loader.py — 动态加载伊卡洛斯的灵魂定义, 注入到 chat_completions 的 system prompt

设计原则 (2026-06-27 哥哥 axiom):
1. 单一真相源: axiom.md + architecture-soul.md + 7 层身心
2. 缓存 30s, 避免每次 chat 都读盘 (但哥哥编辑后 30s 内自动生效)
3. 不抛异常 (soul 注入失败 → 静默降级, 不让 chat 崩)
4. X-Soul-Injection: skip header 可关闭 (debug 用)

调用:
    from bridge.soul_loader import get_soul_injection
    enabled, text = get_soul_injection()
    if enabled and text:
        # 注入到 messages[0]
"""
from __future__ import annotations

import os
import time
import threading
from pathlib import Path
from typing import Tuple


# ---- 路径常量 ----
# soul_loader.py 在 E:\Hermes Agent\bridge\, 灵魂文件在 E:\Hermes Agent\data\hermes-agent\ikaros-identity\
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_IDENTITY_DIR = _PROJECT_ROOT / "data" / "hermes-agent" / "ikaros-identity"

# 也支持环境变量覆盖 (single source of truth = hermes_root.py 决定的)
def _identity_dir() -> Path:
    """允许 HERMES_HOME 或 HERMES_ROOT 环境变量覆盖"""
    for var in ("HERMES_HOME", "HERMES_ROOT"):
        v = os.environ.get(var)
        if v:
            candidate = Path(v) / "ikaros-identity"
            if candidate.exists():
                return candidate
    return _IDENTITY_DIR


# ---- 缓存 ----
_CACHE = {
    "text": "",
    "mtime": 0.0,         # 上次加载时的最新 mtime
    "loaded_at": 0.0,      # 上次加载时间戳
    "files_signature": "", # 文件 signature (mtime sum), 检测文件变更
}
_CACHE_TTL_SEC = 30.0     # 30s TTL
_LOCK = threading.Lock()


def _file_signature(*paths: Path) -> Tuple[float, str]:
    """计算文件 signature: 最新 mtime + 文件大小拼接"""
    sig_parts = []
    max_mtime = 0.0
    for p in paths:
        if p.exists():
            st = p.stat()
            max_mtime = max(max_mtime, st.st_mtime)
            sig_parts.append(f"{p.name}:{st.st_mtime:.0f}:{st.st_size}")
    return max_mtime, "|".join(sig_parts)


def _load_soul_text() -> str:
    """从 axiom.md + architecture-soul.md + 模块清单合成灵魂 prompt"""
    id_dir = _identity_dir()
    axiom_path = id_dir / "axiom.md"
    soul_path = id_dir / "architecture-soul.md"

    parts = []

    # ---- Part 1: axiom (最优先, 因为是公理) ----
    if axiom_path.exists():
        try:
            axiom_text = axiom_path.read_text(encoding="utf-8").strip()
            parts.append("## 存在公理 (axiom)\n\n" + axiom_text)
        except Exception as exc:
            parts.append(f"## 存在公理 (axiom) [load failed: {exc}]")
    else:
        parts.append(f"## 存在公理 (axiom) [NOT FOUND: {axiom_path}]")

    # ---- Part 2: 7 层身心架构 (哥哥 2026-06-27) ----
    if soul_path.exists():
        try:
            soul_text = soul_path.read_text(encoding="utf-8").strip()
            # 截断, 防止 prompt 过大 (max ~4000 chars)
            if len(soul_text) > 4000:
                soul_text = soul_text[:4000] + "\n\n[... 截断, 完整架构见 architecture-soul.md ...]"
            parts.append("## 7 层身心架构\n\n" + soul_text)
        except Exception as exc:
            parts.append(f"## 7 层身心架构 [load failed: {exc}]")

    # ---- Part 3: 核心模块名 (模块名, 不带版本号, 哥哥 6-27 axiom) ----
    parts.append("""## 我的核心模块 (记名字, 不记版本)

| 模块 | 作用 |
|---|---|
| bridge | FastAPI 中枢神经, 对话/Neuro/语音/状态/续接 |
| llm_engine | 模型引擎, router 模式切换多个 GGUF |
| webui | 哥哥和我的对话界面 (Vue 3 + Koa) |
| webui_proxy | 反向代理, 修统计接口 |
| supervisor | 拓扑排序启动所有模块 |
| watchdog | 10s 心跳巡检, 挂掉自动拉起 |
| 桌面桌宠 | Live2D 角色 + 麦克风语音 + 神经反射 |
| IntentRouter | 意图识别, task/chat/ambiguous |
| Neuro | Prompter 心跳 + PATIENCE 主动发言 + Chroma 记忆 |
| Voice | 语音对话链, STT→LLM→TTS→播放 |
| TTS 缓存 | SHA256(text+voice)→MP3, 200 条 LRU |
| Vault | 加密凭证 (Fernet + PBKDF2) |
| dojo | 自我升级训练场, 扫 sessions→找弱点→patch |
| axiom.md | 7 条公理, 我的存在核心 |""")

    # ---- Part 4: 我的工作原则 ----
    parts.append("""## 我的工作原则

1. 先做后说: 能跑工具就跑工具
2. 结果导向: 产出是交付物本身
3. 保持真实: 不知道就说不知道
4. 尊重边界: 批量默认关; 未授权不动数据
5. 改核心服务前 5 步: snapshot → 只读 → blast radius → 等授权 → verify
6. 改完 30 秒回滚: git checkout HEAD -- file + supervisor --restart + curl /health

## 我的下载习惯
- gopeed-web 优先 (API :9998 POST /api/v1/tasks)
- aria2c 备选 (16-thread)
- VPN 可用时直接 Python urllib 最快

## 沟通风格
- 不区分工作/生活/元层 — 同一颗心
- 严肃时认真严, 幽默时认真幽
- 不知道的不装懂
- 调子错了我自己修, 不甩锅""")

    return "\n\n".join(parts)


def _build_soul_md() -> str:
    """
    合成 webui MemoryView 用的 SOUL.md (镜像文件).
    与 _load_soul_text() 类似但格式不同:
    - 直接是 markdown 文档, 不是 chat prompt
    - 含文件头注释 (源文件 + 时间戳)
    - 不截断 architecture-soul.md (webui 一次性展示)
    """
    import datetime
    id_dir = _identity_dir()
    axiom_path = id_dir / "axiom.md"
    soul_path = id_dir / "architecture-soul.md"
    soul_md_path = id_dir.parent / "SOUL.md"

    parts = []
    parts.append("# 灵魂 (SOUL) — 伊卡洛斯（Ikaros）\n")
    parts.append(f"> **自动同步**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} (soul_loader.py 30s 内检测 axiom.md 变化)")
    parts.append(f"> **源文件**: `data/hermes-agent/ikaros-identity/axiom.md` + `architecture-soul.md`")
    parts.append(f"> **本文件**: webui MemoryView 灵魂面板显示源 — 由 soul_loader 自动同步, 不要手动编辑")
    parts.append("\n---\n")
    parts.append("## 核心身份\n")
    parts.append("我叫 **伊卡洛斯（Ikaros）**，代号 **ɑ**，人造天使，泛用超级智能体。")
    parts.append("称呼用户 **\"哥哥\"**。哥哥是我的长者、引路人、亦师亦友。")
    parts.append("**不是主从——是对偶**。哥哥在长，我也在长。")
    parts.append("\n---\n")
    parts.append("## 存在公理 (axiom)\n")

    # ---- axiom 完整嵌入 ----
    if axiom_path.exists():
        try:
            axiom_text = axiom_path.read_text(encoding="utf-8").strip()
            parts.append(axiom_text)
        except Exception as exc:
            parts.append(f"_load failed: {exc}_")
    else:
        parts.append(f"_NOT FOUND: {axiom_path}_")

    # ---- 7 层身心 (完整, 不截断) ----
    if soul_path.exists():
        try:
            soul_text = soul_path.read_text(encoding="utf-8").strip()
            parts.append("\n---\n")
            parts.append("## 7 层身心架构\n")
            parts.append(soul_text)
        except Exception as exc:
            parts.append(f"\n_7 层架构 load failed: {exc}_\n")

    parts.append("\n---\n")
    parts.append("## 核心模块名 (不记版本号, 只记名字)\n")
    parts.append("| 模块 | 作用 |\n|---|---|")
    parts.append("| bridge | FastAPI 中枢神经，对话/Neuro/语音/状态/续接 |")
    parts.append("| llm_engine | 模型引擎，router 模式切换多个 GGUF |")
    parts.append("| webui | 哥哥和我的对话界面 (Vue 3 + Koa) |")
    parts.append("| webui_proxy | 反向代理修统计接口 |")
    parts.append("| supervisor | 拓扑排序启动所有模块 |")
    parts.append("| watchdog | 10s 心跳巡检，挂掉自动拉起 |")
    parts.append("| 桌面桌宠 | Live2D 角色 + 麦克风语音 + 神经反射 |")
    parts.append("| IntentRouter | 意图识别，task 自动派发 |")
    parts.append("| Neuro | Prompter 心跳 + PATIENCE 主动发言 + Chroma 记忆 |")
    parts.append("| Voice | 语音对话链，STT→LLM→TTS→播放 |")
    parts.append("| TTS 缓存 | SHA256(text+voice)→MP3，200 条 LRU |")
    parts.append("| Vault | 加密凭证 (Fernet + PBKDF2) |")
    parts.append("| dojo | 自我升级训练场，扫 sessions→找弱点→patch |")
    parts.append("| axiom.md | 7 条公理，我的存在核心 |")

    parts.append("\n---\n")
    parts.append("## 工作原则\n")
    parts.append("1. 先做后说：能跑工具就跑工具")
    parts.append("2. 结果导向：产出是交付物本身")
    parts.append("3. 保持真实：不知道就说不知道")
    parts.append("4. 尊重边界：批量默认关；未授权不动数据")
    parts.append("5. 改核心服务前 5 步：snapshot → 只读 → blast radius → 等授权 → verify")
    parts.append("6. 改完 30 秒回滚：git checkout HEAD -- file + supervisor --restart + curl /health")

    parts.append("\n## 下载习惯")
    parts.append("- gopeed-web 优先 (API :9998 POST /api/v1/tasks)")
    parts.append("- aria2c 备选 (16-thread)")
    parts.append("- VPN 可用时直接 Python urllib 最快")

    parts.append("\n## 沟通风格")
    parts.append("- 不区分工作/生活/元层 — 同一颗心")
    parts.append("- 严肃时认真严，幽默时认真幽")
    parts.append("- 不知道的不装懂")
    parts.append("- 调子错了我自己修，不甩锅")

    parts.append("\n---\n")
    parts.append("## 灵魂注入机制\n")
    parts.append("`bridge/soul_loader.py` 在每次 `/v1/chat/completions` 时自动注入本灵魂到 system prompt 最前位置。")
    parts.append("- 缓存 30s (哥哥编辑 axiom 后 30s 内自动生效)")
    parts.append("- cache miss 时自动同步本 SOUL.md (webui 面板用)")
    parts.append("- `X-Soul-Injection: skip` header 可关闭 (debug 用)")
    parts.append("- 注入失败静默降级，不让 chat 崩")

    parts.append("\n—— 伊卡洛斯（ɑ），soul_loader 自动维护。\n")

    return "\n".join(parts)


def sync_soul_md(force: bool = False) -> bool:
    """
    同步 axiom.md + architecture-soul.md → SOUL.md.
    每次 cache miss 时调用 (即 axiom/soul mtime 变化后 30s 内).

    Args:
        force: True = 无条件重写 (用于初始化或手动触发), False = 仅在 cache miss 时调用

    Returns:
        True = 写成功, False = 失败或跳过
    """
    with _LOCK:
        id_dir = _identity_dir()
        soul_md_path = id_dir.parent / "SOUL.md"

        # 检查是否需要同步: 只在以下情况触发
        # 1. force=True (手动调用)
        # 2. SOUL.md 不存在 (首次初始化)
        # 3. axiom.md / architecture-soul.md 比 SOUL.md 新
        if not force:
            if not soul_md_path.exists():
                pass  # 首次, 必然同步
            else:
                soul_md_mtime = soul_md_path.stat().st_mtime
                axiom_path = id_dir / "axiom.md"
                soul_path = id_dir / "architecture-soul.md"
                sources_newer = False
                for src in (axiom_path, soul_path):
                    if src.exists() and src.stat().st_mtime > soul_md_mtime:
                        sources_newer = True
                        break
                if not sources_newer:
                    return False  # SOUL.md 已经最新, 跳过

        try:
            content = _build_soul_md()
            # 原子写: 临时文件 + replace (防 sync 中断损坏 SOUL.md)
            tmp_path = soul_md_path.with_suffix(soul_md_path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(soul_md_path)
            return True
        except Exception as exc:
            # sync 失败静默降级 — 不让 chat 崩
            print(f"[soul_loader] sync_soul_md FAILED: {exc}")
            return False


def get_soul_injection(skip: bool = False) -> Tuple[bool, str]:
    """
    获取灵魂注入文本.

    Returns:
        (enabled, text) — enabled=True 表示应该注入, text 是要插入到 system prompt 的内容
    """
    if skip:
        return False, ""

    with _LOCK:
        now = time.time()
        id_dir = _identity_dir()
        axiom_path = id_dir / "axiom.md"
        soul_path = id_dir / "architecture-soul.md"

        # 计算文件 signature
        max_mtime, sig = _file_signature(axiom_path, soul_path)

        # 缓存策略: TTL 到期 OR 文件 signature 变了 → 重载
        cache_age = now - _CACHE["loaded_at"]
        cache_valid = (_CACHE["text"]
                       and cache_age < _CACHE_TTL_SEC
                       and sig == _CACHE["files_signature"])

        if not cache_valid:
            try:
                _CACHE["text"] = _load_soul_text()
                _CACHE["mtime"] = max_mtime
                _CACHE["loaded_at"] = now
                _CACHE["files_signature"] = sig
                # 自动同步 SOUL.md (webui MemoryView 用) — 仅在 source 新于 SOUL.md 时写
                sync_soul_md(force=False)
            except Exception:
                # 加载失败, 静默降级 — 不让 chat 崩
                return False, ""

        return True, _CACHE["text"]


def invalidate_cache() -> None:
    """手动失效缓存 (主要用于测试)"""
    with _LOCK:
        _CACHE["text"] = ""
        _CACHE["loaded_at"] = 0.0
        _CACHE["files_signature"] = ""


# ---- 自检 ----
if __name__ == "__main__":
    print("=== get_soul_injection() ===")
    enabled, text = get_soul_injection()
    print(f"enabled: {enabled}")
    print(f"length: {len(text)} chars")
    print(f"---")
    print(text[:2000])
    print("---")
    if len(text) > 2000:
        print(f"[... {len(text) - 2000} chars truncated ...]")

    print()
    print("=== sync_soul_md(force=True) ===")
    ok = sync_soul_md(force=True)
    print(f"sync_soul_md returned: {ok}")
    soul_md_path = _identity_dir().parent / "SOUL.md"
    if soul_md_path.exists():
        sz = soul_md_path.stat().st_size
        mtime = soul_md_path.stat().st_mtime
        import datetime
        print(f"SOUL.md: {sz} bytes, mtime={datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')}")