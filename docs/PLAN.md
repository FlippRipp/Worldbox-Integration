# WorldBox AI — Sex Toy (Haptics) Integration: `wb_toy_link`

## Context

FlippRipp/WorldboxAI is an AI-driven text roleplaying engine (FastAPI backend + React 19/Vite frontend) with a drop-in module system: each module lives in `modules/<id>/` with `manifest.json`, a `backend.py` exposing optional async hooks, and raw `.jsx` widgets Babel-compiled in the browser at runtime. The storyteller LLM streams prose token-by-token (`sdk.ui.emit_token` → `chat_hub` → websocket `/ws/chat`).

This repo (`FlippRipp/Worldbox-Sex-Toy-Integration-`) will hold a haptics module installed by symlinking/copying its folder into `WorldboxAI/modules/`. All `file:line` references below point into the FlippRipp/WorldboxAI source tree.

**Requirements (confirmed with user):**
1. Two device backends: **Lovense direct local API** (preferred, more stable) + **Intiface Central / buttplug.io** websocket (broad compatibility). Both can run simultaneously.
2. Primary trigger: **keyword detection during LLM token streaming** — react in real time as prose streams, with per-keyword strength and pattern (latched state, no durations — user's explicit choice).
3. Manual control panel + safety: status widget, test buzz, master intensity cap, instant stop.
4. **Semantic triggers via OpenRouter** (user-confirmed): a small instruct LLM classifies streamed prose near-real-time, fixing keyword imprecision (negation, memories, paraphrase). **`trigger_mode: "hybrid"` is the default** — keywords react instantly, semantic verdicts override with authority ~1 s later. Degrades to keywords-only when no API key is configured.
5. **Extend WorldboxAI's module API** (user-approved): add official `on_stream_token` + turn-lifecycle hooks to core on a branch in FlippRipp/WorldboxAI. The toy module **requires** these hooks — no `emit_token` monkey-patch fallback (user's explicit choice). On an older WorldboxAI checkout the module loads but disables itself with a clear "update WorldboxAI" status message.

## Architecture

Single asyncio update loop (~10 Hz) owns all device writes. Trigger producers (keyword matches, semantic verdicts) never touch devices directly — they update a latched `VibeState` (strength + pattern, **no durations**); the loop evaluates `pattern(t) × strength × master_cap` and fans out to whichever backends are connected. This makes instant-stop trivial, puts the cap at one choke point, and keeps per-token work allocation-free/non-blocking.

```
storyteller ─► on_stream_token (new core hook) ─► KeywordScanner ────► VibeState
                                  └─► SemanticClassifier (async, ─────►   │
                                      OpenRouter; hybrid override)        │ 10 Hz loop
turn cancel ─► on_turn_stopped (new core hook) ─► instant stop            │
REST router + /toys command + widgets ─────────────────────────►  DeviceManager
                                                         ┌──────────┴──────────┐
                                                   LovenseClient        ButtplugClient
                                                (httpx :20010/command)  (vendored bp v3 over websockets)
```

### WorldboxAI core API extension (new — branch `claude/module-stream-hooks` in FlippRipp/WorldboxAI)

Small, generic addition (~30-50 lines) that benefits any module (TTS, sound effects, live translation), not just haptics:

