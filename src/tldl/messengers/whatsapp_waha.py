"""WhatsApp via WAHA (https://waha.devlike.pro), a self-hosted WhatsApp HTTP API.

WAHA links to a WhatsApp account like WhatsApp Web does and POSTs incoming
messages to our webhook. Either link a dedicated number and forward voice
messages to it, or link your own account and set ``self_chat: true`` to get
voice messages transcribed that you forward to the "Message yourself" chat.

Configure WAHA with ``WHATSAPP_HOOK_URL=http://<bot>:8000/webhook/<name>`` and
``WHATSAPP_HOOK_EVENTS=message`` (or ``message.any`` for ``self_chat``).
"""

import collections
import logging
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web

from ..models import Audio, AudioRef, Health, IncomingMessage
from .base import Messenger, MessageHandler

log = logging.getLogger(__name__)


def _user_part(jid: str) -> str:
    return jid.split("@", 1)[0]


class WahaMessenger(Messenger):
    max_message_length = 4000
    supports_edit = True
    label = "WhatsApp"

    def __init__(
        self,
        name: str,
        handler: MessageHandler,
        *,
        url: str = "http://localhost:3000",
        session: str = "default",
        api_key: str | None = None,
        webhook_path: str | None = None,
        webhook_token: str | None = None,
        self_chat: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, handler, **kwargs)
        self.url = url.rstrip("/")
        self.session_name = session
        self.api_key = api_key
        self.webhook_path = webhook_path or f"/webhook/{name}"
        self.webhook_token = webhook_token
        self.self_chat = self_chat
        self._session: aiohttp.ClientSession | None = None
        # Ids of messages we sent, so our own replies in the self chat are never processed.
        self._sent_ids: collections.deque[str] = collections.deque(maxlen=200)

    async def start(self) -> None:
        headers = {"X-Api-Key": self.api_key} if self.api_key else {}
        self._session = aiohttp.ClientSession(headers=headers)

    def routes(self) -> list[web.RouteDef]:
        return [web.post(self.webhook_path, self._webhook)]

    async def _webhook(self, request: web.Request) -> web.Response:
        if self.webhook_token and request.query.get("token") != self.webhook_token:
            return web.Response(status=403)
        try:
            body = await request.json()
        except ValueError:
            return web.Response(status=400)
        if body.get("session") != self.session_name:
            return web.Response(text="ok")
        if body.get("event") in ("message", "message.any"):
            msg = self._parse(body.get("payload") or {})
            if msg:
                await self._resolve_phone(msg)
                await self.dispatch(msg)
        elif body.get("event") == "session.status":
            self.health_changed()
        return web.Response(text="ok")

    def _parse(self, p: dict[str, Any]) -> IncomingMessage | None:
        if p.get("id") in self._sent_ids:
            return None
        from_self = False
        if p.get("fromMe"):
            # Only the "Message yourself" chat is of interest among our own messages.
            if not (self.self_chat and p.get("from") and p.get("from") == p.get("to")):
                return None
            from_self = True

        chat_id = p.get("to") if p.get("fromMe") else p.get("from")
        sender = p.get("participant") or p.get("author") or p.get("from") or ""
        # WhatsApp increasingly identifies senders by a LID ("123…@lid") instead of
        # their phone number; WAHA passes the number along as SenderAlt when known.
        alt = ((p.get("_data") or {}).get("Info") or {}).get("SenderAlt") or ""
        phone = _user_part(sender) if sender.endswith("@c.us") else _user_part(alt) if alt else None

        audio = None
        media = p.get("media") or {}
        mime = media.get("mimetype") or ""
        if p.get("hasMedia") and mime.startswith("audio/"):
            if media.get("url"):
                audio = self._audio_ref(media["url"], mime, media.get("filename"))
            else:
                log.warning("whatsapp: voice message without media url (error: %s)", media.get("error"))

        return IncomingMessage(
            messenger=self.name,
            chat_id=chat_id,
            sender_id=phone or _user_part(sender),
            text=None if p.get("hasMedia") else p.get("body"),
            audio=audio,
            from_self=from_self,
            raw={"id": p.get("id"), "jid": sender, "sender_ids": {_user_part(sender)} | ({phone} if phone else set())},
        )

    async def _resolve_phone(self, msg: IncomingMessage) -> None:
        """Look up the phone number behind a LID sender if the message didn't carry it."""
        jid = msg.raw.get("jid", "")
        if not jid.endswith("@lid") or msg.sender_id != _user_part(jid):
            return
        try:
            async with self._session.get(f"{self.url}/api/{quote(self.session_name)}/lids/{quote(jid, safe='@')}") as resp:
                pn = (await resp.json(content_type=None)).get("pn") if resp.status == 200 else None
        except Exception:
            log.debug("whatsapp: LID lookup failed", exc_info=True)
            return
        if pn:
            msg.sender_id = _user_part(pn)
            msg.raw["sender_ids"].add(msg.sender_id)

    def vip_name(self, msg: IncomingMessage) -> str | None:
        # VIPs may be listed by phone number or by LID.
        if msg.from_self:
            return "you"
        for sender_id in msg.raw.get("sender_ids") or {msg.sender_id}:
            if sender_id in self.vips:
                return self.vips[sender_id]
        return None

    def _audio_ref(self, media_url: str, mime: str, filename: str | None) -> AudioRef:
        # WAHA builds media URLs from its own WAHA_BASE_URL, which may not be
        # reachable from here; keep the path and use our configured base URL.
        parts = urlsplit(media_url)
        url = self.url + parts.path + (f"?{parts.query}" if parts.query else "")

        async def fetch() -> Audio:
            async with self._session.get(url) as resp:
                resp.raise_for_status()
                return Audio(data=await resp.read(), mime_type=mime, filename=filename)

        return AudioRef(fetch=fetch, mime_type=mime)

    async def reply(self, msg: IncomingMessage, text: str) -> str | None:
        return await self._send_text(msg.chat_id, text, reply_to=msg.raw.get("id"))

    async def send(self, chat_id: str, text: str) -> str | None:
        return await self._send_text(chat_id, text)

    async def _send_text(self, chat_id: str, text: str, reply_to: str | None = None) -> str | None:
        payload = {"session": self.session_name, "chatId": chat_id, "text": text, "reply_to": reply_to}
        async with self._session.post(f"{self.url}/api/sendText", json=payload) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"waha sendText failed {resp.status}: {await resp.text()}")
            body = await resp.json(content_type=None)
        sent_id = body.get("id") if isinstance(body, dict) else None
        if isinstance(sent_id, dict):  # some engines return {"_serialized": ...}
            sent_id = sent_id.get("_serialized")
        if sent_id:
            self._sent_ids.append(sent_id)
        return sent_id

    async def edit(self, msg: IncomingMessage, handle: str, text: str) -> None:
        url = (
            f"{self.url}/api/{quote(self.session_name)}/chats/{quote(msg.chat_id, safe='@')}"
            f"/messages/{quote(handle, safe='@')}"
        )
        async with self._session.put(url, json={"text": text}) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"waha edit failed {resp.status}: {await resp.text()}")

    async def check_health(self) -> Health:
        try:
            async with self._session.get(f"{self.url}/api/sessions/{quote(self.session_name)}") as resp:
                if resp.status == 404:
                    return Health(False, f"WAHA has no session '{self.session_name}'. Create and start it in the WAHA dashboard.")
                resp.raise_for_status()
                status = (await resp.json(content_type=None)).get("status")
        except Exception as exc:
            return Health(False, f"WAHA is unreachable at {self.url}: {exc}")
        if status == "WORKING":
            return Health(True, "session working")
        hints = {
            "SCAN_QR_CODE": "WhatsApp logged the bot out. Scan the QR code in the WAHA dashboard with the bot's phone, and open WhatsApp on that phone at least every 14 days.",
            "FAILED": "Login is probably required again. Restart the session in the WAHA dashboard and scan the QR code.",
            "STOPPED": "Start the session in the WAHA dashboard.",
            "STARTING": "The session is still starting.",
        }
        return Health(False, f"WhatsApp session is {status}. {hints.get(status, 'Check the WAHA dashboard.')}")

    async def notify_working(self, msg: IncomingMessage) -> None:
        if msg.from_self:
            return
        async with self._session.post(
            f"{self.url}/api/startTyping", json={"session": self.session_name, "chatId": msg.chat_id}
        ):
            pass

    async def close(self) -> None:
        if self._session:
            await self._session.close()
