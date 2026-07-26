import json

import httpx
import pytest

from toylink.lovense_client import LovenseClient, _percent_to_lovense

CFG = {"enabled": True, "host": "192.168.1.9", "port": 20010,
       "use_time_sec_safety": True}


def make_client(cfg=None, handler=None):
    cfg = dict(CFG if cfg is None else cfg)
    requests = []

    def default_handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "data": {"toys": "[]"}})

    transport = httpx.MockTransport(handler or default_handler)
    return LovenseClient(lambda: cfg, transport=transport), requests, cfg


def test_percent_mapping():
    assert _percent_to_lovense(0.0) == 0
    assert _percent_to_lovense(1.0) == 20
    assert _percent_to_lovense(0.5) == 10
    assert _percent_to_lovense(0.01) == 1   # nonzero never rounds to off
    assert _percent_to_lovense(2.0) == 20   # clamped


async def test_set_level_body_with_safety():
    client, requests, _ = make_client()
    await client.set_level(0.85)
    assert requests == [{"command": "Function", "action": "Vibrate:17",
                         "timeSec": 2, "apiVer": 1}]
    assert client.connected


async def test_set_level_without_safety():
    cfg = dict(CFG, use_time_sec_safety=False)
    client, requests, _ = make_client(cfg)
    await client.set_level(1.0)
    assert requests[0]["timeSec"] == 0


async def test_stop_sends_vibrate_zero():
    client, requests, _ = make_client()
    await client.stop()
    assert requests == [{"command": "Function", "action": "Vibrate:0",
                         "timeSec": 0, "apiVer": 1}]


async def test_probe_parses_json_string_toys():
    def handler(request):
        return httpx.Response(200, json={
            "code": 200,
            "data": {"toys": json.dumps([{"id": "abc", "name": "lush",
                                          "battery": 80}])}})
    client, _, _ = make_client(handler=handler)
    assert await client.probe() is True
    assert client.toys == [{"id": "abc", "name": "lush", "battery": 80}]


async def test_probe_parses_dict_toys():
    def handler(request):
        return httpx.Response(200, json={
            "data": {"toys": {"abc": {"name": "hush", "battery": 55}}}})
    client, _, _ = make_client(handler=handler)
    assert await client.probe() is True
    assert client.toys == [{"name": "hush", "battery": 55, "id": "abc"}]


async def test_errors_are_swallowed_and_recorded():
    def handler(request):
        raise httpx.ConnectError("phone unreachable")
    client, _, _ = make_client(handler=handler)
    await client.set_level(0.5)             # must not raise
    assert client.connected is False
    assert "ConnectError" in client.last_error
    assert await client.probe() is False


async def test_disabled_or_no_host_sends_nothing():
    client, requests, cfg = make_client(dict(CFG, enabled=False))
    await client.set_level(0.5)
    await client.stop()
    assert requests == []
    cfg2 = dict(CFG, host="")
    client2, requests2, _ = make_client(cfg2)
    await client2.set_level(0.5)
    assert requests2 == []


async def test_url_uses_configured_host_port():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={})
    cfg = dict(CFG, host="10.0.0.7", port=30010)
    client, _, _ = make_client(cfg, handler=handler)
    await client.stop()
    assert seen["url"] == "http://10.0.0.7:30010/command"
