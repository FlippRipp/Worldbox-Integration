# Toy Link (`wb_toy_link`) — haptics for WorldboxAI

A drop-in [WorldboxAI](https://github.com/FlippRipp/WorldboxAI) module that
drives sex toys from the story in real time. As the storyteller streams prose,
keyword and AI-based triggers set a latched vibration state — intensity and
pattern — that plays on every connected device until the story (or you) change
it.

**18+.** Everything here assumes informed, consenting adult use of your own
devices.

## What it does

- **Two device backends, usable together:**
  - **Lovense Remote (Game Mode)** — direct local HTTP to the phone running
    Lovense Remote. Preferred: no extra software on the host.
  - **Intiface Central / buttplug.io** — websocket to Intiface (desktop or
    Android), which talks to almost any BLE toy.
- **Triggers while the prose streams** (hybrid mode, the default):
  - *Keywords* match instantly (<300 ms) — exact words/phrases mapped to
    categories (gentle / moderate / intense / peak / calm), each with a
    strength and a waveform pattern (constant, pulse, wave).
  - *The AI classifier* re-judges the scene every sentence or two using a
    small LLM and overrides the keyword guess — it understands negation,
    memories, and paraphrase. A calm verdict winds everything down.
- **Latched state, no timers**: the mood persists while you read and type.
  Intensity changes ramp smoothly (`ramp_ms`); nothing expires on its own.
- **Safety first**: master intensity caps (global × per-story), a prominent
  STOP everywhere, instant halt when you cancel a turn, and a Lovense
  `timeSec` keepalive so even a crashed host stops the toy within ~2 s.
- **A floating on/off button** you can drag anywhere: tap to mute (devices go
  silent, the story keeps tracking), tap again to ramp back to the scene's
  current intensity.
- **Toy Studio** — a main-menu screen with all settings: devices, trigger
  rules (with a paste-a-paragraph tester), classifier model, caps.

## Requirements

- A WorldboxAI build with the **streaming module API**
  (`MODULE_API_FEATURES ⊇ {stream_tokens, turn_lifecycle}` — present in
  current WorldboxAI). On an older build the module loads but stays inactive
  and says so in its status.
- No extra Python packages — the module only uses `httpx` and `websockets`,
  which WorldboxAI already ships. Works on desktop and on Android/Termux
  installs.

## Install

```sh
git clone https://github.com/FlippRipp/Worldbox-Sex-Toy-Integration- toy-link
cd toy-link
./install.sh /path/to/WorldboxAI        # or install.bat on Windows
```

(That just symlinks `wb_toy_link/` into `WorldboxAI/modules/` — `--copy`
copies instead. Restart the backend afterwards.)

## Device setup

**Lovense:** open Lovense Remote on your phone → Discover → Game Mode →
enable. Enter the IP it shows into Toy Studio → Devices and hit *Connect*.
Phone and WorldboxAI host must be on the same network — or be the same phone:
on a Termux install, `127.0.0.1` with Lovense Remote on the same device.

**Intiface:** run Intiface Central (desktop or the Android app), start the
server, connect your toys, then enable the backend in Toy Studio (default
`ws://127.0.0.1:12345`).

## Use

- Sidebar widget: status per backend, live intensity bar, STOP, mute, test.
- Floating button: drag it wherever you like; tap toggles vibration.
- `/toys` in the chat composer: `stop | on | off | test [0-100] | status`.
- Toy Studio (main menu): everything configurable, including the trigger
  rules and their categories. The tester box shows exactly what a paragraph
  would trigger before it ever touches a device.

## The AI classifier and your model choice

In hybrid/AI mode the module sends the last few hundred characters of prose
to an LLM for classification: by default the app's *module fast model* (set in
provider settings), optionally a **custom model just for this module** (Toy
Studio → Classifier model).

- The model must be comfortable with explicit prose. A model that refuses
  just fails quiet — you silently fall back to keywords-only.
- Privacy: prose excerpts go to whatever provider serves the chosen model —
  the same trust domain as the storyteller itself, which already sees the
  whole story. Keywords-only mode makes no LLM calls at all.
- Cost: typically well under a cent per turn with a fast model.

## Development

```sh
pip install -r requirements-dev.txt
python -m pytest                # standalone, no WorldboxAI checkout needed
python tools/dev_console.py     # type prose, watch trigger decisions live
```

The design/plan documents live in `docs/`.

## License

MIT — see `LICENSE`.
