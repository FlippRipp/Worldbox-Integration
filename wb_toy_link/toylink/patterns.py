"""Procedural haptic pattern engine, seamless Lovense phrase synthesizer, and VibeState.

Design principles:
1. Gapless hardware loops on Lovense (``timeSec: 0``, ``stopPrevious: 0``):
   ``build_lovense_phrase()`` synthesizes 50-step multi-beat phrases (~6–9 s)
   where carrier cycles, High↔Medium contrast cycles, and dual-harmonic phrase
   envelopes complete exact integer multiples over the 50 steps. Lovense loops
   ``step[49] -> step[0]`` natively in hardware with zero seam and zero periodic
   HTTP refreshes — completely eliminating ~4 s refresh dropouts.
2. Single-packet story ``afterglow``: when winding down after an intimate scene,
   all 9.5 seconds (50 × 190 ms) decay smoothly to trailing zeros in one packet.
3. BLE-aligned High↔Medium contrast (~1.0–1.4 Hz, 6–8 steps per cycle): prevents
   numbing at high intensity while giving physical Lovense motors full spin-up
   and spin-down contrast without BLE aliasing.
"""
from __future__ import annotations

import copy
import math
import time

TAU = 2.0 * math.pi
PHI = 1.618033988749895
XFADE_S = 0.35
AFTERGLOW_DECAY_S = 9.5

PATTERN_SPECS: dict[str, dict] = {
    "velvet": {
        "id": "velvet",
        "label": "Velvet",
        "tagline": "Warm continuous caress that breathes softly; pulses High↔Medium when intense.",
        "default_pace": 0.35,
    },
    "swell": {
        "id": "swell",
        "label": "Ocean Swell",
        "tagline": "Slow, deep 13s tide building gradually from 0% all the way to full crest and back down.",
        "default_pace": 0.40,
    },
    "heartbeat": {
        "id": "heartbeat",
        "label": "Heartbeat",
        "tagline": "Pronounced lub-dub throb punching out of pure silence with a crisp pause between beats.",
        "default_pace": 0.45,
    },
    "tease": {
        "id": "tease",
        "label": "Tease & Edge",
        "tagline": "Climbing swells that hover at the brink, hush softly, then build again.",
        "default_pace": 0.50,
    },
    "rhythm": {
        "id": "rhythm",
        "label": "Rhythm",
        "tagline": "Bold 0%↔100% full-depth strokes with 4-beat accents and crisp zero-floor contrast.",
        "default_pace": 0.60,
    },
    "crest": {
        "id": "crest",
        "label": "Crest Surge",
        "tagline": "Brisk High↔Medium contrast surges riding a deep wave — peak intensity, zero numbing.",
        "default_pace": 0.78,
    },
    "afterglow": {
        "id": "afterglow",
        "label": "Afterglow",
        "tagline": "Slow, silky cosine waves that gently wind down to silence.",
        "default_pace": 0.25,
    },
}

PATTERNS: tuple[str, ...] = tuple(PATTERN_SPECS.keys())

LEGACY_ALIASES: dict[str, str] = {
    "constant": "velvet",
    "wave": "swell",
    "pulse": "rhythm",
}

CONTRAST_MODES: dict[str, float] = {
    "subtle": 0.55,
    "balanced": 1.00,
    "high": 1.30,
}

SENTENCE_ARCS: tuple[str, ...] = ("steady", "rise", "surge", "ease")


def normalize_arc(arc: str | None) -> str:
    if not arc:
        return "steady"
    clean = str(arc).strip().lower()
    return clean if clean in SENTENCE_ARCS else "steady"


def sentence_arc_gain(arc: str | None, progress: float) -> float:
    """Within-sentence amplitude envelope multiplier for ``progress`` in ``[0, 1]``."""
    a = normalize_arc(arc)
    p = _clamp(float(progress), 0.0, 1.0)
    if a == "rise":
        # Gradual buildup across the sentence (0.74x -> 1.08x)
        return 0.74 + 0.34 * (0.5 - 0.5 * math.cos(math.pi * p))
    if a == "surge":
        # Cresting swell peaking around mid-sentence (0.80x -> 1.12x -> 0.88x)
        crest = math.sin(math.pi * (p ** 0.82))
        tail = 0.08 * (0.5 - 0.5 * math.cos(math.pi * p))
        return 0.80 + 0.32 * crest + tail * (1.0 - crest)
    if a == "ease":
        # Gentle breath / softening across the sentence (1.00x -> 0.76x)
        return 1.00 - 0.24 * (0.5 - 0.5 * math.cos(math.pi * p))
    return 1.0


