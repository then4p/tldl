import asyncio

from tldl.core import Bot, split_text

from .conftest import NoEditMessenger, text, voice


async def settle(bot):
    while bot._tasks:
        await asyncio.gather(*bot._tasks)


async def test_transcribes_voice(make_bot):
    bot, m = make_bot()
    msg, _ = voice()
    await bot.handle(m, msg)
    await settle(bot)
    assert m.replies == ["hello world"]


async def test_details_off_by_default_and_toggle(make_bot):
    bot, m = make_bot()
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"
    await bot.handle(m, text("/details on"))
    await settle(bot)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies[-1].startswith("hello world\n\n[a · en · 3s audio · took ")
    await bot.handle(m, text("/details off"))
    await settle(bot)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"


async def test_private_mode_rejects_non_vips_without_downloading(make_bot):
    bot, m = make_bot()  # access.public defaults to False
    msg, fetched = voice(sender="999")
    await bot.handle(m, msg)
    await settle(bot)
    assert not fetched
    assert "private" in m.replies[0] and "999" in m.replies[0]


async def test_from_self_counts_as_vip(make_bot):
    bot, m = make_bot()
    await bot.handle(m, voice(sender="me", from_self=True)[0])
    await settle(bot)
    assert m.replies == ["hello world"]


async def test_engine_and_lang_commands_persist(make_bot):
    bot, m = make_bot()
    for t in ("/engine b", "/lang de"):
        await bot.handle(m, text(t))
        await settle(bot)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies[-1] == "from b"
    assert bot.engines["b"].calls[0][1] == "de"
    # other chats still use the default
    await bot.handle(m, voice(chat="c2")[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"
    # state survives a restart
    bot2 = Bot(bot.config, engines=bot.engines, messengers={})
    assert bot2._prefs["fake:c1"].engine == "b"


async def test_unknown_engine(make_bot):
    bot, m = make_bot()
    await bot.handle(m, text("/engine nope"))
    await settle(bot)
    assert "Unknown engine" in m.replies[0]


async def test_failure_is_reported(make_bot):
    bot, m = make_bot()
    bot.engines["a"].fail = True
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert "Transcription failed (a): boom" in m.replies[-1]


async def test_max_duration(make_bot):
    bot, m = make_bot(max_duration=2)
    msg, fetched = voice(duration=10)
    await bot.handle(m, msg)
    await settle(bot)
    assert "too long" in m.replies[0] and not fetched


async def test_long_transcripts_are_split(make_bot):
    bot, m = make_bot()
    bot.engines["a"].text = "word " * 30
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert len(m.replies) > 1 and all(len(r) <= 50 for r in m.replies)


def test_split_text():
    assert split_text("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]
    assert split_text("x" * 10, 4) == ["xxxx", "xxxx", "xx"]
    assert split_text("", 5) == []


async def test_status_message_is_edited_into_transcript(make_bot):
    bot, m = make_bot()
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.history == [
        ("send", "📥 Received"),
        ("edit", "⏳ Loading model…"),
        ("edit", "✍️ Transcribing…"),
        ("edit", "hello world"),
    ]
    assert m.replies == ["hello world"]
    # model is loaded now, so no loading stage the second time
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert [t for _, t in m.history[4:]] == ["📥 Received", "✍️ Transcribing…", "hello world"]


async def test_queued_stage(make_bot):
    bot, m = make_bot()
    engine = bot.engines["a"]
    await engine.ensure_loaded()
    async with engine._semaphore:  # occupy the only slot
        await bot.handle(m, voice()[0])
        for _ in range(20):
            await asyncio.sleep(0)
        assert m.replies == ["⏳ Waiting for a free slot…"]
    await settle(bot)
    assert m.replies == ["hello world"]


async def test_failure_replaces_status(make_bot):
    bot, m = make_bot()
    bot.engines["a"].fail = True
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["❌ Transcription failed (a): boom"]


async def test_without_edit_support_sends_separate_messages(make_bot):
    bot, m = make_bot(messenger_cls=NoEditMessenger)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["📥 Received", "hello world"]


async def test_status_updates_disabled(make_bot):
    bot, m = make_bot(status_updates=False)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.history == [("send", "hello world")]


async def test_broken_status_does_not_block_transcript(make_bot):
    bot, m = make_bot()

    async def broken_edit(msg, handle, text):
        raise RuntimeError("edit window expired")

    m.edit = broken_edit
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["📥 Received", "hello world"]


async def test_removed_engine_falls_back_to_default(make_bot):
    bot, m = make_bot()
    bot.prefs(text("x")).engine = "whisperx"  # e.g. from an old state file
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert m.replies == ["hello world"]


def test_details_format(make_bot):
    from tldl.models import Transcript

    bot, _ = make_bot()
    t = Transcript(text="hi", engine="a", language="de", duration=3.2, elapsed=0.5)
    assert bot.format(t) == "hi"
    assert bot.format(t, details=True) == "hi\n\n[a · de · 3s audio · took 0.5s]"


async def test_idle_unload(make_bot):
    bot, m = make_bot()
    engine = bot.engines["a"]
    engine.idle_unload = 0.05
    closed = []

    async def close():
        closed.append(True)

    engine.close = close
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert engine.loaded
    await asyncio.sleep(0.1)
    assert not engine.loaded and closed == [True]
    # used again: loads again and shows the loading stage
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert engine.loaded and "⏳ Loading model…" in [t for _, t in m.history[-4:]]


async def test_no_unload_while_busy(make_bot):
    bot, m = make_bot()
    engine = bot.engines["a"]
    await engine.ensure_loaded()
    engine._active = 1  # a transcription is in flight
    await engine.unload()
    assert engine.loaded


async def test_preloaded_engine_unloads_when_never_used(make_bot):
    bot, m = make_bot(preload=True)
    engine = bot.engines["a"]
    engine.idle_unload = 0.05
    m.start = m.run = lambda: asyncio.sleep(0)
    run = asyncio.create_task(bot.run())
    await asyncio.sleep(0.02)
    assert engine.loaded
    await asyncio.sleep(0.1)
    assert not engine.loaded
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)


async def test_audio_is_discarded_whatever_happens(make_bot):
    for sender, fail in (("1", False), ("1", True), ("999", False)):  # ok, failure, rejected
        bot, m = make_bot()
        bot.engines["a"].fail = fail
        msg, _ = voice(sender=sender)
        discarded = []

        async def discard():
            discarded.append(True)

        msg.audio.discard = discard
        await bot.handle(m, msg)
        await settle(bot)
        assert discarded == [True], (sender, fail)
