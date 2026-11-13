import json

import httpx
import pytest

from toylink.lovense_client import LovenseClient, _percent_to_lovense, normalize_endpoint

CFG = {
    "enabled": True,
    "host": "192.168.1.9",
    "port": 20010,
    "https": False,
    "action_mode": "All",
    "use_time_sec_safety": True,
}


class FakeClock:
    def __init__(self):
        self.t = 50.0

    def __call__(self):
        return self.t


def make_client(cfg=None, handler=None):
    cfg = dict(CFG if cfg is None else cfg)
    requests = []
    clock = FakeClock()

    def default_handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200, "data": {"toys": "[]"}})

    transport = httpx.MockTransport(handler or default_handler)
    return LovenseClient(lambda: cfg, transport=transport, clock=clock), requests, cfg, clock


def test_percent_mapping():
    assert _percent_to_lovense(0.0) == 0
    assert _percent_to_lovense(1.0) == 20
    assert _percent_to_lovense(0.5) == 10
    assert _percent_to_lovense(0.01) == 1   # nonzero never rounds to off
    assert _percent_to_lovense(2.0) == 20   # clamped


def test_endpoint_normalization():
    assert normalize_endpoint("192.168.1.42", 20010, False) == (False, "192.168.1.42", 20010)
    assert normalize_endpoint("192.168.1.42:30010", 20010, False) == (True, "192.168.1.42", 30010)
    assert normalize_endpoint("https://10.0.0.8:34568/command", 20010, False) == (True, "10.0.0.8", 34568)
    assert normalize_endpoint("http://127.0.0.1:20010", 30010, True) == (False, "127.0.0.1", 20010)


async def test_set_level_drives_all_motors_and_deduplicates_steps():
    client, requests, _, clock = make_client()
    await client.set_level(0.85)  # step 17
    assert requests == [{"command": "Function", "action": "All:17", "timeSec": 2, "apiVer": 1}]
    assert client.connected is True

    # Identical step (0.84 -> still 17) within keepalive window is skipped
    clock.t += 0.05
    await client.set_level(0.84)
    assert len(requests) == 1

    # Keepalive resends after >0.85s even if step unchanged
    clock.t += 0.90
    await client.set_level(0.85)
    assert len(requests) == 2


async def test_vibrate_only_mode_and_stop():
    client, requests, _, _ = make_client(dict(CFG, action_mode="Vibrate"))
    await client.set_level(0.5)
    assert requests[-1]["action"] == "Vibrate:10"
    await client.stop()
    assert requests[-1] == {"command": "Function", "action": "Stop", "timeSec": 0, "apiVer": 1}


async def test_nonzero_error_code_marks_disconnected():
    def handler(_request):
        return httpx.Response(200, json={"code": 400, "type": "Invalid Command"})

    client, _, _, _ = make_client(handler=handler)
    await client.set_level(0.6)
    assert client.connected is False
    assert "Invalid Command" in client.last_error


async def test_probe_parses_toys_and_returns_endpoint():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "toys": json.dumps({
                        "t1": {"name": "lush", "nickName": "Lush 3", "battery": 88}
                    })
                },
            },
        )

    client, _, _, _ = make_client(handler=handler)
    found = await client.probe()
    assert found == {"host": "192.168.1.9", "port": 20010, "https": False}
    assert len(client.toys) == 1
    assert client.toys[0]["display_name"] == "Lush 3"
    assert client.toys[0]["battery"] == 88


async def test_errors_are_swallowed_and_recorded():
    def handler(_request):
        raise httpx.ConnectError("phone unreachable")

    client, _, _, _ = make_client(handler=handler)
    await client.set_level(0.5)
    assert client.connected is False
    assert "ConnectError" in client.last_error
    assert await client.probe(auto_discover=False) is None


async def test_disabled_or_no_host_sends_nothing():
    client, requests, _, _ = make_client(dict(CFG, enabled=False))
    await client.set_level(0.5)
    await client.stop()
    assert requests == []

    client2, requests2, _, _ = make_client(dict(CFG, host=""))
    await client2.set_level(0.5)
    assert requests2 == []


