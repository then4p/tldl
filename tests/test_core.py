import asyncio

from tldl.core import Bot, paragraphs, split_text

from .conftest import NoEditMessenger, text, voice


async def settle(bot):
    while bot._tasks:
        await asyncio.gather(*bot._tasks)


async def test_fast_transcript_then_better_one_edited_in(make_bot):
    bot, m = make_bot()
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.history == [
        ("send", "📥 Received"),
        ("edit", "✍️ Transcribing…"),
        ("edit", "hello world\n\n🔍 Double-checking…"),
        ("edit", "hello world, better"),
    ]
    assert m.replies == ["hello world, better"]


async def test_second_pass_failure_keeps_the_fast_transcript(make_bot):
    bot, m = make_bot()
    bot.transcriber.better_fail = True
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["hello world"]


async def test_second_pass_hearing_nothing_keeps_the_fast_transcript(make_bot):
    bot, m = make_bot()
    bot.transcriber.better_text = ""
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["hello world"]


async def test_failure_replaces_status(make_bot):
    bot, m = make_bot()
    bot.transcriber.fail = True
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["❌ Transcription failed: boom"]


async def test_max_duration(make_bot):
    bot, m = make_bot(max_duration=2)
    msg, fetched = voice(duration=10)
    await bot.handle(m, msg)
    await settle(bot)
    assert "too long" in m.replies[0] and not fetched


async def test_long_transcripts_are_split_without_second_pass(make_bot):
    bot, m = make_bot()
    bot.transcriber.fast_text = "word " * 30
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert len(m.replies) > 1 and all(len(r) <= 50 for r in m.replies)
    assert bot.transcriber.better_calls == 0


async def test_without_edit_support_sends_fast_transcript_only(make_bot):
    bot, m = make_bot(messenger_cls=NoEditMessenger)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["📥 Received", "hello world"]
    assert bot.transcriber.better_calls == 0


async def test_broken_status_does_not_block_transcript(make_bot):
    bot, m = make_bot()

    async def broken_edit(msg, handle, text):
        raise RuntimeError("edit window expired")

    m.edit = broken_edit
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["📥 Received", "hello world\n\n🔍 Double-checking…", "hello world, better"]


async def test_audio_is_discarded_whatever_happens(make_bot):
    for fail in (False, True):
        bot, m = make_bot()
        bot.transcriber.fail = fail
        msg, _ = voice()
        discarded = []

        async def discard():
            discarded.append(True)

        msg.audio.discard = discard
        await bot.handle(m, msg)
        await settle(bot)
        assert discarded == [True], fail


async def test_welcome_once_per_sender(make_bot):
    bot, m = make_bot(welcomed=False)
    await bot.handle(m, text("/start", sender="7"))
    await settle(bot)
    assert len(m.replies) == 1 and m.replies[0].startswith("👋 Welcome to tldl")
    await bot.handle(m, voice(sender="7")[0])
    await settle(bot)
    assert m.replies[-1] == "hello world, better"
    # remembered across restarts
    assert "fake:7" in Bot(bot.config, transcriber=bot.transcriber, messengers={})._welcomed


async def test_welcome_then_first_voice_message(make_bot):
    bot, m = make_bot(welcomed=False)
    await bot.handle(m, voice(sender="8")[0])
    await settle(bot)
    assert "Welcome" in m.replies[0] and m.replies[-1] == "hello world, better"


async def test_help(make_bot):
    bot, m = make_bot()
    await bot.handle(m, text("/help"))
    await settle(bot)
    assert "github.com/then4p/tldl" in m.replies[0]


def test_split_text():
    assert split_text("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]
    assert split_text("x" * 10, 4) == ["xxxx", "xxxx", "xx"]
    assert split_text("", 5) == []
    assert split_text("aa bb\n\ncc dd ee", 12) == ["aa bb", "cc dd ee"]  # prefers paragraph breaks


def test_paragraphs():
    assert paragraphs("Short. Stays one paragraph.") == "Short. Stays one paragraph."
    assert paragraphs("One two. Three? Four! Five.", min_chars=8) == "One two.\n\nThree? Four!\n\nFive."
    assert paragraphs("No punctuation at all " * 50) == ("No punctuation at all " * 50).strip()
