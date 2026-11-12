"""The single 20 Hz control loop that owns oscillator state and device streams.

Smoothness design:
- Oscillator timing is 100% decoupled from network latency: ``run()`` advances on
  a drift-free monotonic schedule and dispatches HTTP writes asynchronously.
- Clients supporting ``send_pattern`` (``LovenseClient``) receive 45-step lookahead
  curves at 120 ms step intervals (5.4 s buffer, refreshed every ~3.5 s or upon
  JEV / manual state changes). Lovense clocks those steps locally over BLE with
  zero Wi-Fi jitter.
- Direct ``set_level`` clients receive un-quantized levels without an intermediate
  float deadband that would beat against 0..20 integer rounding.
"""
from __future__ import annotations

import asyncio
import math
import time

from .patterns import PATTERN_SPECS, VibeState, normalize_pattern

KEEPALIVE_S = 0.85
PATTERN_STEP_MS = 120
PATTERN_STEP_S = PATTERN_STEP_MS / 1000.0
PATTERN_WINDOW_STEPS = 45               # 45 * 120 ms = 5.40 s (< Lovense 50-step max)
PATTERN_ACTIVE_S = 5.0                  # < 5.40 s so Lovense never wraps around to step 0
PATTERN_REFRESH_S = 3.48                # refresh ~1.5 s before buffer expires
MIN_CHANGE_RESEND_S = 0.12              # coalesce rapid manual slider drags
PREVIEW_RAMP_MS = 380.0                 # silky entrance for previews
PREVIEW_RELEASE_S = 0.90                # soft ~7.5-step cosine feather at end of preview

RESPONSE_FEEL_RAMP_MS: dict[str, float] = {
    "silky": 900.0,
    "natural": 420.0,
    "snappy": 140.0,
}


def _slew_step(current: float, target: float, dt: float, ramp_ms: float) -> float:
    if ramp_ms <= 0.0:
        return target
    if dt <= 0.0:
        return current
    max_step = 100.0 * (dt * 1000.0) / ramp_ms
    delta = target - current
    if abs(delta) <= max_step:
        return target
    return current + (max_step if delta > 0 else -max_step)


