from services.chat_support.flow_result import (
    STATUS_CONTINUE,
    STATUS_REPLIED,
    ChatFlowResult,
)


def test_default_status_is_continue():
    result = ChatFlowResult(reply_text="hi")
    assert result.status == STATUS_CONTINUE
    assert result.handled is False
    assert result.text_for_memory() == "hi"


def test_replied_uses_memory_text_when_present():
    result = ChatFlowResult(
        status=STATUS_REPLIED,
        reply_text="",
        memory_text="sent an image",
        emotion="neutral",
    )
    assert result.handled is True
    assert result.text_for_memory() == "sent an image"


def test_invalid_status_is_rejected():
    try:
        ChatFlowResult(status="handled")
    except ValueError as exc:
        assert "invalid status" in str(exc)
    else:
        raise AssertionError("expected invalid status to raise")
