"""Toy Link — JEV-steered Lovense haptics module for WorldboxAI.

Wires WorldboxAI's streaming & turn-lifecycle module hooks to ``JevDirector``,
the procedural pattern engine, and ``LovenseClient``:

    on_turn_start    → sync story config, detect save switches, arm JevDirector
    on_stream_token  → feed tokens into JevDirector → VibeState
    on_turn_stopped  → final JEV flush; cancelled/error turns hard-stop devices
    /toys + REST API → Toy Studio, in-story controls, live pattern previews
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

_MODULE_DIR = str(Path(__file__).resolve().parent)


def _prepare_hot_reload(module_dir: str) -> None:
    """Purge stale ``toylink.*`` submodules and orphaned loops on hot-update."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        for task in list(asyncio.all_tasks(loop)):
            coro = task.get_coro()
            code = getattr(coro, "cr_code", None)
            fname = (getattr(code, "co_filename", "") or "").replace("\\", "/").lower()
            if "/toylink/device_manager.py" in fname and getattr(code, "co_name", "") == "_loop":
                task.cancel()

    norm_target = os.path.normcase(os.path.abspath(module_dir))
    sys.path[:] = [
        p for p in sys.path
        if os.path.normcase(os.path.abspath(p or ".")) != norm_target
    ]
    sys.path.insert(0, module_dir)

    for mod_name in list(sys.modules):
        if mod_name == "toylink" or mod_name.startswith("toylink."):
            sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()


_prepare_hot_reload(_MODULE_DIR)

from toylink.config_store import ConfigStore
from toylink.device_manager import DeviceManager
from toylink.jev_director import JevDirector
from toylink.lovense_client import LovenseClient, normalize_endpoint
from toylink.patterns import (
    CONTRAST_MODES,
    PATTERNS,
    PATTERN_SPECS,
    VibeState,
    normalize_pattern,
    sample_pattern_curve,
)

MODULE_ID = "wb_toy_link"
REQUIRED_FEATURES = {"stream_tokens", "turn_lifecycle"}

_services: dict = {}
_sdk = None
_hooks_available = False
_story_enabled = True
_active_save_id: str | None = None
_stage_paused_gate = False
_store: ConfigStore | None = None
_vibe: VibeState | None = None
_director: JevDirector | None = None
_manager: DeviceManager | None = None
_lovense: LovenseClient | None = None
_bg_tasks: set = set()


def set_services(services: dict) -> None:
    global _services, _sdk, _hooks_available, _story_enabled, _active_save_id, _stage_paused_gate
    global _store, _vibe, _director, _manager, _lovense
    _services = services or {}
    _sdk = None
    _story_enabled = True
    _active_save_id = None
    _stage_paused_gate = False

    engine = _services.get("engine")
    features = set(getattr(engine, "MODULE_API_FEATURES", set()) or set())
    _hooks_available = REQUIRED_FEATURES <= features

    base = _services.get("global_data_dir") or _services.get("data_dir") or Path(_MODULE_DIR) / "data"
    _store = ConfigStore(Path(base) / MODULE_ID)
    _vibe = VibeState()
    _lovense = LovenseClient(lambda: _store.config().get("lovense", {}))
    _manager = DeviceManager(_vibe, [_lovense], _store.config)
    _director = JevDirector(
        _decide_call,
        _store.config,
        _apply_jev_verdict,
        lambda: _vibe.strength,
    )

    # If a Lovense host is already saved, probe in the background on startup
    try:
        loop = asyncio.get_running_loop()
        if _lovense.enabled:
            _spawn(_startup_probe())
            _manager.ensure_running()
    except RuntimeError:
        pass


async def _startup_probe() -> None:
    if _lovense is None or _store is None:
        return
    found = await _lovense.probe(auto_discover=True)
    if found:
        _store.save_config({"lovense": found})


async def shutdown() -> None:
    """Stop the loop, cancel in-flight JEV tasks, and close HTTP clients."""
    if _director is not None:
        _director.invalidate()
    if _manager is not None and _manager._task is not None:
        _manager._task.cancel()
        _manager._task = None
    if _lovense is not None:
        await _lovense.aclose()


# ------------------------------------------------------------------ JEV bridge

