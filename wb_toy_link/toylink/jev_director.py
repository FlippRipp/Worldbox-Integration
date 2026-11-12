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

from .patterns import PATTERNS, SENTENCE_ARCS, normalize_arc, normalize_pattern

MODULE_ID = "wb_toy_link"

_BEAT_BOUNDARY = re.compile(r"[.!?;\n—][\"'”’)\]]*(\s|$)")
_STATE_BLOCK_RE = re.compile(
    r"<!--[\s\S]*?(?:-->|$)|^ {0,3}<(details|internal_states)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)",
    re.IGNORECASE | re.MULTILINE,
)
_COLOR_TAG_RE = re.compile(r"</?\s*(?:color\s*:\s*)?[a-z]+(?:(?:\s*:\s*|\s+)[a-z]+)?\s*>", re.IGNORECASE)
_COLOR_CLOSE_RE = re.compile(r"^</\s*(?:color\s*:\s*)?[a-z]+(?:(?:\s*:\s*|\s+)[a-z]+)?\s*>", re.IGNORECASE)
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd",
    "capt", "sgt", "col", "gen", "lt", "cmdr", "gov", "sen", "rep", "rev", "no",
}


def strip_stage_hidden_blocks(raw: str) -> str:
    if not raw:
        return ""
    return _STATE_BLOCK_RE.sub("\n\n", str(raw)).replace("\r\n", "\n")


def _visible_len(text: str) -> int:
    if not text:
        return 0
    cleaned = _COLOR_TAG_RE.sub("", str(text))
    cleaned = re.sub(r"[*_~`#>]", "", cleaned)
    return len(cleaned.strip())


def _ends_with_abbreviation(slice_before_dot: str) -> bool:
    m = re.search(r"(?:^|[\s([\"“'])([A-Za-z.]{1,8})$", slice_before_dot)
    if not m:
        return False
    word = m.group(1).replace(".", "").lower()
    if not word:
        return False
    if len(word) == 1:
        return True
    return word in _ABBREVIATIONS


def split_stage_sentences(raw_text: str, *, is_complete: bool = True) -> list[dict]:
    """Mirror frontend ``stageSegments.js`` so pre-scored cues align 1-to-1 with Stage beats."""
    cleaned = strip_stage_hidden_blocks(raw_text)
    if not cleaned.strip():
        return []

    segments: list[dict] = []
    n = len(cleaned)
    seg_start = 0
    while seg_start < n and cleaned[seg_start].isspace():
        seg_start += 1

    i = seg_start
    in_straight = False
    in_curly = False

    def push_seg(end_idx: int, complete: bool) -> None:
        s = seg_start
        e = end_idx
        while s < e and cleaned[s].isspace():
            s += 1
        while e > s and cleaned[e - 1].isspace():
            e -= 1
        if e <= s:
            return
        segments.append({
            "index": len(segments),
            "char_start": s,
            "char_end": e,
            "text": cleaned[s:e],
            "complete": complete,
        })

    while i < n:
        ch = cleaned[i]
        if ch == '"':
            in_straight = not in_straight
        elif ch == "“":
            in_curly = True
        elif ch == "”":
            in_curly = False

        if ch == "\n":
            j = i + 1
            saw_second = False
            while j < n and cleaned[j] in " \t\n":
                if cleaned[j] == "\n":
                    saw_second = True
                j += 1
            if saw_second:
                push_seg(i, True)
                in_straight = False
                in_curly = False
                seg_start = j
                i = j
                continue

        if ch in ".!?…":
            p_end = i + 1
            while p_end < n and cleaned[p_end] in ".!?…":
                p_end += 1
            after = p_end
            straight_after = in_straight
            curly_after = in_curly
            while after < n:
                c_after = cleaned[after]
                if c_after == '"':
                    straight_after = not straight_after
                    after += 1
                    continue
                if c_after == "”":
                    curly_after = False
                    after += 1
                    continue
                if c_after in "'’)]*_":
                    after += 1
                    continue
                m_close = _COLOR_CLOSE_RE.match(cleaned[after:])
                if m_close:
                    after += len(m_close.group(0))
                    continue
                break

            is_single_dot = ch == "." and p_end == i + 1
            is_abbr = is_single_dot and _ends_with_abbreviation(cleaned[seg_start:i])
            at_end = after >= n
            followed_by_space = (not at_end) and cleaned[after].isspace()
            cand_len = _visible_len(cleaned[seg_start:after])

            if (not is_abbr) and (followed_by_space or (at_end and is_complete)):
                next_non_space = after
                nl_count = 0
                while next_non_space < n and cleaned[next_non_space].isspace():
                    if cleaned[next_non_space] == "\n":
                        nl_count += 1
                    next_non_space += 1
                has_para_break = nl_count >= 2
                if cand_len >= 14 or has_para_break or (at_end and is_complete):
                    push_seg(after, True)
                    in_straight = False if has_para_break else straight_after
                    in_curly = False if has_para_break else curly_after
                    seg_start = next_non_space
                    i = next_non_space
                    continue

            in_straight = straight_after
            in_curly = curly_after
            i = max(i + 1, after)
            continue

        i += 1

    if seg_start < n:
        rem = cleaned[seg_start:]
        if (not is_complete) and _visible_len(rem) > 240:
            m_clause = re.match(r"^([\s\S]{140,240}?[,;—:])\s+", rem)
            if m_clause:
                cut = seg_start + len(m_clause.group(1))
                push_seg(cut, True)
                seg_start = cut
                while seg_start < n and cleaned[seg_start].isspace():
                    seg_start += 1
        if seg_start < n:
            push_seg(n, is_complete)

    return segments

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