def normalize_pattern(name: str | None) -> str:
    if not name:
        return "velvet"
    clean = str(name).strip().lower()
    clean = LEGACY_ALIASES.get(clean, clean)
    return clean if clean in PATTERN_SPECS else "velvet"


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if val < lo else (hi if val > hi else val)


def _smoothstep(edge0: float, edge1: float, x: float) -> float:
    if edge1 <= edge0:
        return 1.0 if x >= edge1 else 0.0
    t = _clamp((x - edge0) / (edge1 - edge0))
    return t * t * (3.0 - 2.0 * t)


def _carrier_hz(pattern: str, pace: float, m1: float, hi_blend: float) -> float:
    """Instantaneous carrier frequency in Hz, tuned below BLE's Nyquist limit."""
    rubato = 1.0 + 0.10 * m1
    if pattern == "velvet":
        return (0.30 + 0.38 * pace) * rubato
    if pattern == "swell":
        # Long 11.5–15s majestic tide wave (0% -> 100% -> 0%)
        return (0.066 + 0.026 * pace) * (1.0 + 0.06 * m1)
    if pattern == "heartbeat":
        return (0.46 + 0.30 * pace + 0.08 * hi_blend) * (1.0 + 0.06 * m1)
    if pattern == "tease":
        return (0.55 + 0.55 * pace) * rubato
    if pattern == "rhythm":
        return (0.58 + 0.56 * pace + 0.10 * hi_blend) * rubato
    if pattern == "crest":
        return (1.02 + 0.40 * pace) * rubato
    if pattern == "afterglow":
        return (0.18 + 0.14 * pace) * (1.0 + 0.06 * m1)
    return 0.45


def _contrast_hz(pace: float, m1: float) -> float:
    """BLE-friendly High↔Medium contrast rate (~0.95–1.38 Hz)."""
    return (0.95 + 0.42 * pace) * (1.0 + 0.10 * m1)


def _contrast_osc(contrast_phase: float, m2: float) -> float:
    """Smooth High↔Medium anti-numbing wave in [mid_floor, 1.0]."""
    mid_floor = 0.48 + 0.06 * m2
    raw = 0.5 - 0.5 * math.cos(TAU * contrast_phase)
    shaped = raw ** 0.85
    return mid_floor + (1.0 - mid_floor) * shaped


