"""Wires the messengers to the transcriber and answers chat commands."""

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from .audio import SAMPLE_RATE, decode
from .config import Config
from .health import HealthMonitor
from .messengers.base import Messenger
from .messengers.signal import SignalMessenger
from .messengers.telegram import TelegramMessenger
from .messengers.whatsapp_waha import WahaMessenger
from .models import IncomingMessage
from .transcriber import Transcriber

log = logging.getLogger(__name__)

MESSENGERS = {"telegram": TelegramMessenger, "signal": SignalMessenger, "whatsapp-waha": WahaMessenger}

HELP = """\
Forward or send me a voice message and I'll reply with the text. You get a \
quick transcript within seconds; a slower, more accurate one replaces it \
shortly after.

tldl is free software (AGPL-3.0). Source: https://github.com/then4p/tldl"""

WELCOME = """\
👋 Welcome to tldl: too long; didn't listen.

Forward me a voice message and I'll send you the text a few seconds later.

🔒 Voice messages are transcribed on a private server and never stored."""

RECEIVED = "📥 Received"
TRANSCRIBING = "✍️ Transcribing…"
REFINING = "🔍 Double-checking…"


class StatusMessage:
    """One message that shows progress and is finally replaced by the result.

    Messengers that can't edit get the first status only, then the result as
    new message(s). Status problems are logged, never fatal.
    """

    def __init__(self, messenger: Messenger, msg: IncomingMessage) -> None:
        self.messenger = messenger
        self.msg = msg
        self.handle: Any = None
        self.sent = False
        self.text: str | None = None

    async def update(self, text: str) -> None:
        if text == self.text:
            return
        try:
            if not self.sent:
                self.sent = True
                self.handle = await self.messenger.reply(self.msg, text)
            elif self.handle is not None and self.messenger.supports_edit:
                await self.messenger.edit(self.msg, self.handle, text)
            else:
                return
            self.text = text
        except Exception:
            log.warning("status update failed on %s", self.messenger.name, exc_info=True)

    async def finish(self, chunks: list[str]) -> None:
        """Replace the status with the first chunk; send the rest as new messages.

        Can be called again: it then replaces the first chunk sent last time.
        """
        if chunks and self.handle is not None and self.messenger.supports_edit:
            try:
                await self.messenger.edit(self.msg, self.handle, chunks[0])
                chunks = chunks[1:]
            except Exception:
                # e.g. WhatsApp only allows edits for 15 minutes
                log.warning("final edit failed on %s, sending a new message", self.messenger.name, exc_info=True)
                self.handle = None
        for chunk in chunks:
            handle = await self.messenger.reply(self.msg, chunk)
            if self.handle is None:
                self.handle = handle


def clock(seconds: float) -> str:
    """1:05 for 65 seconds."""
    s = math.ceil(max(seconds, 0))
    return f"{s // 60}:{s % 60:02d}"


def format_text(text: str) -> str:
    return paragraphs(text) if text else "(no speech detected)"


def paragraphs(text: str, min_chars: int = 180) -> str:
    """Break long transcripts into paragraphs at sentence ends.

    A paragraph ends at the first sentence end after ``min_chars`` characters,
    so short transcripts stay one paragraph.
    """
    result, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        current = f"{current} {sentence}" if current else sentence
        if len(current) >= min_chars:
            result.append(current)
            current = ""
    if current:
        result.append(current)
    return "\n\n".join(result)


