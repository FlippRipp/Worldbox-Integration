import pytest

from toylink.device_manager import DeviceManager
from toylink.patterns import VibeState


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeClient:
    name = "fake"
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


CONFIG = {"master_cap_global": 100, "ramp_ms": 500, "tick_hz": 10}


def make_manager(config=None):
    clock = FakeClock()
    vibe = VibeState(clock=clock)
    client = FakeClient()
    cfg = dict(CONFIG if config is None else config)
    manager = DeviceManager(vibe, [client], lambda: cfg, clock=clock)
    return manager, vibe, client, clock, cfg


async def run_ticks(manager, clock, n, dt=0.1):
    for _ in range(n):
        clock.t += dt
        await manager.tick()


async def test_envelope_slews_toward_target():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()                      # initialize tick clock
    vibe.set_target(100, "constant")
    await run_ticks(manager, clock, 1)        # 100 ms of a 500 ms ramp = +20
    assert manager.envelope == pytest.approx(20.0)
    await run_ticks(manager, clock, 3)
    assert manager.envelope == pytest.approx(80.0)
    await run_ticks(manager, clock, 2)
    assert manager.envelope == 100.0          # reaches target, no overshoot
    assert client.levels[-1] == 1.0


async def test_ramp_down_via_stop_word_then_zero_sent_once():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(100, "constant")
    await run_ticks(manager, clock, 6)        # fully up
    vibe.clear(source="keyword:r_sleep")      # stop-word: ramps down via slew
    await run_ticks(manager, clock, 2)
    assert manager.envelope == pytest.approx(60.0)  # descending, not instant
    await run_ticks(manager, clock, 4)
    assert manager.envelope == 0.0
    assert client.stops == 1                  # explicit stop exactly once
    await run_ticks(manager, clock, 5)
    assert client.stops == 1                  # and never again while silent


async def test_change_threshold_and_keepalive():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(50, "constant")
    await run_ticks(manager, clock, 6)        # ramp completes
    sends_after_ramp = len(client.levels)
    await run_ticks(manager, clock, 3)        # level stable, within threshold
    assert len(client.levels) == sends_after_ramp
    await run_ticks(manager, clock, 7)        # >0.9 s since last send
    assert len(client.levels) == sends_after_ramp + 1   # keepalive resend


async def test_instant_stop_bypasses_slew():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(100, "constant")
    await run_ticks(manager, clock, 6)
    await manager.instant_stop()
    assert manager.envelope == 0.0
    assert client.stops == 1
    assert vibe.strength == 0.0               # latch cleared too
    await run_ticks(manager, clock, 3)
    assert client.stops == 1


async def test_gate_off_immediate_on_ramps_back():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(80, "constant")
    await run_ticks(manager, clock, 6)
    await manager.set_gate(False)
    assert client.stops == 1                  # silenced immediately
    assert manager.envelope == 0.0
    assert vibe.strength == 80.0              # latch untouched
    await run_ticks(manager, clock, 3)
    assert manager.envelope == 0.0            # stays silent while gated
    await manager.set_gate(True)
    await run_ticks(manager, clock, 1)
    assert 0 < manager.envelope < 80.0        # ramping back up, not jumping
    await run_ticks(manager, clock, 5)
    assert manager.envelope == pytest.approx(80.0)


async def test_caps_multiply():
    manager, vibe, client, clock, cfg = make_manager(
        dict(CONFIG, master_cap_global=50))
    manager.story_cap = 50
    await manager.tick()
    vibe.set_target(100, "constant")
    await run_ticks(manager, clock, 6)
    assert client.levels[-1] == 0.25          # 1.0 × 0.5 × 0.5


async def test_pulse_edges_stay_crisp_after_ramp():
    manager, vibe, client, clock, _ = make_manager()
    await manager.tick()
    vibe.set_target(100, "pulse", {"pulse_on_ms": 300, "pulse_off_ms": 300})
    await run_ticks(manager, clock, 5)        # envelope reaches 100 inside "on"
    # 0.95 s into a 600 ms cycle = 350 ms in = off phase — full stop, not a ramp.
    clock.t = vibe.latched_at + 0.95
    await manager.tick()
    assert manager.current_level == 0.0
    assert client.stops == 1
    clock.t = vibe.latched_at + 1.25          # back in an on phase
    await manager.tick()
    assert client.levels[-1] == 1.0           # crisp full-level edge


async def test_zero_ramp_ms_means_instant_targets():
    manager, vibe, client, clock, _ = make_manager(dict(CONFIG, ramp_ms=0))
    await manager.tick()
    vibe.set_target(90, "constant")
    await run_ticks(manager, clock, 1)
    assert manager.envelope == 90.0
