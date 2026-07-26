"""Contract tests: the module glue against stub WorldboxAI services.

backend.py is loaded exactly the way WorldboxAI's registry loads it — as a
loose spec module from its file path — so the sys.path bootstrap and package
imports get exercised too.
"""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_PATH = Path(__file__).resolve().parent.parent / "wb_toy_link" / "backend.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("wb_toy_link_backend_test",
                                                  BACKEND_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BACKEND = load_backend()


class StubLLMService:
    def __init__(self):
        self.module_fast_model = "provider/fast-model"
        self.calls = []
        self.response = json.dumps({"no_change": True})

    async def simple_completion(self, messages, model=None, **kwargs):
        self.calls.append({"model": model, "prompt": messages[0]["content"]})
        return self.response


class StubEngine:
    MODULE_API_FEATURES = frozenset({"stream_tokens", "turn_lifecycle"})

    def __init__(self):
        self.llm = StubLLMService()


class StubBridge:
    def __init__(self):
        self.calls = []
        self.response = json.dumps({"no_change": True})

    async def generate(self, prompt, model_preference="balanced", **kwargs):
        self.calls.append({"preference": model_preference, "prompt": prompt})
        return self.response


class StubSDK:
    def __init__(self):
        self.llm = StubBridge()


class FakeClient:
    name = "fake"
    enabled = True
    connected = True

    def __init__(self):
        self.levels = []
        self.stops = 0
        self.last_error = ""

    async def set_level(self, frac):
        self.levels.append(frac)

    async def stop(self):
        self.stops += 1

    def status(self):
        return {"name": self.name, "enabled": True, "connected": True,
                "devices": [], "last_error": ""}


def wire(tmp_path, engine=None):
    """set_services with stubs and swap the device clients for fakes."""
    engine = engine or StubEngine()
    BACKEND.set_services({"engine": engine, "global_data_dir": str(tmp_path)})
    fake = FakeClient()
    BACKEND._manager.clients[:] = [fake]
    return engine, fake, StubSDK()


STATE = {"turn": 3, "module_configs": {"wb_toy_link": {"enabled": True,
                                                       "master_cap": 100}}}


async def drain():
    for _ in range(10):
        await asyncio.sleep(0)


async def test_feature_detection_positive_and_negative(tmp_path):
    engine, _, _ = wire(tmp_path)
    assert BACKEND._hooks_available is True

    class BareEngine:
        pass
    BACKEND.set_services({"engine": BareEngine(), "global_data_dir": str(tmp_path)})
    assert BACKEND._hooks_available is False
    BACKEND.on_stream_token("kissed ", STATE, None)   # inert, must not raise
    assert BACKEND._status()["hooks_available"] is False
    cmd = await BACKEND.on_command_toys(["status"], STATE, None)
    assert "lacks stream hooks" in cmd["message"]


async def test_turn_flow_keyword_latch_and_completed_keeps_state(tmp_path):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("she leaned in and kissed ", STATE, sdk)
        BACKEND.on_stream_token("him softly", STATE, sdk)
        assert BACKEND._vibe.strength == 30          # gentle latched mid-stream
        assert BACKEND._vibe.source == "keyword:r_kiss"
        await BACKEND.on_turn_stopped(STATE, sdk, "completed")
        await drain()
        assert BACKEND._vibe.strength == 30          # latch persists after turn
    finally:
        await BACKEND.shutdown()


@pytest.mark.parametrize("reason", ["cancelled", "error"])
async def test_cancelled_or_error_turn_hard_stops(tmp_path, reason):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("throbbing intensity. ", STATE, sdk)
        assert BACKEND._vibe.strength == 85
        await BACKEND.on_turn_stopped(STATE, sdk, reason)
        assert BACKEND._vibe.strength == 0
        assert fake.stops >= 1
    finally:
        await BACKEND.shutdown()


