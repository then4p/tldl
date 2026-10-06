"""Signal via bbernhard/signal-cli-rest-api running in ``json-rpc`` mode.

Two ways to use it:

* register a dedicated number for the bot and forward voice notes to it, or
* link signal-cli as a secondary device of your own account and set
  ``note_to_self: true``: voice notes you forward to "Note to Self" get
  transcribed and the transcript lands in the same chat.
"""

import asyncio
import base64
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from ..models import Audio, AudioRef, IncomingMessage
from .base import Messenger, MessageHandler

log = logging.getLogger(__name__)


class SignalMessenger(Messenger):
    max_message_length = 6000
    supports_edit = True

    def __init__(
        self,
        name: str,
        handler: MessageHandler,
        *,
        url: str = "http://localhost:8080",
        number: str,
        note_to_self: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, handler, **kwargs)
        self.url = url.rstrip("/")
        self.number = number
        self.note_to_self = note_to_self
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

    async def run(self) -> None:
        ws_url = self.url.replace("http", "ws", 1) + f"/v1/receive/{quote(self.number)}"
        while True:
            try:
                async with self._session.ws_connect(ws_url, heartbeat=30) as ws:
                    log.info("signal: connected to %s", ws_url)
                    async for frame in ws:
                        if frame.type == aiohttp.WSMsgType.TEXT:
                            await self._on_frame(frame.data)
                        elif frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                log.warning("signal: websocket closed, reconnecting in 5s")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("signal: websocket failed, reconnecting in 5s")
            await asyncio.sleep(5)

    async def _on_frame(self, data: str) -> None:
        try:
            envelope = json.loads(data).get("envelope") or {}
        except json.JSONDecodeError:
            log.warning("signal: non-JSON frame: %.200s", data)
            return
        msg = self._parse(envelope)
        if msg:
            await self.dispatch(msg)

    def _parse(self, envelope: dict[str, Any]) -> IncomingMessage | None:
        from_self = False
        data = envelope.get("dataMessage")
        if data is None and self.note_to_self:
            sent = (envelope.get("syncMessage") or {}).get("sentMessage")
            if sent and not sent.get("groupInfo") and self.number in (
                sent.get("destinationNumber"), sent.get("destination")
            ):
                data, from_self = sent, True
        if not data:
            return None

        author = envelope.get("sourceNumber") or envelope.get("sourceUuid") or envelope.get("source")
        group = data.get("groupInfo") or {}
        if group.get("groupId"):
            recipient = "group." + base64.b64encode(group["groupId"].encode()).decode()
        else:
            recipient = author

        audio = None
        for att in data.get("attachments") or []:
            mime = att.get("contentType", "")
            if mime.startswith("audio/"):
                audio = self._audio_ref(att)
                break

        return IncomingMessage(
            messenger=self.name,
            chat_id=recipient,
            sender_id=str(author),
            text=data.get("message"),
            audio=audio,
            from_self=from_self,
            raw={
                "timestamp": data.get("timestamp") or envelope.get("timestamp"),
                "author": author,
                "sender_ids": {envelope.get("sourceNumber"), envelope.get("sourceUuid")} - {None},
            },
        )

    def _audio_ref(self, att: dict[str, Any]) -> AudioRef:
        att_id, mime, filename = att["id"], att["contentType"], att.get("filename")

        async def fetch() -> Audio:
            async with self._session.get(f"{self.url}/v1/attachments/{quote(att_id)}") as resp:
                resp.raise_for_status()
                return Audio(data=await resp.read(), mime_type=mime, filename=filename)

        return AudioRef(fetch=fetch, mime_type=mime)

    def is_authorized(self, msg: IncomingMessage) -> bool:
        # Allow listing either the phone number or the ACI/UUID.
        ids = msg.raw.get("sender_ids") or {msg.sender_id}
        return self.allow_all or msg.from_self or bool(ids & self.allowed_senders)

    async def reply(self, msg: IncomingMessage, text: str) -> int | None:
        return await self._send(msg, text)

    async def edit(self, msg: IncomingMessage, handle: int, text: str) -> None:
        # Signal identifies the message to edit by its sent timestamp.
        await self._send(msg, text, edit_timestamp=handle)

    async def _send(self, msg: IncomingMessage, text: str, edit_timestamp: int | None = None) -> int | None:
        payload: dict[str, Any] = {
            "number": self.number,
            "recipients": [msg.chat_id],
            "message": text,
            "quote_timestamp": msg.raw.get("timestamp"),
            "quote_author": msg.raw.get("author"),
        }
        if msg.from_self:
            payload["notify_self"] = True
        if edit_timestamp is not None:
            payload["edit_timestamp"] = edit_timestamp
        async with self._session.post(f"{self.url}/v2/send", json=payload) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"signal send failed {resp.status}: {await resp.text()}")
            body = await resp.json(content_type=None)
        timestamp = body.get("timestamp") if isinstance(body, dict) else None
        return int(timestamp) if timestamp else None

    async def notify_working(self, msg: IncomingMessage) -> None:
        if msg.from_self:
            return
        async with self._session.put(
            f"{self.url}/v1/typing-indicator/{quote(self.number)}", json={"recipient": msg.chat_id}
        ):
            pass

    async def close(self) -> None:
        if self._session:
            await self._session.close()
