"""The single 20 Hz control loop that owns oscillator state and device streams.

Gapless Lovense playback design:
- Instead of refreshing a short buffer every ~3.5 s (which caused Lovense to
  reset its BLE stream and pause briefly on each refresh), ``LovenseClient``
  receives a 50-step loop-seamless phrase once with ``timeSec: 0`` and
  ``stopPrevious: 0``. Lovense loops the phrase natively in hardware with ZERO
  periodic network refreshes until JEV or the user actually changes the state.
- Story ``afterglow`` wind-downs send a single 9.5 s (50 × 190 ms) decaying
  packet ending in zeros, also requiring zero mid-fade refreshes.
- Toy Studio ``Feel`` previews default to ``seconds=0`` (looping indefinitely
  until stopped or switched).
"""
from __future__ import annotations

import asyncio
import math
import time

from .patterns import (
    PATTERN_SPECS,
    VibeState,
    all_pattern_specs,
    build_lovense_phrase,
    build_lovense_phrase_with_phase,
    normalize_arc,
    normalize_pattern,
    sentence_arc_gain,
)

KEEPALIVE_S = 0.85
MIN_CHANGE_RESEND_S = 0.12              # coalesce rapid manual slider drags
MIN_SAME_PATTERN_RESEND_S = 0.35        # avoid double-tapping BLE on same-pattern nudges
PREVIEW_RAMP_MS = 350.0
PREVIEW_RELEASE_S = 0.80

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

        # Gapless hardware-loop & live-stream tracking
        self._live_stream_active: bool = False
        self._pattern_stream_active: bool = False
        self._sent_mode_key: tuple | None = None
        self._sent_pattern: str = ""
        self._sent_eff_norm: float = -1.0
        self._sent_pace: float = -1.0
        self._sent_arc: str = "steady"
        self._last_pattern_send_at: float = -999.0
        self._sentence_settle_after_s: float = 999.0
        self._loop_needs_settle: bool = False
        self._active_levels: list[float] = []
        self._active_interval_ms: int = 150
        self._active_started_at: float = 0.0
        self._active_u0: float = 0.0
        self._active_cue_id: object = None
        self._cue_entry_strength: float = 0.0

        # Conflict-free preview state (seconds <= 0 => loop until stopped)
        self._preview_id: int = 0
        self._preview_loop: bool = False
        self._preview_seconds: float = 0.0
        self._preview_until: float = 0.0
        self._preview_vibe: VibeState | None = None
        self._preview_pattern_def: dict | None = None
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
                pass
            hz = float((self._get_config() or {}).get("tick_hz", 20) or 20)
            period = 1.0 / max(2.0, min(30.0, hz))
            next_tick = max(now + 0.005, next_tick + period)
            await asyncio.sleep(max(0.005, next_tick - self._clock()))

    def _ramp_ms(self, cfg: dict) -> float:
        if "ramp_ms" in cfg and cfg["ramp_ms"] is not None:
            return max(0.0, float(cfg["ramp_ms"]))
        feel = str(cfg.get("response_feel", "natural"))
        return RESPONSE_FEEL_RAMP_MS.get(feel, 420.0)

    def _compute_sentence_target(self, base_target: float, now: float) -> float:
        """Continuous C0/C1 sentence envelope anchored to live entry strength (zero boundary drops)."""
        if base_target <= 0.5 or not self.vibe.params.get("sentence_cue") or self.vibe.pattern == "afterglow":
            self._active_cue_id = None
            return base_target

        cue_id = self.vibe.params.get("cue_id")
        if cue_id != self._active_cue_id:
            self._active_cue_id = cue_id
            self._cue_entry_strength = self.envelope

        elapsed_cue = max(0.0, now - self.vibe.latched_at)
        cue_dur = max(1.3, float(self.vibe.params.get("duration_s") or 3.4))
        p = min(1.0, elapsed_cue / cue_dur)
        s_curve = 0.5 - 0.5 * math.cos(math.pi * p)
        arc = normalize_arc(self.vibe.params.get("arc"))
        entry_s = self._cue_entry_strength

        if arc == "rise":
            start_s = entry_s if entry_s > 2.0 else (base_target * 0.74)
            if base_target >= start_s * 0.92:
                end_s = min(100.0, max(base_target * 1.05, start_s + min(7.0, base_target * 0.09)))
            else:
                end_s = min(100.0, base_target * 1.04)
            return min(100.0, max(0.0, start_s + (end_s - start_s) * s_curve))

        if arc == "surge":
            start_s = entry_s if entry_s > 2.0 else (base_target * 0.80)
            base_glide = start_s + (base_target - start_s) * s_curve
            peak_s = min(100.0, max(base_target, start_s) * 1.12)
            crest_bump = max(0.0, peak_s - max(start_s, base_target)) + 0.08 * base_target
            return min(100.0, max(0.0, base_glide + crest_bump * math.sin(math.pi * (p ** 0.85))))

        if arc == "ease":
            start_s = entry_s if entry_s > 2.0 else base_target
            end_s = base_target * 0.84
            return min(100.0, max(0.0, start_s + (end_s - start_s) * s_curve))

        return base_target

    def _preview_feather(self, remaining_s: float) -> float:
        if self._preview_loop:
            return 1.0
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

        in_preview = self._preview_vibe is not None and (self._preview_loop or now < self._preview_until)
        if in_preview:
            pv = self._preview_vibe
            assert pv is not None
            auto_decay = not self._preview_loop
            if auto_decay and pv.pattern == "afterglow" and self._preview_envelope > pv.strength:
                self._preview_envelope = pv.strength
            else:
                self._preview_envelope = _slew_step(
                    self._preview_envelope,
                    pv.strength,
                    dt if dt > 0 else 0.05,
                    PREVIEW_RAMP_MS,
                )
            norm_i = (self._preview_envelope / 100.0) * cap
            wave = pv.advance_and_sample(dt, norm_i, contrast_mode=contrast_mode, auto_decay=auto_decay)
            if auto_decay and pv.pattern == "afterglow" and self._preview_envelope > pv.strength:
                self._preview_envelope = pv.strength
            feather = self._preview_feather(self._preview_until - now)
            level = max(0.0, min(1.0, wave * norm_i * feather))
            self.current_level = level
        else:
            if self._preview_vibe is not None:
                self._preview_vibe = None
                self._preview_pattern_def = None
                self._preview_loop = False
                self._preview_envelope = 0.0

            base_target = self.vibe.strength if self.vibe.vibe_on else 0.0
            target = self._compute_sentence_target(base_target, now)

            if not self.vibe.vibe_on:
                self.envelope = 0.0
                self._active_cue_id = None
                self._cue_entry_strength = 0.0
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

        await self._transmit(
            level=level,
            now=now,
            in_preview=in_preview,
            cap=cap,
            contrast_mode=contrast_mode,
            background_io=background_io,
        )

    def _record_active_phrase(self, levels: list[float], interval_ms: int, now: float, u0: float = 0.0) -> None:
        self._active_levels = list(levels)
        self._active_interval_ms = max(50, int(interval_ms))
        self._active_started_at = now
        self._active_u0 = float(u0) % 1.0

    def _clear_active_phrase(self) -> None:
        self._active_levels = []
        self._active_u0 = 0.0
        self._loop_needs_settle = False

    def _live_hw_state(self, now: float, pattern_clients: list) -> tuple[float | None, float | None]:
        """Estimate the motor's live output level and normalized phrase phase right now."""
        if not self._pattern_stream_active or not self._active_levels:
            return None, None
        rtt = 0.04
        if pattern_clients:
            rtt = min(0.18, max(0.015, float(getattr(pattern_clients[0], "last_rtt_s", 0.04) or 0.04)))
        elapsed_ms = max(0.0, (now - self._active_started_at - 0.5 * rtt) * 1000.0)
        step_pos = elapsed_ms / max(50, self._active_interval_ms)
        n = len(self._active_levels)
        idx0 = int(step_pos) % n
        idx1 = (idx0 + 1) % n
        frac = step_pos - int(step_pos)
        live_lv = (1.0 - frac) * self._active_levels[idx0] + frac * self._active_levels[idx1]
        live_u = (self._active_u0 + (step_pos / n)) % 1.0
        return live_lv, live_u

    async def _transmit(
        self,
        *,
        level: float,
        now: float,
        in_preview: bool,
        cap: float,
        contrast_mode: str,
        background_io: bool,
    ) -> None:
        if in_preview:
            live_stream_clients = []
            pattern_clients = [c for c in self.clients if getattr(c, "supports_pattern_stream", False)]
            direct_clients = [c for c in self.clients if c not in pattern_clients]
        else:
            live_stream_clients = [
                c
                for c in self.clients
                if getattr(c, "prefers_live_story_stream", False) and hasattr(c, "send_live_level")
            ]
            pattern_clients = [
                c
                for c in self.clients
                if c not in live_stream_clients and getattr(c, "supports_pattern_stream", False)
            ]
            direct_clients = [
                c
                for c in self.clients
                if c not in live_stream_clients and c not in pattern_clients
            ]

        # 1. Direct step clients (unit-test FakeClient or legacy Function fallback)
        if direct_clients:
            if level <= 0.0005:
                if self._last_sent > 0.0:
                    self._last_sent = 0.0
                    await self._run_clients([c.stop() for c in direct_clients], background_io)
            elif abs(level - self._last_sent) > 1e-4 or (now - self._last_send_time) > KEEPALIVE_S:
                self._last_sent = level
                self._last_send_time = now
                await self._run_clients([c.set_level(level) for c in direct_clients], background_io)

        # 2. Seamless Live-Stream Story clients (`Function` stopPrevious=0, timeSec=0 -> zero BLE pattern resets)
        if live_stream_clients:
            target_on = self.vibe.vibe_on and self.vibe.strength > 0.5
            if not target_on and self.envelope <= 0.2 and level <= 0.0005:
                if self._live_stream_active or self._pattern_stream_active:
                    self._live_stream_active = False
                    if not pattern_clients:
                        self._pattern_stream_active = False
                        self._sent_mode_key = None
                        self._sent_eff_norm = -1.0
                        self._clear_active_phrase()
                    await self._run_clients([c.stop() for c in live_stream_clients], background_io)
            elif target_on or self.envelope > 0.2 or level > 0.0005:
                if not pattern_clients and self._pattern_stream_active:
                    self._pattern_stream_active = False
                    self._sent_mode_key = None
                    self._clear_active_phrase()
                self._live_stream_active = True
                allow_valley = bool(target_on and self.envelope > 1.0)
                await self._run_clients(
                    [c.send_live_level(level, allow_zero_valley=allow_valley) for c in live_stream_clients],
                    background_io,
                )

        # 3. Gapless Lovense Pattern-stream clients (Toy Studio previews + optional Hardware Loop mode)
        if not pattern_clients:
            return

        live_lv, live_u = self._live_hw_state(now, pattern_clients)

        if in_preview and self._preview_vibe is not None:
            pv = self._preview_vibe
            eff_norm = max(0.0, min(1.0, (pv.strength / 100.0) * cap))
            mode_key = ("preview", self._preview_id, round(cap, 3), contrast_mode)
            if mode_key == self._sent_mode_key and self._pattern_stream_active:
                return  # Already playing natively on Lovense — send ZERO refreshes!

            if self._preview_loop:
                levels, interval_ms, is_loop, u0 = build_lovense_phrase_with_phase(
                    pv.pattern,
                    intensity_norm=eff_norm,
                    pace=pv.pace,
                    contrast_mode=contrast_mode,
                    loop=True,
                    pattern_def=self._preview_pattern_def,
                )
                active_s = None
            else:
                # Finite preview (e.g. quick Test Buzz): fit entire preview into one 50-step packet
                step_ms = max(110, int(math.ceil((self._preview_seconds * 1000.0) / 46.0)))
                step_dt = step_ms / 1000.0
                sim_pv = pv.clone()
                sim_env = 0.0
                levels = []
                for k in range(50):
                    t_k = (k + 1) * step_dt
                    if t_k > self._preview_seconds:
                        levels.append(0.0)
                        continue
                    sim_env = _slew_step(sim_env, sim_pv.strength, step_dt, PREVIEW_RAMP_MS)
                    ni = (sim_env / 100.0) * cap
                    w = sim_pv.advance_and_sample(step_dt, ni, contrast_mode=contrast_mode)
                    rem = max(0.0, self._preview_seconds - t_k)
                    feather = 1.0 if rem >= PREVIEW_RELEASE_S else (0.5 - 0.5 * math.cos(math.pi * (rem / PREVIEW_RELEASE_S)))
                    levels.append(round(max(0.0, min(1.0, w * ni * feather)), 4))
                interval_ms = step_ms
                is_loop = False
                active_s = self._preview_seconds
                u0 = 0.0

            self._pattern_stream_active = True
            self._sent_mode_key = mode_key
            self._sent_pattern = pv.pattern
            self._sent_eff_norm = eff_norm
            self._sent_pace = pv.pace
            self._sent_arc = "steady"
            self._last_pattern_send_at = now
            self._record_active_phrase(levels, interval_ms, now, u0)
            await self._run_clients(
                [
                    c.send_pattern(levels, interval_ms=interval_ms, loop=is_loop, active_seconds=active_s)
                    for c in pattern_clients
                ],
                background_io,
            )
            return

        # Live Story / Manual state
        target = self.vibe.strength if self.vibe.vibe_on else 0.0
        if target <= 0.5:
            # Wait for live envelope ramp-down before sending Stop once
            if self.envelope <= 0.2 and self._pattern_stream_active:
                self._pattern_stream_active = False
                self._sent_mode_key = None
                self._sent_eff_norm = -1.0
                self._clear_active_phrase()
                await self._run_clients([c.stop() for c in pattern_clients], background_io)
            return

        if self.vibe.pattern == "afterglow":
            # One-shot 9.5s wind-down: send ONCE per latch, never refresh mid-decay
            start_i = self.vibe._afterglow_start_strength or target
            eff_norm = max(0.0, min(1.0, (start_i / 100.0) * cap))
            mode_key = ("afterglow", round(self.vibe.latched_at, 3), round(cap, 3), contrast_mode)
            if mode_key == self._sent_mode_key and self._pattern_stream_active:
                return
            levels, interval_ms, _, u0 = build_lovense_phrase_with_phase(
                "afterglow",
                intensity_norm=eff_norm,
                pace=self.vibe.pace,
                contrast_mode=contrast_mode,
                loop=False,
                entry_norm=live_lv,
            )
            self._pattern_stream_active = True
            self._sent_mode_key = mode_key
            self._sent_pattern = "afterglow"
            self._sent_eff_norm = eff_norm
            self._sent_pace = self.vibe.pace
            self._sent_arc = "ease"
            self._last_pattern_send_at = now
            self._record_active_phrase(levels, interval_ms, now, u0)
            await self._run_clients(
                [
                    c.send_pattern(levels, interval_ms=interval_ms, loop=False, active_seconds=9.2)
                    for c in pattern_clients
                ],
                background_io,
            )
            return

        # Shaped per-sentence Stage cue: phase-aligned + output-bridged with zero unnecessary resets
        base_eff_norm = max(0.0, min(1.0, (self.vibe.strength / 100.0) * cap))
        same_pat = bool(self._pattern_stream_active and self._sent_pattern == self.vibe.pattern)

        if self.vibe.params.get("sentence_cue") and self.vibe.pattern != "afterglow":
            cue_id = self.vibe.params.get("cue_id")
            arc = normalize_arc(self.vibe.params.get("arc"))
            dur_s = max(1.3, float(self.vibe.params.get("duration_s") or 3.4))
            cue_key = ("sentence", cue_id, round(cap, 3), contrast_mode)
            settled_key = ("sentence_settled", cue_id, round(cap, 3), contrast_mode)
            end_eff = max(0.0, min(1.0, base_eff_norm * sentence_arc_gain(arc, 1.0)))

            if self._pattern_stream_active and self._sent_mode_key == settled_key:
                return

            if self._pattern_stream_active and self._sent_mode_key == cue_key:
                if (now - self._last_pattern_send_at) < self._sentence_settle_after_s:
                    return
                # Sentence held past shaped contour window: settle once at exact live hardware phase
                levels, interval_ms, _, u0 = build_lovense_phrase_with_phase(
                    self.vibe.pattern,
                    intensity_norm=end_eff,
                    pace=self.vibe.pace,
                    contrast_mode=contrast_mode,
                    loop=True,
                    entry_norm=live_lv,
                    live_phase=live_u,
                    same_pattern=True,
                )
                self._sent_mode_key = settled_key
                self._sent_pattern = self.vibe.pattern
                self._sent_eff_norm = end_eff
                self._sent_pace = self.vibe.pace
                self._sent_arc = "steady"
                self._last_pattern_send_at = now
                self._loop_needs_settle = False
                self._record_active_phrase(levels, interval_ms, now, u0)
                await self._run_clients(
                    [c.send_pattern(levels, interval_ms=interval_ms, loop=True) for c in pattern_clients],
                    background_io,
                )
                return

            if (
                same_pat
                and arc in ("steady", "ease")
                and abs(end_eff - self._sent_eff_norm) < 0.055
                and abs(self.vibe.pace - self._sent_pace) < 0.14
            ):
                # Pattern & strength already match within ~1 Lovense step: keep hardware loop untouched!
                already_clean = (
                    self._sent_mode_key is not None
                    and self._sent_mode_key[0] in ("loop", "sentence_settled")
                    and not self._loop_needs_settle
                )
                self._sent_mode_key = settled_key if already_clean else cue_key
                return

            entry_lv = (
                live_lv
                if (self._pattern_stream_active and live_lv is not None)
                else max(0.0, min(1.0, (self.envelope / 100.0) * cap))
            )
            levels, interval_ms, _, u0 = build_lovense_phrase_with_phase(
                self.vibe.pattern,
                intensity_norm=base_eff_norm,
                pace=self.vibe.pace,
                contrast_mode=contrast_mode,
                loop=True,
                entry_norm=entry_lv,
                arc=arc,
                duration_s=dur_s,
                live_phase=live_u,
                same_pattern=same_pat,
            )
            needs_settle = abs(levels[0] - levels[-1]) > 0.10
            self._pattern_stream_active = True
            self._sent_mode_key = cue_key if needs_settle else settled_key
            self._sent_pattern = self.vibe.pattern
            self._sent_eff_norm = end_eff
            self._sent_pace = self.vibe.pace
            self._sent_arc = arc
            self._last_pattern_send_at = now
            self._loop_needs_settle = needs_settle
            self._sentence_settle_after_s = max(2.2, ((len(levels) * interval_ms) / 1000.0) * 0.86)
            self._record_active_phrase(levels, interval_ms, now, u0)
            await self._run_clients(
                [c.send_pattern(levels, interval_ms=interval_ms, loop=True) for c in pattern_clients],
                background_io,
            )
            return

        # Steady continuous / Classic Feed pattern: loop natively in hardware (timeSec: 0)
        eff_norm = base_eff_norm
        mode_key = ("loop", self.vibe.pattern, contrast_mode)

        # If an entry-feathered steady loop was held past its phrase window, settle cleanly once
        if (
            self._pattern_stream_active
            and mode_key == self._sent_mode_key
            and self._loop_needs_settle
            and (now - self._last_pattern_send_at) >= self._sentence_settle_after_s
            and abs(eff_norm - self._sent_eff_norm) < 0.055
        ):
            levels, interval_ms, _, u0 = build_lovense_phrase_with_phase(
                self.vibe.pattern,
                intensity_norm=eff_norm,
                pace=self.vibe.pace,
                contrast_mode=contrast_mode,
                loop=True,
                live_phase=live_u,
                same_pattern=True,
            )
            self._loop_needs_settle = False
            self._last_pattern_send_at = now
            self._record_active_phrase(levels, interval_ms, now, u0)
            await self._run_clients(
                [c.send_pattern(levels, interval_ms=interval_ms, loop=True) for c in pattern_clients],
                background_io,
            )
            return

        same_loop = (
            same_pat
            and self._sent_mode_key is not None
            and self._sent_mode_key[0] in ("loop", "sentence_settled")
            and abs(eff_norm - self._sent_eff_norm) < 0.055
            and abs(self.vibe.pace - self._sent_pace) < 0.15
        )
        if same_loop:
            self._sent_mode_key = mode_key
            return

        min_gap = MIN_SAME_PATTERN_RESEND_S if same_pat else MIN_CHANGE_RESEND_S
        if (now - self._last_pattern_send_at) < min_gap and self._pattern_stream_active:
            return

        entry_lv = live_lv if (self._pattern_stream_active and live_lv is not None and live_lv > 0.035) else None
        levels, interval_ms, _, u0 = build_lovense_phrase_with_phase(
            self.vibe.pattern,
            intensity_norm=eff_norm,
            pace=self.vibe.pace,
            contrast_mode=contrast_mode,
            loop=True,
            entry_norm=entry_lv,
            live_phase=live_u,
            same_pattern=same_pat,
        )
        needs_settle = bool(entry_lv is not None and abs(levels[0] - levels[-1]) > 0.12)
        self._pattern_stream_active = True
        self._sent_mode_key = mode_key
        self._sent_pattern = self.vibe.pattern
        self._sent_eff_norm = eff_norm
        self._sent_pace = self.vibe.pace
        self._sent_arc = "steady"
        self._last_pattern_send_at = now
        self._loop_needs_settle = needs_settle
        self._sentence_settle_after_s = max(2.2, ((len(levels) * interval_ms) / 1000.0) * 0.86)
        self._record_active_phrase(levels, interval_ms, now, u0)
        await self._run_clients(
            [
                c.send_pattern(levels, interval_ms=interval_ms, loop=True)
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
        self._live_stream_active = False
        self._pattern_stream_active = False
        self._sent_mode_key = None
        self._sent_eff_norm = -1.0
        self._clear_active_phrase()
        for client in self.clients:
            await client.stop()

    # --------------------------------------------------------------- controls

    def notify_pattern_updated(self, pattern_id: str | None = None) -> None:
        """Force Lovense stream refresh if the edited pattern is actively playing."""
        if pattern_id is None or self._sent_pattern == pattern_id or self.vibe.pattern == pattern_id:
            self._sent_mode_key = None
            self._last_pattern_send_at = -999.0

    def start_preview(
        self,
        strength: float = 55.0,
        pattern: str = "velvet",
        seconds: float = 0.0,
        pace: float | None = None,
        pattern_def: dict | None = None,
    ) -> None:
        """Start a pattern preview. ``seconds <= 0`` loops indefinitely until stopped."""
        pv = VibeState(clock=self._clock)
        pv.set_target(
            max(5.0, min(100.0, float(strength))),
            pattern,
            source=f"preview:{pattern}",
            pace=pace,
            pattern_def=pattern_def,
        )
        self._preview_id += 1
        self._preview_vibe = pv
        self._preview_pattern_def = pv._pattern_def
        self._preview_envelope = min(self.envelope, pv.strength)
        self._live_stream_active = False
        sec = float(seconds or 0.0)
        if sec <= 0.0:
            self._preview_loop = True
            self._preview_seconds = 0.0
            self._preview_until = math.inf
        else:
            self._preview_loop = False
            self._preview_seconds = max(0.5, min(30.0, sec))
            self._preview_until = self._clock() + self._preview_seconds
        self._last_pattern_send_at = -999.0

    async def test_buzz(self, strength: float = 55.0, seconds: float = 2.4, pattern: str = "swell") -> None:
        """Schedule a short finite test swell preview (for Connection Test button)."""
        self.start_preview(strength=strength, pattern=pattern, seconds=seconds)

    async def instant_stop(self) -> None:
        """Hard stop: cancel any preview, clear latch, zero output immediately."""
        self._preview_vibe = None
        self._preview_pattern_def = None
        self._preview_loop = False
        self._preview_seconds = 0.0
        self._preview_until = 0.0
        self._preview_envelope = 0.0
        self._active_cue_id = None
        self._cue_entry_strength = 0.0
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
            self._preview_pattern_def = None
            self._preview_loop = False
            self._preview_until = 0.0
            self._active_cue_id = None
            self._cue_entry_strength = 0.0
            self.envelope = 0.0
            self.current_level = 0.0
            self._last_sent = 0.0
            await self._send_stop()
        else:
            self._last_pattern_send_at = -999.0

    @property
    def preview_active(self) -> bool:
        return self._preview_vibe is not None and (self._preview_loop or self._clock() < self._preview_until)

    def status(self) -> dict:
        preview = None
        if self.preview_active and self._preview_vibe is not None:
            preview = {
                "pattern": self._preview_vibe.pattern,
                "strength": round(self._preview_vibe.strength, 1),
                "looping": self._preview_loop,
                "remaining_s": None if self._preview_loop else round(max(0.0, self._preview_until - self._clock()), 1),
            }
        return {
            "level": round(self.current_level, 3),
            "envelope": round(self.envelope, 1),
            "story_cap": self.story_cap,
            "vibe": self.vibe.snapshot(),
            "preview": preview,
            "backends": [c.status() for c in self.clients],
        }