def _jev_service():
    engine = _services.get("engine")
    llm = getattr(engine, "llm", None)
    return getattr(llm, "decisions", None)


def _jev_host_status() -> dict:
    svc = _jev_service()
    if svc is None:
        return {"available": False, "enabled": False, "has_credentials": False, "ready": False, "model": ""}
    pub = svc.public_config() if hasattr(svc, "public_config") else {}
    enabled = bool(pub.get("enabled", False))
    has_creds = bool(svc.has_credentials()) if hasattr(svc, "has_credentials") else bool(pub.get("api_key_set"))
    return {
        "available": True,
        "enabled": enabled,
        "has_credentials": has_creds,
        "ready": enabled and has_creds,
        "model": pub.get("model", "jev-latest"),
    }


async def _decide_call(state_payload: dict, questions: dict, step: str):
    """Route typed decisions through sdk.llm.decide (or engine.llm.decisions pre-turn)."""
    engine = _services.get("engine")
    sdk = _sdk or getattr(engine, "sdk", None)
    if sdk is not None and hasattr(getattr(sdk, "llm", None), "decide"):
        return await sdk.llm.decide(state_payload, questions, module=MODULE_ID, step=step)

    svc = _jev_service()
    if svc is not None:
        llm = getattr(engine, "llm", None)
        mode = getattr(llm, "mode", "live")
        inspector = getattr(llm, "inspector", None)
        return await svc.decide(
            state_payload,
            questions,
            module=MODULE_ID,
            step=step,
            mode=mode,
            inspector=inspector,
        )

    class _UnavailableDecision:
        answers = {}
        reason = "JEV service unavailable on host"
        finished = True

        async def finish(self, *_args, **_kwargs):
            pass

    return _UnavailableDecision()


def _apply_jev_verdict(verdict: dict) -> None:
    if not _story_enabled:
        return
    pat = verdict.get("pattern") or "velvet"
    _vibe.set_target(
        verdict.get("strength", 0.0),
        pat,
        source=f"jev:{pat}",
        pace=verdict.get("pace", 0.45),
    )
    _manager.ensure_running()


# ----------------------------------------------------------------- story hooks

def _story_config(state: dict) -> dict:
    configs = (state or {}).get("module_configs") or {}
    own = configs.get(MODULE_ID)
    return own if isinstance(own, dict) else {}


async def _apply_story_config(enabled: bool, master_cap: float, save_id: str | None = None) -> None:
    global _story_enabled, _active_save_id
    if save_id is not None and _active_save_id is not None and save_id != _active_save_id:
        # Switching stories clears the old story's latch so nothing bleeds across saves
        _director.invalidate()
        await _manager.instant_stop()
    if save_id is not None:
        _active_save_id = save_id

    _story_enabled = bool(enabled)
    _manager.story_cap = max(0.0, min(100.0, float(master_cap)))
    if not _story_enabled:
        _director.invalidate()
        if _vibe.strength > 0 or _manager.current_level > 0 or _manager.preview_active:
            await _manager.instant_stop()


async def on_turn_start(state: dict, sdk) -> None:
    global _sdk
    _sdk = sdk
    cfg = _story_config(state)
    await _apply_story_config(
        enabled=bool(cfg.get("enabled", True)),
        master_cap=float(cfg.get("master_cap", 70)),
        save_id=(state or {}).get("active_save_id"),
    )
    if not _story_enabled:
        return
    _director.start_turn(state)
    _manager.ensure_running()


def on_stream_token(token: str, state: dict, sdk) -> None:
    global _sdk
    if sdk is not None:
        _sdk = sdk
    if not _story_enabled or not _hooks_available:
        return
    _director.feed_token(token)


async def _apply_between_blocks_policy() -> None:
    """Apply user's Between Storyteller Blocks mode ('continue' | 'low' | 'stop')."""
    cfg = _store.config()
    mode = cfg.get("between_blocks") or ("stop" if cfg.get("stop_on_turn_end") else "continue")
    if mode == "stop":
        _director.invalidate()
        await _manager.instant_stop()
        return
    if mode == "low":
        # Only step down to a gentle low simmer if an active non-afterglow scene was playing;
        # never start vibration out of silence after a non-intimate block.
        if _vibe.strength > 0.5 and _vibe.pattern != "afterglow":
            low_cap = max(5.0, min(50.0, float(cfg.get("low_between_strength", 20.0) or 20.0)))
            target = min(_vibe.strength, low_cap)
            pat = _director.pattern_override or "velvet"
            _vibe.set_target(target, pat, source="between:low", pace=0.28)
            _manager.ensure_running()


