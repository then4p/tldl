"""Data types shared between messengers, engines and the core."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Audio:
    """Raw audio bytes as received from a messenger (usually ogg/opus or m4a)."""

    data: bytes
    mime_type: str = "application/octet-stream"
    filename: str | None = None

    @property
    def suffix(self) -> str:
        if self.filename and "." in self.filename:
            return "." + self.filename.rsplit(".", 1)[1]
        return _MIME_SUFFIXES.get(self.mime_type.split(";")[0].strip(), ".bin")


_MIME_SUFFIXES = {
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
}


@dataclass
class AudioRef:
    """A not-yet-downloaded audio attachment.

    Messengers hand these to the core so nothing is downloaded for senders that
    are not authorized.
    """

    fetch: Callable[[], Awaitable[Audio]]
    mime_type: str | None = None
    duration: float | None = None


@dataclass
class IncomingMessage:
    messenger: str
    chat_id: str
    sender_id: str
    text: str | None = None
    audio: AudioRef | None = None
    # True when the message comes from the bot account itself (e.g. a
    # "Note to Self" / "Message yourself" chat on a linked device).
    from_self: bool = False
    # Messenger-specific data needed to reply (message ids, timestamps, ...).
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Transcript:
    text: str
    engine: str = ""
    language: str | None = None
    duration: float | None = None  # audio length in seconds
    elapsed: float | None = None  # wall time spent transcribing


@dataclass
class Health:
    ok: bool
    detail: str = ""  # what's wrong and how to fix it (or a short status when ok)