class DeviceManager:
    def __init__(self, vibe: VibeState, clients: list, get_config, clock=time.monotonic):
        self.vibe = vibe
        self.clients = clients
        self._get_config = get_config
        self._clock = clock
        self.story_cap: float = 100.0
        self.envelope: float = 0.0          # slewed strength, 0..100
        self.current_level: float = 0.0     # live UI output level, 0..1
        self._last_sent: float = 0.0
        self._last_send_time: float = 0.0
        self._last_tick: float | None = None
        self._task: asyncio.Task | None = None
        self._io_tasks: set[asyncio.Task] = set()

        # Buffered Pattern stream tracking
        self._pattern_stream_active: bool = False
        self._last_stream_sig: tuple | None = None
        self._last_pattern_send_at: float = -999.0

        # Conflict-free temporary preview state (for Test Buzz & Pattern Gallery)
        self._preview_id: int = 0
        self._preview_until: float = 0.0
        self._preview_vibe: VibeState | None = None
        self._preview_envelope: float = 0.0

    # ------------------------------------------------------------------ loop

    def ensure_running(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())

    def _spawn_io(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._io_tasks.add(task)
        task.add_done_callback(self._io_tasks.discard)

    async def run(self) -> None:
        next_tick = self._clock()
        while True:
            now = self._clock()
            try:
                await self.tick(now=now, background_io=True)
            except Exception:
                pass  # loop must never die; client errors are caught internally
            hz = float((self._get_config() or {}).get("tick_hz", 20) or 20)
            period = 1.0 / max(2.0, min(30.0, hz))
            next_tick = max(now + 0.005, next_tick + period)
            await asyncio.sleep(max(0.005, next_tick - self._clock()))

    def _ramp_ms(self, cfg: dict) -> float:
        if "ramp_ms" in cfg and cfg["ramp_ms"] is not None:
            return max(0.0, float(cfg["ramp_ms"]))
        feel = str(cfg.get("response_feel", "natural"))
        return RESPONSE_FEEL_RAMP_MS.get(feel, 420.0)

    def _preview_feather(self, remaining_s: float) -> float:
        """Gentle cosine fade-out on the last 360 ms of a preview when returning to silence."""
        if self.vibe.vibe_on and self.vibe.strength > 0.5:
            return 1.0
        if remaining_s >= PREVIEW_RELEASE_S:
            return 1.0
        u = max(0.0, min(1.0, remaining_s / PREVIEW_RELEASE_S))
        return 0.5 - 0.5 * math.cos(math.pi * u)

    async def tick(self, now: float | None = None, *, background_io: bool = False) -> None:
        now = self._clock() if now is None else now
        dt = 0.0 if self._last_tick is None else max(0.0, min(0.5, now - self._last_tick))
        self._last_tick = now
        cfg = self._get_config() or {}
        contrast_mode = str(cfg.get("contrast_mode", "balanced"))
        ramp_ms = self._ramp_ms(cfg)
        cap = (float(cfg.get("master_cap_global", 100)) / 100.0) * (float(self.story_cap) / 100.0)
        cap = max(0.0, min(1.0, cap))

        in_preview = self._preview_vibe is not None and now < self._preview_until
        if in_preview:
            pv = self._preview_vibe
            assert pv is not None
            # Ramp smoothly in over 220 ms; follow afterglow's gentle decay downward
            if pv.pattern == "afterglow" and self._preview_envelope > pv.strength:
                self._preview_envelope = pv.strength
            else:
                self._preview_envelope = _slew_step(
                    self._preview_envelope,
                    pv.strength,
                    dt if dt > 0 else 0.05,
                    PREVIEW_RAMP_MS,
                )
            norm_i = (self._preview_envelope / 100.0) * cap
            wave = pv.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
            if pv.pattern == "afterglow" and self._preview_envelope > pv.strength:
                self._preview_envelope = pv.strength
            feather = self._preview_feather(self._preview_until - now)
            level = max(0.0, min(1.0, wave * norm_i * feather))
            self.current_level = level
            sig = ("preview", self._preview_id, round(cap, 3), contrast_mode)
        else:
            if self._preview_vibe is not None:
                self._preview_vibe = None
                self._preview_envelope = 0.0

            target = self.vibe.strength if self.vibe.vibe_on else 0.0
            if not self.vibe.vibe_on:
                self.envelope = 0.0
            elif self.vibe.pattern == "afterglow" and self.envelope > target and target > 0.0:
                self.envelope = target
            else:
                self.envelope = _slew_step(self.envelope, target, dt, ramp_ms)

            norm_i = (self.envelope / 100.0) * cap
            if self.envelope <= 0.001:
                wave = 0.0
            else:
                wave = self.vibe.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode)
                if self.vibe.pattern == "afterglow" and self.envelope > self.vibe.strength:
                    self.envelope = self.vibe.strength
            level = max(0.0, min(1.0, wave * norm_i))
            self.current_level = level
            sig = (
                "live",
                self.vibe.version,
                self.vibe.vibe_on,
                round(cap, 3),
                contrast_mode,
                round(ramp_ms, 0),
            )

        await self._transmit(
            level=level,
            now=now,
            sig=sig,
            cap=cap,
            contrast_mode=contrast_mode,
            ramp_ms=ramp_ms,
            background_io=background_io,
        )

    def _build_lookahead_window(
        self,
        *,
        now: float,
        lead_s: float,
        cap: float,
        contrast_mode: str,
        ramp_ms: float,
    ) -> tuple[list[float], float]:
        """Simulate ``PATTERN_WINDOW_STEPS`` ahead at ``PATTERN_STEP_S`` (120 ms)
        without mutating live oscillator state."""
        sim_vibe = self.vibe.clone()
        sim_env = self.envelope
        sim_pv = self._preview_vibe.clone() if (self._preview_vibe is not None and now < self._preview_until) else None
        sim_pv_env = self._preview_envelope
        pv_until = self._preview_until
        sim_t = now

        def advance_sim(step_dt: float) -> float:
            nonlocal sim_env, sim_pv, sim_pv_env, sim_t
            sim_t += step_dt
            if sim_pv is not None and sim_t < pv_until:
                if sim_pv.pattern == "afterglow" and sim_pv_env > sim_pv.strength:
                    sim_pv_env = sim_pv.strength
                else:
                    sim_pv_env = _slew_step(sim_pv_env, sim_pv.strength, step_dt, PREVIEW_RAMP_MS)
                ni = (sim_pv_env / 100.0) * cap
                w = sim_pv.advance_and_sample(step_dt, ni, contrast_mode=contrast_mode)
                if sim_pv.pattern == "afterglow" and sim_pv_env > sim_pv.strength:
                    sim_pv_env = sim_pv.strength
                feather = self._preview_feather(pv_until - sim_t)
                return max(0.0, min(1.0, w * ni * feather))

            sim_pv = None
            target = sim_vibe.strength if sim_vibe.vibe_on else 0.0
            if not sim_vibe.vibe_on:
                sim_env = 0.0
            elif sim_vibe.pattern == "afterglow" and sim_env > target and target > 0.0:
                sim_env = target
            else:
                sim_env = _slew_step(sim_env, target, step_dt, ramp_ms)
            if sim_env <= 0.001:
                return 0.0
            ni = (sim_env / 100.0) * cap
            w = sim_vibe.advance_and_sample(step_dt, ni, contrast_mode=contrast_mode)
            if sim_vibe.pattern == "afterglow" and sim_env > sim_vibe.strength:
                sim_env = sim_vibe.strength
            return max(0.0, min(1.0, w * ni))

        levels: list[float] = []
        if lead_s > 0.002:
            levels.append(round(advance_sim(lead_s), 4))
        else:
            levels.append(round(self.current_level, 4))
        for _ in range(PATTERN_WINDOW_STEPS - 1):
            levels.append(round(advance_sim(PATTERN_STEP_S), 4))

        # Determine active duration: if the buffer decays to silence and stays 0,
        # size active_seconds cleanly around the non-zero span.
        nonzero_indices = [i for i, v in enumerate(levels) if v > 0.004]
        if not nonzero_indices:
            return levels, 0.0
        last_nz = nonzero_indices[-1]
        if last_nz < len(levels) - 3 and levels[-1] <= 0.004:
            active_s = min(PATTERN_ACTIVE_S, (last_nz + 1) * PATTERN_STEP_S)
        else:
            active_s = PATTERN_ACTIVE_S
        return levels, active_s

    async def _transmit(
        self,
        *,
        level: float,
        now: float,
        sig: tuple,
        cap: float,
        contrast_mode: str,
        ramp_ms: float,
        background_io: bool,
    ) -> None:
        pattern_clients = [c for c in self.clients if getattr(c, "supports_pattern_stream", False)]
        direct_clients = [c for c in self.clients if not getattr(c, "supports_pattern_stream", False)]

        # 1. Direct step clients (e.g. unit-test FakeClient or Function-mode fallback)
        if direct_clients:
            if level <= 0.0005:
                if self._last_sent > 0.0:
                    self._last_sent = 0.0
                    await self._run_clients([c.stop() for c in direct_clients], background_io)
            elif abs(level - self._last_sent) > 1e-4 or (now - self._last_send_time) > KEEPALIVE_S:
                self._last_sent = level
                self._last_send_time = now
                await self._run_clients([c.set_level(level) for c in direct_clients], background_io)

        # 2. Buffered Pattern-stream clients (Lovense)
        if not pattern_clients:
            return

        active_output = (
            (self._preview_vibe is not None and now < self._preview_until)
            or (self.vibe.vibe_on and (self.vibe.strength > 0.5 or self.envelope > 0.1))
        )
        if not active_output and level <= 0.004:
            if self._pattern_stream_active:
                self._pattern_stream_active = False
                self._last_stream_sig = None
                await self._run_clients([c.stop() for c in pattern_clients], background_io)
            return

        sig_changed = sig != self._last_stream_sig
        since_last = now - self._last_pattern_send_at
        need_send = (
            (not self._pattern_stream_active)
            or (sig_changed and since_last >= MIN_CHANGE_RESEND_S)
            or (since_last >= PATTERN_REFRESH_S)
        )
        if not need_send:
            return

        avg_rtt = sum(float(getattr(c, "last_rtt_s", 0.02)) for c in pattern_clients) / len(pattern_clients)
        lead_s = min(0.12, max(0.0, avg_rtt * 0.5)) if self._pattern_stream_active else 0.0
        levels, active_s = self._build_lookahead_window(
            now=now,
            lead_s=lead_s,
            cap=cap,
            contrast_mode=contrast_mode,
            ramp_ms=ramp_ms,
        )
        if active_s <= 0.0:
            if self._pattern_stream_active:
                self._pattern_stream_active = False
                self._last_stream_sig = None
                await self._run_clients([c.stop() for c in pattern_clients], background_io)
            return

        self._pattern_stream_active = True
        self._last_stream_sig = sig
        self._last_pattern_send_at = now
        await self._run_clients(
            [
                c.send_pattern(levels, interval_ms=PATTERN_STEP_MS, active_seconds=active_s)
                for c in pattern_clients
            ],
            background_io,
        )

    async def _run_clients(self, coros: list, background_io: bool) -> None:
        if not coros:
            return
        if background_io:
            for coro in coros:
                self._spawn_io(coro)
        else:
            for coro in coros:
                await coro

    async def _send_stop(self) -> None:
        self._pattern_stream_active = False
        self._last_stream_sig = None
        for client in self.clients:
            await client.stop()

    # --------------------------------------------------------------- controls

    def start_preview(
        self,
        strength: float = 55.0,
        pattern: str = "velvet",
        seconds: float = 4.8,
        pace: float | None = None,
    ) -> None:
        """Run a temporary pattern/test preview cleanly inside the loop."""
        pat = normalize_pattern(pattern)
        pv = VibeState(clock=self._clock)
        pv.set_target(
            max(5.0, min(100.0, float(strength))),
            pat,
            source=f"preview:{pat}",
            pace=PATTERN_SPECS[pat]["default_pace"] if pace is None else pace,
        )
        self._preview_id += 1
        self._preview_vibe = pv
        self._preview_envelope = min(self.envelope, pv.strength)
        self._preview_until = self._clock() + max(0.5, min(30.0, float(seconds)))
        # Reset debounce timer so an explicit user click dispatches on the very next tick
        self._last_pattern_send_at = -999.0

    async def test_buzz(self, strength: float = 55.0, seconds: float = 2.4, pattern: str = "swell") -> None:
        """Schedule a smooth test swell preview via the loop."""
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
            self._last_sent = 0.0
            await self._send_stop()
        else:
            self._last_pattern_send_at = -999.0

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