def _schedule_between_blocks() -> None:
    try:
        _spawn(_apply_between_blocks_policy())
    except RuntimeError:
        pass


async def on_turn_stopped(state: dict, sdk, reason: str) -> None:
    global _sdk
    if sdk is not None:
        _sdk = sdk
    if not _story_enabled:
        return
    if reason in ("cancelled", "error"):
        _director.invalidate()
        await _manager.instant_stop()
        return
    if _director.is_reader_synced():
        # Flush the trailing sentence into JEV's pre-score queue; between-blocks
        # waits until the Paced Stage reader finishes displaying the final beat.
        _director.finish_turn()
        return
    cfg = _store.config()
    mode = cfg.get("between_blocks") or ("stop" if cfg.get("stop_on_turn_end") else "continue")
    if mode == "stop":
        _director.invalidate()
        await _manager.instant_stop()
        return
    if mode == "low":
        _director.finish_turn(on_settled=_schedule_between_blocks)
        return
    _director.finish_turn()


# -------------------------------------------------------------------- command

async def on_command_toys(args: list, state: dict, sdk) -> dict:
    sub = args[0].lower() if args else "status"
    if sub == "stop":
        _director.invalidate()
        await _manager.instant_stop()
        return {"message": "⏹ All toys stopped and cleared."}
    if sub in ("on", "off"):
        await _manager.set_gate(sub == "on")
        return {
            "message": "▶ Vibration enabled — returning to the story's level."
            if sub == "on"
            else "⏸ Vibration muted (JEV keeps tracking in the background)."
        }
    if sub == "test":
        try:
            strength = float(args[1]) if len(args) > 1 else 55.0
        except ValueError:
            return {"message": "Usage: /toys test [0-100] [pattern]", "error": True}
        pat = normalize_pattern(args[2]) if len(args) > 2 else "crest"
        _manager.ensure_running()
        _manager.start_preview(strength=strength, pattern=pat, seconds=2.5)
        return {"message": f"⚡ Testing {PATTERN_SPECS[pat]['label']} at {int(strength)}%."}
    if sub == "pattern":
        if len(args) < 2:
            return {
                "message": f"Available patterns: auto, {', '.join(PATTERNS)}",
                "error": True,
            }
        choice_name = args[1].lower()
        if choice_name in ("auto", "jev", "none"):
            _director.pattern_override = None
            return {"message": "🎛️ Pattern selection returned to Auto (JEV)."}
        if choice_name not in PATTERN_SPECS:
            return {
                "message": f"Unknown pattern {choice_name!r}. Choose: auto, {', '.join(PATTERNS)}",
                "error": True,
            }
        _director.pattern_override = choice_name
        if len(args) > 2:
            try:
                s = float(args[2])
                _vibe.set_target(s, choice_name, source=f"command:{choice_name}")
                _manager.ensure_running()
            except ValueError:
                pass
        elif _vibe.strength > 0:
            _vibe.set_target(_vibe.strength, choice_name, source=f"command:{choice_name}")
        return {"message": f"🎛️ Locked pattern to {PATTERN_SPECS[choice_name]['label']}."}
    if sub == "status":
        s = _status()
        lov = s["lovense"]
        lov_txt = (
            f"connected ({len(lov['devices'])} toy{'s' if len(lov['devices']) != 1 else ''})"
            if lov["connected"]
            else ("not configured" if not lov["configured_host"] else "not connected")
        )
        jev_txt = "ready" if s["jev"]["ready"] else ("disabled" if not s["jev"]["enabled"] else "needs API key")
        return {
            "message": (
                f"Toys — {int(s['level'] * 100)}% ({s['vibe']['pattern_label']}), "
                f"{'on' if s['vibe_on'] else 'muted'}, Lovense {lov_txt}, JEV {jev_txt}."
                + ("" if s["hooks_available"] else " ⚠ WorldboxAI build lacks stream hooks.")
            )
        }
    return {
        "message": "Usage: /toys stop | on | off | test [0-100] [pattern] | pattern <name|auto> | status",
        "error": True,
    }


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# --------------------------------------------------------------------- status

