import asyncio
import json

import pytest

from toylink.keyword_engine import RuleSet
from toylink.semantic_engine import SemanticEngine

RULES = {
    "categories": {
        "gentle": {"strength": 30, "pattern": "constant"},
        "intense": {"strength": 85, "pattern": "pulse",
                    "pulse_on_ms": 400, "pulse_off_ms": 250},
        "calm": {"strength": 0, "pattern": "constant"},
    },
    "rules": [],
}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Harness:
    def __init__(self, responses=None, mode="hybrid"):
        self.ruleset = RuleSet.from_config(RULES)
        self.config = {"trigger_mode": mode,
                       "semantic": {"model_override": "", "window_chars": 600,
                                    "timeout_s": 4}}
        self.applied = []
        self.prompts = []
        self.responses = list(responses or [])
        self.release = asyncio.Event()
        self.release.set()
        self.clock = FakeClock()
        self.engine = SemanticEngine(
            self.call, lambda: self.ruleset, lambda: self.config,
            self.applied.append, clock=self.clock)

    async def call(self, prompt):
        self.prompts.append(prompt)
        await self.release.wait()
        if not self.responses:
            return json.dumps({"no_change": True})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def drain(self):
        for _ in range(20):
            task = self.engine._inflight
            if task is None:
                break
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)


async def test_schedules_only_on_sentence_end():
    h = Harness(responses=[json.dumps({"category": "gentle", "strength": 30})])
    h.engine.feed("her hand brushed his and ")
    await h.drain()
    assert h.prompts == []
    h.engine.feed("lingered there. ")
    await h.drain()
    assert len(h.prompts) == 1
    assert h.applied[0]["category"] == "gentle"


async def test_verdict_resolves_category_defaults():
    h = Harness(responses=[json.dumps({"category": "intense"})])
    h.engine.feed("It built and built. ")
    await h.drain()
    eff = h.applied[0]
    assert eff["strength"] == 85 and eff["pattern"] == "pulse"
    assert eff["params"]["pulse_on_ms"] == 400
    assert eff["rule_id"] == "semantic"


async def test_verdict_strength_overrides_and_clamps():
    h = Harness(responses=[json.dumps({"category": "gentle", "strength": 400})])
    h.engine.feed("Softly. ")
    await h.drain()
    assert h.applied[0]["strength"] == 100.0


async def test_no_change_applies_nothing():
    h = Harness(responses=[json.dumps({"no_change": True})])
    h.engine.feed("They talked about the weather. ")
    await h.drain()
    assert h.applied == []
    assert h.engine.last_verdict == {"no_change": True}


async def test_single_inflight_then_latest_window_followup():
    h = Harness(responses=[json.dumps({"no_change": True}),
                           json.dumps({"category": "gentle"})])
    h.release.clear()                       # first call blocks
    h.engine.feed("First sentence. ")
    for _ in range(5):                      # let the nested wait_for task start
        await asyncio.sleep(0)
    h.engine.feed("Second sentence. ")
    h.engine.feed("Third sentence. ")
    assert len(h.prompts) == 1              # still just the blocked call
    h.release.set()
    await h.drain()
    assert len(h.prompts) == 2              # one follow-up, not one per sentence
    assert "Third sentence." in h.prompts[1]   # classified the latest window


async def test_json_extracted_from_chatter_and_fences():
    raw = "Sure! Here you go:\n```json\n{\"category\": \"calm\"}\n```"
    h = Harness(responses=[raw])
    h.engine.feed("They finished and lay still. ")
    await h.drain()
    assert h.applied[0]["strength"] == 0


async def test_failures_fail_quiet_then_back_off():
    h = Harness(responses=[RuntimeError("boom"), "no json here at all",
                           RuntimeError("boom"),
                           json.dumps({"category": "gentle"})])
    for i in range(3):
        h.engine.feed(f"Sentence number {i}. ")
        await h.drain()
    assert h.applied == []
    assert h.engine.consecutive_failures == 3
    assert "boom" in h.engine.last_error or "no JSON" in h.engine.last_error
    # In backoff: nothing schedules.
    h.engine.feed("Another sentence. ")
    await h.drain()
    assert len(h.prompts) == 3
    # Backoff expires with time.
    h.clock.t += 31
    h.engine.feed("Later, a kiss. ")
    await h.drain()
    assert len(h.prompts) == 4
    assert h.applied[-1]["category"] == "gentle"


async def test_end_of_turn_flushes_trailing_text():
    h = Harness(responses=[json.dumps({"category": "gentle"})])
    h.engine.feed("a trailing fragment with no period")
    await h.drain()
    assert h.prompts == []
    h.engine.end_of_turn()
    await h.drain()
    assert len(h.prompts) == 1


async def test_keywords_mode_disables_engine():
    h = Harness(mode="keywords")
    h.engine.feed("A whole sentence. ")
    await h.drain()
    assert h.prompts == []
    assert h.engine.active is False


async def test_prompt_lists_user_categories_and_window():
    h = Harness(responses=[json.dumps({"no_change": True})])
    h.engine.feed("Some prose here. ")
    await h.drain()
    prompt = h.prompts[0]
    assert '"gentle": strength 30' in prompt
    assert '"calm": strength 0 (this means: stop / wind down)' in prompt
    assert "Some prose here." in prompt


async def test_unknown_category_without_strength_ignored():
    h = Harness(responses=[json.dumps({"category": "nonsense"})])
    h.engine.feed("Hm. ")
    await h.drain()
    assert h.applied == []
