import pytest

from tldl.config import Config
from tldl.core import Bot
from tldl.messengers.base import Messenger
from tldl.models import Audio, AudioRef, IncomingMessage


class FakeTranscriber:
    def __init__(self, fast="hello world", better="hello world, better"):
        self.fast_text, self.better_text, self.fail, self.better_fail = fast, better, False, False
        self.better_calls = 0

    async def load(self):
        pass

    async def fast(self, samples):
        if self.fail:
            raise RuntimeError("boom")
        return self.fast_text

    async def better(self, samples):
        self.better_calls += 1
        if self.better_fail:
            raise RuntimeError("whisper boom")
        return self.better_text


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
    def make(messenger_cls=FakeMessenger, welcomed=True, **cfg):
        config = Config(state_file=str(tmp_path / "state.json"), **cfg)
        bot = Bot(config, transcriber=FakeTranscriber(), messengers={})
        messenger = messenger_cls("fake", bot.handle)
        bot.messengers = {"fake": messenger}
        if welcomed:  # most tests aren't about the first-contact welcome
            bot._welcomed |= {"fake:1", "fake:999", "fake:me", "fake:2", "fake:3"}
        return bot, messenger

    return make


@pytest.fixture(autouse=True)
def fake_decode(monkeypatch):
    """No ffmpeg in tests: every clip decodes to 3 s of silence."""
    import numpy as np

    async def decode(audio):
        return np.zeros(3 * 16000, dtype=np.float32)

    monkeypatch.setattr("tldl.core.decode", decode)
