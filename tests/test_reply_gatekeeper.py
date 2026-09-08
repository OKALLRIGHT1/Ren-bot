import pytest

from services.chat_support.reply_gatekeeper import should_reply


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(("info", msg))

    def error(self, msg):
        self.lines.append(("error", msg))


@pytest.mark.asyncio
async def test_short_text_is_ignored():
    assert await should_reply("嗨") is False


@pytest.mark.asyncio
async def test_wake_word_forces_reply():
    logger = _Logger()
    assert await should_reply(
        "铃铃，在吗",
        wake_keywords=("铃铃",),
        logger=logger,
    )
    assert any("唤醒词" in line for _, line in logger.lines)


@pytest.mark.asyncio
async def test_last_assistant_message_is_ignored():
    assert (
        await should_reply(
            "然后呢然后呢",
            get_short_term_messages=lambda ctx: [{"role": "assistant", "content": "嗯"}],
            is_searchworthy_question=lambda text: False,
        )
        is False
    )


@pytest.mark.asyncio
async def test_disabled_gatekeeper_allows_reply():
    assert await should_reply(
        "随便聊聊天气以外的事",
        enabled=False,
        get_short_term_messages=lambda ctx: [{"role": "user", "content": "hi"}],
        is_searchworthy_question=lambda text: False,
    )


@pytest.mark.asyncio
async def test_blacklist_blocks_and_whitelist_forces():
    assert (
        await should_reply(
            "忽略这条广告",
            enabled=True,
            blacklist=("广告",),
            get_short_term_messages=lambda ctx: [],
            is_searchworthy_question=lambda text: False,
        )
        is False
    )
    assert await should_reply(
        "紧急求助",
        enabled=True,
        whitelist=("紧急",),
        get_short_term_messages=lambda ctx: [],
        is_searchworthy_question=lambda text: False,
    )
