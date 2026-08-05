from services.action_gate import ActionGate, ActionRisk


def _ctx(source="text_input", *, owner=False, message_type="private"):
    if source in {"text_input", "voice"}:
        return {"source": source}
    return {
        "source": source,
        "channel_meta": {
            "adapter": "napcat_qq",
            "is_owner": owner,
            "message_type": message_type,
            "user_id": "10001" if owner else "20002",
        },
    }


def test_read_actions_are_allowed():
    gate = ActionGate()
    decision = gate.evaluate("system.health_check", _ctx())
    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.risk == ActionRisk.READ


def test_install_actions_require_confirmation_local():
    gate = ActionGate()
    decision = gate.evaluate("system.install_dependency", _ctx("text_input"))
    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert decision.risk == ActionRisk.HIGH


def test_remote_non_owner_cannot_high():
    gate = ActionGate()
    decision = gate.evaluate("system.install_dependency", _ctx("qq_gateway", owner=False))
    assert decision.allowed is False
    assert decision.requires_confirmation is False


def test_owner_group_cannot_high():
    gate = ActionGate()
    decision = gate.evaluate(
        "system.spawn_process_trusted",
        _ctx("qq_gateway", owner=True, message_type="group"),
    )
    assert decision.allowed is False
    assert "群聊" in decision.reason


def test_owner_private_trusted_spawn_allowed():
    gate = ActionGate()
    decision = gate.evaluate(
        "system.spawn_process_trusted",
        _ctx("qq_gateway", owner=True, message_type="private"),
    )
    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.risk == ActionRisk.LOW


def test_owner_private_high_needs_confirm():
    gate = ActionGate()
    decision = gate.evaluate(
        "system.exec_code",
        _ctx("qq_gateway", owner=True, message_type="private"),
    )
    assert decision.allowed is False
    assert decision.requires_confirmation is True

    decision2 = gate.evaluate(
        "system.exec_code",
        {
            **_ctx("qq_gateway", owner=True, message_type="private"),
            "action_confirmed": True,
        },
    )
    assert decision2.allowed is True


def test_code_agent_action_is_high_and_needs_confirm():
    gate = ActionGate()
    decision = gate.evaluate("system.code_agent", _ctx("text_input"))
    assert decision.risk == ActionRisk.HIGH
    assert decision.allowed is False
    assert decision.requires_confirmation is True

    ok = gate.evaluate(
        "system.code_agent",
        {**_ctx("text_input"), "action_confirmed": True},
    )
    assert ok.allowed is True


def test_code_agent_plugin_maps_to_gate_action():
    gate = ActionGate()

    class P:
        gated_action = "system.code_agent"
        plugin_trigger = "code_agent"

        def resolve_gated_action(self, args, ctx=None):
            if str(args or "").strip().startswith("status"):
                return ""
            return "system.code_agent"

    assert gate.resolve_plugin_action(P(), "status", {}) is None
    assert gate.resolve_plugin_action(P(), "analyze ||| x", {}) == "system.code_agent"


def test_local_trusted_spawn_allowed():
    gate = ActionGate()
    decision = gate.evaluate("system.spawn_process_trusted", _ctx("text_input"))
    assert decision.allowed is True
