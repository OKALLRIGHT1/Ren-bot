from services.chat_support.gateway_context_service import GatewayContextService


def _service() -> GatewayContextService:
    return GatewayContextService(
        qq_remote_sources={"qq_gateway", "napcat_qq"},
        owner_shared_session_id="owner_shared",
        owner_shared_local_sources={"desktop", "text_input"},
    )


def test_owner_group_context_is_scoped_but_memory_is_shared():
    service = _service()
    first_ctx = {
            "source": "qq_gateway",
            "channel_meta": {
                "is_owner": True,
                "message_type": "group",
                "session_id": "group:100",
            },
        }
    second_ctx = {
            "source": "qq_gateway",
            "channel_meta": {
                "is_owner": True,
                "message_type": "group",
                "session_id": "group:200",
            },
        }

    assert service.conversation_session_key(first_ctx) == "group:100"
    assert service.conversation_session_key(second_ctx) == "group:200"
    assert service.memory_session_id(first_ctx) == "owner_shared"
    assert service.memory_session_id(second_ctx) == "owner_shared"


def test_owner_private_memory_still_shares_with_local_owner_session():
    service = _service()

    session_id = service.memory_session_id(
        {
            "source": "qq_gateway",
            "channel_meta": {
                "is_owner": True,
                "message_type": "private",
                "session_id": "private:42",
            },
        }
    )

    assert session_id == "owner_shared"
