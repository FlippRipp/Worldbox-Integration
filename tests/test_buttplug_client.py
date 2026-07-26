import asyncio
import json

import pytest
import websockets

from toylink.buttplug_client import ButtplugClient

DEVICES = [
    {"DeviceIndex": 0, "DeviceName": "Test Vibe",
     "DeviceMessages": {"ScalarCmd": [
         {"StepCount": 20, "ActuatorType": "Vibrate"},
         {"StepCount": 10, "ActuatorType": "Oscillate"}]}},
    {"DeviceIndex": 3, "DeviceName": "Dual Motor",
     "DeviceMessages": {"ScalarCmd": [
         {"StepCount": 20, "ActuatorType": "Vibrate"},
         {"StepCount": 20, "ActuatorType": "Vibrate"}]}},
    {"DeviceIndex": 5, "DeviceName": "No Vibes",
     "DeviceMessages": {"LinearCmd": [{"StepCount": 100}]}},
]


class FakeIntiface:
    def __init__(self, max_ping=0, devices=DEVICES, drop_first_session=False,
                 error_on_scalar=False):
        self.max_ping = max_ping
        self.devices = devices
        self.drop_first_session = drop_first_session
        self.error_on_scalar = error_on_scalar
        self.connections = 0
        self.scalar_cmds = []
        self.stops = 0
        self.pings = 0
        self.server = None

    async def start(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def handler(self, ws):
        self.connections += 1
        first = self.connections == 1
        try:
            async for raw in ws:
                for msg in json.loads(raw):
                    for kind, body in msg.items():
                        await self.respond(ws, kind, body, first)
        except websockets.ConnectionClosed:
            pass

    async def respond(self, ws, kind, body, first_session):
        msg_id = body.get("Id")
        if kind == "RequestServerInfo":
            await ws.send(json.dumps([{"ServerInfo": {
                "Id": msg_id, "ServerName": "fake", "MessageVersion": 3,
                "MaxPingTime": self.max_ping}}]))
        elif kind == "RequestDeviceList":
            await ws.send(json.dumps([{"DeviceList": {
                "Id": msg_id, "Devices": self.devices}}]))
            if self.drop_first_session and first_session:
                await ws.close()
        elif kind == "ScalarCmd":
            self.scalar_cmds.append(body)
            if self.error_on_scalar:
                await ws.send(json.dumps([{"Error": {
                    "Id": msg_id, "ErrorMessage": "nope", "ErrorCode": 3}}]))
            else:
                await ws.send(json.dumps([{"Ok": {"Id": msg_id}}]))
        elif kind == "Ping":
            self.pings += 1
            await ws.send(json.dumps([{"Ok": {"Id": msg_id}}]))
        elif kind == "StopAllDevices":
            self.stops += 1
            await ws.send(json.dumps([{"Ok": {"Id": msg_id}}]))


async def wait_until(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def make_connected(server, **client_kwargs):
    url = await server.start()
    cfg = {"enabled": True, "url": url}
    client = ButtplugClient(lambda: cfg, backoff=(0.05,), **client_kwargs)
    client.start()
    await wait_until(lambda: client.connected)
    return client


async def test_handshake_and_device_tracking():
    server = FakeIntiface()
    client = await make_connected(server)
    try:
        assert set(client.devices) == {0, 3, 5}
        assert client.devices[0]["vibrate_actuators"] == [0]
        assert client.devices[3]["vibrate_actuators"] == [0, 1]
        assert client.devices[5]["vibrate_actuators"] == []
    finally:
        await client.disconnect()
        await server.stop()


async def test_scalar_cmd_framing():
    server = FakeIntiface()
    client = await make_connected(server)
    try:
        await client.set_level(0.85)
        # One ScalarCmd per device that has vibrate actuators.
        by_device = {c["DeviceIndex"]: c for c in server.scalar_cmds}
        assert set(by_device) == {0, 3}
        assert by_device[0]["Scalars"] == [
            {"Index": 0, "Scalar": 0.85, "ActuatorType": "Vibrate"}]
        assert [s["Index"] for s in by_device[3]["Scalars"]] == [0, 1]
        await client.stop()
        await wait_until(lambda: server.stops == 1)
    finally:
        await client.disconnect()
        await server.stop()


async def test_error_response_recorded_not_raised():
    server = FakeIntiface(error_on_scalar=True)
    client = await make_connected(server)
    try:
        await client.set_level(0.5)   # must not raise
        assert "nope" in client.last_error
    finally:
        await client.disconnect()
        await server.stop()


async def test_reconnect_after_server_drop():
    server = FakeIntiface(drop_first_session=True)
    client = await make_connected(server)
    try:
        # First session ends right after handshake; the supervisor reconnects.
        await wait_until(lambda: server.connections >= 2 and client.connected)
        await client.set_level(0.3)
        assert server.scalar_cmds
    finally:
        await client.disconnect()
        await server.stop()


async def test_ping_task_honors_max_ping_time():
    server = FakeIntiface(max_ping=200)   # client should ping every ~100 ms
    client = await make_connected(server)
    try:
        await wait_until(lambda: server.pings >= 2)
    finally:
        await client.disconnect()
        await server.stop()


async def test_disconnect_is_clean_and_disabled_client_idles():
    cfg = {"enabled": False, "url": ""}
    client = ButtplugClient(lambda: cfg, backoff=(0.05,))
    client.start()
    await asyncio.sleep(0.05)
    assert client.connected is False
    await client.disconnect()             # no server ever existed: still clean
    await client.set_level(0.5)           # no-op, must not raise