def _status() -> dict:
    m = _manager.status()
    lov = _lovense.status()
    jev = {**_jev_host_status(), **_director.status()}
    return {
        "module": MODULE_ID,
        "hooks_available": _hooks_available,
        "story_enabled": _story_enabled,
        "vibe_on": _vibe.vibe_on,
        "level": m["level"],
        "envelope": m["envelope"],
        "story_cap": m["story_cap"],
        "vibe": m["vibe"],
        "preview": m["preview"],
        "lovense": lov,
        "backends": m["backends"],
        "jev": jev,
    }


def _patterns_catalog() -> list[dict]:
    cfg = _store.config()
    enabled_set = set(cfg.get("enabled_patterns") or PATTERNS)
    contrast_mode = str(cfg.get("contrast_mode", "balanced"))
    items = []
    for pat_id in PATTERNS:
        spec = PATTERN_SPECS[pat_id]
        win_s = 14.0 if pat_id == "swell" else 6.5
        items.append({
            **spec,
            "enabled": pat_id in enabled_set,
            "curve_moderate": sample_pattern_curve(
                pat_id, duration_s=win_s, steps=84, intensity=58.0, contrast_mode=contrast_mode
            ),
            "curve_high": sample_pattern_curve(
                pat_id, duration_s=win_s, steps=84, intensity=92.0, contrast_mode=contrast_mode
            ),
        })
    return items


# --------------------------------------------------------------------- router

def _sanitize_config(updates: dict) -> dict:
    def clamp(value, lo, hi, fallback):
        try:
            return min(max(float(value), lo), hi)
        except (TypeError, ValueError):
            return fallback

    out: dict = {}
    if isinstance(updates.get("lovense"), dict):
        lov = updates["lovense"]
        lov_out: dict = {}
        if "enabled" in lov:
            lov_out["enabled"] = bool(lov["enabled"])
        if "host" in lov:
            https, host, port = normalize_endpoint(
                str(lov["host"] or ""),
                lov.get("port", _store.config().get("lovense", {}).get("port", 20010)),
                lov.get("https", _store.config().get("lovense", {}).get("https", False)),
            )
            lov_out["host"] = host
            # If user pasted host:port or https://host:port, carry port/https over
            if ":" in str(lov["host"] or "") or "://" in str(lov["host"] or ""):
                lov_out["port"] = int(clamp(port, 1, 65535, 20010))
                lov_out["https"] = bool(https)
        if "port" in lov and "port" not in lov_out:
            lov_out["port"] = int(clamp(lov["port"], 1, 65535, 20010))
        if "https" in lov and "https" not in lov_out:
            lov_out["https"] = bool(lov["https"])
        if lov.get("action_mode") in ("All", "Vibrate"):
            lov_out["action_mode"] = lov["action_mode"]
        if "use_time_sec_safety" in lov:
            lov_out["use_time_sec_safety"] = bool(lov["use_time_sec_safety"])
        if lov_out:
            out["lovense"] = lov_out

    if "master_cap_global" in updates:
        out["master_cap_global"] = clamp(updates["master_cap_global"], 0, 100, 100)
    if updates.get("contrast_mode") in CONTRAST_MODES:
        out["contrast_mode"] = updates["contrast_mode"]
    if updates.get("response_feel") in ("silky", "natural", "snappy"):
        out["response_feel"] = updates["response_feel"]
    if updates.get("between_blocks") in ("continue", "low", "stop"):
        out["between_blocks"] = updates["between_blocks"]
        out["stop_on_turn_end"] = updates["between_blocks"] == "stop"
    elif "stop_on_turn_end" in updates:
        out["stop_on_turn_end"] = bool(updates["stop_on_turn_end"])
        out["between_blocks"] = "stop" if out["stop_on_turn_end"] else "continue"
    if "low_between_strength" in updates:
        out["low_between_strength"] = int(clamp(updates["low_between_strength"], 5, 50, 20))
    if "sync_stage_reader" in updates:
        out["sync_stage_reader"] = bool(updates["sync_stage_reader"])
    if "tick_hz" in updates:
        out["tick_hz"] = int(clamp(updates["tick_hz"], 5, 30, 20))
    if isinstance(updates.get("enabled_patterns"), list):
        valid = [p for p in updates["enabled_patterns"] if p in PATTERNS]
        if valid:
            out["enabled_patterns"] = valid
    if isinstance(updates.get("jev"), dict):
        j = updates["jev"]
        jev_out: dict = {}
        if "min_interval_s" in j:
            jev_out["min_interval_s"] = clamp(j["min_interval_s"], 0.5, 10.0, 1.8)
        if "sensitivity" in j:
            jev_out["sensitivity"] = clamp(j["sensitivity"], 0.5, 1.5, 1.0)
        if jev_out:
            out["jev"] = jev_out
    return out