ARC_ORDER: tuple[str, ...] = SENTENCE_ARCS
ARC_CRITERIA: dict[str, str] = {
    "steady": "Consistent, sustained rhythm or touch across this sentence.",
    "rise": "Gradually building tension, closeness, pressure, or speed through this sentence.",
    "surge": "A pronounced crest, thrust, gasp, kiss, or wave peaking within this sentence.",
    "ease": "Softening, catching breath, tender dialogue, or lingering gently while staying connected.",
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


def build_sentence_questions(enabled_patterns: list[str] | tuple[str, ...] = PATTERNS) -> dict:
    """Questions scoped to ``current_sentence`` with full active-state continuity."""
    allowed = [p for p in enabled_patterns if p in PATTERN_CRITERIA] or list(PATTERNS)
    questions: dict = {
        "engaged": {
            "type": "noul",
            "instructions": (
                "Taking turn_so_far, active_haptic_state, and recent_sentence_history into account, "
                "is physical touch, bodily closeness, or erotic stimulation either starting in "
                "current_sentence OR still ongoing in the scene during current_sentence? "
                "IMPORTANT: Spoken dialogue, eye contact, breathing, thoughts, or atmosphere occurring "
                "WHILE characters remain in an active physical embrace or encounter IS STILL ENGAGED "
                "(answer yes). Answer no ONLY if no physical contact has started yet, or if the characters "
                "explicitly pull apart, stop touching, or end the encounter."
            ),
        },
        "wind_down": {
            "type": "noul",
            "instructions": (
                "Does current_sentence explicitly describe the characters stopping physical contact, "
                "pulling apart, pausing the encounter, or settling into post-climax rest/afterglow?"
            ),
        },
        "intensity": {
            "type": "choice",
            "instructions": (
                "Choose the physical/erotic intensity during current_sentence (accounting for "
                "active_haptic_state so ongoing scenes transition smoothly rather than resetting)."
            ),
            "criteria": dict(INTENSITY_CRITERIA),
        },
        "pace": {
            "type": "choice",
            "instructions": "Choose the physical tempo/pace during current_sentence.",
            "criteria": dict(PACE_CRITERIA),
        },
        "arc": {
            "type": "choice",
            "instructions": "Choose how the sensation shapes across current_sentence.",
            "criteria": dict(ARC_CRITERIA),
        },
    }
    if len(allowed) >= 2:
        questions["pattern"] = {
            "type": "choice",
            "instructions": "Choose the haptic motion pattern that best matches current_sentence.",
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
    sentence_mode: bool = False,
    current_pattern: str = "velvet",
    current_pace: float = 0.45,
    low_engagement_streak: int = 0,
) -> dict:
    """Pure decision interpreter -> ``{action, strength, pattern, pace, ...}``."""
    allowed = [p for p in enabled_patterns if p in PATTERN_CRITERIA] or list(PATTERNS)

    engaged_ans = answers.get("engaged") or {}
    wind_ans = answers.get("wind_down") or {}
    int_ans = answers.get("intensity") or {}
    pace_ans = answers.get("pace") or {}
    pat_ans = answers.get("pattern") or {}
    arc_ans = answers.get("arc") or {}
    chosen_arc = normalize_arc(arc_ans.get("choice")) if sentence_mode else "steady"

    if engaged_ans.get("type") != "noul" or wind_ans.get("type") != "noul":
        return {"action": "hold", "reason": "missing engagement answer"}

    p_engaged = float(engaged_ans.get("noul", 0.0))
    p_wind = float(wind_ans.get("noul", 0.0))

    # Not engaged -> wind down via afterglow, hold active scene continuity, or ramp to 0
    if p_engaged < 0.45:
        if p_wind >= 0.45 and current_strength > 8.0 and "afterglow" in allowed:
            pat = normalize_pattern(pattern_override) if pattern_override else "afterglow"
            return {
                "action": "apply",
                "strength": round(min(max(current_strength, 24.0), 48.0), 1),
                "pattern": pat,
                "pace": 0.22,
                "arc": "ease",
                "engaged": round(p_engaged, 3),
                "wind_down": round(p_wind, 3),
            }

        if sentence_mode and current_strength > 8.0 and current_pattern != "afterglow":
            # Explicit disengagement or 2+ prior low-engagement sentences -> step down cleanly
            explicit_stop = p_engaged <= 0.12 and p_wind >= 0.28
            sustained_shift = low_engagement_streak >= 2 and p_engaged <= 0.24
            if explicit_stop or sustained_shift:
                return {
                    "action": "apply",
                    "strength": 0.0,
                    "pattern": normalize_pattern(pattern_override or "velvet"),
                    "pace": 0.25,
                    "arc": "ease",
                    "engaged": round(p_engaged, 3),
                    "wind_down": round(p_wind, 3),
                }
            # Mid-scene dialogue, eye contact, or atmospheric sentence: preserve
            # the active vibration with a gentle softening rather than cutting to 0%.
            keep_pat = normalize_pattern(pattern_override or current_pattern)
            if not pattern_override and keep_pat not in allowed:
                keep_pat = allowed[0]
            eased_strength = round(max(14.0, current_strength * 0.86), 1)
            eased_pace = round(max(0.15, min(1.0, float(current_pace) * 0.90)), 2)
            return {
                "action": "apply",
                "strength": eased_strength,
                "pattern": keep_pat,
                "pace": eased_pace,
                "arc": "ease",
                "continuity_hold": True,
                "engaged": round(p_engaged, 3),
                "wind_down": round(p_wind, 3),
            }

        if p_engaged <= 0.15:
            return {
                "action": "apply",
                "strength": 0.0,
                "pattern": normalize_pattern(pattern_override or "velvet"),
                "pace": 0.25,
                "arc": "steady",
                "engaged": round(p_engaged, 3),
                "wind_down": round(p_wind, 3),
            }
        return {"action": "hold", "reason": "uncertain engagement", "engaged": round(p_engaged, 3)}

    int_probs = int_ans.get("probabilities") if int_ans.get("type") == "choice" else None
    pace_probs = pace_ans.get("probabilities") if pace_ans.get("type") == "choice" else None
    if not isinstance(int_probs, dict) or not _band_coherent(int_probs, INTENSITY_ORDER):
        return {"action": "hold", "reason": "incoherent intensity distribution"}

    raw_strength = sum(float(int_probs.get(k, 0.0)) * INTENSITY_ANCHORS[k] for k in INTENSITY_ORDER)
    scaled_strength = min(100.0, max(0.0, raw_strength * max(0.5, min(1.5, float(sensitivity)))))
    if 0.0 < scaled_strength < 10.0 and int_ans.get("choice") != "none":
        scaled_strength = 10.0

    # Smooth downward dips during ongoing sentence-by-sentence scenes so a softer
    # transitional sentence blends naturally instead of dropping steeply.
    if (
        sentence_mode
        and current_strength > 12.0
        and p_wind < 0.35
        and 0.0 < scaled_strength < current_strength * 0.58
    ):
        scaled_strength = 0.62 * scaled_strength + 0.38 * current_strength

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

    res = {
        "action": "apply",
        "strength": round(scaled_strength, 1),
        "pattern": chosen_pat,
        "pace": round(pace, 2),
        "engaged": round(p_engaged, 3),
        "wind_down": round(p_wind, 3),
        "top_intensity": int_ans.get("choice", ""),
    }
    if sentence_mode:
        res["arc"] = chosen_arc
    return res


class JevDirector:
    """Streams turn tokens, coalesces JEV calls, and steers ``VibeState``."""

    def __init__(
        self,
        decide_call,
        get_config,
        apply_effect,
        get_current_strength,
        clock=time.monotonic,
        get_current_vibe=None,
    ):
        self._decide_call = decide_call          # async (state, questions, step) -> Decision
        self._get_config = get_config
        self._apply_effect = apply_effect
        self._get_current_strength = get_current_strength
        self._get_current_vibe = get_current_vibe
        self._clock = clock

        self.pattern_override: str | None = None
        self.reader_mode_active: bool = False
        self._recent_story: list[str] = []
        self._player_action: str = ""
        self._narration: str = ""
        self._chars_since_eval: int = 0
        self._last_eval_at: float = 0.0
        self._seq: int = 0
        self._inflight: asyncio.Task | None = None
        self._dirty: bool = False
        self._on_turn_settled = None

        # Sentence-by-sentence lookahead state for Paced Stage sync
        self._queued_count: int = 0
        self._pending_sentences: list[dict] = []
        self._sentence_cues: dict[int, dict] = {}
        self._sentence_worker: asyncio.Task | None = None
        self._cue_event: asyncio.Event | None = None
        self._simulated_strength: float = 0.0
        self._simulated_pattern: str = "velvet"
        self._simulated_pace: float = 0.45
        self._simulated_low_streak: int = 0
        self._simulated_history: list[dict] = []
        self._active_reader_idx: int = -1
        self._cue_counter: int = 0

        self.calls: int = 0
        self.last_verdict: dict | None = None
        self.last_error: str = ""

    def _jev_cfg(self) -> dict:
        return (self._get_config() or {}).get("jev", {}) or {}

    def _enabled_patterns(self) -> list[str]:
        pats = (self._get_config() or {}).get("enabled_patterns") or list(PATTERNS)
        return [p for p in pats if p in PATTERNS] or list(PATTERNS)

    def set_reader_mode(self, active: bool) -> None:
        was_synced = self.is_reader_synced()
        self.reader_mode_active = bool(active)
        if not was_synced and self.is_reader_synced():
            if self._inflight is not None and not self._inflight.done():
                self._inflight.cancel()
                self._inflight = None
            if self._narration.strip():
                self._enqueue_completed_sentences(is_complete=False)

    def is_reader_synced(self) -> bool:
        cfg = self._get_config() or {}
        return self.reader_mode_active and bool(cfg.get("sync_stage_reader", True))

    def start_turn(self, state: dict | None) -> None:
        s = state or {}
        history = list(s.get("history") or [])
        self._recent_story = [str(item) for item in history[-3:] if item]
        self._player_action = str(s.get("input_text") or "")
        self._narration = ""
        self._chars_since_eval = 0
        self._dirty = False
        self._on_turn_settled = None

        if self._sentence_worker is not None and not self._sentence_worker.done():
            self._sentence_worker.cancel()
        self._sentence_worker = None
        self._seq += 1
        self._queued_count = 0
        self._pending_sentences = []
        self._sentence_cues = {}
        cur_vibe = (self._get_current_vibe() or {}) if self._get_current_vibe else {}
        self._simulated_strength = float(cur_vibe.get("strength", self._get_current_strength()))
        self._simulated_pattern = normalize_pattern(cur_vibe.get("pattern") or "velvet")
        self._simulated_pace = float(cur_vibe.get("pace", 0.45))
        self._simulated_low_streak = 0
        self._simulated_history = []
        self._active_reader_idx = -1

    def invalidate(self) -> None:
        """Cancel any in-flight JEV call so it cannot overwrite manual STOP/changes."""
        self._seq += 1
        self._dirty = False
        self._on_turn_settled = None
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()
        self._inflight = None
        if self._sentence_worker is not None and not self._sentence_worker.done():
            self._sentence_worker.cancel()
        self._sentence_worker = None
        self._pending_sentences = []

    def feed_token(self, token: str) -> None:
        if not token:
            return
        self._narration += token
        self._chars_since_eval += len(token)
        if not self._narration.strip():
            return

        if self.is_reader_synced():
            self._enqueue_completed_sentences(is_complete=False)
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

    def finish_turn(self, on_settled=None) -> None:
        """Evaluate the completed turn so the resting latch matches the closing beat."""
        if not self._narration.strip():
            if on_settled is not None:
                on_settled()
            return

        if self.is_reader_synced():
            self._enqueue_completed_sentences(is_complete=True)
            return

        if on_settled is not None:
            self._on_turn_settled = on_settled
        if self._inflight is not None and not self._inflight.done():
            self._dirty = True
            return
        if self._chars_since_eval > 0 or self._dirty:
            self._schedule(step="toy:turn_end")
        elif self._on_turn_settled is not None:
            cb = self._on_turn_settled
            self._on_turn_settled = None
            cb()

    # ---------------------------------------- Stage Reader lookahead pipeline

    def _get_cue_event(self) -> asyncio.Event:
        if self._cue_event is None:
            self._cue_event = asyncio.Event()
        return self._cue_event

    def _enqueue_completed_sentences(self, *, is_complete: bool) -> None:
        segs = split_stage_sentences(self._narration, is_complete=is_complete)
        cleaned = strip_stage_hidden_blocks(self._narration)
        added = False
        for seg in segs:
            idx = seg["index"]
            if not seg["complete"] or idx < self._queued_count:
                continue
            preceding = cleaned[: seg["char_start"]].strip()
            self._pending_sentences.append({
                **seg,
                "preceding": preceding,
            })
            self._queued_count = idx + 1
            added = True
        if added:
            self._ensure_sentence_worker()

    def _ensure_sentence_worker(self) -> None:
        if self._sentence_worker is not None and not self._sentence_worker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        seq = self._seq
        self._sentence_worker = loop.create_task(self._drain_sentence_queue(seq))

    async def _drain_sentence_queue(self, seq: int) -> None:
        try:
            while self._pending_sentences and seq == self._seq:
                item = self._pending_sentences.pop(0)
                idx = item["index"]
                if idx in self._sentence_cues:
                    continue
                verdict = await self._score_sentence_item(seq, item)
                if seq != self._seq:
                    return
                if verdict is not None:
                    self._sentence_cues[idx] = {
                        "index": idx,
                        "char_start": item["char_start"],
                        "char_end": item["char_end"],
                        "text": item["text"],
                        "verdict": verdict,
                    }
                    self._record_simulated_verdict(idx, item["text"], verdict)
                    ev = self._get_cue_event()
                    ev.set()
                    ev.clear()
        finally:
            if self._sentence_worker is asyncio.current_task():
                self._sentence_worker = None
                if self._pending_sentences and seq == self._seq:
                    self._ensure_sentence_worker()

    def _record_simulated_verdict(self, idx: int, text: str, verdict: dict) -> None:
        p_eng = float(verdict.get("engaged", 0.5))
        if p_eng < 0.38:
            self._simulated_low_streak += 1
        else:
            self._simulated_low_streak = 0

        if verdict.get("action") == "apply":
            self._simulated_strength = float(verdict.get("strength", 0.0))
            self._simulated_pattern = normalize_pattern(verdict.get("pattern") or self._simulated_pattern)
            self._simulated_pace = float(verdict.get("pace", self._simulated_pace))
            if self._simulated_strength <= 0.5 or self._simulated_pattern == "afterglow":
                self._simulated_low_streak = 0

        self._simulated_history.append({
            "sentence_index": idx,
            "sentence": text,
            "strength": round(self._simulated_strength, 1),
            "pattern": self._simulated_pattern,
            "pace": round(self._simulated_pace, 2),
            "arc": verdict.get("arc", "steady"),
        })

    async def _score_sentence_item(self, seq: int, item: dict) -> dict | None:
        enabled_patterns = self._enabled_patterns()
        questions = build_sentence_questions(enabled_patterns)
        state_payload = {
            "recent_story": self._recent_story,
            "player_action": self._player_action,
            "turn_so_far": item.get("preceding", ""),
            "current_sentence": item["text"],
            "current_haptic_strength": round(float(self._simulated_strength), 1),
            "active_haptic_state": {
                "active": self._simulated_strength > 5.0 and self._simulated_pattern != "afterglow",
                "strength": round(float(self._simulated_strength), 1),
                "pattern": self._simulated_pattern,
                "pace": round(float(self._simulated_pace), 2),
            },
            "recent_sentence_history": list(self._simulated_history),
        }
        decision = None
        try:
            decision = await self._decide_call(state_payload, questions, f"toy:sentence_{item['index']}")
            self.calls += 1
            if seq != self._seq:
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("cancelled")
                return None

            answers = getattr(decision, "answers", None) or {}
            if not answers:
                reason = getattr(decision, "reason", "") or "JEV unavailable"
                self.last_error = reason
                if decision is not None and not getattr(decision, "finished", True):
                    await decision.finish("fallback", reason)
                return None

            sensitivity = float(self._jev_cfg().get("sensitivity", 1.0) or 1.0)
            verdict = interpret_answers(
                answers,
                enabled_patterns=enabled_patterns,
                current_strength=float(self._simulated_strength),
                sensitivity=sensitivity,
                pattern_override=self.pattern_override,
                sentence_mode=True,
                current_pattern=self._simulated_pattern,
                current_pace=self._simulated_pace,
                low_engagement_streak=self._simulated_low_streak,
            )
            self.last_verdict = verdict
            self.last_error = ""
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("accepted" if verdict["action"] == "apply" else "skipped", verdict.get("reason", ""))
            return verdict
        except asyncio.CancelledError:
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("cancelled")
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if decision is not None and not getattr(decision, "finished", True):
                await decision.finish("fallback", self.last_error)
            return None

    def _find_matching_cue(self, sentence_index: int, char_start: int, char_end: int) -> dict | None:
        exact = self._sentence_cues.get(sentence_index)
        if exact is not None and abs(exact["char_start"] - char_start) <= 32:
            return exact
        best = None
        best_overlap = 0
        for cue in self._sentence_cues.values():
            ov = min(char_end, cue["char_end"]) - max(char_start, cue["char_start"])
            if ov > best_overlap:
                best_overlap = ov
                best = cue
        return best

    async def on_reader_sentence(
        self,
        *,
        sentence_index: int,
        char_start: int = 0,
        char_end: int = 0,
        text: str = "",
        duration_ms: int = 0,
    ) -> dict | None:
        """Apply the pre-scored cue for the sentence now appearing on Stage."""
        self.reader_mode_active = True
        if not bool((self._get_config() or {}).get("sync_stage_reader", True)):
            return None

        self._active_reader_idx = sentence_index
        seq_at_call = self._seq

        cue = self._find_matching_cue(sentence_index, char_start, char_end)
        if cue is None and self._sentence_worker is not None and not self._sentence_worker.done():
            # Wait briefly if JEV is finishing this very sentence right now
            deadline = self._clock() + 1.2
            while cue is None and seq_at_call == self._seq and self._clock() < deadline:
                if self._sentence_worker is None or self._sentence_worker.done():
                    break
                try:
                    await asyncio.wait_for(self._get_cue_event().wait(), timeout=0.15)
                except asyncio.TimeoutError:
                    pass
                cue = self._find_matching_cue(sentence_index, char_start, char_end)

        if cue is not None:
            verdict = cue["verdict"]
        elif text.strip():
            cleaned = strip_stage_hidden_blocks(self._narration)
            preceding = cleaned[: max(0, char_start)].strip() if cleaned else ""
            verdict = await self._score_sentence_item(
                seq_at_call,
                {
                    "index": sentence_index,
                    "char_start": char_start,
                    "char_end": char_end or len(text),
                    "text": text,
                    "preceding": preceding,
                },
            )
            if verdict is not None and seq_at_call == self._seq:
                self._sentence_cues[sentence_index] = {
                    "index": sentence_index,
                    "char_start": char_start,
                    "char_end": char_end or len(text),
                    "text": text,
                    "verdict": verdict,
                }
                self._record_simulated_verdict(sentence_index, text, verdict)
        else:
            return None

        if verdict is None or seq_at_call != self._seq or self._active_reader_idx != sentence_index:
            return verdict

        if verdict.get("action") == "apply":
            eff = dict(verdict)
            if self.pattern_override:
                eff["pattern"] = normalize_pattern(self.pattern_override)
            self._cue_counter += 1
            dur_s = round(max(1.3, (duration_ms / 1000.0) if duration_ms > 0 else 3.4), 2)
            eff["params"] = {
                **(eff.get("params") or {}),
                "sentence_cue": True,
                "cue_id": self._cue_counter,
                "arc": normalize_arc(eff.get("arc")),
                "duration_s": dur_s,
            }
            self._apply_effect(eff)
        return verdict

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
            elif seq == self._seq and self._on_turn_settled is not None:
                cb = self._on_turn_settled
                self._on_turn_settled = None
                cb()

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
            "reader_synced": self.is_reader_synced(),
            "reader_mode_active": self.reader_mode_active,
            "pre_scored_sentences": len(self._sentence_cues),
        }
