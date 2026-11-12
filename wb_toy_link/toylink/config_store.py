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
    "between_blocks": "continue",      # "continue" | "low" | "stop"
    "low_between_strength": 20,
    "stop_on_turn_end": False,
    "sync_stage_reader": True,
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

    def _normalize_loaded(raw_disk: dict, merged: dict) -> dict:
        valid = [p for p in (merged.get("enabled_patterns") or []) if p in PATTERNS]
        merged["enabled_patterns"] = valid or list(PATTERNS)
        # Migrate legacy boolean `stop_on_turn_end` if `between_blocks` wasn't set yet
        if "between_blocks" not in (raw_disk or {}) and raw_disk.get("stop_on_turn_end") is True:
            merged["between_blocks"] = "stop"
        if merged.get("between_blocks") not in ("continue", "low", "stop"):
            merged["between_blocks"] = "stop" if merged.get("stop_on_turn_end") else "continue"
        merged["stop_on_turn_end"] = merged["between_blocks"] == "stop"
        return merged

    def config(self) -> dict:
        with self._lock:
            if self._config is None:
                raw_disk = self._read_json(CONFIG_FILE)
                merged = _deep_merge(DEFAULT_CONFIG, raw_disk)
                self._config = ConfigStore._normalize_loaded(raw_disk, merged)
            return json.loads(json.dumps(self._config))

    def save_config(self, updates: dict) -> dict:
        with self._lock:
            raw_disk = self._read_json(CONFIG_FILE)
            base = self._config if self._config is not None else ConfigStore._normalize_loaded(
                raw_disk, _deep_merge(DEFAULT_CONFIG, raw_disk)
            )
            upd = dict(updates or {})
            if "stop_on_turn_end" in upd and "between_blocks" not in upd:
                upd["between_blocks"] = "stop" if upd["stop_on_turn_end"] else "continue"
            merged = _deep_merge(base, upd)
            valid = [p for p in (merged.get("enabled_patterns") or []) if p in PATTERNS]
            merged["enabled_patterns"] = valid or list(PATTERNS)
            if merged.get("between_blocks") not in ("continue", "low", "stop"):
                merged["between_blocks"] = "continue"
            merged["stop_on_turn_end"] = merged["between_blocks"] == "stop"
            self._config = merged
            self._write_json(CONFIG_FILE, merged)
            return json.loads(json.dumps(merged))
