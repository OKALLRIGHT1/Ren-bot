import asyncio

import pytest

from services.chat_support.qq_private_buffer import QqPrivateMessageBuffer


def _ctx(message_id, text="", *, images=None, reply=None):
    return {
        "source": "qq_gateway",
        "channel_meta": {
            "adapter": "napcat_qq",
            "session_id": "private:10001",
            "message_type": "private",
            "message_id": message_id,
            "sender_name": "Tester",
            "images": list(images or []),
            "has_image": bool(images),
            "image_count": len(images or []),
            "reply": dict(reply or {}),
            "components": [{"type": "text", "text": text, "data": {}}],
        },
    }


@pytest.mark.asyncio
async def test_merges_consecutive_private_text_messages():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.03,
        short_debounce_sec=0.03,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
    )
    first_ctx = _ctx("m1", "第一句")
    second_ctx = _ctx("m2", "第二句")

    first = asyncio.create_task(buffer.wait("第一句", first_ctx))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(buffer.wait("第二句", second_ctx))
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result is None
    assert second_result is not None
    assert second_result.text == "第一句\n第二句"
    assert second_ctx["qq_buffered_count"] == 2
    assert second_ctx["qq_buffered_messages"] == ["第一句", "第二句"]
    assert second_ctx["channel_meta"]["message_id"] == "m2"


@pytest.mark.asyncio
async def test_merges_images_into_latest_context():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.03,
        short_debounce_sec=0.03,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
    )
    first_ctx = _ctx("m1", "看这个", images=[{"url": "https://example.test/1.png"}])
    second_ctx = _ctx("m2", "还有这个", images=[{"url": "https://example.test/2.png"}])

    first = asyncio.create_task(buffer.wait("看这个", first_ctx))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(buffer.wait("还有这个", second_ctx))
    _, result = await asyncio.gather(first, second)

    assert result is not None
    assert result.text == "看这个\n还有这个"
    assert second_ctx["channel_meta"]["has_image"] is True
    assert second_ctx["channel_meta"]["image_count"] == 2
    assert [item["url"] for item in second_ctx["channel_meta"]["images"]] == [
        "https://example.test/1.png",
        "https://example.test/2.png",
    ]


@pytest.mark.asyncio
async def test_command_bypasses_and_flushes_pending_buffer():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.2,
        short_debounce_sec=0.2,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
    )
    pending_ctx = _ctx("m1", "先说一句")
    command_ctx = _ctx("m2", "/help")

    pending = asyncio.create_task(buffer.wait("先说一句", pending_ctx))
    await asyncio.sleep(0.01)
    command_result = await buffer.wait("/help", command_ctx)
    pending_result = await pending

    assert command_result.text == "/help"
    assert command_result.bypassed is True
    assert pending_result is not None
    assert pending_result.text == "先说一句"


@pytest.mark.asyncio
async def test_recall_removes_pending_message_by_id():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.03,
        short_debounce_sec=0.03,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
    )
    first_ctx = _ctx("m1", "会撤回")
    second_ctx = _ctx("m2", "留下")

    first = asyncio.create_task(buffer.wait("会撤回", first_ctx))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(buffer.wait("留下", second_ctx))
    await asyncio.sleep(0.01)
    removed = await buffer.handle_recall("private:10001", "m1")
    _, result = await asyncio.gather(first, second)

    assert removed == 1
    assert result is not None
    assert result.text == "留下"
    assert second_ctx["qq_buffered_messages"] == ["留下"]


@pytest.mark.asyncio
async def test_typing_start_extends_deadline_until_typing_stop():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.03,
        short_debounce_sec=0.03,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
    )
    ctx = _ctx("m1", "等等")

    task = asyncio.create_task(buffer.wait("等等", ctx))
    await asyncio.sleep(0.01)
    assert await buffer.handle_typing("private:10001", is_typing=True) is True
    await asyncio.sleep(0.06)
    assert task.done() is False
    assert await buffer.handle_typing("private:10001", is_typing=False) is True
    result = await asyncio.wait_for(task, timeout=0.2)

    assert result is not None
    assert result.text == "等等"


@pytest.mark.asyncio
async def test_includes_quoted_message_context_when_present():
    buffer = QqPrivateMessageBuffer(
        enabled=True,
        debounce_sec=0.03,
        short_debounce_sec=0.03,
        max_typing_wait_sec=0.2,
        max_items=12,
        max_text_chars=2400,
        enable_reply_context=True,
    )
    ctx = _ctx(
        "m1",
        "我回复的是这个",
        reply={"message_id": "r1", "sender_name": "对方", "text": "被引用的话"},
    )

    result = await buffer.wait("我回复的是这个", ctx)

    assert result is not None
    assert '<quoted_message sender="对方">被引用的话</quoted_message>' in result.text
    assert result.text.endswith("我回复的是这个")
