"""Procedural haptic pattern engine and latched vibration state.

Design principles:
1. Phase continuity: strength or tempo updates never reset waveform phase;
   switching patterns crossfades smoothly over ~350 ms without clicks.
2. Non-chaotic variation: every pattern combines a rhythmic carrier with a slow
   golden-ratio dual-LFO phrase envelope (~9 s and ~14.5 s), so locally the
   rhythm is coherent and readable while globally no two 10 s windows repeat
   mechanically.
3. Anti-numbing High↔Medium contrast: sustained flat high amplitude quickly
   numbs mechanoreceptors, while dropping to 0% loses physical momentum. As
   intensity approaches the high zone (and natively in ``crest``), patterns
   lift deep troughs toward Medium (~46–56%) and cycle briskly (~1.8–3.3 Hz,
   tuned to physical Lovense motor inertia on a 20 Hz loop) between High and
   Medium to maximize contrast without numbing.
"""
from __future__ import annotations

import math
import time

TAU = 2.0 * math.pi
PHI = 1.618033988749895
XFADE_S = 0.35
AFTERGLOW_DECAY_S = 9.0

PATTERN_SPECS: dict[str, dict] = {
    "velvet": {
        "id": "velvet",
        "label": "Velvet",
        "tagline": "Warm continuous caress that breathes softly; shimmers at high intensity.",
        "default_pace": 0.35,
    },
    "swell": {
        "id": "swell",
        "label": "Ocean Swell",
        "tagline": "Unhurried rising waves with lingering crests and evolving depth.",
        "default_pace": 0.40,
    },
    "heartbeat": {
        "id": "heartbeat",
        "label": "Heartbeat",
        "tagline": "Organic paired lub-dub throb that quickens and eases in phrases.",
        "default_pace": 0.45,
    },
    "tease": {
        "id": "tease",
        "label": "Tease & Edge",
        "tagline": "Climbing steps that flutter at the brink, pause, then build again.",
        "default_pace": 0.50,
    },
    "rhythm": {
        "id": "rhythm",
        "label": "Rhythm",
        "tagline": "Punchy physical cadence with 4-beat accents and high-speed drive.",
        "default_pace": 0.60,
    },
    "crest": {
        "id": "crest",
        "label": "Crest Surge",
        "tagline": "Rapid High↔Medium contrast surges riding a deep wave — peak intensity, zero numbing.",
        "default_pace": 0.80,
    },
    "afterglow": {
        "id": "afterglow",
        "label": "Afterglow",
        "tagline": "Slow, warm fading waves that gently wind down to silence.",
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
    """Instantaneous carrier frequency in Hz for a pattern."""
    rubato = 1.0 + 0.12 * m1
    if pattern == "velvet":
        return (0.45 + 0.55 * pace) * rubato
    if pattern == "swell":
        return (0.24 + 0.48 * pace) * rubato
    if pattern == "heartbeat":
        return (0.62 + 0.85 * pace + 0.28 * hi_blend) * rubato
    if pattern == "tease":
        # 8 beats per tease phrase -> ~0.75-1.5 beats/s => ~6-11 s full phrase
        return (0.72 + 0.78 * pace) * rubato
    if pattern == "rhythm":
        return (0.82 + 1.55 * pace + 0.55 * hi_blend) * rubato
    if pattern == "crest":
        return (2.15 + 1.10 * pace) * rubato
    if pattern == "afterglow":
        return (0.22 + 0.22 * pace) * (1.0 + 0.08 * m1)
    return 0.5


def _contrast_osc(contrast_phase: float, m2: float) -> float:
    """Rapid High↔Medium anti-numbing wave in [mid_floor, 1.0].

    Cycles cleanly between full peak (1.0) and a breathing Medium floor
    (~0.44-0.56) so high intensities feel electric and punchy rather than flat.
    """
    mid_floor = 0.49 + 0.06 * m2
    raw = 0.5 + 0.5 * math.sin(TAU * contrast_phase)
    shaped = raw ** 0.82  # slightly fuller peak dwell for motor spin-up
    return mid_floor + (1.0 - mid_floor) * shaped


def _eval_single(
    pattern: str,
    phase: float,
    phrase_phase: float,
    contrast_phase: float,
    intensity_norm: float,
    pace: float,
    contrast_gain: float,
) -> float:
    """Pure instantaneous waveform value in [0.0, 1.0]."""
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
        # At high intensity, shimmer briskly between High and Medium to prevent numbing
        val = warm * (1.0 - 0.68 * hi_blend) + c_wave * (0.68 * hi_blend)
        return _clamp(val)

    if pattern == "swell":
        skew = 0.64 + 0.08 * m1  # slow rise, quicker warm release
        if u < skew:
            s = 0.5 - 0.5 * math.cos(math.pi * (u / skew))
        else:
            s = 0.5 + 0.5 * math.cos(math.pi * ((u - skew) / (1.0 - skew)))
        valley = 0.24 + 0.16 * breath + 0.18 * hi_blend
        peak = 0.88 + 0.12 * breath
        swell_val = valley + (peak - valley) * s
        # Shimmer the upper crest with High↔Medium contrast when intense
        crest_mask = s * hi_blend * 0.65
        return _clamp(swell_val * (1.0 - crest_mask) + c_wave * crest_mask)

    if pattern == "heartbeat":
        # Paired lub-dub Gaussian pulses over a warm breathing floor
        lub = math.exp(-(((u - 0.15) / 0.075) ** 2)) * (0.82 + 0.08 * breath)
        dub = math.exp(-(((u - 0.37) / 0.090) ** 2)) * 1.0
        beat = max(lub, dub)
        floor = 0.22 + 0.08 * m2 + 0.24 * hi_blend
        hb = floor + (1.0 - floor) * beat
        return _clamp(hb * (1.0 - 0.30 * hi_blend) + c_wave * (0.30 * hi_blend))

    if pattern == "tease":
        # 8-beat phrase: 5.4 beats climbing steps -> 1.3 beats rapid brink -> 1.3 beats hush
        bar = (phase % 8.0) / 8.0
        if bar < 0.68:
            prog = bar / 0.68
            step_wave = 0.5 - 0.5 * math.cos(TAU * u)
            ceil = 0.48 + 0.52 * (prog ** 0.9)
            floor = 0.18 + 0.28 * prog
            return _clamp(floor + (ceil - floor) * (step_wave ** 0.75))
        if bar < 0.85:
            # Hover right at the brink with rapid High↔Medium contrast
            return _clamp(c_wave * (0.94 + 0.06 * m1))
        # Tantalizing pull-back breath before next climb
        release = _smoothstep(0.85, 0.91, bar) * (1.0 - _smoothstep(0.95, 1.0, bar))
        hush = 0.12 + 0.12 * hi_blend + 0.05 * breath
        return _clamp(hush + 0.12 * (1.0 - release))

    if pattern == "rhythm":
        bar_beat = int(math.floor(phase)) % 4
        accent = 1.0 if bar_beat == 3 else (0.88 + 0.10 * breath)
        attack_frac = 0.38 - 0.06 * pace
        if u < attack_frac:
            stroke = math.sin(0.5 * math.pi * (u / attack_frac))
        else:
            rem = (u - attack_frac) / (1.0 - attack_frac)
            stroke = 0.5 + 0.5 * math.cos(math.pi * (rem ** 0.85))
        # Lift floor from deep stroke (0.24) to crisp Medium (~0.50) at high intensity
        floor = 0.24 + 0.26 * hi_blend + 0.05 * m2
        val = floor + (accent - floor) * stroke
        return _clamp(val)

    if pattern == "crest":
        # Dedicated high-intensity contrast surge: rapid High↔Medium cycle riding a
        # 5-second macro wave so contrast stays sharp and never numbs.
        macro = 0.82 + 0.18 * (0.5 - 0.5 * math.cos(TAU * (phrase_phase * 1.65)))
        # Alternate slightly fuller bursts every few cycles for organic excitement
        carrier_mod = 0.5 + 0.5 * math.sin(TAU * u)
        mid_floor = 0.44 + 0.12 * breath
        fast = mid_floor + (1.0 - mid_floor) * (carrier_mod ** 0.80)
        return _clamp(fast * macro)

    if pattern == "afterglow":
        gentle_swell = 0.5 - 0.5 * math.cos(TAU * u)
        echo = 0.18 * math.exp(-(((u - 0.28) / 0.10) ** 2))
        return _clamp(0.22 + 0.65 * gentle_swell * (0.75 + 0.25 * breath) + echo)

    return 1.0


def sample_pattern_curve(
    pattern: str,
    *,
    duration_s: float = 6.0,
    steps: int = 96,
    intensity: float = 80.0,
    pace: float | None = None,
    contrast_mode: str = "balanced",
) -> list[float]:
    """Deterministic waveform preview samples in [0.0, 1.0] for UI & tests."""
    pat = normalize_pattern(pattern)
    spec = PATTERN_SPECS[pat]
    use_pace = _clamp(spec["default_pace"] if pace is None else float(pace))
    c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)
    steps = max(8, int(steps))
    dt = max(0.01, float(duration_s) / steps)

    phase = 0.0
    phrase_phase = 0.12  # start slightly into a phrase so preview shows rich shape
    contrast_phase = 0.0
    strength = _clamp(float(intensity), 0.0, 100.0)
    samples: list[float] = []

    for _ in range(steps):
        norm_i = strength / 100.0
        m1 = math.sin(TAU * phrase_phase)
        thresh = 0.52 if c_gain > 1.1 else 0.62
        hi_blend = _clamp(_clamp((norm_i - thresh) / max(0.15, 1.0 - thresh)) * c_gain)

        w = _eval_single(pat, phase, phrase_phase, contrast_phase, norm_i, use_pace, c_gain)
        samples.append(round(w * norm_i, 4))

        phase += dt * _carrier_hz(pat, use_pace, m1, hi_blend)
        phrase_phase += dt * (0.105 + 0.045 * use_pace)
        contrast_phase += dt * (2.05 + 1.15 * use_pace) * (1.0 + 0.14 * m1)
        if pat == "afterglow":
            strength = max(0.0, strength - (100.0 * dt / AFTERGLOW_DECAY_S))

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
    contrast_phase = max(0.0, t) * (2.05 + 1.15 * pace) * (1.0 + 0.14 * m1)
    return _eval_single(pat, phase, phrase_phase, contrast_phase, intensity_norm, pace, c_gain)


class VibeState:
    """Latched target and continuous oscillator state read by the 20 Hz loop.

    Updating strength or pace preserves oscillator phase so streaming JEV
    updates glide seamlessly; changing pattern crossfades over ``XFADE_S``.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.strength: float = 0.0          # target strength, 0..100
        self.pattern: str = "velvet"
        self.pace: float = 0.45             # tempo 0..1
        self.params: dict = {}
        self.vibe_on: bool = True           # mute/output gate
        self.latched_at: float = clock()
        self.source: str = ""
        self.version: int = 0

        # Continuous phase accumulators (never jump on strength/pace updates)
        self.phase: float = 0.0
        self.phrase_phase: float = 0.0
        self.contrast_phase: float = 0.0
        self._prev_pattern: str | None = None
        self._xfade: float = 1.0
        self._afterglow_start_strength: float = 0.0

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
            # Fresh start from silence: start cleanly at beginning of swell/phrase
            self.phase = 0.0
            self.phrase_phase = 0.0
            self.contrast_phase = 0.0
            self._prev_pattern = None
            self._xfade = 1.0
        elif new_pat != self.pattern and not was_silent:
            # Smooth crossfade between active patterns without phase discontinuity
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

    def advance_and_sample(
        self,
        dt: float,
        intensity_norm: float,
        contrast_mode: str = "balanced",
    ) -> float:
        """Advance oscillators by ``dt`` seconds and return waveform in [0, 1]."""
        dt = max(0.0, float(dt))
        c_gain = CONTRAST_MODES.get(contrast_mode, 1.0)

        # Gentle automatic wind-down when in afterglow
        if self.pattern == "afterglow" and self.strength > 0.0 and dt > 0.0:
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
            self.contrast_phase += dt * (2.05 + 1.15 * self.pace) * (1.0 + 0.14 * m1)
            # Keep floats well-conditioned over multi-hour sessions
            if self.phase > 1e6:
                self.phase %= 8.0
            if self.contrast_phase > 1e6:
                self.contrast_phase %= 1.0

        return w

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
