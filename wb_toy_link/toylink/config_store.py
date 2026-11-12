"""Persistence for Toy Link's app-global configuration.

Lives under ``<global_data_dir>/wb_toy_link/config.json``. Deep-merges over
``DEFAULT_CONFIG`` so upgrades adding fields never break an existing install.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .patterns import PATTERNS

CONFIG_FILE = "config.json"

DEFAULT_CONFIG: dict = {
    "lovense": {
        "enabled": True,
        "host": "",
        "port": 20010,
        "https": False,
        "action_mode": "All",          # "All" (all actuators) | "Vibrate"
        "use_time_sec_safety": True,
    },
    "master_cap_global": 100,
    "contrast_mode": "balanced",       # "subtle" | "balanced" | "high"
    "response_feel": "natural",        # "silky" | "natural" | "snappy"
    "stop_on_turn_end": False,
    "tick_hz": 20,
    "enabled_patterns": list(PATTERNS),
    "jev": {
        "min_interval_s": 1.8,
        "sensitivity": 1.0,
    },
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
    """Thread-safe JSON config store with atomic writes."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._config: dict | None = None

    def _read_json(self, name: str) -> dict:
        path = self.root / name
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
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
                merged = _deep_merge(DEFAULT_CONFIG, self._read_json(CONFIG_FILE))
                # Ensure enabled_patterns only keeps valid known pattern ids
                valid = [p for p in (merged.get("enabled_patterns") or []) if p in PATTERNS]
                merged["enabled_patterns"] = valid or list(PATTERNS)
                self._config = merged
            return json.loads(json.dumps(self._config))

    def save_config(self, updates: dict) -> dict:
        with self._lock:
            base = self._config if self._config is not None else _deep_merge(
                DEFAULT_CONFIG, self._read_json(CONFIG_FILE)
            )
            merged = _deep_merge(base, updates)
            valid = [p for p in (merged.get("enabled_patterns") or []) if p in PATTERNS]
            merged["enabled_patterns"] = valid or list(PATTERNS)
            self._config = merged
            self._write_json(CONFIG_FILE, merged)
            return json.loads(json.dumps(merged))
