"""Waveform patterns and the latched vibration state.

The state is a latch: a trigger sets {strength, pattern} and it holds until
another trigger, a stop, or the output gate changes it. Nothing expires on its
own. Patterns are continuous waveforms evaluated per tick; the ramp/slew that
smooths *strength* changes lives in DeviceManager (the pattern waveform itself
must stay crisp — a slewed pulse edge is just mush).
"""
import math
import time

PATTERNS = ("constant", "pulse", "wave")

DEFAULT_PARAMS = {
    "pulse_on_ms": 400,
    "pulse_off_ms": 250,
    "wave_low_pct": 30,
    "wave_period_ms": 2500,
}


def pattern_multiplier(pattern: str, t: float, params: dict | None = None) -> float:
    """Waveform value in [0, 1] at ``t`` seconds since the pattern was latched."""
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update({k: v for k, v in params.items() if v is not None})
    if pattern == "pulse":
        on = max(1.0, float(p["pulse_on_ms"]))
        off = max(0.0, float(p["pulse_off_ms"]))
        phase = (t * 1000.0) % (on + off)
        return 1.0 if phase < on else 0.0
    if pattern == "wave":
        low = min(max(float(p["wave_low_pct"]) / 100.0, 0.0), 1.0)
        period = max(1.0, float(p["wave_period_ms"]))
        # Start at the low point and rise, so a fresh latch swells in.
        s = 0.5 - 0.5 * math.cos(2 * math.pi * (t * 1000.0) / period)
        return low + (1.0 - low) * s
    return 1.0  # "constant" and anything unknown


class VibeState:
    """Latched target the 10 Hz loop reads. All writes go through methods so
    the pattern phase resets when the target changes and /status can report
    what set it."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.strength = 0.0        # latched target, 0-100
        self.pattern = "constant"
        self.params: dict = {}
        self.vibe_on = True        # output gate: off = silent, latch keeps tracking
        self.latched_at = clock()
        self.source = ""           # "keyword:<rule_id>" | "semantic" | "manual" | ...
        self.version = 0

    def set_target(self, strength: float, pattern: str = "constant",
                   params: dict | None = None, source: str = "") -> None:
        self.strength = min(max(float(strength), 0.0), 100.0)
        self.pattern = pattern if pattern in PATTERNS else "constant"
        self.params = dict(params or {})
        self.latched_at = self._clock()
        self.source = source
        self.version += 1

    def apply_effect(self, effect: dict, source_prefix: str = "") -> None:
        """Apply a trigger-engine effect dict ({strength, pattern, params, rule_id})."""
        src = effect.get("rule_id") or effect.get("category") or ""
        self.set_target(
            effect.get("strength", 0),
            effect.get("pattern") or "constant",
            effect.get("params"),
            source=f"{source_prefix}{src}",
        )

    def clear(self, source: str = "stop") -> None:
        self.set_target(0, "constant", None, source=source)

    def set_gate(self, on: bool) -> None:
        if self.vibe_on != bool(on):
            self.vibe_on = bool(on)
            self.version += 1

    def target_multiplier(self) -> float:
        """pattern(t) × strength, in [0, 100]. Gate is applied by the loop."""
        t = self._clock() - self.latched_at
        return pattern_multiplier(self.pattern, t, self.params) * self.strength

    def snapshot(self) -> dict:
        return {
            "strength": self.strength,
            "pattern": self.pattern,
            "params": dict(self.params),
            "vibe_on": self.vibe_on,
            "source": self.source,
        }
