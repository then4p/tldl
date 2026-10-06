import pytest

from tldl.config import Config
from tldl.core import Bot
from tldl.messengers.base import Messenger
from tldl.models import Audio, AudioRef, IncomingMessage, Transcript
from tldl.engines.base import Engine


class FakeEngine(Engine):
    def __init__(self, name, *, text="hello world", fail=False, **kw):
        super().__init__(name, **kw)
        self.text, self.fail, self.calls = text, fail, []

    async def _transcribe(self, audio, language):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((audio, language))
        return Transcript(text=f" {self.text} ", language=language or "en", duration=3.0)


class FakeMessenger(Messenger):
    """Chat as a list of message texts; ``history`` records every send/edit."""

    max_message_length = 50
    supports_edit = True

    def __init__(self, name, handler, **kw):
        super().__init__(name, handler, **kw)
        self.replies = []
        self.history = []

    async def reply(self, msg, text):
        self.replies.append(text)
        self.history.append(("send", text))
        return len(self.replies) - 1

    async def edit(self, msg, handle, text):
        self.replies[handle] = text
        self.history.append(("edit", text))


class NoEditMessenger(FakeMessenger):
    supports_edit = False

    async def edit(self, msg, handle, text):
        raise AssertionError("edit called on a messenger without edit support")


def voice(sender="1", chat="c1", duration=3.0, from_self=False):
    fetched = []

    async def fetch():
        fetched.append(True)
        return Audio(b"data", "audio/ogg")

    msg = IncomingMessage("fake", chat, sender, audio=AudioRef(fetch, "audio/ogg", duration), from_self=from_self)
    return msg, fetched


def text(t, sender="1", chat="c1"):
    return IncomingMessage("fake", chat, sender, text=t)


@pytest.fixture
def make_bot(tmp_path):
    def make(messenger_cls=FakeMessenger, **cfg):
        engines = {"a": FakeEngine("a"), "b": FakeEngine("b", text="from b")}
        config = Config(
            engines={"a": {"type": "x"}, "b": {"type": "x"}},
            state_file=str(tmp_path / "state.json"),
            **cfg,
        )
        bot = Bot(config, engines=engines, messengers={})
        messenger = messenger_cls("fake", bot.handle, allowed_senders=["1"])
        bot.messengers = {"fake": messenger}
        return bot, messenger

    return make
