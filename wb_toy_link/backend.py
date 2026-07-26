"""Toy Link — drives haptics devices (Lovense / buttplug.io) from the story.

Thin glue: the real logic lives in the ``toylink`` package. This file wires
WorldboxAI's module hooks to the trigger engines and the device loop:

    on_stream_token  → KeywordScanner + SemanticEngine feed → VibeState
    on_turn_stopped  → flush; cancelled/error turns hard-stop the devices
    /toys command + REST router + Toy Studio → manual control and settings

Requires a WorldboxAI build with the stream/turn-lifecycle module API
(``MODULE_API_FEATURES`` ⊇ {stream_tokens, turn_lifecycle}). Without it the
module loads but stays inert and says so in ``/status`` — no monkey-patching.
"""
import asyncio
import sys
from pathlib import Path

# Loaded as a loose spec module by the registry, so make the sibling package
# importable no matter the working directory.
_MODULE_DIR = str(Path(__file__).resolve().parent)
if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

from toylink.buttplug_client import ButtplugClient
from toylink.config_store import ConfigStore
from toylink.device_manager import DeviceManager
from toylink.keyword_engine import KeywordScanner
from toylink.lovense_client import LovenseClient
from toylink.patterns import VibeState
from toylink.semantic_engine import SemanticEngine

MODULE_ID = "wb_toy_link"
REQUIRED_FEATURES = {"stream_tokens", "turn_lifecycle"}

_services: dict = {}
_sdk = None                    # captured from hook calls; used for sdk.llm
_hooks_available = False
_story_enabled = True
_store: ConfigStore | None = None
_vibe: VibeState | None = None
_scanner: KeywordScanner | None = None
_semantic: SemanticEngine | None = None
_manager: DeviceManager | None = None
_lovense: LovenseClient | None = None
_buttplug: ButtplugClient | None = None
_bg_tasks: set = set()


def set_services(services: dict) -> None:
    global _services, _sdk, _hooks_available, _store, _vibe, _scanner
    global _semantic, _manager, _lovense, _buttplug
    _services = services or {}
    _sdk = None
    engine = _services.get("engine")
    features = set(getattr(engine, "MODULE_API_FEATURES", set()) or set())
    _hooks_available = REQUIRED_FEATURES <= features

    base = _services.get("global_data_dir") or _services.get("data_dir") \
        or Path(_MODULE_DIR) / "data"
    _store = ConfigStore(Path(base) / MODULE_ID)
    _vibe = VibeState()
    _lovense = LovenseClient(lambda: _store.config().get("lovense", {}))
    _buttplug = ButtplugClient(lambda: _store.config().get("buttplug", {}))
    _manager = DeviceManager(_vibe, [_lovense, _buttplug], _store.config)
    _scanner = KeywordScanner(_store.ruleset)
    _semantic = SemanticEngine(_classify_call, _store.ruleset, _store.config,
                               _apply_semantic_effect)


async def shutdown() -> None:
    """Test/teardown helper: stop the loop and drop connections."""
    if _manager is not None and _manager._task is not None:
        _manager._task.cancel()
        _manager._task = None
    if _buttplug is not None:
        await _buttplug.disconnect()
    if _lovense is not None:
        await _lovense.aclose()


# ----------------------------------------------------------------- LLM access

async def _classify_call(prompt: str) -> str:
    """Semantic classifier call: the user's per-module model override goes
    straight to engine.llm; otherwise the sdk bridge's "fastest" slot
    (module_fast_model) — provider-agnostic, inherits keys and retries.
    The sdk comes from the hooks when a turn has run, or from engine.sdk
    (graph.py owns one) so Toy Studio's tester works before any turn; as a
    last resort call engine.llm with the fast model directly."""
    override = (_store.config().get("semantic", {}) or {}).get("model_override", "")
    engine = _services.get("engine")
    inspector_ctx = {"call_type": "module_fast",
                     "step": "module:toy_link_semantic",
                     "module_source": MODULE_ID}
    if override and engine is not None:
        return await engine.llm.simple_completion(
            messages=[{"role": "user", "content": prompt}],
            model=override, temperature=0, inspector_ctx=inspector_ctx)
    sdk = _sdk or getattr(engine, "sdk", None)
    if sdk is not None:
        return await sdk.llm.generate(prompt, model_preference="fastest")
    if engine is not None:
        model = _app_default_model() or None
        return await engine.llm.simple_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model, temperature=0, inspector_ctx=inspector_ctx)
    raise RuntimeError("no LLM access: engine service unavailable")


def _app_default_model() -> str:
    engine = _services.get("engine")
    return str(getattr(getattr(engine, "llm", None), "module_fast_model", "") or "")


# ------------------------------------------------------------------- triggers

def _mode() -> str:
    return _store.config().get("trigger_mode", "hybrid")


