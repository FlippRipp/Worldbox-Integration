"""Lovense Remote / Lovense Connect (Game Mode) local LAN client.

Supports both Lovense Remote on iOS/Android (LAN IP) and Lovense Connect on PC
(127.0.0.1), over either HTTP (ports 20010 / 34567) or local HTTPS with
Lovense's self-signed Game Mode certificate (ports 30010 / 34568).

Smoothness & reliability architecture:
- Buffered ``Pattern`` timelines (``command: "Pattern"``, ``rule: "V:1;F:;S:120#"``,
  up to 50 ``0..20`` steps per request, ``apiVer: 2``): instead of spamming discrete
  ``Function`` HTTP calls every 50 ms (which floods phone Wi-Fi and Lovense's BLE
  command queue), ``DeviceManager`` hands Lovense multi-second lookahead curves
  that Lovense clocks locally on its own 120 ms BLE timer.
- Anti-wraparound ``timeSec`` sizing: buffer length always exceeds ``timeSec``
  (e.g. 45 steps × 120 ms = 5.4 s with ``timeSec: 5``, refreshed every ~3.5 s,
  and padded with trailing zeros on decay/preview tails) so Lovense never loops
  back to step 0 at the end of a fade-out.
- Automatic fallback to ``Function`` if an older bridge rejects ``Pattern``.
"""
from __future__ import annotations

import json
import math
import time
from urllib.parse import urlparse

import httpx

CANDIDATE_PORTS: tuple[tuple[bool, int], ...] = (
    (False, 20010),
    (True, 30010),
    (False, 34567),
    (True, 34568),
)
KEEPALIVE_RESEND_S = 0.85
LIVE_STEP_INTERVAL_S = 0.11
MAX_PATTERN_STEPS = 50


def _percent_to_lovense(frac: float) -> int:
    """Map 0.0..1.0 -> 0..20, never rounding a positive audible level down to 0."""
    frac = min(max(float(frac), 0.0), 1.0)
    if frac <= 0.004:
        return 0
    v = round(frac * 20)
    return 1 if v == 0 else v


def normalize_endpoint(raw_host: str, default_port: int = 20010, default_https: bool = False) -> tuple[bool, str, int]:
    """Accept bare IPs, host:port, or full http(s):// URLs cleanly."""
    text = (raw_host or "").strip()
    if not text:
        return bool(default_https), "", int(default_port or 20010)

    if "://" in text:
        parsed = urlparse(text)
        https = parsed.scheme.lower() == "https"
        host = parsed.hostname or ""
        port = parsed.port or (30010 if https else int(default_port or 20010))
        return https, host, int(port)

    text = text.split("/", 1)[0].strip()
    if ":" in text and text.count(":") == 1:
        host_part, port_part = text.rsplit(":", 1)
        try:
            port = int(port_part)
            https = True if port in (30010, 34568) else bool(default_https)
            return https, host_part.strip(), port
        except ValueError:
            pass

    port = int(default_port or 20010)
    https = True if port in (30010, 34568) else bool(default_https)
    return https, text, port


