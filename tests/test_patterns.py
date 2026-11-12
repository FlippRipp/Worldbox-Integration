import pytest

from toylink.patterns import (
    PATTERNS,
    VibeState,
    normalize_pattern,
    pattern_multiplier,
    sample_pattern_curve,
)


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize("pat", PATTERNS)
def test_all_patterns_bounded_and_smooth(pat):
    samples = sample_pattern_curve(pat, duration_s=8.0, steps=160, intensity=85.0)
    assert len(samples) == 160
    assert all(0.0 <= s <= 1.0 for s in samples)
    # Smoothness: no instant 0->1 square-cliff spikes within a single 50 ms step
    diffs = [abs(samples[i + 1] - samples[i]) for i in range(len(samples) - 1)]
    assert max(diffs) < 0.45


def test_legacy_aliases_and_unknown_normalize_cleanly():
    assert normalize_pattern("constant") == "velvet"
    assert normalize_pattern("wave") == "swell"
    assert normalize_pattern("pulse") == "rhythm"
    assert normalize_pattern("nonexistent") == "velvet"
    assert normalize_pattern(None) == "velvet"


def test_high_intensity_applies_high_medium_contrast_without_zero_drops():
    """At high intensity, patterns should cycle rapidly between High and Medium
    to prevent numbing — neither clamping flat nor dropping all the way to 0."""
    crest_hi = sample_pattern_curve("crest", duration_s=4.0, steps=80, intensity=100.0, contrast_mode="high")
    assert max(crest_hi) >= 0.88
    # Troughs stay in the Medium band (~0.35-0.62), never dropping near 0
    assert 0.34 <= min(crest_hi) <= 0.65
    # Verify rapid High↔Medium crossings (several cycles across 4 seconds)
    mid = 0.70
    crossings = sum(
        1 for i in range(len(crest_hi) - 1) if (crest_hi[i] < mid) != (crest_hi[i + 1] < mid)
    )
    assert crossings >= 10

    # Even Velvet (gentle caress at low intensity) engages contrast shimmer at 95%
    velvet_mod = sample_pattern_curve("velvet", duration_s=3.0, steps=60, intensity=45.0)
    velvet_hi = sample_pattern_curve("velvet", duration_s=3.0, steps=60, intensity=95.0, contrast_mode="high")
    mod_rel_span = (max(velvet_mod) - min(velvet_mod)) / 0.45
    hi_rel_span = (max(velvet_hi) - min(velvet_hi)) / 0.95
    assert hi_rel_span > mod_rel_span * 1.4


def test_phrase_variation_evolves_across_windows():
    """Adjacent multi-second windows should vary organically rather than
    repeating an identical static loop."""
    s = sample_pattern_curve("swell", duration_s=14.0, steps=280, intensity=75.0)
    first_window = s[:100]
    second_window = s[100:200]
    assert first_window != second_window


def test_phase_continuity_when_strength_or_pace_updates():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(60, "swell", pace=0.4)
    for _ in range(15):
        vs.advance_and_sample(0.05, 0.60)
    saved_phase = vs.phase
    assert saved_phase > 0.1

    # Updating strength and pace mid-stream MUST NOT reset oscillator phase to 0
    vs.set_target(85, "swell", pace=0.75)
    assert vs.phase == pytest.approx(saved_phase)


def test_pattern_switch_crossfades_smoothly():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(80, "velvet")
    for _ in range(10):
        vs.advance_and_sample(0.05, 0.80)
    before = vs.advance_and_sample(0.0, 0.80)

    vs.set_target(80, "tease")
    assert vs._prev_pattern == "velvet"
    assert vs._xfade == 0.0
    # At t=0 of switch, output starts right at the previous pattern's value (no click!)
    at_switch = vs.advance_and_sample(0.0, 0.80)
    assert at_switch == pytest.approx(before, abs=1e-6)

    # After >0.35s, crossfade finishes cleanly
    for _ in range(10):
        vs.advance_and_sample(0.05, 0.80)
    assert vs._prev_pattern is None
    assert vs._xfade == 1.0


def test_afterglow_decays_to_silence():
    clock = FakeClock()
    vs = VibeState(clock=clock)
    vs.set_target(45, "afterglow")
    for _ in range(200):  # 10 seconds at 20 Hz
        vs.advance_and_sample(0.05, vs.strength / 100.0)
    assert vs.strength == 0.0
    assert vs.source == "afterglow:complete"


def test_stateless_pattern_multiplier_helper():
    v = pattern_multiplier("heartbeat", 0.5, {"pace": 0.5})
    assert 0.0 <= v <= 1.0
