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


def test_external_sender_context_empty_for_desktop():
    service = _service()
    assert service.build_external_sender_context({"source": "desktop"}) == ""


def test_non_owner_qq_has_no_user_address():
    service = _service()
    ctx = {
        "source": "napcat_qq",
        "channel_meta": {
            "is_owner": False,
            "sender_name": "Other",
            "user_id": "9",
            "message_type": "private",
        },
    }
    assert service.get_active_user_address(ctx) == ""
    assert service.build_user_address_context(ctx) == ""
    external = service.build_external_sender_context(ctx)
    assert "The current sender is not" in external
    assert "sender: Other" in external


def test_owner_sender_context_uses_character_user_address():
    service = _service()
    ctx = {
        "source": "napcat_qq",
        "channel_meta": {
            "is_owner": True,
            "owner_label": "主人",
            "sender_name": "ExampleUser",
            "user_id": "10001",
            "message_type": "private",
        },
    }
    address = service.get_active_user_address(ctx)
    assert address
    assert f"当前角色称呼用户为「{address}」" in service.build_user_address_context(ctx)
    external = service.build_external_sender_context(ctx)
    assert f"The current sender is {address}." in external
    assert "The current sender is 主人." not in external


@pytest.mark.asyncio
async def test_sync_qq_user_profile_writes_owner_payload():
    written = []

    class Store:
        def upsert_qq_user_profile(self, payload):
            written.append(payload)

    service = GatewayContextService(
        qq_remote_sources={"qq_gateway", "napcat_qq"},
        owner_shared_session_id="owner_shared",
        owner_shared_local_sources={"desktop"},
        memory_store_getter=lambda: Store(),
    )
    await service.sync_qq_user_profile(
        {
            "source": "napcat_qq",
            "channel_meta": {
                "user_id": "10001",
                "sender_name": "ExampleUser",
                "is_owner": True,
                "message_type": "private",
                "sender": {"nickname": "ExampleUser", "remark": "主"},
            },
        }
    )
    assert written
    assert written[0]["user_id"] == "10001"
    assert written[0]["relationship_to_owner"] == "owner"
    assert written[0]["memory_scope"] == "owner_shared"


@pytest.mark.asyncio
async def test_sync_qq_user_profile_skips_desktop():
    written = []

    class Store:
        def upsert_qq_user_profile(self, payload):
            written.append(payload)

    service = GatewayContextService(
        qq_remote_sources={"napcat_qq"},
        owner_shared_session_id="owner_shared",
        owner_shared_local_sources={"desktop"},
        memory_store_getter=lambda: Store(),
    )
    await service.sync_qq_user_profile({"source": "desktop"})
    assert written == []
