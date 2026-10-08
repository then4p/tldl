"""Messenger plugin interface."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..models import Health, IncomingMessage

MessageHandler = Callable[["Messenger", IncomingMessage], Awaitable[None]]


class Messenger(ABC):
    """Base class for chat integrations.

    A messenger turns platform events into :class:`IncomingMessage` objects and
    passes them to the handler given in ``__init__``; it knows how to reply.
    It does no authorization or transcription itself - the core does that.

    Two styles are supported:

    * polling / socket based: implement :meth:`run` as a long-running loop;
    * webhook based: return aiohttp routes from :meth:`routes`; the core serves
      them on its shared HTTP server.
    """

    #: Replies longer than this are split into several messages.
    max_message_length = 4000
    #: Whether :meth:`edit` works. If so, the bot shows one status message
    #: ("received", "transcribing", ...) and replaces it with the transcript.
    supports_edit = False

    def __init__(
        self,
        name: str,
        handler: MessageHandler,
        *,
        allowed_senders: list[str | int] | None = None,
        allow_all: bool = False,
    ) -> None:
        self.name = name
        self.handler = handler
        self.allowed_senders = {str(s) for s in (allowed_senders or [])}
        self.allow_all = allow_all
        #: Set by the health monitor; see :meth:`health_changed`.
        self.on_health_change: Callable[[], None] | None = None

    def is_authorized(self, msg: IncomingMessage) -> bool:
        return self.allow_all or msg.from_self or msg.sender_id in self.allowed_senders

    async def start(self) -> None:
        """Open clients, verify credentials. Called before :meth:`run`."""

    async def run(self) -> None:
        """Receive messages until cancelled. Webhook messengers can leave this as is."""

    def routes(self) -> list[web.RouteDef]:
        """HTTP routes to mount on the shared web server (for webhooks)."""
        return []

    @abstractmethod
    async def reply(self, msg: IncomingMessage, text: str) -> Any:
        """Send ``text`` to the chat ``msg`` came from, quoting it if possible.

        Returns a handle for the sent message (e.g. its id) that :meth:`edit`
        accepts, or None.
        """

    async def edit(self, msg: IncomingMessage, handle: Any, text: str) -> None:
        """Replace the text of a message previously sent with :meth:`reply`."""
        raise NotImplementedError

    async def send(self, chat_id: str, text: str) -> Any:
        """Send ``text`` to ``chat_id`` on its own (used for health alerts)."""
        raise NotImplementedError

    async def check_health(self) -> Health:
        """Check that the messenger can still receive and send messages.

        Called periodically by the health monitor. Return a failing
        :class:`Health` with a detail that says what's wrong and how to fix it.
        """
        return Health(True)

    def health_changed(self) -> None:
        """Ask for an immediate health check, e.g. when the platform reports a status change."""
        if self.on_health_change:
            self.on_health_change()

    async def notify_working(self, msg: IncomingMessage) -> None:
        """Optional "typing..." indicator / reaction while transcribing."""

    async def close(self) -> None:
        """Release resources."""

    async def dispatch(self, msg: IncomingMessage) -> None:
        await self.handler(self, msg)
