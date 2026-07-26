from toylink.config_store import DEFAULT_CONFIG, DEFAULT_RULES, ConfigStore
from toylink.keyword_engine import KeywordScanner


def test_defaults_when_no_files(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.config() == DEFAULT_CONFIG
    assert store.rules() == DEFAULT_RULES


def test_config_roundtrip_and_deep_merge(tmp_path):
    store = ConfigStore(tmp_path)
    store.save_config({"lovense": {"host": "192.168.1.50"}, "trigger_mode": "keywords"})

    fresh = ConfigStore(tmp_path)
    cfg = fresh.config()
    assert cfg["lovense"]["host"] == "192.168.1.50"
    assert cfg["lovense"]["port"] == 20010            # untouched sibling survives
    assert cfg["trigger_mode"] == "keywords"
    assert cfg["semantic"]["window_chars"] == 600     # defaults fill missing keys


def test_rules_roundtrip_replaces_wholesale(tmp_path):
    store = ConfigStore(tmp_path)
    custom = {"categories": {"gentle": {"strength": 20, "pattern": "constant"}},
              "rules": [{"id": "r_only", "keywords": ["only"], "category": "gentle",
                         "enabled": True, "strength": None, "pattern": None}]}
    store.save_rules(custom)

    fresh = ConfigStore(tmp_path)
    assert fresh.rules() == custom
    assert len(fresh.rules()["rules"]) == 1           # no merge with defaults


def test_ruleset_version_bumps_on_save(tmp_path):
    store = ConfigStore(tmp_path)
    v1 = store.ruleset().version
    assert store.ruleset().version == v1              # cached, no bump
    store.save_rules(store.rules())
    assert store.ruleset().version == v1 + 1


def test_default_rules_actually_match(tmp_path):
    store = ConfigStore(tmp_path)
    scanner = KeywordScanner(store.ruleset)
    effects = scanner.scan_full_text(
        "She kissed him slowly, moaning, then they drift off to sleep.")
    assert [e["rule_id"] for e in effects] == ["r_kiss", "r_moan", "r_sleep"]
    assert effects[-1]["strength"] == 0               # calm latches everything off


def test_default_categories_resolve_params(tmp_path):
    store = ConfigStore(tmp_path)
    rs = store.ruleset()
    throb = next(r for r in rs.rules if r["id"] == "r_throb")
    eff = rs.resolve(throb)
    assert eff["pattern"] == "pulse"
    assert eff["params"]["pulse_on_ms"] == 400
