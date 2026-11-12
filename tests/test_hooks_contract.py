"""Contract tests: backend glue, WorldboxAI hooks, REST API, and JSX syntax."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_jev_director import make_answers

MODULE_ROOT = Path(__file__).resolve().parent.parent / "wb_toy_link"
BACKEND_PATH = MODULE_ROOT / "backend.py"


def load_backend():
    spec = importlib.util.spec_from_file_location("wb_toy_link_backend_test", BACKEND_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BACKEND = load_backend()


class StubDecision:
    def __init__(self, answers=None, reason=""):
        self.answers = answers or {}
        self.reason = reason
        self.finished = False
        self.outcome = ""

    async def finish(self, outcome, reason=""):
        self.finished = True
        self.outcome = outcome


class StubJevService:
    def __init__(self):
        self.enabled = True
        self.has_creds = True
        self.calls = []
        self.answers = make_answers(pattern="swell")

    def public_config(self):
        return {"enabled": self.enabled, "model": "jev-latest", "api_key_set": self.has_creds}

    def has_credentials(self):
        return self.has_creds

    def save(self, updates):
        if "enabled" in updates:
            self.enabled = bool(updates["enabled"])
        return self.public_config()

    async def decide(self, state, questions, *, module, step, **_kwargs):
        self.calls.append({"state": state, "questions": questions, "module": module, "step": step})
        if not self.enabled or not self.has_creds:
            return StubDecision(reason="disabled or unavailable")
        return StubDecision(self.answers)


class StubSDK:
    def __init__(self, jev_service: StubJevService):
        async def decide(state, questions, *, module, step):
            return await jev_service.decide(state, questions, module=module, step=step)

        self.llm = SimpleNamespace(decide=decide)


class StubEngine:
    MODULE_API_FEATURES = frozenset({"stream_tokens", "turn_lifecycle"})

    def __init__(self):
        self.jev = StubJevService()
        self.llm = SimpleNamespace(decisions=self.jev, mode="live", inspector=None)
        self.sdk = StubSDK(self.jev)


class FakeLovense:
    name = "lovense"
    enabled = True
    connected = True
    toys = [{"id": "t1", "name": "lush", "display_name": "Lush 3", "battery": 90}]
    last_error = ""

    def __init__(self):
        self.levels = []
        self.stops = 0

    async def set_level(self, frac):
        self.levels.append(frac)

    async def stop(self):
        self.stops += 1

    async def probe(self, auto_discover=True):
        self.connected = True
        return {"host": "192.168.1.42", "port": 20010, "https": False}

    async def aclose(self):
        self.connected = False

    def status(self):
        return {
            "name": "lovense",
            "enabled": True,
            "configured_host": "192.168.1.42",
            "port": 20010,
            "https": False,
            "connected": self.connected,
            "devices": list(self.toys),
            "last_error": self.last_error,
        }


def wire(tmp_path, engine=None):
    engine = engine or StubEngine()
    BACKEND.set_services({"engine": engine, "global_data_dir": str(tmp_path)})
    fake = FakeLovense()
    BACKEND._lovense = fake
    BACKEND._manager.clients[:] = [fake]
    return engine, fake, engine.sdk


STATE = {
    "turn": 3,
    "active_save_id": "save_1",
    "history": ["They sat beside the fire."],
    "input_text": "I lean in closer.",
    "module_configs": {"wb_toy_link": {"enabled": True, "master_cap": 100}},
}


async def drain():
    for _ in range(12):
        await asyncio.sleep(0)


async def test_feature_detection_positive_and_negative(tmp_path):
    wire(tmp_path)
    assert BACKEND._hooks_available is True

    class BareEngine:
        pass

    BACKEND.set_services({"engine": BareEngine(), "global_data_dir": str(tmp_path)})
    assert BACKEND._hooks_available is False
    BACKEND.on_stream_token("A kiss. ", STATE, None)
    assert BACKEND._status()["hooks_available"] is False


async def test_turn_streams_into_jev_and_steers_vibe_state(tmp_path):
    engine, _, sdk = wire(tmp_path)
    try:
        engine.jev.answers = make_answers(
            int_probs={"none": 0.0, "subtle": 0.0, "sensual": 0.0, "aroused": 0.2, "intense": 0.8, "climax": 0.0},
            pace_probs={"slow": 0.0, "measured": 0.0, "fast": 0.8, "frantic": 0.2},
            pattern="rhythm",
        )
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("Their rhythm quickened breathlessly. ", STATE, sdk)
        await drain()
        assert BACKEND._vibe.pattern == "rhythm"
        assert BACKEND._vibe.strength == pytest.approx(80.0)
        assert BACKEND._vibe.pace == pytest.approx(0.80)
        assert engine.jev.calls[0]["state"]["player_action"] == "I lean in closer."
        await BACKEND.on_turn_stopped(STATE, sdk, "completed")
        await drain()
        assert BACKEND._vibe.strength == pytest.approx(80.0)
    finally:
        await BACKEND.shutdown()


@pytest.mark.parametrize("reason", ["cancelled", "error"])
async def test_cancelled_or_error_turn_hard_stops(tmp_path, reason):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("Intense passion. ", STATE, sdk)
        await drain()
        assert BACKEND._vibe.strength > 0
        await BACKEND.on_turn_stopped(STATE, sdk, reason)
        assert BACKEND._vibe.strength == 0
        assert fake.stops >= 1
    finally:
        await BACKEND.shutdown()


async def test_save_switch_and_story_disable_immediately_stop(tmp_path):
    _, fake, sdk = wire(tmp_path)
    try:
        await BACKEND.on_turn_start(STATE, sdk)
        BACKEND.on_stream_token("Intense kiss. ", STATE, sdk)
        await drain()
        assert BACKEND._vibe.strength > 0

        # Switching to another save stops the previous save's latched output
        other_save = {**STATE, "active_save_id": "save_2"}
        await BACKEND.on_turn_start(other_save, sdk)
        assert BACKEND._vibe.strength == 0
        assert fake.stops >= 1
    finally:
        await BACKEND.shutdown()


async def test_toys_slash_commands(tmp_path):
    _, _, sdk = wire(tmp_path)
    try:
        out = await BACKEND.on_command_toys(["pattern", "crest", "75"], STATE, sdk)
        assert "Crest Surge" in out["message"]
        assert BACKEND._director.pattern_override == "crest"
        assert BACKEND._vibe.strength == 75.0

        out = await BACKEND.on_command_toys(["pattern", "auto"], STATE, sdk)
        assert BACKEND._director.pattern_override is None

        out = await BACKEND.on_command_toys(["stop"], STATE, sdk)
        assert BACKEND._vibe.strength == 0.0
    finally:
        await BACKEND.shutdown()


def make_client(tmp_path):
    engine, _, _ = wire(tmp_path)
    app = FastAPI()
    app.include_router(BACKEND.get_router(), prefix="/api/modules/wb_toy_link")
    return TestClient(app), engine


def test_router_endpoints_patterns_preview_and_jev_prose(tmp_path):
    client, engine = make_client(tmp_path)

    status = client.get("/api/modules/wb_toy_link/status").json()
    assert status["module"] == "wb_toy_link"
    assert status["jev"]["ready"] is True
    assert status["lovense"]["connected"] is True

    pats = client.get("/api/modules/wb_toy_link/patterns").json()["patterns"]
    assert len(pats) == 7
    assert all(len(p["curve_high"]) == 84 for p in pats)

    # Config sanitization including URL host:port extraction
    cfg = client.put("/api/modules/wb_toy_link/config", json={
        "lovense": {"host": "https://192.168.1.77:30010"},
        "contrast_mode": "high",
        "master_cap_global": 250,
    }).json()
    assert cfg["lovense"]["host"] == "192.168.1.77"
    assert cfg["lovense"]["port"] == 30010
    assert cfg["lovense"]["https"] is True
    assert cfg["master_cap_global"] == 100

    # Enable JEV endpoint
    engine.jev.enabled = False
    assert client.get("/api/modules/wb_toy_link/status").json()["jev"]["enabled"] is False
    assert client.post("/api/modules/wb_toy_link/jev/enable").json()["jev"]["enabled"] is True

    # Prose tester endpoint
    probe = client.post("/api/modules/wb_toy_link/jev/test-prose", json={
        "text": "She kissed him deeply.",
        "feel": True,
    }).json()
    assert probe["ok"] is True
    assert probe["verdict"]["pattern"] == "swell"

    # Story-sync disable stops immediately
    sync = client.post("/api/modules/wb_toy_link/story-sync", json={
        "enabled": False,
        "master_cap": 60,
    }).json()
    assert sync["status"]["story_enabled"] is False
    assert sync["status"]["level"] == 0.0

    # Story-leave silences
    assert client.post("/api/modules/wb_toy_link/story-leave").json()["ok"] is True


def test_jsx_files_have_balanced_structure_and_valid_imports():
    jsx_files = list(MODULE_ROOT.rglob("*.jsx"))
    assert len(jsx_files) == 7
    # widget_settings.jsx must NOT import relative modules or `storage`
    ws_src = (MODULE_ROOT / "widget_settings.jsx").read_text(encoding="utf-8")
    assert "from './" not in ws_src and "from 'storage'" not in ws_src
    # widget.jsx must NOT use `transition-all` on the floating orb
    w_src = (MODULE_ROOT / "widget.jsx").read_text(encoding="utf-8")
    assert "transition-all" not in w_src
    assert "translate3d" in w_src


def test_hot_reload_evicts_stale_v01_toylink_submodules():
    """Simulates Worldbox's Module Manager hot-updating from v0.1.0 where old
    `toylink` / `toylink.patterns` (lacking `normalize_pattern`) are cached in
    `sys.modules`."""
    import sys
    import types

    stale_pkg = types.ModuleType("toylink")
    stale_patterns = types.ModuleType("toylink.patterns")
    stale_patterns.PATTERNS = ("constant", "pulse", "wave", "escalate")
    sys.modules["toylink"] = stale_pkg
    sys.modules["toylink.patterns"] = stale_patterns

    reloaded = load_backend()
    assert callable(reloaded.normalize_pattern)
    assert reloaded.normalize_pattern("wave") == "swell"
    assert sys.modules["toylink.patterns"] is not stale_patterns

