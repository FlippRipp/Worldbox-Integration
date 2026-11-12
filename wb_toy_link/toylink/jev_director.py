"""JEV-powered haptic director: steers strength, pattern, and tempo from prose.

Instead of brittle keyword regexes or slow chat-LLM JSON prompts, ``JevDirector``
queries WorldboxAI's JEV decision engine in a single fast typed call:
- ``engaged`` (noul): filters out negations, memories, and non-physical prose.
- ``wind_down`` (noul): detects when an intimate scene settles so ``afterglow``
  can gently fade out instead of cutting off abruptly or buzzing forever.
- ``intensity`` (choice): probability-weighted across 6 ordered bands to yield
  smooth analog 0–100% intensity rather than coarse step jumps.
- ``pace`` (choice): probability-weighted across 4 tempo bands -> continuous
  0.0–1.0 tempo knob for the pattern engine.
- ``pattern`` (choice): selects the best-fitting procedural waveform from the
  user's enabled pattern palette.
"""
from __future__ import annotations

import asyncio
import re
import time

from .patterns import PATTERNS, normalize_pattern

MODULE_ID = "wb_toy_link"

_BEAT_BOUNDARY = re.compile(r"[.!?;\n—][\"'”’)\]]*(\s|$)")

INTENSITY_ORDER: tuple[str, ...] = (
    "none",
    "subtle",
    "sensual",
    "aroused",
    "intense",
    "climax",
)
INTENSITY_ANCHORS: dict[str, float] = {
    "none": 0.0,
    "subtle": 22.0,
    "sensual": 42.0,
    "aroused": 64.0,
    "intense": 84.0,
    "climax": 100.0,
}
INTENSITY_CRITERIA: dict[str, str] = {
    "none": "No physical touch or erotic stimulation is happening right now (0%).",
    "subtle": "Light, tender, or fleeting physical contact — holding hands, gentle brush, warm hug, soft kiss (~20%).",
    "sensual": "Unhurried sensual touch — deep kissing, caressing, undressing, teasing closeness (~42%).",
    "aroused": "Active erotic stimulation — gasping, grinding, oral or manual touch, steady physical rhythm (~64%).",
    "intense": "Driving, urgent, passionate physical intensity — fast thrusting, pounding, trembling near the brink (~84%).",
    "climax": "Peak orgasmic climax, overwhelming release, or maximum physical peak right now (100%).",
}

PACE_ORDER: tuple[str, ...] = ("slow", "measured", "fast", "frantic")
PACE_ANCHORS: dict[str, float] = {
    "slow": 0.15,
    "measured": 0.45,
    "fast": 0.75,
    "frantic": 1.00,
}
PACE_CRITERIA: dict[str, str] = {
    "slow": "Slow, languid, lingering, unhurried.",
    "measured": "Steady, unhurried medium cadence.",
    "fast": "Quickening, urgent, eager, fast-paced.",
    "frantic": "Breathless, relentless, frantic speed.",
}

PATTERN_CRITERIA: dict[str, str] = {
    "velvet": "Continuous close contact, warm embrace, lingering kiss, or steady sensual pressure.",
    "swell": "Slow, deep, gradually building sensual waves — long rising arousal, slow deep kissing, lingering full-body caresses.",
    "heartbeat": "Pronounced throbbing pulse out of stillness — breathless tension, aching anticipation, racing heartbeats.",
    "tease": "Playful teasing, edging, stop-and-go touch, building anticipation then pulling back.",
    "rhythm": "Bold full-stroke physical cadence (0 to full) — deliberate thrusting, stroking, rocking, or bouncing in rhythm.",
    "crest": "Peak or near-peak overwhelming intensity — rapid High-to-Medium surges at climax or right on the brink.",
    "afterglow": "Coming down from climax, trembling aftershocks, softening touch, resting together.",
}


