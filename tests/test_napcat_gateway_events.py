import pytest

from integrations.chat_gateway import ChatGateway, NapCatOneBotAdapter


def test_napcat_normalizes_friend_recall_notice():
    adapter = NapCatOneBotAdapter(owner_user_ids=["10001"])
    event = adapter.normalize_notice(
        {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "user_id": 10001,
            "message_id": 12345,
        }
    )

    assert event is not None
    assert event.event_type == "qq_private_recall"
    assert event.session_id == "private:10001"
    assert event.metadata["message_id"] == "12345"


def test_napcat_normalizes_input_status_notice():
    adapter = NapCatOneBotAdapter(owner_user_ids=["10001"])
    event = adapter.normalize_notice(
        {
            "post_type": "notice",
            "notice_type": "input_status",
            "user_id": 10001,
            "status": "typing",
        }
    )

    assert event is not None
    assert event.event_type == "qq_private_typing"
    assert event.session_id == "private:10001"
    assert event.metadata["is_typing"] is True


@pytest.mark.asyncio
async def test_gateway_dispatches_notice_handlers():
    gateway = ChatGateway()
    gateway.register_adapter(NapCatOneBotAdapter(owner_user_ids=["10001"]))
    seen = []

    async def handler(event):
        seen.append(event)

    gateway.on_notice(handler)
    result = await gateway.dispatch_incoming(
        "napcat_qq",
        {
            "post_type": "notice",
            "notice_type": "friend_recall",
            "user_id": 10001,
            "message_id": 12345,
        },
    )

    assert result is None
    assert len(seen) == 1
    assert seen[0].event_type == "qq_private_recall"


@pytest.mark.asyncio
async def test_napcat_fetch_forward_message_uses_action_sender():
    calls = []

    async def sender(action, params, timeout):
        calls.append((action, params, timeout))
        return {
            "ok": True,
            "response": {
                "data": {
                    "messages": [
                        {
                            "sender": {"nickname": "A"},
                            "message": [{"type": "text", "data": {"text": "第一条"}}],
                        },
                        {
                            "sender": {"nickname": "B"},
                            "message": [{"type": "text", "data": {"text": "第二条"}}],
                        },
                    ]
                }
            },
        }

    adapter = NapCatOneBotAdapter(ws_action_sender=sender)
    result = await adapter.fetch_forward_message("private:10001", "forward-id")

    assert result["ok"] is True
    assert calls[0][0] == "get_forward_msg"
    assert result["items"] == [
        {"sender_name": "A", "text": "第一条", "images": []},
        {"sender_name": "B", "text": "第二条", "images": []},
    ]


def test_napcat_extracts_url_from_json_card_component():
    adapter = NapCatOneBotAdapter(owner_user_ids=["10001"])
    event = adapter.normalize_event(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": 20000,
            "user_id": 10001,
            "message_id": 1,
            "message": [
                {
                    "type": "json",
                    "data": {
                        "data": '{"meta":{"news":{"jumpUrl":"https://www.bilibili.com/video/BV123"}}}'
                    },
                }
            ],
        }
    )

    assert event is not None
    assert "https://www.bilibili.com/video/BV123" in event.text
    assert event.metadata["qq_card_links"] == ["https://www.bilibili.com/video/BV123"]
