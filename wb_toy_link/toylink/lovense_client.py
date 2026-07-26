"""Lovense Remote "Game Mode" local API client.

The phone runs Lovense Remote with Game Mode enabled; it serves a small HTTP
API on the LAN (default port 20010). Commands are POSTs to ``/command``:

    {"command": "Function", "action": "Vibrate:0-20", "timeSec": T, "apiVer": 1}

Safety default: every level send uses ``timeSec: 2`` and the 10 Hz loop
re-sends within ~0.9 s, so a crashed host stops the toy within ~2 s instead of
leaving it running. VERIFY-LIVE: the exact GetToys response envelope and stop
action are confirmed against real hardware during integration; parsing is
defensive until then.

Failures never propagate — they land in ``last_error`` and flip ``connected``.
"""
import json

import httpx


def _percent_to_lovense(frac: float) -> int:
    """0..1 → 0..20, never rounding a nonzero level down to 'off'."""
    frac = min(max(frac, 0.0), 1.0)
    v = round(frac * 20)
    if frac > 0 and v == 0:
        v = 1
    return v


class LovenseClient:
    name = "lovense"

    def __init__(self, get_config, transport: httpx.AsyncBaseTransport | None = None):
        self._get_config = get_config      # -> the config's "lovense" dict
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.connected = False
        self.toys: list = []
        self.last_error = ""

    def _cfg(self) -> dict:
        return self._get_config() or {}

    @property
    def enabled(self) -> bool:
        cfg = self._cfg()
        return bool(cfg.get("enabled")) and bool(cfg.get("host"))

    def _url(self) -> str:
        cfg = self._cfg()
        return f"http://{cfg.get('host')}:{cfg.get('port', 20010)}/command"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=1.0, transport=self._transport)
        return self._client

    async def _post(self, body: dict) -> dict | None:
        try:
            resp = await self._http().post(self._url(), json=body)
            resp.raise_for_status()
            data = resp.json()
            self.connected = True
            self.last_error = ""
            return data
        except Exception as e:  # a slow/absent phone must never stall the loop
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    async def probe(self) -> bool:
        """GetToys doubles as connection check and device list."""
        if not self.enabled:
            return False
        data = await self._post({"command": "GetToys"})
        if data is None:
            self.toys = []
            return False
        self.toys = self._parse_toys(data)
        return True

    @staticmethod
    def _parse_toys(data: dict) -> list:
        # Observed shape: {"code":200,"data":{"toys":"<json string>", ...}} —
        # but treat every layer as optional until verified live.
        toys = data.get("data", data)
        if isinstance(toys, dict):
            toys = toys.get("toys", toys)
        if isinstance(toys, str):
            try:
                toys = json.loads(toys)
            except ValueError:
                return [toys]
        if isinstance(toys, dict):
            return [dict(v, id=k) if isinstance(v, dict) else {"id": k, "info": v}
                    for k, v in toys.items()]
        return list(toys) if isinstance(toys, list) else []

    async def set_level(self, frac: float) -> None:
        if not self.enabled:
            return
        cfg = self._cfg()
        use_safety = cfg.get("use_time_sec_safety", True)
        await self._post({
            "command": "Function",
            "action": f"Vibrate:{_percent_to_lovense(frac)}",
            "timeSec": 2 if use_safety else 0,
            "apiVer": 1,
        })

    async def stop(self) -> None:
        if not self.enabled:
            return
        await self._post({
            "command": "Function",
            "action": "Vibrate:0",
            "timeSec": 0,
            "apiVer": 1,
        })

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "devices": self.toys,
            "last_error": self.last_error,
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
