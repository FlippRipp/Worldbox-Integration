# Toy Link (`wb_toy_link`) — JEV-Steered Lovense Haptics for WorldboxAI

A drop-in [WorldboxAI](https://github.com/FlippRipp/WorldboxAI) module that drives Lovense toys from story narration in real time, steered by **JEV** and a **procedural anti-numbing pattern engine**.

**18+.** Everything here assumes informed, consenting adult use of your own devices.

## Highlights

- **JEV-steered in ~200 ms**:
  - Uses WorldboxAI's typed JEV decision engine (`engaged`, `wind_down`, `intensity`, `pace`, `pattern`) to read physical momentum as narration streams.
  - Understands negations, past memories, and scene transitions out of the box — no brittle keyword lists or slow chat-LLM refusals.
  - Computes **probability-weighted analog intensity and tempo**, so intensity swells smoothly instead of jumping in coarse steps.
- **Procedural Pattern Engine (`velvet`, `swell`, `heartbeat`, `tease`, `rhythm`, `crest`, `afterglow`)**:
  - **Phase-continuous oscillators** with smooth 350 ms crossfades when patterns change.
  - **Golden-ratio dual-LFO phrasing** (~9 s and ~14.5 s cycles) for organic, non-chaotic variation across waves.
  - **High↔Medium anti-numbing contrast**: as intensity enters the high zone, patterns lift deep troughs to Medium (~46–56%) and cycle briskly (~1.8–3.3 Hz, matched to Lovense motor physics on a 20 Hz loop) between High and Medium so sensation stays sharp instead of numbing out.
  - **Automatic `afterglow` wind-down**: gently fades out over ~9 s when an intimate moment settles.
- **Effortless Lovense setup**:
  - Works with **Lovense Remote** (iOS/Android Game Mode) and **Lovense Connect** (PC `127.0.0.1`).
  - Automatically discovers HTTP (`20010` / `34567`) and local HTTPS (`30010` / `34568`), drives all actuators (`All:0-20`), deduplicates 0–20 steps, and uses a 2 s safety keepalive so a crashed host never leaves a toy running.
- **Clean UI & Zero-Lag Floating Toggle**:
  - **Toy Studio** (Main Menu): 3 clean cards — *Lovense Connection*, *JEV Steering & Feel*, and an interactive *Dynamic Pattern Gallery* with live SVG waveforms and `▶ Feel` buttons.
  - **In-Story Sidebar & Floating Orb**: GPU-composited `translate3d` drag positioning, one-tap Mute/Resume, quick pattern override, and in-story Lovense reconnect.

## Install

```bat
install.bat K:\Python\AI\Worldbox
```
(Or `./install.sh /path/to/WorldboxAI` on Linux/macOS/Termux.) Restart WorldboxAI afterwards.

## Setup

1. Enable **JEV decisions** in WorldboxAI Model Settings (or click **Enable JEV Now** inside Toy Studio).
2. Open **Lovense Remote** on your phone → *Discover* → *Game Mode* (or start **Lovense Connect** on PC).
3. Open **Toy Studio** from WorldboxAI's main menu, enter the Local IP (or click **This PC**), and hit **Connect**.

## Slash Commands

In chat:
```text
/toys status
/toys stop
/toys off | on
/toys test [0-100] [pattern]
/toys pattern <velvet|swell|heartbeat|tease|rhythm|crest|afterglow|auto>
```

## Development & Tests

```sh
python -m pytest
python tools/dev_console.py --lovense 192.168.1.42
```

## License

MIT — see `LICENSE`.