async def test_stop_on_turn_end_config(tmp_path):
    _, fake, sdk = wire(tmp_path)
    try:
        BACKEND._store.save_config({"stop_on_turn_end": True})
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("a kiss. ", STATE, sdk)
        await BACKEND.on_turn_stopped(STATE, sdk, "completed")
        await drain()
        assert BACKEND._vibe.strength == 0
    finally:
        await BACKEND.shutdown()


async def test_story_disabled_ignores_tokens_and_silences(tmp_path):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("kissed. ", STATE, sdk)
        assert BACKEND._vibe.strength == 30
        disabled = {"turn": 4, "module_configs": {"wb_toy_link": {"enabled": False}}}
        await BACKEND.on_turn_start(disabled, sdk)
        assert BACKEND._vibe.strength == 0           # disable = instant stop
        assert fake.stops >= 1
        BACKEND.on_stream_token("throbbing. ", disabled, sdk)
        assert BACKEND._vibe.strength == 0           # tokens ignored while off
    finally:
        await BACKEND.shutdown()


async def test_semantic_verdict_overrides_keyword_state(tmp_path):
    _, _, sdk = wire(tmp_path)
    try:
        sdk.llm.response = json.dumps({"category": "calm"})
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("she remembered how he kissed her once. ", STATE, sdk)
        assert BACKEND._vibe.strength == 30          # keyword twitch (false positive)
        await drain()                                # semantic verdict lands
        assert BACKEND._vibe.strength == 0           # corrected to calm
        assert BACKEND._vibe.source == "semantic:calm"
        assert sdk.llm.calls[0]["preference"] == "fastest"
    finally:
        await BACKEND.shutdown()


async def test_model_override_routes_to_engine_llm(tmp_path):
    engine, _, sdk = wire(tmp_path)
    try:
        BACKEND._store.save_config({"semantic": {"model_override": "org/custom-model"}})
        engine.llm.response = json.dumps({"category": "gentle"})
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("A sentence. ", STATE, sdk)
        await drain()
        assert engine.llm.calls and engine.llm.calls[0]["model"] == "org/custom-model"
        assert sdk.llm.calls == []                   # bridge not used
    finally:
        await BACKEND.shutdown()


async def test_keywords_mode_skips_semantic(tmp_path):
    _, _, sdk = wire(tmp_path)
    try:
        BACKEND._store.save_config({"trigger_mode": "keywords"})
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("kissed her. ", STATE, sdk)
        await drain()
        assert sdk.llm.calls == []
        assert BACKEND._vibe.strength == 30
    finally:
        await BACKEND.shutdown()


async def test_toys_command_stop_on_off_test(tmp_path):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("throbbing. ", STATE, sdk)
        out = await BACKEND.on_command_toys(["stop"], STATE, sdk)
        assert "stopped" in out["message"]
        assert BACKEND._vibe.strength == 0

        out = await BACKEND.on_command_toys(["off"], STATE, sdk)
        assert BACKEND._vibe.vibe_on is False
        out = await BACKEND.on_command_toys(["on"], STATE, sdk)
        assert BACKEND._vibe.vibe_on is True

        out = await BACKEND.on_command_toys(["test", "abc"], STATE, sdk)
        assert out.get("error") is True
        out = await BACKEND.on_command_toys(["bogus"], STATE, sdk)
        assert out.get("error") is True
    finally:
        await BACKEND.shutdown()


def make_client(tmp_path):
    wire(tmp_path)
    app = FastAPI()
    app.include_router(BACKEND.get_router(), prefix="/api/modules/wb_toy_link")
    return TestClient(app)


def test_router_status_toggle_and_stop(tmp_path):
    client = make_client(tmp_path)
    status = client.get("/api/modules/wb_toy_link/status").json()
    assert status["module"] == "wb_toy_link"
    assert status["hooks_available"] is True
    assert status["semantic"]["app_default_model"] == "provider/fast-model"

    assert client.post("/api/modules/wb_toy_link/toggle").json()["vibe_on"] is False
    assert client.post("/api/modules/wb_toy_link/toggle").json()["vibe_on"] is True
    assert client.post("/api/modules/wb_toy_link/stop").json()["ok"] is True


