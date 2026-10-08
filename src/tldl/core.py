"""Wires messengers to engines and handles chat commands."""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from .config import Config, split_type
from .messengers.base import Messenger
from .models import IncomingMessage, Transcript
from .engines.base import Engine
from .health import HealthMonitor
from .registry import ENGINES, MESSENGERS, load_class

log = logging.getLogger(__name__)

HELP = """\
Forward or send me a voice message and I'll transcribe it.

Commands:
/engines - list transcription engines
/engine <name> - use another engine in this chat
/lang <code|auto> - set the spoken language (e.g. en, de) or auto-detect
/details <on|off> - show engine, language and timing under transcripts
/help - this message"""


@dataclass
class ChatPrefs:
    engine: str | None = None
    language: str | None = None
    details: bool | None = None  # None: use config.show_details


STAGES = {
    "received": "📥 Received",
    "loading": "⏳ Loading model…",
    "queued": "⏳ Waiting for a free slot…",
    "transcribing": "✍️ Transcribing…",
}


class StatusMessage:
    """One message that shows progress and is finally replaced by the result.

    Messengers that can't edit get the first status only, then the result as
    new message(s). Status problems are logged, never fatal.
    """

    def __init__(self, messenger: Messenger, msg: IncomingMessage, enabled: bool = True) -> None:
        self.messenger = messenger
        self.msg = msg
        self.enabled = enabled
        self.handle: Any = None
        self.sent = False
        self.text: str | None = None

    async def update(self, text: str) -> None:
        if not self.enabled or text == self.text:
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
        """Replace the status with the first chunk; send the rest as new messages."""
        if chunks and self.handle is not None and self.messenger.supports_edit:
            try:
                await self.messenger.edit(self.msg, self.handle, chunks[0])
                chunks = chunks[1:]
            except Exception:
                # e.g. WhatsApp only allows edits for 15 minutes
                log.warning("final edit failed on %s, sending a new message", self.messenger.name, exc_info=True)
        for chunk in chunks:
            await self.messenger.reply(self.msg, chunk)


def build_engines(config: Config) -> dict[str, Engine]:
    result = {}
    for name, section in config.engines.items():
        kind, options = split_type(section)
        # An engine's own idle_unload (even null = never) wins over the global one.
        options.setdefault("idle_unload", config.idle_unload)
        result[name] = load_class(kind, ENGINES)(name, **options)
    return result


