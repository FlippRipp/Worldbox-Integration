"""Keyword trigger engine: rules → one compiled regex → streaming scanner.

The scanner sees the storyteller's prose one token at a time. Tokens split
words arbitrarily ("thr" + "obbing"), so matching runs against a rolling
buffer; the trailing partial word is held back until the next token (or end of
turn) shows where the word actually ends. Multi-word keywords are supported —
already-scanned text is retained in the buffer so a phrase completing across
scans still matches, and ``m.end() > scan_pos`` keeps matches from being
emitted twice.
"""
import re

# Category fields that flow into pattern params.
PARAM_FIELDS = ("pulse_on_ms", "pulse_off_ms", "wave_low_pct", "wave_period_ms")

_TRAILING_WORD = re.compile(r"[a-z0-9]+$")
_MAX_BUF = 4096
_TRIM_TO = 1024


class RuleSet:
    """Rules resolved against category defaults, compiled into a single
    longest-first word-boundary alternation. Instances are immutable; editing
    rules builds a new RuleSet with a bumped version (hot reload)."""

    def __init__(self, categories: dict, rules: list, version: int = 0):
        self.categories = categories
        self.rules = rules
        self.version = version
        self._by_keyword: dict[str, dict] = {}
        parts = []
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            for kw in rule.get("keywords", []):
                kw = str(kw).strip().lower()
                if not kw or kw in self._by_keyword:
                    continue
                self._by_keyword[kw] = rule
                parts.append(re.escape(kw))
        parts.sort(key=len, reverse=True)  # longest-first so "drift off" beats "drift"
        self._regex = re.compile(r"\b(?:%s)\b" % "|".join(parts)) if parts else None

    @classmethod
    def from_config(cls, rules_config: dict, version: int = 0) -> "RuleSet":
        return cls(dict(rules_config.get("categories", {})),
                   list(rules_config.get("rules", [])), version)

    def resolve(self, rule: dict) -> dict:
        """Rule + category defaults → concrete effect. Null rule fields inherit."""
        cat = self.categories.get(rule.get("category", ""), {})
        strength = rule.get("strength")
        if strength is None:
            strength = cat.get("strength", 0)
        pattern = rule.get("pattern") or cat.get("pattern") or "constant"
        params = {k: cat[k] for k in PARAM_FIELDS if cat.get(k) is not None}
        return {
            "rule_id": rule.get("id", ""),
            "category": rule.get("category", ""),
            "strength": strength,
            "pattern": pattern,
            "params": params,
        }

    def find(self, text: str, start_after: int, end_limit: int) -> list[tuple[int, dict]]:
        """Matches with end in (start_after, end_limit], in text order."""
        if self._regex is None:
            return []
        out = []
        for m in self._regex.finditer(text, 0, end_limit):
            if m.end() > start_after:
                rule = self._by_keyword.get(m.group(0))
                if rule is not None:
                    out.append((m.end(), self.resolve(rule)))
        return out


class KeywordScanner:
    """Streaming scanner over one turn's prose. ``get_ruleset`` is a callable
    so rule edits hot-apply between feeds."""

    def __init__(self, get_ruleset):
        self._get_ruleset = get_ruleset
        self._buf = ""
        self._scan_pos = 0

    def reset(self) -> None:
        self._buf = ""
        self._scan_pos = 0

    def feed(self, token: str) -> list[dict]:
        """Scan up to the last completed word; hold the trailing partial back."""
        self._buf += token.lower()
        tail = _TRAILING_WORD.search(self._buf)
        scan_end = tail.start() if tail else len(self._buf)
        effects = self._emit(scan_end)
        if len(self._buf) > _MAX_BUF:
            cut = len(self._buf) - _TRIM_TO
            self._buf = self._buf[cut:]
            self._scan_pos = max(0, self._scan_pos - cut)
        return effects

    def end_of_turn(self) -> list[dict]:
        """Flush: the turn boundary is a word boundary, so scan everything."""
        effects = self._emit(len(self._buf))
        self.reset()
        return effects

    def _emit(self, scan_end: int) -> list[dict]:
        matches = self._get_ruleset().find(self._buf, self._scan_pos, scan_end)
        if scan_end > self._scan_pos:
            self._scan_pos = scan_end
        return [eff for _, eff in matches]

    def scan_full_text(self, text: str) -> list[dict]:
        """One-shot scan (tester box / dev console) — same matching path."""
        probe = KeywordScanner(self._get_ruleset)
        effects = probe.feed(text)
        effects.extend(probe.end_of_turn())
        return effects
