#!/usr/bin/env python
"""soul_refine.py — LLM 定时精炼 Ikaros SOUL.md (pi-reflect 模式).

设计动机
--------
`bin/ikaros-soul-sync.py` 的 `_build_soul_md()` 是**盲抄**: 把 V5 的 self_narrative、
v4.db 教训、identity/axiom 记忆原样拼进 SOUL.md, 零去重、零摘要、还可能截断残句。
本模块在其之上加一个 **LLM 精炼阶段**: 复用现有 collector 产出 raw draft, 交给 LLM
去重 / 摘要 / 收敛人格, 再带安全护栏落盘。

pipeline (每次 run):
  collect raw draft (复用 ikaros-soul-sync._build_soul_md) + axiom
    -> refine_with_llm  (LLM 清洗/去重/摘要)
    -> apply_refinement (备份 + 质量门 + 无变化跳过 + git 留版本)

安全护栏 (取自 pi-reflect):
  - 覆盖前备份当前 SOUL.md
  - 拒绝可疑的大段删除 / 异常膨胀 (相似度 + 长度比双门)
  - 内容无实质变化则跳过 (不浪费 LLM 调用 / 不刷 git)
  - 质量门: 精炼后 <rules> 区必须逐字等于 axiom (铁律不可被改掉)

调度
----
精炼是**重操作 (一次 LLM 调用)**, 绝不能放进 `--watch` 热循环。请用 cron / 任务计划程序
定时调用 `python bin/soul_refine.py --run` (例如每日一次)。`--dry-run` 只打印不落盘。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

def _resolve_ikaros_root() -> Path:
    """定位项目根 (config/identity/axiom.md 所在目录)。

    soul_refine.py 位于 bin/ 下, 若直接取 __file__.parent 会得到 bin/ 而非项目根,
    导致 axiom.md / SOUL.md 路径错位 (axiom 读不到、精炼写错位置)。
    优先用环境变量, 否则按 axiom.md 的实际位置向上回溯。
    """
    env = os.environ.get("IKAROS_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent  # .../bin
    for cand in (here.parent, here, here.parent.parent):
        if (cand / "config" / "identity" / "axiom.md").is_file():
            return cand
    return here.parent  # 兜底: bin 的上一级当作项目根


IKAROS_ROOT = _resolve_ikaros_root()
HERMES_HOME = Path(os.environ.get("HERMES_HOME", IKAROS_ROOT / "data" / "hermes-agent"))
SOUL_PATH = HERMES_HOME / "SOUL.md"
REFINED_MARKER = "<!-- REFINED by soul_refine -->"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [soul-refine] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ikaros.soul_refine")


# ─────────────────────────── LLM 路由 (3 层, 同 conversation-tree server) ───────────────────────────
def _load_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    for _ep in (
        HERMES_HOME / ".env",
        IKAROS_ROOT / ".env",
    ):
        try:
            if _ep.exists():
                for _line in _ep.read_text(encoding="utf-8").split("\n"):
                    _line = _line.strip()
                    if _line.startswith("DEEPSEEK_API_KEY="):
                        return _line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


_DEEPSEEK_KEY = _load_deepseek_key()
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"
HERMES_CHAT_URL = os.environ.get("HERMES_DASHBOARD_URL", "http://127.0.0.1:9119").rstrip("/") + "/v1/chat/completions"
LOCAL_CHAT_URL = os.environ.get("IKAROS_LOCAL_LLM_URL", "http://127.0.0.1:8080").rstrip("/") + "/v1/chat/completions"
LLM_TIMEOUT = int(os.environ.get("SOUL_REFINE_TIMEOUT", "120"))


def _post(url: str, payload: dict, headers: dict) -> str:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


def default_llm(messages: list[dict]) -> str:
    """3 层 LLM: DeepSeek -> Hermes Dashboard -> 本地 :8080. 返回去空白后的文本."""
    errors: list[str] = []
    if _DEEPSEEK_KEY:
        try:
            return _post(DEEPSEEK_CHAT_URL,
                         {"model": "deepseek-chat", "messages": messages,
                          "max_tokens": 2048, "temperature": 0.3, "stream": False},
                         {"Content-Type": "application/json",
                          "Authorization": f"Bearer {_DEEPSEEK_KEY}"})
        except Exception as e:  # noqa: BLE001
            errors.append(f"DeepSeek: {e}")
    try:
        return _post(HERMES_CHAT_URL,
                     {"model": "hermes", "messages": messages,
                      "max_tokens": 2048, "temperature": 0.3, "stream": False},
                     {"Content-Type": "application/json"})
    except Exception as e:  # noqa: BLE001
        errors.append(f"Hermes: {e}")
    try:
        return _post(LOCAL_CHAT_URL,
                     {"model": "local-llm", "messages": messages,
                      "max_tokens": 2048, "temperature": 0.3, "stream": False},
                     {"Content-Type": "application/json"})
    except Exception as e:  # noqa: BLE001
        errors.append(f"Local: {e}")
    raise RuntimeError("LLM unavailable: " + "; ".join(errors))


# ─────────────────────────── 证据收集 ───────────────────────────
def _import_soul_sync():
    """加载 bin/ikaros-soul-sync.py。

    该文件名含连字符 (`ikaros-soul-sync.py`), 无法用 `import ikaros_soul_sync` 直接导入,
    必须用 importlib 按路径加载。返回模块对象或 None。
    """
    import importlib.util
    candidate = IKAROS_ROOT / "bin" / "ikaros-soul-sync.py"
    if not candidate.is_file():
        return None
    try:
        sys.path.insert(0, str(IKAROS_ROOT / "bin"))
        spec = importlib.util.spec_from_file_location("ikaros_soul_sync_loader", str(candidate))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:  # noqa: BLE001
        logger.warning("load ikaros-soul-sync failed: %s", e)
        return None


def collect_evidence() -> tuple[str, str]:
    """收集精炼证据: 现有 SOUL.md 作为 draft, 项目根 axiom.md 作为公理。

    设计:
      - draft 直接读现有 SOUL.md (已由 ikaros-soul-sync 从 V5 拼装, 含经验教训等完整章节),
        不依赖脆弱的 `_build_soul_md` 导入 (其 v4.db 查询在隔离环境会丢 lessons)。
      - axiom 由 soul_refine 自己按**正确项目根**读取 (修正 bin/ 错位), 作为精炼护栏与
        prompt 的权威公理。若 SOUL.md 缺失 (首跑), 再用 `_build_soul_md` 兜底。
    返回 (raw_draft, axiom_text)。
    """
    axiom = ""
    ax_path = IKAROS_ROOT / "config" / "identity" / "axiom.md"
    if ax_path.exists():
        axiom = ax_path.read_text(encoding="utf-8").strip()

    draft = ""
    if SOUL_PATH.exists():
        draft = SOUL_PATH.read_text(encoding="utf-8")
    if not draft:
        # 首跑兜底: 现有 SOUL.md 不存在时, 借 ikaros-soul-sync 的 collector 重建
        try:
            mod = _import_soul_sync()
            if mod is not None:
                mod.IKAROS_ROOT = IKAROS_ROOT  # 修正模块内根, 使其能找到 axiom.md
                draft = mod._build_soul_md()
        except Exception as e:  # noqa: BLE001
            logger.warning("collect_evidence _build_soul_md fallback failed: %s", e)
    return draft, axiom


# ─────────────────────────── 精炼 prompt ───────────────────────────
def build_refine_prompt(draft: str, axiom: str) -> list[dict]:
    system = (
        "你是 Ikaros (人造天使 / 桌面 AI 伴侣) 的身份文件精炼器。给你一份由记忆系统"
        "自动拼装的 SOUL.md 草稿, 它含有重复偏好、截断残句、冗长叙事。你的任务: 重组为一份"
        "带 XML 标签分区的干净身份文件。\n\n"
        "输出结构 (严格使用以下标签):\n"
        "<rules>\n(铁律区: 留空或写占位即可, 程序会自动填入权威 axiom, 你不要编造任何铁律)\n</rules>\n\n"
        "<identity>\n## 核心身份 / ## 信念与价值观 等稳定身份内容\n</identity>\n\n"
        "<style>\n## 沟通风格 / ## 对话指令 / 记忆与自我指南 —— 合并重复、去噪\n</style>\n\n"
        "<memory>\n## 经验教训 / ## 我的能力 —— 合并重复、去噪\n</memory>\n\n"
        "实时动态章节: 保留草稿中出现的 `## 此刻的我` / `## 相关记忆召回` / `## 当前情感状态`\n"
        "等由 V5 实时生成的章节, 原样不动; 不要改写, 也不要新增草稿里没有的实时章节。\n\n"
        "硬性约束:\n"
        "1. `<rules>` 区你只需留占位, 真实铁律由程序填入, 严禁在此编造规则。\n"
        "2. 合并重复: 多条相同的偏好/身份陈述 (如反复出现的'哥哥偏好短句') 合并为一条。\n"
        "3. 摘要叙事: `### 自我叙事` 压缩到最多 2 句, 去掉被截断的半截句子 (结尾不是完整"
        "词语的片段一律删除)。\n"
        "4. 实时章节 (此刻的我 / 相关记忆召回 / 当前情感状态) 原样保留, 不精炼。\n"
        "5. 不新增虚构事实, 不添加解释性评论。\n"
        "6. 只输出精炼后的完整 markdown (含上述标签), 不要任何前后说明或代码围栏。"
    )
    user = (
        f"# Axiom (必须原样保留在 存在公理 章节):\n{axiom}\n\n"
        f"# 待精炼的 raw SOUL.md 草稿:\n{draft}\n\n"
        f"# 请输出精炼后的完整 SOUL.md markdown:"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _strip_fences(text: str) -> str:
    """去掉模型可能包上的 ```markdown ... ``` 围栏。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉首行围栏 (``` 或 ```markdown)
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        if text.endswith("```"):
            text = text[: -3].rstrip()
    return text.strip()