def build_questions(enabled_patterns: list[str] | tuple[str, ...] = PATTERNS) -> dict:
    allowed = [p for p in enabled_patterns if p in PATTERN_CRITERIA]
    if not allowed:
        allowed = list(PATTERNS)

    questions: dict = {
        "engaged": {
            "type": "noul",
            "instructions": (
                "Is physical touch, bodily intimacy, or erotic stimulation actively "
                "happening RIGHT NOW at the latest moment of current_narration? "
                "Answer no for negations ('did not touch'), memories, plans, or non-physical dialogue."
            ),
        },
        "wind_down": {
            "type": "noul",
            "instructions": (
                "Did an active physical or intimate moment just wind down, pause, "
                "finish, or settle into post-climax rest/afterglow at the end of current_narration?"
            ),
        },
        "intensity": {
            "type": "choice",
            "instructions": "Choose the physical/erotic intensity at the latest moment of current_narration.",
            "criteria": dict(INTENSITY_CRITERIA),
        },
        "pace": {
            "type": "choice",
            "instructions": "Choose the physical tempo/pace of the action at the latest moment of current_narration.",
            "criteria": dict(PACE_CRITERIA),
        },
    }
    # JEV requires at least 2 options for a choice question; if the user narrowed
    # the palette to a single pattern, we use it directly without asking JEV.
    if len(allowed) >= 2:
        questions["pattern"] = {
            "type": "choice",
            "instructions": "Choose the haptic motion pattern that best matches the physical action right now.",
            "criteria": {p: PATTERN_CRITERIA[p] for p in allowed},
        }
    return questions


def _band_coherent(probs: dict, order: tuple[str, ...], radius: int = 1, min_mass: float = 0.65) -> bool:
    if not probs:
        return False
    best_key = max(order, key=lambda k: float(probs.get(k, 0.0)))
    idx = order.index(best_key)
    band = order[max(0, idx - radius): idx + radius + 1]
    mass = sum(float(probs.get(k, 0.0)) for k in band)
    return mass >= min_mass


def interpret_answers(
    answers: dict,
    *,
    enabled_patterns: list[str],
    current_strength: float = 0.0,
    sensitivity: float = 1.0,
    pattern_override: str | None = None,
) -> dict:
    """Pure decision interpreter -> ``{action, strength, pattern, pace, ...}``."""
    allowed = [p for p in enabled_patterns if p in PATTERN_CRITERIA] or list(PATTERNS)

    engaged_ans = answers.get("engaged") or {}
    wind_ans = answers.get("wind_down") or {}
    int_ans = answers.get("intensity") or {}
    pace_ans = answers.get("pace") or {}
    pat_ans = answers.get("pattern") or {}

    if engaged_ans.get("type") != "noul" or wind_ans.get("type") != "noul":
        return {"action": "hold", "reason": "missing engagement answer"}

    p_engaged = float(engaged_ans.get("noul", 0.0))
    p_wind = float(wind_ans.get("noul", 0.0))

    # Not engaged -> wind down via afterglow or ramp smoothly to 0
    if p_engaged < 0.25:
        if p_wind >= 0.45 and current_strength > 8.0 and "afterglow" in allowed:
            pat = normalize_pattern(pattern_override) if pattern_override else "afterglow"
            return {
                "action": "apply",
                "strength": round(min(max(current_strength, 24.0), 48.0), 1),
                "pattern": pat,
                "pace": 0.22,
                "engaged": round(p_engaged, 3),
                "wind_down": round(p_wind, 3),
            }
        if p_engaged <= 0.15:
            return {
                "action": "apply",
                "strength": 0.0,
                "pattern": normalize_pattern(pattern_override or "velvet"),
                "pace": 0.25,
                "engaged": round(p_engaged, 3),
                "wind_down": round(p_wind, 3),
            }
        return {"action": "hold", "reason": "ambiguous low engagement", "engaged": round(p_engaged, 3)}

    if p_engaged < 0.45:
        return {"action": "hold", "reason": "uncertain engagement", "engaged": round(p_engaged, 3)}

    int_probs = int_ans.get("probabilities") if int_ans.get("type") == "choice" else None
    pace_probs = pace_ans.get("probabilities") if pace_ans.get("type") == "choice" else None
    if not isinstance(int_probs, dict) or not _band_coherent(int_probs, INTENSITY_ORDER):
        return {"action": "hold", "reason": "incoherent intensity distribution"}

    raw_strength = sum(float(int_probs.get(k, 0.0)) * INTENSITY_ANCHORS[k] for k in INTENSITY_ORDER)
    scaled_strength = min(100.0, max(0.0, raw_strength * max(0.5, min(1.5, float(sensitivity)))))
    if 0.0 < scaled_strength < 10.0 and int_ans.get("choice") != "none":
        scaled_strength = 10.0

    if isinstance(pace_probs, dict) and _band_coherent(pace_probs, PACE_ORDER, radius=1, min_mass=0.60):
        pace = sum(float(pace_probs.get(k, 0.0)) * PACE_ANCHORS[k] for k in PACE_ORDER)
    else:
        pace = PACE_ANCHORS.get(str(pace_ans.get("choice", "measured")), 0.45)
    pace = min(1.0, max(0.0, pace))

    if pattern_override:
        chosen_pat = normalize_pattern(pattern_override)
    elif len(allowed) == 1:
        chosen_pat = allowed[0]
    else:
        raw_pat = str(pat_ans.get("choice") or "")
        chosen_pat = raw_pat if raw_pat in allowed else allowed[0]
        # Guard against accidental decay to 0 during an energetic active scene
        if chosen_pat == "afterglow" and scaled_strength > 55.0 and p_wind < 0.35:
            chosen_pat = "swell" if "swell" in allowed else allowed[0]
        # High climax intensities default cleanly to crest when appropriate
        if scaled_strength >= 88.0 and chosen_pat == "velvet" and "crest" in allowed:
            pat_conf = float(pat_ans.get("confidence", 0.0))
            if pat_conf < 0.75:
                chosen_pat = "crest"

    return {
        "action": "apply",
        "strength": round(scaled_strength, 1),
        "pattern": chosen_pat,
        "pace": round(pace, 2),
        "engaged": round(p_engaged, 3),
        "wind_down": round(p_wind, 3),
        "top_intensity": int_ans.get("choice", ""),
    }


