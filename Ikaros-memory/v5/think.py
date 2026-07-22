# -*- coding: utf-8 -*-
# See docs/scripts/Ikaros-memory/v5/think.md
from __future__ import annotations

import json
import logging
import os
import random
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ikaros.v5.think")

# ─── Paths ──────────────────────────────────────────────────────

V5_ROOT = Path(__file__).resolve().parent.parent  # Ikaros-memory/
_LATEST_PATH = V5_ROOT / "data" / "v5" / "latest_thought.json"
# Subconscious stream — lightweight inner musings generated every 2-3 min by local model
_SUBCONSCIOUS_PATH = V5_ROOT / "data" / "v5" / "subconscious.json"

# ─── Persistent runtime state (module-level, for SIGTERM graceful stop + PAD snapshots) ───
_stop_event: "threading.Event | None" = None
_last_pad: dict = {}  # last PAD snapshot for intent-driven PAD change detection

# ─── ECA (Elementary Cellular Automaton) driver (lazy singleton) ────
_eca: object | None = None     # ECAGrid instance


def _write_latest(text: str, kind: str, theme: str = "",
                  curiosity: float = 0.0) -> None:
    """Write the latest thought to data/v5/latest_thought.json.

    Compatible with metacog._write_latest format so the monitoring panel
    (Rust read_ikaros_state → Vue) can read it directly.
    Uses json_lock to prevent concurrent write corruption.
    """
    try:
        from v5.self_model import json_lock
        p = _LATEST_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / (p.name + f".tmp.{os.getpid()}")
        payload = {
            "text": text,
            "kind": kind,
            "theme": theme,
            "curiosity": round(curiosity, 3),
            "ts": time.time(),
        }
        with json_lock(p):
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, p)
    except Exception as exc:
        logger.debug("think _write_latest failed: %s", exc)


def _pad_to_mood(p: float, a: float, d: float) -> str:
    """PAD → mood category label."""
    # Sleepy first (extremely low arousal)
    if a < -0.4:
        return "any_sleepy"
    # Dominance dimension
    if d > 0.4:
        return "dominant"
    if d < -0.3:
        # Low dominance + affect
        if p > 0.3:
            return "joyful_calm"
        if p < -0.2:
            return "sad_calm"
        return "submissive"
    # P × A matrix
    if p > 0.3:
        if a > 0.2:
            return "joyful_aroused"
        if a > -0.2:
            return "joyful_alert"
        return "joyful_calm"
    if p < -0.2:
        if a > 0.2:
            return "sad_aroused"
        if a > -0.2:
            return "sad_alert"
        return "sad_calm"
    # Neutral P
    if a > 0.2:
        return "neutral_alert"
    if a > -0.2:
        return "neutral_alert"
    return "neutral_calm"


# ─── Core API ─────────────────────────────────────────────────

@dataclass
class Thought:
    """A single inner thought (used by consumer code)."""
    text: str
    mood: str
    intensity: float  # 0~1, intensity of the thought
    created: float
    surfaced: bool = False  # whether has been surfaced


def _intensity(p: float, a: float, d: float) -> float:
    """Compute emotional intensity from PAD (0~1)."""
    return min(1.0, (abs(p) + abs(a) + abs(d) * 0.5) / 2.0)


def check_pending() -> Thought | None:
    """Check for a pending thought in latest_thought.json.

    Used by cogno_5d.enrich() or enrich_reply().
    Reads from latest_thought.json (unified thinking output) and
    maps it to a Thought object. Does NOT delete the file — it
    stays for monitoring panel consumption.
    """
    if not _LATEST_PATH.is_file():
        return None
    try:
        data = json.loads(_LATEST_PATH.read_text(encoding="utf-8"))
        return Thought(
            text=data.get("text", ""),
            mood="neutral_calm",
            intensity=data.get("curiosity", 0.5),
            created=data.get("ts", time.time()),
            surfaced=False,
        )
    except Exception as exc:
        logger.debug("think: check_pending read failed (%s)", exc)
        return None


def clear_pending() -> None:
    """Force clear pending marker (does not read)."""
    try:
        _LATEST_PATH.unlink(missing_ok=True)
    except Exception:
        pass


