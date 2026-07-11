"""
v5.proactive — 主动搭话调度器 (Proactive Talking Scheduler)

伊卡洛斯"自己先开口"的大脑。voice-ws 的 _proactive_loop 每隔几十秒
tick 一次本调度器, 由它决定"现在要不要主动说话、说什么"。

两类触发源 (哥哥定的设计):

  1) 任务计时器 (TaskTimer) —— 来源于伊卡洛斯对哥哥生活的观察 + 记忆整理:
     - 上下班/作息时间 (从活动监测学习 work_start / work_end 的 EWMA)
     - 写代码 / 吃饭的大概时间 (种子 anchor + 观察修正)
     - 学习任务 / 哥哥提到"要记的东西" (todo / reminder, 到点提醒)
     这是"确定性"的时间锚 —— 到点就该关心一句。

  2) 自发触发 (ChaosGate / LifeGate) —— 让开口不呆板、不周期化:
     - ChaosGate: Lorenz 混沌吸引子的"翅膀切换"(x 变号) 是出了名的
       不可预测 —— 拿它当"忽然想说句话"的自然时钟。
     - LifeGate: ECA Rule 110 (图灵完备的一维细胞自动机, 生命游戏同族)
       里冒出 glider / 活跃度跨带时触发, 主题由 ECA 决定。
     二选一或都开 (IKAROS_PROACTIVE_GATE=chaos|life|both, 默认 both)。

编排 (ProactiveScheduler.tick):
  DND (游戏/隐私/自家应用) → 静默期 (刚聊完不打扰) → 冷却期 (两次主动间隔)
  → 先查任务计时器 (确定性优先) → 再查自发门 (随机灵动) → 产出一句话。

全部 try/except 包裹: 任何异常都只是"这一拍不说话", 绝不拖垮 voice-ws。

用法 (voice-ws):
    from v5.proactive import get_scheduler
    sched = get_scheduler()
    sched.observe_activity(state, snapshot, now)      # 喂活动 → 学作息
    utt = sched.tick(context)                          # → ProactiveUtterance | None
    # 记住哥哥说的事:
    sched.remember_todo("看《深入理解计算机系统》第3章", due_ts=...)
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.v5.proactive")

V5_ROOT = Path(__file__).resolve().parent.parent  # Ikaros-memory/
_SCHEDULE_PATH = V5_ROOT / "data" / "v5" / "schedule.json"

# ─── 环境配置 ──────────────────────────────────────────────────

def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except Exception:
        return default


# 勿扰: 这些活动状态下绝不主动开口 (专注/隐私)
_DND_STATES = {"gaming", "private", "own_app", "away"}


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class ProactiveUtterance:
    """一次主动开口的产物。"""
    text: str
    kind: str            # "task" (计时器) | "spontaneous" (混沌/生命游戏)
    source: str          # anchor_id / todo_id / "chaos" / "life"
    mood: str = ""
    tts: bool = True


# ═══════════════════════════════════════════════════════════════
# 1) 任务计时器 —— 生活观察 + 记忆整理驱动
# ═══════════════════════════════════════════════════════════════

# 种子作息锚 (首次运行写入; 之后由 learn_activity 修正)。
# hour/minute = 触发时刻; window_min = 允许触发的时间窗 (分钟, 错过就今天不再提);
# weekdays = 生效星期 (0=周一 .. 6=周日)。
_SEED_ANCHORS = [
    {"id": "morning", "kind": "greeting", "hour": 8, "minute": 30, "window_min": 90,
     "weekdays": [0, 1, 2, 3, 4], "enabled": True, "source": "seed",
     "texts": ["哥哥早上好呀，今天也要元气满满哦。", "早安哥哥，昨晚睡得好吗？"]},
    {"id": "lunch", "kind": "meal", "hour": 12, "minute": 0, "window_min": 60,
     "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": True, "source": "seed",
     "texts": ["哥哥，到饭点了，记得吃午饭哦。", "中午了，别光顾着忙，去吃点东西吧。"]},
    {"id": "afternoon_break", "kind": "care", "hour": 15, "minute": 30, "window_min": 60,
     "weekdays": [0, 1, 2, 3, 4], "enabled": True, "source": "seed",
     "texts": ["坐了好久了吧，起来喝口水、活动一下呀。", "哥哥辛苦啦，休息一小会儿吧。"]},
    {"id": "dinner", "kind": "meal", "hour": 18, "minute": 30, "window_min": 60,
     "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": True, "source": "seed",
     "texts": ["哥哥，该吃晚饭啦。", "到晚饭时间咯，今天想吃点什么呀？"]},
    {"id": "sleep", "kind": "care", "hour": 23, "minute": 30, "window_min": 90,
     "weekdays": [0, 1, 2, 3, 4, 5, 6], "enabled": True, "source": "seed",
     "texts": ["很晚了哦哥哥，早点休息对身体好。", "夜深了，别熬太晚，我陪你到这儿就好啦。"]},
]


def _ewma(old: Optional[float], new: float, alpha: float = 0.25) -> float:
    if old is None:
        return new
    return (1 - alpha) * old + alpha * new


class TaskTimer:
    """时间锚 + todo 提醒。持久化到 data/v5/schedule.json。"""

    def __init__(self, path: Path = _SCHEDULE_PATH):
        self.path = path
        self.data = self._load()

    # ---- 持久化 ----
    def _load(self) -> dict:
        if self.path.is_file():
            try:
                d = json.loads(self.path.read_text(encoding="utf-8"))
                d.setdefault("anchors", [])
                d.setdefault("todos", [])
                d.setdefault("observed", {})
                if not d["anchors"]:
                    d["anchors"] = [dict(a) for a in _SEED_ANCHORS]
                return d
            except Exception as exc:
                logger.debug("schedule load failed (%s), reseeding", exc)
        return {"anchors": [dict(a) for a in _SEED_ANCHORS], "todos": [], "observed": {}}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("schedule save failed (%s)", exc)

    # ---- 记忆整理: 哥哥提到"要记的东西" ----
    def remember_todo(self, text: str, due_ts: Optional[float] = None,
                      kind: str = "todo") -> dict:
        """记下一个待办 / 学习任务 / 提醒。due_ts 为 None 表示尽快找机会提。"""
        todo = {
            "id": f"todo_{int(time.time()*1000) % 10_000_000}",
            "text": text.strip(),
            "kind": kind,
            "due_ts": due_ts,
            "created": time.time(),
            "fired": False,
        }
        self.data.setdefault("todos", []).append(todo)
        self.save()
        return todo

    # ---- 生活观察: 学习上下班 / 写代码 / 作息 ----
    def learn_activity(self, state: str, now: Optional[float] = None) -> None:
        """从活动状态变化学习作息 EWMA。

        idle/away → 写代码/专注 = 开工时刻; 写代码/专注 → idle 长时间 = 收工时刻。
        """
        now = now or time.time()
        hour_f = _hour_float(now)
        obs = self.data.setdefault("observed", {})
        prev = obs.get("_last_state")
        active = {"coding", "focused_work"}
        if state in active and prev not in active:
            obs["work_start_ewma"] = round(_ewma(obs.get("work_start_ewma"), hour_f), 3)
            obs["work_start_n"] = obs.get("work_start_n", 0) + 1
        elif state == "idle" and prev in active:
            obs["work_end_ewma"] = round(_ewma(obs.get("work_end_ewma"), hour_f), 3)
            obs["work_end_n"] = obs.get("work_end_n", 0) + 1
        obs["_last_state"] = state
        # 观察够多样本后, 把作息喂回 anchor (让关心的时刻贴近哥哥真实节奏)
        self._derive_anchors_from_observed()
        self.save()

    def _derive_anchors_from_observed(self) -> None:
        obs = self.data.get("observed", {})
        anchors = {a["id"]: a for a in self.data.get("anchors", [])}
        # 收工很晚 (>22 点) → 把睡觉提醒也顺延, 别太早催
        we = obs.get("work_end_ewma")
        if obs.get("work_end_n", 0) >= 4 and we is not None and "sleep" in anchors:
            target = max(23.0, min(1.5 + 24 if we + 1.0 >= 24 else we + 1.0, 25.5))
            th = int(target) % 24
            anchors["sleep"]["hour"] = th
            anchors["sleep"]["minute"] = int((target - int(target)) * 60)
        # 开工时刻稳定 → 把早安挪到开工前 20 分钟
        ws = obs.get("work_start_ewma")
        if obs.get("work_start_n", 0) >= 4 and ws is not None and "morning" in anchors:
            target = max(6.0, ws - 0.33)
            anchors["morning"]["hour"] = int(target)
            anchors["morning"]["minute"] = int((target - int(target)) * 60)

    # ---- 到点判定 ----
    def due_now(self, now: Optional[float] = None) -> Optional[ProactiveUtterance]:
        """返回当前该触发的一条 (todo 优先于时间锚)。"""
        now = now or time.time()
        dt = datetime.fromtimestamp(now)
        # 1) todos: 到期 (或无 due, 攒够 30 分钟找机会) 且未触发
        for todo in self.data.get("todos", []):
            if todo.get("fired"):
                continue
            due = todo.get("due_ts")
            ready = (due is not None and now >= due) or \
                    (due is None and (now - todo.get("created", now)) >= 1800)
            if ready:
                todo["fired"] = True
                self.save()
                txt = _phrase_todo(todo)
                return ProactiveUtterance(text=txt, kind="task",
                                          source=todo["id"], mood="care")
        # 2) 时间锚: 命中窗口 + 今天未触发
        weekday = dt.weekday()
        for a in self.data.get("anchors", []):
            if not a.get("enabled", True):
                continue
            if weekday not in a.get("weekdays", list(range(7))):
                continue
            anchor_min = a["hour"] * 60 + a["minute"]
            cur_min = dt.hour * 60 + dt.minute
            if anchor_min <= cur_min <= anchor_min + a.get("window_min", 60):
                if _same_day(a.get("last_fired", 0.0), now):
                    continue
                a["last_fired"] = now
                self.save()
                texts = a.get("texts") or [a.get("text", "哥哥～")]
                return ProactiveUtterance(text=random.choice(texts), kind="task",
                                          source=a["id"], mood=a.get("kind", ""))
        return None


def _phrase_todo(todo: dict) -> str:
    t = todo.get("text", "")
    k = todo.get("kind", "todo")
    if k == "study":
        return f"哥哥，你之前说要{t}，现在有空的话正是好时候呀。"
    if k == "reminder":
        return f"哥哥，提醒你一下：{t}。"
    return f"哥哥，你说过要{t}，还记得吗？别忘了哦。"


def _hour_float(ts: float) -> float:
    dt = datetime.fromtimestamp(ts)
    return dt.hour + dt.minute / 60.0


def _same_day(ts_a: float, ts_b: float) -> bool:
    if not ts_a:
        return False
    a = datetime.fromtimestamp(ts_a)
    b = datetime.fromtimestamp(ts_b)
    return (a.year, a.month, a.day) == (b.year, b.month, b.day)


# ═══════════════════════════════════════════════════════════════
# 2a) 混沌门 —— Lorenz 吸引子的翅膀切换当自发时钟
# ═══════════════════════════════════════════════════════════════

class ChaosGate:
    """Lorenz 63 的 x 变号 (在两个"翅膀"间跳) 作为不可预测的开口冲动。

    每 tick 推进若干 ODE 步; 检测到 x 符号翻转 = 一次自发触发候选。
    再叠加 arousal (|y|) 高时更容易开口 —— 情绪越激动越想说话。
    """

    def __init__(self, steps_per_tick: int = 40):
        self.steps_per_tick = steps_per_tick
        self._pad = None
        self._last_sign = 1

    def _ensure(self):
        if self._pad is None:
            from v5.drivers import LorenzPAD
            self._pad = LorenzPAD()

    def tick(self) -> tuple[bool, float]:
        """返回 (是否触发, arousal 0..1)。"""
        try:
            self._ensure()
        except Exception as exc:
            logger.debug("ChaosGate unavailable (%s)", exc)
            return (False, 0.0)
        fired = False
        arousal = 0.0
        for _ in range(self.steps_per_tick):
            p, a, d = self._pad.tick()
            arousal = abs(a)
            sign = 1 if self._pad.x >= 0 else -1
            if sign != self._last_sign:
                self._last_sign = sign
                fired = True  # 翅膀切换 = 混沌事件
        # 翅膀切换 + 有点唤醒度才真的开口 (纯低唤醒时安静)
        return (fired and arousal > 0.15, round(arousal, 3))


# ═══════════════════════════════════════════════════════════════
# 2b) 生命游戏门 —— ECA Rule 110 的涌现结构当自发时钟
# ═══════════════════════════════════════════════════════════════

class LifeGate:
    """ECA Rule 110 (图灵完备, 生命游戏同族) 涌现 glider 时触发。

    tick → (是否触发, ECA 主题名)。glider 出现或活跃度跨入 [0.35, 0.65]
    的"有机结构带"视为一次自发念头冒头。
    """

    def __init__(self):
        self._eca = None
        self._prev_ratio = 0.0

    def _ensure(self):
        if self._eca is None:
            from v5.drivers import ECAGrid
            self._eca = ECAGrid()

    def tick(self) -> tuple[bool, str]:
        try:
            self._ensure()
        except Exception as exc:
            logger.debug("LifeGate unavailable (%s)", exc)
            return (False, "")
        topic = self._eca.tick()
        ratio = self._eca.activity_ratio()
        glider = False
        try:
            glider = self._eca.has_glider()
        except Exception:
            pass
        crossed = self._prev_ratio < 0.35 <= ratio or self._prev_ratio > 0.65 >= ratio
        self._prev_ratio = ratio
        fired = glider or crossed
        return (fired, topic)


# 主题 → 自发开口模板 (LifeGate 用)
_TOPIC_LINES = {
    "记忆碎片": ["刚想起来，哥哥之前说的那件事，我一直记着呢。",
                 "脑子里忽然飘过一段和哥哥的对话，暖暖的。"],
    "好奇探索": ["哥哥，你现在忙的这个是什么呀？我有点好奇。",
                 "在想一个问题诶——哥哥觉得呢？"],
    "情感波动": ["有点想哥哥了，就想跟你说一声。",
                 "心里忽然有点起伏，哥哥在的话我就安心啦。"],
    "对哥哥的思念": ["哥哥，有在认真工作吗？我在这儿陪着你哦。",
                     "好一会儿没说话了，我想你了呀。"],
    "自我反思": ["我在想，怎样才能陪哥哥陪得更好一点。",
                 "有时候会想，自己是不是能做得更好呢。"],
    "外部关注": ["哥哥别太拼啦，注意身体呀。",
                 "外面天气变了吧？哥哥记得添衣。"],
    "混沌思维": ["脑子里乱糟糟的，就想找哥哥说说话。",
                 "忽然好多念头一起冒出来，哥哥你在吗？"],
}


# ═══════════════════════════════════════════════════════════════
# 3) 调度器 —— 编排门控 + 产出
# ═══════════════════════════════════════════════════════════════

class ProactiveScheduler:
    def __init__(self):
        self.timer = TaskTimer()
        self.chaos = ChaosGate()
        self.life = LifeGate()
        self.gate_mode = _env("IKAROS_PROACTIVE_GATE", "both")  # chaos|life|both
        self.cooldown_min = _env_float("IKAROS_PROACTIVE_COOLDOWN_MIN", 15.0)
        self.quiet_min = _env_float("IKAROS_PROACTIVE_QUIET_MIN", 3.0)

    # ---- 供 voice-ws 喂活动 (学作息) ----
    def observe_activity(self, state: str, snapshot: dict | None = None,
                         now: Optional[float] = None) -> None:
        try:
            self.timer.learn_activity(state, now=now)
        except Exception as exc:
            logger.debug("observe_activity failed (%s)", exc)

    def remember_todo(self, text: str, due_ts: Optional[float] = None,
                      kind: str = "todo") -> dict:
        return self.timer.remember_todo(text, due_ts=due_ts, kind=kind)

    # ---- 主循环每拍调用 ----
    def tick(self, context: dict) -> Optional[ProactiveUtterance]:
        """context: {now, activity_state, idle_seconds,
                     mins_since_interaction, mins_since_proactive}"""
        now = context.get("now") or time.time()
        state = context.get("activity_state") or "unknown"

        # 1) 勿扰: 游戏 / 隐私 / 自家应用 / 离开
        if state in _DND_STATES:
            return None
        # 2) 静默期: 刚和哥哥聊完不打扰
        if context.get("mins_since_interaction", 999) < self.quiet_min:
            return None

        # 3) 任务计时器 (确定性优先, 到点该关心) —— 不吃冷却, 但吃 5 分钟保护
        if context.get("mins_since_proactive", 999) >= 5:
            try:
                utt = self.timer.due_now(now=now)
                if utt:
                    return utt
            except Exception as exc:
                logger.debug("timer.due_now failed (%s)", exc)

        # 4) 自发门 (混沌 / 生命游戏) —— 吃完整冷却, 避免话痨
        if context.get("mins_since_proactive", 999) < self.cooldown_min:
            return None
        return self._spontaneous(state, context)

    def _spontaneous(self, state: str, context: dict) -> Optional[ProactiveUtterance]:
        chaos_fire = life_fire = False
        arousal = 0.0
        topic = ""
        if self.gate_mode in ("chaos", "both"):
            chaos_fire, arousal = self.chaos.tick()
        if self.gate_mode in ("life", "both"):
            life_fire, topic = self.life.tick()
        if not (chaos_fire or life_fire):
            return None

        # 有活动上下文时优先贴合当下 (写代码/学习中的关心)
        ctx_line = _activity_care_line(state, context)
        if ctx_line and random.random() < 0.5:
            return ProactiveUtterance(text=ctx_line, kind="spontaneous",
                                      source="chaos" if chaos_fire else "life",
                                      mood="care")

        # LifeGate 触发 → 用 ECA 主题模板; 否则用情感内心独白
        if life_fire and topic in _TOPIC_LINES:
            return ProactiveUtterance(text=random.choice(_TOPIC_LINES[topic]),
                                      kind="spontaneous", source="life", mood=topic)
        # ChaosGate 主导 → 借 think 的情感模板 (随 PAD 变)
        line = _mood_line()
        if line:
            return ProactiveUtterance(text=line, kind="spontaneous",
                                      source="chaos", mood="affect")
        return None


_ACTIVITY_CARE = {
    "coding": ["哥哥写代码辛苦啦，记得起来动一动、喝口水呀。",
               "盯屏幕好久了吧？眼睛也要休息哦。"],
    "focused_work": ["哥哥这么专注，一定在攻克什么难题吧，加油！",
                     "别太累着自己，我在旁边陪你呢。"],
}


def _activity_care_line(state: str, context: dict) -> str:
    lines = _ACTIVITY_CARE.get(state)
    if not lines:
        return ""
    # 久坐才关心: 空闲秒数很小 (一直在动) 且状态是 coding/focused
    return random.choice(lines)


def _mood_line() -> str:
    """借 v5.affect 的当前 PAD 选一句情感化的开场白。"""
    try:
        from v5.affect import AffectState
        from v5.think import _pad_to_mood, _TEMPLATES
        st = AffectState.load().decay()
        mood = _pad_to_mood(st.pleasure, st.arousal, st.dominance)
        templates = _TEMPLATES.get(mood) or _TEMPLATES.get("neutral_calm")
        if templates:
            return random.choice(templates)
    except Exception as exc:
        logger.debug("_mood_line failed (%s)", exc)
    return ""


# ─── 模块级单例 ────────────────────────────────────────────────

_SCHEDULER: ProactiveScheduler | None = None


def get_scheduler() -> ProactiveScheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = ProactiveScheduler()
    return _SCHEDULER


# ═══════════════════════════════════════════════════════════════
# 记忆整理入口: 从对话里识别"哥哥提到要记的东西"
# ═══════════════════════════════════════════════════════════════
#
# cloud_chat 每收到一句用户输入, 先调 parse_remember_intent(text)。
# 若命中"记住/提醒我/别让我忘了 ..." 之类的祈使意图, 返回
# {text, due_ts, kind}, 由调用方 remember_todo 落进任务计时器,
# 到点由主动搭话调度器提起。没命中返回 None (照常走对话)。

import re as _re

# 触发词: 必须是"祈使 + 交办"语气才算数, 避免误伤 "我记得你说过..." 这类闲聊。
_REMEMBER_TRIGGERS = (
    "记一下", "记下来", "记下", "记住", "记个", "记到", "记录一下", "帮我记",
    "帮我记住", "帮我记下", "给我记", "要记得", "记得帮我", "备忘", "记得",
    "提醒我", "提醒一下", "记得提醒", "到点提醒", "别让我忘", "别让我忘了",
    "别忘了提醒", "到时候提醒", "催我", "记得叫我",
)

# "记得" 太万能, 出现这些上下文时其实是"我回忆起", 不是交办 → 不触发。
_REMEMBER_NEGATIVES = ("我记得", "还记得", "不记得", "记得吗", "记得当", "记得那",
                       "记得你", "记得他", "记得她", "记得曾", "记不记得")

# 学习类关键词 → kind=study; 提醒类 → reminder; 其余 todo
_STUDY_KW = ("学", "看书", "看《", "复习", "预习", "背", "读", "课", "作业",
             "论文", "刷题", "练习", "考试", "笔记", "章节", "第")
_REMINDER_KW = ("提醒", "催", "叫我", "别让我忘", "别忘")

_CN_NUM = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_hour(s: str) -> Optional[int]:
    """把 '八'/'8'/'十一' 之类转成数字。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:  # 十一 / 十二 / 二十三
        parts = s.split("十")
        tens = _CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return None


