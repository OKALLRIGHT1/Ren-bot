from __future__ import annotations

import asyncio

from services.capability_gatekeeper import (
    refine_capability_args,
    resolve_ambiguous_capability,
)


def test_refine_capability_args_uses_gatekeeper_task_type():
    seen = {}

    def fake_chat(messages, *, task_type="default", caller=""):
        seen["task_type"] = task_type
        seen["caller"] = caller
        return (
            '{"approved":true,"capability_id":"info.weather_now",'
            '"args":{"city":"长春"},"confidence":0.91,"reason":"ok"}'
        )

    decision = asyncio.run(
        refine_capability_args(
            user_text="长春天气怎么样",
            capability_id="info.weather_now",
            initial_args={"city": "长春"},
            chat_with_ai=fake_chat,
        )
    )

    assert seen["task_type"] == "gatekeeper"
    assert seen["caller"] == "capability_gatekeeper"
    assert decision is not None
    assert decision.approved is True
    assert decision.args.get("city") == "长春"


def test_resolve_ambiguous_capability_selects_from_candidates():
    seen = {}

    def fake_chat(messages, *, task_type="default", caller=""):
        seen["task_type"] = task_type
        seen["caller"] = caller
        return (
            '{"need_tools":true,"capability_id":"memory.query",'
            '"args":{},"confidence":0.84,"reason":"habit recall"}'
        )

    decision = asyncio.run(
        resolve_ambiguous_capability(
            user_text="我平常都是周几开会",
            candidates=[
                {
                    "capability_id": "memory.query",
                    "plugin": "memory_tools",
                    "score": 0.55,
                    "args": {},
                    "reason": "low",
                },
                {
                    "capability_id": "activity.query",
                    "plugin": "memory_tools",
                    "score": 0.4,
                    "args": {},
                },
            ],
            chat_with_ai=fake_chat,
        )
    )

    assert seen["task_type"] == "gatekeeper"
    assert seen["caller"] == "capability_route_gatekeeper"
    assert decision is not None
    assert decision.approved is True
    assert decision.capability_id == "memory.query"
    assert decision.plugin == "memory_tools"


def test_resolve_ambiguous_capability_rejects_unknown_id():
    def fake_chat(messages, *, task_type="default", caller=""):
        return (
            '{"need_tools":true,"capability_id":"made.up",'
            '"args":{},"confidence":0.99,"reason":"hallucinated"}'
        )

    decision = asyncio.run(
        resolve_ambiguous_capability(
            user_text="帮我查一下",
            candidates=[
                {
                    "capability_id": "memory.query",
                    "plugin": "memory_tools",
                    "score": 0.5,
                }
            ],
            chat_with_ai=fake_chat,
        )
    )

    assert decision is not None
    assert decision.approved is False
    assert decision.reason == "gatekeeper_unknown_capability"


def test_resolve_ambiguous_capability_can_decline_tools():
    def fake_chat(messages, *, task_type="default", caller=""):
        return (
            '{"need_tools":false,"capability_id":"","args":{},'
            '"confidence":0.9,"reason":"plain chat"}'
        )

    decision = asyncio.run(
        resolve_ambiguous_capability(
            user_text="今天心情怎么样",
            candidates=[
                {
                    "capability_id": "memory.query",
                    "plugin": "memory_tools",
                    "score": 0.4,
                }
            ],
            chat_with_ai=fake_chat,
        )
    )

    assert decision is not None
    assert decision.approved is False


def test_tool_router_exposes_ambiguous_capability_candidates():
    from modules.tool_router import ToolRouter
    from services.capability_manager import ToolCapability, ToolCapabilityMatch

    class Memoryish:
        def get_capabilities(self):
            return [
                ToolCapability(
                    "memory.query",
                    "memory_tools",
                    "natural",
                    lambda text, ctx: ToolCapabilityMatch(
                        capability_id="memory.query",
                        plugin="memory_tools",
                        score=0.55,
                        reason="low",
                    )
                    if "开会" in text
                    else None,
                )
            ]

    router = ToolRouter(
        react_map={},
        direct_map={},
        delegate_map={"memory_tools": Memoryish()},
        enable_intent_keywords=False,
    )
    route = router.route("我平常都是周几开会")
    assert route.need_tools is False
    assert route.reason == "capability_ambiguous"
    assert route.capability_ambiguous is True
    assert route.capability_candidates
    assert route.capability_candidates[0]["capability_id"] == "memory.query"