# ─── Event-driven awakening (V5 #3) ─────────────────────────────

# Last activity state (for change detection)
_last_activity_state: str | None = None


def on_activity_change(activity_state: str, activity_phrase: str = "",
                       category: str = "") -> Optional[Thought]:
    """Trigger inner thought when activity state changes (event-driven awakening).

    Called by monitor when activity_state changes.
    Returns Thought or None (when change is not significant enough).
    """
    global _last_activity_state
    if activity_state == _last_activity_state:
        return None
    _last_activity_state = activity_state

    # Only react to significant transitions (idle -> coding / coding -> gaming etc.)
    _significant_transitions = {
        ("idle", "coding"), ("idle", "gaming"),
        ("idle", "focused_work"), ("coding", "gaming"),
        ("gaming", "coding"), ("focused_work", "idle"),
        ("away", "coding"), ("away", "focused_work"),
    }

    # Build transition pair
    old_state = _last_activity_state or "unknown"
    transition = (old_state, activity_state)

    # Activity start (idle -> active)
    if activity_state in ("coding", "gaming", "focused_work") and old_state in ("idle", "away", "unknown"):
        try:
            from v5.affect import AffectState
            state = AffectState.load().decay()
            p, a, d = state.pleasure, state.arousal, state.dominance
            label_map = {"coding": "coding", "gaming": "gaming", "focused_work": "working"}
            label = label_map.get(activity_state, "busy")
            intensity = _intensity(p, a, d)
            text = f"Brother started {label}."
            thought = Thought(text=text, mood=_pad_to_mood(p, a, d),
                            intensity=intensity, created=time.time())
            _store_thought(thought, p, a, d)
            return thought
        except Exception as exc:
            logger.debug("think: activity change failed (%s)", exc)
            return None

    # Activity end (active -> idle)
    if activity_state == "idle" and old_state in ("coding", "gaming", "focused_work"):
        try:
            from v5.affect import AffectState
            state = AffectState.load().decay()
            p, a, d = state.pleasure, state.arousal, state.dominance
            text = "Brother stopped. I wonder how he's doing."
            thought = Thought(text=text, mood=_pad_to_mood(p, a, d),
                            intensity=0.25, created=time.time())
            _store_thought(thought, p, a, d)
            return thought
        except Exception:
            return None

    return None


# ─── Genuine curiosity (V5 #9) ───────────────────────────────────

