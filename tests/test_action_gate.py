from services.action_gate import ActionGate, ActionRisk


def test_read_actions_are_allowed():
    gate = ActionGate()

    decision = gate.evaluate("system.health_check", {"source": "text_input"})

    assert decision.allowed is True
    assert decision.requires_confirmation is False
    assert decision.risk == ActionRisk.READ


def test_install_actions_require_confirmation():
    gate = ActionGate()

    decision = gate.evaluate("system.install_dependency", {"source": "text_input"})

    assert decision.allowed is False
    assert decision.requires_confirmation is True
    assert decision.risk == ActionRisk.HIGH


def test_remote_qq_cannot_install():
    gate = ActionGate()

    decision = gate.evaluate("system.install_dependency", {"source": "qq_gateway"})

    assert decision.allowed is False
    assert decision.requires_confirmation is False
