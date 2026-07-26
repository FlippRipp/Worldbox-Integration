"""buttplug.io protocol v3 client over websocket (Intiface Central).

Message framing is JSON arrays of ``{"Type": {...fields, "Id": n}}``. The
session flow: ``RequestServerInfo`` (MessageVersion 3) → ``ServerInfo`` (honor
``MaxPingTime`` with a ping task) → ``RequestDeviceList`` → track
``DeviceAdded``/``DeviceRemoved`` → drive Vibrate actuators with ``ScalarCmd``
(scalar 0.0–1.0), stop with ``StopAllDevices``.

A reader task resolves request futures by Id; ``run()`` is a supervisor that
reconnects with backoff (1→2→5→15→30 s cap). Failures land in ``last_error``
and never propagate to callers.
"""
import asyncio
import json

CLIENT_NAME = "WorldBox ToyLink"
MESSAGE_VERSION = 3
REQUEST_TIMEOUT_S = 5.0
DEFAULT_BACKOFF = (1, 2, 5, 15, 30)


class ButtplugClient:
    name = "buttplug"

    def __init__(self, get_config, connect=None, backoff=DEFAULT_BACKOFF):
        self._get_config = get_config      # -> the config's "buttplug" dict
        self._connect = connect            # injectable for tests
        self._backoff = tuple(backoff)
        self._ws = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self.connected = False
        self.devices: dict[int, dict] = {}
        self.last_error = ""

    def _cfg(self) -> dict:
        return self._get_config() or {}

    @property
    def enabled(self) -> bool:
        return bool(self._cfg().get("enabled")) and bool(self._cfg().get("url"))

    # ----------------------------------------------------------------- session

    def start(self) -> None:
        """Idempotently start the reconnect supervisor."""
        if self._supervisor is None or self._supervisor.done():
            self._stopping = False
            self._supervisor = asyncio.get_running_loop().create_task(self._run())

    async def disconnect(self) -> None:
        self._stopping = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._supervisor is not None:
            self._supervisor.cancel()
            try:
                await self._supervisor
            except (asyncio.CancelledError, Exception):
                pass
            self._supervisor = None
        self._teardown()

    async def _run(self) -> None:
        attempt = 0
        while not self._stopping:
            if not self.enabled:
                await asyncio.sleep(1.0)
                continue
            try:
                await self._session()
                attempt = 0  # a completed session (server closed) resets backoff
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            self._teardown()
            if self._stopping:
                break
            delay = self._backoff[min(attempt, len(self._backoff) - 1)]
            attempt += 1
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        connect = self._connect
        if connect is None:
            import websockets
            connect = websockets.connect
        async with connect(self._cfg().get("url")) as ws:
            self._ws = ws
            reader = asyncio.get_running_loop().create_task(self._reader(ws))
            ping_task = None
            try:
                kind, info = await self._request(
                    "RequestServerInfo",
                    {"ClientName": CLIENT_NAME, "MessageVersion": MESSAGE_VERSION},
                )
                if kind != "ServerInfo":
                    raise RuntimeError(f"handshake rejected: {kind} {info}")
                max_ping = int(info.get("MaxPingTime", 0) or 0)
                if max_ping > 0:
                    ping_task = asyncio.get_running_loop().create_task(
                        self._pinger(max_ping))
                kind, dev_list = await self._request("RequestDeviceList", {})
                if kind == "DeviceList":
                    self._set_devices(dev_list.get("Devices", []))
                self.connected = True
                self.last_error = ""
                await reader  # runs until the connection drops
            finally:
                if ping_task is not None:
                    ping_task.cancel()
                if not reader.done():
                    reader.cancel()

    async def _pinger(self, max_ping_ms: int) -> None:
        interval = max(0.05, max_ping_ms / 2000.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._request("Ping", {})
            except Exception:
                return  # the reader/session teardown handles the fallout

    async def _reader(self, ws) -> None:
        async for raw in ws:
            try:
                messages = json.loads(raw)
            except ValueError:
                continue
            for msg in messages:
                for kind, body in msg.items():
                    self._dispatch(kind, body)

    def _dispatch(self, kind: str, body: dict) -> None:
        if kind in ("Ok", "Error", "ServerInfo", "DeviceList"):
            fut = self._pending.pop(body.get("Id"), None)
            if fut is not None and not fut.done():
                if kind == "Error":
                    fut.set_exception(
                        RuntimeError(body.get("ErrorMessage", "buttplug error")))
                else:
                    fut.set_result((kind, body))
        elif kind == "DeviceAdded":
            self._set_devices([body], merge=True)
        elif kind == "DeviceRemoved":
            self.devices.pop(body.get("DeviceIndex"), None)

    def _set_devices(self, entries: list, merge: bool = False) -> None:
        if not merge:
            self.devices = {}
        for dev in entries:
            scalar_attrs = (dev.get("DeviceMessages") or {}).get("ScalarCmd") or []
            vibrate = [i for i, attr in enumerate(scalar_attrs)
                       if attr.get("ActuatorType") == "Vibrate"]
            self.devices[dev.get("DeviceIndex")] = {
                "index": dev.get("DeviceIndex"),
                "name": dev.get("DeviceName", "?"),
                "vibrate_actuators": vibrate,
            }

    async def _request(self, kind: str, payload: dict) -> tuple[str, dict]:
        if self._ws is None:
            raise RuntimeError("not connected")
        self._msg_id += 1
        msg_id = self._msg_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send(json.dumps([{kind: {**payload, "Id": msg_id}}]))
            return await asyncio.wait_for(fut, REQUEST_TIMEOUT_S)
        finally:
            self._pending.pop(msg_id, None)

    def _teardown(self) -> None:
        self.connected = False
        self.devices = {}
        self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    # ----------------------------------------------------------------- control

    async def set_level(self, frac: float) -> None:
        if not self.connected:
            return
        frac = min(max(frac, 0.0), 1.0)
        for dev in list(self.devices.values()):
            if not dev["vibrate_actuators"]:
                continue
            scalars = [{"Index": i, "Scalar": frac, "ActuatorType": "Vibrate"}
                       for i in dev["vibrate_actuators"]]
            try:
                await self._request("ScalarCmd", {
                    "DeviceIndex": dev["index"], "Scalars": scalars})
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"

    async def stop(self) -> None:
        if not self.connected:
            return
        try:
            await self._request("StopAllDevices", {})
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"

    def status(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "connected": self.connected,
            "devices": [dict(d) for d in self.devices.values()],
            "last_error": self.last_error,
        }
