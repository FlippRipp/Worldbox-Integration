"""The single 20 Hz update loop that owns all device writes.

JEV steering, manual controls, and pattern previews all feed through this single
loop so caps, slew envelopes, anti-numbing waveforms, and emergency STOP act at
one choke point without racing each other.
"""
from __future__ import annotations

import asyncio
import time

from .patterns import PATTERN_SPECS, VibeState, normalize_pattern

CHANGE_THRESHOLD = 0.02
KEEPALIVE_S = 0.9

RESPONSE_FEEL_RAMP_MS: dict[str, float] = {
    "silky": 900.0,
    "natural": 420.0,
    "snappy": 140.0,
}


class DeviceManager:
    def __init__(self, vibe: VibeState, clients: list, get_config, clock=time.monotonic):
        self.vibe = vibe
        self.clients = clients
        self._get_config = get_config
        self._clock = clock
        self.story_cap: float = 100.0
        self.envelope: float = 0.0          # slewed strength, 0..100
        self.current_level: float = 0.0     # last output level, 0..1
        self._last_sent: float = 0.0
        self._last_send_time: float = 0.0
        self._last_tick: float | None = None
        self._task: asyncio.Task | None = None

        # Conflict-free temporary preview state (for Test Buzz & Pattern Gallery)
        self._preview_until: float = 0.0
        self._preview_vibe: VibeState | None = None
        self._preview_envelope: float = 0.0

    # ------------------------------------------------------------------ loop

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # loop must never die; client errors are caught internally
            hz = float(self._get_config().get("tick_hz", 20) or 20)
            await asyncio.sleep(1.0 / max(2.0, min(30.0, hz)))

    def _ramp_ms(self, cfg: dict) -> float:
        if "ramp_ms" in cfg and cfg["ramp_ms"] is not None:
            return max(0.0, float(cfg["ramp_ms"]))
        feel = str(cfg.get("response_feel", "natural"))
        return RESPONSE_FEEL_RAMP_MS.get(feel, 420.0)

    async def tick(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        dt = 0.0 if self._last_tick is None else max(0.0, min(0.5, now - self._last_tick))
        self._last_tick = now
        cfg = self._get_config() or {}
        contrast_mode = str(cfg.get("contrast_mode", "balanced"))
        cap = (float(cfg.get("master_cap_global", 100)) / 100.0) * (float(self.story_cap) / 100.0)
        cap = max(0.0, min(1.0, cap))

        # Active preview (from Toy Studio pattern preview or Test Buzz) runs
        # inside the loop so it never fights normal ticks.
        if self._preview_vibe is not None and now < self._preview_until:
            pv = self._preview_vibe
            target = pv.strength
            max_step = 100.0 * (dt * 1000.0) / 180.0 if dt > 0 else target
            delta = target - self._preview_envelope
            if abs(delta) <= max_step:
                self._preview_envelope = target
            else:
                self._preview_envelope += max_step if delta > 0 else -max_step
            norm_i = (self._preview_envelope / 100.0) * cap
            wave = pv.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
            level = max(0.0, min(1.0, wave * norm_i))
            self.current_level = level
            await self._transmit(level, now)
            return
        elif self._preview_vibe is not None:
            self._preview_vibe = None
            self._preview_envelope = 0.0

        target = self.vibe.strength if self.vibe.vibe_on else 0.0
        if not self.vibe.vibe_on:
            self.envelope = 0.0
        else:
            ramp_ms = self._ramp_ms(cfg)
            if ramp_ms <= 0:
                self.envelope = target
            else:
                max_step = 100.0 * (dt * 1000.0) / ramp_ms
                delta = target - self.envelope
                if abs(delta) <= max_step:
                    self.envelope = target
                else:
                    self.envelope += max_step if delta > 0 else -max_step

        norm_i = (self.envelope / 100.0) * cap
        if self.envelope <= 0.001:
            wave = 0.0
        else:
            wave = self.vibe.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
        level = max(0.0, min(1.0, wave * norm_i))
        self.current_level = level
        await self._transmit(level, now)

    async def _transmit(self, level: float, now: float) -> None:
        if level <= 0.0:
            if self._last_sent > 0.0:
                self._last_sent = 0.0
                await self._send_stop()
            return
        if abs(level - self._last_sent) > CHANGE_THRESHOLD or (now - self._last_send_time) > KEEPALIVE_S:
            self._last_sent = level
            self._last_send_time = now
            for client in self.clients:
                await client.set_level(level)

    async def _send_stop(self) -> None:
        for client in self.clients:
            await client.stop()

    # --------------------------------------------------------------- controls

    def start_preview(
        self,
        strength: float = 55.0,
        pattern: str = "velvet",
        seconds: float = 3.0,
        pace: float | None = None,
    ) -> None:
        """Run a temporary pattern/test preview cleanly inside the 20 Hz loop."""
        pat = normalize_pattern(pattern)
        pv = VibeState(clock=self._clock)
        pv.set_target(
            max(5.0, min(100.0, float(strength))),
            pat,
            source=f"preview:{pat}",
            pace=PATTERN_SPECS[pat]["default_pace"] if pace is None else pace,
        )
        self._preview_vibe = pv
        self._preview_envelope = min(self.envelope, pv.strength)
        self._preview_until = self._clock() + max(0.3, min(30.0, float(seconds)))

    async def test_buzz(self, strength: float = 50.0, seconds: float = 1.5, pattern: str = "crest") -> None:
        """Schedule a conflict-free test preview via the loop."""
        self.start_preview(strength=strength, pattern=pattern, seconds=seconds)

    async def instant_stop(self) -> None:
        """Hard stop: cancel any preview, clear latch, zero output immediately."""
        self._preview_vibe = None
        self._preview_until = 0.0
        self._preview_envelope = 0.0
        self.vibe.clear(source="manual")
        self.envelope = 0.0
        self.current_level = 0.0
        self._last_sent = 0.0
        await self._send_stop()

    async def set_gate(self, on: bool) -> None:
        """Mute toggle: off silences immediately while preserving the latch."""
        self.vibe.set_gate(on)
        if not on:
            self._preview_vibe = None
            self._preview_until = 0.0
            self.envelope = 0.0
            self.current_level = 0.0
            if self._last_sent > 0.0:
                self._last_sent = 0.0
            await self._send_stop()

    @property
    def preview_active(self) -> bool:
        return self._preview_vibe is not None and self._clock() < self._preview_until

    def status(self) -> dict:
        preview = None
        if self.preview_active and self._preview_vibe is not None:
            preview = {
                "pattern": self._preview_vibe.pattern,
                "strength": round(self._preview_vibe.strength, 1),
                "remaining_s": round(max(0.0, self._preview_until - self._clock()), 1),
            }
        return {
            "level": round(self.current_level, 3),
            "envelope": round(self.envelope, 1),
            "story_cap": self.story_cap,
            "vibe": self.vibe.snapshot(),
            "preview": preview,
            "backends": [c.status() for c in self.clients],
        }