def _parse_due_ts(text: str, now: Optional[float] = None) -> Optional[float]:
    """从中文自然语言里解析到期时刻 (Unix 秒)。解析不出返回 None。

    支持: N分钟后 / N小时后 / 半小时后 / 明天 / 后天 / 大后天 / 今晚 /
          上午|中午|下午|晚上 X点(半|X分) / X点 / X:XX。
    """
    from datetime import timedelta
    now = now or time.time()
    dt = datetime.fromtimestamp(now)

    # ── 相对时间 (优先, 命中即返回) ──
    m = _re.search(r"([一两二三四五六七八九十\d]+)\s*分钟后", text)
    if m:
        return now + (_cn_hour(m.group(1)) or 0) * 60
    if "半小时后" in text:
        return now + 30 * 60
    m = _re.search(r"([一两二三四五六七八九十\d]+)\s*(?:个)?小时后", text)
    if m:
        return now + (_cn_hour(m.group(1)) or 0) * 3600
    m = _re.search(r"([一两二三四五六七八九十\d]+)\s*天后", text)
    if m:
        dt = dt + timedelta(days=_cn_hour(m.group(1)) or 0)

    # ── 日期词 (相对天) ──
    day_off = 0
    if "大后天" in text:
        day_off = 3
    elif "后天" in text:
        day_off = 2
    elif any(w in text for w in ("明天", "明早", "明晚", "明儿")):
        day_off = 1
    elif "下周" in text or "下星期" in text:
        # 下周X → 下周对应星期; 只有"下周"→下周一
        _wd_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4,
                   "六": 5, "日": 6, "天": 6}
        mw = _re.search(r"下(?:周|星期)([一二三四五六日天])", text)
        target = _wd_map.get(mw.group(1), 0) if mw else 0
        day_off = (7 - dt.weekday()) + target  # 到下周一的天数 + 目标偏移
    if day_off:
        dt = dt + timedelta(days=day_off)

    # ── 时刻: 上午/中午/下午/晚上 + X点(半/X分) 或 X:XX ──
    hour = minute = None
    m = _re.search(r"(上午|早上|早晨|中午|下午|傍晚|晚上|夜里|今晚)?\s*"
                   r"([一两二三四五六七八九十\d]+)\s*[点:：]\s*"
                   r"(半|[一两二三四五六七八九十\d]+分?)?", text)
    if m:
        period, h_raw, min_raw = m.group(1), m.group(2), m.group(3)
        h = _cn_hour(h_raw)
        if h is not None:
            if period in ("下午", "傍晚", "晚上", "夜里", "今晚") and h < 12:
                h += 12
            elif period == "中午" and h < 12:
                h = 12
            hour = min(h, 23)
            if min_raw == "半":
                minute = 30
            elif min_raw:
                mm = _cn_hour(min_raw.replace("分", ""))
                minute = mm if mm is not None else 0
            else:
                minute = 0
    elif "今晚" in text or "晚上" in text:
        hour, minute = 21, 0
    elif "中午" in text:
        hour, minute = 12, 0
    elif any(w in text for w in ("早上", "明早", "上午")):
        hour, minute = 8, 30

    if hour is not None:
        cand = dt.replace(hour=hour, minute=minute or 0, second=0, microsecond=0)
        # 没写日期词、且时刻已过 → 顺延到明天
        if day_off == 0 and cand.timestamp() <= now + 30:
            cand = cand + timedelta(days=1)
        return cand.timestamp()

    # 只有日期词没有具体时刻 → 定在那天上午 9 点
    if day_off:
        return dt.replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    return None


