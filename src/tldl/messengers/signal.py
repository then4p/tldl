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

from ..models import Audio, AudioRef, Health, IncomingMessage
from .base import Messenger, MessageHandler

log = logging.getLogger(__name__)


class SignalMessenger(Messenger):
    max_message_length = 6000
    supports_edit = True
    label = "Signal"

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
        # Unquoted "+49..." in YAML is read as a number and loses its "+".
        self.vips = {f"+{k}" if k.isdigit() else k: v for k, v in self.vips.items()}
        self.url = url.rstrip("/")
        self.number = number
        self.note_to_self = note_to_self
        self._session: aiohttp.ClientSession | None = None
        self._connected = False  # receive websocket is open
        self._pending: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()

    async def run(self) -> None:
        ws_url = self.url.replace("http", "ws", 1) + f"/v1/receive/{quote(self.number)}"
        while True:
            try:
                async with self._session.ws_connect(ws_url, heartbeat=30) as ws:
                    log.info("signal: connected to %s", ws_url)
                    self._connected = True
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
            finally:
                self._connected = False
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
            if audio is None and att.get("contentType", "").startswith("audio/"):
                audio = self._audio_ref(att)
            else:
                # signal-cli saves every attachment to disk; drop the ones we don't use right away.
                self._discard_later(att["id"])

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

        return AudioRef(fetch=fetch, mime_type=mime, discard=lambda: self._delete_attachment(att_id))

    async def _delete_attachment(self, att_id: str) -> None:
        async with self._session.delete(f"{self.url}/v1/attachments/{quote(att_id)}") as resp:
            if resp.status >= 400:
                log.warning("signal: could not delete attachment: %s", resp.status)

    def _discard_later(self, att_id: str) -> None:
        task = asyncio.create_task(self._delete_attachment(att_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def vip_name(self, msg: IncomingMessage) -> str | None:
        # VIPs may be listed by UUID or by phone number (only visible if the sender shares it).
        if msg.from_self:
            return "you"
        for sender_id in msg.raw.get("sender_ids") or {msg.sender_id}:
            if sender_id in self.vips:
                return self.vips[sender_id]
        return None

    async def reply(self, msg: IncomingMessage, text: str) -> int | None:
        return await self._send(msg, text)

    async def edit(self, msg: IncomingMessage, handle: int, text: str) -> None:
        # Signal identifies the message to edit by its sent timestamp.
        await self._send(msg, text, edit_timestamp=handle)

    async def send(self, chat_id: str, text: str) -> int | None:
        return await self._post({"number": self.number, "recipients": [chat_id], "message": text})

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
        return await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> int | None:
        async with self._session.post(f"{self.url}/v2/send", json=payload) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"signal send failed {resp.status}: {await resp.text()}")
            body = await resp.json(content_type=None)
        # json-rpc mode answers [{"timestamp": "..."}] (one entry per send), not the documented {"timestamp": ...}.
        if isinstance(body, list):
            body = body[0] if body else {}
        timestamp = body.get("timestamp") if isinstance(body, dict) else None
        return int(timestamp) if timestamp else None

    async def check_health(self) -> Health:
        try:
            async with self._session.get(f"{self.url}/v1/accounts") as resp:
                accounts = await resp.json(content_type=None)
        except Exception as exc:
            return Health(False, f"signal-cli-rest-api is unreachable at {self.url}: {exc}")
        if self.number not in (accounts or []):
            return Health(False, f"{self.number} isn't loaded in signal-cli-rest-api. After a reboot it sometimes "
                          "doesn't load the account: run `docker compose restart signal-api`. If that doesn't help, "
                          "register the number again.")
        # Lists linked devices: a real round trip to Signal's servers with this account.
        async with self._session.get(f"{self.url}/v1/devices/{quote(self.number)}") as resp:
            if resp.status >= 400:
                error = (await resp.text())[:300]
                if "499" in error or "Deprecated" in error:
                    hint = "Signal rejects this signal-cli version. Update the signal-cli-rest-api image (Signal blocks clients older than about 90 days)."
                else:
                    hint = "The number may have been unregistered or registered on another device. Register it again."
                return Health(False, f"Signal rejected the account check ({error}). {hint}")
        if not self._connected:
            return Health(False, "Not connected to signal-cli-rest-api's receive websocket. Is it running in json-rpc mode?")
        return Health(True, self.number)

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
