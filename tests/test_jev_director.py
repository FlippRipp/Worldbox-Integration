import asyncio
from types import SimpleNamespace

import pytest

from toylink.jev_director import JevDirector, build_questions, interpret_answers
from toylink.patterns import PATTERNS


def make_answers(
    engaged=0.95,
    wind_down=0.02,
    int_probs=None,
    pace_probs=None,
    pattern="swell",
    pat_conf=0.92,
):
    int_probs = int_probs or {
        "none": 0.0,
        "subtle": 0.0,
        "sensual": 0.5,
        "aroused": 0.5,
        "intense": 0.0,
        "climax": 0.0,
    }
    pace_probs = pace_probs or {"slow": 0.0, "measured": 0.5, "fast": 0.5, "frantic": 0.0}
    top_int = max(int_probs, key=int_probs.get)
    top_pace = max(pace_probs, key=pace_probs.get)
    pat_probs = {p: (1.0 if p == pattern else 0.0) for p in PATTERNS}
    return {
        "engaged": {"type": "noul", "noul": engaged},
        "wind_down": {"type": "noul", "noul": wind_down},
        "intensity": {
            "type": "choice",
            "choice": top_int,
            "confidence": max(int_probs.values()),
            "probabilities": int_probs,
        },
        "pace": {
            "type": "choice",
            "choice": top_pace,
            "confidence": max(pace_probs.values()),
            "probabilities": pace_probs,
        },
        "pattern": {
            "type": "choice",
            "choice": pattern,
            "confidence": pat_conf,
            "probabilities": pat_probs,
        },
    }


def test_probability_weighted_analog_intensity_and_pace():
    # 50% sensual (42) + 50% aroused (64) -> smooth analog 53.0% instead of coarse step jump!
    verdict = interpret_answers(make_answers(), enabled_patterns=list(PATTERNS))
    assert verdict["action"] == "apply"
    assert verdict["strength"] == pytest.approx(53.0)
    assert verdict["pace"] == pytest.approx(0.60)
    assert verdict["pattern"] == "swell"


def test_negation_or_non_physical_scene_ramps_to_zero():
    verdict = interpret_answers(
        make_answers(engaged=0.04, wind_down=0.05),
        enabled_patterns=list(PATTERNS),
        current_strength=60.0,
    )
    assert verdict["action"] == "apply"
    assert verdict["strength"] == 0.0


def test_wind_down_triggers_afterglow_when_previously_active():
    verdict = interpret_answers(
        make_answers(engaged=0.08, wind_down=0.82),
        enabled_patterns=list(PATTERNS),
        current_strength=80.0,
    )
    assert verdict["action"] == "apply"
    assert verdict["pattern"] == "afterglow"
    assert 20.0 <= verdict["strength"] <= 50.0


def test_incoherent_bimodal_intensity_holds_instead_of_guessing():
    bimodal = {
        "none": 0.48,
        "subtle": 0.0,
        "sensual": 0.0,
        "aroused": 0.0,
        "intense": 0.0,
        "climax": 0.52,
    }
    verdict = interpret_answers(
        make_answers(int_probs=bimodal),
        enabled_patterns=list(PATTERNS),
    )
    assert verdict["action"] == "hold"


def test_single_enabled_pattern_skips_choice_question_with_one_option():
    # JEV requires >=2 options for `choice`; narrowing to 1 pattern must omit `pattern` question
    q = build_questions(["crest"])
    assert "pattern" not in q
    verdict = interpret_answers(make_answers(), enabled_patterns=["crest"])
    assert verdict["pattern"] == "crest"