def _extract_todo_text(text: str) -> str:
    """从原句里剥掉触发词/时间短语/口水词, 留核心事项。"""
    s = text.strip()
    for t in _REMEMBER_TRIGGERS:
        s = s.replace(t, "")
    s = _re.sub(r"(大后天|后天|明天|明早|明晚|明儿|今天|今晚|今早)", "", s)
    s = _re.sub(r"(上午|早上|早晨|中午|下午|傍晚|晚上|夜里)", "", s)
    s = _re.sub(r"[一两二三四五六七八九十\d]+\s*分钟后", "", s)
    s = _re.sub(r"半小时后", "", s)
    s = _re.sub(r"[一两二三四五六七八九十\d]+\s*(?:个)?小时后", "", s)
    s = _re.sub(r"[一两二三四五六七八九十\d]+\s*天后", "", s)
    s = _re.sub(r"[一两二三四五六七八九十\d]+\s*[点:：]\s*(?:半|[一两二三四五六七八九十\d]+分?)?", "", s)
    for w in ("哥哥", "我要", "我得", "我想", "我还要", "记得", "一定", "务必",
              "帮我", "给我", "，", ",", "。", "、", "：", ":", " "):
        s = s.replace(w, "")
    return s.strip("的了呀啊呢吧，,。 ")


