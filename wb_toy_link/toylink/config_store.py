"""Persistence for the module's app-global config and trigger rules.

Both files live under ``<global_data_dir>/wb_toy_link/`` (device addresses and
rules are app-global — one set for all stories; the per-story enable/cap lives
in module_configs, not here). Loads deep-merge over the defaults so upgrades
that add fields never break an existing install.
"""
import json
import threading
from pathlib import Path

from .keyword_engine import RuleSet

CONFIG_FILE = "config.json"
RULES_FILE = "rules.json"

DEFAULT_CONFIG = {
    "lovense": {"enabled": True, "host": "", "port": 20010, "use_time_sec_safety": True},
    "buttplug": {"enabled": False, "url": "ws://127.0.0.1:12345"},
    "master_cap_global": 100,
    "ramp_ms": 500,
    "stop_on_turn_end": False,
    "tick_hz": 10,
    "trigger_mode": "hybrid",  # hybrid | keywords | semantic
    "semantic": {"model_override": "", "window_chars": 600, "timeout_s": 4},
}

# Shipped defaults: the out-of-box behavior most users will run. Strengths and
# word lists get tuned against real hardware; keywords are exact word/phrase
# matches (no stemming), so inflections are listed out.
DEFAULT_RULES = {
    "categories": {
        "gentle":   {"strength": 30, "pattern": "constant"},
        "moderate": {"strength": 55, "pattern": "wave",
                     "wave_low_pct": 35, "wave_period_ms": 3000},
        "intense":  {"strength": 85, "pattern": "pulse",
                     "pulse_on_ms": 400, "pulse_off_ms": 250},
        "peak":     {"strength": 100, "pattern": "pulse",
                     "pulse_on_ms": 250, "pulse_off_ms": 150},
        "calm":     {"strength": 0, "pattern": "constant"},
    },
    "rules": [
        {"id": "r_kiss", "keywords": ["kiss", "kisses", "kissed", "kissing"],
         "category": "gentle", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_caress", "keywords": ["caress", "caresses", "caressed", "caressing"],
         "category": "gentle", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_cuddle", "keywords": ["cuddle", "cuddles", "cuddled", "cuddling"],
         "category": "gentle", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_moan", "keywords": ["moan", "moans", "moaned", "moaning"],
         "category": "moderate", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_gasp", "keywords": ["gasp", "gasps", "gasped", "gasping"],
         "category": "moderate", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_shiver", "keywords": ["shiver", "shivers", "shivered", "shivering"],
         "category": "moderate", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_tease", "keywords": ["tease", "teases", "teased", "teasing"],
         "category": "moderate", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_throb", "keywords": ["throb", "throbs", "throbbed", "throbbing"],
         "category": "intense", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_thrust", "keywords": ["thrust", "thrusts", "thrusting"],
         "category": "intense", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_pound", "keywords": ["pound", "pounds", "pounding", "pounded"],
         "category": "intense", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_climax", "keywords": ["climax", "climaxes", "climaxed", "climaxing"],
         "category": "peak", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_orgasm", "keywords": ["orgasm", "orgasms", "orgasmed", "orgasming"],
         "category": "peak", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_finish", "keywords": ["finished", "afterglow", "spent"],
         "category": "calm", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_sleep", "keywords": ["asleep", "drift off", "drifts off", "falls asleep"],
         "category": "calm", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_wind_down", "keywords": ["settles down", "pulls away", "gets dressed"],
         "category": "calm", "enabled": True, "strength": None, "pattern": None},
    ],
}


def _deep_merge(defaults: dict, override: dict) -> dict:
    out = dict(defaults)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class ConfigStore:
    """Loads once, caches, and rebuilds the RuleSet only when rules change."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._config: dict | None = None
        self._rules: dict | None = None
        self._ruleset: RuleSet | None = None
        self._rules_version = 0

    def _read_json(self, name: str) -> dict:
        path = self.root / name
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write_json(self, name: str, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / (name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(self.root / name)

    def config(self) -> dict:
        with self._lock:
            if self._config is None:
                self._config = _deep_merge(DEFAULT_CONFIG, self._read_json(CONFIG_FILE))
            return self._config

    def save_config(self, updates: dict) -> dict:
        with self._lock:
            merged = _deep_merge(self._config or _deep_merge(DEFAULT_CONFIG, self._read_json(CONFIG_FILE)), updates)
            self._config = merged
            self._write_json(CONFIG_FILE, merged)
            return merged

    def rules(self) -> dict:
        with self._lock:
            if self._rules is None:
                stored = self._read_json(RULES_FILE)
                # Rules replace wholesale (a deep-merge of rule lists is nonsense);
                # missing file means the shipped defaults.
                self._rules = stored if stored.get("rules") is not None else json.loads(json.dumps(DEFAULT_RULES))
            return self._rules

    def save_rules(self, rules: dict) -> dict:
        with self._lock:
            self._rules = {
                "categories": dict(rules.get("categories", {})),
                "rules": list(rules.get("rules", [])),
            }
            self._write_json(RULES_FILE, self._rules)
            self._ruleset = None  # force rebuild with a bumped version
            return self._rules

    def ruleset(self) -> RuleSet:
        with self._lock:
            if self._ruleset is None:
                self._rules_version += 1
                rules = self._rules
                if rules is None:
                    stored = self._read_json(RULES_FILE)
                    rules = self._rules = (
                        stored if stored.get("rules") is not None
                        else json.loads(json.dumps(DEFAULT_RULES)))
                self._ruleset = RuleSet.from_config(rules, version=self._rules_version)
            return self._ruleset
