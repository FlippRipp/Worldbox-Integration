"""Semantic trigger engine: a small LLM classifies the streaming prose.

Fixes what keywords can't: negation ("he didn't touch her"), memories,
paraphrase. On each completed sentence it classifies a rolling window of
recent prose against the user's own categories (shared vocabulary with the
keyword engine) and applies the verdict to the latch — in hybrid mode that
verdict overrides whatever a keyword guessed a second earlier.

At most one call is in flight; if sentences complete while busy, one follow-up
classifies the *latest* window afterwards (natural rate limit, no queue).
Everything fails quiet: errors, refusals, and malformed output keep the
current state, land in ``last_error``, and back off after 3 straight failures.
The actual LLM call is injected (``classify_call``) — backend.py wires it to
the app's sdk.llm "fastest" slot or to engine.llm with the user's per-module
model override.
"""
import asyncio
import json
import re
import time

BACKOFF_S = 30.0
FAILURES_BEFORE_BACKOFF = 3
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*(\s|$)")

PROMPT_TEMPLATE = """You classify a snippet of roleplay prose for a haptics (vibration) controller.
Judge the CURRENT moment of physical/erotic intensity at the END of the snippet — what is happening now, not what is remembered, negated, or merely discussed.

Answer IMMEDIATELY with your first impression. Do not reason step by step, do not deliberate, do not weigh alternatives — this is a low-stakes, latency-critical call made every few seconds, and a fast approximate answer is worth more than a slow careful one. If unsure, answer no_change.

Categories (pick exactly one):
{categories}

Respond with ONLY one line of valid JSON, nothing else:
  {{"category": "<name>", "strength": <0-100 integer>}}
or, if the current intensity has not changed since the last sentence or the snippet is not physical at all:
  {{"no_change": true}}

Use the category's listed strength unless the prose clearly calls for more or less.

Prose snippet:
---
{window}
---"""


class SemanticEngine:
    def __init__(self, classify_call, get_ruleset, get_config, apply_effect,
                 clock=time.monotonic):
        self._classify_call = classify_call   # async (prompt) -> raw text
        self._get_ruleset = get_ruleset
        self._get_config = get_config         # -> full module config dict
        self._apply_effect = apply_effect     # (effect dict) -> None
        self._clock = clock
        self._buf = ""
        self._inflight: asyncio.Task | None = None
        self._reclassify_wanted = False
        self.consecutive_failures = 0
        self.backoff_until = 0.0
        self.last_verdict: dict | None = None
        self.last_error = ""
        self.calls = 0

    # ------------------------------------------------------------------ state

    def _cfg(self) -> dict:
        return (self._get_config() or {}).get("semantic", {})

    @property
    def active(self) -> bool:
        mode = (self._get_config() or {}).get("trigger_mode", "hybrid")
        return mode in ("hybrid", "semantic")

    def reset(self) -> None:
        """Full reset (story switch). The buffer normally persists across
        turns — the scene doesn't end because the player is typing."""
        self._buf = ""
        self._reclassify_wanted = False

    # ------------------------------------------------------------------- feed

    def feed(self, token: str) -> None:
        """Sync and cheap — called from the token hook."""
        if not self.active:
            return
        self._buf += token
        window_chars = int(self._cfg().get("window_chars", 600) or 600)
        if len(self._buf) > window_chars * 4:
            self._buf = self._buf[-window_chars * 2:]
        if _SENTENCE_END.search(token):
            self._schedule()

    def end_of_turn(self) -> None:
        """Classify the trailing partial sentence so the state we hold while
        the player reads reflects the turn's closing mood."""
        if self.active and self._buf.strip():
            self._schedule()

    def _schedule(self) -> None:
        if self._clock() < self.backoff_until:
            return
        if self._inflight is not None and not self._inflight.done():
            self._reclassify_wanted = True
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._inflight = loop.create_task(self._classify())

    # --------------------------------------------------------------- classify

    def _build_prompt(self) -> str:
        ruleset = self._get_ruleset()
        lines = []
        for name, cat in ruleset.categories.items():
            strength = cat.get("strength", 0)
            hint = " (this means: stop / wind down)" if not strength else ""
            lines.append(f'- "{name}": strength {strength}{hint}')
        window_chars = int(self._cfg().get("window_chars", 600) or 600)
        return PROMPT_TEMPLATE.format(
            categories="\n".join(lines), window=self._buf[-window_chars:])

    async def _classify(self) -> None:
        timeout = float(self._cfg().get("timeout_s", 4) or 4)
        try:
            raw = await asyncio.wait_for(self._classify_call(self._build_prompt()),
                                         timeout)
            self.calls += 1
            verdict = self._parse(raw)
            self.consecutive_failures = 0
            self.last_error = ""
            self.last_verdict = verdict
            if verdict is not None and not verdict.get("no_change"):
                effect = self._verdict_to_effect(verdict)
                if effect is not None:
                    self._apply_effect(effect)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.consecutive_failures += 1
            self.last_error = f"{type(e).__name__}: {e}"
            if self.consecutive_failures >= FAILURES_BEFORE_BACKOFF:
                self.backoff_until = self._clock() + BACKOFF_S
        finally:
            self._inflight = None
            if self._reclassify_wanted:
                self._reclassify_wanted = False
                self._schedule()

    @staticmethod
    def _parse(raw: str) -> dict:
        """Pull the first JSON object out of the response; refusals and prose
        around it are tolerated, no JSON at all is a failure."""
        if not raw:
            raise ValueError("empty response")
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"no JSON in response: {raw[:120]!r}")
        return json.loads(raw[start:end + 1])

    def _verdict_to_effect(self, verdict: dict) -> dict | None:
        ruleset = self._get_ruleset()
        category = str(verdict.get("category", ""))
        cat = ruleset.categories.get(category)
        strength = verdict.get("strength")
        if cat is None and strength is None:
            return None                      # nothing usable in the verdict
        if strength is None:
            strength = cat.get("strength", 0)
        try:
            strength = min(max(float(strength), 0.0), 100.0)
        except (TypeError, ValueError):
            return None
        pattern = (cat or {}).get("pattern", "constant")
        params = {k: v for k, v in (cat or {}).items()
                  if k.startswith(("pulse_", "wave_")) and v is not None}
        return {"rule_id": "semantic", "category": category,
                "strength": strength, "pattern": pattern, "params": params}

    def status(self) -> dict:
        return {
            "active": self.active,
            "calls": self.calls,
            "last_verdict": self.last_verdict,
            "last_error": self.last_error,
            "in_backoff": self._clock() < self.backoff_until,
        }
