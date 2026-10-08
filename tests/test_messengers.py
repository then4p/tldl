import base64

from tldl.messengers.signal import SignalMessenger
from tldl.messengers.telegram import TelegramMessenger
from tldl.messengers.whatsapp_waha import WahaMessenger


async def noop(*_):
    pass


def test_telegram_voice():
    m = TelegramMessenger("tg", noop, token="t")
    msg = m._parse({
        "message_id": 5, "chat": {"id": 42}, "from": {"id": 7},
        "forward_origin": {"type": "user"},
        "voice": {"file_id": "F", "duration": 12, "mime_type": "audio/ogg"},
    })
    assert (msg.chat_id, msg.sender_id, msg.raw["message_id"]) == ("42", "7", 5)
    assert msg.audio.duration == 12


def test_telegram_text():
    m = TelegramMessenger("tg", noop, token="t")
    msg = m._parse({"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, "text": "/help"})
    assert msg.audio is None and msg.text == "/help"


def _signal_env(**kw):
    return {"sourceNumber": "+491", "sourceUuid": "uuid-1", "timestamp": 100, **kw}


def test_signal_direct_voice():
    m = SignalMessenger("sig", noop, number="+49bot", vips={"uuid-1": "Ann"})
    msg = m._parse(_signal_env(dataMessage={
        "timestamp": 100, "attachments": [{"id": "att1", "contentType": "audio/aac"}],
    }))
    assert msg.chat_id == "+491" and msg.audio and not msg.from_self
    assert m.vip_name(msg) == "Ann"  # matched by uuid


def test_signal_group():
    m = SignalMessenger("sig", noop, number="+49bot")
    msg = m._parse(_signal_env(dataMessage={"timestamp": 1, "message": "hi", "groupInfo": {"groupId": "abc="}}))
    assert msg.chat_id == "group." + base64.b64encode(b"abc=").decode()


def test_signal_note_to_self():
    sent = {"destinationNumber": "+49me", "timestamp": 5,
            "attachments": [{"id": "a", "contentType": "audio/aac"}]}
    env = {"sourceNumber": "+49me", "timestamp": 5, "syncMessage": {"sentMessage": sent}}
    assert SignalMessenger("s", noop, number="+49me")._parse(env) is None
    msg = SignalMessenger("s", noop, number="+49me", note_to_self=True)._parse(env)
    assert msg.from_self and msg.chat_id == "+49me" and msg.audio
    # sync of a message sent to someone else is ignored
    env["syncMessage"]["sentMessage"] = {**sent, "destinationNumber": "+49other"}
    assert SignalMessenger("s", noop, number="+49me", note_to_self=True)._parse(env) is None


def _waha(**kw):
    return {"id": "m1", "from": "4911@c.us", "to": "4999@c.us", "fromMe": False, "body": "", **kw}


def test_waha_voice_and_url_rewrite():
    m = WahaMessenger("wa", noop, url="http://waha:3000")
    msg = m._parse(_waha(hasMedia=True, media={"url": "http://localhost:3000/api/files/x.oga", "mimetype": "audio/ogg; codecs=opus"}))
    assert msg.sender_id == "4911" and msg.chat_id == "4911@c.us" and msg.audio
    assert msg.text is None


def test_waha_from_me():
    m = WahaMessenger("wa", noop)
    assert m._parse(_waha(fromMe=True, body="/help")) is None
    m = WahaMessenger("wa", noop, self_chat=True)
    msg = m._parse(_waha(fromMe=True, to="4911@c.us", body="/help"))
    assert msg.from_self and msg.chat_id == "4911@c.us"
    m._sent_ids.append("m2")
    assert m._parse(_waha(id="m2", fromMe=True, to="4911@c.us")) is None


def test_waha_group_sender():
    m = WahaMessenger("wa", noop)
    msg = m._parse(_waha(**{"from": "123@g.us", "participant": "4955@c.us", "body": "/engines"}))
    assert msg.chat_id == "123@g.us" and msg.sender_id == "4955"


def test_signal_vips_numbers_with_or_without_plus():
    m = SignalMessenger("sig", noop, number="+49bot", vips={4915111: "A", "+4915222": "B", "uuid-3": "C"})
    for ids in ({"+4915111", "uuid-x"}, {"+4915222"}, {"uuid-3"}):
        msg = m._parse(_signal_env(dataMessage={"timestamp": 1, "message": "/help"}))
        msg.raw["sender_ids"] = ids
        assert m.vip_name(msg), ids
    msg.raw["sender_ids"] = {"uuid-unknown"}
    assert m.vip_name(msg) is None


async def test_signal_reads_message_id_from_both_response_shapes():
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    for body in ([{"timestamp": "1791469160856"}], {"timestamp": "1791469160856"}):
        async def send(_, body=body):
            return web.json_response(body, status=201)

        app = web.Application()
        app.router.add_post("/v2/send", send)
        server = TestServer(app)
        await server.start_server()
        m = SignalMessenger("sig", noop, url=str(server.make_url("")).rstrip("/"), number="+49bot")
        await m.start()
        try:
            assert await m.send("uuid-1", "hi") == 1791469160856
        finally:
            await m.close()
            await server.close()