def _eval_single(
    pattern: str,
    phase: float,
    phrase_phase: float,
    contrast_phase: float,
    intensity_norm: float,
    pace: float,
    contrast_gain: float,
    *,
    periodic_lfo: tuple[float, float] | None = None,
) -> float:
    """Pure instantaneous waveform value in [0.0, 1.0]."""
    if periodic_lfo is not None:
        m1, m2 = periodic_lfo
    else:
        m1 = math.sin(TAU * phrase_phase)
        m2 = math.sin(TAU * (phrase_phase / PHI))
    breath = _clamp(0.5 + 0.30 * m1 + 0.20 * m2)

    thresh = 0.52 if contrast_gain > 1.1 else 0.62
    hi_raw = _clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh))
    hi_blend = _clamp(hi_raw * contrast_gain)
    c_wave = _contrast_osc(contrast_phase, m2)

    u = phase % 1.0

    if pattern == "velvet":
        warm = 0.76 + 0.16 * breath + 0.08 * math.sin(TAU * u)
        val = warm * (1.0 - 0.70 * hi_blend) + c_wave * (0.70 * hi_blend)
        return _clamp(val)

    if pattern == "swell":
        # Long gradual tide: brief 0% rest -> slow ~64% buildup -> full crest -> smooth ~30% glide to 0%
        if u < 0.03 or u >= 0.97:
            return 0.0
        crest_u = 0.67 if periodic_lfo is not None else (0.65 + 0.04 * m1)
        if u < crest_u:
            r = (u - 0.03) / (crest_u - 0.03)
            s = (0.5 - 0.5 * math.cos(math.pi * r)) ** 1.18
        else:
            d = (u - crest_u) / (0.97 - crest_u)
            s = (0.5 + 0.5 * math.cos(math.pi * d)) ** 1.10
        peak = 1.0 if periodic_lfo is not None else (0.94 + 0.06 * breath)
        return _clamp(peak * s)

    if pattern == "heartbeat":
        # Crisp paired LUB–DUB punching out of pure 0% silence with a clean brake pause between beats
        if 0.035 <= u < 0.235:
            w_lub = (u - 0.035) / 0.200
            lub = min(1.0, 1.12 * ((0.5 - 0.5 * math.cos(TAU * w_lub)) ** 0.72)) * (0.68 + 0.08 * breath)
            return _clamp(lub)
        if 0.420 <= u < 0.665:
            w_dub = (u - 0.420) / 0.245
            dub = min(1.0, 1.12 * ((0.5 - 0.5 * math.cos(TAU * w_dub)) ** 0.68)) * (0.97 + 0.03 * breath)
            return _clamp(dub)
        return 0.0

    if pattern == "tease":
        bar = (phase % 8.0) / 8.0
        prog = _clamp(bar / 0.68)
        step_wave = 0.5 - 0.5 * math.cos(TAU * u)
        ceil = 0.48 + 0.52 * (prog ** 0.9)
        floor = 0.18 + 0.28 * prog
        climb_val = _clamp(floor + (ceil - floor) * (step_wave ** 0.80))
        brink_val = _clamp(c_wave * (0.94 + 0.06 * m1))
        hush_val = _clamp(0.18 + 0.10 * hi_blend + 0.04 * breath)

        to_brink = _smoothstep(0.64, 0.71, bar)
        to_hush = _smoothstep(0.83, 0.89, bar)
        re_enter = _smoothstep(0.94, 1.00, bar)

        mid = climb_val * (1.0 - to_brink) + brink_val * to_brink
        hushed = mid * (1.0 - to_hush) + hush_val * to_hush
        return _clamp(hushed * (1.0 - re_enter) + 0.18 * re_enter)

    if pattern == "rhythm":
        # Full 0% <-> 100% high-contrast stroke with accented 4th beat and clean zero valleys
        bar_beat = int(math.floor(phase)) % 4
        accent = 1.0 if bar_beat == 3 else (0.90 + 0.06 * breath)
        active_frac = 0.65
        if u >= active_frac:
            return 0.0
        w_u = u / active_frac
        stroke = min(1.0, 1.24 * ((0.5 - 0.5 * math.cos(TAU * w_u)) ** 0.60))
        return _clamp(accent * stroke)

    if pattern == "crest":
        if periodic_lfo is not None:
            # Periodic 2-wave macro envelope across the 50-step phrase
            macro = 0.84 + 0.16 * (0.5 + 0.5 * m2)
        else:
            macro = 0.84 + 0.16 * (0.5 - 0.5 * math.cos(TAU * (phrase_phase * 1.5)))
        carrier_mod = 0.5 - 0.5 * math.cos(TAU * u)
        mid_floor = 0.46 + 0.10 * breath
        fast = mid_floor + (1.0 - mid_floor) * (carrier_mod ** 0.82)
        return _clamp(fast * macro)

    if pattern == "afterglow":
        gentle_swell = 0.5 - 0.5 * math.cos(TAU * u)
        return _clamp(0.20 + 0.78 * gentle_swell * (0.85 + 0.15 * breath))

    return 1.0