def split_text(text: str, limit: int) -> list[str]:
    """Split on whitespace into chunks of at most ``limit`` characters."""
    chunks = []
    while len(text) > limit:
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
        engines: dict[str, Engine] | None = None,
        messengers: dict[str, Messenger] | None = None,
    ) -> None:
        self.config = config
        self.engines = engines if engines is not None else build_engines(config)
        if messengers is None:
            messengers = {}
            for name, section in config.messengers.items():
                kind, options = split_type(section)
                messengers[name] = load_class(kind, MESSENGERS)(name, self.handle, **options)
        self.messengers = messengers
        self._prefs: dict[str, ChatPrefs] = {}
        self._tasks: set[asyncio.Task] = set()
        self._load_state()

    # ---- per-chat preferences -------------------------------------------------

    def _key(self, msg: IncomingMessage) -> str:
        return f"{msg.messenger}:{msg.chat_id}"

    def prefs(self, msg: IncomingMessage) -> ChatPrefs:
        return self._prefs.setdefault(self._key(msg), ChatPrefs())

    def engine_name(self, prefs: ChatPrefs) -> str:
        # Engines removed from the config fall back to the default.
        return prefs.engine if prefs.engine in self.engines else self.config.default_engine

    def _load_state(self) -> None:
        path = self.config.state_file
        if path and Path(path).exists():
            data = json.loads(Path(path).read_text())
            self._prefs = {k: ChatPrefs(**v) for k, v in data.get("chats", {}).items()}

    def _save_state(self) -> None:
        path = self.config.state_file
        if not path:
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps({"chats": {k: asdict(v) for k, v in self._prefs.items()}}, indent=2))
        tmp.replace(path)

    # ---- message handling -----------------------------------------------------

    async def handle(self, messenger: Messenger, msg: IncomingMessage) -> None:
        """Entry point for messengers. Returns quickly; work happens in a task."""
        task = asyncio.create_task(self._handle(messenger, msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, messenger: Messenger, msg: IncomingMessage) -> None:
        try:
            if not messenger.is_authorized(msg):
                log.info("unauthorized %s sender %s", msg.messenger, msg.sender_id)
                if msg.audio or (msg.text and msg.text.startswith("/")):
                    await messenger.reply(
                        msg,
                        f"Sorry, you're not allowed to use this bot. Your sender id is: {msg.sender_id}",
                    )
                return
            if msg.audio:
                await self._transcribe(messenger, msg)
            elif msg.text and msg.text.startswith("/"):
                await self._command(messenger, msg)
        except Exception:
            log.exception("failed to handle message on %s", msg.messenger)

    async def _transcribe(self, messenger: Messenger, msg: IncomingMessage) -> None:
        prefs = self.prefs(msg)
        engine_name = self.engine_name(prefs)
        engine = self.engines[engine_name]
        language = prefs.language or self.config.default_language
        max_duration = self.config.max_duration
        if max_duration and msg.audio.duration and msg.audio.duration > max_duration:
            await messenger.reply(msg, f"Audio is too long ({msg.audio.duration:.0f}s, limit {max_duration:.0f}s).")
            return
        status = StatusMessage(messenger, msg, enabled=self.config.status_updates)
        await status.update(STAGES["received"])
        try:
            await messenger.notify_working(msg)
        except Exception:
            log.debug("notify_working failed", exc_info=True)

        async def on_stage(stage: str) -> None:
            await status.update(STAGES.get(stage, stage))

        try:
            audio = await msg.audio.fetch()
            transcript = await engine.transcribe(audio, language, on_stage=on_stage)
        except Exception as exc:
            log.exception("transcription with %s failed", engine_name)
            await status.finish([f"❌ Transcription failed ({engine_name}): {exc}"])
            return
        log.info(
            "%s: transcribed %.1fs audio with %s in %.1fs",
            msg.messenger, transcript.duration or 0, engine_name, transcript.elapsed or 0,
        )
        details = prefs.details if prefs.details is not None else self.config.show_details
        await status.finish(split_text(self.format(transcript, details), messenger.max_message_length))

    def format(self, t: Transcript, details: bool = False) -> str:
        text = t.text or "(no speech detected)"
        if not details:
            return text
        info = [t.engine]
        if t.language:
            info.append(t.language)
        if t.duration:
            info.append(f"{t.duration:.0f}s audio")
        if t.elapsed is not None:
            info.append(f"took {t.elapsed:.1f}s")
        return f"{text}\n\n[{' · '.join(info)}]"

    async def _command(self, messenger: Messenger, msg: IncomingMessage) -> None:
        cmd, _, arg = msg.text.strip().partition(" ")
        cmd = cmd.lower().split("@", 1)[0]  # telegram adds @botname in groups
        arg = arg.strip()
        prefs = self.prefs(msg)
        if cmd in ("/start", "/help"):
            await messenger.reply(msg, HELP)
        elif cmd == "/engines":
            current = self.engine_name(prefs)
            lines = []
            for name, engine in self.engines.items():
                marker = "▶ " if name == current else "• "
                lines.append(marker + name + (f" - {engine.description}" if engine.description else ""))
            await messenger.reply(msg, "\n".join(lines))
        elif cmd == "/engine":
            if arg not in self.engines:
                await messenger.reply(msg, f"Unknown engine {arg!r}. Available: {', '.join(self.engines)}")
                return
            prefs.engine = arg
            self._save_state()
            await messenger.reply(msg, f"Using {arg} in this chat.")
        elif cmd == "/lang":
            prefs.language = None if arg in ("", "auto") else arg.lower()
            self._save_state()
            await messenger.reply(msg, f"Language: {prefs.language or 'auto-detect'}")
        elif cmd == "/details":
            if arg not in ("on", "off"):
                await messenger.reply(msg, "Usage: /details on|off")
                return
            prefs.details = arg == "on"
            self._save_state()
            await messenger.reply(msg, f"Details {arg}.")

    # ---- lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        if not self.messengers:
            raise RuntimeError("no messengers configured")
        if self.config.preload:
            log.info("preloading engine %s", self.config.default_engine)
            engine = self.engines[self.config.default_engine]
            await engine.ensure_loaded()
            engine.schedule_unload()

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
            for engine in self.engines.values():
                await engine.close()
