from services.chat_support.tool_turn import should_force_capability_route


def test_should_not_force_when_already_used():
    assert (
        should_force_capability_route(
            route_reason="capability:info.weather_now",
            normal_triggers=["info_gateway"],
            used_triggers=["info_gateway"],
        )
        is False
    )


def test_should_not_force_without_triggers():
    assert (
        should_force_capability_route(
            route_reason="capability:info.weather_now",
            normal_triggers=[],
            used_triggers=[],
        )
        is False
    )


def test_should_not_force_non_capability_reason():
    assert (
        should_force_capability_route(
            route_reason="search",
            normal_triggers=["search_web"],
            used_triggers=[],
        )
        is False
    )
