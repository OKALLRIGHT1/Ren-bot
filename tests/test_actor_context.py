from services.security.actor import ActorChannel, ActorKind, resolve_actor_context


def test_local_actor():
    actor = resolve_actor_context({"source": "text_input"})
    assert actor.kind == ActorKind.LOCAL
    assert actor.channel == ActorChannel.LOCAL_UI
    assert actor.allows_high_risk is True


def test_owner_private():
    actor = resolve_actor_context(
        {
            "source": "qq_gateway",
            "channel_meta": {
                "is_owner": True,
                "message_type": "private",
                "user_id": "1",
            },
        }
    )
    assert actor.kind == ActorKind.QQ_OWNER
    assert actor.channel == ActorChannel.PRIVATE
    assert actor.allows_high_risk is True


def test_owner_group_no_high():
    actor = resolve_actor_context(
        {
            "source": "qq_gateway",
            "channel_meta": {
                "is_owner": True,
                "message_type": "group",
                "group_id": "9",
            },
        }
    )
    assert actor.kind == ActorKind.QQ_OWNER
    assert actor.channel == ActorChannel.GROUP
    assert actor.allows_high_risk is False


def test_other_qq():
    actor = resolve_actor_context(
        {
            "source": "qq_gateway",
            "channel_meta": {"is_owner": False, "message_type": "private"},
        }
    )
    assert actor.kind == ActorKind.QQ_OTHER
    assert actor.allows_high_risk is False