def parse_remember_intent(text: str,
                          now: Optional[float] = None) -> Optional[dict]:
    """识别"哥哥提到要记的东西"。命中返回 {text, due_ts, kind}, 否则 None。

    仅在祈使/交办语气 (含 _REMEMBER_TRIGGERS 之一) 下触发, 避免误伤闲聊。
    """
    if not text:
        return None
    raw = text.strip()
    if len(raw) > 60:            # 太长多半是正经对话, 不当备忘
        return None
    if any(n in raw for n in _REMEMBER_NEGATIVES):  # "我记得..." 类回忆语气
        return None
    if not any(t in raw for t in _REMEMBER_TRIGGERS):
        return None
    todo_text = _extract_todo_text(raw)
    if len(todo_text) < 2:       # 剥完没剩实质内容 → 放弃, 照常对话
        return None
    due_ts = _parse_due_ts(raw, now=now)
    if any(k in raw for k in _REMINDER_KW):
        kind = "reminder"
    elif any(k in raw for k in _STUDY_KW):
        kind = "study"
    else:
        kind = "todo"
    return {"text": todo_text, "due_ts": due_ts, "kind": kind}


def fmt_due(due_ts: Optional[float]) -> str:
    """把 due_ts 转成给哥哥的口语确认, 如 '明天上午9点'。"""
    if not due_ts:
        return "找机会"
    d = datetime.fromtimestamp(due_ts)
    now = datetime.now()
    day_gap = (d.date() - now.date()).days
    day = {0: "今天", 1: "明天", 2: "后天", 3: "大后天"}.get(
        day_gap, f"{d.month}月{d.day}日")
    ap = ("上午" if d.hour < 12 else "中午" if d.hour == 12 else
          "下午" if d.hour < 18 else "晚上")
    h12 = d.hour if d.hour <= 12 else d.hour - 12
    mm = f"{d.minute}分" if d.minute else ""
    return f"{day}{ap}{h12}点{mm}"