async def test_send_pattern_formats_rule_and_prevents_wraparound():
    # 1. Looping pattern: timeSec=0, stopPrevious=0 -> native gapless hardware loop
    client, requests, _, _ = make_client()
    await client.send_pattern([0.25, 0.5, 0.75, 0.5], interval_ms=135, loop=True)
    assert len(requests) == 1
    loop_body = requests[0]
    assert loop_body["command"] == "Pattern"
    assert loop_body["rule"] == "V:1;F:;S:135#"
    assert loop_body["timeSec"] == 0
    assert loop_body["stopPrevious"] == 0
    assert loop_body["apiVer"] == 2

    # 2. One-shot pattern (loop=False): buffer span exceeds timeSec and ends on 0
    requests.clear()
    await client.send_pattern([0.1, 0.25, 0.5, 0.75, 1.0, 0.5, 0.0, 0.0], interval_ms=120, loop=False)
    body = requests[0]
    assert body["timeSec"] >= 1
    assert body["stopPrevious"] == 0
    steps = [int(x) for x in body["strength"].split(";")]
    assert steps[:7] == [2, 5, 10, 15, 20, 10, 0]
    assert len(steps) * 0.12 >= body["timeSec"]
    assert steps[-1] == 0

    # Vibrate-only mode sets F:v in rule
    v_client, v_requests, _, _ = make_client(dict(CFG, action_mode="Vibrate"))
    await v_client.send_pattern([0.4, 0.6], interval_ms=120, loop=True)
    assert v_requests[0]["rule"] == "V:1;F:v;S:120#"


async def test_send_pattern_falls_back_to_function_if_bridge_rejects_pattern():
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        if body.get("command") == "Pattern":
            return httpx.Response(200, json={"code": 400, "type": "Unsupported Command"})
        return httpx.Response(200, json={"code": 200, "data": {}})

    client, _, _, _ = make_client(handler=handler)
    await client.probe(auto_discover=False)
    assert client.connected is True
    assert client.supports_pattern_stream is True

    await client.send_pattern([0.5, 0.6, 0.7], interval_ms=120)
    assert client.supports_pattern_stream is False
    assert sent[-1]["command"] == "Function"
    assert sent[-1]["action"] == "All:10"


async def test_send_live_level_uses_stop_previous_zero_and_paces_ble_slots():
    client, requests, _, clock = make_client()

    # First live step starts immediately with stopPrevious=0, timeSec=0
    await client.send_live_level(0.50)  # step 10
    assert requests == [
        {"command": "Function", "action": "All:10", "timeSec": 0, "stopPrevious": 0, "apiVer": 1}
    ]

    # Holding the same step does NOT spam 0.85s keepalive packets
    clock.t += 3.0
    await client.send_live_level(0.51)  # still step 10
    assert len(requests) == 1

    # Step change paces to 110ms BLE slot and delivers latest step on eligible tick
    await client.send_live_level(0.60)  # step 12 -> sends now
    assert len(requests) == 2
    assert requests[-1]["action"] == "All:12"
    assert requests[-1]["stopPrevious"] == 0

    clock.t += 0.05  # < 110ms -> coalesced
    await client.send_live_level(0.70)
    assert len(requests) == 2

    clock.t += 0.07  # >= 110ms -> sends newest step (15)
    await client.send_live_level(0.75)
    assert len(requests) == 3
    assert requests[-1]["action"] == "All:15"

    # Switching directly out of a hardware Pattern loop sends stopPrevious=1 once, then 0
    await client.send_pattern([0.5, 0.6, 0.7], interval_ms=130, loop=True)
    assert requests[-1]["command"] == "Pattern"
    clock.t += 0.2
    await client.send_live_level(0.65)
    assert requests[-1] == {
        "command": "Function",
        "action": "All:13",
        "timeSec": 0,
        "stopPrevious": 1,
        "apiVer": 1,
    }
    clock.t += 0.2
    await client.send_live_level(0.70)
    assert requests[-1]["stopPrevious"] == 0


