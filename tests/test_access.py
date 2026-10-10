import asyncio

import pytest

from tldl.config import AccessConfig, load_config
from tldl.engines.base import UnsupportedLanguage
from tldl.engines.onnx import EU_LANGUAGES, pick_language
from tldl.limits import Budget, clock

from .conftest import text, voice


async def settle(bot):
    while bot._tasks:
        await asyncio.gather(*bot._tasks)


def public(**kw):
    return {"access": AccessConfig(public=True, **kw)}


# ---- welcome ------------------------------------------------------------------

async def test_welcome_once_per_sender_with_id(make_bot):
    bot, m = make_bot(welcomed=False, **public())
    await bot.handle(m, text("hello", sender="7"))
    await settle(bot)
    assert len(m.replies) == 1
    assert m.replies[0].startswith("👋 Welcome to tldl") and "Your chat ID: 7" in m.replies[0]
    assert "5:00 minutes of audio per day" in m.replies[0]
    await bot.handle(m, text("hello again", sender="7"))
    await settle(bot)
    assert len(m.replies) == 1  # only the first message ever


async def test_welcome_then_transcribe_first_voice_message(make_bot):
    bot, m = make_bot(welcomed=False, show_details=False)
    await bot.handle(m, voice()[0])
    await settle(bot)
    assert "VIP list" in m.replies[0] and m.replies[-1] == "hello world"


async def test_first_start_command_gets_only_the_welcome(make_bot):
    bot, m = make_bot(welcomed=False, **public())
    await bot.handle(m, text("/start", sender="8"))
    await settle(bot)
    assert len(m.replies) == 1 and "Welcome" in m.replies[0]


async def test_welcome_is_remembered_across_restarts(make_bot):
    from tldl.core import Bot

    bot, m = make_bot(welcomed=False, **public())
    await bot.handle(m, text("hi", sender="9"))
    await settle(bot)
    assert "fake:9" in Bot(bot.config, engines=bot.engines, messengers={})._welcomed


# ---- VIPs vs free users ---------------------------------------------------------

async def test_free_users_get_default_engine_vips_get_vip_engine(make_bot):
    bot, m = make_bot(show_details=False, vip_engine="b", **public())
    await bot.handle(m, voice(sender="1")[0])  # "1" is the fixture's VIP
    await settle(bot)
    assert m.replies[-1] == "from b"
    await bot.handle(m, voice(sender="2")[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"


async def test_free_users_cannot_switch_engines(make_bot):
    bot, m = make_bot(**public())
    await bot.handle(m, text("/engine b", sender="2"))
    await settle(bot)
    assert "for VIPs" in m.replies[-1]


async def test_budget_blocks_free_users_and_refunds_failures(make_bot):
    bot, m = make_bot(show_details=False, **public(daily_limit=5, daily_total=100))
    await bot.handle(m, voice(sender="2", duration=3.0)[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"
    await bot.handle(m, voice(sender="2", duration=3.0)[0])
    await settle(bot)
    assert "you have 0:02 of your 0:05 free minutes left" in m.replies[-1]
    # failures don't cost anything
    bot.engines["a"].fail = True
    await bot.handle(m, voice(sender="3", duration=3.0)[0])
    await settle(bot)
    assert bot.budget.user_left("fake:3") == 5
    # VIPs are never limited
    bot.engines["a"].fail = False
    for _ in range(3):
        await bot.handle(m, voice(sender="1", duration=3.0)[0])
    await settle(bot)
    assert m.replies[-1] == "hello world"


async def test_unsupported_language_is_explained_and_free(make_bot):
    bot, m = make_bot(**public(daily_limit=60))

    async def japanese(audio, language):
        raise UnsupportedLanguage("ja")

    bot.engines["a"]._transcribe = japanese
    await bot.handle(m, voice(sender="2")[0])
    await settle(bot)
    assert "sounds like Japanese" in m.replies[-1]
    assert bot.budget.user_left("fake:2") == 60


async def test_info(make_bot):
    bot, m = make_bot(**public(daily_limit=300, daily_total=7200))
    await bot.handle(m, text("/info", sender="1"))
    await bot.handle(m, text("/info", sender="2"))
    await settle(bot)
    vip, free = m.replies[-2:]
    assert "VIP: Tester" in vip and "Unlimited" in vip and "Your chat ID: 1" in vip
    assert "5:00 of 5:00 left" in free and "midnight (Berlin time)" in free and "Your chat ID: 2" in free


# ---- budget and language picking ----------------------------------------------

def test_budget_shared_pool():
    b = Budget(AccessConfig(daily_limit=300, daily_total=400), {})
    assert b.reserve("a", 250) is None and b.reserve("b", 100) is None
    refusal = b.reserve("c", 100)
    assert "0:50 of today's shared free minutes" in refusal
    assert b.reserve("c", 50) is None
    assert "used up for everyone" in b.reserve("d", 10)


def test_budget_resets_daily():
    state = {"date": "2000-01-01", "users": {"a": 300}, "total": 300}
    b = Budget(AccessConfig(daily_limit=300), state)
    assert b.user_left("a") == 300 and state["date"] != "2000-01-01"


def test_clock():
    assert clock(0) == "0:00" and clock(65) == "1:05" and clock(299.2) == "5:00"


def test_pick_language():
    assert pick_language({"de": 5.0, "en": 3.0, "ja": 1.0}, EU_LANGUAGES) == "de"
    # Norwegian isn't supported: the overall top guess decides
    with pytest.raises(UnsupportedLanguage) as exc:
        pick_language({"no": 5.0, "da": 4.5}, EU_LANGUAGES)
    assert exc.value.language == "no"


def test_old_allowlist_config_is_explained(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("engines: {a: {}}\nmessengers: {tg: {type: telegram, token: x, allowed_senders: [1]}}\n")
    with pytest.raises(ValueError, match="replaced by 'vips'"):
        load_config(p)


async def test_messenger_can_stay_vip_only_in_public_mode(make_bot):
    bot, m = make_bot(welcomed=False, **public())
    m.public = False  # e.g. WhatsApp
    msg, fetched = voice(sender="2")
    await bot.handle(m, msg)
    await settle(bot)
    assert "This bot is private" in m.replies[0] and "Your chat ID: 2" in m.replies[0]
    assert not fetched and "private" in m.replies[-1]
    await bot.handle(m, voice(sender="1")[0])  # VIPs still work
    await settle(bot)
    assert m.replies[-1].startswith("hello world")