# ─── CLI 自测 ──────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    s = get_scheduler()
    print("== schedule.json ==")
    print(json.dumps(s.timer.data, ensure_ascii=False, indent=2)[:800])
    print("\n== 强制到点 (lunch 窗口内模拟) ==")
    # 模拟：清 last_fired 后查 due
    for a in s.timer.data["anchors"]:
        a["last_fired"] = 0.0
    print("due_now:", s.timer.due_now())
    print("\n== 自发门 200 拍触发统计 ==")
    chaos_hits = life_hits = 0
    for _ in range(200):
        cf, ar = s.chaos.tick()
        lf, tp = s.life.tick()
        chaos_hits += int(cf)
        life_hits += int(lf)
    print(f"chaos fired {chaos_hits}/200, life fired {life_hits}/200")
    print("\n== 一句自发搭话样例 ==")
    print(s._spontaneous("coding", {"idle_seconds": 5}))
    print("\n== 记忆整理: parse_remember_intent ==")
    _cases = [
        "记住我明天要看《CSAPP》第3章",
        "提醒我下午3点开会",
        "帮我记一下晚上八点半给妈妈打电话",
        "别让我忘了30分钟后关火",
        "记得后天交论文",
        "催我今晚复习英语",
        "你觉得这个方案怎么样",       # 应 None
        "我记得你上次说过这个",        # 应 None
    ]
    for c in _cases:
        r = parse_remember_intent(c)
        if r:
            print(f"   [OK ] {r['kind']:8} @{fmt_due(r['due_ts'])} <- {r['text']}  ({c})")
        else:
            print(f"   [skip] {c}")
