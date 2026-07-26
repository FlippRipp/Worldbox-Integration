from toylink.patterns import DEFAULT_PARAMS, VibeState, pattern_multiplier


def test_constant_is_flat():
    assert pattern_multiplier("constant", 0.0) == 1.0
    assert pattern_multiplier("constant", 123.4) == 1.0


def test_unknown_pattern_behaves_as_constant():
    assert pattern_multiplier("wobble", 1.0) == 1.0


def test_pulse_on_off_timing():
    params = {"pulse_on_ms": 400, "pulse_off_ms": 250}
    assert pattern_multiplier("pulse", 0.0, params) == 1.0
    assert pattern_multiplier("pulse", 0.399, params) == 1.0
    assert pattern_multiplier("pulse", 0.401, params) == 0.0
    assert pattern_multiplier("pulse", 0.649, params) == 0.0
    # next cycle
    assert pattern_multiplier("pulse", 0.651, params) == 1.0


def test_wave_starts_low_and_stays_in_range():
    params = {"wave_low_pct": 30, "wave_period_ms": 2000}
    low = pattern_multiplier("wave", 0.0, params)
    assert abs(low - 0.30) < 1e-9
    peak = pattern_multiplier("wave", 1.0, params)  # half period = peak
    assert abs(peak - 1.0) < 1e-9
    for t in (0.1, 0.5, 0.9, 1.3, 1.7):
        v = pattern_multiplier("wave", t, params)
        assert 0.30 - 1e-9 <= v <= 1.0 + 1e-9


def test_param_defaults_apply_when_missing():
    # No params at all: pulse uses the documented defaults.
    on = DEFAULT_PARAMS["pulse_on_ms"] / 1000.0
    assert pattern_multiplier("pulse", on - 0.001) == 1.0
    assert pattern_multiplier("pulse", on + 0.001) == 0.0
    # None values inherit the default rather than crashing.
    assert pattern_multiplier("pulse", 0.0, {"pulse_on_ms": None}) == 1.0


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_vibestate_latch_and_clamp():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(150, "pulse", {"pulse_on_ms": 100, "pulse_off_ms": 100}, source="test")
    assert vs.strength == 100.0
    vs.set_target(-5)
    assert vs.strength == 0.0
    vs.set_target(85, "nonsense")
    assert vs.pattern == "constant"


def test_vibestate_target_multiplier_tracks_pattern_phase():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(80, "pulse", {"pulse_on_ms": 400, "pulse_off_ms": 250})
    assert vs.target_multiplier() == 80.0     # phase 0 = on
    clock.t += 0.5                            # into the off part
    assert vs.target_multiplier() == 0.0
    clock.t += 0.2                            # next cycle, on again
    assert vs.target_multiplier() == 80.0


def test_vibestate_phase_resets_on_new_target():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(80, "pulse", {"pulse_on_ms": 400, "pulse_off_ms": 250})
    clock.t += 0.5
    assert vs.target_multiplier() == 0.0
    vs.set_target(60, "pulse", {"pulse_on_ms": 400, "pulse_off_ms": 250})
    assert vs.target_multiplier() == 60.0     # fresh latch starts "on"


def test_vibestate_apply_effect_and_clear():
    vs = VibeState(clock=FakeClock())
    vs.apply_effect({"rule_id": "r_kiss", "category": "gentle", "strength": 30,
                     "pattern": "constant", "params": {}}, source_prefix="keyword:")
    assert vs.strength == 30.0
    assert vs.source == "keyword:r_kiss"
    v = vs.version
    vs.clear()
    assert vs.strength == 0.0 and vs.version > v


def test_vibestate_gate_changes_version_only_on_flip():
    vs = VibeState(clock=FakeClock())
    v = vs.version
    vs.set_gate(True)          # already on: no-op
    assert vs.version == v
    vs.set_gate(False)
    assert vs.vibe_on is False and vs.version == v + 1