async def test_director_coalesces_tokens_and_invalidates_stale_calls():
    applied = []
    gate = asyncio.Event()

    class StubDecision:
        def __init__(self, answers):
            self.answers = answers
            self.reason = ""
            self.finished = False
            self.outcome = ""

        async def finish(self, outcome, reason=""):
            self.finished = True
            self.outcome = outcome

    async def slow_decide(_state, _questions, _step):
        await gate.wait()
        return StubDecision(make_answers(pattern="crest"))

    director = JevDirector(
        slow_decide,
        lambda: {"enabled_patterns": list(PATTERNS), "jev": {"min_interval_s": 0.5, "sensitivity": 1.0}},
        applied.append,
        lambda: 0.0,
    )
    director.start_turn({"history": ["Prior turn."], "input_text": "I kiss her."})
    director.feed_token("She pulls him close. ")
    await asyncio.sleep(0)

    # User hits manual STOP while JEV is still in-flight -> invalidate must block stale apply
    director.invalidate()
    gate.set()
    await asyncio.sleep(0.02)
    assert applied == []


def test_split_stage_sentences_matches_frontend_rules():
    from toylink.jev_director import split_stage_sentences

    text = (
        'Dr. Vance stepped into the dim room. "Oh!" She caught her breath as the door clicked shut. '
        "Outside, rain drummed against the glass."
    )
    segs = split_stage_sentences(text, is_complete=True)
    assert [s["text"] for s in segs] == [
        "Dr. Vance stepped into the dim room.",
        '"Oh!" She caught her breath as the door clicked shut.',
        "Outside, rain drummed against the glass.",
    ]

    # Multi-sentence quote splits into individual sentence beats matching stageSegments.js
    q_segs = split_stage_sentences('<pink>"Stay close to me. Don\'t look away."</pink> Warm breath met his skin.', is_complete=True)
    assert len(q_segs) == 3
    assert q_segs[2]["text"] == "Warm breath met his skin."


