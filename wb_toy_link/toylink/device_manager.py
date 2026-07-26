"""The single update loop that owns all device writes.

Trigger engines and UI only ever touch ``VibeState``; this loop evaluates it
~10×/s and fans the result out to whichever clients are connected. That makes
instant-stop trivial and puts every cap at one choke point.

Level math per tick:

    envelope  — slewed copy of the latched strength (0-100). Moves toward the
                target at ``ramp_ms`` per full swing, so intensity changes ramp
                instead of jumping. The output gate (vibe_on=False) and
                instant_stop zero it immediately, bypassing the slew.
    level     = pattern(t) × envelope/100 × master_cap_global × story cap

The pattern waveform is applied after the slew on purpose: pulse edges stay
crisp; only the intensity envelope ramps.

Send policy: transmit on >2 % change, or as a keepalive every ~0.9 s while
level > 0 (the Lovense timeSec:2 safety relies on that cadence); an explicit
stop is sent exactly once on reaching zero.
"""
import asyncio
import time

from .patterns import VibeState, pattern_multiplier

CHANGE_THRESHOLD = 0.02
KEEPALIVE_S = 0.9


class DeviceManager:
    def __init__(self, vibe: VibeState, clients: list, get_config,
                 clock=time.monotonic):
        self.vibe = vibe
        self.clients = clients
        self._get_config = get_config
        self._clock = clock
        self.story_cap = 100.0        # per-story master_cap, set at turn start
        self.envelope = 0.0           # slewed strength, 0-100
        self.current_level = 0.0      # last computed output, 0-1
        self._last_sent = 0.0
        self._last_send_time = 0.0
        self._last_tick = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ loop

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                pass  # the loop must never die; client errors are already caught
            hz = float(self._get_config().get("tick_hz", 10) or 10)
            await asyncio.sleep(1.0 / max(1.0, hz))

    async def tick(self, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        cfg = self._get_config()

        target = self.vibe.strength if self.vibe.vibe_on else 0.0
        if not self.vibe.vibe_on:
            self.envelope = 0.0        # gate off = immediate silence, no ramp-down
        else:
            ramp_ms = float(cfg.get("ramp_ms", 500) or 0)
            if ramp_ms <= 0:
                self.envelope = target
            else:
                max_step = 100.0 * (dt * 1000.0) / ramp_ms
                delta = target - self.envelope
                if abs(delta) <= max_step:
                    self.envelope = target
                else:
                    self.envelope += max_step if delta > 0 else -max_step

        t = now - self.vibe.latched_at
        wave = pattern_multiplier(self.vibe.pattern, t, self.vibe.params)
        cap = (float(cfg.get("master_cap_global", 100)) / 100.0) \
            * (float(self.story_cap) / 100.0)
        level = max(0.0, min(1.0, wave * (self.envelope / 100.0) * cap))
        self.current_level = level
        await self._transmit(level, now)

    async def _transmit(self, level: float, now: float) -> None:
        if level <= 0.0:
            if self._last_sent > 0.0:
                self._last_sent = 0.0
                await self._send_stop()
            return
        if abs(level - self._last_sent) > CHANGE_THRESHOLD \
                or (now - self._last_send_time) > KEEPALIVE_S:
            self._last_sent = level
            self._last_send_time = now
            for client in self.clients:
                await client.set_level(level)

    async def _send_stop(self) -> None:
        for client in self.clients:
            await client.stop()

    # --------------------------------------------------------------- controls

    async def instant_stop(self) -> None:
        """Hard stop: clear the latch, zero the output, tell devices now."""
        self.vibe.clear(source="manual")
        self.envelope = 0.0
        self.current_level = 0.0
        self._last_sent = 0.0
        await self._send_stop()

    async def set_gate(self, on: bool) -> None:
        """The vibe toggle: off silences immediately but keeps the latch."""
        self.vibe.set_gate(on)
        if not on:
            self.envelope = 0.0
            self.current_level = 0.0
            if self._last_sent > 0.0:
                self._last_sent = 0.0
            await self._send_stop()

    async def test_buzz(self, strength: float = 50.0, seconds: float = 1.5) -> None:
        """Direct fixed-length buzz for the test button — bypasses the latch."""
        cap = (float(self._get_config().get("master_cap_global", 100)) / 100.0) \
            * (float(self.story_cap) / 100.0)
        level = max(0.0, min(1.0, (strength / 100.0) * cap))
        for client in self.clients:
            await client.set_level(level)
        await asyncio.sleep(seconds)
        await self._send_stop()

    def status(self) -> dict:
        return {
            "level": round(self.current_level, 3),
            "envelope": round(self.envelope, 1),
            "story_cap": self.story_cap,
            "vibe": self.vibe.snapshot(),
            "backends": [c.status() for c in self.clients],
        }