def get_router():
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.get("/status")
    async def status():
        return _status()

    @router.get("/patterns")
    async def patterns():
        return {"patterns": _patterns_catalog()}

    @router.post("/stop")
    async def stop():
        _director.invalidate()
        await _manager.instant_stop()
        return {"ok": True, "status": _status()}

    @router.post("/toggle")
    async def toggle():
        await _manager.set_gate(not _vibe.vibe_on)
        return {"ok": True, "vibe_on": _vibe.vibe_on, "status": _status()}

    @router.post("/manual")
    async def manual(body: dict | None = None):
        payload = body or {}
        try:
            strength = min(max(float(payload.get("strength", 0)), 0.0), 100.0)
        except (TypeError, ValueError):
            strength = 0.0
        pat = normalize_pattern(payload.get("pattern") or _director.pattern_override or _vibe.pattern)
        pace = payload.get("pace")
        _director.invalidate()
        _vibe.set_gate(True)
        _vibe.set_target(strength, pat, source=f"manual:{pat}", pace=pace)
        _manager.ensure_running()
        return {"ok": True, "strength": strength, "pattern": pat, "status": _status()}

    @router.post("/pattern-override")
    async def set_pattern_override(body: dict | None = None):
        raw = (body or {}).get("pattern")
        if not raw or str(raw).lower() in ("auto", "none", ""):
            _director.pattern_override = None
        else:
            pat = normalize_pattern(str(raw))
            _director.pattern_override = pat
            if _vibe.strength > 0:
                _vibe.set_target(_vibe.strength, pat, source=f"override:{pat}")
        return {"ok": True, "pattern_override": _director.pattern_override, "status": _status()}

    @router.post("/preview")
    async def preview(body: dict | None = None):
        payload = body or {}
        pat = normalize_pattern(payload.get("pattern") or "crest")
        try:
            strength = float(payload.get("strength", 65))
        except (TypeError, ValueError):
            strength = 65.0
        try:
            seconds = float(payload.get("seconds", 0.0))
        except (TypeError, ValueError):
            seconds = 0.0
        pace = payload.get("pace")
        _vibe.set_gate(True)
        _manager.ensure_running()
        _manager.start_preview(strength=strength, pattern=pat, seconds=seconds, pace=pace)
        return {"ok": True, "status": _status()}

    @router.post("/test")
    async def test(body: dict | None = None):
        payload = body or {}
        try:
            strength = float(payload.get("strength", 55))
        except (TypeError, ValueError):
            strength = 55.0
        pat = normalize_pattern(payload.get("pattern") or "crest")
        _vibe.set_gate(True)
        _manager.ensure_running()
        _manager.start_preview(strength=strength, pattern=pat, seconds=2.0)
        return {"ok": True}

    @router.post("/story-sync")
    async def story_sync(body: dict | None = None):
        payload = body or {}
        enabled = bool(payload.get("enabled", True))
        try:
            cap = float(payload.get("master_cap", 70))
        except (TypeError, ValueError):
            cap = 70.0
        save_id = payload.get("save_id")
        await _apply_story_config(enabled=enabled, master_cap=cap, save_id=save_id)
        return {"ok": True, "status": _status()}

    @router.post("/reader-sync")
    async def reader_sync(body: dict | None = None):
        """Sync haptics to Worldbox's Paced Stage reader sentence-by-sentence."""
        payload = body or {}
        phase = str(payload.get("phase") or "")
        if phase == "mode":
            is_stage = str(payload.get("mode") or "") == "stage"
            _director.set_reader_mode(is_stage)
            return {"ok": True, "reader_synced": _director.is_reader_synced()}

        if not _story_enabled:
            return {"ok": True, "ignored": True}

        if phase == "turn_start":
            global _stage_paused_gate
            _director.set_reader_mode(True)
            if _stage_paused_gate:
                _stage_paused_gate = False
                await _manager.set_gate(True)
            return {"ok": True}

        if phase == "pause":
            if _vibe.vibe_on:
                _stage_paused_gate = True
                await _manager.set_gate(False)
            return {"ok": True, "vibe_on": _vibe.vibe_on}

        if phase == "resume":
            if _stage_paused_gate:
                _stage_paused_gate = False
                await _manager.set_gate(True)
            return {"ok": True, "vibe_on": _vibe.vibe_on}

        if phase == "sentence":
            if _stage_paused_gate:
                _stage_paused_gate = False
                await _manager.set_gate(True)
            try:
                idx = int(payload.get("sentence_index", 0))
            except (TypeError, ValueError):
                idx = 0
            try:
                c_start = int(payload.get("char_start", 0))
                c_end = int(payload.get("char_end", 0))
            except (TypeError, ValueError):
                c_start, c_end = 0, 0
            text = str(payload.get("text") or "")
            verdict = await _director.on_reader_sentence(
                sentence_index=idx,
                char_start=c_start,
                char_end=c_end,
                text=text,
            )
            return {"ok": True, "verdict": verdict, "status": _status()}

        if phase == "turn_end":
            reason = str(payload.get("reason") or "completed")
            if reason == "stopped":
                _director.invalidate()
                await _manager.instant_stop()
            else:
                await _apply_between_blocks_policy()
            return {"ok": True, "status": _status()}

        return {"ok": True}

    @router.post("/story-leave")
    async def story_leave():
        """Silence story-driven output when exiting a story back to the main menu."""
        _director.invalidate()
        if not _manager.preview_active:
            await _manager.instant_stop()
        return {"ok": True}

    @router.get("/config")
    async def get_config():
        return _store.config()

    @router.put("/config")
    async def put_config(body: dict):
        return _store.save_config(_sanitize_config(body or {}))

    @router.post("/lovense/connect")
    @router.post("/backends/{name}/connect")
    async def connect(name: str = "lovense", body: dict | None = None):
        if name != "lovense":
            raise HTTPException(404, f"unknown backend {name!r}")
        if isinstance(body, dict) and body:
            _store.save_config(_sanitize_config({"lovense": body}))
        found = await _lovense.probe(auto_discover=True)
        if found:
            _store.save_config({"lovense": found})
            _manager.ensure_running()
        return {"ok": bool(found), "backend": _lovense.status(), "config": _store.config()}

    @router.post("/lovense/disconnect")
    @router.post("/backends/{name}/disconnect")
    async def disconnect(name: str = "lovense"):
        if name != "lovense":
            raise HTTPException(404, f"unknown backend {name!r}")
        await _manager.instant_stop()
        await _lovense.aclose()
        return {"ok": True, "backend": _lovense.status()}

    @router.post("/jev/enable")
    async def enable_jev():
        svc = _jev_service()
        if svc is None or not hasattr(svc, "save"):
            raise HTTPException(503, "JEV service is not available on this WorldboxAI build.")
        svc.save({"enabled": True})
        return {"ok": True, "jev": _jev_host_status()}

    @router.post("/jev/test-prose")
    async def test_prose(body: dict | None = None):
        payload = body or {}
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(422, "Provide non-empty prose text to test.")
        res = await _director.probe_prose(text)
        if res.get("ok") and payload.get("feel") and res["verdict"]["action"] == "apply":
            v = res["verdict"]
            if v["strength"] > 0:
                _vibe.set_gate(True)
                _manager.ensure_running()
                _manager.start_preview(
                    strength=v["strength"],
                    pattern=v["pattern"],
                    seconds=3.5,
                    pace=v["pace"],
                )
        return res

    return router
