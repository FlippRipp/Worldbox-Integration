import pytest

from toylink.device_manager import DeviceManager
from toylink.patterns import VibeState


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeClient:
    name = "lovense"
    enabled = True
    connected = True

    def __init__(self):
        self.levels = []
        self.stops = 0
        self.last_error = ""

    async def set_level(self, frac):
        self.levels.append(round(frac, 4))

    async def stop(self):
        self.stops += 1

    def status(self):
        return {"name": self.name}


CONFIG = {
    "master_cap_global": 100,
    "ramp_ms": 500,
    "contrast_mode": "balanced",
    "tick_hz": 20,
}


def make_manager(config=None):
    clock = FakeClock()
    vibe = VibeState(clock=clock)
    client = FakeClient()
    cfg = dict(CONFIG if config is None else config)
    manager = DeviceManager(vibe, [client], lambda: cfg, clock=clock)
    return manager, vibe, client, clock, cfg


async def run_ticks(manager, clock, n, dt=0.05):
    for _ in range(n):
        clock.t += dt
        await manager.tick()


async def test_envelope_slews_toward_target():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(100, "velvet")
    await run_ticks(manager, clock, 2)        # 100 ms of a 500 ms ramp = +20
    assert manager.envelope == pytest.approx(20.0)
    await run_ticks(manager, clock, 6)
    assert manager.envelope == pytest.approx(80.0)
    await run_ticks(manager, clock, 3)
    assert manager.envelope == 100.0
    assert client.levels[-1] > 0.5


async def test_ramp_down_to_zero_sends_stop_once():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(100, "velvet")
    await run_ticks(manager, clock, 12)
    vibe.clear(source="jev:none")
    await run_ticks(manager, clock, 4)
    assert 0.0 < manager.envelope < 100.0
    await run_ticks(manager, clock, 8)
    assert manager.envelope == 0.0
    assert client.stops == 1
    await run_ticks(manager, clock, 6)
    assert client.stops == 1


async def test_instant_stop_bypasses_slew_and_cancels_preview():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    manager.start_preview(strength=90, pattern="crest", seconds=4.0)
    await run_ticks(manager, clock, 5)
    assert manager.preview_active is True
    assert manager.current_level > 0.3

    await manager.instant_stop()
    assert manager.preview_active is False
    assert manager.envelope == 0.0
    assert manager.current_level == 0.0
    assert client.stops == 1


async def test_preview_runs_cleanly_inside_loop_then_returns_to_latch():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    manager.start_preview(strength=80, pattern="crest", seconds=1.0)
    await run_ticks(manager, clock, 10)       # 0.5 s in -> active preview
    assert manager.preview_active is True
    assert manager.current_level > 0.25

    await run_ticks(manager, clock, 15)       # past 1.0 s -> preview ends cleanly
    assert manager.preview_active is False
    assert manager.current_level == 0.0
    assert client.stops >= 1


async def test_gate_off_immediate_on_ramps_back():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(80, "swell")
    await run_ticks(manager, clock, 12)
    await manager.set_gate(False)
    assert client.stops == 1
    assert manager.envelope == 0.0
    assert vibe.strength == 80.0
    await manager.set_gate(True)
    await run_ticks(manager, clock, 2)
    assert 0.0 < manager.envelope < 80.0


async def test_caps_multiply_and_feel_presets_work():
    cfg = {"master_cap_global": 50, "response_feel": "snappy", "tick_hz": 20}
    manager, vibe, client, clock, _ = make_manager(cfg)
    manager.story_cap = 50
    await manager.tick()
    vibe.set_target(100, "velvet")
    await run_ticks(manager, clock, 6)        # snappy 140 ms ramp finishes in 300 ms
    assert manager.envelope == 100.0
    assert client.levels[-1] <= 0.25 + 1e-4