def build_lovense_phrase(
    pattern: str,
    *,
    intensity_norm: float,
    pace: float | None = None,
    contrast_mode: str = "balanced",
    loop: bool = True,
    steps: int | None = None,
    entry_norm: float | None = None,
    arc: str = "steady",
    duration_s: float | None = None,
) -> tuple[list[float], int, bool]:
    """Synthesize a 48–50 step Lovense ``Pattern`` phrase: ``(levels, interval_ms, is_loop)``.

    - When ``loop=True`` and ``entry_norm is None`` and ``arc == "steady"``,
      carrier cycles, contrast cycles, and dual-harmonic phrase modulation complete
      exact integer multiples across ``steps`` so the end wraps to ``steps[0]``
      seamlessly inside Lovense with ``timeSec: 0`` and ZERO refresh packets.
    - When ``entry_norm`` or a shaped ``arc`` (``rise`` | ``surge`` | ``ease``) is
      supplied for a Stage sentence cue, the opening ~0.75 s smoothly blends from
      ``entry_norm`` into the sentence's arc contour timed to ``duration_s``.
    - When ``loop=False`` and ``pattern == "afterglow"`` (story wind-down),
      synthesizes a single 9.5 s decaying cosine phrase ending on ``0;0;0;0``.
    """
    pat = normalize_pattern(pattern)
    spec = PATTERN_SPECS[pat]
    use_pace = _clamp(spec["default_pace"] if pace is None else float(pace))
    norm_i = _clamp(float(intensity_norm), 0.0, 1.0)
    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    use_arc = normalize_arc(arc)
    default_n = 48 if (loop and pat in ("heartbeat", "tease", "rhythm")) else 50
    n = max(16, min(50, default_n if steps is None else int(steps)))

    if pat == "afterglow" and not loop:
        interval_ms = 190  # 50 * 190 ms = 9.50 s full decay in one packet
        levels: list[float] = []
        active_n = max(8, n - 4)
        for k in range(n):
            if k >= active_n:
                levels.append(0.0)
                continue
            env = 0.5 + 0.5 * math.cos(math.pi * (k / active_n))  # smooth cosine fade 1 -> 0
            u = (2.5 * (k / active_n)) % 1.0
            swell = 0.22 + 0.78 * (0.5 - 0.5 * math.cos(TAU * u))
            levels.append(round(_clamp(norm_i * env * swell), 4))
        return levels, interval_ms, False

    # Loop-seamless phrase grid (integer cycles across n steps)
    if pat == "velvet":
        interval_ms = int(round(160 - 30 * use_pace))
        m_cycles = 3
        k_contrast = 7 + int(round(2 * use_pace))
    elif pat == "swell":
        # 50 steps * 230..300 ms = 11.5..15.0 s single gradual 0% -> 100% -> 0% tide wave
        interval_ms = int(round(300 - 70 * use_pace))
        m_cycles = 1
        k_contrast = 6
    elif pat == "heartbeat":
        # 4 paired LUB–DUB beats across 48 steps (~12 steps / ~1.7–2.0 s per heartbeat)
        interval_ms = int(round(165 - 35 * use_pace))
        m_cycles = 4
        k_contrast = 8
    elif pat == "tease":
        interval_ms = int(round(170 - 35 * use_pace))
        m_cycles = 8  # 1 complete 8-beat climb -> brink -> hush tease arc
        k_contrast = 8
    elif pat == "rhythm":
        interval_ms = int(round(165 - 45 * use_pace))
        m_cycles = 8  # 2 full 4-beat accented bars
        k_contrast = 8
    elif pat == "crest":
        interval_ms = int(round(135 - 20 * use_pace))
        m_cycles = 7 + int(round(2 * use_pace))
        k_contrast = m_cycles
    else:  # looping afterglow preview
        interval_ms = int(round(185 - 30 * use_pace))
        m_cycles = 2
        k_contrast = 6

    interval_ms = max(110, interval_ms)
    shaped_sentence = (entry_norm is not None) or (use_arc != "steady")
    phrase_dur_s = (n * interval_ms) / 1000.0
    win_s = max(1.3, min(phrase_dur_s * 0.85, float(duration_s or 3.4)))
    ramp_s = 0.72
    clamped_entry = None if entry_norm is None else _clamp(float(entry_norm), 0.0, 1.0)

    levels = []
    sub_offsets = (0.125, 0.375, 0.625, 0.875)

    for k in range(n):
        if shaped_sentence:
            t_k = ((k + 0.5) * interval_ms) / 1000.0
            arc_prog = min(1.0, t_k / win_s)
            local_i = _clamp(norm_i * sentence_arc_gain(use_arc, arc_prog), 0.0, 1.0)
            if clamped_entry is not None and t_k < ramp_s:
                blend = 0.5 - 0.5 * math.cos(math.pi * (t_k / ramp_s))
                local_i = _clamp(clamped_entry * (1.0 - blend) + local_i * blend, 0.0, 1.0)
        else:
            local_i = norm_i

        acc = 0.0
        for off in sub_offsets:
            frac_u = (k + off) / n
            theta = TAU * frac_u
            m1 = math.sin(theta)
            m2 = math.sin(2.0 * theta + 0.7)
            rubato = 0.0 if pat in ("swell", "heartbeat", "tease", "rhythm") else (0.04 * m1)
            phase = m_cycles * frac_u + rubato
            c_phase = k_contrast * frac_u + 0.05 * m1
            w = _eval_single(
                pat,
                phase,
                frac_u,
                c_phase,
                local_i,
                use_pace,
                c_gain,
                periodic_lfo=(m1, m2),
            )
            acc += w * local_i
        levels.append(round(_clamp(acc / len(sub_offsets)), 4))

    return levels, interval_ms, True


