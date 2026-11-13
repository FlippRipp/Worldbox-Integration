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
    assert saved_phase > 0.04

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


@pytest.mark.parametrize("pat", ["velvet", "swell", "heartbeat", "tease", "rhythm", "crest", "afterglow"])
def test_build_lovense_phrase_loops_seamlessly_across_wrap(pat):
    from toylink.patterns import build_lovense_phrase

    levels, interval_ms, is_loop = build_lovense_phrase(
        pat,
        intensity_norm=0.85,
        pace=0.55,
        contrast_mode="balanced",
        loop=True,
    )
    assert is_loop is True
    assert 48 <= len(levels) <= 50
    assert interval_ms >= 110
    # Wrap seam from last step -> step 0 must be just as smooth as interior steps!
    max_interior = max(abs(levels[i + 1] - levels[i]) for i in range(len(levels) - 1))
    seam_jump = abs(levels[0] - levels[-1])
    assert seam_jump <= max(0.12, max_interior + 0.03)


def test_build_lovense_phrase_one_shot_afterglow_ends_on_zeros():
    from toylink.patterns import build_lovense_phrase

    levels, interval_ms, is_loop = build_lovense_phrase(
        "afterglow",
        intensity_norm=0.75,
        loop=False,
    )
    assert is_loop is False
    assert interval_ms == 190
    assert len(levels) == 50
    assert levels[-4:] == [0.0, 0.0, 0.0, 0.0]


def test_rhythm_full_zero_to_hundred_contrast_distinct_from_crest():
    from toylink.lovense_client import _percent_to_lovense
    from toylink.patterns import build_lovense_phrase

    rhythm_lv, _, _ = build_lovense_phrase("rhythm", intensity_norm=1.0, pace=0.60)
    crest_lv, _, _ = build_lovense_phrase("crest", intensity_norm=1.0, pace=0.78)
    rhythm_steps = [_percent_to_lovense(x) for x in rhythm_lv]
    crest_steps = [_percent_to_lovense(x) for x in crest_lv]

    # Rhythm drives 0% <-> 100% every stroke (8 beats * 2 zero steps >= 16 zeros, peak == 20)
    assert min(rhythm_steps) == 0
    assert max(rhythm_steps) == 20
    assert rhythm_steps.count(0) >= 16

    # Crest Surge stays in High <-> Medium band (~45%-100%) without dropping to 0
    assert min(crest_steps) >= 8
    assert max(crest_steps) == 20


def test_heartbeat_pronounced_lub_dub_with_zero_brake_and_silence():
    from toylink.lovense_client import _percent_to_lovense
    from toylink.patterns import build_lovense_phrase

    hb_lv, interval_ms, _ = build_lovense_phrase("heartbeat", intensity_norm=1.0, pace=0.45)
    steps = [_percent_to_lovense(x) for x in hb_lv]
    assert len(steps) == 48
    assert 130 <= interval_ms <= 165

    # Inspect the first 12-step heartbeat pair: LUB (~70%) -> 0 brake -> DUB (100%) -> 0 silence
    first_pair = steps[:12]
    lub_peak = max(first_pair[:3])
    inter_beat_notch = first_pair[3:5]
    dub_peak = max(first_pair[5:8])
    diastole = first_pair[8:12]

    assert 12 <= lub_peak <= 16
    assert inter_beat_notch == [0, 0]
    assert dub_peak == 20
    assert diastole == [0, 0, 0, 0]


def test_ocean_swell_long_gradual_zero_to_full_and_down():
    from toylink.lovense_client import _percent_to_lovense
    from toylink.patterns import build_lovense_phrase

    swell_lv, interval_ms, _ = build_lovense_phrase("swell", intensity_norm=1.0, pace=0.40)
    steps = [_percent_to_lovense(x) for x in swell_lv]
    total_duration_s = (len(steps) * interval_ms) / 1000.0

    # Long ~13.6s wave starting at 0, building monotonically to 20, and gliding monotonically back to 0
    assert total_duration_s >= 12.0
    assert steps[0] == 0 and steps[-1] == 0
    assert max(steps) == 20
    peak_idx = steps.index(20)
    assert peak_idx >= 28  # ~60%+ of the wave spent gradually building up
    # Monotonic non-decreasing buildup and non-increasing glide down
    assert all(steps[i] <= steps[i + 1] for i in range(peak_idx))
    last_peak_idx = len(steps) - 1 - steps[::-1].index(20)
    assert all(steps[i] >= steps[i + 1] for i in range(last_peak_idx, len(steps) - 1))


def test_shaped_sentence_phrase_entry_ramp_and_arcs():
    from toylink.patterns import build_lovense_phrase

    # Entry ramp from 0.20 into 0.85 rising sentence cue starts gently near 0.20 and climbs
    rise_lv, _, _ = build_lovense_phrase(
        "velvet",
        intensity_norm=0.80,
        pace=0.5,
        entry_norm=0.20,
        arc="rise",
        duration_s=3.2,
    )
    ease_lv, _, _ = build_lovense_phrase(
        "velvet",
        intensity_norm=0.80,
        pace=0.5,
        entry_norm=0.80,
        arc="ease",
        duration_s=3.2,
    )
    assert rise_lv[0] < 0.35
    assert max(rise_lv[20:]) > 0.75
    assert sum(ease_lv[:8]) / 8 > sum(ease_lv[25:35]) / 10


