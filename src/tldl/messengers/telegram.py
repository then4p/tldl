"""Telegram via the Bot API (long polling, no public URL needed).

Create a bot with @BotFather and put its token in the config. Forward voice
messages, audio files or round video notes to the bot.
"""

import asyncio
import logging
from typing import Any

import aiohttp

from ..models import Audio, AudioRef, IncomingMessage
from .base import Messenger, MessageHandler

log = logging.getLogger(__name__)


class TelegramMessenger(Messenger):
    max_message_length = 4096
    supports_edit = True

    def __init__(
        self,
        name: str,
        handler: MessageHandler,
        *,
        token: str,
        api_url: str = "https://api.telegram.org",
        poll_timeout: int = 50,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, handler, **kwargs)
        self.base = f"{api_url.rstrip('/')}/bot{token}"
        self.file_base = f"{api_url.rstrip('/')}/file/bot{token}"
        self.poll_timeout = poll_timeout
        self._session: aiohttp.ClientSession | None = None

    async def _call(self, method: str, **params: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.poll_timeout + 30)
        async with self._session.post(f"{self.base}/{method}", json=params, timeout=timeout) as resp:
            body = await resp.json(content_type=None)
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body.get('description', body)}")
        return body["result"]

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        me = await self._call("getMe")
        log.info("telegram: logged in as @%s", me.get("username"))

    async def run(self) -> None:
        offset = None
        while True:
            try:
                params: dict[str, Any] = {"timeout": self.poll_timeout, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset
                updates = await self._call("getUpdates", **params)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram: polling failed, retrying in 5s")
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    msg = self._parse(message)
                    if msg:
                        await self.dispatch(msg)

    def _parse(self, message: dict[str, Any]) -> IncomingMessage | None:
        media = None
        for key in ("voice", "audio", "video_note", "video"):
            if key in message:
                media = message[key]
                break
        doc = message.get("document")
        if media is None and doc and str(doc.get("mime_type", "")).startswith("audio/"):
            media = doc

        audio = None
        if media is not None:
            file_id = media["file_id"]
            mime = media.get("mime_type") or ("video/mp4" if "video_note" in message else "audio/ogg")
            filename = media.get("file_name")

            async def fetch() -> Audio:
                info = await self._call("getFile", file_id=file_id)
                async with self._session.get(f"{self.file_base}/{info['file_path']}") as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                return Audio(data=data, mime_type=mime, filename=filename)

            audio = AudioRef(fetch=fetch, mime_type=mime, duration=media.get("duration"))

        sender = message.get("from") or {}
        return IncomingMessage(
            messenger=self.name,
            chat_id=str(message["chat"]["id"]),
            sender_id=str(sender.get("id", "")),
            text=message.get("text"),
            audio=audio,
            raw={"message_id": message["message_id"]},
        )

    async def reply(self, msg: IncomingMessage, text: str) -> int:
        sent = await self._call(
            "sendMessage",
            chat_id=msg.chat_id,
            text=text,
            reply_parameters={"message_id": msg.raw["message_id"], "allow_sending_without_reply": True},
        )
        return sent["message_id"]

    async def edit(self, msg: IncomingMessage, handle: int, text: str) -> None:
        try:
            await self._call("editMessageText", chat_id=msg.chat_id, message_id=handle, text=text)
        except RuntimeError as exc:
            if "message is not modified" not in str(exc):
                raise

    async def notify_working(self, msg: IncomingMessage) -> None:
        await self._call("sendChatAction", chat_id=msg.chat_id, action="typing")

    async def close(self) -> None:
        if self._session:
            await self._session.close()
