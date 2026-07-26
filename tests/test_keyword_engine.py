from toylink.keyword_engine import KeywordScanner, RuleSet

RULES = {
    "categories": {
        "gentle": {"strength": 30, "pattern": "constant"},
        "intense": {"strength": 85, "pattern": "pulse",
                    "pulse_on_ms": 400, "pulse_off_ms": 250},
        "calm": {"strength": 0, "pattern": "constant"},
    },
    "rules": [
        {"id": "r_kiss", "keywords": ["kiss", "kissed", "kissing"],
         "category": "gentle", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_throb", "keywords": ["throbbing"],
         "category": "intense", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_custom", "keywords": ["shiver"],
         "category": "gentle", "enabled": True, "strength": 55, "pattern": "wave"},
        {"id": "r_sleep", "keywords": ["drift off", "asleep"],
         "category": "calm", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_drift", "keywords": ["drift"],
         "category": "gentle", "enabled": True, "strength": None, "pattern": None},
        {"id": "r_off", "keywords": ["banned"],
         "category": "intense", "enabled": False, "strength": None, "pattern": None},
    ],
}


def make_scanner():
    ruleset = RuleSet.from_config(RULES, version=1)
    return KeywordScanner(lambda: ruleset), ruleset


def ids(effects):
    return [e["rule_id"] for e in effects]


def test_resolution_inherits_category_defaults():
    _, ruleset = make_scanner()
    eff = ruleset.resolve(RULES["rules"][0])
    assert eff["strength"] == 30 and eff["pattern"] == "constant"
    eff = ruleset.resolve(RULES["rules"][1])
    assert eff["strength"] == 85 and eff["pattern"] == "pulse"
    assert eff["params"] == {"pulse_on_ms": 400, "pulse_off_ms": 250}


def test_resolution_rule_overrides_win():
    _, ruleset = make_scanner()
    eff = ruleset.resolve(RULES["rules"][2])
    assert eff["strength"] == 55 and eff["pattern"] == "wave"


def test_match_split_across_tokens():
    scanner, _ = make_scanner()
    assert scanner.feed("she felt it thr") == []
    assert ids(scanner.feed("obbing against her ")) == ["r_throb"]


def test_word_boundaries_no_substring_match():
    scanner, _ = make_scanner()
    # "kissingly" contains "kissing" but as part of a longer word: no match.
    assert scanner.feed("she moved kissingly ") == []
    # And "kiss" must not fire inside "kissed" (longest-first handles overlap).
    assert ids(scanner.feed("then kissed him ")) == ["r_kiss"]


def test_trailing_partial_word_held_until_disambiguated():
    scanner, _ = make_scanner()
    assert scanner.feed("a soft kiss") == []          # could still become "kissing"
    assert ids(scanner.feed(" on the cheek")) == ["r_kiss"]


def test_flush_on_end_of_turn():
    scanner, _ = make_scanner()
    assert scanner.feed("one last kiss") == []
    assert ids(scanner.end_of_turn()) == ["r_kiss"]
    # Scanner is reset afterwards.
    assert scanner.end_of_turn() == []


def test_multiword_phrase_across_feeds_and_longest_first():
    scanner, _ = make_scanner()
    assert scanner.feed("they drift") == []
    # "drift" alone is r_drift, but the completed phrase must win as "drift off".
    effects = scanner.feed(" off to sleep ")
    assert ids(effects) == ["r_sleep"]
    assert effects[0]["strength"] == 0    # stop-word category


def test_no_duplicate_emission():
    scanner, _ = make_scanner()
    assert ids(scanner.feed("a kiss goodnight ")) == ["r_kiss"]
    assert scanner.feed("and then quiet ") == []
    assert scanner.end_of_turn() == []


def test_effects_in_text_order_for_last_wins():
    scanner, _ = make_scanner()
    effects = scanner.feed("a throbbing pulse, then a gentle kiss and sleep. ")
    assert ids(effects) == ["r_throb", "r_kiss"]


def test_disabled_rule_never_fires():
    scanner, _ = make_scanner()
    assert scanner.feed("banned word ") == []
    assert scanner.end_of_turn() == []


def test_case_insensitive():
    scanner, _ = make_scanner()
    assert ids(scanner.feed("KISSED her. ")) == ["r_kiss"]


def test_scan_full_text_matches_streaming():
    scanner, _ = make_scanner()
    text = "A Kiss, then throbbing, then they drift off."
    assert ids(scanner.scan_full_text(text)) == ["r_kiss", "r_throb", "r_sleep"]
    # And it does not disturb the streaming buffer.
    assert scanner.feed("kissed ") != []


def test_hot_reload_ruleset_between_feeds():
    ruleset_holder = {"rs": RuleSet.from_config(RULES, version=1)}
    scanner = KeywordScanner(lambda: ruleset_holder["rs"])
    assert ids(scanner.feed("a kiss ")) == ["r_kiss"]
    ruleset_holder["rs"] = RuleSet.from_config(
        {"categories": {"gentle": {"strength": 10, "pattern": "constant"}},
         "rules": [{"id": "r_new", "keywords": ["sigh"], "category": "gentle",
                    "enabled": True, "strength": None, "pattern": None}]},
        version=2)
    assert scanner.feed("another kiss ") == []      # old rule gone
    assert ids(scanner.feed("a sigh ")) == ["r_new"]


def test_long_stream_buffer_stays_bounded():
    scanner, _ = make_scanner()
    for _ in range(200):
        scanner.feed("plain words with nothing interesting at all here ")
    assert len(scanner._buf) <= 4096
    assert ids(scanner.feed("finally a kiss ")) == ["r_kiss"]