1. **`on_stream_token(token, state, sdk)`** — in `graph.py`'s storyteller node: at turn start, collect the active modules exposing this hook (respecting `__active_modules__` gating, same filtering as `_run_modules_in_levels`); wrap the streaming callback so each emitted token is also dispatched to subscribers. Per-token dispatch must be cheap and safe: exceptions swallowed per-subscriber (log once per turn, not per token), and `state` passed as the same filtered view built once at turn start — not rebuilt per token.
2. **`on_turn_start(state, sdk)` / `on_turn_stopped(state, sdk, reason)`** — dispatched from the turn entry point, and with `reason="cancelled"` from the turn-cancel handler (`server.py:2679-2680`) or `reason="completed"` on normal turn completion. `reason="cancelled"` gives the toy module instant vibration halt when the user hits Stop (without it, a cancelled turn would leave the last latched state running with no automatic stop).
3. **Feature detection** — add `MODULE_API_FEATURES = {"stream_tokens", "turn_lifecycle"}` as a class attribute on `EngineGraph`. The toy module reads `getattr(services["engine"], "MODULE_API_FEATURES", set())` in `set_services`: if `stream_tokens` is missing, the module marks itself inactive and surfaces "requires updated WorldboxAI (stream hooks)" in `/status` and the sidebar widget. **No `emit_token` monkey-patch fallback** (user's explicit choice — keeps the module 100 % official-API).
4. Document the new hooks in `docs/MODULES.md`, and extend `test_module_contract.py` with a test that a stub module's `on_stream_token`/`on_turn_start`/`on_turn_stopped` get called (fake streaming callback, no LLM).

Workflow note: requires adding the WorldboxAI repo to this session (`add_repo` with push access — user approved). Work lands on branch `claude/module-stream-hooks`; no pushes to WorldboxAI's default branch, and no PR unless the user asks.

Streaming path facts core work builds on: storyteller passes `streaming_callback=self.sdk.ui.emit_token` looked up fresh each turn (`graph.py:824`); one `WorldBoxSDK` per `EngineGraph` (`graph.py:43`); engine created once at import (`server.py:96`); turn cancel handler at `server.py:2679-2680`. The fan-out wrapper lives in core at the `streaming_callback` construction site, and `on_turn_stopped` dispatch goes in both the cancel handler and normal turn completion.

Module-side hook duties: `on_stream_token` → `KeywordScanner.feed()` (sync, O(len(token)), exceptions never escape); `on_turn_start` → reset scanner turn state; `on_turn_stopped` → flush scanner tail, then if `reason=="cancelled"` (or `stop_on_turn_end` config) clear state + immediate device stop, else keep the latched state (it persists until a stop-word, a manual stop, or the next match). Gating: core already filters dispatch by `__active_modules__`; `feed()` additionally checks the per-story `enabled` config.

### Device clients (vendored in module — modules can't add pip deps; `websockets` + `httpx` are already WorldboxAI deps)

**ButtplugClient** (buttplug protocol v3 JSON over websocket, default `ws://127.0.0.1:12345`): handshake `RequestServerInfo`(MessageVersion 3) → `ServerInfo` (honor MaxPingTime with a ping task) → `RequestDeviceList`; reader task resolves `Ok`/`Error` futures by Id and tracks `DeviceAdded`/`DeviceRemoved`; drive via `ScalarCmd` (Vibrate actuators, scalar 0.0–1.0); stop via `StopAllDevices`. Supervisor reconnect with backoff 1→2→5→15→30s cap; failures recorded to `last_error`, never propagated.

**LovenseClient** — Lovense Remote "Game Mode" local API: `POST http://{ip}:20010/command`, body `{"command":"Function","action":"Vibrate:{0-20}","timeSec":T,"apiVer":1}` (percent → 0–20). Safety default: `timeSec:2` re-sent every ~900 ms so a crashed host can't leave a toy running; explicit `Vibrate:0` when the level reaches zero. `{"command":"GetToys"}` doubles as connection probe + device list. User enters the IP shown in Lovense Remote's Game Mode screen. **Verify live at implementation: exact `GetToys` response envelope and stop action** (fallback `Vibrate:0`). Shared `httpx.AsyncClient(timeout=1.0)`, sends wrapped in `wait_for` so a slow phone never stalls the loop.

### Keyword engine

- `RuleSet.from_config`: resolves rules against category defaults; compiles ONE case-insensitive word-boundary alternation regex (longest-first).
- `KeywordScanner.feed(token)`: lowercased rolling tail buffer (~256 chars) so keywords split across tokens ("thr"+"obbing") match; holds back the trailing partial word until the next token or `end_of_turn()` disambiguates boundaries; `scan_full_text()` for tests/dev-console parity. (No cooldowns — with last-wins latching, retriggering the same rule is a no-op.)
- `VibeState` (latched — no durations, per user decision): a keyword match sets the current target `{strength 0-100, pattern}`; **last match wins** and holds until changed. Patterns are continuous waveforms evaluated each tick: `constant`, `pulse` (on/off ms from category config), `wave` (smooth sine between low % and full strength). `ramp` as a *pattern* is dropped — it needs an endpoint, i.e. a duration. A rule with `strength: 0` is a **stop-word** (e.g. "finished", "calms", "drift off to sleep") that latches everything off. Output level = pattern(t) × strength × master cap; `clear()` = instant stop.
- **Intensity ramping (slew smoothing)**: the emitted level never jumps — each tick the output moves toward the current target at a rate set by `ramp_ms` (default 500 ms for a full 0→100 swing), so a new match ramps up/down smoothly instead of hitting 0→85 in one tick. This is output smoothing in the loop, not a pattern, and applies to both increases and decreases (including stop-words). **Instant stop bypasses the slew** — STOP button, `/toys stop`, router stop, and `on_turn_stopped(reason="cancelled")` all zero the output immediately.
- **Vibration toggle (output gate)**: a runtime `vibe_on` flag (default on) that gates output without touching the latched state. Toggling **off** zeroes the device immediately (bypasses the slew, like other manual stops) but the scanner keeps feeding and `VibeState` keeps tracking keyword matches; toggling **on** ramps (via slew) up to the *current* latched target — you rejoin the story's present intensity/pattern, not a stale one. Distinct from STOP, which clears the latched state entirely.
- Stop conditions (**no idle watchdog** — user decision; nothing expires on its own, the latch holds indefinitely): stop-word rules (ramped down via slew); `/toys stop` / STOP button / router; `on_turn_stopped(reason="cancelled")` — manual/cancel stops bypass the slew and zero immediately. Normal turn completion keeps the latched state (the story mood persists while you read/type) unless `stop_on_turn_end` is enabled in config. The Lovense `timeSec` keepalive remains the crashed-host backstop. This requires the core `on_turn_stopped` hook to carry a `reason: "completed" | "cancelled"` argument.
- `DeviceManager.run()` 10 Hz loop: applies the slew (output moves toward `pattern(t) × strength × cap` at the `ramp_ms` rate, target forced to 0 while `vibe_on` is false); send on >2 % change or >0.9 s keepalive while level>0; explicit stop sent exactly once on reaching zero; `instant_stop()` out-of-band from widget/command/router, skipping the slew. Task creation follows the wb_image_gen pattern (module-level task set + `asyncio.Lock`, lazily started on first running loop).

### Semantic trigger engine (`semantic_engine.py`)

Fixes keyword imprecision: negation ("he didn't touch her"), memories/dialogue-about, euphemism and paraphrase all classify correctly; a calm verdict acts like a stop-word, so scene wind-downs are caught without keyword curation. Doesn't change pacing — state still evolves at generation speed.

- **Chunking**: buffers `on_stream_token` text at sentence boundaries; on each completed sentence (min 1) classifies a rolling window of the last ~`window_chars` (default 600) of prose. Max one call in flight — if busy, the next completed sentence classifies the *latest* window (natural rate limit, no queue growth). Calls run as fire-and-forget tasks off the token hot path; the hook itself stays sync and cheap.
- **OpenRouter backend**: `POST {base_url}/chat/completions` (default `https://openrouter.ai/api/v1`, overridable for tests/proxies) via the shared `httpx.AsyncClient`; user-configurable `model` — **pick default at implementation: verify candidate models are permissive with explicit prose and reliable at JSON output**; temperature 0; system prompt lists the *user's own categories from rules.json* (shared vocabulary with the keyword engine) and demands strict JSON `{"category": …, "strength": 0-100, "pattern": …}` or `{"no_change": true}`.
- **Hybrid precedence** (`trigger_mode: "hybrid"`, the default): keyword matches apply to `VibeState` instantly (the twitch reaction); a semantic verdict arriving ~1 s later **overrides** whatever keywords set (authoritative-latest-wins); `no_change` leaves state untouched. `"keywords"` and `"semantic"` modes run one producer alone. Slew smoothing makes semantic corrections ramp, not jump.
- **Fail-quiet**: timeout (`timeout_s`, default 4) wrapped in `wait_for`; HTTP errors, refusals, malformed JSON → keep current state, record to `last_error`, back off 30 s after 3 consecutive failures. Trigger path must never raise into the token hook or block streaming.
- **Degradation**: no `api_key` (or backend error state) → hybrid runs keywords-only and `/status` + sidebar show "semantic: no API key" / last error. Backend is a small interface (`classify(window) -> verdict`) so a local zero-shot backend can be added later without touching the engine.
- **Privacy/cost note for README**: in hybrid/semantic mode, story prose is sent to OpenRouter and its upstream provider; the key is stored plaintext in the module's global `config.json`. Typical cost: well under a cent per turn with a small model.

## Module surface

**manifest.json** (validated against registry.py:181-344): id `wb_toy_link`, `consumes: {state:["turn"], module_data:[], module_configs:[], world_data:false}`, `produces` all false; `ui_slots:["slot_sidebar"]`; `commands: {"/toys":"on_command_toys"}` (stop | on | off | test [0-100] | status); `settings_schema`: `enabled` toggle + `master_cap` slider (default 70); `modes:[{id:"toy-studio", screen:"ui/ToyStudio.jsx"}]`.

**Router** at `/api/modules/wb_toy_link/*`: `GET /status` (backend states, devices, current level, active effects, trigger mode + semantic state/last verdict, last errors), `POST /stop`, `POST /toggle` (flips `vibe_on`, returns new state; `/status` includes it), `POST /test`, `GET|PUT /config`, `GET|PUT /rules`, `POST /backends/{name}/connect|disconnect`.

**Frontend** (widget imports limited to whitelist in moduleLoader.js — use plain `fetch` like wb_image_gen/widget.jsx):
- `widget.jsx` (sidebar): polls `/status` @1.5 s — per-backend status dot, live intensity bar, prominent STOP, test buzz, vibe on/off state.
- **Floating toggle** (rendered from `widget.jsx`): a small draggable stop/play button floating over the whole app that flips `vibe_on` via `POST /toggle`. Drag with pointer events; a tap (movement under ~5 px) toggles, a drag repositions; position persisted to `localStorage`. Render via `ReactDOM.createPortal(…, document.body)` so it escapes the sidebar. **Verify at implementation: `react-dom`/portal availability in moduleLoader.js's import whitelist, and that the sidebar widget stays mounted when the sidebar is collapsed** — fallback is `position: fixed` inside the widget tree (beware ancestor `transform` creating a containing block) or, if the widget unmounts, asking for a core overlay slot is out of scope for v1. Button shows current state (playing / muted) and the live level.
- `widget_settings.jsx`: per-story enabled/cap via `{config, onSaveConfig}`.
- `ui/ToyStudio.jsx` (mode screen): Lovense IP + Intiface URL entry, connect buttons, device list; keyword-rule table editor (keywords, category, strength, pattern, enabled) + category-defaults editor; semantic settings (trigger-mode selector, OpenRouter API key + model, status of last classification); saves via `PUT /rules|/config`, hot-applies via RuleSet version bump.

**Config persistence**: app-global (device addresses, rules) as JSON under `services["global_data_dir"]/wb_toy_link/` (`config.json`, `rules.json` — schemas below); per-story toggle/cap in `module_configs`.

```json
// config.json
{ "lovense":  { "enabled": true,  "host": "", "port": 20010, "use_time_sec_safety": true },
  "buttplug": { "enabled": false, "url": "ws://127.0.0.1:12345" },
  "master_cap_global": 100, "ramp_ms": 500, "stop_on_turn_end": false, "tick_hz": 10,
  "trigger_mode": "hybrid",
  "semantic": { "backend": "openrouter", "api_key": "", "model": "",
                "base_url": "https://openrouter.ai/api/v1",
                "window_chars": 600, "timeout_s": 4 } }
// rules.json — null field = inherit from category; no durations (latched strength+pattern)
{ "categories": { "gentle":  {"strength":30, "pattern":"constant"},
                  "intense": {"strength":85, "pattern":"pulse", "pulse_on_ms":400, "pulse_off_ms":250},
                  "calm":    {"strength":0,  "pattern":"constant"} },
  "rules": [ {"id":"r_kiss","keywords":["kiss","kisses","kissed","kissing"],"category":"gentle","enabled":true,
              "strength":null,"pattern":null} ] }
```

Ship a sensible default rules.json (a handful of gentle/moderate/intense example rules the user edits in Toy Studio).

## Repo layout

```
README.md  LICENSE(MIT)  install.sh  install.bat  pytest.ini  requirements-dev.txt
wb_toy_link/
  manifest.json
  backend.py            # thin: sys.path bootstrap (registry.py:141 loads it as a loose spec
                        #   module), set_services + feature detection, stream/lifecycle hooks, get_router()
  toylink/              # __init__.py, keyword_engine.py, semantic_engine.py, patterns.py
                        #   (waveforms + VibeState), device_manager.py, lovense_client.py,
                        #   buttplug_client.py, config_store.py
  widget.jsx  widget_settings.jsx  ui/ToyStudio.jsx
tests/                  # standalone, no WorldboxAI needed
  test_keyword_engine.py  test_semantic_engine.py (httpx.MockTransport fake OpenRouter)
  test_patterns.py  test_buttplug_client.py (fake ws server)
  test_lovense_client.py (httpx.MockTransport)  test_hooks_contract.py (stub services/engine:
                          # feature detection, hook dispatch → scanner/effects, missing-feature disable)
tools/dev_console.py    # REPL: type prose, watch levels / drive real devices sans WorldboxAI
```

## Implementation order

1. Scaffold repo (LICENSE, pytest.ini, requirements-dev.txt, manifest, empty package). Verify: symlink into the scratchpad WorldboxAI checkout → registry loads "Toy Link" with no validation errors.
2. **WorldboxAI core hooks**: `add_repo` FlippRipp/WorldboxAI (push access), branch `claude/module-stream-hooks`; implement `on_stream_token` / `on_turn_start` / `on_turn_stopped` dispatch + `MODULE_API_FEATURES` flag + `docs/MODULES.md` update + contract test. Verify: WorldboxAI's `python -m pytest test_module_contract.py` green; push branch.
3. `keyword_engine.py` + `patterns.py` + tests (pure logic): token-split matches, word boundaries, tail flush on end_of_turn, last-wins latching, stop-words, waveform math, cap, clamps.
4. `config_store.py` (defaults, load/merge/save, RuleSet resolution) + round-trip test.
5. `buttplug_client.py` + fake-server test (handshake order, Id/future matching, ScalarCmd framing, reconnect after drop).
6. `lovense_client.py` + MockTransport test (command bodies, 0–20 mapping, timeSec safety).
7. `device_manager.py` + loop test with fake clients (change-threshold sends, keepalive, zero-once, slew ramping toward new targets, kill-switch immediacy bypassing the slew, `vibe_on` gate: off = immediate zero while latched state keeps updating, on = ramp back to current target).
8. `semantic_engine.py` + MockTransport tests (fake OpenRouter: sentence-boundary cadence, single in-flight/latest-window behavior, JSON verdict parsing incl. `no_change`, hybrid override of keyword-set state, fail-quiet on error/refusal/malformed JSON, backoff after consecutive failures, keywords-only degradation without api_key).
9. `backend.py`: feature detection (require official hooks; disable with clear status if absent), `on_stream_token`/`on_turn_start`/`on_turn_stopped` implementations fanning tokens to keyword scanner + semantic chunker per `trigger_mode`, `/toys` command, router, gating + `test_hooks_contract.py`.
10. Frontend: `widget.jsx` (incl. floating toggle — resolve the portal/whitelist verify item first), `widget_settings.jsx`, `ui/ToyStudio.jsx`.
11. Docs + installers; default rules.json. README states the module requires a WorldboxAI build with the stream-hook module API (the `claude/module-stream-hooks` branch until merged), and documents the semantic mode's privacy/cost note + OpenRouter key setup.

## Verification

- `pip install -r requirements-dev.txt && pytest` — green standalone (no WorldboxAI checkout needed).
- Integration (in this environment): symlink `wb_toy_link` into the WorldboxAI checkout on the `claude/module-stream-hooks` branch, start the backend (`python main.py` after `pip install -r requirements.txt`), confirm module loads and reports "official hooks" mode in `/status`, `curl localhost:8321/api/modules/wb_toy_link/status` works, and a scripted fake-token feed (or a fake ws client against a locally-run Intiface stub from the test suite) shows effects firing while streaming; point `semantic.base_url` at a local fake-OpenRouter stub to verify the hybrid override end-to-end (keyword sets state, semantic verdict corrects it). Also check against an unmodified WorldboxAI checkout: module loads but reports "requires updated WorldboxAI" and drives nothing.
- Real hardware (user, post-merge): Intiface Central simulated device → keyword mid-stream buzzes <300 ms after the word renders; STOP zeroes instantly; floating toggle off silences immediately and toggle on ramps back to the story's current intensity/pattern; with a real OpenRouter key, a false-positive keyword (e.g. a negated phrase) gets corrected by the semantic verdict within a couple of seconds; Lovense Remote Game Mode on-LAN → enter IP in Toy Studio, GetToys populates, test buzz, confirm exact API envelope (flagged verify-live item).

Commit and push: this repo's work to `claude/sex-toy-integration-njsalj`; WorldboxAI core hooks to `claude/module-stream-hooks` in FlippRipp/WorldboxAI (no PRs unless requested).