def split_text(text: str, limit: int) -> list[str]:
    """Split into chunks of at most ``limit`` characters, preferably between paragraphs."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit + 1)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class Bot:
    def __init__(
        self,
        config: Config,
        transcriber: Transcriber | None = None,
        messengers: dict[str, Messenger] | None = None,
    ) -> None:
        self.config = config
        self.transcriber = transcriber or Transcriber(threads=config.threads)
        if messengers is None:
            messengers = {}
            for name, section in config.messengers.items():
                options = dict(section)
                cls = MESSENGERS[options.pop("type")]
                messengers[name] = cls(name, self.handle, **options)
        self.messengers = messengers
        self._welcomed: set[str] = set()  # "messenger:sender" that got the welcome message
        self._tasks: set[asyncio.Task] = set()
        self._load_state()

    def _user(self, msg: IncomingMessage) -> str:
        return f"{msg.messenger}:{msg.sender_id}"

    def _load_state(self) -> None:
        path = self.config.state_file
        if path and Path(path).exists():
            self._welcomed = set(json.loads(Path(path).read_text()).get("welcomed", []))

    def _save_state(self) -> None:
        path = self.config.state_file
        if not path:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps({"welcomed": sorted(self._welcomed)}, indent=2))
        tmp.replace(path)

    # ---- message handling -----------------------------------------------------

    async def handle(self, messenger: Messenger, msg: IncomingMessage) -> None:
        """Entry point for messengers. Returns quickly; work happens in a task."""
        task = asyncio.create_task(self._handle(messenger, msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, messenger: Messenger, msg: IncomingMessage) -> None:
        try:
            command = msg.text.strip().split()[0].lower().split("@", 1)[0] if msg.text and msg.text.startswith("/") else None
            if self._user(msg) not in self._welcomed:
                self._welcomed.add(self._user(msg))
                self._save_state()
                await messenger.reply(msg, WELCOME)
                if command in ("/start", "/help"):
                    return  # the welcome already answers these
            if msg.audio:
                await self._transcribe(messenger, msg)
            elif command == "/start":
                await messenger.reply(msg, WELCOME)
            elif command == "/help":
                await messenger.reply(msg, HELP)
        except Exception:
            log.exception("failed to handle message on %s", msg.messenger)
        finally:
            if msg.audio and msg.audio.discard:
                try:
                    await msg.audio.discard()
                except Exception:
                    log.warning("could not discard audio on %s", msg.messenger, exc_info=True)

    async def _transcribe(self, messenger: Messenger, msg: IncomingMessage) -> None:
        max_duration = self.config.max_duration
        if max_duration and msg.audio.duration and msg.audio.duration > max_duration:
            await messenger.reply(msg, f"Audio is too long ({clock(msg.audio.duration)}, limit {clock(max_duration)}).")
            return
        status = StatusMessage(messenger, msg)
        await status.update(RECEIVED)
        try:
            await messenger.notify_working(msg)
        except Exception:
            log.debug("notify_working failed", exc_info=True)
        try:
            samples = await decode(await msg.audio.fetch())
            await status.update(TRANSCRIBING)
            start = time.perf_counter()
            fast = await self.transcriber.fast(samples)
        except Exception as exc:
            log.exception("transcription failed")
            await status.finish([f"❌ Transcription failed: {exc}"])
            return
        seconds = len(samples) / SAMPLE_RATE
        log.info("%s: %.1fs audio, fast pass in %.1fs", msg.messenger, seconds, time.perf_counter() - start)

        # Send the fast transcript now, then edit in Whisper's better one. Only
        # when it fits one message: a split transcript can't be swapped as a whole.
        limit = messenger.max_message_length
        first = format_text(fast)
        refining = status.handle is not None and messenger.supports_edit and len(first) + len(REFINING) + 2 <= limit
        await status.finish([f"{first}\n\n{REFINING}"] if refining else split_text(first, limit))
        if not refining:
            return
        start = time.perf_counter()
        try:
            better = await self.transcriber.better(samples)
            log.info("%s: second pass in %.1fs", msg.messenger, time.perf_counter() - start)
        except Exception:
            log.exception("second pass failed")
            better = ""
        text = format_text(better) if better else first
        await status.finish([text if len(text) <= limit else first])

    # ---- lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        if not self.messengers:
            raise RuntimeError("no messengers configured")
        log.info("loading Parakeet")
        await self.transcriber.load()

        for m in self.messengers.values():
            try:
                await m.start()
                log.info("messenger %s started", m.name)
            except Exception:
                # Keep the others running; the health monitor reports and alerts.
                log.exception("messenger %s failed to start", m.name)

        monitor = HealthMonitor(self.messengers, self.config.health)
        app = web.Application(client_max_size=64 * 1024**2)
        app.add_routes([r for m in self.messengers.values() for r in m.routes()] + monitor.routes())
        runner = web.AppRunner(app)
        await runner.setup()
        http = self.config.http
        await web.TCPSite(runner, http.host, http.port).start()
        log.info("http server listening on %s:%s", http.host, http.port)

        try:
            await asyncio.gather(monitor.run(), *(m.run() for m in self.messengers.values()))
            await asyncio.Event().wait()  # webhook-only setups: run forever
        finally:
            await runner.cleanup()
            for task in list(self._tasks):
                task.cancel()
            for m in self.messengers.values():
                await m.close()