async def test_reader_synced_mode_prescores_ahead_and_applies_on_sentence_reveal():
    applied = []
    seen_states = []

    class StubDecision:
        def __init__(self, answers):
            self.answers = answers
            self.reason = ""
            self.finished = False

        async def finish(self, outcome, reason=""):
            self.finished = True

    async def per_sentence_decide(state, _questions, _step):
        seen_states.append(state)
        sent = state.get("current_sentence", "")
        if "kissed" in sent:
            ans = make_answers(
                pattern="velvet",
                int_probs={"none": 0, "subtle": 0, "sensual": 1.0, "aroused": 0, "intense": 0, "climax": 0},
            )
            ans["arc"] = {"type": "choice", "choice": "rise", "confidence": 0.9, "probabilities": {"steady": 0.1, "rise": 0.9, "surge": 0.0, "ease": 0.0}}
            return StubDecision(ans)
        if "whispered" in sent:
            # Mid-scene dialogue lacking explicit touch words (low engaged, low wind_down):
            # continuity inertia should keep vibration warmly alive instead of cutting to 0!
            return StubDecision(make_answers(engaged=0.10, wind_down=0.04))
        if "knocked" in sent:
            # Explicit pause/interruption (low engaged + clear stop/pause signal) -> drops to 0
            return StubDecision(make_answers(engaged=0.02, wind_down=0.35))
        ans = make_answers(
            pattern="rhythm",
            int_probs={"none": 0, "subtle": 0, "sensual": 0, "aroused": 0, "intense": 1.0, "climax": 0},
        )
        ans["arc"] = {"type": "choice", "choice": "surge", "confidence": 0.9, "probabilities": {"steady": 0.0, "rise": 0.1, "surge": 0.9, "ease": 0.0}}
        return StubDecision(ans)

    director = JevDirector(
        per_sentence_decide,
        lambda: {"enabled_patterns": list(PATTERNS), "sync_stage_reader": True, "jev": {"min_interval_s": 0.5, "sensitivity": 1.0}},
        applied.append,
        lambda: applied[-1]["strength"] if applied else 0.0,
    )
    director.set_reader_mode(True)
    director.start_turn({"history": [], "input_text": ""})

    # Stream all 4 sentences quickly at LLM speed: nothing should touch the motor yet!
    s0 = "She leaned in and kissed his lips softly."
    s1 = '"Don\'t say a word," she whispered against his cheek.'
    s2 = "Suddenly someone knocked loudly down the hallway and they froze."
    s3 = "Once silence returned, she pulled him into a driving, breathless embrace."
    full = f"{s0} {s1} {s2} {s3}"
    for tok in full.split(" "):
        director.feed_token(tok + " ")
    director.finish_turn()
    await asyncio.sleep(0.05)

    assert applied == []
    assert director.status()["pre_scored_sentences"] == 4
    calls_after_prescore = director.calls
    assert calls_after_prescore == 4

    # Verify JEV received structured active_haptic_state & recent_sentence_history on Sentence 1+
    assert seen_states[1]["active_haptic_state"]["active"] is True
    assert seen_states[1]["active_haptic_state"]["pattern"] == "velvet"
    assert len(seen_states[1]["recent_sentence_history"]) == 1

    # Now StageReader displays Sentences 0..3 at human reading pace:
    await director.on_reader_sentence(sentence_index=0, char_start=0, char_end=len(s0), text=s0, duration_ms=2800)
    assert len(applied) == 1
    assert applied[-1]["pattern"] == "velvet"
    assert applied[-1]["strength"] == pytest.approx(42.0)
    assert applied[-1]["params"]["arc"] == "rise"
    assert applied[-1]["params"]["duration_s"] == pytest.approx(2.8)

    # Sentence 1 (dialogue mid-embrace): continuity inertia keeps velvet alive (~36.1%) with ease arc!
    c1_start = len(s0) + 1
    await director.on_reader_sentence(sentence_index=1, char_start=c1_start, char_end=c1_start + len(s1), text=s1, duration_ms=2400)
    assert len(applied) == 2
    assert applied[-1]["continuity_hold"] is True
    assert applied[-1]["pattern"] == "velvet"
    assert applied[-1]["strength"] == pytest.approx(36.1)
    assert applied[-1]["params"]["arc"] == "ease"

    # Sentence 2 (explicit interruption): cleanly steps down to 0%
    c2_start = c1_start + len(s1) + 1
    await director.on_reader_sentence(sentence_index=2, char_start=c2_start, char_end=c2_start + len(s2), text=s2, duration_ms=3000)
    assert len(applied) == 3
    assert applied[-1]["strength"] == 0.0

    # Sentence 3 (resumed embrace): surges to 84% rhythm
    c3_start = c2_start + len(s2) + 1
    await director.on_reader_sentence(sentence_index=3, char_start=c3_start, char_end=len(full), text=s3, duration_ms=3200)
    assert len(applied) == 4
    assert applied[-1]["pattern"] == "rhythm"
    assert applied[-1]["strength"] == pytest.approx(84.0)
    assert applied[-1]["params"]["arc"] == "surge"
    # Zero extra JEV calls needed at reveal time because all 4 were already pre-scored!
    assert director.calls == calls_after_prescore


def test_pattern_continuity_hysteresis_prevents_marginal_flip_flopping():
    # Marginal split (swell 0.36 vs current velvet 0.30) keeps active 'velvet' smoothly
    marginal = make_answers(pattern="swell", pat_conf=0.36)
    marginal["pattern"]["probabilities"] = {
        p: (0.36 if p == "swell" else (0.30 if p == "velvet" else 0.068))
        for p in PATTERNS
    }
    v_keep = interpret_answers(
        marginal,
        enabled_patterns=list(PATTERNS),
        current_strength=50.0,
        current_pattern="velvet",
        sentence_mode=True,
    )
    assert v_keep["pattern"] == "velvet"

    # Clear action shift (rhythm 0.62 vs current velvet 0.12) switches immediately
    decisive = make_answers(pattern="rhythm", pat_conf=0.62)
    decisive["pattern"]["probabilities"] = {
        p: (0.62 if p == "rhythm" else (0.12 if p == "velvet" else 0.052))
        for p in PATTERNS
    }
    v_switch = interpret_answers(
        decisive,
        enabled_patterns=list(PATTERNS),
        current_strength=50.0,
        current_pattern="velvet",
        sentence_mode=True,
    )
    assert v_switch["pattern"] == "rhythm"


