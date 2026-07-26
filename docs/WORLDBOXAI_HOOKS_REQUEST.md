# Implementation request: module stream + turn-lifecycle hooks for WorldboxAI

Requested by the `wb_toy_link` haptics module (this repo). Self-contained spec —
implementable without reading this repo's plan. Estimated scope: ~30–50 lines in
core plus a contract test.

## Why

Modules currently have no way to observe the storyteller's prose as it streams,
and no way to know when a turn starts, completes, or is cancelled. `wb_toy_link`
needs all three (real-time trigger scanning; instant device stop on turn cancel),
but the hooks are deliberately generic — TTS, sound effects, or live translation
modules would use the same API. The haptics module will **not** monkey-patch
`emit_token` as a fallback; it requires these official hooks and disables itself
(with a clear status message) on a WorldboxAI build that lacks them.

## API contract

Three new optional module hooks, discovered on module backends the same way as
existing hooks:

### 1. `on_stream_token(token: str, state, sdk) -> None` — **sync, not async**

Called once per token the storyteller streams. This is the one deliberate
exception to the async-hook convention: the token path is hot, and awaiting per
token would stall streaming. Core should reject (log + skip) a coroutine
function registered under this name rather than awaiting it per token.

- **Dispatch point**: the storyteller node looks up `streaming_callback=self.sdk.ui.emit_token`
  fresh each turn (`graph.py:824`). Wrap the callback at that construction site:
  emit to the UI as today, then fan out to subscribers.
- **Subscriber collection**: once at turn start, not per token — collect active
  modules exposing the hook, respecting `__active_modules__` gating (same
  filtering as `_run_modules_in_levels`).
- **`state`**: the same filtered per-module view already built for other hooks,
  built **once at turn start** and reused for every token — never rebuilt per token.
- **Error isolation**: exceptions from a subscriber are swallowed and must never
  reach the streaming path or other subscribers. Log at most once per turn per
  module (not per token).

### 2. `on_turn_start(state, sdk)` — async ok

Dispatched from the turn entry point, before the first token of the turn.

### 3. `on_turn_stopped(state, sdk, reason: str)` — async ok

Dispatched when a turn ends, with:

- `reason="completed"` on normal turn completion;
- `reason="cancelled"` from the turn-cancel handler (`server.py:2679-2680`).

**Guarantee: fires exactly once for every turn that fired `on_turn_start`,
including cancelled turns.** The cancelled case is the haptics kill switch — if
it can be skipped on some path, a device keeps vibrating after the user hits
Stop.

### Ordering guarantees

Per turn, per module: `on_turn_start` → zero or more `on_stream_token` (in
stream order) → `on_turn_stopped`. No tokens after `on_turn_stopped`.

### 4. Feature detection

Class attribute on `EngineGraph`:

```python
MODULE_API_FEATURES = {"stream_tokens", "turn_lifecycle"}
```

Modules probe it via `getattr(services["engine"], "MODULE_API_FEATURES", set())`
in `set_services` — `getattr` with a default, so the probe is safe on older
builds. Add a feature name per capability so future additions are detectable
independently.

## Documentation

Document the three hooks + `MODULE_API_FEATURES` in `docs/MODULES.md`: signatures,
the sync requirement on `on_stream_token`, the ordering and exactly-once
guarantees, and the error-isolation behavior.

## Acceptance tests

Extend `test_module_contract.py` with a stub module (fake streaming callback, no
LLM):

1. `on_turn_start` → each streamed token to `on_stream_token` in order →
   `on_turn_stopped(reason="completed")`, correct args throughout.
2. Cancelled turn fires `on_turn_stopped(reason="cancelled")` exactly once.
3. A subscriber that raises on every token does not break streaming, other
   subscribers, or turn completion; the log records one line, not one per token.
4. A module not in `__active_modules__` receives no dispatches.
5. An `async def on_stream_token` is skipped with a logged warning, not awaited.
6. `EngineGraph.MODULE_API_FEATURES` contains `"stream_tokens"` and
   `"turn_lifecycle"`.

Suite must stay green: `python -m pytest test_module_contract.py`.

## Reference facts (from prior source inspection)

- One `WorldBoxSDK` per `EngineGraph` (`graph.py:43`); engine created once at
  import (`server.py:96`).
- Storyteller passes `streaming_callback=self.sdk.ui.emit_token`, looked up
  fresh each turn (`graph.py:824`).
- Turn-cancel handler: `server.py:2679-2680`.
- Module backends are loaded as loose spec modules by `registry.py:141`.

Line numbers are from the inspection that produced this spec — treat as
pointers, not gospel, if the files have moved since.

## How wb_toy_link consumes this (context, not requirements)

`on_stream_token` feeds a keyword scanner and a semantic-classifier chunker
(both O(token), non-blocking); `on_turn_start` resets per-turn scanner state;
`on_turn_stopped` flushes the scanner tail, and on `reason="cancelled"`
immediately zeroes all connected devices.