class LovenseClient:
    name = "lovense"

    def __init__(
        self,
        get_config,
        transport: httpx.AsyncBaseTransport | None = None,
        clock=time.monotonic,
    ):
        self._get_config = get_config
        self._transport = transport
        self._clock = clock
        self._client: httpx.AsyncClient | None = None
        self.connected: bool = False
        self.toys: list[dict] = []
        self.last_error: str = ""
        self.resolved_url: str = ""
        self.last_rtt_s: float = 0.025

        self._pattern_unsupported: bool = False
        self._hw_mode: str = "idle"  # "idle" | "function" | "pattern"
        self._last_step: int = 0
        self._last_send_at: float = 0.0
        self._sending: bool = False
        self._pending_op: tuple | None = None

    def _cfg(self) -> dict:
        return self._get_config() or {}

    @property
    def supports_pattern_stream(self) -> bool:
        return (not self._pattern_unsupported) and bool(self._cfg().get("use_pattern_api", True))

    @property
    def prefers_live_story_stream(self) -> bool:
        mode = str(self._cfg().get("story_drive_mode", "stream") or "stream").strip().lower()
        return mode != "pattern"

    @property
    def enabled(self) -> bool:
        cfg = self._cfg()
        _, host, _ = normalize_endpoint(cfg.get("host", ""), cfg.get("port", 20010), cfg.get("https", False))
        return bool(cfg.get("enabled", True)) and bool(host)

    def _endpoint(self) -> tuple[bool, str, int]:
        cfg = self._cfg()
        return normalize_endpoint(
            cfg.get("host", ""),
            cfg.get("port", 20010),
            cfg.get("https", False),
        )

    @staticmethod
    def _build_url(https: bool, host: str, port: int) -> str:
        scheme = "https" if https else "http"
        return f"{scheme}://{host}:{port}/command"

    def _url(self) -> str:
        https, host, port = self._endpoint()
        return self._build_url(https, host, port)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict = {"timeout": 1.5}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                # Lovense Game Mode HTTPS uses a local self-signed certificate.
                kwargs["verify"] = False
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def _post_url(self, url: str, body: dict) -> dict | None:
        t0 = self._clock()
        try:
            resp = await self._http().post(url, json=body)
            resp.raise_for_status()
            elapsed = max(0.0, self._clock() - t0)
            self.last_rtt_s = 0.7 * self.last_rtt_s + 0.3 * min(0.5, elapsed)
            data = resp.json()
            if isinstance(data, dict) and "code" in data:
                try:
                    code = int(data["code"])
                except (TypeError, ValueError):
                    code = 0
                if code != 200:
                    msg = data.get("type") or data.get("message") or f"code {data['code']}"
                    self.connected = False
                    self.last_error = f"Lovense error: {msg}"
                    return None
            self.connected = True
            self.last_error = ""
            self.resolved_url = url
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    async def _post(self, body: dict) -> dict | None:
        return await self._post_url(self._url(), body)

    async def probe(self, auto_discover: bool = True) -> dict | None:
        """Query GetToys; if configured port fails, try standard Lovense ports."""
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            self.connected = False
            return None

        https, host, port = self._endpoint()
        candidates: list[tuple[bool, str, int]] = []
        if host:
            candidates.append((https, host, port))
            if auto_discover and self._transport is None:
                for cand_https, cand_port in CANDIDATE_PORTS:
                    cand = (cand_https, host, cand_port)
                    if cand not in candidates:
                        candidates.append(cand)
        elif auto_discover:
            for cand_https, cand_port in CANDIDATE_PORTS:
                candidates.append((cand_https, "127.0.0.1", cand_port))

        if not candidates:
            self.connected = False
            self.toys = []
            return None

        first_err = ""
        for cand_https, cand_host, cand_port in candidates:
            url = self._build_url(cand_https, cand_host, cand_port)
            data = await self._post_url(url, {"command": "GetToys"})
            if data is not None:
                self.toys = self._parse_toys(data)
                self._pattern_unsupported = False
                return {"host": cand_host, "port": cand_port, "https": cand_https}
            if not first_err:
                first_err = self.last_error

        self.toys = []
        self.connected = False
        if first_err:
            self.last_error = first_err
        return None

    @staticmethod
    def _parse_toys(data: dict) -> list[dict]:
        toys = data.get("data", data)
        if isinstance(toys, dict):
            toys = toys.get("toys", toys)
        if isinstance(toys, str):
            try:
                toys = json.loads(toys)
            except ValueError:
                return [{"id": "toy", "name": toys}]
        raw_list: list[dict] = []
        if isinstance(toys, dict):
            for k, v in toys.items():
                if isinstance(v, dict):
                    raw_list.append({**v, "id": v.get("id", k)})
                else:
                    raw_list.append({"id": k, "name": str(v)})
        elif isinstance(toys, list):
            for item in toys:
                if isinstance(item, dict):
                    raw_list.append(dict(item))
        normalized = []
        for t in raw_list:
            if not any(k in t for k in ("id", "name", "nickName", "battery", "status")):
                continue
            name = t.get("nickName") or t.get("name") or t.get("id") or "Lovense Toy"
            normalized.append({
                **t,
                "display_name": str(name).capitalize() if str(name).islower() else str(name),
            })
        return normalized

    def _action_prefix(self) -> str:
        mode = str(self._cfg().get("action_mode", "All") or "All").strip()
        return "Vibrate" if mode.lower() == "vibrate" else "All"

    def _pattern_features(self) -> str:
        return "v" if self._action_prefix() == "Vibrate" else ""

    async def send_pattern(
        self,
        levels: list[float],
        *,
        interval_ms: int = 135,
        loop: bool = True,
        active_seconds: float | None = None,
    ) -> None:
        """Send a multi-step 0..20 ``Pattern`` timeline to Lovense.

        When ``loop=True``, sends ``timeSec: 0`` and ``stopPrevious: 0`` so
        Lovense loops the 50-step phrase natively in hardware with zero network
        refreshes and zero ~4 s reset pauses.
        """
        if not self.enabled:
            return
        if not self.supports_pattern_stream:
            if levels:
                await self.set_level(levels[0])
            return

        interval_ms = max(100, int(interval_ms))
        steps = [_percent_to_lovense(x) for x in levels[:MAX_PATTERN_STEPS]]
        if not steps or all(s == 0 for s in steps):
            await self.stop()
            return

        if loop:
            time_sec = 0
        else:
            step_s = interval_ms / 1000.0
            last_nonzero_idx = max(i for i, s in enumerate(steps) if s > 0)
            nonzero_span_s = (last_nonzero_idx + 1) * step_s
            if active_seconds is not None:
                desired_s = min(float(active_seconds), len(steps) * step_s)
            else:
                desired_s = min(nonzero_span_s, len(steps) * step_s - 0.15)
            time_sec = max(1, int(math.ceil(max(0.8, desired_s))))
            min_steps = min(MAX_PATTERN_STEPS, int(math.ceil((time_sec + 0.15) / step_s)))
            if len(steps) < min_steps:
                pad_val = 0 if steps[-1] == 0 else steps[-1]
                steps.extend([pad_val] * (min_steps - len(steps)))

        payload = {
            "command": "Pattern",
            "rule": f"V:1;F:{self._pattern_features()};S:{interval_ms}#",
            "strength": ";".join(str(s) for s in steps),
            "timeSec": time_sec,
            "stopPrevious": 0,
            "apiVer": 2,
        }
        await self._enqueue_or_send(("pattern", payload, steps[0], levels[0]))

    async def send_live_level(self, frac: float, *, allow_zero_valley: bool = False) -> None:
        """Gapless real-time motor streaming (`stopPrevious: 0, timeSec: 0`).

        Unlike `command: "Pattern"` (which resets Lovense Remote's BLE timer on
        every pattern change) and legacy `set_level` (which omitted `stopPrevious`
        and resent every 0.85s), `send_live_level` updates motor speed directly in
        place only when the 0..20 step changes, paced cleanly to BLE's 110ms slot.
        """
        if not self.enabled:
            return
        step = _percent_to_lovense(frac)
        if step == 0 and not allow_zero_valley:
            if self._last_step > 0 or self._hw_mode != "idle":
                await self.stop()
            return

        now = self._clock()
        if step == self._last_step and self._hw_mode == "function" and self.connected:
            return

        # Pace positive step transitions to BLE's 110ms slot; DeviceManager's 20Hz
        # ticker automatically delivers the newest step on the next eligible tick.
        if (
            step > 0
            and self._last_step > 0
            and self._hw_mode == "function"
            and (now - self._last_send_at) < LIVE_STEP_INTERVAL_S
            and self.connected
        ):
            return

        # If transitioning directly out of an indefinite hardware `Pattern` preview
        # without an intermediate Stop, clear the hardware loop once on the first packet.
        stop_prev = 1 if self._hw_mode == "pattern" else 0
        prefix = self._action_prefix()
        payload = {
            "command": "Function",
            "action": f"{prefix}:{step}",
            "timeSec": 0,
            "stopPrevious": stop_prev,
            "apiVer": 1,
        }
        await self._enqueue_or_send(("live_function", payload, step))

    async def set_level(self, frac: float) -> None:
        if not self.enabled:
            return
        step = _percent_to_lovense(frac)
        if step == 0:
            if self._last_step > 0 or self._hw_mode != "idle":
                await self.stop()
            return
        now = self._clock()
        if step == self._last_step and (now - self._last_send_at) < KEEPALIVE_RESEND_S and self.connected:
            return
        cfg = self._cfg()
        use_safety = cfg.get("use_time_sec_safety", True)
        prefix = self._action_prefix()
        payload = {
            "command": "Function",
            "action": f"{prefix}:{step}",
            "timeSec": 2 if use_safety else 0,
            "apiVer": 1,
        }
        await self._enqueue_or_send(("function", payload, step))

    async def stop(self) -> None:
        self._pending_op = None
        self._last_step = 0
        self._last_send_at = 0.0
        self._hw_mode = "idle"
        if not self.enabled:
            return
        payload = {
            "command": "Function",
            "action": "Stop",
            "timeSec": 0,
            "apiVer": 1,
        }
        if self._sending:
            # Preempt any queued pattern so Stop always wins as soon as in-flight finishes
            self._pending_op = ("stop", payload)
            return
        await self._enqueue_or_send(("stop", payload))

    async def _enqueue_or_send(self, op: tuple) -> None:
        if self._sending:
            self._pending_op = op
            return

        self._sending = True
        try:
            cur: tuple | None = op
            while cur is not None:
                self._pending_op = None
                kind = cur[0]
                if kind == "stop":
                    await self._post(cur[1])
                    self._hw_mode = "idle"
                    self._last_step = 0
                    self._last_send_at = 0.0
                elif kind in ("function", "live_function"):
                    _, payload, step = cur
                    ok = await self._post(payload)
                    if ok is not None:
                        self._hw_mode = "function"
                        self._last_step = step
                        self._last_send_at = self._clock()
                elif kind == "pattern":
                    _, payload, first_step, first_frac = cur
                    was_connected = self.connected
                    ok = await self._post(payload)
                    if ok is not None:
                        self._hw_mode = "pattern"
                        self._last_step = first_step
                        self._last_send_at = self._clock()
                    elif was_connected and self.last_error.startswith("Lovense error:"):
                        # Bridge connected via GetToys but rejected Pattern -> fallback
                        self._pattern_unsupported = True
                        prefix = self._action_prefix()
                        s = _percent_to_lovense(first_frac)
                        fb = await self._post({
                            "command": "Function",
                            "action": f"{prefix}:{s}" if s > 0 else "Stop",
                            "timeSec": 0,
                            "stopPrevious": 0,
                            "apiVer": 1,
                        })
                        if fb is not None:
                            self._hw_mode = "function" if s > 0 else "idle"
                            self._last_step = s
                            self._last_send_at = self._clock()
                cur = self._pending_op
        finally:
            self._sending = False

    def status(self) -> dict:
        https, host, port = self._endpoint()
        return {
            "name": self.name,
            "enabled": self.enabled,
            "configured_host": host,
            "port": port,
            "https": https,
            "connected": self.connected,
            "mode": "pattern" if self.supports_pattern_stream else "function",
            "story_drive_mode": "stream" if self.prefers_live_story_stream else "pattern",
            "rtt_ms": round(self.last_rtt_s * 1000.0, 1),
            "devices": list(self.toys),
            "last_error": self.last_error,
        }

    async def aclose(self) -> None:
        self._pending_op = None
        self._hw_mode = "idle"
        self._last_step = 0
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
