import pytest

from services.chat_support.conversation_event_service import ConversationEventService
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


@pytest.mark.parametrize(
    ("ctx", "expected"),
    [
        ({"source": "desktop"}, ("desktop", "local:desktop")),
        ({"source": "text_input"}, ("desktop", "local:text_input")),
        (
            {
                "source": "qq_gateway",
                "channel_meta": {"session_id": "private:42", "user_id": "42"},
            },
            ("qq", "private:42"),
        ),
        (
            {
                "source": "qq_gateway",
                "channel_meta": {"session_id": "group:7", "user_id": "42"},
            },
            ("qq", "group:7"),
        ),
    ],
)
def test_event_scope_matrix(ctx, expected):
    service = ConversationEventService(
        store=None,
        gateway_context_service=_service(),
        enabled=False,
    )
    scope = service.resolve_scope(ctx, persona_id="suzu", person_id="owner")
    assert (scope.channel, scope.conversation_id) == expected


def test_gateway_event_scope_parts_reuse_session_key():
    service = _service()
    ctx = {
        "source": "qq_gateway",
        "channel_meta": {"session_id": "group:100", "user_id": "1"},
    }
    assert service.event_scope_parts(ctx) == ("qq", "group:100")
    assert service.conversation_session_key(ctx) == "group:100"