def sample_pattern_curve(
    pattern: str,
    *,
    duration_s: float = 8.0,
    steps: int = 120,
    intensity: float = 80.0,
    pace: float | None = None,
    contrast_mode: str = "balanced",
) -> list[float]:
    """Deterministic waveform preview samples in [0.0, 1.0] for UI & tests."""
    pat = normalize_pattern(pattern)
    spec = PATTERN_SPECS[pat]
    use_pace = _clamp(spec["default_pace"] if pace is None else float(pace))
    steps = max(8, int(steps))
    dt = max(0.01, float(duration_s) / steps)

    vs = VibeState()
    vs.set_target(intensity, pat, pace=use_pace, source="sample")
    vs.phrase_phase = 0.12
    samples: list[float] = []
    for _ in range(steps):
        norm_i = vs.strength / 100.0
        w = vs.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
        samples.append(round(_clamp(w * (vs.strength / 100.0)), 4))
    return samples


def pattern_multiplier(
    pattern: str,
    t: float,
    params: dict | None = None,
    *,
    intensity_norm: float = 0.75,
) -> float:
    """Stateless helper for quick evaluation at ``t`` seconds."""
    pat = normalize_pattern(pattern)
    p = params or {}
    pace = _clamp(float(p.get("pace", PATTERN_SPECS[pat]["default_pace"])))
    contrast_mode = str(p.get("contrast_mode", "balanced"))
    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    phrase_phase = max(0.0, t) * (0.105 + 0.045 * pace)
    m1 = math.sin(TAU * phrase_phase)
    thresh = 0.52 if c_gain > 1.1 else 0.62
    hi_blend = _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)
    phase = max(0.0, t) * _carrier_hz(pat, pace, m1, hi_blend)
    contrast_phase = max(0.0, t) * _contrast_hz(pace, m1)
    return _eval_single(pat, phase, phrase_phase, contrast_phase, intensity_norm, pace, c_gain)