def curiosity_explore() -> Optional[Thought]:
    """When ECA topic = 'curious_explore', actively search memory for exploration.

    Uses AISDetectorSet to find high-novelty memories → generate exploratory thought.
    """
    try:
        from v5.drivers import get_ais_detector_set
        from v5 import store as store
        ais = get_ais_detector_set()   # Persistent singleton: negative selection/clonal evolution across calls
        # Get last 20 memories with PAD fingerprints
        with store.conn() as c:
            rows = c.execute(
                "SELECT id, content, pad_p, pad_a, pad_d FROM memory "
                "WHERE type NOT IN ('conversation', 'inner_monologue') "
                "  AND pad_p != 0.0 OR pad_a != 0.0 OR pad_d != 0.0 "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            return None
        memories = [(int(r["id"]), (float(r["pad_p"] or 0),
                       float(r["pad_a"] or 0), float(r["pad_d"] or 0)))
                    for r in rows]
        novelties = ais.tick(memories)
        if not novelties:
            return None
        # Get the most novel memory
        top_novelty, top_id = novelties[0]
        if top_novelty < 0.5:
            return None

        # Get that memory's content
        mem = store.get(top_id)
        if not mem:
            return None

        content_preview = (mem.content or "")[:120]
        import random as _r
        thoughts = [
            f"I just remembered something: {content_preview[:80]}... Why did that come to mind?",
            f"A memory flashed by: {content_preview[:80]}... Maybe it means something.",
            f"I suddenly thought of {content_preview[:60]}. That feels important to me.",
        ]
        text = _r.choice(thoughts)

        from v5.affect import AffectState
        state = AffectState.load().decay()
        p, a, d = state.pleasure, state.arousal, state.dominance
        intensity = min(1.0, 0.3 + top_novelty * 0.5)

        thought = Thought(text=text, mood=_pad_to_mood(p, a, d),
                        intensity=round(intensity, 3), created=time.time())
        _store_thought(thought, p, a, d)
        # V5.1: curiosity exploration result syncs self_model (shares same curiosity value with metacog)
        try:
            from v5.self_model import SelfModel
            sm = SelfModel.load()
            sm.set_curiosity(sm.get_curiosity() + 0.02)  # Slight bump when finding something new
            sm.save()
        except Exception:
            pass
        return thought
    except Exception as exc:
        logger.debug("think: curiosity exploration failed (%s)", exc)
        return None


def _store_thought(thought: Thought, p: float, a: float, d: float) -> None:
    """Store inner thought to V4 memory + write latest_thought.json."""
    try:
        from v5 import store as store
        store.store(thought.text, type="inner_monologue",
                 weight=min(1.0, 0.3 + thought.intensity * 0.4),
                 tags=f"inner_monologue,mood:{thought.mood},intensity:{thought.intensity:.2f}",
                 pad_p=round(p, 3), pad_a=round(a, 3), pad_d=round(d, 3))
    except Exception:
        pass
    if thought.intensity >= 0.35:
        try:
            _write_latest(thought.text, "mood", thought.mood, thought.intensity)
        except Exception:
            pass


# ─── Cron / CLI entry ──────────────────────────────────────────


def schedule(interval_minutes: int = 5) -> None:
    """Start unified thinking loop as a background thread (V5.1: 5min deep beat).

    Architecture (refactored 2026-07-12):
      - Removed inner_monologue (template-based independent loop)
      - metacog cycle acts as unified thinking output
      - metacog.cycle() output goes through latest_thought.json
      - When LLM unavailable, metacog._fallback_thought() generates placeholder
      - Curiosity detection + care detection merged into metacog beat
      - Subconscious stream kept at 2-3min lightweight musings (informational)
      - Hermes unified: inner monologue + reflection go through :9119 (2026-07-12)
    """
    import threading
    # ── Main thread signal registration + module-scope supervisor state (used by _unified_loop closure) ──
    global _stop_event
    _stop_event = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *a: _stop_event.set())
        signal.signal(signal.SIGINT, lambda *a: _stop_event.set())
    except (ValueError, AttributeError):
        pass  # Non-main thread cannot register signals; graceful stop degrades to process-level kill
    from v5 import supervisor_persist as sp
    poll_sec = max(30, min(120, interval_minutes * 60 // 15))  # Short polling: default ~60s

    # ── Start Hermes background client (reflection + inner monologue unified via :9119) ──
    try:
        from v5.hermes_client import start as _hermes_start, reflect as _hermes_reflect
        _hermes_start()
        logger.info("think: hermes_client worker started")

        # Monkey-patch call_llm_auto: reflection goes through Hermes first, fallback to original path
        import v5.reflect.llm_client as _llm
        _orig_call_llm_auto = _llm.call_llm_auto
        def _hermes_first_llm(system: str, user: str, max_tokens=600, temperature=0.7, **kw):
            try:
                prompt = f"<system>{system}</system>\n{user}"
                reply = _hermes_reflect(prompt, timeout=120)
                if not reply.startswith("(Hermes"):
                    from dataclasses import dataclass
                    @dataclass
                    class _R:
                        content: str = reply
                    return _R()
            except Exception:
                pass
            return _orig_call_llm_auto(system, user, max_tokens=max_tokens, temperature=temperature, **kw)
        _llm.call_llm_auto = _hermes_first_llm

        # Monkey-patch call_llm: only redirect deepseek → Hermes (distill self-review)
        # local keeps direct :8080 connection (consolidate batch extraction)
        _orig_call_llm = _llm.call_llm
        def _hermes_distill(system: str, user: str, *, provider="local", max_tokens=1024, temperature=0.0, timeout=None):
            if provider == "deepseek":
                try:
                    prompt = f"<system>{system}</system>\n{user}"
                    reply = _hermes_reflect(prompt, timeout=180)
                    if not reply.startswith("(Hermes"):
                        from dataclasses import dataclass
                        @dataclass
                        class _R2:
                            content: str = reply
                        return _R2()
                except Exception:
                    pass
            return _orig_call_llm(system, user, provider=provider,
                                  max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        _llm.call_llm = _hermes_distill
        logger.info("think: call_llm_auto patched → hermes first for reflection")
    except Exception as exc:
        logger.debug("think: hermes_client not available (%s)", exc)

    # ── Run lock (prevent overlapping metacog.cycle after timeout) ───────────
    _deep = {"future": None}   # Currently running deep-think future (held when still running after timeout)
    _SKIP = object()           # _deep_think_once overlap skip sentinel

    # ── Intent-driven decision (Reverie subconscious intent + proactive gating) ──
    def _should_deep_think(state, now):
        from v5 import supervisor_persist as sp
        since = now - (state.last_deep_think_ts or 0)
        if since >= sp.SOFT_CAP_SEC:
            return True, f"Soft cap exceeded {since:.0f}s (anti-starvation)"
        score = 0.0
        # 1) New memories
        try:
            from v5 import store as store
            mems = store.list_all(1)
            if mems and float(mems[0].created) > (state.last_deep_think_ts or 0):
                score += 0.4
        except Exception:
            pass
        # 2) Significant PAD change
        try:
            from v5.affect import AffectState
            st = AffectState.load().decay(now=now)
            p, a, d = st.pleasure, st.arousal, st.dominance
            lp = _last_pad.get("p"); la = _last_pad.get("a"); ld = _last_pad.get("d")
            if lp is not None:
                dp = abs(p - lp) + abs(a - la) + abs(d - ld)
                if dp > 0.5:
                    score += 0.3
            _last_pad["p"], _last_pad["a"], _last_pad["d"] = p, a, d
        except Exception:
            pass
        # 3) High curiosity
        try:
            from v5.self_model import SelfModel
            if SelfModel.load().get_curiosity() > 0.6:
                score += 0.2
        except Exception:
            pass
        # 4) Upcoming task due
        try:
            from v5.proactive import get_scheduler
            for it in getattr(get_scheduler(), "_items", []):
                if it.get("due_ts", 0) and it["due_ts"] <= now:
                    score += 0.3
                    break
        except Exception:
            pass
        state.last_intent_score = score
        if score >= 0.5:
            return True, f"Intent score {score:.2f}"
        return False, f"Intent score {score:.2f} insufficient"

    # ── Single deep think with hard timeout + run lock (strict-agent-loop style) ──
    def _deep_think_once(state, now, timeout=120):
        import concurrent.futures
        import v5.metacog as metacog
        prev = _deep["future"]
        if prev is not None and not prev.done():
            logger.debug("deep think: previous still running in background, skip to avoid overlap")
            return _SKIP
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(metacog.cycle)
            _deep["future"] = fut
            try:
                r = fut.result(timeout=timeout)
                state.last_deep_think_ts = now
                state.total_cycles += 1
                return r
            except concurrent.futures.TimeoutError:
                # Background metacog.cycle still running, keep _deep["future"] until it completes;
                # Next loop sees done()==False and skips, preventing overlap
                logger.warning("deep think timed out after %ds (continuing in background, locked until completion)", timeout)
                raise
            finally:
                if fut.done():
                    _deep["future"] = None

    # V5.2 Intent-driven unified thinking loop (replaces fixed 15min)
    def _unified_loop():
        # Signal registered in schedule() main thread; sp/poll_sec/_stop_event are schedule scope closure vars
        state = sp.load_state()
        sp.ensure_mission()
        while not _stop_event.is_set():
            now = time.time()
            # User away → sleep (PAUSED), don't burn GPU
            try:
                from v5.proactive import is_user_away
                if is_user_away():
                    state.phase = sp.PHASE_PAUSED
                    try:
                        from v5.affect import AffectState
                        AffectState.load().decay()
                    except Exception:
                        pass
                    sp.write_heartbeat(state, note="user away, idle")
                    time.sleep(60)
                    continue
            except Exception:
                pass
            # Circuit breaker tripped → stop deep thinking, wait for external state.json reset
            if state.circuit_tripped:
                logger.error("supervisor: circuit tripped, LLM writes halted until reset")
                sp.write_heartbeat(state, note="CIRCUIT TRIPPED")
                time.sleep(120)
                state = sp.load_state()  # May have been externally reset
                continue
            # Intent-driven decision
            do_think, reason = _should_deep_think(state, now)
            state.phase = sp.PHASE_RUNNING if do_think else sp.PHASE_IDLE
            if do_think:
                try:
                    r = _deep_think_once(state, now, timeout=120)
                    if r is _SKIP:
                        # Run lock triggered: previous timeout task still in background, skip this cycle
                        logger.debug("deep think skipped (overlap protection)")
                    else:
                        state = sp.record_success(state)
                        if r:
                            logger.info("think/deep: %s [%s]", r.get("mode"), str(r.get("text", ""))[:50])
                        # Curiosity / care / vitality / proactive speech
                        _maybe_curiosity_tick()
                        _maybe_care_tick()
                        try:
                            from v5.vitality import track_activity
                            track_activity()
                        except Exception:
                            pass
                        try:
                            from v5.proactive import try_proactive
                            speech = try_proactive()
                            if speech:
                                pp = Path(__file__).resolve().parent.parent / "data" / "v5" / "proactive_speech.json"
                                pp.parent.mkdir(parents=True, exist_ok=True)
                                pp.write_text(json.dumps({"text": speech, "ts": time.time()}, ensure_ascii=False), encoding="utf-8")
                        except Exception:
                            pass
                except Exception as exc:
                    state = sp.record_failure(state, exc)
                    logger.warning("deep think failed: %s", exc)
            # Heartbeat broadcast (strict-agent-loop style)
            sp.write_heartbeat(state, intent_score=state.last_intent_score, note=reason)
            time.sleep(poll_sec)
        # Graceful exit (SIGTERM/SIGINT): write STOPPED heartbeat, state already persisted
        try:
            state.phase = sp.PHASE_STOPPED
            sp.write_heartbeat(state, note="graceful stop")
        except Exception:
            pass
    t = threading.Thread(target=_unified_loop, daemon=True, name="v5-think")
    t.start()
    logger.info("think: intent-driven schedule started (poll=%ds, soft_cap=%ds)", poll_sec, sp.SOFT_CAP_SEC)

    # Subconscious stream — lightweight inner musings every 2-3 min
    def _whisper_loop():
        while True:
            try:
                _subconscious_whisper()
            except Exception as exc:
                logger.debug("whisper loop error (%s)", exc)
            time.sleep(random.randint(120, 180))  # 2-3 min
    wt = threading.Thread(target=_whisper_loop, daemon=True, name="v5-whisper")
    wt.start()
    logger.info("whisper: subconscious loop started (interval=2-3min)")


def _maybe_curiosity_tick() -> None:
    """If current ECA topic is curious_explore and self_model.curiosity is high enough → trigger memory exploration.

    V5.1: Shares single source of truth with self_model.curiosity, no independent AIS curiosity path.
    """
    # Gate on self_model.curiosity first (shared curiosity value with metacog)
    try:
        from v5.self_model import SelfModel
        sm = SelfModel.load()
        if sm.get_curiosity() < 0.35:
            return
    except Exception:
        pass
    global _eca
    if _eca is not None:
        try:
            topic = _eca.tick()
            if topic == "好奇探索":  # ECA topic name from drivers.py (pre-existing external data)
                t = curiosity_explore()
                if t:
                    logger.info("think: curiosity explored: %s", t.text[:50])
        except Exception:
            pass


def _maybe_care_tick() -> None:
    """Check if proactive care is needed (V5 #4)."""
    try:
        from v5.care import check_and_care
        care_text = check_and_care()
        if care_text:
            from v5.affect import AffectState
            state = AffectState.load().decay()
            p, a, d = state.pleasure, state.arousal, state.dominance
            thought = Thought(text=care_text, mood="joyful_calm",
                            intensity=0.4, created=time.time())
            _store_thought(thought, p, a, d)
            logger.info("think: care triggered: %s", care_text[:50])
    except Exception as exc:
        logger.debug("think: care tick failed (%s)", exc)


# ─── V5 Subconscious Stream: continuous inner musings from local model ──


def _subconscious_whisper() -> Optional[str]:
    """Generate a lightweight inner thought (~30 chars) from local qwen2.5-7b, every 2-3 min.

    This is the true "subconscious" layer — unlike metacog's deep reflection or PAD templates,
    it is just a naturally surfacing thought in the moment, as natural as breathing.
    """
    import json as _json
    try:
        # 1) Read current mood
        from v5.affect import AffectState
        state = AffectState.load().decay()
        # Map PAD to a simple English mood label
        label = _pad_to_mood(state.pleasure, state.arousal, state.dominance)
        # 2) Read most recent memory with content as seed material
        from v5 import store as store
        recent = store.list_all(10)
        seed = ""
        for m in recent:
            if m.type not in ("conversation", "inner_monologue") and m.content:
                seed = m.content[:80].replace("\n", " ")
                break
        # 3) Call LLM (Hermes :9119 inner monologue session first, fallback to :8080)
        mood_hint = label or "neutral"
        prompt = (
            f"Current mood: {mood_hint}\n"
            f"Recently remembered: {seed or '(nothing)'}\n\n"
            "Write one sentence in first-person 'I' that naturally surfaces in your mind right now, "
            "10-20 words. Like talking to yourself. No quotes."
        )
        text = ""
        try:
            from v5.hermes_client import whisper as hermes_whisper
            reply = hermes_whisper(prompt, timeout=60)
            if not reply.startswith("(Hermes"):
                text = reply.strip().strip('"').strip()
        except Exception:
            pass
        if not text:
            from v5.reflect.llm_client import call_llm_auto
            resp = call_llm_auto(
                "You are Ikaros. No pleasantries. Write one sentence of your most honest inner thought right now.",
                prompt, max_tokens=60, temperature=0.7,
            )
            text = (resp.content or "").strip().strip('"').strip()
        if not text or len(text) < 4:
            return None
        # 4) Deduplication: skip if same as previous
        if _SUBCONSCIOUS_PATH.is_file():
            try:
                prev = _json.loads(_SUBCONSCIOUS_PATH.read_text(encoding="utf-8"))
                if prev.get("text", "")[:30] == text[:30]:
                    return None  # Duplicate, skip
            except Exception:
                pass
        # 5) Write (V5.1: use json_lock to prevent concurrent write corruption)
        from v5.self_model import json_lock
        _SUBCONSCIOUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with json_lock(_SUBCONSCIOUS_PATH):
            _SUBCONSCIOUS_PATH.write_text(
                _json.dumps({
                    "text": text, "mood": mood_hint, "ts": time.time(),
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.info("whisper: %s", text[:40])
        return text
    except Exception as exc:
        logger.debug("whisper skipped (%s)", exc)
        return None


# ─── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _sys
    # Ensure Ikaros-memory/ is in path
    _HERE = Path(__file__).resolve().parent.parent  # Ikaros-memory/
    if str(_HERE) not in _sys.path:
        _sys.path.insert(0, str(_HERE))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    if "--schedule" in _sys.argv or "--watch" in _sys.argv:  # --watch == --schedule alias
        interval = 15
        for arg in _sys.argv[1:]:
            if arg.startswith("--interval="):
                interval = int(arg.split("=")[1])
        schedule(interval)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nthink: stopped")
        _sys.exit(0)

    # Single metacog entry point
    if "--metacog" in _sys.argv:
        import v5.metacog as metacog
        _m = "reflect"
        for a in _sys.argv[1:]:
            if a in ("--reflect", "--philosophy", "--cycle"):
                _m = a.lstrip("-")
        if _m == "reflect":
            r = metacog.reflect_once()
        elif _m == "philosophy":
            r = metacog.explore_philosophy()
        else:
            r = metacog.cycle()
        print(json.dumps(r, ensure_ascii=False, indent=2) if r else "{}")
        _sys.exit(0)

    # Default: run a single metacog cycle (unified thinking)
    import v5.metacog as metacog
    r = metacog.cycle()
    if r:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("{}")
        _sys.exit(1)
