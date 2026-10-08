import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tldl.config import HealthConfig
from tldl.health import HealthMonitor
from tldl.messengers.signal import SignalMessenger
from tldl.messengers.telegram import TelegramMessenger
from tldl.messengers.whatsapp_waha import WahaMessenger
from tldl.models import Health

from .conftest import FakeMessenger


async def noop(*_):
    pass


class Flaky(FakeMessenger):
    def __init__(self, name, handler, **kw):
        super().__init__(name, handler, **kw)
        self.health = Health(True)
        self.sent = []

    async def check_health(self):
        return self.health

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))


def monitor(**cfg):
    tg, wa = Flaky("telegram", noop), Flaky("whatsapp", noop)
    config = HealthConfig(alert={"messenger": "telegram", "chat_id": "42"}, **cfg)
    return HealthMonitor({"telegram": tg, "whatsapp": wa}, config), tg, wa


async def test_alerts_after_confirmation_and_on_recovery():
    mon, tg, wa = monitor(confirm=2)
    await mon.check_all()
    wa.health = Health(False, "logged out")
    await mon.check_all()
    assert tg.sent == []  # one failure could be a blip
    await mon.check_all()
    assert tg.sent == [("42", "⚠️ whatsapp is not working: logged out")]
    await mon.check_all()
    assert len(tg.sent) == 1  # no repeat before remind_every
    wa.health = Health(True)
    await mon.check_all()
    assert tg.sent[-1] == ("42", "✅ whatsapp is working again.")


async def test_reminder():
    mon, tg, wa = monitor(confirm=1, remind_every=0.001)
    wa.health = Health(False, "down")
    await mon.check_all()
    mon.state["whatsapp"].last_alert -= 1
    await mon.check_all()
    assert len(tg.sent) == 2


async def test_check_exception_counts_as_failure():
    mon, tg, wa = monitor(confirm=1)

    async def boom():
        raise RuntimeError("bridge exploded")

    wa.check_health = boom
    await mon.check_all()
    assert mon.state["whatsapp"].ok is False and "bridge exploded" in tg.sent[0][1]


async def test_health_changed_wakes_monitor():
    mon, _, wa = monitor()
    wa.health_changed()
    assert mon._wake.is_set()


async def test_healthz_and_metrics():
    mon, _, wa = monitor(confirm=1)
    app = web.Application()
    app.add_routes(mon.routes())
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/healthz")).status == 200  # nothing checked yet
        wa.health = Health(False, "logged out")
        await mon.check_all()
        resp = await client.get("/healthz")
        body = await resp.json()
        assert resp.status == 503 and body["ok"] is False
        assert body["messengers"]["whatsapp"]["detail"] == "logged out"
        assert body["messengers"]["telegram"]["ok"] is True
        metrics = await (await client.get("/metrics")).text()
        assert 'tldl_messenger_up{messenger="telegram"} 1' in metrics
        assert 'tldl_messenger_up{messenger="whatsapp"} 0' in metrics


# ---- the messengers' own checks, against fake bridges -------------------------

async def serve(routes):
    app = web.Application()
    app.add_routes(routes)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_telegram_detects_second_instance():
    async def call(request):
        return web.json_response({"ok": True, "result": {"username": "testbot"}})

    server = await serve([web.post("/bottok/{method}", call)])
    m = TelegramMessenger("tg", noop, token="tok", api_url=str(server.make_url("")))
    await m.start()
    try:
        assert (await m.check_health()) == Health(True, "@testbot")
        m._poll_error = "telegram getUpdates: Conflict: terminated by other getUpdates request"
        health = await m.check_health()
        assert not health.ok and "same bot token" in health.detail
    finally:
        await m.close(); await server.close()


async def test_signal_checks():
    state = {"accounts": ["+49bot"], "devices": (200, "[]")}

    async def accounts(_):
        return web.json_response(state["accounts"])

    async def devices(_):
        status, text = state["devices"]
        return web.Response(status=status, text=text)

    server = await serve([web.get("/v1/accounts", accounts), web.get("/v1/devices/{n}", devices)])
    m = SignalMessenger("sig", noop, url=str(server.make_url("")).rstrip("/"), number="+49bot")
    await m.start()
    try:
        m._connected = True
        assert (await m.check_health()).ok
        state["devices"] = (400, '{"error":"StatusCode: 499 (DeprecatedVersionException)"}')
        health = await m.check_health()
        assert not health.ok and "Update the signal-cli-rest-api image" in health.detail
        state["accounts"] = []
        assert "not registered" in (await m.check_health()).detail
        await server.close()
        assert "unreachable" in (await m.check_health()).detail
    finally:
        await m.close(); await server.close()


async def test_waha_checks_and_status_webhook():
    state = {"status": "WORKING"}

    async def session(request):
        if request.match_info["name"] != "default":
            return web.Response(status=404)
        return web.json_response({"name": "default", "status": state["status"]})

    server = await serve([web.get("/api/sessions/{name}", session)])
    m = WahaMessenger("wa", noop, url=str(server.make_url("")).rstrip("/"))
    await m.start()
    try:
        assert (await m.check_health()).ok
        state["status"] = "SCAN_QR_CODE"
        health = await m.check_health()
        assert not health.ok and "Scan the QR code" in health.detail and "14 days" in health.detail
        m.session_name = "other"
        assert "no session" in (await m.check_health()).detail
    finally:
        await m.close(); await server.close()

    woken = []
    m = WahaMessenger("wa", noop)
    m.on_health_change = lambda: woken.append(True)
    app = web.Application()
    app.add_routes(m.routes())
    async with TestClient(TestServer(app)) as client:
        await client.post("/webhook/wa", data=json.dumps({"event": "session.status", "session": "default", "payload": {"status": "FAILED"}}))
    assert woken == [True]