_LIVE_TITLES = ("此刻的我", "相关记忆召回", "当前情感状态")


def _extract_live(text: str) -> str:
    """从草稿抽取实时章节 (## 此刻的我 / 相关记忆召回 / 当前情感状态), 原样返回。"""
    lines = text.split("\n")
    blocks: list[str] = []
    cur: list[str] | None = None
    for ln in lines:
        m = re.match(r"^## (.*)$", ln)
        if m:
            title = m.group(1).strip()
            if any(title.startswith(t) for t in _LIVE_TITLES):
                if cur is not None:
                    blocks.append("\n".join(cur))
                cur = [ln]
            else:
                if cur is not None:
                    blocks.append("\n".join(cur))
                    cur = None
        else:
            if cur is not None:
                cur.append(ln)
    if cur is not None:
        blocks.append("\n".join(cur))
    return "\n\n".join(b.strip() for b in blocks).strip()


def _enforce_rules(refined: str, axiom: str) -> str:
    """程序化保证精炼稿的 `<rules>` 区逐字等于 axiom.md (铁律锁定, 不信任 LLM 自觉)。

    类似 Constitutional AI: 即便 prompt 要求 LLM 在 <rules> 区留占位, 实测它仍会乱填,
    故程序硬替换, 绝不让 LLM 触碰铁律。
    """
    if not axiom or not axiom.strip():
        return refined
    ax = axiom.strip()
    block = f"<rules>\n{ax}\n</rules>"
    m = re.search(r"(?s)<rules>.*?</rules>", refined)
    if m:
        refined = refined[: m.start()] + block + refined[m.end():]
    else:
        refined = refined.rstrip() + "\n\n" + block + "\n"
    return refined