def _apply_semantic_effect(effect: dict) -> None:
    label = effect.get("category") or "semantic"
    _vibe.apply_effect({**effect, "rule_id": label}, source_prefix="semantic:")


def _apply_keyword_effects(effects: list) -> None:
    for eff in effects:
        _vibe.apply_effect(eff, source_prefix="keyword:")


def _story_config(state: dict) -> dict:
    configs = (state or {}).get("module_configs") or {}
    own = configs.get(MODULE_ID)
    return own if isinstance(own, dict) else {}


# ---------------------------------------------------------------------- hooks

async def on_turn_start(state: dict, sdk) -> None:
    global _sdk, _story_enabled
    _sdk = sdk
    cfg = _story_config(state)
    _story_enabled = bool(cfg.get("enabled", True))
    _manager.story_cap = float(cfg.get("master_cap", 70))
    if not _story_enabled:
        # Disable must mean silence — never leave a latched level running.
        if _vibe.strength > 0 or _manager.current_level > 0:
            await _manager.instant_stop()
        return
    _scanner.reset()
    _manager.ensure_running()
    if _buttplug.enabled:
        _buttplug.start()


def on_stream_token(token: str, state: dict, sdk) -> None:
    # Sync and O(token): scanner/semantic buffering only; LLM calls and device
    # writes happen on their own tasks.
    if not _story_enabled or not _hooks_available:
        return
    mode = _mode()
    if mode in ("hybrid", "keywords"):
        effects = _scanner.feed(token)
        if effects:
            _apply_keyword_effects(effects)
    if mode in ("hybrid", "semantic"):
        _semantic.feed(token)


async def on_turn_stopped(state: dict, sdk, reason: str) -> None:
    if not _story_enabled:
        return
    mode = _mode()
    if mode in ("hybrid", "keywords"):
        _apply_keyword_effects(_scanner.end_of_turn())
    if reason in ("cancelled", "error"):
        # The kill switch: a stopped or crashed turn must not keep buzzing.
        await _manager.instant_stop()
        return
    if mode in ("hybrid", "semantic"):
        _semantic.end_of_turn()
    if _store.config().get("stop_on_turn_end"):
        await _manager.instant_stop()


# -------------------------------------------------------------------- command

async def on_command_toys(args: list, state: dict, sdk) -> dict:
    sub = (args[0].lower() if args else "status")
    if sub == "stop":
        await _manager.instant_stop()
        return {"message": "⏹ All toys stopped and cleared."}
    if sub in ("on", "off"):
        await _manager.set_gate(sub == "on")
        return {"message": "▶ Vibration enabled — returning to the story's level."
                if sub == "on" else "⏸ Vibration off (the story keeps tracking)."}
    if sub == "test":
        try:
            strength = float(args[1]) if len(args) > 1 else 50.0
        except ValueError:
            return {"message": "Usage: /toys test [0-100]", "error": True}
        _spawn(_manager.test_buzz(strength))
        return {"message": f"Test buzz at {int(strength)}%."}
    if sub == "status":
        s = _status()
        backends = ", ".join(
            f"{b['name']}: {'connected' if b['connected'] else ('off' if not b['enabled'] else 'not connected')}"
            for b in s["backends"])
        return {"message": (
            f"Toys — level {int(s['level'] * 100)}%, "
            f"{'on' if s['vibe_on'] else 'muted'}, mode {s['trigger_mode']}; "
            f"{backends or 'no backends'}."
            + ("" if s["hooks_available"] else
               " ⚠ WorldboxAI build lacks stream hooks — triggers inactive."))}
    return {"message": "Usage: /toys stop | on | off | test [0-100] | status",
            "error": True}


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# --------------------------------------------------------------------- status

def _status() -> dict:
    m = _manager.status()
    semantic = _semantic.status()
    semantic["model_override"] = (_store.config().get("semantic", {}) or {}) \
        .get("model_override", "")
    semantic["app_default_model"] = _app_default_model()
    return {
        "module": MODULE_ID,
        "hooks_available": _hooks_available,
        "story_enabled": _story_enabled,
        "trigger_mode": _mode(),
        "vibe_on": _vibe.vibe_on,
        "level": m["level"],
        "envelope": m["envelope"],
        "vibe": m["vibe"],
        "backends": m["backends"],
        "semantic": semantic,
    }


# --------------------------------------------------------------------- router