def test_router_config_sanitization_and_rules_roundtrip(tmp_path):
    client = make_client(tmp_path)
    cfg = client.put("/api/modules/wb_toy_link/config", json={
        "master_cap_global": 400, "trigger_mode": "bogus",
        "lovense": {"host": "10.0.0.5", "port": 99999},
        "semantic": {"model_override": "  org/model  ", "window_chars": 50},
    }).json()
    assert cfg["master_cap_global"] == 100            # clamped
    assert cfg["trigger_mode"] == "hybrid"            # invalid value rejected
    assert cfg["lovense"]["port"] == 65535
    assert cfg["semantic"]["model_override"] == "org/model"
    assert cfg["semantic"]["window_chars"] == 100     # clamped up to minimum

    rules = client.get("/api/modules/wb_toy_link/rules").json()
    rules["rules"] = [r for r in rules["rules"] if r["id"] == "r_kiss"]
    saved = client.put("/api/modules/wb_toy_link/rules", json=rules).json()
    assert len(saved["rules"]) == 1
    assert client.put("/api/modules/wb_toy_link/rules", json={"nope": 1}).status_code == 422


def test_router_manual_drive(tmp_path):
    client = make_client(tmp_path)
    out = client.post("/api/modules/wb_toy_link/manual", json={"strength": 62}).json()
    assert out == {"ok": True, "strength": 62.0}
    assert BACKEND._vibe.strength == 62.0
    assert BACKEND._vibe.source == "manual:drive"
    # Clamps garbage, and un-mutes: manual drive is explicit intent.
    client.post("/api/modules/wb_toy_link/toggle")
    assert BACKEND._vibe.vibe_on is False
    out = client.post("/api/modules/wb_toy_link/manual", json={"strength": 900}).json()
    assert out["strength"] == 100.0
    assert BACKEND._vibe.vibe_on is True
    out = client.post("/api/modules/wb_toy_link/manual", json={"strength": "junk"}).json()
    assert out["strength"] == 0.0


def test_router_rules_tester(tmp_path):
    client = make_client(tmp_path)
    out = client.post("/api/modules/wb_toy_link/rules/test", json={
        "text": "She kissed him, then they drift off."}).json()
    assert [e["rule_id"] for e in out["keyword_effects"]] == ["r_kiss", "r_sleep"]
    assert "semantic_verdict" not in out              # not requested

    # Before any turn has run there is no hook-captured sdk — the tester must
    # still reach the LLM through the engine service (the bug report: "no llm
    # access" from Toy Studio on a fresh backend).
    out = client.post("/api/modules/wb_toy_link/rules/test", json={
        "text": "hello", "semantic": True}).json()
    assert out.get("semantic_verdict") == {"no_change": True}
    assert "semantic_error" not in out


async def test_classify_uses_engine_sdk_before_first_turn(tmp_path):
    engine = StubEngine()
    engine.sdk = StubSDK()                            # graph.py owns one of these
    engine.sdk.llm.response = json.dumps({"category": "gentle"})
    wire(tmp_path, engine=engine)
    raw = await BACKEND._classify_call("prompt text")
    assert json.loads(raw) == {"category": "gentle"}
    assert engine.sdk.llm.calls[0]["preference"] == "fastest"
    assert engine.llm.calls == []                     # bridge preferred over raw llm


async def test_classify_falls_back_to_engine_llm_without_any_sdk(tmp_path):
    engine, _, _ = wire(tmp_path)                     # StubEngine has no .sdk
    engine.llm.response = json.dumps({"no_change": True})
    raw = await BACKEND._classify_call("prompt text")
    assert json.loads(raw) == {"no_change": True}
    # Uses the app's fast model explicitly, so the right slot is billed/logged.
    assert engine.llm.calls[0]["model"] == "provider/fast-model"
