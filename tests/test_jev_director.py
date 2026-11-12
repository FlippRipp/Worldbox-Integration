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