def _preserve_live(refined: str, draft: str) -> str:
    """实时章节 (此刻的我 / 相关记忆召回 / 当前情感状态) 从原 draft 原样保留。

    LLM 可能幻觉改写实时数据, 故先用 draft 原版整体覆盖 refined 中的实时章节:
    按 `## 标题` 切段, 丢弃属于实时区的段, 再把 draft 抽出的实时章节原样接回末尾。
    (比正则区间删除更鲁棒, 不受 <memory> 等标签未闭合影响)
    """
    live = _extract_live(draft)
    if not live:
        return refined
    parts = re.split(r"(?m)^(## .*)$", refined)
    kept: list[str] = []
    i = 0
    while i < len(parts):
        if i % 2 == 1:  # 奇数索引是标题行
            title = parts[i].strip()
            title_core = re.sub(r"^##\s*", "", title)  # 去掉 "## " 前缀再比标题
            if any(title_core.startswith(t) for t in _LIVE_TITLES):
                i += 2  # 跳过该实时标题及其正文段
                continue
        kept.append(parts[i])
        i += 1
    cleaned = "".join(kept).rstrip()
    return cleaned + "\n\n" + live + "\n"


def _strip_stray_axiom(refined: str) -> str:
    """删除非 <rules> 区里残留的 `## 存在公理` / `## 公理` 块。

    axiom 只应出现在 <rules> 区 (由 _enforce_rules 锁定)。LLM 重组时常把旧版公理
    当作普通身份内容塞进 <identity>, 造成与 <rules> 冲突的冗余。此处程序清理。
    """
    parts = re.split(r"(?m)^(## .*)$", refined)
    kept: list[str] = []
    i = 0
    while i < len(parts):
        if i % 2 == 1:
            title = re.sub(r"^##\s*", "", parts[i].strip())
            if title.startswith("存在公理") or title == "公理":
                i += 2
                continue
        kept.append(parts[i])
        i += 1
    return "".join(kept).rstrip() + "\n"


