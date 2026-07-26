#!/usr/bin/env python3
"""Interactive trigger console — type prose, watch what the toys would do.

No WorldboxAI needed. By default nothing is driven; pass a device flag to
send output to real hardware:

    python tools/dev_console.py                       # dry run, prints levels
    python tools/dev_console.py --lovense 192.168.1.9 # drive Lovense Remote
    python tools/dev_console.py --intiface ws://127.0.0.1:12345

Commands: /stop, /on, /off, /quit — anything else is fed to the keyword
scanner as story prose (the semantic engine needs an LLM and is not part of
this console).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wb_toy_link"))

from toylink.buttplug_client import ButtplugClient
from toylink.config_store import DEFAULT_RULES
from toylink.device_manager import DeviceManager
from toylink.keyword_engine import KeywordScanner, RuleSet
from toylink.lovense_client import LovenseClient
from toylink.patterns import VibeState


class PrintClient:
    name = "console"
    enabled = True
    connected = True
    last_error = ""

    def __init__(self):
        self._last = None

    async def set_level(self, frac):
        pct = round(frac * 100)
        if pct != self._last:
            self._last = pct
            print(f"  [device] level -> {pct}%")

    async def stop(self):
        if self._last != 0:
            self._last = 0
            print("  [device] STOP")

    def status(self):
        return {"name": self.name}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lovense", metavar="IP", help="Lovense Remote Game Mode IP")
    parser.add_argument("--intiface", metavar="URL", help="Intiface websocket URL")
    args = parser.parse_args()

    config = {"master_cap_global": 100, "ramp_ms": 500, "tick_hz": 10}
    clients = [PrintClient()]
    if args.lovense:
        clients.append(LovenseClient(lambda: {
            "enabled": True, "host": args.lovense, "port": 20010,
            "use_time_sec_safety": True}))
    buttplug = None
    if args.intiface:
        buttplug = ButtplugClient(lambda: {"enabled": True, "url": args.intiface})
        clients.append(buttplug)

    vibe = VibeState()
    manager = DeviceManager(vibe, clients, lambda: config)
    ruleset = RuleSet.from_config(DEFAULT_RULES)
    scanner = KeywordScanner(lambda: ruleset)

    manager.ensure_running()
    if buttplug:
        buttplug.start()

    print("Toy Link dev console — type prose; /stop /on /off /quit.")
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            line = "/quit"
        line = line.strip()
        if line == "/quit":
            break
        if line == "/stop":
            await manager.instant_stop()
            continue
        if line in ("/on", "/off"):
            await manager.set_gate(line == "/on")
            continue
        effects = scanner.feed(line + " ")
        effects.extend(scanner.end_of_turn())
        for eff in effects:
            print(f"  match: {eff['rule_id']} -> {eff['category']} "
                  f"{eff['strength']}% {eff['pattern']}")
            vibe.apply_effect(eff, "keyword:")
        if not effects:
            print("  (no triggers)")

    await manager.instant_stop()
    if buttplug:
        await buttplug.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