class JevDirector:
    """Streams turn tokens, coalesces JEV calls, and steers ``VibeState``."""

    def __init__(self, decide_call, get_config, apply_effect, get_current_strength, clock=time.monotonic):
        self._decide_call = decide_call          # async (state, questions, step) -> Decision
        self._get_config = get_config
        self._apply_effect = apply_effect
        self._get_current_strength = get_current_strength
        self._clock = clock

        self.pattern_override: str | None = None
        self._recent_story: list[str] = []
        self._player_action: str = ""
        self._narration: str = ""
        self._chars_since_eval: int = 0
        self._last_eval_at: float = 0.0
        self._seq: int = 0
        self._inflight: asyncio.Task | None = None
        self._dirty: bool = False

        self.calls: int = 0
        self.last_verdict: dict | None = None
        self.last_error: str = ""

    def _jev_cfg(self) -> dict:
        return (self._get_config() or {}).get("jev", {}) or {}

    def _enabled_patterns(self) -> list[str]:
        pats = (self._get_config() or {}).get("enabled_patterns") or list(PATTERNS)
        return [p for p in pats if p in PATTERNS] or list(PATTERNS)

    def start_turn(self, state: dict | None) -> None:
        s = state or {}
        history = list(s.get("history") or [])
        self._recent_story = [str(item) for item in history[-3:] if item]
        self._player_action = str(s.get("input_text") or "")
        self._narration = ""
        self._chars_since_eval = 0
        self._dirty = False

    def invalidate(self) -> None:
        """Cancel any in-flight JEV call so it cannot overwrite manual STOP/changes."""
        self._seq += 1
        self._dirty = False
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
        self._inflight = None

    def feed_token(self, token: str) -> None:
        if not token:
            return
        self._narration += token
        self._chars_since_eval += len(token)
        if not self._narration.strip():
            return

        has_boundary = bool(_BEAT_BOUNDARY.search(token))
        long_clause = self._chars_since_eval >= 160 and token.endswith(" ")
        if not (has_boundary or long_clause):
            return

        min_interval = max(0.5, float(self._jev_cfg().get("min_interval_s", 1.8) or 1.8))
        now = self._clock()
        if self._inflight is not None and not self._inflight.done():
            self._dirty = True
            return
        if self._last_eval_at > 0.0 and (now - self._last_eval_at) < min_interval:
            self._dirty = True
            return
        self._schedule(step="toy:stream")

    def finish_turn(self) -> None:
        """Evaluate the completed turn so the resting latch matches the closing beat."""
        if not self._narration.strip():
            return
        if self._inflight is not None and not self._inflight.done():
            self._dirty = True
            return
        if self._chars_since_eval > 0 or self._dirty:
            self._schedule(step="toy:turn_end")

    def _schedule(self, step: str = "toy:stream") -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._seq += 1
        seq = self._seq
        self._dirty = False
        self._chars_since_eval = 0
        self._last_eval_at = self._clock()
        snapshot_narration = self._narration
        self._inflight = loop.create_task(self._evaluate(seq, snapshot_narration, step))

    async def _evaluate(self, seq: int, narration: str, step: str) -> None:
        enabled_patterns = self._enabled_patterns()
        questions = build_questions(enabled_patterns)
        state_payload = {
            "recent_story": self._recent_story,
            "player_action": self._player_action,
            "current_narration": narration,
            "current_haptic_strength": round(float(self._get_current_strength()), 1),
        }
        decision = None
        try:
            decision = await self._decide_call(state_payload, questions, step)
            self.calls += 1
            if seq != self._seq:
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("cancelled")
                return

            answers = getattr(decision, "answers", None) or {}
            if not answers:
                reason = getattr(decision, "reason", "") or "JEV unavailable"
                self.last_error = reason
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("fallback", reason)
                return

            sensitivity = float(self._jev_cfg().get("sensitivity", 1.0) or 1.0)
            verdict = interpret_answers(
                answers,
                enabled_patterns=enabled_patterns,
                current_strength=float(self._get_current_strength()),
                sensitivity=sensitivity,
                pattern_override=self.pattern_override,
            )
            self.last_verdict = verdict
            self.last_error = ""

            if verdict["action"] == "apply":
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("accepted")
                self._apply_effect(verdict)
            else:
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("skipped", verdict.get("reason", ""))
        except asyncio.CancelledError:
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("cancelled")
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("fallback", self.last_error)
        finally:
            if self._inflight is asyncio.current_task():
                self._inflight = None
            if self._dirty and seq == self._seq and self._narration.strip():
                min_interval = max(0.5, float(self._jev_cfg().get("min_interval_s", 1.8) or 1.8))
                elapsed = self._clock() - self._last_eval_at
                if elapsed >= min_interval:
                    self._schedule(step="toy:stream_followup")
                else:
                    self._schedule_delayed(min_interval - elapsed)

    def _schedule_delayed(self, delay_s: float) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        seq_at_wait = self._seq

        async def _wait_and_fire():
            await asyncio.sleep(max(0.05, delay_s))
            if self._dirty and self._seq == seq_at_wait and (self._inflight is None or self._inflight.done()):
                self._schedule(step="toy:stream_followup")

        loop.create_task(_wait_and_fire())

    async def probe_prose(self, text: str) -> dict:
        """One-shot diagnostic evaluation for Toy Studio's prose tester."""
        enabled_patterns = self._enabled_patterns()
        questions = build_questions(enabled_patterns)
        state_payload = {
            "recent_story": [],
            "player_action": "",
            "current_narration": str(text or ""),
            "current_haptic_strength": round(float(self._get_current_strength()), 1),
        }
        decision = await self._decide_call(state_payload, questions, "toy:probe")
        answers = getattr(decision, "answers", None) or {}
        if not answers:
            reason = getattr(decision, "reason", "") or "JEV unavailable"
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("fallback", reason)
            return {"ok": False, "error": reason}

        sensitivity = float(self._jev_cfg().get("sensitivity", 1.0) or 1.0)
        verdict = interpret_answers(
            answers,
            enabled_patterns=enabled_patterns,
            current_strength=float(self._get_current_strength()),
            sensitivity=sensitivity,
            pattern_override=self.pattern_override,
        )
        if decision is not None and not getattr(decision, "finished", True):
            await decision.finish("accepted" if verdict["action"] == "apply" else "skipped")
        return {"ok": True, "verdict": verdict}

    def status(self) -> dict:
        return {
            "calls": self.calls,
            "last_verdict": self.last_verdict,
            "last_error": self.last_error,
            "pattern_override": self.pattern_override,
        }
