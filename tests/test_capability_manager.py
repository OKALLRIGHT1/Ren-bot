from services.capability_manager import (
    CapabilityManager,
    ToolCapability,
    ToolCapabilityManager,
    ToolCapabilityMatch,
    is_force_executable_capability,
)


class Runtime:
    def list_tools(self):
        return [
            {"trigger": "agently_mail", "source": "plugin", "type": "direct"},
            {"trigger": "mcp.demo.read", "source": "mcp", "type": "mcp"},
        ]


class Bridge:
    def list_server_status(self):
        return [{"name": "demo", "connected": True, "tool_count": 1, "error": ""}]


def test_health_check_reports_tools_and_mcp():
    manager = CapabilityManager(
        runtime_getter=lambda: Runtime(),
        mcp_bridge_getter=lambda: Bridge(),
    )

    report = manager.health_check()

    assert report["ok"] is True
    assert report["tool_count"] == 2
    assert report["mcp_servers"][0]["name"] == "demo"


def test_install_plan_is_plan_only():
    manager = CapabilityManager(runtime_getter=lambda: Runtime())

    plan = manager.propose_install("weather")

    assert plan["action"] == "system.install_dependency"
    assert plan["requires_confirmation"] is True
    assert "commands" in plan
    assert plan["executed"] is False


def test_tool_capability_manager_selects_first_confident_match():
    def low_weather(text, ctx):
        if "天气" in text:
            return ToolCapabilityMatch(
                capability_id="info.weather_now",
                plugin="info_gateway",
                score=0.6,
            )
        return None

    def forecast(text, ctx):
        if "这周" in text and "天气" in text:
            return ToolCapabilityMatch(
                capability_id="info.weather_7d",
                plugin="info_gateway",
                score=0.9,
                args={"city": "上海"},
            )
        return None

    manager = ToolCapabilityManager(
        [
            ToolCapability("info.weather_7d", "info_gateway", "natural", forecast),
            ToolCapability("info.weather_now", "info_gateway", "natural", low_weather),
        ]
    )

    result = manager.match("上海这周天气怎么样", {})

    assert result.selected is not None
    assert result.selected.capability_id == "info.weather_7d"
    assert result.selected.args == {"city": "上海"}


def test_tool_capability_manager_keeps_low_confidence_ambiguous():
    def weather(text, ctx):
        if "天气" in text:
            return ToolCapabilityMatch(
                capability_id="info.weather_now",
                plugin="info_gateway",
                score=0.5,
                args=None,
            )
        return None

    manager = ToolCapabilityManager(
        [ToolCapability("info.weather_now", "info_gateway", "natural", weather)]
    )

    result = manager.match("我这边天气怎么样", {})

    assert result.selected is None
    assert len(result.candidates) == 1
    assert result.ambiguous is True


def test_tool_capability_manager_can_build_from_plugins():
    class Plugin:
        def get_capabilities(self):
            return [
                ToolCapability(
                    "demo.echo",
                    "demo",
                    "natural",
                    lambda text, ctx: ToolCapabilityMatch(
                        capability_id="demo.echo",
                        plugin="demo",
                        score=0.9,
                    )
                    if "echo" in text
                    else None,
                )
            ]

    manager = ToolCapabilityManager.from_plugin_maps(react_map={"demo": Plugin()})

    result = manager.match("please echo", {})

    assert result.selected is not None
    assert result.selected.plugin == "demo"


def test_tool_capability_manager_marks_unavailable_without_losing_match_data():
    def matcher(text, ctx):
        return ToolCapabilityMatch(
            capability_id="demo.weather",
            plugin="demo",
            score=0.9,
            args={"city": "上海"},
            raw_text=text,
            reason="weather_query",
        )

    manager = ToolCapabilityManager(
        [
            ToolCapability(
                "demo.weather",
                "demo",
                "natural",
                matcher,
                check_available=lambda ctx: {
                    "available": False,
                    "reason": "missing_token",
                },
            )
        ]
    )

    result = manager.match("上海天气怎么样", {})

    assert result.selected is None
    assert result.reason == "unavailable"
    assert result.candidates[0].available is False
    assert result.candidates[0].unavailable_reason == "missing_token"
    assert result.candidates[0].args == {"city": "上海"}
    assert result.candidates[0].reason == "weather_query"


def test_tool_capability_manager_ignores_matcher_exceptions():
    def broken_matcher(text, ctx):
        raise ValueError("bad matcher")

    manager = ToolCapabilityManager(
        [ToolCapability("demo.broken", "demo", "natural", broken_matcher)]
    )

    result = manager.match("anything", {})

    assert result.selected is None
    assert result.candidates == []
    assert result.reason == "no_match"


def test_from_plugin_maps_wraps_legacy_direct_handler():
    class LegacyDirectPlugin:
        type = "direct"

        def should_handle_direct(self, text, context, key):
            return key == "legacy_tool" and str(text).startswith("/legacy")

    manager = ToolCapabilityManager.from_plugin_maps(
        direct_map={"legacy_tool": LegacyDirectPlugin()}
    )

    result = manager.match("/legacy ping", {})

    assert result.selected is not None
    assert result.selected.plugin == "legacy_tool"
    assert result.selected.capability_id == "legacy_tool.direct"
    assert result.selected.reason == "legacy_direct_handler"


def test_from_plugin_maps_wraps_slash_alias_commands():
    class SlashAliasPlugin:
        type = "react"
        aliases = ["/demo", "demo natural"]

    manager = ToolCapabilityManager.from_plugin_maps(
        react_map={"demo_tool": SlashAliasPlugin()}
    )

    command_result = manager.match("/demo ping", {})
    natural_result = manager.match("demo natural ping", {})

    assert command_result.selected is not None
    assert command_result.selected.plugin == "demo_tool"
    assert command_result.selected.capability_id == "demo_tool.command"
    assert command_result.selected.reason == "slash_alias_command"
    assert natural_result.selected is None


def test_mcp_domain_capability_is_not_force_executable():
    assert is_force_executable_capability("capability:info.weather_now") is True
    assert is_force_executable_capability("capability:mcp_tools.domain_call") is False
    assert is_force_executable_capability("mcp_domain_preferred") is False
