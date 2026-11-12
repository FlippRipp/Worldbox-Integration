"""Lovense Remote / Lovense Connect (Game Mode) local LAN client.

Supports both Lovense Remote on iOS/Android (LAN IP) and Lovense Connect on PC
(127.0.0.1), over either HTTP (ports 20010 / 34567) or local HTTPS with
Lovense's self-signed Game Mode certificate (ports 30010 / 34568).

Key reliability details:
- Step deduplication & coalescing: the 20 Hz loop calls ``set_level`` every
  50 ms, but Lovense quantizes strength to 0..20. Identical integer steps within
  the 0.85 s keepalive window are skipped, and if phone Wi-Fi stalls briefly,
  intermediate steps coalesce so only the freshest level is sent next.
- Multi-motor support: defaults to ``All:<0-20>`` and ``Stop`` (apiVer 1) so
  Lush, Hush, Domi, Nora, Max, Edge, Solace, Tenera, and Ferri all respond.
- Strict API envelope check: validates Lovense's ``code == 200`` field instead
  of treating any HTTP 200 error payload as connected.
"""
from __future__ import annotations

import asyncio
import json
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


def _percent_to_lovense(frac: float) -> int:
    """Map 0.0..1.0 -> 0..20, never rounding a nonzero level down to 0."""
    frac = min(max(float(frac), 0.0), 1.0)
    v = round(frac * 20)
    if frac > 0.0 and v == 0:
        v = 1
    return v


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

    # Strip accidental trailing paths like "192.168.1.5:20010/command"
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

        # Step deduplication & Wi-Fi backpressure coalescing
        self._last_step: int = 0
        self._last_send_at: float = 0.0
        self._sending: bool = False
        self._pending_frac: float | None = None

    def _cfg(self) -> dict:
        return self._get_config() or {}

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
            kwargs: dict = {"timeout": 1.2}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                # Lovense Game Mode HTTPS uses a local self-signed certificate.
                kwargs["verify"] = False
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def _post_url(self, url: str, body: dict) -> dict | None:
        try:
            resp = await self._http().post(url, json=body)
            resp.raise_for_status()
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
        """Query GetToys; if configured port fails, try standard Lovense ports.

        Returns ``{"host": ..., "port": ..., "https": ...}`` on success so the
        caller can persist auto-discovered settings, or ``None`` on failure.
        """
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
            # Blank host: try local Lovense Connect on 127.0.0.1
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
            # Filter out non-toy metadata keys if data had no "toys" wrapper
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

    async def set_level(self, frac: float) -> None:
        if not self.enabled:
            return
        step = _percent_to_lovense(frac)
        now = self._clock()
        if step == self._last_step and (now - self._last_send_at) < KEEPALIVE_RESEND_S and self.connected:
            return

        if self._sending:
            # Coalesce while a LAN request is in flight so packets never backlog
            self._pending_frac = frac
            return

        self._sending = True
        try:
            current_frac: float | None = frac
            while current_frac is not None:
                self._pending_frac = None
                s = _percent_to_lovense(current_frac)
                cfg = self._cfg()
                use_safety = cfg.get("use_time_sec_safety", True)
                prefix = self._action_prefix()
                ok = await self._post({
                    "command": "Function",
                    "action": f"{prefix}:{s}",
                    "timeSec": 2 if use_safety else 0,
                    "apiVer": 1,
                })
                if ok is not None:
                    self._last_step = s
                    self._last_send_at = self._clock()
                current_frac = self._pending_frac
        finally:
            self._sending = False

    async def stop(self) -> None:
        self._pending_frac = None
        self._last_step = 0
        self._last_send_at = 0.0
        if not self.enabled:
            return
        await self._post({
            "command": "Function",
            "action": "Stop",
            "timeSec": 0,
            "apiVer": 1,
        })

    def status(self) -> dict:
        https, host, port = self._endpoint()
        return {
            "name": self.name,
            "enabled": self.enabled,
            "configured_host": host,
            "port": port,
            "https": https,
            "connected": self.connected,
            "devices": list(self.toys),
            "last_error": self.last_error,
        }

    async def aclose(self) -> None:
        self._pending_frac = None
        self._last_step = 0
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self.connected = False
