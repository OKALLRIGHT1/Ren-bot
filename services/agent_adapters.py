from __future__ import annotations

from typing import Any, Dict, Protocol


class AgentAdapter(Protocol):
    async def handle(
        self,
        text: str,
        ctx: Dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        ...


class NoopAgentAdapter:
    async def handle(
        self,
        text: str,
        ctx: Dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> Dict[str, Any]:
        return {"handled": False, "reply": None, "meta": {"adapter": "noop"}}