class VibeState:
    """Latched target and continuous oscillator state."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.strength: float = 0.0
        self.pattern: str = "velvet"
        self.pace: float = 0.45
        self.params: dict = {}
        self.vibe_on: bool = True
        self.latched_at: float = clock()
        self.source: str = ""
        self.version: int = 0

        self.phase: float = 0.0
        self.phrase_phase: float = 0.0
        self.contrast_phase: float = 0.0
        self._prev_pattern: str | None = None
        self._xfade: float = 1.0
        self._afterglow_start_strength: float = 0.0

    def clone(self) -> VibeState:
        dup = copy.copy(self)
        dup.params = dict(self.params)
        return dup

    def set_target(
        self,
        strength: float,
        pattern: str | None = None,
        params: dict | None = None,
        source: str = "",
        *,
        pace: float | None = None,
    ) -> None:
        new_strength = _clamp(float(strength), 0.0, 100.0)
        new_pat = normalize_pattern(pattern if pattern is not None else self.pattern)
        p = dict(params or {})
        if pace is None:
            pace = p.get("pace", self.pace if pattern is None else PATTERN_SPECS[new_pat]["default_pace"])
        new_pace = _clamp(float(pace), 0.0, 1.0)

        was_silent = self.strength <= 0.5
        if was_silent and new_strength > 0.5:
            self.phase = 0.0
            self.phrase_phase = 0.0
            self.contrast_phase = 0.0
            self._prev_pattern = None
            self._xfade = 1.0
        elif new_pat != self.pattern and not was_silent:
            self._prev_pattern = self.pattern
            self._xfade = 0.0

        self.strength = new_strength
        self.pattern = new_pat
        self.pace = new_pace
        self.params = p
        self.latched_at = self._clock()
        self.source = source
        if new_pat == "afterglow":
            self._afterglow_start_strength = new_strength
        self.version += 1

    def apply_effect(self, effect: dict, source_prefix: str = "") -> None:
        src = effect.get("rule_id") or effect.get("pattern") or effect.get("category") or "jev"
        self.set_target(
            effect.get("strength", 0.0),
            effect.get("pattern") or "velvet",
            effect.get("params"),
            source=f"{source_prefix}{src}",
            pace=effect.get("pace"),
        )

    def clear(self, source: str = "stop") -> None:
        self.strength = 0.0
        self._prev_pattern = None
        self._xfade = 1.0
        self.latched_at = self._clock()
        self.source = source
        self.version += 1

    def set_gate(self, on: bool) -> None:
        if self.vibe_on != bool(on):
            self.vibe_on = bool(on)
            self.version += 1

    def _step_once(self, dt: float, intensity_norm: float, c_gain: float, auto_decay: bool = True) -> float:
        if auto_decay and self.pattern == "afterglow" and self.strength > 0.0 and dt > 0.0:
            rate = max(6.0, self._afterglow_start_strength or 50.0) / AFTERGLOW_DECAY_S
            self.strength = max(0.0, self.strength - rate * dt)
            if self.strength == 0.0:
                self.source = "afterglow:complete"

        m1 = math.sin(TAU * self.phrase_phase)
        thresh = 0.52 if c_gain > 1.1 else 0.62
        hi_blend = _clamp(_clamp((intensity_norm - thresh) / max(0.15, 1.0 - thresh)) * c_gain)

        cur = _eval_single(
            self.pattern,
            self.phase,
            self.phrase_phase,
            self.contrast_phase,
            intensity_norm,
            self.pace,
            c_gain,
        )

        if self._prev_pattern is not None and self._xfade < 1.0:
            prev = _eval_single(
                self._prev_pattern,
                self.phase,
                self.phrase_phase,
                self.contrast_phase,
                intensity_norm,
                self.pace,
                c_gain,
            )
            w = prev * (1.0 - self._xfade) + cur * self._xfade
            if dt > 0.0:
                self._xfade = min(1.0, self._xfade + dt / XFADE_S)
                if self._xfade >= 1.0:
                    self._prev_pattern = None
        else:
            w = cur

        if dt > 0.0:
            self.phase += dt * _carrier_hz(self.pattern, self.pace, m1, hi_blend)
            self.phrase_phase += dt * (0.105 + 0.045 * self.pace)
            self.contrast_phase += dt * _contrast_hz(self.pace, m1)
            if self.phase > 1e6:
                self.phase %= 8.0
            if self.contrast_phase > 1e6:
                self.contrast_phase %= 1.0

        return w

    def advance_and_sample(
        self,
        dt: float,
        intensity_norm: float,
        contrast_mode: str = "balanced",
        *,
        auto_decay: bool = True,
    ) -> float:
        """Advance oscillators by ``dt`` seconds with anti-aliased sub-stepping."""
        dt = max(0.0, float(dt))
        c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
        if dt <= 0.04:
            return self._step_once(dt, intensity_norm, c_gain, auto_decay=auto_decay)

        sub_n = min(8, max(2, math.ceil(dt / 0.03)))
        sub_dt = dt / sub_n
        acc = 0.0
        for _ in range(sub_n):
            acc += self._step_once(sub_dt, intensity_norm, c_gain, auto_decay=auto_decay)
        return acc / sub_n

    def snapshot(self) -> dict:
        spec = PATTERN_SPECS.get(self.pattern, PATTERN_SPECS["velvet"])
        return {
            "strength": round(self.strength, 1),
            "pattern": self.pattern,
            "pattern_label": spec["label"],
            "pace": round(self.pace, 2),
            "params": dict(self.params),
            "vibe_on": self.vibe_on,
            "source": self.source,
        }
