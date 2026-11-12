#!/usr/bin/env python3
"""Interactive Pattern & Lovense console — audition procedural patterns live.

    python tools/dev_console.py                       # dry run, prints live bars
    python tools/dev_console.py --lovense 192.168.1.9 # drive Lovense Remote

Commands:
    <pattern> [strength] [pace]   e.g. `crest 90 0.8` or `swell 60`
    /list                         list all patterns
    /contrast <subtle|balanced|high>
    /stop, /on, /off, /quit
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wb_toy_link"))

from toylink.device_manager import DeviceManager
from toylink.lovense_client import LovenseClient
from toylink.patterns import PATTERNS, PATTERN_SPECS, VibeState


class PrintClient:
    name = "console"
    enabled = True
    connected = True
    last_error = ""

    def __init__(self):
        self._last = None

    async def set_level(self, frac: float) -> None:
        pct = round(frac * 100)
        if pct != self._last:
            self._last = pct
            bar = "█" * (pct // 4) + "░" * (25 - pct // 4)
            print(f"  [{bar}] {pct:3d}%")

    async def stop(self) -> None:
        if self._last != 0:
            self._last = 0
            print("  [device] ■ STOP (0%)")

    def status(self) -> dict:
        return {"name": self.name}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lovense", metavar="IP", help="Lovense Game Mode IP (or 127.0.0.1)")
    args = parser.parse_args()

    config = {
        "master_cap_global": 100,
        "contrast_mode": "balanced",
        "response_feel": "natural",
        "tick_hz": 20,
    }
    clients = [PrintClient()]
    lovense = None
    if args.lovense:
        lov_cfg = {
            "enabled": True,
            "host": args.lovense,
            "port": 20010,
            "https": False,
            "action_mode": "All",
            "use_time_sec_safety": True,
        }
        lovense = LovenseClient(lambda: lov_cfg)
        found = await lovense.probe(auto_discover=True)
        if found:
            lov_cfg.update(found)
            print(f"Connected to Lovense at {found['host']}:{found['port']} (https={found['https']})")
        else:
            print(f"Warning: Lovense probe failed ({lovense.last_error})")
        clients.append(lovense)

    vibe = VibeState()
    manager = DeviceManager(vibe, clients, lambda: config)
    manager.ensure_running()

    print(f"Patterns: {', '.join(PATTERNS)}")
    print("Type `<pattern> [0-100] [0.0-1.0]`, `/contrast high`, `/stop`, `/on`, `/off`, `/quit`.")
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            line = "/quit"
        line = line.strip()
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/stop":
            await manager.instant_stop()
            continue
        if line in ("/on", "/off"):
            await manager.set_gate(line == "/on")
            continue
        if line == "/list":
            for pid, spec in PATTERN_SPECS.items():
                print(f"  {pid:10s} — {spec['tagline']}")
            continue
        if line.startswith("/contrast"):
            parts = line.split()
            if len(parts) > 1 and parts[1] in ("subtle", "balanced", "high"):
                config["contrast_mode"] = parts[1]
                print(f"  contrast_mode = {parts[1]}")
            continue

        parts = line.split()
        pat = parts[0].lower()
        if pat not in PATTERN_SPECS:
            print(f"  Unknown pattern {pat!r}. Try /list.")
            continue
        strength = float(parts[1]) if len(parts) > 1 else 80.0
        pace = float(parts[2]) if len(parts) > 2 else None
        vibe.set_target(strength, pat, source="console", pace=pace)

    await manager.instant_stop()
    if lovense:
        await lovense.aclose()


if __name__ == "__main__":
    asyncio.run(main())