def _sanitize_config(updates: dict) -> dict:
    def clamp(value, lo, hi, fallback):
        try:
            return min(max(float(value), lo), hi)
        except (TypeError, ValueError):
            return fallback
    out = {}
    if isinstance(updates.get("lovense"), dict):
        lov = updates["lovense"]
        out["lovense"] = {k: lov[k] for k in
                          ("enabled", "host", "port", "use_time_sec_safety")
                          if k in lov}
        if "port" in out["lovense"]:
            out["lovense"]["port"] = int(clamp(out["lovense"]["port"], 1, 65535, 20010))
    if isinstance(updates.get("buttplug"), dict):
        bp = updates["buttplug"]
        out["buttplug"] = {k: bp[k] for k in ("enabled", "url") if k in bp}
    if "master_cap_global" in updates:
        out["master_cap_global"] = clamp(updates["master_cap_global"], 0, 100, 100)
    if "ramp_ms" in updates:
        out["ramp_ms"] = clamp(updates["ramp_ms"], 0, 10000, 500)
    if "tick_hz" in updates:
        out["tick_hz"] = clamp(updates["tick_hz"], 1, 30, 10)
    if "stop_on_turn_end" in updates:
        out["stop_on_turn_end"] = bool(updates["stop_on_turn_end"])
    if updates.get("trigger_mode") in ("hybrid", "keywords", "semantic"):
        out["trigger_mode"] = updates["trigger_mode"]
    if isinstance(updates.get("semantic"), dict):
        sem = updates["semantic"]
        out["semantic"] = {}
        if "model_override" in sem:
            out["semantic"]["model_override"] = str(sem["model_override"] or "").strip()
        if "window_chars" in sem:
            out["semantic"]["window_chars"] = int(clamp(sem["window_chars"], 100, 4000, 600))
        if "timeout_s" in sem:
            out["semantic"]["timeout_s"] = clamp(sem["timeout_s"], 1, 30, 4)
    return out


async def _sync_buttplug() -> None:
    if _buttplug.enabled:
        _buttplug.start()
    else:
        await _buttplug.disconnect()


def get_router():
    from fastapi import APIRouter, HTTPException
    router = APIRouter()

    @router.get("/status")
    async def status():
        return _status()

    @router.post("/stop")
    async def stop():
        await _manager.instant_stop()
        return {"ok": True, "status": _status()}

    @router.post("/toggle")
    async def toggle():
        await _manager.set_gate(not _vibe.vibe_on)
        return {"ok": True, "vibe_on": _vibe.vibe_on}

    @router.post("/test")
    async def test(body: dict | None = None):
        strength = float((body or {}).get("strength", 50))
        _manager.ensure_running()
        _spawn(_manager.test_buzz(strength))
        return {"ok": True}

    @router.get("/config")
    async def get_config():
        return _store.config()

    @router.put("/config")
    async def put_config(body: dict):
        merged = _store.save_config(_sanitize_config(body or {}))
        await _sync_buttplug()
        return merged

    @router.get("/rules")
    async def get_rules():
        return _store.rules()

    @router.put("/rules")
    async def put_rules(body: dict):
        if not isinstance(body, dict) or not isinstance(body.get("rules"), list):
            raise HTTPException(422, "expected {categories: {...}, rules: [...]}")
        return _store.save_rules(body)

    @router.post("/rules/test")
    async def rules_test(body: dict):
        """Tester box: what would this paragraph do? Keyword matches always;
        one semantic classification only when asked (it costs an LLM call)."""
        text = str((body or {}).get("text", ""))
        result = {"keyword_effects": _scanner.scan_full_text(text)}
        if (body or {}).get("semantic"):
            try:
                raw = await asyncio.wait_for(
                    _classify_call(_semantic_probe_prompt(text)),
                    float(_store.config().get("semantic", {}).get("timeout_s", 4)))
                result["semantic_verdict"] = _semantic._parse(raw)
            except Exception as e:
                result["semantic_error"] = f"{type(e).__name__}: {e}"
        return result

    @router.post("/backends/{name}/connect")
    async def connect(name: str):
        if name == "lovense":
            ok = await _lovense.probe()
            return {"ok": ok, "backend": _lovense.status()}
        if name == "buttplug":
            _buttplug.start()
            for _ in range(20):               # give the handshake a moment
                if _buttplug.connected:
                    break
                await asyncio.sleep(0.1)
            return {"ok": _buttplug.connected, "backend": _buttplug.status()}
        raise HTTPException(404, f"unknown backend {name!r}")

    @router.post("/backends/{name}/disconnect")
    async def disconnect(name: str):
        if name == "lovense":
            await _lovense.aclose()
            return {"ok": True}
        if name == "buttplug":
            await _buttplug.disconnect()
            return {"ok": True}
        raise HTTPException(404, f"unknown backend {name!r}")

    return router


def _semantic_probe_prompt(text: str) -> str:
    from toylink.semantic_engine import PROMPT_TEMPLATE
    ruleset = _store.ruleset()
    lines = []
    for name, cat in ruleset.categories.items():
        strength = cat.get("strength", 0)
        hint = " (this means: stop / wind down)" if not strength else ""
        lines.append(f'- "{name}": strength {strength}{hint}')
    return PROMPT_TEMPLATE.format(categories="\n".join(lines), window=text)
