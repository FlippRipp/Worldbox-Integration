from toylink.config_store import DEFAULT_CONFIG, ConfigStore
from toylink.patterns import PATTERNS


def test_defaults_when_no_files(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.config() == DEFAULT_CONFIG


def test_config_roundtrip_and_deep_merge(tmp_path):
    store = ConfigStore(tmp_path)
    store.save_config({
        "lovense": {"host": "192.168.1.50", "https": True},
        "contrast_mode": "high",
    })

    fresh = ConfigStore(tmp_path)
    cfg = fresh.config()
    assert cfg["lovense"]["host"] == "192.168.1.50"
    assert cfg["lovense"]["https"] is True
    assert cfg["lovense"]["port"] == 20010          # untouched sibling survives
    assert cfg["contrast_mode"] == "high"
    assert cfg["jev"]["min_interval_s"] == 1.8      # defaults fill missing keys


def test_enabled_patterns_filters_invalid_and_never_empties(tmp_path):
    store = ConfigStore(tmp_path)
    saved = store.save_config({"enabled_patterns": ["velvet", "bogus", "crest"]})
    assert saved["enabled_patterns"] == ["velvet", "crest"]

    # Empty or all-invalid falls back to full pattern set so JEV always has a palette
    saved2 = store.save_config({"enabled_patterns": ["invalid_only"]})
    assert saved2["enabled_patterns"] == list(PATTERNS)


def test_legacy_stop_on_turn_end_migrates_to_between_blocks(tmp_path):
    import json

    (tmp_path / "config.json").write_text(json.dumps({"stop_on_turn_end": True}), encoding="utf-8")
    store = ConfigStore(tmp_path)
    cfg = store.config()
    assert cfg["between_blocks"] == "stop"
    assert cfg["stop_on_turn_end"] is True

    updated = store.save_config({"between_blocks": "low"})
    assert updated["between_blocks"] == "low"
    assert updated["stop_on_turn_end"] is False