async def test_pattern_stream_client_buffers_smoothly_without_per_tick_spam():
    class FakePatternClient:
        name = "lovense"
        enabled = True
        connected = True
        supports_pattern_stream = True
        last_rtt_s = 0.02

        def __init__(self):
            self.patterns = []
            self.stops = 0

        async def send_pattern(self, levels, *, interval_ms=135, loop=True, active_seconds=None):
            self.patterns.append((list(levels), interval_ms, loop, active_seconds))

        async def stop(self):
            self.stops += 1

        def status(self):
            return {"name": self.name}

    clock = FakeClock()
    vibe = VibeState(clock=clock)
    pclient = FakePatternClient()
    cfg = dict(CONFIG)
    manager = DeviceManager(vibe, [pclient], lambda: cfg, clock=clock)

    await manager.tick()
    # 1. Default seconds=0 preview loops indefinitely in ONE packet (zero 4s refreshes!)
    manager.start_preview(strength=75, pattern="afterglow", seconds=0)
    await run_ticks(manager, clock, 400)  # 20 FULL SECONDS @ 20 Hz!
    assert manager.preview_active is True
    assert manager.status()["preview"]["looping"] is True
    assert len(pclient.patterns) == 1
    levels, interval_ms, is_loop, _ = pclient.patterns[0]
    assert is_loop is True
    assert len(levels) == 50
    max_jump = max(abs(levels[i + 1] - levels[i]) for i in range(len(levels) - 1))
    assert max_jump < 0.14

    # Stopping explicitly cancels the looping preview and sends Stop once
    await manager.instant_stop()
    assert manager.preview_active is False
    assert pclient.stops == 1

    # 2. Steady story pattern also sends ONLY ONE packet over 20s (zero 4s refreshes!)
    pclient.patterns.clear()
    vibe.set_target(82, "crest")
    await run_ticks(manager, clock, 400)  # 20 FULL SECONDS @ 20 Hz!
    assert len(pclient.patterns) == 1

    # Tiny JEV jitter (< 4%) does not re-send or reset the hardware loop
    vibe.set_target(84, "crest")
    await run_ticks(manager, clock, 20)
    assert len(pclient.patterns) == 1

    # Meaningful JEV shift sends one clean update
    vibe.set_target(50, "swell")
    await run_ticks(manager, clock, 20)
    assert len(pclient.patterns) == 2

    # 3. Shaped per-sentence cues apply entry ramp + arc immediately and settle into steady loop if held > 7s
    pclient.patterns.clear()
    vibe.set_target(
        76,
        "velvet",
        {"sentence_cue": True, "cue_id": 1, "arc": "rise", "duration_s": 3.0},
        source="jev:velvet",
        pace=0.55,
    )
    await run_ticks(manager, clock, 40)  # 2.0s normal sentence reading -> single shaped packet
    assert len(pclient.patterns) == 1
    first_lv, _, _, _ = pclient.patterns[0]
    assert first_lv[0] < first_lv[25]  # Smooth entry ramp from previous 50% up toward rising 76%

    # Next sentence cue (even subtle change with 'ease' arc) updates immediately without 4.5% deadband skip
    vibe.set_target(
        74,
        "velvet",
        {"sentence_cue": True, "cue_id": 2, "arc": "ease", "duration_s": 2.8},
        source="jev:velvet",
        pace=0.50,
    )
    await run_ticks(manager, clock, 20)  # 1.0s
    assert len(pclient.patterns) == 2

    # Holding on Sentence 2 for 12s settles once into a pure steady loop and never spams again
    await run_ticks(manager, clock, 240)
    assert len(pclient.patterns) == 3

    # 4. Seamless handoff between different patterns (Velvet -> Ocean Swell -> Heartbeat)
    # bridges from live hardware level without starting on a 0% gap, and minor same-pattern
    # nudges do not interrupt the active hardware loop!
    pclient.patterns.clear()
    vibe.set_target(72, "velvet", {"sentence_cue": True, "cue_id": 10, "arc": "steady", "duration_s": 3.0})
    await run_ticks(manager, clock, 40)  # 2.0s in: Velvet actively spinning around ~0.62
    assert len(pclient.patterns) == 1

    # Minor same-pattern nudge (< 5%) sends ZERO reset packets
    vibe.set_target(74, "velvet", {"sentence_cue": True, "cue_id": 11, "arc": "steady", "duration_s": 3.0})
    await run_ticks(manager, clock, 40)
    assert len(pclient.patterns) == 1

    # Switch from Velvet (~62%) into Ocean Swell (85%): starts right on Swell's rising slope (> 0.50), NOT 0.0!
    vibe.set_target(85, "swell", {"sentence_cue": True, "cue_id": 12, "arc": "steady", "duration_s": 4.0})
    await run_ticks(manager, clock, 60)  # 3.0s into Swell
    assert len(pclient.patterns) == 2
    swell_lv, _, _, _ = pclient.patterns[-1]
    assert swell_lv[0] >= 0.50

    # Switch from active Swell into Heartbeat (85%): enters cleanly on active beat (> 0.45), NOT 0.0 diastole!
    vibe.set_target(85, "heartbeat", {"sentence_cue": True, "cue_id": 13, "arc": "steady", "duration_s": 3.2})
    await run_ticks(manager, clock, 20)
    assert len(pclient.patterns) == 3
    hb_lv, _, _, _ = pclient.patterns[-1]
    assert hb_lv[0] >= 0.45