def test_custom_block_pattern_wave_x2_then_saw_x2_harmonic_and_gapless():
    from toylink.lovense_client import _percent_to_lovense
    from toylink.patterns import build_lovense_phrase, eval_block_pattern, sanitize_block_pattern

    spec = sanitize_block_pattern({
        "id": "wave_saw_combo",
        "label": "Wave x2 + Saw x2",
        "loop_seconds": 6.0,
        "lift_high_floor": False,
        "vibe_tags": ["rhythmic", "building"],
        "blocks": [
            {"type": "wave", "width": 1.0, "repeat": 2, "peak": 1.0, "floor": 0.0, "skew": 0.5, "bend": 0.0},
            {"type": "saw_up", "width": 1.0, "repeat": 2, "peak": 1.0, "floor": 0.0, "skew": 0.5, "bend": 0.0},
        ],
    })

    # Continuous curve connects seamlessly at 0.0, 0.25, 0.50, 0.75
    assert eval_block_pattern(spec, 0.0) == 0.0
    assert abs(eval_block_pattern(spec, 0.125) - 1.0) < 1e-4
    assert abs(eval_block_pattern(spec, 0.25) - 0.0) < 1e-4
    assert abs(eval_block_pattern(spec, 0.375) - 1.0) < 1e-4
    assert abs(eval_block_pattern(spec, 0.50) - 0.0) < 1e-4

    levels, interval_ms, is_loop = build_lovense_phrase(
        "wave_saw_combo",
        intensity_norm=1.0,
        pattern_def=spec,
    )
    steps = [_percent_to_lovense(x) for x in levels]

    # 4 sub-cycles -> 48 harmonic steps (exact 12 steps per beat), 125 ms -> 6.00 s loop
    assert is_loop is True
    assert len(steps) == 48
    assert interval_ms == 125

    wave1, wave2, saw1, saw2 = steps[0:12], steps[12:24], steps[24:36], steps[36:48]
    # Both waves match identically and peak at 20 with single-step 0 valley
    assert wave1 == wave2
    assert wave1[0] == 0 and max(wave1) == 20
    # Both sawtooth climbs match identically, climb monotonically 0 -> 20, and drop crisply to 0
    assert saw1 == saw2
    assert saw1[0] == 0 and saw1[-1] == 20
    assert all(saw1[i] <= saw1[i + 1] for i in range(len(saw1) - 1))


def test_stitched_line_blocks_connect_edge_to_edge_around_loop():
    from toylink.patterns import eval_block_pattern, sanitize_block_pattern

    # 3 connected lines: 0.2 -> 0.6 -> 1.0 -> 0.2 (wraps last peak 0.2 into block 0's start!)
    spec = sanitize_block_pattern({
        "id": "triangle_climb",
        "label": "Connected Climb",
        "blocks": [
            {"type": "line", "width": 1.0, "repeat": 1, "peak": 0.6, "bend": 0.0},
            {"type": "line", "width": 1.0, "repeat": 1, "peak": 1.0, "bend": 0.0},
            {"type": "line", "width": 1.0, "repeat": 1, "peak": 0.2, "bend": 0.0},
        ],
    })
    assert abs(eval_block_pattern(spec, 0.0) - 0.2) < 1e-4
    assert abs(eval_block_pattern(spec, 1 / 3 - 1e-5) - 0.6) < 1e-3
    assert abs(eval_block_pattern(spec, 1 / 3 + 1e-5) - 0.6) < 1e-3
    assert abs(eval_block_pattern(spec, 2 / 3 - 1e-5) - 1.0) < 1e-3
    assert abs(eval_block_pattern(spec, 2 / 3 + 1e-5) - 1.0) < 1e-3
    assert abs(eval_block_pattern(spec, 0.9999) - 0.2) < 1e-3


def test_active_story_transitions_align_phase_without_zero_seam_gap():
    from toylink.lovense_client import _percent_to_lovense
    from toylink.patterns import build_lovense_phrase, sanitize_block_pattern

    # 1. Switching from active 62% vibration into Ocean Swell, Heartbeat, Rhythm, or Custom Wave+Saw
    # must NEVER drop to a 0/20 motor-brake gap at the start of the phrase!
    custom_spec = sanitize_block_pattern({
        "id": "wave_saw",
        "label": "Wave + Saw",
        "loop_seconds": 6.0,
        "blocks": [
            {"type": "wave", "repeat": 2, "peak": 1.0, "floor": 0.0},
            {"type": "saw_up", "repeat": 2, "peak": 1.0, "floor": 0.0},
        ],
    })
    for pat_id, pdef in [
        ("swell", None),
        ("heartbeat", None),
        ("rhythm", None),
        ("wave_saw", custom_spec),
    ]:
        lv, _, _ = build_lovense_phrase(
            pat_id,
            intensity_norm=0.85,
            entry_norm=0.62,
            arc="steady",
            pattern_def=pdef,
        )
        steps = [_percent_to_lovense(x) for x in lv]
        # Entry step matches live motor speed (~12/20) instead of slamming to 0/20!
        assert steps[0] >= 10, f"{pat_id} dropped to {steps[0]}/20 on active handoff"
        assert max(steps) >= 16

    # 2. Updating Ocean Swell mid-climb preserves live phase instead of resetting to 0%
    mid_swell, _, _ = build_lovense_phrase(
        "swell",
        intensity_norm=0.90,
        entry_norm=0.55,
        live_phase=0.45,
        same_pattern=True,
    )
    assert mid_swell[0] >= 0.50
    assert max(mid_swell[:15]) >= 0.82