def refine_with_llm(draft: str, axiom: str, llm=default_llm) -> str:
    """调用 LLM 精炼, 返回精炼后的 markdown 文本 (铁律区锁定 + 实时原样 + 去杂公理)。"""
    out = llm(build_refine_prompt(draft, axiom))
    out = _strip_fences(out)
    out = _enforce_rules(out, axiom)      # 铁律区程序锁定, 绝不假手 LLM
    out = _preserve_live(out, draft)      # 实时章节从原稿原样保留
    out = _strip_stray_axiom(out)         # 清理误入其它区的旧公理块
    return out


# ─────────────────────────── 安全护栏 / 落盘 ───────────────────────────
def _similarity(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


def apply_refinement(
    refined: str,
    *,
    current: str | None = None,
    axiom: str | None = None,
    soul_path: Path = SOUL_PATH,
    dry_run: bool = False,
    backup: bool = True,
    min_similarity: float = 0.30,
    min_len_ratio: float = 0.30,
    max_len_ratio: float = 2.00,
) -> dict:
    """把精炼结果安全落盘。返回状态 dict。

    护栏:
      - 空结果 -> 拒绝
      - axiom 缺失 (存在公理 被改掉) -> 拒绝 (质量门)
      - 与原文相似度 < min_similarity (可疑大段删除) -> 拒绝 (仅当有旧文时)
      - 长度比超出 [min_len_ratio, max_len_ratio] -> 拒绝 (仅当有旧文时)
      - 无实质变化 -> 跳过 (no_change)
      - 否则: 备份 -> 写盘 (加 REFINED 标记) -> git 留版本
    """
    status: dict = {"written": False, "skipped": False, "rejected": None}
    if not refined or not refined.strip():
        status["rejected"] = "empty"
        logger.warning("refinement rejected: empty output")
        return status

    # 铁律区质量门: <rules> 块内容必须逐字等于 axiom (程序已 _enforce_rules, 此处二次校验)
    if axiom and axiom.strip():
        rm = re.search(r"(?s)<rules>(.*?)</rules>", refined)
        if not rm or rm.group(1).strip() != axiom.strip():
            status["rejected"] = "axiom_missing"
            logger.warning("refinement rejected: axiom not preserved in <rules>")
            return status

    cur = current if current is not None else (soul_path.read_text(encoding="utf-8") if soul_path.exists() else "")
    if cur.strip() == refined.strip():
        status["skipped"] = "no_change"
        logger.info("refinement skipped: no meaningful change")
        return status

    # 相似度 / 长度比门仅在有旧文对照时生效 (首次写入无基线, 直接放行)
    sim = 1.0  # 默认 (无旧文对照时) 视为完全新增, 不触发低相似拒绝
    if cur.strip():
        # 已是精炼稿 (含 REFINED 标记) 用严格阈值; 首次/脏稿放宽。
        # 脏稿(盲抄)->干净分区本就该大变, 0.30 会误杀首次收敛 (实测 sim=0.26 被拒)。
        is_refined = REFINED_MARKER in cur
        eff_min_sim = min_similarity if is_refined else max(min_similarity - 0.15, 0.10)
        sim = _similarity(cur, refined)
        if sim < eff_min_sim:
            status["rejected"] = f"low_similarity={sim:.2f}"
            logger.warning(
                "refinement rejected: similarity %.2f < %.2f (suspicious deletion; base=%s)",
                sim, eff_min_sim, "refined" if is_refined else "raw",
            )
            return status
        lr = len(refined) / max(len(cur), 1)
        if lr < min_len_ratio or lr > max_len_ratio:
            status["rejected"] = f"len_ratio={lr:.2f}"
            logger.warning("refinement rejected: len_ratio %.2f out of [%.2f, %.2f]", lr, min_len_ratio, max_len_ratio)
            return status

    if dry_run:
        status["dry_run"] = True
        logger.info("dry-run: would write %d chars", len(refined))
        return status

    # 备份
    if backup and soul_path.exists():
        bak = soul_path.with_suffix(f".md.bak.{datetime.now():%Y%m%d-%H%M%S}")
        try:
            shutil.copy2(soul_path, bak)
            status["backup"] = str(bak)
        except Exception as e:  # noqa: BLE001
            logger.warning("backup failed (non-fatal): %s", e)

    # 写盘 (加 REFINED 标记, 置于顶部注释后)
    body = refined.rstrip() + "\n"
    if REFINED_MARKER not in body:
        body = body.replace("<!-- AUTO-SYNCED", f"{REFINED_MARKER}\n<!-- AUTO-SYNCED", 1)
        if REFINED_MARKER not in body:
            body = REFINED_MARKER + "\n" + body
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(body, encoding="utf-8")
    status["written"] = True
    status["bytes"] = len(body)
    logger.info("SOUL.md refined (%d bytes, sim=%.2f) -> %s", len(body), sim, soul_path)

    # git 留版本 (best-effort, 非致命)
    try:
        if subprocess.run(["git", "-C", str(IKAROS_ROOT), "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True, timeout=10).returncode == 0:
            subprocess.run(["git", "-C", str(IKAROS_ROOT), "add", str(soul_path)],
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["git", "-C", str(IKAROS_ROOT), "commit", "-m",
                            f"soul_refine: refine SOUL.md ({datetime.now():%Y-%m-%d %H:%M})"],
                           capture_output=True, text=True, timeout=20)
            status["git_committed"] = True
    except Exception as e:  # noqa: BLE001
        logger.debug("git commit skipped: %s", e)
    return status


def run_refine(
    llm=default_llm,
    *,
    draft: str | None = None,
    axiom: str | None = None,
    dry_run: bool = False,
    soul_path: Path = SOUL_PATH,
) -> dict:
    """编排一次精炼。draft/axiom 可注入 (测试用); 否则自动收集。"""
    if draft is None or axiom is None:
        d, a = collect_evidence()
        draft = draft if draft is not None else d
        axiom = axiom if axiom is not None else a
    refined = refine_with_llm(draft, axiom, llm)
    return apply_refinement(refined, axiom=axiom, soul_path=soul_path, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser(description="LLM-scheduled refinement of Ikaros SOUL.md")
    ap.add_argument("--dry-run", action="store_true", help="只打印精炼结果, 不落盘")
    ap.add_argument("--run", action="store_true", help="执行一次精炼并落盘")
    args = ap.parse_args()
    if not args.dry_run and not args.run:
        args.run = True
    status = run_refine(dry_run=args.dry_run)
    if args.dry_run:
        # 也把精炼结果打到 stdout 便于审阅
        d, a = collect_evidence()
        print(refine_with_llm(d, a))
    logger.info("done: %s", status)


if __name__ == "__main__":
    main()
